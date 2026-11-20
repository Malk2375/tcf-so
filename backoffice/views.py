import json
from decimal import Decimal

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.core.files.storage import default_storage
from django.db.models import Count, Sum
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods, require_POST

from accounts import lockout
from accounts.reset import make_reset_link
from backoffice.decorators import staff_required
from backoffice.forms import BriefForm, ImportForm
from bank.brief import create_brief
from bank.constants import MAX_IMPORT_FILE_BYTES, MAX_SPEAKERS
from bank.importer import import_files
from bank.models import AudioAsset, Brief, ExamFormat, ImportBatch, Test
from bank.synthesis import SAMPLE_TEXT, voice_sample_path
from bank.tts import TTSError, chirp3_voices, get_provider
from usage.models import (
    SiteSettings,
    UsageEntry,
    current_month,
    tts_chars_remaining,
    tts_chars_used,
)

# Caractères synthétisés par test, tant qu'aucun test du mode n'a été synthétisé (SPEC 15).
DEFAULT_CHARS_PER_TEST = {"rapide": 5_000, "moyen": 9_500, "reel": 19_000}
MIN_VOICES_PER_GENDER = MAX_SPEAKERS  # un document peut avoir trois locuteurs du même genre


@staff_required
@require_http_methods(["GET", "POST"])
def users(request):
    User = get_user_model()
    generated = None
    if request.method == "POST":
        user = get_object_or_404(User, pk=request.POST.get("user_id"))
        if request.POST.get("action") == "unlock":
            # Débloquer la connexion (SPEC 13) : efface le compteur d'échecs de ce compte.
            lockout.unlock(user.email)
            messages.success(request, f"Connexion débloquée pour {user.email}.")
            return redirect("backoffice:users")
        generated = {"user": user, "link": make_reset_link(user)}
    accounts = list(User.objects.order_by("-date_joined"))
    for account in accounts:
        account.login_locked = bool(account.email) and lockout.is_locked(account.email)
    return render(request, "backoffice/users.html", {"accounts": accounts, "generated": generated})


