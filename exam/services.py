"""Règles du passage de test, côté serveur (SPEC 8).

Le serveur fait foi pour le temps : chaque fonction qui touche un passage commence par `refresh`,
qui applique les échéances (clôture paresseuse, sans tâche de fond).
"""

import functools
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from django.db import transaction
from django.utils import timezone

from bank.content import estimated_seconds
from bank.models import FormatSection, Item, Test, TestItem
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


@dataclass
class TestProgress:
    """Où en est un utilisateur sur un test : passage complet terminé, ou épreuves terminées une
    par une (8.1 ter). Premier passage `completed` de chaque sorte ; un abandon ne compte pas."""

    n_sections: int
    full: Attempt | None = None
    parts: dict = field(default_factory=dict)  # épreuve → premier passage terminé

    @property
    def passed(self) -> bool:
        """Test « passé » : terminé en entier, ou ses trois épreuves terminées séparément."""
        return self.full is not None or len(self.parts) >= self.n_sections

    @property
    def touched(self) -> bool:
        """Au moins une épreuve terminée : le test complet n'est plus proposé."""
        return self.full is not None or bool(self.parts)

    def section_done(self, section: str) -> bool:
        return self.full is not None or section in self.parts


def test_progress(user, exam_format=None) -> dict[int, TestProgress]:
    attempts = (
        Attempt.objects.filter(user=user, status=Attempt.Status.COMPLETED)
        .select_related("test__format")
        .order_by("finished_at", "pk")
    )
    if exam_format is not None:
        attempts = attempts.filter(test__format=exam_format)
    progress: dict[int, TestProgress] = {}
    sections: dict[int, int] = {}
    for attempt in attempts:
        format_id = attempt.test.format_id
        if format_id not in sections:
            sections[format_id] = FormatSection.objects.filter(format_id=format_id).count()
        entry = progress.setdefault(attempt.test_id, TestProgress(n_sections=sections[format_id]))
        if attempt.scope == Attempt.Scope.FULL:
            entry.full = entry.full or attempt
        else:
            entry.parts.setdefault(attempt.scope, attempt)
    return progress


def completed_test_ids(user, exam_format=None) -> set[int]:
    """Tests que l'utilisateur a passés (en entier, ou épreuve par épreuve) : ils ne lui sont
    plus proposés (8.1)."""
    return {pk for pk, p in test_progress(user, exam_format).items() if p.passed}


def touched_test_ids(user, exam_format=None) -> set[int]:
    """Tests dont au moins une épreuve est terminée : le bouton de la carte ne les lance plus."""
    return {pk for pk, p in test_progress(user, exam_format).items() if p.touched}


def published_tests(exam_format):
    """Tests publiés du mode, dans l'ordre de leur numéro public (ordre de mise en ligne)."""
    return Test.objects.filter(format=exam_format, status=Test.Status.PUBLISHED).order_by(
        "public_number", "number"
    )


def proposed_test(user, exam_format) -> Test | None:
    """Le test publié de ce mode au plus petit numéro public que l'utilisateur n'a pas terminé
    (un passage abandonné ne compte pas), et dont aucune épreuve n'est faite (8.1 ter) ; None s'il
    n'y en a plus.
    C'est le test que lance le bouton de la carte ; la liste du mode permet d'en choisir un autre
    (8.1)."""
    done = touched_test_ids(user, exam_format)
    return published_tests(exam_format).exclude(pk__in=done).first()


# --- Durées --------------------------------------------------------------------------------------


def co_item_seconds(test_item: TestItem) -> float:
    """Durée de l'audio d'un item de CO (durée réelle, sinon estimée)."""
    item = test_item.item
    audio = getattr(item, "audio", None)
    if audio is not None:
        return audio.duration_ms / 1000
    return estimated_seconds(item.audio_script)


def co_read_seconds(test_item: TestItem, fs: FormatSection) -> int:
    """Temps de lecture avant l'audio (4.2) : seulement si les options sont écrites ; 0 pour
    `audio_letters`. Géré par le lecteur : aucune régénération, y compris des tests publiés."""
    if test_item.item.options_mode == Item.OptionsMode.AUDIO_LETTERS:
        return 0
    return fs.read_before_seconds or 0


