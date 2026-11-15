"""Règles du passage de test, côté serveur (SPEC 8).

Le serveur fait foi pour le temps : chaque fonction qui touche un passage commence par `refresh`,
qui applique les échéances (clôture paresseuse, sans tâche de fond).
"""

import functools
from datetime import datetime, timedelta

from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from bank.content import estimated_seconds
from bank.models import FormatSection, Test, TestItem
from exam import scoring
from exam.models import Answer, Attempt, AttemptSection

# Marge au-delà de la durée totale avant qu'un passage non terminé soit abandonné (8.2).
EXPIRY_MARGIN = timedelta(minutes=30)
# MSL et CE : une réponse reste acceptée 3 s après l'échéance (8.4).
ANSWER_GRACE = timedelta(seconds=3)
# CO : un item resté ouvert plus de (audio + temps de réponse + 30 s) est clos d'office.
CO_ITEM_GRACE = timedelta(seconds=30)
# CO : l'audio peut redémarrer une fois après un rechargement (audio_starts ≤ 2).
MAX_AUDIO_STARTS = 2


class AttemptError(Exception):
    """Action refusée par les règles du passage (HTTP 409)."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


# --- Choix du test (8.1) -------------------------------------------------------------------------


def in_progress_attempt(user) -> Attempt | None:
    attempt = Attempt.objects.filter(user=user, status=Attempt.Status.IN_PROGRESS).first()
    if attempt:
        refresh(attempt)
        if attempt.status != Attempt.Status.IN_PROGRESS:
            return None
    return attempt


def proposed_test(user, exam_format) -> tuple[Test | None, datetime | None]:
    """Le plus ancien test publié pas encore terminé ; sinon le plus anciennement passé.

    Renvoie (test, date du dernier passage terminé, ou None si jamais terminé).
    """
    published = Test.objects.filter(format=exam_format, status=Test.Status.PUBLISHED).order_by(
        "published_at", "number"
    )
    completed = dict(
        Attempt.objects.filter(user=user, status=Attempt.Status.COMPLETED, test__in=published)
        .values("test")
        .annotate(last=Max("finished_at"))
        .values_list("test", "last")
    )
    for test in published:
        if test.pk not in completed:
            return test, None
    if not completed:
        return None, None
    test_id = min(completed, key=completed.get)
    return published.get(pk=test_id), completed[test_id]


# --- Durées --------------------------------------------------------------------------------------


def co_item_seconds(test_item: TestItem) -> float:
    """Durée de l'audio d'un item de CO (durée réelle, sinon estimée)."""
    item = test_item.item
    audio = getattr(item, "audio", None)
    if audio is not None:
        return audio.duration_ms / 1000
    return estimated_seconds(item.audio_script)


def format_section(attempt_section: AttemptSection) -> FormatSection:
    return attempt_section.attempt.test.format.sections.get(section=attempt_section.section)


def section_items(attempt_section: AttemptSection):
    return (
        TestItem.objects.filter(test=attempt_section.attempt.test, section=attempt_section.section)
        .select_related("item", "item__audio")
        .order_by("position")
    )


def co_duration_seconds(test: Test, answer_window: int) -> float:
    """Durée de l'épreuve de CO : somme (audio + temps de réponse) des items (4.2)."""
    items = TestItem.objects.filter(test=test, section="CO").select_related("item", "item__audio")
    return sum(co_item_seconds(t) + answer_window for t in items)


def section_duration_seconds(test: Test, fs: FormatSection) -> float:
    if fs.section == "CO":
        return co_duration_seconds(test, fs.answer_window_seconds)
    return fs.duration_seconds


# --- Création ------------------------------------------------------------------------------------


def create_attempt(user, test: Test, now: datetime | None = None) -> Attempt:
    if in_progress_attempt(user):
        raise AttemptError("attempt_in_progress", "Un passage est déjà en cours.")
    now = now or timezone.now()
    sections = list(test.format.sections.order_by("order"))
    total = sum(section_duration_seconds(test, fs) for fs in sections)
    with transaction.atomic():
        return _create_attempt(user, test, sections, now, total)