def _brief_download(brief: Brief) -> HttpResponse:
    content = json.dumps(brief.content, ensure_ascii=False, indent=2)
    response = HttpResponse(content, content_type="application/json; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{brief.filename}"'
    return response


@staff_required
@require_http_methods(["GET", "POST"])
def briefs(request):
    form = BriefForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        brief = create_brief(form.cleaned_data["exam_format"], user=request.user)
        return _brief_download(brief)
    recent = Brief.objects.select_related("format")[:20]
    return render(request, "backoffice/briefs.html", {"form": form, "briefs": recent})


@staff_required
def brief_download(request, pk):
    return _brief_download(get_object_or_404(Brief, pk=pk))


def _render_import(request, form, batch=None):
    context = {
        "form": form,
        "batch": batch,
        "test": batch.tests.first() if batch else None,
        "show_files": batch is not None and len(batch.filenames) > 1,
        "recent": ImportBatch.objects.select_related("format", "brief")[:20],
    }
    return render(request, "backoffice/import.html", context)


@staff_required
@require_http_methods(["GET", "POST"])
def import_view(request):
    form = ImportForm(request.POST or None, request.FILES or None)
    batch = None
    if request.method == "POST" and form.is_valid():
        # Lecture bornée : un fichier trop gros est signalé sans être chargé en entier.
        files = [(f.name, f.read(MAX_IMPORT_FILE_BYTES + 1)) for f in form.cleaned_data["files"]]
        batch = import_files(files, user=request.user).batch
    return _render_import(request, form, batch)


@staff_required
def import_report(request, pk):
    """Réaffiche le rapport d'un import, par exemple pour le coller dans le Projet de rédaction."""
    return _render_import(request, ImportForm(), get_object_or_404(ImportBatch, pk=pk))


# --- Tableau de bord du back-office (11, 15) ---------------------------------------------------


def chars_per_test(exam_format: ExamFormat) -> int:
    """Moyenne des caractères facturés par test synthétisé du mode, sinon la valeur initiale."""
    totals = (
        AudioAsset.objects.filter(item__test_item__test__format=exam_format)
        .values("item__test_item__test")
        .annotate(chars=Sum("chars_billed"))
    )
    # L'audio factice de make_dev_test n'est pas facturé (0 caractère) : il ne compte pas.
    values = [row["chars"] for row in totals if row["chars"]]
    if not values:
        return DEFAULT_CHARS_PER_TEST[exam_format.mode]
    return max(1, (2 * sum(values) + len(values)) // (2 * len(values)))


@staff_required
def home(request):
    site = SiteSettings.load()
    month = current_month()
    remaining = tts_chars_remaining()
    used = tts_chars_used(month)
    stock = []
    for exam_format in ExamFormat.objects.filter(is_active=True):
        tests = Test.objects.filter(format=exam_format)
        per_test = chars_per_test(exam_format)
        stock.append(
            {
                "format": exam_format,
                "published": tests.filter(status=Test.Status.PUBLISHED).count(),
                "drafts": tests.filter(status=Test.Status.DRAFT).count(),
                "chars_per_test": per_test,
                "tests_left": remaining // per_test,
            }
        )
    api_spent = UsageEntry.objects.filter(
        month=month, kind__in=[UsageEntry.Kind.LLM_ADVICE, UsageEntry.Kind.LLM_KEYCHECK]
    ).aggregate(total=Sum("cost_usd"))["total"] or Decimal(0)
    context = {
        "month": month,
        "stock": stock,
        "tts": {
            "quota": site.tts_monthly_char_quota,
            "limit": site.tts_effective_quota,
            "used": used,
            "remaining": remaining,
            "percent": min(100, (100 * used) // max(1, site.tts_effective_quota)),
        },
        "api": {"spent": api_spent, "budget": site.api_monthly_budget_usd},
        "recent": ImportBatch.objects.select_related("format", "brief")[:5],
        "provider": settings.TTS_PROVIDER,
    }
    return render(request, "backoffice/home.html", context)


@staff_required
def usage(request):
    labels = dict(UsageEntry.Kind.choices)
    totals = list(
        UsageEntry.objects.values("month", "kind")
        .annotate(
            chars=Sum("chars"),
            input_tokens=Sum("input_tokens"),
            output_tokens=Sum("output_tokens"),
            cost=Sum("cost_usd"),
            count=Count("id"),
        )
        .order_by("-month", "kind")
    )
    for row in totals:
        row["label"] = labels.get(row["kind"], row["kind"])
    entries = UsageEntry.objects.all()[:200]
    return render(request, "backoffice/usage.html", {"totals": totals, "entries": entries})


# --- Voix (7.2) ---------------------------------------------------------------------------------


def _voices():
    try:
        return chirp3_voices(get_provider()), None
    except TTSError as exc:
        return [], str(exc)


def _save_casting(request, voice_list) -> list[str]:
    by_name = {voice.name: voice for voice in voice_list}
    instructions = request.POST.get("instructions", "")
    cast = [
        name for name in request.POST.getlist("cast") if name in by_name and name != instructions
    ]
    casting = {
        "instructions": instructions,
        "F": [name for name in cast if by_name[name].gender == "F"],
        "M": [name for name in cast if by_name[name].gender == "M"],
    }
    errors = []
    if instructions not in by_name:
        errors.append("Choisissez la voix des consignes.")
    for gender, label in (("F", "féminines"), ("M", "masculines")):
        if len(casting[gender]) < MIN_VOICES_PER_GENDER:
            errors.append(
                f"Choisissez au moins {MIN_VOICES_PER_GENDER} voix {label} pour les documents "
                "(un document peut avoir trois locuteurs du même genre)."
            )
    if not errors:
        site = SiteSettings.load()
        site.tts_casting = casting
        site.save(update_fields=["tts_casting"])
    return errors


@staff_required
@require_http_methods(["GET", "POST"])
def voices(request):
    voice_list, error = _voices()
    errors = []
    if request.method == "POST" and voice_list:
        errors = _save_casting(request, voice_list)
        if not errors:
            messages.success(request, "Distribution des voix enregistrée.")
            return redirect("backoffice:voices")
    casting = SiteSettings.load().tts_casting
    context = {
        "voices": voice_list,
        "error": error,
        "errors": errors,
        "instructions": request.POST.get("instructions") if errors else casting.get("instructions"),
        "cast": set(request.POST.getlist("cast"))
        if errors
        else set(casting.get("F", [])) | set(casting.get("M", [])),
        "counts": {gender: len(casting.get(gender, [])) for gender in ("F", "M")},
        "provider": settings.TTS_PROVIDER,
        "sample_text": SAMPLE_TEXT,
        "min_per_gender": MIN_VOICES_PER_GENDER,
    }
    return render(request, "backoffice/voices.html", context)


@staff_required
@require_POST
def voice_sample(request):
    """Bouton « Écouter » : phrase d'essai de 80 caractères, en cache, comptée dans le quota."""
    voice = request.POST.get("voice", "")
    voice_list, error = _voices()
    if error is None and voice not in {v.name for v in voice_list}:
        error = "Voix inconnue."
    url = None
    if error is None:
        try:
            url = default_storage.url(voice_sample_path(voice))
        except TTSError as exc:
            error = str(exc)
    if request.headers.get("HX-Request") != "true":
        if error:
            messages.error(request, error)
            return redirect("backoffice:voices")
        return redirect(url)
    context = {"url": url, "error": error, "voice": voice}
    return render(request, "backoffice/includes/voice_sample.html", context)