def co_item_total(test_item: TestItem, fs: FormatSection) -> float:
    """Temps de lecture + audio + temps de réponse d'un item de CO."""
    return co_read_seconds(test_item, fs) + co_item_seconds(test_item) + fs.answer_window_seconds


def format_section(attempt_section: AttemptSection) -> FormatSection:
    return attempt_section.attempt.test.format.sections.get(section=attempt_section.section)


def section_items(attempt_section: AttemptSection):
    return (
        TestItem.objects.filter(test=attempt_section.attempt.test, section=attempt_section.section)
        .select_related("item", "item__audio")
        .order_by("position")
    )


def co_duration_seconds(test: Test, fs: FormatSection) -> float:
    """Durée de l'épreuve de CO : somme (lecture + audio + temps de réponse) des items (4.2)."""
    items = TestItem.objects.filter(test=test, section="CO").select_related("item", "item__audio")
    return sum(co_item_total(t, fs) for t in items)


def section_duration_seconds(test: Test, fs: FormatSection) -> float:
    if fs.section == "CO":
        return co_duration_seconds(test, fs)
    return fs.duration_seconds


# --- Création ------------------------------------------------------------------------------------


def create_attempt(
    user, test: Test, now: datetime | None = None, scope: str = Attempt.Scope.FULL
) -> Attempt:
    """Nouveau passage, complet ou d'une seule épreuve (8.1 ter). Un test ne se passe qu'une
    fois, et chacune de ses épreuves aussi, pour tous les comptes (8.1)."""
    exam_format = test.format
    sections = list(exam_format.sections.order_by("order"))
    if scope != Attempt.Scope.FULL:
        if not exam_format.allow_part_attempts or scope not in {fs.section for fs in sections}:
            raise AttemptError("part_not_allowed", "Épreuve seule impossible pour ce mode.")
        sections = [fs for fs in sections if fs.section == scope]
    progress = test_progress(user).get(test.pk)
    if progress is not None:
        if scope == Attempt.Scope.FULL and progress.passed:
            raise AttemptError("test_already_completed", exam_format.names.already_done)
        if scope == Attempt.Scope.FULL and progress.touched:
            raise AttemptError("test_partly_done", exam_format.names.partly_done)
        if scope != Attempt.Scope.FULL and progress.section_done(scope):
            raise AttemptError("section_already_completed", SECTION_ALREADY_DONE)
    if in_progress_attempt(user):
        raise AttemptError("attempt_in_progress", "Un passage est déjà en cours.")
    now = now or timezone.now()
    total = sum(section_duration_seconds(test, fs) for fs in sections)
    with transaction.atomic():
        return _create_attempt(user, test, sections, now, total, scope)


SECTION_ALREADY_DONE = "Vous avez déjà passé cette épreuve."


def _create_attempt(user, test, sections, now, total, scope=Attempt.Scope.FULL) -> Attempt:
    attempt = Attempt.objects.create(
        user=user,
        test=test,
        scope=scope,
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


def co_item_limit(answer: Answer, fs: FormatSection) -> datetime:
    """Instant où l'item de CO courant est clos d'office : lecture + audio + temps de réponse
    + 30 s après sa première apparition (8.4)."""
    seconds = co_item_total(answer.test_item, fs)
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
    fs = format_section(attempt_section)
    while attempt_section.status == AttemptSection.Status.IN_PROGRESS:
        answer = current_co_answer(attempt_section)
        limit = co_item_limit(answer, fs)
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
    fs = format_section(attempt_section)
    return sum(co_item_total(t, fs) for t in items)


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

    if not attempt.is_part:
        attempt.global_score = scoring.global_score(scores)
        attempt.global_level = scoring.level_for(attempt.global_score)
        attempt.level_low, attempt.level_high = scoring.level_range(
            attempt.global_score, attempt.mode
        )
    # Épreuve seule (8.1 ter) : score et niveau de l'épreuve, pas de score global.
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
