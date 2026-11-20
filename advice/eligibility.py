"""Seuil d'éligibilité aux conseils (SPEC 12.1). Les modes ne se cumulent pas.

Seuls comptent les tests distincts : pour chaque test, le premier passage `completed` de
l'utilisateur. Les doublons enregistrés avant la règle « un test ne se passe qu'une fois »
(section 8.1) sont ignorés, pour le seuil comme pour les constats.
"""

from exam.models import Attempt

# Tests distincts terminés nécessaires, par mode.
THRESHOLDS = {"rapide": 3, "moyen": 2, "reel": 1}
MODE_WORDS = {
    "rapide": ("rapide", "rapides"),
    "moyen": ("moyen", "moyens"),
    "reel": ("réel", "réels"),
}


def retained_attempts(user) -> list[Attempt]:
    """Premier passage terminé de chaque test, du plus récent au plus ancien."""
    first: dict[int, Attempt] = {}
    attempts = (
        Attempt.objects.filter(user=user, status=Attempt.Status.COMPLETED)
        .select_related("test__format")
        .order_by("finished_at", "pk")
    )
    for attempt in attempts:
        first.setdefault(attempt.test_id, attempt)
    return sorted(first.values(), key=lambda a: (a.finished_at, a.pk), reverse=True)


def is_retained(attempt: Attempt) -> bool:
    """Ce passage est-il le premier passage terminé de son test ?"""
    if attempt.status != Attempt.Status.COMPLETED:
        return False
    earlier = Attempt.objects.filter(
        user=attempt.user_id, test=attempt.test_id, status=Attempt.Status.COMPLETED
    ).order_by("finished_at", "pk")
    return earlier.values_list("pk", flat=True).first() == attempt.pk


def completed_by_mode(user) -> dict[str, int]:
    """Nombre de tests distincts terminés, par mode."""
    counts = dict.fromkeys(THRESHOLDS, 0)
    for attempt in retained_attempts(user):
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
    """« encore 2 tests rapides, ou 2 tests moyens, ou 1 test réel » ; vide si éligible."""
    left = remaining(user)
    if not left:
        return ""
    parts = []
    for mode, n in left.items():
        singular, plural = MODE_WORDS[mode]
        parts.append(f"{n} test {singular}" if n == 1 else f"{n} tests {plural}")
    return "encore " + ", ou ".join(parts)
