"""Structure de `Advice.content` (SPEC 12.3) : schéma, contrôles, modèles de secours.

Révision du 20 novembre 2026 : aucun chiffre de réussite, de 0 à 3 conseils, rien sans constat.
Un conseil qui échoue à un contrôle est retiré ; les autres sont gardés.
"""

import json
import re
from functools import cache
from pathlib import Path

import jsonschema
from rapidfuzz import fuzz

from advice.labels import SECTION_SHORT, SKILL_LABELS, SUBTYPE_LABELS
from bank.constants import LETTERS
from bank.language_points import FAMILY_LABELS

APP_DIR = Path(__file__).resolve().parent
NO_ERROR_REVIEW_TYPES = {"time_pressure", "answer_changes"}
MAX_ITEMS = 3
MAX_REVIEWS = 2
MAX_PRACTICE = 3
NEAR_QUESTION_RATIO = 85


@cache
def resources() -> list[dict]:
    return json.loads((APP_DIR / "data" / "resources.json").read_text(encoding="utf-8"))


def resource_ids() -> set[str]:
    return {r["id"] for r in resources()}


@cache
def fallbacks() -> dict:
    return json.loads((APP_DIR / "fallbacks" / "fallbacks.json").read_text(encoding="utf-8"))


# --- Schéma de sortie --------------------------------------------------------------------------

_NULLABLE_STRING = {"anyOf": [{"type": "string"}, {"type": "null"}]}


def _object(properties: dict) -> dict:
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def output_schema() -> dict:
    """Schéma envoyé à l'API (sortie structurée). Les contraintes de nombre et de longueur ne
    sont pas prises en charge par l'API : `check_item` les vérifie après l'appel."""
    error_review = _object(
        {
            "test_item_id": {"type": "integer"},
            "why_wrong": {"type": "string"},
            "how_to_find": {"type": "string"},
        }
    )
    practice = _object(
        {
            "question": {"type": "string"},
            "options": {"type": "array", "items": {"type": "string"}},
            "correct_index": {"type": "integer"},
            "explanation": {"type": "string"},
        }
    )
    resource = _object({"resource_id": {"type": "string"}, "what_to_do": {"type": "string"}})
    item = _object(
        {
            "finding_id": {"type": "string"},
            "title": {"type": "string"},
            "diagnostic": {"type": "string"},
            "lesson": {"type": "string"},
            "error_reviews": {"type": "array", "items": error_review},
            "practice": {"type": "array", "items": practice},
            "resource": {"anyOf": [resource, {"type": "null"}]},
            "progress_note": _NULLABLE_STRING,
        }
    )
    return _object({"summary": {"type": "string"}, "items": {"type": "array", "items": item}})


# --- Filtres de texte ---------------------------------------------------------------------------

_NUMBER_WORDS = (
    r"(?:un|une|deux|trois|quatre|cinq|six|sept|huit|neuf|dix|onze|douze|treize|quatorze|quinze|"
    r"seize|vingt|trente|quarante|cinquante|soixante|cent)"
)
_NUMBER = rf"(?:\d+(?:[.,]\d+)?|{_NUMBER_WORDS}(?:[-\s]{_NUMBER_WORDS})*)"
_COUNT = r"(?:\d+|deux|trois|quatre|cinq|six|sept|huit|neuf|dix|onze|douze|quinze|vingt)"
FIGURE_PATTERNS = [
    # « 83 % », « 83 pour cent », « quatre-vingts pour cent »
    re.compile(rf"\b{_NUMBER}\s*(?:%|pour\s*cent\b|pourcent\b)", re.IGNORECASE),
    re.compile(r"%"),
    # « 50 points », « 1 point » (chiffres seulement : « deux points » désigne aussi « : »)
    re.compile(r"\b\d+(?:[.,]\d+)?\s*points?\b", re.IGNORECASE),
    # « 3 sur 5 », « 3 questions sur 5 », « trois réponses sur cinq » (sans « un » : « un regard
    # sur un problème » n'est pas un score)
    re.compile(rf"\b{_COUNT}\s+(?:\w+\s+){{0,2}}sur\s+{_COUNT}\b", re.IGNORECASE),
]
RETAKE_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\brefai(?:re|tes|sez|sant)\b",
        r"\brecommence(?:r|z)\b",
        r"\brepass(?:er|ez|ant)\b",
        r"\breprene[zr]\b[^.]{0,40}\b(?:questions?|tests?|épreuves?)\b",
        r"\brevoye[zr]\b[^.]{0,40}\bquestions?\s+(?:manquées|ratées|fausses)\b",
        r"\brefaire\b",
    )
]


def text_problem(text: str | None) -> str | None:
    """Pourquoi ce texte est refusé (chiffre de réussite, invitation à refaire), ou None."""
    if not text:
        return None
    for pattern in FIGURE_PATTERNS:
        if match := pattern.search(text):
            return f"chiffre de réussite « {match.group(0).strip()} »"
    for pattern in RETAKE_PATTERNS:
        if match := pattern.search(text):
            return f"invitation à refaire « {match.group(0).strip()} »"
    return None


