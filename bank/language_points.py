"""Points de langue de la MSL, rangés en familles (SPEC 4.4, révision du 20 novembre 2026).

La table est dans `bank/data/language_points.json` : `id`, `family`, `levels`, `hint`.
Un point est valide pour un item si le niveau de l'item figure dans ses `levels`.
"""

import json
from functools import cache
from pathlib import Path

DATA = Path(__file__).resolve().parent / "data" / "language_points.json"

FAMILIES = ["verbes", "pronoms", "prepositions", "phrase", "lexique", "expressions"]
FAMILY_LABELS = {
    "verbes": "les verbes : temps, modes et accords",
    "pronoms": "les pronoms",
    "prepositions": "les déterminants et les prépositions",
    "phrase": "la construction de la phrase et les connecteurs",
    "lexique": "le vocabulaire",
    "expressions": "les expressions",
}

# Familles de MSL par test, selon le mode (section 4.4) : la somme vaut le nombre d'items de MSL.
FAMILY_QUOTAS = {
    "rapide": {
        "verbes": 1,
        "pronoms": 1,
        "prepositions": 1,
        "phrase": 1,
        "lexique": 1,
        "expressions": 1,
    },
    "moyen": {
        "verbes": 3,
        "pronoms": 2,
        "prepositions": 2,
        "phrase": 2,
        "lexique": 2,
        "expressions": 1,
    },
    "reel": {
        "verbes": 6,
        "pronoms": 4,
        "prepositions": 3,
        "phrase": 4,
        "lexique": 4,
        "expressions": 2,
    },
}

GRAMMAR_FAMILIES = {"verbes", "pronoms", "prepositions", "phrase"}
REGISTER_POINTS = {"registre", "registre_soutenu"}


@cache
def language_points() -> dict[str, dict]:
    """Les points, par identifiant, dans l'ordre du fichier."""
    rows = json.loads(DATA.read_text(encoding="utf-8"))
    return {row["id"]: row for row in rows}


def points_at(level: str, family: str | None = None) -> list[str]:
    return [
        point["id"]
        for point in language_points().values()
        if level in point["levels"] and (family is None or point["family"] == family)
    ]


def family_of(point_id: str | None) -> str | None:
    point = language_points().get(point_id or "")
    return point["family"] if point else None


def expected_skill(point_id: str) -> str:
    """Compétence attendue d'un item de MSL (section 4.4)."""
    if point_id in REGISTER_POINTS:
        return "registre"
    return "grammaire" if family_of(point_id) in GRAMMAR_FAMILIES else "lexique"
