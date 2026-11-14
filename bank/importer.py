"""Import des fichiers de questions (SPEC 6.2 et 6.3).

Validation complète, sans IA, puis enregistrement atomique.
"""

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from django.db import IntegrityError, transaction
from rapidfuzz import fuzz, process

from bank.constants import (
    CORRECT_LONGEST_SHARE,
    LANGUAGE_POINTS,
    LETTERS,
    LEVELS,
    MAX_IMPORT_FILE_BYTES,
    MAX_IMPORT_FILES,
    MAX_SPEAKERS,
    MSL_BLANK,
    NEAR_DUPLICATE_RATIO,
    OPTION_LENGTH_EXCESS,
    QUESTION_REPONSE,
    QUESTION_REPONSE_TEXT,
    SECTIONS,
    SKILLS,
    expected_options_mode,
    subtypes_for,
)
from bank.content import (
    comparison_text,
    content_hash,
    doc_length,
    length_check,
    normalize,
    support_text,
    tts_chars,
    within_tolerance,
    word_count,
)
from bank.models import Brief, ExamFormat, ImportBatch, Item, Test, TestItem
from bank.schema import StrictJSONError, parse_json, schema_errors
from bank.themes import themes
from usage.models import tts_chars_remaining


@dataclass
class Issue:
    message: str
    file: str | None = None
    slot: int | None = None

    def as_dict(self) -> dict:
        return {"file": self.file, "slot": self.slot, "message": self.message}


@dataclass
class ImportReport:
    errors: list[Issue] = field(default_factory=list)
    warnings: list[Issue] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def error(self, message, file=None, slot=None):
        self.errors.append(Issue(message, file, slot))

    def warning(self, message, file=None, slot=None):
        self.warnings.append(Issue(message, file, slot))

    def as_dict(self) -> dict:
        return {
            "errors": [e.as_dict() for e in self.errors],
            "warnings": [w.as_dict() for w in self.warnings],
        }


@dataclass
class ImportResult:
    report: ImportReport
    test: Test | None = None


@dataclass
class _Parsed:
    exam_format: ExamFormat | None = None
    brief: Brief | None = None
    items: list[dict] = field(default_factory=list)  # items de tous les fichiers, avec « _file »


def _fmt(value: float) -> str:
    return f"{value:.1f}".replace(".", ",") if value != int(value) else str(int(value))


# --- Étape 1 : lecture des fichiers -------------------------------------------------------------


def _read_files(files: list[tuple[str, bytes]], report: ImportReport) -> list[tuple[str, dict]]:
    if not 1 <= len(files) <= MAX_IMPORT_FILES:
        report.error(f"Envoyez de 1 à {MAX_IMPORT_FILES} fichiers JSON.")
        return []
    documents = []
    for name, raw in files:
        if len(raw) > MAX_IMPORT_FILE_BYTES:
            report.error("Fichier de plus de 2 Mo.", file=name)
            continue
        try:
            data = parse_json(raw)
        except StrictJSONError as exc:
            report.error(f"Lecture impossible : {exc}.", file=name)
            continue
        problems = schema_errors(data)
        for problem in problems:
            report.error(f"Non conforme au schéma — {problem}", file=name)
        if not problems:
            documents.append((name, data))
    return documents


# --- Étape 2 : format, brief, fusion -------------------------------------------------------------


def _resolve_brief(documents, report: ImportReport) -> _Parsed:
    parsed = _Parsed()
    codes = {data["format"] for _, data in documents}
    brief_ids = {data["brief_id"] for _, data in documents}
    if len(codes) > 1:
        report.error(f"Les fichiers portent des formats différents : {', '.join(sorted(codes))}.")
        return parsed
    if len(brief_ids) > 1:
        report.error(
            f"Les fichiers portent des brief_id différents : {', '.join(sorted(brief_ids))}."
        )
        return parsed

    code, brief_id = codes.pop(), brief_ids.pop()
    parsed.exam_format = ExamFormat.objects.filter(code=code, is_active=True).first()
    if parsed.exam_format is None:
        report.error(f"Format inconnu : {code}.")
        return parsed
    parsed.brief = Brief.objects.filter(brief_id=brief_id, format=parsed.exam_format).first()
    if parsed.brief is None:
        report.error(
            f"Brief inconnu : {brief_id}. "
            "Le fichier doit répondre à un brief généré dans le back-office."
        )
        return parsed
    if Test.objects.filter(format=parsed.exam_format, number=parsed.brief.number).exists():
        report.error(f"Le brief {brief_id} a déjà donné lieu à un test.")

    for name, data in documents:
        for item in data["items"]:
            parsed.items.append({**item, "_file": name})
    return parsed


