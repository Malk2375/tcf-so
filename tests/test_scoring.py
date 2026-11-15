"""Score et niveau (SPEC 9)."""

from fractions import Fraction

import pytest

from exam.models import Level
from exam.scoring import (
    LEVEL_WEIGHTS,
    global_score,
    level_for,
    level_range,
    section_score,
    weighted_score,
)

# CO du mode réel : répartition 5/6/6/6/6/5.
DISTRIBUTION = {"A1": 5, "A2": 6, "B1": 6, "B2": 6, "C1": 6, "C2": 5}
TOTAL = sum(LEVEL_WEIGHTS[level] * n for level, n in DISTRIBUTION.items())


def weight_up_to(last_level: str) -> int:
    weight = 0
    for level, n in DISTRIBUTION.items():
        weight += LEVEL_WEIGHTS[level] * n
        if level == last_level:
            return weight
    raise ValueError(last_level)


def test_total_weight_of_real_co():
    assert TOTAL == 606


@pytest.mark.parametrize(
    ("last_level", "expected"),
    [
        ("A2", 234),
        # La section 9 annonce 313 ; la formule donne 699 × 270,75 / 606 = 312,30 → 312.
        ("B1", 312),
        ("B2", 421),
        ("C1", 556),
    ],
)
def test_all_right_up_to_level_then_a_quarter_of_remaining_weights(last_level, expected):
    done = weight_up_to(last_level)
    correct = done + Fraction(TOTAL - done, 4)
    assert weighted_score(correct, TOTAL) == expected


def test_all_right_and_all_wrong():
    results = [(level, True) for level, n in DISTRIBUTION.items() for _ in range(n)]
    assert section_score(results) == 699
    assert section_score([(level, False) for level, _ in results]) == 0


def test_section_score_weights_levels():
    # Une seule bonne réponse en C2 vaut plus que cinq bonnes réponses en A1.
    results = [("A1", True)] * 5 + [("C2", False)] * 4 + [("C2", True)]
    assert section_score(results) == weighted_score(15 + 33, 15 + 5 * 33)


@pytest.mark.parametrize(
    ("score", "level"),
    [
        (0, Level.BELOW_A1),
        (99, Level.BELOW_A1),
        (100, Level.A1),
        (199, Level.A1),
        (200, Level.A2),
        (300, Level.B1),
        (400, Level.B2),
        (499, Level.B2),
        (500, Level.C1),
        (600, Level.C2),
        (699, Level.C2),
    ],
)
def test_level_table(score, level):
    assert level_for(score) == level


def test_global_score_is_rounded_mean():
    assert global_score([300, 301, 301]) == 301  # 300,67
    assert global_score([100, 101, 100]) == 100  # 100,33
    assert global_score([100, 101, 0]) == 67  # 67,0
    assert global_score([1, 2, 2]) == 2  # 1,67
    assert global_score([0, 1, 0]) == 0  # 0,33


@pytest.mark.parametrize(
    ("score", "mode", "low", "high"),
    [
        (420, "rapide", Level.B1, Level.B2),  # 360 à 480
        (450, "rapide", Level.B1, Level.C1),  # 390 à 510
        (450, "moyen", Level.B2, Level.B2),  # 420 à 480
        (410, "moyen", Level.B1, Level.B2),  # 380 à 440
        (410, "reel", Level.B2, Level.B2),
        (30, "rapide", Level.BELOW_A1, Level.BELOW_A1),
        (680, "rapide", Level.C2, Level.C2),
    ],
)
def test_level_range_margin_by_mode(score, mode, low, high):
    assert level_range(score, mode) == (low, high)
