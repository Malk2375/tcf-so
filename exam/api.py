"""Points d'entrée JSON du lecteur (SPEC 8.3).

Session de l'utilisateur propriétaire du passage et jeton CSRF exigés (en-tête X-CSRFToken).
"""

import functools
import json

from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_GET, require_POST

from exam import payloads, services
from exam.models import Attempt
from exam.services import AttemptError


def _error(code: str, message: str, status: int) -> JsonResponse:
    return JsonResponse({"error": code, "message": message}, status=status)


def attempt_api(func):
    """Charge le passage de l'utilisateur connecté ; transforme les refus en HTTP 409."""

    @functools.wraps(func)
    def wrapper(request, attempt_id, *args, **kwargs):
        if not request.user.is_authenticated:
            return _error("login_required", "Connexion requise.", 401)
        attempt = get_object_or_404(Attempt, pk=attempt_id, user=request.user)
        try:
            return func(request, attempt, *args, **kwargs)
        except AttemptError as exc:
            return _error(exc.code, exc.message, 409)

    return wrapper


def _body(request) -> dict:
    try:
        data = json.loads(request.body or b"{}")
    except (ValueError, UnicodeDecodeError):
        raise AttemptError("invalid_json", "Requête illisible.") from None
    if not isinstance(data, dict):
        raise AttemptError("invalid_json", "Requête illisible.")
    return data


def _int_or_none(value, field: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise AttemptError("invalid_field", f"Champ « {field} » invalide.")
    return value


@require_GET
@attempt_api
def state(request, attempt):
    services.refresh(attempt)
    return JsonResponse(payloads.state(attempt))


@require_POST
@attempt_api
def start_section(request, attempt, order):
    attempt_section = services.start_section(attempt, order)
    return JsonResponse(payloads.section_session(attempt_section))


@require_POST
@attempt_api
def answer(request, attempt):
    data = _body(request)
    test_item_id = _int_or_none(data.get("test_item_id"), "test_item_id")
    if test_item_id is None:
        raise AttemptError("invalid_field", "Champ « test_item_id » manquant.")
    recorded = services.record_answer(
        attempt,
        test_item_id,
        _int_or_none(data.get("selected_index"), "selected_index"),
        flagged=bool(data.get("flagged", False)),
        time_spent_ms=_int_or_none(data.get("time_spent_ms"), "time_spent_ms") or 0,
    )
    return JsonResponse(payloads.answer_ack(recorded))


@require_POST
@attempt_api
def co_audio_started(request, attempt):
    recorded = services.co_audio_started(attempt)
    return JsonResponse({**payloads.answer_ack(recorded), "audio_starts": recorded.audio_starts})


@require_POST
@attempt_api
def co_next(request, attempt):
    data = _body(request)
    attempt_section = services.co_next(
        attempt, _int_or_none(data.get("test_item_id"), "test_item_id")
    )
    return JsonResponse(payloads.section_session(attempt_section))


@require_POST
@attempt_api
def finish_section(request, attempt, order):
    attempt_section = services.finish_section(attempt, order)
    return JsonResponse(payloads.section_session(attempt_section))


@require_POST
@attempt_api
def abandon(request, attempt):
    services.abandon(attempt)
    return JsonResponse(payloads.state(attempt))
