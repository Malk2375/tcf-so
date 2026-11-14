import hashlib
import random
from collections import Counter

import pytest

from bank.brief import _avoid_lines, balanced_letters, build_brief, create_brief
from bank.constants import (
    LANGUAGE_POINTS,
    LEVELS,
    QUESTION_REPONSE_SECONDS,
    expected_options_mode,
    length_target,
    subtypes_for,
)
from bank.models import Item

pytestmark = pytest.mark.django_db


def by_section(content):
    sections = {}
    for slot in content["slots"]:
        sections.setdefault(slot["section"], []).append(slot)
    return sections


@pytest.mark.parametrize("seed", range(5))
def test_real_mode_brief(formats, seed):
    content = build_brief(formats["reel"], 1, random.Random(seed))
    slots = content["slots"]
    assert len(slots) == 91
    assert [s["slot"] for s in slots] == list(range(1, 92))
    assert content["brief_id"] == "tcf_tp_so_reel-0001"
    assert content["format"] == "tcf_tp_so_reel"

    sections = by_section(content)
    assert [s["section"] for s in slots] == ["CO"] * 34 + ["MSL"] * 23 + ["CE"] * 34
    for section, section_slots in sections.items():
        letters = Counter(s["correct_letter"] for s in section_slots)
        assert set(letters) == {"A", "B", "C", "D"}
        assert max(letters.values()) - min(letters.values()) <= 1, (section, letters)
        section_themes = [s["theme"] for s in section_slots]
        assert len(section_themes) == len(set(section_themes)), section
        levels = [LEVELS.index(s["level"]) for s in section_slots]
        assert levels == sorted(levels)


def test_slot_contents_follow_rules(formats, rng):
    content = build_brief(formats["reel"], 1, rng)
    distribution = {s.section: s.level_distribution for s in formats["reel"].sections.all()}
    counts = Counter((s["section"], s["level"]) for s in content["slots"])
    for section, levels in distribution.items():
        for level, n in levels.items():
            assert counts[(section, level)] == n
    for slot in content["slots"]:
        section, level, subtype = slot["section"], slot["level"], slot["subtype"]
        assert subtype in subtypes_for(section, level)
        assert slot["options_mode"] == expected_options_mode(section, level, subtype)
        assert slot["target"] == length_target(section, level, subtype)
        if section == "MSL":
            assert slot["language_point"] in LANGUAGE_POINTS[level]
        else:
            assert "language_point" not in slot


def test_question_reponse_slot(formats):
    for seed in range(20):
        content = build_brief(formats["reel"], 1, random.Random(seed))
        for slot in content["slots"]:
            if slot["subtype"] == "question_reponse":
                assert slot["options_mode"] == "audio_letters"
                assert slot["target"] == {"audio_seconds": list(QUESTION_REPONSE_SECONDS)}
                return
    pytest.fail("aucun emplacement question_reponse tiré")


@pytest.mark.parametrize("n", [1, 6, 9, 17, 23, 34])
def test_balanced_letters(n, rng):
    letters = balanced_letters(n, rng)
    counts = Counter(letters)
    assert len(letters) == n
    assert max(counts.values()) - min(counts.values(), default=0) <= 1


def test_least_used_theme_comes_first(formats, rng):
    """Un thème déjà présent en banque pour la CO n'est repris qu'une fois les autres épuisés."""
    for index in range(3):
        Item.objects.create(
            section="CO",
            level="A1",
            subtype="annonce",
            options=["a", "b", "c", "d"],
            correct_index=0,
            explanation="Explication de test suffisante.",
            tags={"skill": "explicite", "theme": "transports"},
            content_hash=f"{index:064d}",
        )
    content = build_brief(formats["reel"], 1, rng)
    sections = by_section(content)
    assert "transports" not in {s["theme"] for s in sections["CO"]}


def test_least_used_language_points_cycle(formats, rng):
    content = build_brief(formats["reel"], 1, rng)
    a1_points = [s["language_point"] for s in by_section(content)["MSL"] if s["level"] == "A1"]
    assert len(a1_points) == 4
    assert len(set(a1_points)) == 4


def make_bank_item(theme, index, text="Bonjour Paul, je serai en retard ce soir. "):
    return Item.objects.create(
        section="CE",
        level="A1",
        subtype="message",
        stimulus_text=f"{text} {index}",
        question="Pourquoi Paul sera-t-il en retard ?",
        options=["a", "b", "c", "d"],
        correct_index=0,
        explanation="Explication de test suffisante.",
        tags={"skill": "explicite", "theme": theme},
        content_hash=hashlib.sha256(f"{theme}-{index}".encode()).hexdigest(),
    )


def test_avoid_lists_existing_items_of_same_themes(db):
    make_bank_item("sante", 1, "Le cabinet médical du docteur Lenoir sera fermé " * 3)
    make_bank_item("sport", 2)
    lines = _avoid_lines({"sante"})
    assert len(lines) == 1
    support, question = lines[0].split(" — ")
    assert support == ("Le cabinet médical du docteur Lenoir sera fermé " * 3)[:60]
    assert question == "Pourquoi Paul sera-t-il en retard ?"


def test_avoid_is_limited_to_40_lines(db):
    for index in range(45):
        make_bank_item("sante", index)
    assert len(_avoid_lines({"sante"})) == 40


def test_brief_avoid_uses_brief_themes(formats, rng):
    content = build_brief(formats["rapide"], 1, rng)
    theme = content["slots"][0]["theme"]
    make_bank_item(theme, 1)
    content = build_brief(formats["rapide"], 2, random.Random(0))
    expected = {s["theme"] for s in content["slots"]}
    assert content["avoid"] == _avoid_lines(expected)


def test_create_brief_numbers_increase(formats, staff_user, rng):
    first = create_brief(formats["rapide"], staff_user, rng)
    second = create_brief(formats["rapide"], staff_user, rng)
    other = create_brief(formats["moyen"], staff_user, rng)
    assert (first.number, second.number, other.number) == (1, 2, 1)
    assert second.brief_id == "tcf_tp_so_rapide-0002"
    assert second.filename == "brief_tcf_tp_so_rapide_0002.json"
