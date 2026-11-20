from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_POST

from advice import eligibility, services
from advice.content import resources
from advice.models import Advice
from exam.models import Attempt
from exam.services import proposed_test, published_tests


def _cards(advice: Advice) -> list[dict]:
    """Chaque conseil, avec les données utiles à l'affichage. Les anciens conseils (objectif,
    stratégie, plan de la semaine) s'affichent tels qu'ils ont été enregistrés."""
    findings = {f["id"]: f for f in advice.findings}
    errors = {e["test_item_id"]: e for f in advice.findings for e in f.get("errors", [])}
    # Un passage supprimé dans /admin/ n'a plus de correction à ouvrir.
    attempts = set(
        Attempt.objects.filter(
            pk__in={e["attempt_id"] for e in errors.values()}, user=advice.user_id
        ).values_list("pk", flat=True)
    )
    by_resource = {r["id"]: r for r in resources()}
    done = set(advice.plan_done)
    cards = []
    for index, item in enumerate(advice.content.get("items", [])):
        finding = findings.get(item["finding_id"], {})
        resource = item.get("resource") or {}
        cards.append(
            {
                "index": index,
                "item": item,
                "finding": finding,
                "reviews": [
                    {
                        **review,
                        "error": errors.get(review["test_item_id"]),
                        "linkable": errors.get(review["test_item_id"], {}).get("attempt_id")
                        in attempts,
                    }
                    for review in item["error_reviews"]
                ],
                "resource": {**by_resource[resource["resource_id"]], **resource}
                if resource.get("resource_id") in by_resource
                else None,
                "plan": [
                    {
                        **action,
                        "key": f"{index}-{number}",
                        "done": f"{index}-{number}" in done,
                        "resource": by_resource.get(action.get("resource_id")),
                    }
                    for number, action in enumerate(item.get("week_plan", []))
                ],
            }
        )
    return cards


def _advice_context(advice: Advice) -> dict:
    mode = advice.attempt.test.format
    return {
        "advice": advice,
        "cards": _cards(advice),
        "next_mode": mode,
        "next_test": proposed_test(advice.user, mode),
        "all_done": mode.names.all_done(published_tests(mode).count()),
    }


@login_required
def advice_page(request):
    """Derniers conseils ; avant le seuil, ce qu'il reste à passer."""
    history = list(Advice.objects.filter(user=request.user).select_related("attempt__test__format"))
    latest = history[0] if history else None
    pending = None
    if eligibility.is_eligible(request.user):
        retained = eligibility.retained_attempts(request.user)
        last_completed = retained[0] if retained else None
        if last_completed and (latest is None or latest.attempt_id != last_completed.pk):
            pending = last_completed  # conseils du dernier test pas encore rédigés
    context = {
        "remaining": eligibility.remaining_message(request.user),
        "history": history[1:],
        "pending": pending,
        **(_advice_context(latest) if latest else {}),
    }
    return render(request, "advice/advice.html", context)


@login_required
def advice_detail(request, pk):
    advice = get_object_or_404(Advice, pk=pk, user=request.user)
    context = {**_advice_context(advice), "is_archive": True}
    return render(request, "advice/detail.html", context)


@login_required
@require_POST
def attempt_advice(request, pk):
    """Bloc de conseils chargé par HTMX depuis la page de résultats (ou la page Conseils)."""
    attempt = get_object_or_404(Attempt, pk=pk, user=request.user)
    advice = services.advice_for(attempt)
    context = {
        "attempt": attempt,
        "advice": advice,
        "remaining": "" if advice else eligibility.remaining_message(request.user),
    }
    return render(request, "advice/includes/block.html", context)


@login_required
@require_POST
def plan_toggle(request, pk):
    """Case du plan de la semaine des anciens conseils : mémorisée en base."""
    advice = get_object_or_404(Advice, pk=pk, user=request.user)
    key = request.POST.get("key", "")
    valid = {
        f"{i}-{n}"
        for i, item in enumerate(advice.content.get("items", []))
        for n in range(len(item.get("week_plan", [])))
    }
    if key not in valid:
        return HttpResponseBadRequest("Action inconnue.")
    done = set(advice.plan_done)
    if request.POST.get("done"):
        done.add(key)
    else:
        done.discard(key)
    advice.plan_done = sorted(done)
    advice.save(update_fields=["plan_done"])
    return HttpResponse(status=204)
