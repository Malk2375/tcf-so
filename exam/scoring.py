"""Score et niveau estimé (SPEC 9). Proposition, pas le barème officiel."""

from fractions import Fraction
from math import floor

from exam.models import Level

MAX_SCORE = 699
LEVEL_WEIGHTS = {"A1": 3, "A2": 9, "B1": 15, "B2": 21, "C1": 26, "C2": 33}
# Marge de la fourchette de niveau, selon la précision du mode.
LEVEL_MARGIN = {"rapide": 60, "moyen": 30, "reel": 0}
# Seuils du tableau de la section 9 : 0–99 A1 non atteint, 100–199 A1, …, 600–699 C2.
SCORE_LEVELS = [
    Level.BELOW_A1,
    Level.A1,
    Level.A2,
    Level.B1,
    Level.B2,
    Level.C1,
    Level.C2,
]


def round_half_up(value: Fraction) -> int:
    return floor(value + Fraction(1, 2))


def weighted_score(correct_weight: Fraction | int, total_weight: int) -> int:
    """round(699 × poids des bonnes réponses / poids de tous les items)."""
    if not total_weight:
        return 0
    return round_half_up(Fraction(MAX_SCORE) * Fraction(correct_weight) / total_weight)


def section_score(results: list[tuple[str, bool]]) -> int:
    """Score d'une épreuve à partir de (niveau, juste ?) pour chacun de ses items."""
    total = sum(LEVEL_WEIGHTS[level] for level, _ in results)
    correct = sum(LEVEL_WEIGHTS[level] for level, ok in results if ok)
    return weighted_score(correct, total)


def level_for(score: int) -> Level:
    score = max(0, min(MAX_SCORE, score))
    return SCORE_LEVELS[score // 100]


def global_score(scores: list[int]) -> int:
    return round_half_up(Fraction(sum(scores), len(scores)))


def level_range(score: int, mode: str) -> tuple[Level, Level]:
    margin = LEVEL_MARGIN[mode]
    return level_for(score - margin), level_for(score + margin)