def _create_attempt(user, test, sections, now, total) -> Attempt:
    attempt = Attempt.objects.create(
        user=user,
        test=test,
        started_at=now,
        expires_at=now + timedelta(seconds=total) + EXPIRY_MARGIN,
    )
    AttemptSection.objects.bulk_create(
        AttemptSection(attempt=attempt, section=fs.section, order=fs.order) for fs in sections
    )
    return attempt


# --- Clôture paresseuse (8.4) --------------------------------------------------------------------


def refresh(attempt: Attempt, now: datetime | None = None) -> Attempt:
    """Applique les échéances : items de CO dépassés, épreuves échues, passage expiré."""
    if attempt.status != Attempt.Status.IN_PROGRESS:
        return attempt
    now = now or timezone.now()
    with transaction.atomic():
        if now > attempt.expires_at:
            attempt.status = Attempt.Status.ABANDONED
            attempt.finished_at = now
            attempt.save(update_fields=["status", "finished_at"])
            return attempt
        for attempt_section in attempt.sections.filter(status=AttemptSection.Status.IN_PROGRESS):
            if attempt_section.section == "CO":
                _expire_co_items(attempt_section, now)
            elif now > attempt_section.deadline_at + ANSWER_GRACE:
                close_section(attempt_section, attempt_section.deadline_at + ANSWER_GRACE)
    return attempt


def co_item_limit(answer: Answer, answer_window: int) -> datetime:
    """Instant où l'item de CO courant est clos d'office."""
    seconds = co_item_seconds(answer.test_item) + answer_window
    return answer.first_seen_at + timedelta(seconds=seconds) + CO_ITEM_GRACE


def current_co_answer(attempt_section: AttemptSection) -> Answer | None:
    if attempt_section.current_position is None:
        return None
    return (
        Answer.objects.select_related("test_item__item__audio")
        .filter(
            attempt=attempt_section.attempt,
            test_item__section="CO",
            test_item__position=attempt_section.current_position,
        )
        .first()
    )


def _expire_co_items(attempt_section: AttemptSection, now: datetime) -> None:
    """Clôt les items de CO dépassés. L'épreuve continue sans l'utilisateur : chaque item suivant
    est réputé ouvert à l'instant où le précédent a été clos."""
    window = format_section(attempt_section).answer_window_seconds
    while attempt_section.status == AttemptSection.Status.IN_PROGRESS:
        answer = current_co_answer(attempt_section)
        limit = co_item_limit(answer, window)
        if now <= limit:
            return
        _advance_co(attempt_section, limit)


def _advance_co(attempt_section: AttemptSection, at: datetime) -> None:
    """Passe à l'item de CO suivant (ouvert à l'instant `at`), ou clôt l'épreuve."""
    following = section_items(attempt_section).filter(position__gt=attempt_section.current_position)
    next_item = following.first()
    if next_item is None:
        close_section(attempt_section, at)
        return
    attempt_section.current_position = next_item.position
    attempt_section.deadline_at = at + timedelta(
        seconds=co_remaining_seconds(attempt_section, following)
    )
    attempt_section.save(update_fields=["current_position", "deadline_at"])
    Answer.objects.get_or_create(
        attempt=attempt_section.attempt, test_item=next_item, defaults={"first_seen_at": at}
    )


def co_remaining_seconds(attempt_section: AttemptSection, items) -> float:
    window = format_section(attempt_section).answer_window_seconds
    return sum(co_item_seconds(t) + window for t in items)


def close_section(attempt_section: AttemptSection, at: datetime) -> None:
    """Clôt une épreuve ; termine le passage après la dernière."""
    attempt = attempt_section.attempt
    attempt_section.status = AttemptSection.Status.DONE
    attempt_section.finished_at = at
    attempt_section.current_position = None
    attempt_section.save(update_fields=["status", "finished_at", "current_position"])
    # Chaque item reçoit une réponse, vide si l'utilisateur ne l'a pas vu.
    seen = set(
        Answer.objects.filter(
            attempt=attempt, test_item__section=attempt_section.section
        ).values_list("test_item_id", flat=True)
    )
    Answer.objects.bulk_create(
        Answer(attempt=attempt, test_item=test_item, first_seen_at=at)
        for test_item in section_items(attempt_section)
        if test_item.pk not in seen
    )
    if not attempt.sections.exclude(status=AttemptSection.Status.DONE).exists():
        complete_attempt(attempt, at)