# --- Contrôles (après l'appel) -----------------------------------------------------------------


def _normalized(text: str) -> str:
    return " ".join((text or "").lower().split())


def _near_a_joined_question(exercise: dict, errors: list[dict]) -> bool:
    question = _normalized(exercise["question"])
    for error in errors:
        joined = _normalized(f"{error['support']} {error['question']}")
        if fuzz.token_set_ratio(question, joined) >= NEAR_QUESTION_RATIO:
            return True
    return False


def _resource_fits(resource: dict, finding: dict) -> bool:
    section, levels = finding.get("section"), finding.get("details", {}).get("levels", [])
    if section and section not in resource.get("for", [section]):
        return False
    allowed_levels = resource.get("levels")
    return not (allowed_levels and levels and not set(levels) & set(allowed_levels))


def check_item(item: dict, finding: dict | None, sent_errors: dict[int, dict]) -> list[str]:
    """Problèmes d'un conseil ; vide s'il peut être gardé."""
    if finding is None:
        return [f"constat inconnu « {item['finding_id']} »"]
    problems = []
    for key in ("title", "diagnostic", "lesson"):
        if not item[key].strip():
            problems.append(f"« {key} » vide")
    for key in ("title", "diagnostic", "lesson", "progress_note"):
        if problem := text_problem(item.get(key)):
            problems.append(f"{key} : {problem}")
    reviews = item["error_reviews"]
    joined = finding.get("errors", [])
    for review in reviews:
        if review["test_item_id"] not in sent_errors:
            problems.append(f"question {review['test_item_id']} inconnue")
    if len(reviews) > MAX_REVIEWS:
        problems.append(f"{len(reviews)} erreurs commentées (2 au plus)")
    if finding["type"] not in NO_ERROR_REVIEW_TYPES and joined and not reviews:
        problems.append("aucune erreur commentée")
    practice = item["practice"]
    if len(practice) > MAX_PRACTICE:
        problems.append(f"{len(practice)} exercices (3 au plus)")
    if practice and finding.get("section") == "CO":
        problems.append("exercice pour un constat de CO")
    for number, exercise in enumerate(practice, start=1):
        options = exercise["options"]
        if len(options) != 4 or len(set(options)) != 4:
            problems.append(f"exercice {number} : il faut 4 options différentes")
        if not 0 <= exercise["correct_index"] <= 3:
            problems.append(f"exercice {number} : correct_index hors de 0 à 3")
        if _near_a_joined_question(exercise, list(sent_errors.values())):
            problems.append(f"exercice {number} : trop proche d'une question jointe")
    resource = item["resource"]
    if resource is not None:
        known = {r["id"]: r for r in resources()}
        if resource["resource_id"] not in known:
            problems.append(f"ressource inconnue « {resource['resource_id']} »")
        elif not _resource_fits(known[resource["resource_id"]], finding):
            problems.append(f"ressource « {resource['resource_id']} » hors épreuve ou niveau")
    return problems


def screen(content, findings: list[dict]) -> tuple[dict | None, list[str]]:
    """Contenu à garder (conseils invalides retirés un par un) et problèmes relevés.

    Renvoie (None, problèmes) si la réponse est inutilisable : JSON non conforme, résumé refusé,
    ou plus aucun conseil alors qu'il y avait des constats.
    """
    errors = [
        f"{'/'.join(str(p) for p in e.absolute_path) or 'racine'} : {e.message}"
        for e in jsonschema.Draft202012Validator(output_schema()).iter_errors(content)
    ]
    if errors:
        return None, errors[:20]
    summary_problem = text_problem(content["summary"])
    if not content["summary"].strip() or summary_problem:
        return None, [f"résumé : {summary_problem or 'vide'}"]
    by_id = {f["id"]: f for f in findings if f["type"] != "progress"}
    sent_errors = {e["test_item_id"]: e for f in by_id.values() for e in f.get("errors", [])}
    kept, problems, seen = [], [], set()
    for index, item in enumerate(content["items"], start=1):
        item_problems = check_item(item, by_id.get(item["finding_id"]), sent_errors)
        if item["finding_id"] in seen:
            item_problems.append(f"constat « {item['finding_id']} » traité deux fois")
        if len(kept) >= MAX_ITEMS:
            item_problems.append("plus de 3 conseils")
        if item_problems:
            problems.append(f"conseil {index} retiré : " + ", ".join(item_problems))
        else:
            kept.append(item)
            seen.add(item["finding_id"])
    if by_id and not kept:
        return None, problems or ["aucun conseil"]
    return {"summary": content["summary"], "items": kept}, problems


def validate(content, findings: list[dict]) -> list[str]:
    """Tous les problèmes d'un contenu ; vide s'il est entièrement valide."""
    kept, problems = screen(content, findings)
    return problems if kept is None or problems else []


# --- Modèles de secours -------------------------------------------------------------------------


def template_key(finding: dict) -> str:
    kind, details = finding["type"], finding.get("details", {})
    if kind == "msl_family":
        return f"msl_family:{details['family']}"
    if kind == "skill":
        return f"skill:{details['skill']}"
    if kind in {"level_break", "doc_type"}:
        return f"{kind}:{finding['section']}"
    return kind


