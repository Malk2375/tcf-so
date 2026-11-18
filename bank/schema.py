"""Analyse stricte d'un fichier d'import et contrôle de conformité au schéma.

`bank/data/schema-import.json` est la copie de `docs/schema-import.json` utilisée par l'application
(un test vérifie que les deux sont identiques).
"""

import json
import re
from functools import cache
from pathlib import Path

from jsonschema import Draft202012Validator

SCHEMA_PATH = Path(__file__).resolve().parent / "data" / "schema-import.json"


class StrictJSONError(ValueError):
    pass


def _reject_duplicate_keys(pairs):
    keys = [key for key, _ in pairs]
    duplicates = sorted({key for key in keys if keys.count(key) > 1})
    if duplicates:
        raise StrictJSONError(f"clé en double : {', '.join(duplicates)}")
    return dict(pairs)


def _reject_constant(name):
    raise StrictJSONError(f"valeur non autorisée : {name}")


def parse_json(raw: bytes) -> object:
    """JSON strict : UTF-8, pas de clé en double, pas de NaN ni d'Infinity."""
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise StrictJSONError("le fichier n'est pas encodé en UTF-8") from exc
    try:
        return json.loads(
            text, object_pairs_hook=_reject_duplicate_keys, parse_constant=_reject_constant
        )
    except json.JSONDecodeError as exc:
        raise StrictJSONError(f"JSON invalide (ligne {exc.lineno}, colonne {exc.colno})") from exc


@cache
def validator() -> Draft202012Validator:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def schema_errors(data: object) -> list[str]:
    """Erreurs de conformité, lisibles : « items[3].options : … »."""
    messages = []
    for error in sorted(validator().iter_errors(data), key=lambda e: list(e.absolute_path)):
        path = "".join(f"[{p}]" if isinstance(p, int) else f".{p}" for p in error.absolute_path)
        messages.append(f"{path.lstrip('.') or 'racine'} : {error.message}")
    return messages


_FENCE = re.compile(r"```[^\S\n]*(?:json)?[^\S\n]*\n(.*?)```", re.DOTALL | re.IGNORECASE)


def decode_text(raw: bytes | str) -> str:
    if isinstance(raw, str):
        return raw
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise StrictJSONError("le fichier n'est pas encodé en UTF-8") from exc


def json_candidate(raw: bytes | str) -> str | None:
    """Le JSON contenu dans un texte collé : le premier bloc de code (balises retirées),
    ou le texte entier s'il commence par « { ». None si aucun JSON n'est trouvé."""
    text = decode_text(raw).strip().lstrip("\ufeff")
    fenced = _FENCE.search(text)
    if fenced:
        return fenced.group(1).strip()
    if text.startswith("{") or text.startswith("["):
        return text
    return None


def parse_pasted_json(raw: bytes | str) -> object:
    """JSON strict tiré d'un fichier ou d'un texte collé (bloc ```json``` accepté)."""
    candidate = json_candidate(raw)
    if candidate is None:
        raise StrictJSONError("aucun JSON trouvé (ni objet { … }, ni bloc de code ```json```)")
    return parse_json(candidate.encode("utf-8"))