# --- Score ---------------------------------------------------------------------------------------


def complete_attempt(attempt: Attempt, at: datetime) -> None:
    answers = list(Answer.objects.filter(attempt=attempt).select_related("test_item__item"))
    for answer in answers:
        answer.is_correct = answer.selected_index == answer.test_item.item.correct_index
    Answer.objects.bulk_update(answers, ["is_correct"])

    scores = []
    for attempt_section in attempt.sections.all():
        results = [
            (a.test_item.item.level, a.is_correct)
            for a in answers
            if a.test_item.section == attempt_section.section
        ]
        attempt_section.n_correct = sum(ok for _, ok in results)
        attempt_section.score = scoring.section_score(results)
        attempt_section.level = scoring.level_for(attempt_section.score)
        attempt_section.save(update_fields=["n_correct", "score", "level"])
        scores.append(attempt_section.score)

    attempt.global_score = scoring.global_score(scores)
    attempt.global_level = scoring.level_for(attempt.global_score)
    attempt.level_low, attempt.level_high = scoring.level_range(attempt.global_score, attempt.mode)
    attempt.status = Attempt.Status.COMPLETED
    attempt.finished_at = at
    attempt.save()


# --- Actions du lecteur (8.3) --------------------------------------------------------------------
#
# Chaque action applique d'abord les échéances (`refresh`, validé à part), puis agit dans sa propre
# transaction : une action refusée (AttemptError) n'annule pas les clôtures déjà appliquées.


def _atomic_action(func):
    @functools.wraps(func)
    def wrapper(attempt: Attempt, *args, now: datetime | None = None, **kwargs):
        now = now or timezone.now()
        refresh(attempt, now)
        with transaction.atomic():
            return func(attempt, *args, now=now, **kwargs)

    return wrapper


def _require_in_progress(attempt: Attempt) -> None:
    if attempt.status != Attempt.Status.IN_PROGRESS:
        raise AttemptError("attempt_closed", "Ce passage est terminé.")


def get_section(attempt: Attempt, order: int) -> AttemptSection:
    try:
        return attempt.sections.get(order=order)
    except AttemptSection.DoesNotExist:
        raise AttemptError("unknown_section", "Épreuve inconnue.") from None


def active_section(attempt: Attempt) -> AttemptSection | None:
    return attempt.sections.filter(status=AttemptSection.Status.IN_PROGRESS).first()


@_atomic_action
def start_section(attempt: Attempt, order: int, *, now: datetime) -> AttemptSection:
    """Démarre une épreuve. Idempotent : une épreuve déjà en cours est renvoyée telle quelle."""
    _require_in_progress(attempt)
    attempt_section = get_section(attempt, order)
    if attempt_section.status == AttemptSection.Status.IN_PROGRESS:
        return attempt_section
    if attempt_section.status == AttemptSection.Status.DONE:
        raise AttemptError("section_done", "Cette épreuve est terminée.")
    previous = attempt.sections.filter(order__lt=order)
    if previous.exclude(status=AttemptSection.Status.DONE).exists():
        raise AttemptError("section_locked", "Terminez d'abord l'épreuve précédente.")

    fs = format_section(attempt_section)
    attempt_section.status = AttemptSection.Status.IN_PROGRESS
    attempt_section.started_at = now
    if attempt_section.section == "CO":
        items = list(section_items(attempt_section))
        attempt_section.current_position = items[0].position
        attempt_section.deadline_at = now + timedelta(
            seconds=co_remaining_seconds(attempt_section, items)
        )
        Answer.objects.get_or_create(
            attempt=attempt, test_item=items[0], defaults={"first_seen_at": now}
        )
    else:
        attempt_section.deadline_at = now + timedelta(seconds=fs.duration_seconds)
    attempt_section.save()
    return attempt_section


