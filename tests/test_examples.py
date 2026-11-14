"""Les fichiers d'exemple de docs/ respectent le schéma et les fourchettes de longueur.

docs/ n'est pas versionné : les tests lisent une copie dans tests/fixtures/ et vérifient,
quand docs/ est présent, que la copie est à jour.
"""

from pathlib import Path

import pytest

from bank.constants import LANGUAGE_POINTS, SKILLS, expected_options_mode, subtypes_for
from bank.content import length_check
from bank.schema import SCHEMA_PATH, schema_errors
from bank.themes import themes
from tests.conftest import FIXTURES, load_fixture

DOCS = Path(__file__).resolve().parent.parent / "docs"


@pytest.mark.parametrize(
    ("copy", "original"),
    [
        (SCHEMA_PATH, DOCS / "schema-import.json"),
        (FIXTURES / "items_exemple.json", DOCS / "exemples" / "items_exemple.json"),
        (FIXTURES / "exemples_reference.json", DOCS / "exemples" / "exemples_reference.json"),
    ],
)
def test_copies_match_docs(copy, original):
    if not original.exists():
        pytest.skip("docs/ absent")
    assert copy.read_bytes() == original.read_bytes(), f"Recopier {original} dans {copy}"


def test_items_exemple_matches_schema():
    assert schema_errors(load_fixture("items_exemple.json")) == []


def test_reference_examples_match_schema():
    data = load_fixture("exemples_reference.json")
    assert schema_errors(data) == []
    assert len(data["items"]) == 36


def test_reference_examples_cover_each_section_and_level_twice():
    items = load_fixture("exemples_reference.json")["items"]
    pairs = [(i["section"], i["level"]) for i in items]
    assert all(pairs.count(pair) == 2 for pair in pairs)
    assert len(set(pairs)) == 18


@pytest.mark.parametrize("item", load_fixture("exemples_reference.json")["items"], ids=str)
def test_reference_example_within_length_range(item):
    """Fourchettes de la section 4.3, sans la tolérance de 15 %."""
    value, low, high, unit = length_check(item)
    assert low <= value <= high, f"emplacement {item['slot']} : {value:.1f} {unit}"


@pytest.mark.parametrize("item", load_fixture("exemples_reference.json")["items"], ids=str)
def test_reference_example_uses_spec_vocabulary(item):
    section, level = item["section"], item["level"]
    assert item["subtype"] in subtypes_for(section, level)
    assert item["options_mode"] == expected_options_mode(section, level, item["subtype"])
    assert item["tags"]["skill"] in SKILLS[section]
    assert item["tags"]["theme"] in themes()
    if section == "MSL":
        assert item["tags"]["language_point"] in LANGUAGE_POINTS[level]
        assert item["stimulus_text"].count("___") == 1


def test_themes_list():
    ids = list(themes())
    assert len(ids) >= 60
    assert len(ids) == len(set(ids))
    example_themes = {i["tags"]["theme"] for i in load_fixture("items_exemple.json")["items"]}
    assert example_themes <= set(ids)
