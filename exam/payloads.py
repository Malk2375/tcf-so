"""Données envoyées au lecteur (SPEC 8.3).

Liste blanche de champs : ni `correct_index`, ni `is_correct`, ni `explanation`, ni le niveau
de l'item ne quittent le serveur tant que le passage n'est pas terminé.
"""

from datetime import datetime, timedelta

from django.urls import reverse
from django.utils import timezone

from bank.constants import SECTION_LABELS
from bank.models import TestItem
from exam import services
from exam.models import Answer, Attempt, AttemptSection


def iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _base(attempt: Attempt) -> dict:
    return {"server_time": iso(timezone.now()), "attempt_status": attempt.status}


def _redirect(attempt: Attempt) -> str | None:
    if attempt.status == Attempt.Status.COMPLETED:
        return reverse("attempt_results", args=[attempt.pk])
    if attempt.status == Attempt.Status.ABANDONED:
        return reverse("dashboard")
    return None


def section_summary(attempt_section: AttemptSection) -> dict:
    fs = services.format_section(attempt_section)
    test = attempt_section.attempt.test
    answered = Answer.objects.filter(
        attempt=attempt_section.attempt,
        test_item__section=attempt_section.section,
        selected_index__isnull=False,
    ).count()
    return {
        "order": attempt_section.order,
        "section": attempt_section.section,
        "label": SECTION_LABELS[attempt_section.section],
        "status": attempt_section.status,
        "n_items": fs.n_items,
        "duration_seconds": round(services.section_duration_seconds(test, fs)),
        "allow_back": fs.allow_back,
        "auto_advance": fs.auto_advance,
        "allow_early_next": fs.allow_early_next,
        "answer_window_seconds": fs.answer_window_seconds,
        "read_before_seconds": fs.read_before_seconds,
        "deadline_at": iso(attempt_section.deadline_at),
        "n_answered": answered,
    }


def state(attempt: Attempt) -> dict:
    return {
        **_base(attempt),
        "attempt_id": attempt.pk,
        "mode": attempt.mode,
        "expires_at": iso(attempt.expires_at),
        "redirect": _redirect(attempt),
        "sections": [section_summary(s) for s in attempt.sections.all()],
    }


def _answer_fields(answer: Answer | None) -> dict:
    return {
        "selected_index": answer.selected_index if answer else None,
        "flagged": answer.flagged if answer else False,
        "time_spent_ms": answer.time_spent_ms if answer else 0,
    }


def _item_text(test_item: TestItem) -> dict:
    item = test_item.item
    return {
        "test_item_id": test_item.pk,
        "position": test_item.position,
        "stimulus_text": item.stimulus_text,
        "question": item.question,
        "options": list(item.options),
    }


def _co_item(attempt_section: AttemptSection, answer: Answer) -> dict:
    test_item = answer.test_item
    item = test_item.item
    fs = services.format_section(attempt_section)
    audio = getattr(item, "audio", None)
    following = (
        services.section_items(attempt_section).filter(position__gt=test_item.position).first()
    )
    next_audio = getattr(following.item, "audio", None) if following else None
    audio_letters = item.options_mode == item.OptionsMode.AUDIO_LETTERS
    read = services.co_read_seconds(test_item, fs)
    return {
        "test_item_id": test_item.pk,
        "position": test_item.position,
        "question": item.question,
        # En mode « lettres lues », les options ne sont que dans l'audio.
        "options": None if audio_letters else list(item.options),
        "options_mode": item.options_mode,
        "audio_url": audio.file.url if audio else None,
        "audio_duration_ms": round(services.co_item_seconds(test_item) * 1000),
        "answer_window_seconds": fs.answer_window_seconds,
        # Temps de lecture avant l'audio (4.2, 8.5) ; le décompte reprend après un rechargement.
        "read_before_seconds": read,
        "reading_ends_at": iso(answer.first_seen_at + timedelta(seconds=read)),
        "first_seen_at": iso(answer.first_seen_at),
        "closes_at": iso(services.co_item_limit(answer, fs)),
        "audio_starts": answer.audio_starts,
        "can_play_audio": answer.audio_starts < services.MAX_AUDIO_STARTS,
        "next_audio_url": next_audio.file.url if next_audio else None,
        **_answer_fields(answer),
    }


def section_session(attempt_section: AttemptSection) -> dict:
    """Ce qu'il faut au lecteur pour (re)construire une épreuve en cours."""
    attempt = attempt_section.attempt
    data = {
        **_base(attempt),
        "redirect": _redirect(attempt),
        "section": section_summary(attempt_section),
    }
    if attempt_section.status != AttemptSection.Status.IN_PROGRESS:
        data["section_done"] = True
        return data
    data["section_done"] = False
    if attempt_section.section == "CO":
        data["item"] = _co_item(attempt_section, services.current_co_answer(attempt_section))
        return data
    answers = {
        a.test_item_id: a
        for a in Answer.objects.filter(attempt=attempt, test_item__section=attempt_section.section)
    }
    data["items"] = [
        {**_item_text(t), **_answer_fields(answers.get(t.pk))}
        for t in services.section_items(attempt_section)
    ]
    return data


def answer_ack(answer: Answer) -> dict:
    return {
        **_base(answer.attempt),
        "test_item_id": answer.test_item_id,
        **_answer_fields(answer),
    }