@_atomic_action
def record_answer(
    attempt: Attempt,
    test_item_id: int,
    selected_index: int | None,
    flagged: bool = False,
    time_spent_ms: int = 0,
    *,
    now: datetime,
) -> Answer:
    # Après `refresh`, une épreuve de MSL ou de CE échue (échéance + 3 s) est déjà close.
    _require_in_progress(attempt)
    try:
        test_item = TestItem.objects.get(pk=test_item_id, test=attempt.test)
    except TestItem.DoesNotExist:
        raise AttemptError("unknown_item", "Question inconnue.") from None
    attempt_section = attempt.sections.get(section=test_item.section)
    if attempt_section.status != AttemptSection.Status.IN_PROGRESS:
        raise AttemptError("section_closed", "Épreuve close : réponse refusée.")
    if selected_index is not None and not 0 <= selected_index <= 3:
        raise AttemptError("invalid_option", "Option inconnue.")

    if test_item.section == "CO":
        if test_item.position != attempt_section.current_position:
            raise AttemptError("not_current", "Seule la question en cours accepte une réponse.")
        flagged = False

    answer, _ = Answer.objects.get_or_create(
        attempt=attempt,
        test_item=test_item,
        defaults={"first_seen_at": attempt_section.started_at},
    )
    if selected_index is not None:
        if answer.selected_index is not None and answer.selected_index != selected_index:
            answer.change_count += 1
        if answer.selected_index != selected_index:
            answer.answered_at = now
            answer.choice_history.append({"index": selected_index, "at": now.isoformat()})
        answer.selected_index = selected_index
    answer.flagged = bool(flagged)
    # Temps cumulé par le client, plafonné au temps écoulé depuis la première apparition.
    elapsed_ms = max(0, int((now - answer.first_seen_at).total_seconds() * 1000))
    answer.time_spent_ms = max(answer.time_spent_ms, min(max(0, int(time_spent_ms)), elapsed_ms))
    answer.save()
    return answer


@_atomic_action
def co_audio_started(attempt: Attempt, *, now: datetime) -> Answer:
    _require_in_progress(attempt)
    attempt_section = active_section(attempt)
    if attempt_section is None or attempt_section.section != "CO":
        raise AttemptError("no_co_item", "Aucune question de compréhension orale en cours.")
    answer = current_co_answer(attempt_section)
    answer.audio_starts += 1
    answer.save(update_fields=["audio_starts"])
    return answer


@_atomic_action
def co_next(attempt: Attempt, test_item_id: int | None, *, now: datetime) -> AttemptSection:
    """Clôt l'item de CO courant et passe au suivant. Jamais de retour en arrière.

    `test_item_id` désigne l'item que le client veut clore : si ce n'est plus l'item courant
    (requête renvoyée deux fois, item déjà clos d'office), rien ne bouge.
    """
    _require_in_progress(attempt)
    attempt_section = attempt.sections.get(section="CO")
    if attempt_section.status != AttemptSection.Status.IN_PROGRESS:
        return attempt_section
    answer = current_co_answer(attempt_section)
    if test_item_id is None or answer.test_item_id == test_item_id:
        _advance_co(attempt_section, now)
    return attempt_section


@_atomic_action
def finish_section(attempt: Attempt, order: int, *, now: datetime) -> AttemptSection:
    attempt_section = get_section(attempt, order)
    if attempt_section.status == AttemptSection.Status.DONE:
        return attempt_section
    _require_in_progress(attempt)
    if attempt_section.status != AttemptSection.Status.IN_PROGRESS:
        raise AttemptError("section_not_started", "Cette épreuve n'a pas commencé.")
    if attempt_section.section == "CO":
        raise AttemptError("co_auto", "La compréhension orale se termine d'elle-même.")
    close_section(attempt_section, min(now, attempt_section.deadline_at + ANSWER_GRACE))
    return attempt_section


@_atomic_action
def abandon(attempt: Attempt, *, now: datetime) -> Attempt:
    if attempt.status == Attempt.Status.IN_PROGRESS:
        attempt.status = Attempt.Status.ABANDONED
        attempt.finished_at = now
        attempt.save(update_fields=["status", "finished_at"])
    return attempt