# --- Étape 3 : contrôles item par item ----------------------------------------------------------


def _check_slots(parsed: _Parsed, report: ImportReport) -> None:
    brief_slots = {s["slot"]: s for s in parsed.brief.content["slots"]}
    seen = Counter(item["slot"] for item in parsed.items)
    for slot, count in sorted(seen.items()):
        if count > 1:
            report.error(f"Emplacement présent {count} fois.", slot=slot)
    for item in parsed.items:
        file, slot = item["_file"], item["slot"]
        expected = brief_slots.get(slot)
        if expected is None:
            report.error("Emplacement absent du brief.", file, slot)
            continue
        item["_brief_slot"] = expected
        if (item["section"], item["level"]) != (expected["section"], expected["level"]):
            report.error(
                f"Le brief attend {expected['section']} {expected['level']} à cet emplacement, "
                f"le fichier donne {item['section']} {item['level']}.",
                file,
                slot,
            )
        if item["subtype"] != expected["subtype"]:
            report.warning(
                f"Sous-type « {item['subtype']} » au lieu de « {expected['subtype']} » (brief).",
                file,
                slot,
            )
        if item["tags"]["theme"] != expected["theme"]:
            report.warning(
                f"Thème « {item['tags']['theme']} » au lieu de « {expected['theme']} » (brief).",
                file,
                slot,
            )
        point = item["tags"].get("language_point")
        if expected.get("language_point") and point != expected["language_point"]:
            report.warning(
                f"Point de langue « {point} » au lieu de « {expected['language_point']} » (brief).",
                file,
                slot,
            )


def _check_counts(parsed: _Parsed, report: ImportReport) -> None:
    """Règle 3 : nombre d'items par épreuve et par niveau."""
    counts = Counter((item["section"], item["level"]) for item in parsed.items)
    for format_section in parsed.exam_format.sections.all():
        for level in LEVELS:
            expected = format_section.level_distribution.get(level, 0)
            found = counts.get((format_section.section, level), 0)
            if found != expected:
                report.error(
                    f"{format_section.section} {level} : {found} item(s) au lieu de {expected}."
                )


def _check_item(item: dict, report: ImportReport) -> None:
    file, slot = item["_file"], item["slot"]
    section, level, subtype = item["section"], item["level"], item["subtype"]

    if subtype not in subtypes_for(section, level):
        report.error(f"Sous-type « {subtype} » non autorisé en {section} {level}.", file, slot)

    # Règle 4 : options et bonne réponse.
    normalized = [normalize(option) for option in item["options"]]
    if len(set(normalized)) < len(normalized):
        report.error("Options en double.", file, slot)
    if not 0 <= item["correct_index"] <= 3:
        report.error(f"correct_index hors bornes : {item['correct_index']}.", file, slot)
    elif "_brief_slot" in item:
        letter = item["_brief_slot"]["correct_letter"]
        if LETTERS[item["correct_index"]] != letter:
            report.error(
                f"La bonne réponse est {LETTERS[item['correct_index']]} ; "
                f"le brief impose {letter}.",
                file,
                slot,
            )

    expected_mode = expected_options_mode(section, level, subtype)
    if item["options_mode"] != expected_mode:
        report.error(f"options_mode doit valoir « {expected_mode} ».", file, slot)
    if subtype == QUESTION_REPONSE and item["question"].strip() != QUESTION_REPONSE_TEXT:
        report.error(f"La question doit être « {QUESTION_REPONSE_TEXT} ».", file, slot)

    # Règle 5 : un seul blanc en MSL.
    if section == "MSL":
        blanks = re.findall(r"_{3,}", item.get("stimulus_text") or "")
        if blanks != [MSL_BLANK]:
            report.error(
                f"La phrase doit contenir exactement un blanc « {MSL_BLANK} » "
                f"({len(blanks)} trouvé(s)).",
                file,
                slot,
            )

    # Règle 6 : script de CO.
    if section == "CO":
        script = item.get("audio_script")
        if not script:
            report.error("Script audio absent.", file, slot)
        else:
            speakers = script.get("speakers", {})
            if len(speakers) > MAX_SPEAKERS:
                report.error(f"Plus de {MAX_SPEAKERS} locuteurs.", file, slot)
            undeclared = sorted({line["speaker"] for line in script["lines"]} - set(speakers))
            if undeclared:
                report.error(f"Locuteur non déclaré : {', '.join(undeclared)}.", file, slot)
    elif item.get("audio_script"):
        report.error("Script audio réservé à la CO.", file, slot)

    # Règle 7 : longueur, tolérance de 15 %.
    if section != "CO" or item.get("audio_script"):
        value, low, high, unit = length_check(item)
        if not within_tolerance(value, low, high):
            report.error(
                f"Longueur hors fourchette : {_fmt(value)} {unit} pour {low} à {high} {unit} "
                "(tolérance 15 %).",
                file,
                slot,
            )


