"""Import de corrections sur un test en brouillon (SPEC 6.4 bis).

Le fichier a la forme d'un import habituel mais ne contient que les items à remplacer.
Chaque item remplace celui du test qui a le même `slot`. Mêmes contrôles qu'à l'import, sauf la
répartition par niveau ; les doublons ignorent les items remplacés eux-mêmes. Tout ou rien.
"""

from collections import Counter
from dataclasses import dataclass, field

from django.db import transaction
from django.utils import timezone

from bank.audio import base_hash
from bank.content import (
    content_hash,
    doc_length,
    item_fields,
    normalize,
    script_hash,
    support_text,
    word_count,
)
from bank.deletion import remove_unused_files
from bank.importer import (
    ImportReport,
    _check_duplicates,
    _check_item,
    _check_slots,
    _Parsed,
    refresh_warnings,
)
from bank.models import AudioAsset, Brief, Item, Test
from bank.review import require_draft
from bank.schema import StrictJSONError, parse_pasted_json, schema_errors

# Champs comparés pour savoir si un item a changé (ceux du fichier d'import, hors `slot`).
COMPARED = [
    "section",
    "level",
    "subtype",
    "stimulus_text",
    "audio_script",
    "question",
    "options",
    "correct_index",
    "options_mode",
    "explanation",
    "tags",
]


@dataclass
class CorrectionResult:
    report: ImportReport
    replaced: list[int] = field(default_factory=list)
    unchanged: list[int] = field(default_factory=list)


def _comparable(data: dict) -> dict:
    values = {key: data.get(key) for key in COMPARED}
    values["stimulus_text"] = normalize(values["stimulus_text"] or "")
    values["tags"] = {k: v for k, v in (values["tags"] or {}).items() if k != "doc_length"}
    return values


def _current(item: Item, slot: int) -> dict:
    return {**item_fields(item), "explanation": item.explanation, "slot": slot}


def import_corrections(test: Test, raw: bytes | str, source: str = "texte collé"):
    """Valide puis applique les corrections ; rien n'est modifié s'il reste une erreur."""
    require_draft(test)
    report = ImportReport()
    result = CorrectionResult(report)
    try:
        data = parse_pasted_json(raw)
    except StrictJSONError as exc:
        report.error(f"Lecture impossible : {exc}.", file=source)
        return result
    for problem in schema_errors(data):
        report.error(f"Non conforme au schéma — {problem}", file=source)
    if not report.ok:
        return result

    if test.import_batch is None:
        report.error("Ce test ne vient pas d'un import : il n'a pas de brief à corriger.")
        return result
    brief = test.import_batch.brief or _matching_brief(test, data["brief_id"])
    if brief is None:
        report.error(
            f"Brief {data['brief_id']} introuvable, ou ses emplacements ne correspondent pas "
            "à ceux de ce test."
        )
        return result
    if data["format"] != test.format.code:
        report.error(f"Format {data['format']} : ce test est au format {test.format.code}.")
    if data["brief_id"] != brief.brief_id:
        report.error(f"brief_id {data['brief_id']} : celui de ce test est {brief.brief_id}.")
    if not report.ok:
        return result

    by_slot = {ti.slot: ti.item for ti in test.test_items.select_related("item")}
    items = [{**item, "_file": source} for item in data["items"]]
    counts = Counter(item["slot"] for item in items)
    for slot, count in sorted(counts.items()):
        if count > 1:
            report.error(f"Emplacement présent {count} fois.", source, slot)
    for item in items:
        if item["slot"] not in by_slot:
            report.error("Emplacement absent de ce test.", source, item["slot"])
    if not report.ok:
        return result

    # Seuls les items dont le contenu a changé sont remplacés ; les autres gardent leur statut.
    changed = []
    for item in items:
        if _comparable(item) == _comparable(_current(by_slot[item["slot"]], item["slot"])):
            result.unchanged.append(item["slot"])
        else:
            changed.append(item)
    parsed = _Parsed(exam_format=test.format, brief=brief, items=changed)
    _check_slots(parsed, report)  # emplacement en double, épreuve et niveau du brief
    for item in changed:
        _check_item(item, report)
    replaced_pks = [by_slot[item["slot"]].pk for item in changed if item["slot"] in by_slot]
    _check_duplicates(changed, report, exclude=replaced_pks)
    if not report.ok:
        result.unchanged = []
        return result
    if not changed:
        result.unchanged.sort()
        return result

    new_slots = {item["slot"] for item in changed}
    orphans: list[str] = []
    with transaction.atomic():
        test = Test.objects.select_for_update().get(pk=test.pk)
        require_draft(test)
        for data_item in changed:
            orphans += _replace(by_slot[data_item["slot"]], data_item)
        if test.import_batch.brief_id is None:  # lien réparé une fois les corrections acceptées
            test.import_batch.brief = brief
            test.import_batch.save(update_fields=["brief"])
        _update_trace(test, new_slots)
        # Avertissements recalculés sur tout le test, avec les règles en vigueur.
        report.warnings = refresh_warnings(test)
    remove_unused_files(orphans)
    result.replaced = sorted(new_slots)
    result.unchanged.sort()
    return result


