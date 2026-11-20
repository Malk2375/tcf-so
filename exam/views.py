from collections import defaultdict
from fractions import Fraction

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.templatetags.static import static
from django.urls import reverse
from django.views.decorators.http import require_POST

from advice.eligibility import remaining_message
from advice.models import Advice
from bank.constants import LEVELS, SECTION_LABELS
from bank.models import ExamFormat, Test
from exam import services
from exam.charts import score_chart
from exam.models import Answer, Attempt
from exam.overview import mode_overview
from exam.scoring import LEVEL_MARGIN, round_half_up
from exam.services import AttemptError


def _user_attempt(request, pk) -> Attempt:
    attempts = Attempt.objects.select_related("test__format")
    attempt = get_object_or_404(attempts, pk=pk, user=request.user)
    return services.refresh(attempt)


def _overviews(user, current) -> list:
    formats = ExamFormat.objects.filter(is_active=True).prefetch_related("sections")
    return [mode_overview(user, exam_format, current) for exam_format in formats]


@login_required
def dashboard(request):
    current = services.in_progress_attempt(request.user)
    # Le niveau global ne vient que des passages complets (8.1 ter).
    last = (
        Attempt.objects.filter(
            user=request.user, status=Attempt.Status.COMPLETED, scope=Attempt.Scope.FULL
        )
        .select_related("test__format")
        .first()
    )
    context = {
        "modes": _overviews(request.user, current),
        "current": current,
        "last": last,
        "latest_advice": Advice.objects.filter(user=request.user).first(),
        "advice_remaining": remaining_message(request.user),
    }
    return render(request, "exam/dashboard.html", context)


@login_required
def mode_tests(request, mode):
    """Liste des tests d'un mode (8.1 bis), dans l'ordre des numéros publics."""
    exam_format = get_object_or_404(
        ExamFormat.objects.prefetch_related("sections"), mode=mode, is_active=True
    )
    current = services.in_progress_attempt(request.user)
    overview = mode_overview(request.user, exam_format, current)
    return render(request, "exam/mode_tests.html", {"mode": overview})


@login_required
@require_POST
def attempt_create(request):
    """Lance un test (SPEC 8.1). Deux formes :

    - `test` : identifiant du test choisi sur la liste du mode. Un test qui n'est pas publié
      (brouillon, archivé, inexistant) donne 404 ; un test déjà terminé, 409 ;
    - `mode` : bouton de la carte du tableau de bord, qui lance le prochain test non passé.

    `scope` (avec `test`) : `CO`, `MSL` ou `CE` pour une seule épreuve (8.1 ter) ; 404 pour un
    format qui ne le permet pas, 409 si l'épreuve ou le test est déjà passé.

    Un autre passage en cours renvoie au tableau de bord, qui propose de le reprendre ou de
    l'abandonner. `create_attempt` revérifie : une requête forgée ne contourne rien."""
    scope = request.POST.get("scope") or Attempt.Scope.FULL
    if scope not in Attempt.Scope.values:
        raise Http404
    test_id = request.POST.get("test")
    if test_id:
        if not test_id.isdigit():
            raise Http404
        test = get_object_or_404(
            Test.objects.select_related("format"),
            pk=int(test_id),
            status=Test.Status.PUBLISHED,
            format__is_active=True,
        )
        exam_format = test.format
    elif scope != Attempt.Scope.FULL:
        raise Http404
    else:
        exam_format = get_object_or_404(ExamFormat, mode=request.POST.get("mode"), is_active=True)
        names = exam_format.names
        test = services.proposed_test(request.user, exam_format)
        if test is None:
            published = services.published_tests(exam_format)
            if published.exclude(pk__in=services.completed_test_ids(request.user)).exists():
                return _refused(request, names.partly_done, exam_format)
            if published.exists():
                return _refused(request, names.already_done, exam_format)
            messages.error(request, f"Aucun {names.noun} n'est disponible pour ce mode.")
            return redirect("dashboard")
    try:
        attempt = services.create_attempt(request.user, test, scope=scope)
    except AttemptError as exc:
        if exc.code == "part_not_allowed":
            raise Http404 from exc
        if exc.code in REFUSALS:
            return _refused(request, exc.message, exam_format)
        messages.error(request, "Un passage est déjà en cours : reprenez-le ou abandonnez-le.")
        return redirect("dashboard")
    return redirect("attempt_player", pk=attempt.pk)


# Refus d'un test ou d'une épreuve déjà passés (8.1, 8.1 ter) : HTTP 409.
REFUSALS = {"test_already_completed", "test_partly_done", "section_already_completed"}


def _refused(request, message: str, exam_format):
    context = {"message": message, "names": exam_format.names, "mode": exam_format.mode}
    return render(request, "exam/refused.html", context, status=409)


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