def _check_duplicates(items: list[dict], report: ImportReport) -> None:
    """Règles 8 et 9 : doublons exacts et quasi-doublons, en banque ou dans le fichier."""
    for item in items:
        item["_hash"] = content_hash(item)
    existing = set(
        Item.objects.filter(content_hash__in=[i["_hash"] for i in items]).values_list(
            "content_hash", flat=True
        )
    )
    seen: dict[str, int] = {}
    for item in items:
        if item["_hash"] in existing:
            report.error("Doublon exact d'un item déjà en banque.", item["_file"], item["slot"])
        if item["_hash"] in seen:
            report.error(
                f"Doublon exact de l'emplacement {seen[item['_hash']]}.",
                item["_file"],
                item["slot"],
            )
        seen.setdefault(item["_hash"], item["slot"])

    bank_by_section = defaultdict(list)
    for bank_item in Item.objects.all():
        fields = {
            "section": bank_item.section,
            "subtype": bank_item.subtype,
            "stimulus_text": bank_item.stimulus_text,
            "audio_script": bank_item.audio_script,
            "question": bank_item.question,
        }
        bank_by_section[bank_item.section].append((bank_item.pk, comparison_text(fields)))

    file_by_section = defaultdict(list)
    for item in items:
        text = comparison_text(item)
        section = item["section"]
        bank_choices = {pk: t for pk, t in bank_by_section[section]}
        match = process.extractOne(
            text, bank_choices, scorer=fuzz.token_set_ratio, score_cutoff=NEAR_DUPLICATE_RATIO
        )
        if match:
            report.error(
                f"Quasi-doublon de l'item {match[2]} en banque (similarité {round(match[1])}).",
                item["_file"],
                item["slot"],
            )
        file_choices = {slot: t for slot, t in file_by_section[section]}
        match = process.extractOne(
            text, file_choices, scorer=fuzz.token_set_ratio, score_cutoff=NEAR_DUPLICATE_RATIO
        )
        if match:
            report.error(
                f"Quasi-doublon de l'emplacement {match[2]} (similarité {round(match[1])}).",
                item["_file"],
                item["slot"],
            )
        file_by_section[section].append((item["slot"], text))


# --- Étape 4 : avertissements -------------------------------------------------------------------