def template_for(finding: dict) -> dict | None:
    return fallbacks().get(template_key(finding))


def _levels(levels: list[str]) -> str:
    return levels[0] if len(levels) == 1 else ", ".join(levels[:-1]) + " et " + levels[-1]


def diagnostic_values(finding: dict) -> dict:
    """Mots à insérer dans le diagnostic de secours ; aucun chiffre de réussite."""
    details = finding.get("details", {})
    values = {
        "section": SECTION_SHORT.get(finding.get("section"), ""),
        "levels": _levels(details.get("levels", [])) if details.get("levels") else "",
        "often": "très souvent" if finding.get("intensity") == "très marqué" else "souvent",
    }
    if finding["type"] == "level_break":
        values.update(from_level=details["from_level"], to_level=details["to_level"])
    if finding["type"] == "doc_type":
        values["subtype"] = SUBTYPE_LABELS.get(details.get("subtype"), details.get("subtype"))
    if finding["type"] == "skill":
        values["skill"] = SKILL_LABELS.get(details.get("skill"), details.get("skill"))
    if finding["type"] == "msl_family":
        values["family"] = FAMILY_LABELS[details["family"]]
    return values


def fallback_diagnostic(template: dict, finding: dict) -> str:
    text = template["diagnostic"].format(**diagnostic_values(finding))
    if finding["type"] == "time_pressure":
        text += " " + time_landmarks(finding)
    return text.strip()


def time_landmarks(finding: dict) -> str:
    """Repères de temps par question (seuls chiffres permis, section 12.3)."""
    targets = finding.get("details", {}).get("targets", {})
    parts = [
        f"en {SECTION_SHORT[section]}, environ {t['average_seconds']} secondes par question"
        for section, t in targets.items()
    ]
    return f"Repères : {' ; '.join(parts)}." if parts else ""


def error_review(error: dict) -> dict:
    correct = error["correct_index"]
    right = f"« {error['options'][correct]} » ({LETTERS[correct]})"
    if error["chosen_index"] is None:
        why = f"Vous n'avez pas répondu. La bonne réponse était {right}."
    else:
        chosen = error["chosen_index"]
        why = (
            f"Vous avez choisi « {error['options'][chosen]} » ({LETTERS[chosen]}) ; "
            f"la bonne réponse était {right}."
        )
    return {
        "test_item_id": error["test_item_id"],
        "why_wrong": why,
        "how_to_find": error["explanation"],
    }


PROGRESS_NOTES = {
    "en hausse": "Depuis votre conseil précédent, ce point s'améliore : continuez.",
    "stable": "Depuis votre conseil précédent, ce point n'a pas encore bougé.",
    "en baisse": "Depuis votre conseil précédent, ce point pose davantage de difficultés.",
    "non mesurable": "Pas encore assez de questions de ce type depuis votre conseil précédent "
    "pour mesurer une évolution.",
}


def progress_note(finding: dict, findings: list[dict]) -> str | None:
    progress = next((f for f in findings if f["type"] == "progress"), None)
    if not progress:
        return None
    entry = next(
        (e for e in progress["details"]["items"] if e["finding_id"] == finding["id"]), None
    )
    if entry is None:
        return "Nouveau constat depuis votre conseil précédent."
    return PROGRESS_NOTES[entry.get("trend", "non mesurable")]


NO_FINDING_SUMMARY = (
    "Vos derniers tests ne font ressortir aucune difficulté nette : il n'y a pas de conseil "
    "particulier pour l'instant. Passez le test suivant ; de nouveaux conseils apparaîtront "
    "si une difficulté se dessine."
)


def fallback_content(findings: list[dict]) -> dict:
    """Conseils sans IA : un texte par type de constat (et par famille de MSL), les erreurs
    réelles commentées, sans exercice ni ressource. Un constat sans modèle ne donne rien."""
    main = [f for f in findings if f["type"] != "progress"]
    items = []
    for finding in main:
        template = template_for(finding)
        if template is None:
            continue
        reviews = [error_review(e) for e in finding.get("errors", [])[:MAX_REVIEWS]]
        items.append(
            {
                "finding_id": finding["id"],
                "title": template["title"],
                "diagnostic": fallback_diagnostic(template, finding),
                "lesson": template["lesson"],
                "error_reviews": reviews,
                "practice": [],
                "resource": None,
                "progress_note": progress_note(finding, findings),
            }
        )
    if not items:
        return {"summary": NO_FINDING_SUMMARY, "items": []}
    titles = [item["title"][0].lower() + item["title"][1:] for item in items]
    if len(titles) == 1:
        summary = f"Vos derniers tests font ressortir une difficulté nette : {titles[0]}."
    else:
        listed = ", ".join(titles[:-1]) + " et " + titles[-1]
        summary = f"Vos derniers tests font ressortir quelques difficultés nettes : {listed}."
    summary += f" Commencez par la première : {titles[0]}."
    return {"summary": summary, "items": items}
