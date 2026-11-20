"""Seuil d'éligibilité aux conseils (SPEC 12.1). Les modes ne se cumulent pas.

Seuls comptent les tests distincts : pour chaque test, le premier passage `completed` de
l'utilisateur ; un entraînement moyen passé épreuve par épreuve compte quand ses trois épreuves
sont terminées (section 8.1 ter). Les doublons enregistrés avant la règle « un test ne se passe
qu'une fois » (section 8.1) sont ignorés, pour le seuil comme pour les constats.
"""

from bank.models import FormatSection
from bank.modes import mode_names
from exam.models import Attempt

# Tests distincts terminés nécessaires, par mode.
THRESHOLDS = {"rapide": 3, "moyen": 2, "reel": 1}


def _covered(user) -> dict[tuple[int, str], Attempt]:
    """Premier passage terminé couvrant chaque couple (test, épreuve) : un passage complet couvre
    les trois épreuves, un passage d'épreuve (8.1 ter) la sienne."""
    covered: dict[tuple[int, str], Attempt] = {}
    sections: dict[int, list[str]] = {}
    attempts = (
        Attempt.objects.filter(user=user, status=Attempt.Status.COMPLETED)
        .select_related("test__format")
        .order_by("finished_at", "pk")
    )
    for attempt in attempts:
        format_id = attempt.test.format_id
        if format_id not in sections:
            sections[format_id] = list(
                FormatSection.objects.filter(format_id=format_id).values_list("section", flat=True)
            )
        for section in [attempt.scope] if attempt.is_part else sections[format_id]:
            covered.setdefault((attempt.test_id, section), attempt)
    return covered


def retained_attempts(user) -> list[Attempt]:
    """Passages retenus, du plus récent au plus ancien : pour chaque test et chaque épreuve, le
    premier passage terminé (complet, ou de cette seule épreuve)."""
    unique = {attempt.pk: attempt for attempt in _covered(user).values()}
    return sorted(unique.values(), key=lambda a: (a.finished_at, a.pk), reverse=True)


def is_retained(attempt: Attempt) -> bool:
    """Ce passage est-il retenu (premier passage terminé pour au moins une de ses épreuves) ?"""
    if attempt.status != Attempt.Status.COMPLETED:
        return False
    return any(a.pk == attempt.pk for a in _covered(attempt.user).values())


def completed_by_mode(user) -> dict[str, int]:
    """Nombre de tests distincts passés, par mode : en entier, ou leurs trois épreuves
    séparément (8.1 ter)."""
    counts = dict.fromkeys(THRESHOLDS, 0)
    by_test: dict[int, tuple[Attempt, set]] = {}
    for (test_id, section), attempt in _covered(user).items():
        by_test.setdefault(test_id, (attempt, set()))[1].add(section)
    for attempt, sections in by_test.values():
        expected = attempt.test.format.sections.count()
        if len(sections) >= expected:
            counts[attempt.test.format.mode] += 1
    return counts


def is_eligible(user) -> bool:
    counts = completed_by_mode(user)
    return any(counts[mode] >= needed for mode, needed in THRESHOLDS.items())


def remaining(user) -> dict[str, int]:
    """Tests qu'il reste à terminer dans chaque mode ; vide si l'utilisateur est éligible."""
    counts = completed_by_mode(user)
    left = {mode: needed - counts[mode] for mode, needed in THRESHOLDS.items()}
    if any(n <= 0 for n in left.values()):
        return {}
    return left


def remaining_message(user) -> str:
    """« encore 2 entraînements rapides, ou 2 entraînements moyens, ou 1 test en conditions
    réelles » ; vide si éligible."""
    left = remaining(user)
    if not left:
        return ""
    return "encore " + ", ou ".join(mode_names(mode).count(n) for mode, n in left.items())