def _warnings(items: list[dict], report: ImportReport) -> None:
    theme_ids = themes()
    correct_longest = Counter()
    per_section = Counter()
    for item in items:
        file, slot, section = item["_file"], item["slot"], item["section"]
        lengths = [len(option) for option in item["options"]]
        for index, length in enumerate(lengths):
            others = lengths[:index] + lengths[index + 1 :]
            mean = sum(others) / len(others)
            if mean and length > mean * (1 + OPTION_LENGTH_EXCESS):
                report.warning(
                    f"L'option {LETTERS[index]} est bien plus longue que les autres.", file, slot
                )
        per_section[section] += 1
        if lengths[item["correct_index"]] == max(lengths) and lengths.count(max(lengths)) == 1:
            correct_longest[section] += 1

        tags = item["tags"]
        if tags["skill"] not in SKILLS[section]:
            report.warning(
                f"Compétence « {tags['skill']} » hors vocabulaire en {section}.", file, slot
            )
        if tags["theme"] not in theme_ids:
            report.warning(f"Thème « {tags['theme']} » hors de la liste des thèmes.", file, slot)
        point = tags.get("language_point")
        if section == "MSL" and point not in LANGUAGE_POINTS[item["level"]]:
            report.warning(
                f"Point de langue « {point} » hors de la liste du niveau {item['level']}.",
                file,
                slot,
            )

    for section in SECTIONS:
        if (
            per_section[section]
            and correct_longest[section] / per_section[section] > CORRECT_LONGEST_SHARE
        ):
            report.warning(
                f"{section} : la bonne réponse est l'option la plus longue dans "
                f"{correct_longest[section]} items sur {per_section[section]}."
            )

    needed = sum(tts_chars(item) for item in items)
    remaining = tts_chars_remaining()
    if needed > remaining:
        report.warning(
            f"Synthèse vocale : {needed} caractères à synthétiser "
            f"pour {remaining} restants ce mois-ci."
        )


# --- Enregistrement -----------------------------------------------------------------------------


def _save(parsed: _Parsed, filenames: list[str], report: ImportReport, user) -> Test:
    batch = ImportBatch.objects.create(
        uploaded_by=user,
        filenames=filenames,
        format=parsed.exam_format,
        status=ImportBatch.Status.ACCEPTED,
        report=report.as_dict(),
    )
    test = Test.objects.create(
        format=parsed.exam_format, number=parsed.brief.number, import_batch=batch
    )
    level_rank = {level: rank for rank, level in enumerate(LEVELS)}
    by_section = defaultdict(list)
    for data in parsed.items:
        by_section[data["section"]].append(data)
    for section, section_items in by_section.items():
        section_items.sort(key=lambda d: (level_rank[d["level"]], d["slot"]))
        for position, data in enumerate(section_items, start=1):
            support = support_text(section, data.get("stimulus_text"), data.get("audio_script"))
            item = Item.objects.create(
                section=section,
                level=data["level"],
                subtype=data["subtype"],
                stimulus_text=data.get("stimulus_text") or "",
                audio_script=data.get("audio_script"),
                question=data["question"],
                options=data["options"],
                correct_index=data["correct_index"],
                options_mode=data["options_mode"],
                explanation=data["explanation"],
                tags={**data["tags"], "doc_length": doc_length(data["level"])},
                word_count=word_count(support),
                content_hash=data["_hash"],
                import_batch=batch,
            )
            TestItem.objects.create(
                test=test, item=item, section=section, position=position, slot=data["slot"]
            )
    return test


def import_files(files: list[tuple[str, bytes]], user=None) -> ImportResult:
    """Valide puis enregistre 1 à 3 fichiers (nom, contenu).

    Rien n'est écrit si une erreur subsiste.
    """
    report = ImportReport()
    documents = _read_files(files, report)
    if not report.ok:
        return ImportResult(report)

    parsed = _resolve_brief(documents, report)
    if not report.ok:
        return ImportResult(report)

    _check_slots(parsed, report)
    _check_counts(parsed, report)
    for item in parsed.items:
        _check_item(item, report)
    _check_duplicates(parsed.items, report)
    _warnings(parsed.items, report)
    if not report.ok:
        return ImportResult(report)

    try:
        with transaction.atomic():
            test = _save(parsed, [name for name, _ in files], report, user)
    except IntegrityError:
        # Import concurrent du même brief ou d'un même item : rien n'a été écrit.
        report.error("Enregistrement impossible : un test ou un item identique vient d'être créé.")
        return ImportResult(report)
    return ImportResult(report, test)
