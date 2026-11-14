import json
from functools import cache
from pathlib import Path

THEMES_PATH = Path(__file__).resolve().parent / "data" / "themes.json"


@cache
def themes() -> dict[str, str]:
    """Identifiant → libellé, dans l'ordre du fichier."""
    return {t["id"]: t["label"] for t in json.loads(THEMES_PATH.read_text(encoding="utf-8"))}
