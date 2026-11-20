"""Passages terminés factices, pour la base de démonstration (jamais pour la base réelle)."""

from datetime import datetime, timedelta

from django.utils import timezone

from bank.language_points import family_of
from bank.models import Test
from exam import services
from exam.models import Answer, Attempt


def finished_attempt(user, test: Test, wrong_families: set[str], finished_at: datetime) -> Attempt:
    """Passage terminé où seules les questions de MSL des familles `wrong_families` sont fausses
    (de quoi faire apparaître un constat `msl_family`)."""
    started = finished_at - timedelta(minutes=40)
    attempt = services.create_attempt(user, test, now=started)
    for test_item in test.test_items.select_related("item"):
        item = test_item.item
        is_wrong = family_of(item.tags.get("language_point")) in wrong_families
        selected = (item.correct_index + 1) % 4 if is_wrong else item.correct_index
        Answer.objects.create(
            attempt=attempt,
            test_item=test_item,
            selected_index=selected,
            first_seen_at=started,
            answered_at=started,
            time_spent_ms=20_000,
            choice_history=[{"index": selected, "at": started.isoformat()}],
        )
    services.complete_attempt(attempt, finished_at)
    attempt.refresh_from_db()
    return attempt


def finished_attempts(user, tests: list[Test], wrong_families: set[str]) -> list[Attempt]:
    """Un passage terminé par test, un jour d'écart, le dernier hier."""
    now = timezone.now()
    return [
        finished_attempt(user, test, wrong_families, now - timedelta(days=len(tests) - index))
        for index, test in enumerate(tests)
    ]
