"""Relecture d'un test en brouillon, contrôle à l'aveugle et publication (SPEC 6.4 et 11)."""

from dataclasses import dataclass, field

from django.db import transaction
from django.utils import timezone

from bank.audio import AudioState, audio_state
from bank.constants import SECTIONS
from bank.content import content_hash, item_fields, support_text, word_count
from bank.importer import ImportReport, _check_item
from bank.models import Item, Test, TestItem
from bank.schema import StrictJSONError, parse_json, schema_errors


class ReviewError(Exception):
    """Action refusée : message en français, affichable tel quel."""


def require_draft(test: Test) -> None:
    if test.status != Test.Status.DRAFT:
        raise ReviewError(
            "Test publié : ses questions sont figées. Seul l'archivage reste possible."
        )


# --- Écran de relecture -------------------------------------------------------------------------


@dataclass
class Row:
    test_item: TestItem
    item: Item
    warnings: list[str] = field(default_factory=list)
    blind_answer: int | None = None
    blind_doubt: str = ""
    blind_checked: bool = False
    audio_state: str = ""  # CO : valeur de AudioState (ok, missing, stale, engine)

    @property
    def disagreement(self) -> bool:
        return self.blind_checked and self.blind_answer != self.item.correct_index

    @property
    def flagged(self) -> bool:
        return bool(self.disagreement or self.blind_doubt or self.warnings)


def rows(test: Test) -> list[Row]:
    report = test.import_batch.report if test.import_batch else {}
    by_slot: dict[int, list[str]] = {}
    for warning in report.get("warnings", []):
        if warning.get("slot") is not None:
            by_slot.setdefault(warning["slot"], []).append(warning["message"])
    blind = test.blind_check or {}
    answers, doubts = blind.get("answers", {}), blind.get("doubts", {})
    result = []
    test_items = test.test_items.select_related("item", "item__audio")
    for test_item in sorted(test_items, key=lambda t: (SECTIONS.index(t.section), t.position)):
        item, slot = test_item.item, str(test_item.slot)
        row = Row(test_item, item, warnings=by_slot.get(test_item.slot, []))
        if blind:
            row.blind_checked = True
            row.blind_answer = answers.get(slot)
            row.blind_doubt = doubts.get(slot, "")
        if item.section == "CO":
            row.audio_state = audio_state(item)
        result.append(row)
    return result


def general_warnings(test: Test) -> list[str]:
    report = test.import_batch.report if test.import_batch else {}
    return [w["message"] for w in report.get("warnings", []) if w.get("slot") is None]


def set_status(item: Item, status: str) -> None:
    require_draft(item.test_item.test)
    item.status = status
    item.reviewed_at = timezone.now()
    item.save(update_fields=["status", "reviewed_at"])


# --- Modification d'un item ---------------------------------------------------------------------


def validate_fields(item: Item, fields: dict) -> list[str]:
    """Mêmes contrôles qu'à l'import, pour un seul item modifié ; liste d'erreurs en français."""
    test = item.test_item.test
    # `doc_length` est calculé à l'import : il ne fait pas partie du schéma.
    tags = {key: value for key, value in fields["tags"].items() if key != "doc_length"}
    candidate = {"slot": item.test_item.slot, **fields, "tags": tags}
    document = {
        "schema_version": 1,
        "format": test.format.code,
        "brief_id": "relecture",
        "items": [candidate],
    }
    errors = [message.removeprefix("items[0].") for message in schema_errors(document)]
    if errors:
        return errors
    report = ImportReport()
    # Sans l'emplacement du brief : à la relecture, la bonne réponse peut changer de lettre.
    _check_item({**candidate, "_file": None}, report)
    errors = [issue.message for issue in report.errors]
    new_hash = content_hash(candidate)
    if Item.objects.filter(content_hash=new_hash).exclude(pk=item.pk).exists():
        errors.append("Doublon exact d'un item déjà en banque.")
    return errors


def update_item(item: Item, changes: dict) -> list[str]:
    """Applique les modifications si elles sont valides ; l'item repasse alors en brouillon.

    Un script de CO modifié rend l'audio périmé (comparaison de `script_hash`).
    """
    require_draft(item.test_item.test)
    fields = {**item_fields(item), "explanation": item.explanation, **changes}
    errors = validate_fields(item, fields)
    if errors:
        return errors
    item.stimulus_text = fields["stimulus_text"] or ""
    item.audio_script = fields["audio_script"]
    item.question = fields["question"]
    item.options = fields["options"]
    item.correct_index = fields["correct_index"]
    item.explanation = fields["explanation"]
    item.word_count = word_count(support_text(item.section, item.stimulus_text, item.audio_script))
    item.content_hash = content_hash(fields)
    item.status = Item.Status.DRAFT
    item.reviewed_at = None
    item.save()
    return []


