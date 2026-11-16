"""Structure de `Advice.content` (SPEC 12.3) : schéma, validation, modèles de secours."""

import json
from functools import cache
from pathlib import Path

import jsonschema

from bank.constants import LETTERS

APP_DIR = Path(__file__).resolve().parent
NO_ERROR_REVIEW_TYPES = {"time_pressure", "answer_changes"}
PRACTICE_COUNT = 3
PLAN_MIN, PLAN_MAX = 2, 3


@cache
def resources() -> list[dict]:
    return json.loads((APP_DIR / "data" / "resources.json").read_text(encoding="utf-8"))


def resource_ids() -> set[str]:
    return {r["id"] for r in resources()}


@cache
def _fallbacks(name: str) -> dict:
    return json.loads((APP_DIR / "fallbacks" / f"{name}.json").read_text(encoding="utf-8"))


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
    """Schéma envoyé à l'API (sortie structurée). Les contraintes de nombre (3 exercices,
    4 options, 2 ou 3 actions) ne sont pas exprimables : `validate` les vérifie après l'appel."""
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
    action = _object(
        {
            "action": {"type": "string"},
            "minutes": {"type": "integer"},
            "frequency": {"type": "string"},
            "resource_id": _NULLABLE_STRING,
        }
    )
    item = _object(
        {
            "finding_id": {"type": "string"},
            "title": {"type": "string"},
            "diagnostic": {"type": "string"},
            "lesson": {"type": "string"},
            "error_reviews": {"type": "array", "items": error_review},
            "exam_strategy": {"type": "string"},
            "practice": {"type": "array", "items": practice},
            "week_plan": {"type": "array", "items": action},
            "progress_note": _NULLABLE_STRING,
        }
    )
    return _object(
        {
            "summary": {"type": "string"},
            "next_goal": {"type": "string"},
            "items": {"type": "array", "items": item},
        }
    )


def _strict_schema() -> dict:
    """Schéma de validation locale : celui de l'API, plus les contraintes de nombre."""
    schema = output_schema()
    item = schema["properties"]["items"]["items"]["properties"]
    schema["properties"]["items"]["minItems"] = 1
    for key in ("summary", "next_goal"):
        schema["properties"][key]["minLength"] = 1
    for key in ("title", "diagnostic", "lesson", "exam_strategy"):
        item[key]["minLength"] = 1
    item["practice"]["minItems"] = item["practice"]["maxItems"] = PRACTICE_COUNT
    practice = item["practice"]["items"]["properties"]
    practice["options"]["minItems"] = practice["options"]["maxItems"] = 4
    practice["correct_index"].update(minimum=0, maximum=3)
    item["week_plan"].update(minItems=PLAN_MIN, maxItems=PLAN_MAX)
    item["week_plan"]["items"]["properties"]["minutes"]["minimum"] = 1
    return schema


# --- Validation (contraintes vérifiées par le code après l'appel) ------------------------------


def validate(content, findings: list[dict]) -> list[str]:
    """Liste des problèmes ; vide si le contenu est utilisable."""
    errors = [
        f"{'/'.join(str(p) for p in e.absolute_path) or 'racine'} : {e.message}"
        for e in jsonschema.Draft202012Validator(_strict_schema()).iter_errors(content)
    ]
    if errors:
        return errors[:20]
    by_id = {f["id"]: f for f in findings if f["type"] != "progress"}
    sent_items = {e["test_item_id"] for f in findings for e in f.get("errors", [])}
    known_resources = resource_ids()
    seen = set()
    for index, item in enumerate(content["items"]):
        where = f"conseil {index + 1}"
        finding = by_id.get(item["finding_id"])
        if finding is None:
            errors.append(f"{where} : constat inconnu « {item['finding_id']} ».")
            continue
        if item["finding_id"] in seen:
            errors.append(f"{where} : constat « {item['finding_id']} » traité deux fois.")
        seen.add(item["finding_id"])
        for review in item["error_reviews"]:
            if review["test_item_id"] not in sent_items:
                errors.append(f"{where} : question {review['test_item_id']} inconnue.")
        needs_review = finding["type"] not in NO_ERROR_REVIEW_TYPES and finding.get("errors")
        if needs_review and not item["error_reviews"]:
            errors.append(f"{where} : aucune erreur commentée.")
        for number, exercise in enumerate(item["practice"], start=1):
            if len(set(exercise["options"])) != 4:
                errors.append(f"{where}, exercice {number} : options en double.")
        for action in item["week_plan"]:
            resource = action["resource_id"]
            if resource is not None and resource not in known_resources:
                errors.append(f"{where} : ressource inconnue « {resource} ».")
    return errors


# --- Modèles de secours -------------------------------------------------------------------------


