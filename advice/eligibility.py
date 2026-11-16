"""Seuil d'éligibilité aux conseils (SPEC 12.1). Les modes ne se cumulent pas."""

from django.db.models import Count

from exam.models import Attempt

# Passages terminés nécessaires, par mode.
THRESHOLDS = {"rapide": 3, "moyen": 2, "reel": 1}
MODE_WORDS = {
    "rapide": ("rapide", "rapides"),
    "moyen": ("moyen", "moyens"),
    "reel": ("réel", "réels"),
}


def completed_by_mode(user) -> dict[str, int]:
    rows = (
        Attempt.objects.filter(user=user, status=Attempt.Status.COMPLETED)
        .values("test__format__mode")
        .annotate(n=Count("id"))
    )
    counts = dict.fromkeys(THRESHOLDS, 0)
    for row in rows:
        counts[row["test__format__mode"]] = row["n"]
    return counts


def is_eligible(user) -> bool:
    counts = completed_by_mode(user)
    return any(counts[mode] >= needed for mode, needed in THRESHOLDS.items())


def remaining(user) -> dict[str, int]:
    """Passages qu'il reste à terminer dans chaque mode ; vide si l'utilisateur est éligible."""
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