# --- Contrôle à l'aveugle (6.4) -----------------------------------------------------------------

BLIND_FIELDS = [
    "section",
    "level",
    "subtype",
    "stimulus_text",
    "audio_script",
    "question",
    "options",
    "options_mode",
]


def blind_export(test: Test) -> dict:
    """Les items du test sans `correct_index` ni `explanation` (liste blanche de champs)."""
    items = []
    for test_item in test.test_items.select_related("item").order_by("slot"):
        fields = item_fields(test_item.item)
        items.append({"slot": test_item.slot, **{key: fields[key] for key in BLIND_FIELDS}})
    return {"format": test.format.code, "test": test.number, "items": items}


def import_blind_answers(test: Test, raw: bytes) -> list[str]:
    """Enregistre les réponses du relecteur, ou rien s'il y a une erreur ; renvoie les erreurs."""
    require_draft(test)
    try:
        data = parse_json(raw)
    except StrictJSONError as exc:
        return [f"Lecture impossible : {exc}."]
    if not isinstance(data, dict) or not isinstance(data.get("answers"), dict):
        return ['Le fichier doit contenir un objet « answers » : {"<slot>": <index>, …}.']
    doubts = data.get("doubts", {})
    if not isinstance(doubts, dict):
        return ['« doubts » doit être un objet : {"<slot>": "raison", …}.']
    slots = {str(slot) for slot in test.test_items.values_list("slot", flat=True)}
    errors = []
    answers = {}
    for slot, index in data["answers"].items():
        if slot not in slots:
            errors.append(f"Emplacement {slot} : absent de ce test.")
        elif type(index) is not int or not 0 <= index <= 3:
            errors.append(f"Emplacement {slot} : l'indice doit être un entier de 0 à 3.")
        else:
            answers[slot] = index
    clean_doubts = {}
    for slot, reason in doubts.items():
        if slot not in slots:
            errors.append(f"Doute sur l'emplacement {slot} : absent de ce test.")
        elif not isinstance(reason, str):
            errors.append(f"Doute sur l'emplacement {slot} : la raison doit être un texte.")
        else:
            clean_doubts[slot] = reason.strip() or "Doute sans précision."
    if errors:
        return errors
    test.blind_check = {
        "answers": answers,
        "doubts": clean_doubts,
        "received_at": timezone.now().isoformat(),
    }
    test.save(update_fields=["blind_check"])
    return []


# --- Publication (11) ---------------------------------------------------------------------------


def publication_problems(test: Test) -> list[str]:
    problems = []
    test_rows = rows(test)
    not_approved = [r for r in test_rows if r.item.status != Item.Status.APPROVED]
    if not_approved:
        problems.append(f"{len(not_approved)} question(s) non approuvée(s).")
    missing = [r for r in test_rows if r.audio_state == AudioState.MISSING]
    stale = [r for r in test_rows if r.audio_state == AudioState.STALE]
    other_engine = [r for r in test_rows if r.audio_state == AudioState.ENGINE]
    if missing:
        problems.append(f"{len(missing)} audio(s) de CO manquant(s).")
    if stale:
        problems.append(
            f"{len(stale)} audio(s) de CO périmé(s) : script modifié depuis la synthèse."
        )
    if other_engine:
        problems.append(
            f"{len(other_engine)} audio(s) de CO produit(s) par un autre moteur "
            "(audio factice avec Google) : relancez la génération de l'audio."
        )
    return problems


def publish(test: Test) -> None:
    with transaction.atomic():
        test = Test.objects.select_for_update().get(pk=test.pk)
        require_draft(test)
        problems = publication_problems(test)
        if problems:
            raise ReviewError("Publication refusée : " + " ".join(problems))
        Item.objects.filter(test_item__test=test).update(status=Item.Status.PUBLISHED)
        test.status = Test.Status.PUBLISHED
        test.published_at = timezone.now()
        test.save(update_fields=["status", "published_at"])


def archive(test: Test) -> None:
    if test.status != Test.Status.PUBLISHED:
        raise ReviewError("Seul un test publié peut être archivé.")
    with transaction.atomic():
        test.status = Test.Status.ARCHIVED
        test.save(update_fields=["status"])
        Item.objects.filter(test_item__test=test).update(status=Item.Status.RETIRED)
