from collections import defaultdict
from fractions import Fraction

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.templatetags.static import static
from django.urls import reverse
from django.views.decorators.http import require_POST

from advice.eligibility import MODE_WORDS, remaining_message
from advice.models import Advice
from bank.constants import LEVELS, SECTION_LABELS
from bank.models import ExamFormat
from config.views import mode_cards
from exam import services
from exam.charts import score_chart
from exam.models import Answer, Attempt
from exam.scoring import LEVEL_MARGIN, round_half_up
from exam.services import AttemptError


def _user_attempt(request, pk) -> Attempt:
    attempts = Attempt.objects.select_related("test__format")
    attempt = get_object_or_404(attempts, pk=pk, user=request.user)
    return services.refresh(attempt)


@login_required
def dashboard(request):
    current = services.in_progress_attempt(request.user)
    cards = []
    for card in mode_cards():
        exam_format = card["format"]
        published = services.published_tests(exam_format)
        available = published.count()
        remaining = published.exclude(
            pk__in=services.completed_test_ids(request.user, exam_format)
        ).count()
        cards.append(
            {
                **card,
                "available": available,
                "remaining": remaining,
                "test": services.proposed_test(request.user, exam_format),
                "all_done": all_done_message(exam_format.mode, available)
                if available and not remaining
                else "",
            }
        )
    last = (
        Attempt.objects.filter(user=request.user, status=Attempt.Status.COMPLETED)
        .select_related("test__format")
        .first()
    )
    context = {
        "modes": cards,
        "current": current,
        "last": last,
        "latest_advice": Advice.objects.filter(user=request.user).first(),
        "advice_remaining": remaining_message(request.user),
    }
    return render(request, "exam/dashboard.html", context)


@login_required
@require_POST
def attempt_create(request):
    """Lance le test proposé pour ce mode. Un test déjà terminé est refusé (409, section 8.1),
    même par une requête forgée : le serveur choisit le test, et `create_attempt` vérifie."""
    exam_format = get_object_or_404(ExamFormat, mode=request.POST.get("mode"), is_active=True)
    test = services.proposed_test(request.user, exam_format)
    if test is None:
        if services.published_tests(exam_format).exists():
            return _refused(request, services.ALREADY_DONE)
        messages.error(request, "Aucun test n'est disponible pour ce mode.")
        return redirect("dashboard")
    try:
        attempt = services.create_attempt(request.user, test)
    except AttemptError as exc:
        if exc.code == "test_already_completed":
            return _refused(request, exc.message)
        messages.error(request, "Un passage est déjà en cours : reprenez-le ou abandonnez-le.")
        return redirect("dashboard")
    return redirect("attempt_player", pk=attempt.pk)


def all_done_message(mode: str, available: int) -> str:
    singular, plural = MODE_WORDS[mode]
    if available == 1:
        passed = f"Vous avez passé le seul test {singular} disponible."
    else:
        passed = f"Vous avez passé les {available} tests {plural} disponibles."
    return f"{passed} De nouveaux tests seront ajoutés."


def _refused(request, message: str):
    return render(request, "exam/refused.html", {"message": message}, status=409)


@login_required
@require_POST
def attempt_abandon(request, pk):
    attempt = _user_attempt(request, pk)
    if attempt.status == Attempt.Status.IN_PROGRESS:
        services.abandon(attempt)
        messages.success(request, "Le passage a été abandonné.")
    return redirect("dashboard")


@login_required
def attempt_player(request, pk):
    attempt = _user_attempt(request, pk)
    if attempt.status == Attempt.Status.COMPLETED:
        return redirect("attempt_results", pk=attempt.pk)
    if attempt.status == Attempt.Status.ABANDONED:
        messages.error(request, "Ce passage a été abandonné ou a expiré.")
        return redirect("dashboard")
    config = {
        "attemptId": attempt.pk,
        "mode": attempt.mode,
        "silenceUrl": static("audio/silence.mp3"),
        "soundCheckUrl": static("audio/sound-check.mp3"),
        "urls": {
            "state": reverse("api_attempt_state", args=[attempt.pk]),
            "sections": {
                s.order: {
                    "start": reverse("api_section_start", args=[attempt.pk, s.order]),
                    "finish": reverse("api_section_finish", args=[attempt.pk, s.order]),
                }
                for s in attempt.sections.all()
            },
            "answers": reverse("api_answers", args=[attempt.pk]),
            "audioStarted": reverse("api_co_audio_started", args=[attempt.pk]),
            "coNext": reverse("api_co_next", args=[attempt.pk]),
            "abandon": reverse("api_abandon", args=[attempt.pk]),
            "dashboard": reverse("dashboard"),
        },
    }
    return render(request, "exam/player.html", {"attempt": attempt, "player_config": config})


def _completed_attempt(request, pk):
    attempt = _user_attempt(request, pk)
    if attempt.status != Attempt.Status.COMPLETED:
        return attempt, redirect("attempt_player", pk=attempt.pk)
    return attempt, None


def _answers_by_section(attempt):
    answers = (
        Answer.objects.filter(attempt=attempt)
        .select_related("test_item__item__audio")
        .order_by("test_item__position")
    )
    grouped = defaultdict(list)
    for answer in answers:
        grouped[answer.test_item.section].append(answer)
    return grouped


@login_required
def attempt_results(request, pk):
    attempt, response = _completed_attempt(request, pk)
    if response:
        return response
    grouped = _answers_by_section(attempt)
    sections = []
    for attempt_section in attempt.sections.all():
        answers = grouped[attempt_section.section]
        by_level = []
        for level in LEVELS:
            at_level = [a for a in answers if a.test_item.item.level == level]
            if at_level:
                correct = sum(a.is_correct for a in at_level)
                by_level.append(
                    {
                        "level": level,
                        "correct": correct,
                        "total": len(at_level),
                        "percent": round_half_up(Fraction(100 * correct, len(at_level))),
                    }
                )
        sections.append(
            {
                "obj": attempt_section,
                "label": SECTION_LABELS[attempt_section.section],
                "total": len(answers),
                "unanswered": sum(a.selected_index is None for a in answers),
                "by_level": by_level,
                "seconds_used": round_half_up(
                    Fraction(
                        (attempt_section.finished_at - attempt_section.started_at).total_seconds()
                    )
                )
                if attempt_section.started_at and attempt_section.finished_at
                else None,
            }
        )
    context = {
        "attempt": attempt,
        "sections": sections,
        "margin": LEVEL_MARGIN[attempt.mode],
    }
    return render(request, "exam/results.html", context)


@login_required
def attempt_review(request, pk):
    attempt, response = _completed_attempt(request, pk)
    if response:
        return response
    grouped = _answers_by_section(attempt)
    sections = [
        {
            "code": s.section,
            "label": SECTION_LABELS[s.section],
            "answers": grouped[s.section],
        }
        for s in attempt.sections.all()
    ]
    return render(request, "exam/review.html", {"attempt": attempt, "sections": sections})


@login_required
def history(request):
    attempts = list(
        Attempt.objects.filter(user=request.user)
        .exclude(status=Attempt.Status.IN_PROGRESS)
        .select_related("test__format")
        .prefetch_related("sections")
    )
    completed = [a for a in attempts if a.status == Attempt.Status.COMPLETED]
    chart = score_chart(list(reversed(completed)))
    return render(request, "exam/history.html", {"attempts": attempts, "chart": chart})