def _matching_brief(test: Test, brief_id: str) -> Brief | None:
    """Import antérieur au lien ImportBatch → Brief : le brief du fichier est accepté s'il existe,
    s'il est du format du test et si ses emplacements (épreuve, niveau) sont ceux du test."""
    brief = Brief.objects.filter(brief_id=brief_id, format=test.format).first()
    if brief is None:
        return None
    expected = {s["slot"]: (s["section"], s["level"]) for s in brief.content.get("slots", [])}
    actual = {
        ti.slot: (ti.item.section, ti.item.level) for ti in test.test_items.select_related("item")
    }
    return brief if expected == actual else None


def _replace(item: Item, data: dict) -> list[str]:
    """L'item repasse en brouillon. Si ce qui est lu a changé, l'ancien audio est supprimé :
    la nouvelle version n'a jamais été synthétisée, « Générer l'audio du test » la produira.
    Renvoie les fichiers audio à supprimer s'ils ne servent plus."""
    removed = []
    asset = AudioAsset.objects.filter(item=item).first()
    if asset is not None and base_hash(asset) != script_hash(data):
        removed.append(asset.file.name)
        asset.delete()
    item.level = data["level"]
    item.subtype = data["subtype"]
    item.stimulus_text = data.get("stimulus_text") or ""
    item.audio_script = data.get("audio_script")
    item.question = data["question"]
    item.options = data["options"]
    item.correct_index = data["correct_index"]
    item.options_mode = data["options_mode"]
    item.explanation = data["explanation"]
    item.tags = {**data["tags"], "doc_length": doc_length(data["level"])}
    item.word_count = word_count(support_text(item.section, item.stimulus_text, item.audio_script))
    item.content_hash = data.get("_hash") or content_hash(data)
    item.status = Item.Status.DRAFT
    item.reviewed_at = None
    item.save()
    return removed


def _update_trace(test: Test, slots: set[int]) -> None:
    """Résultat du contrôle à l'aveugle effacé pour les items remplacés ; trace des corrections
    dans le rapport d'import."""
    keys = {str(slot) for slot in slots}
    blind = dict(test.blind_check or {})
    for part in ("answers", "doubts"):
        if part in blind:
            blind[part] = {k: v for k, v in blind[part].items() if k not in keys}
    test.blind_check = blind
    test.save(update_fields=["blind_check"])

    batch = test.import_batch
    old = batch.report or {}
    batch.report = {
        **old,
        "corrections": [
            *old.get("corrections", []),
            {"at": timezone.now().isoformat(), "slots": sorted(slots)},
        ],
    }
    batch.save(update_fields=["report"])