def template_for(finding: dict) -> dict:
    """Le texte de secours le plus précis : point de langue, puis type et épreuve ou compétence,
    puis type seul."""
    types = _fallbacks("types")
    kind, details = finding["type"], finding.get("details", {})
    if kind == "language_point":
        specific = _fallbacks("language_points").get(details.get("language_point"))
        if specific:
            return specific
    keys = [f"{kind}:{details['skill']}"] if kind == "skill" else []
    keys += [f"{kind}:{finding.get('section')}", kind]
    if kind in {"skill", "doc_type"}:
        # Compétence ou document sans texte dédié : le texte de la même compétence ou épreuve.
        keys.append(f"level_break:{finding.get('section')}")
    for key in keys:
        if key in types:
            return types[key]
    return types["language_point"]


def diagnostic(finding: dict) -> str:
    kind, d = finding["type"], finding.get("details", {})
    if kind == "level_break":
        return (
            f"Sur vos derniers tests, vous réussissez {d['rate_from']} % des questions de niveau "
            f"{d['from_level']} ({d['n_from']} questions), mais seulement {d['rate_to']} % au "
            f"niveau {d['to_level']} ({d['n_to']} questions)."
        )
    if kind == "time_pressure":
        parts = []
        if d.get("trailing_unanswered"):
            parts.append(
                f"vous laissez en moyenne {str(d['trailing_per_test']).replace('.', ',')} "
                "question(s) sans réponse en fin d'épreuve"
            )
        parts.append(
            f"vous passez {d['first_half_share']} % de votre temps sur la première moitié des "
            "questions"
        )
        targets = "; ".join(
            f"{section} : {t['average_seconds']} s par question en moyenne"
            for section, t in d.get("targets", {}).items()
        )
        text = "En structures de la langue et en compréhension écrite, " + ", et ".join(parts) + "."
        return text + (
            f" Repères du mode {next(iter(d['targets'].values()))['mode']} : {targets}."
            if targets
            else ""
        )
    if kind == "answer_changes":
        return (
            f"Vous avez changé {d['changes']} fois de réponse ; {d['unfavorable']} de ces "
            f"changements ({d['unfavorable_share']} %) ont remplacé une bonne réponse par une "
            "mauvaise."
        )
    text = (
        f"{finding['label']} : {finding['rate']} % de réussite sur {finding['n']} questions, "
        f"contre {finding['reference_rate']} % "
    )
    if kind == "language_point":
        return text + "sur l'ensemble des structures de la langue."
    if d.get("forced"):
        return text + "attendus à votre niveau. C'est votre point le plus faible."
    return text + "en moyenne pour vous aux mêmes niveaux."


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


def progress_note(finding: dict, findings: list[dict]) -> str | None:
    progress = next((f for f in findings if f["type"] == "progress"), None)
    if not progress:
        return None
    entry = next(
        (e for e in progress["details"]["items"] if e["finding_id"] == finding["id"]), None
    )
    if entry is None:
        return "Nouveau constat depuis votre conseil précédent."
    if "delta" not in entry:
        return (
            "Évolution non mesurable : pas assez de questions de ce type dans vos derniers tests."
        )
    delta = entry["delta"]
    trend = "en hausse" if delta > 0 else "en baisse" if delta < 0 else "stable"
    return (
        f"Depuis votre conseil précédent : {entry['recent']['rate']} % de réussite sur vos "
        f"2 derniers tests, contre {entry['earlier']['rate']} % avant ({trend})."
    )


def next_goal(finding: dict) -> str:
    kind = finding["type"]
    if kind == "time_pressure":
        return "Au prochain test, répondre à toutes les questions de MSL et de CE avant la fin."
    if kind == "answer_changes":
        return "Au prochain test, ne changer une réponse que lorsqu'un passage précis le justifie."
    target = min(100, finding["rate"] + 15)
    return f"Au prochain test, dépasser {target} % de réussite sur ce point : {finding['label']}."


def fallback_content(findings: list[dict]) -> dict:
    """Conseils complets sans IA : textes de secours + chiffres et erreurs réels."""
    main = [f for f in findings if f["type"] != "progress"]
    items = []
    for finding in main:
        template = template_for(finding)
        reviews = [error_review(e) for e in finding.get("errors", [])]
        items.append(
            {
                "finding_id": finding["id"],
                "title": template["title"],
                "diagnostic": diagnostic(finding),
                "lesson": template["lesson"],
                "error_reviews": reviews,
                "exam_strategy": template["exam_strategy"],
                "practice": [dict(p) for p in template["practice"]],
                "week_plan": [dict(a) for a in template["week_plan"]],
                "progress_note": progress_note(finding, findings),
            }
        )
    if main:
        labels = ", ".join(f["label"].lower() for f in main[:3])
        summary = (
            f"Vos derniers tests font ressortir {len(main)} point(s) à travailler : {labels}. "
            f"Commencez par le premier : {main[0]['label'].lower()}."
        )
        goal = next_goal(main[0])
    else:
        summary = "Vos derniers tests ne font ressortir aucune faiblesse marquée. Continuez ainsi."
        goal = "Au prochain test, viser un niveau de plus dans l'épreuve la moins réussie."
    return {"summary": summary, "next_goal": goal, "items": items}
