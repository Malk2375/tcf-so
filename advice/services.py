"""Déclenchement et production des conseils (SPEC 12.1, 12.3, 15).

L'appel à l'API se fait hors de toute transaction : les lectures sont terminées avant,
l'enregistrement se fait après.
"""

import logging
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.db.models import Sum

from advice import content as advice_content
from advice import eligibility
from advice.findings import compute_findings
from advice.llm import SYSTEM_PROMPT, LLMError, LLMProvider, LLMResult, get_provider, user_message
from advice.models import Advice
from exam.models import Attempt
from usage.models import SiteSettings, UsageEntry, current_month

logger = logging.getLogger(__name__)

LLM_KINDS = [UsageEntry.Kind.LLM_ADVICE, UsageEntry.Kind.LLM_KEYCHECK]


def api_spent(month: str | None = None) -> Decimal:
    entries = UsageEntry.objects.filter(month=month or current_month(), kind__in=LLM_KINDS)
    return entries.aggregate(total=Sum("cost_usd"))["total"] or Decimal(0)


def budget_reached() -> bool:
    return api_spent() >= SiteSettings.load().api_monthly_budget_usd


def cost(model: str, input_tokens: int, output_tokens: int) -> Decimal:
    prices = SiteSettings.load().price_table.get(model, {})
    per_million = (
        Decimal(str(prices.get("input", 0))) * input_tokens
        + Decimal(str(prices.get("output", 0))) * output_tokens
    )
    return (per_million / 1_000_000).quantize(Decimal("0.000001"))


def _record_usage(result: LLMResult, provider: LLMProvider, attempt: Attempt) -> Decimal:
    amount = cost(result.model, result.input_tokens, result.output_tokens)
    UsageEntry.objects.create(
        kind=UsageEntry.Kind.LLM_ADVICE,
        provider=provider.name,
        model=result.model,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        cost_usd=amount,
        ref=f"conseils du passage {attempt.pk}",
    )
    return amount


def _context(attempt: Attempt, attempt_ids: list[int]) -> dict:
    return {
        "mode_of_last_test": attempt.test.format.mode,
        "tests_analysed": len(attempt_ids),
        "last_test_levels": {
            s.section: s.get_level_display() for s in attempt.sections.all() if s.level
        },
    }


def generate(attempt: Attempt, provider: LLMProvider | None = None) -> Advice:
    """Calcule les constats, appelle le modèle si possible, sinon modèles de secours."""
    user = attempt.user
    previous = Advice.objects.filter(user=user).exclude(attempt=attempt).first()
    findings, attempt_ids = compute_findings(user, previous.findings if previous else None)
    site = SiteSettings.load()
    data = {"source": Advice.Source.FALLBACK, "model": "", "fallback_reason": ""}
    content = None
    main = [f for f in findings if f["type"] != "progress"]
    try:
        provider = provider or get_provider()
    except LLMError as exc:
        provider, data["fallback_reason"] = None, str(exc)
    if not main:
        data["fallback_reason"] = "Aucun constat à rédiger."
    elif provider is None:
        data["fallback_reason"] = data["fallback_reason"] or "LLM_PROVIDER=fake."
    elif budget_reached():
        data["fallback_reason"] = "Plafond mensuel de l'API atteint."
    else:
        try:
            result = provider.generate(
                site.advice_model,
                SYSTEM_PROMPT,
                user_message(findings, _context(attempt, attempt_ids)),
                advice_content.output_schema(),
            )
        except LLMError as exc:
            data["fallback_reason"] = str(exc)
        else:
            amount = _record_usage(result, provider, attempt)
            data.update(
                model=result.model,
                input_tokens=result.input_tokens,
                output_tokens=result.output_tokens,
                cost_usd=amount,
            )
            problems = (
                advice_content.validate(result.content, findings)
                if result.content is not None
                else ["Réponse vide, refusée ou tronquée."]
            )
            if problems:
                data["fallback_reason"] = ("Réponse invalide : " + " ; ".join(problems))[:300]
                logger.warning("Conseils du passage %s : %s", attempt.pk, problems)
            else:
                content, data["source"] = result.content, Advice.Source.LLM
    if content is None:
        content = advice_content.fallback_content(findings)
    try:
        with transaction.atomic():
            return Advice.objects.create(
                user=user,
                attempt=attempt,
                attempt_ids=attempt_ids,
                findings=findings,
                content=content,
                **data,
            )
    except IntegrityError:
        # Deux demandes simultanées : la première enregistrée fait foi.
        return Advice.objects.get(attempt=attempt)


def advice_for(attempt: Attempt, provider: LLMProvider | None = None) -> Advice | None:
    """Conseils d'un passage terminé : existants, ou produits si l'utilisateur est éligible."""
    if attempt.status != Attempt.Status.COMPLETED:
        return None
    existing = Advice.objects.filter(attempt=attempt).first()
    if existing:
        return existing
    if not eligibility.is_eligible(attempt.user):
        return None
    return generate(attempt, provider)
