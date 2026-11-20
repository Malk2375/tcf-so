"""Bibliothèque de bruits de fond (SPEC 7.5.4) : `/bo/ambiences/`."""

import os
import tempfile
from contextlib import suppress
from pathlib import Path

from django.contrib import messages
from django.core.files.storage import default_storage
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods, require_POST

from backoffice.decorators import staff_required
from backoffice.forms import AmbienceForm
from bank.ambiences import AmbienceError, add_clip
from bank.constants import AMBIENCE_CATEGORIES, AMBIENCE_LABELS, CHIME
from bank.models import AmbienceClip
from bank.synthesis import QuotaError, trial_mixes
from bank.tts import TTSError

GAIN_RANGE = (-12.0, 12.0)


def _stored_upload(upload) -> tuple[str, bool]:
    """Chemin d'un fichier envoyé ; (chemin, temporaire à supprimer)."""
    if hasattr(upload, "temporary_file_path"):
        return upload.temporary_file_path(), False
    suffix = Path(upload.name).suffix
    handle, path = tempfile.mkstemp(suffix=suffix)
    with os.fdopen(handle, "wb") as out:
        for chunk in upload.chunks():
            out.write(chunk)
    return path, True


def _sections() -> list[dict]:
    clips = list(AmbienceClip.objects.all())
    sections = []
    for category in AMBIENCE_CATEGORIES:
        in_category = [c for c in clips if category in c.categories]
        sections.append(
            {
                "code": category,
                "label": AMBIENCE_LABELS[category],
                "clips": in_category,
                "missing": not any(c.is_active for c in in_category),
            }
        )
    return sections


@staff_required
@require_http_methods(["GET", "POST"])
def ambiences(request):
    form = AmbienceForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        path, temporary = _stored_upload(data["file"])
        try:
            result = add_clip(
                path,
                categories=data["categories"],
                title=data["title"],
                author=data["author"],
                source_url=data["source_url"],
                license=data["license"],
                start=float(data["start"]) if data["start"] is not None else None,
                end=float(data["end"]) if data["end"] is not None else None,
                notes=data["notes"],
                source_name=data["file"].name,
            )
        except AmbienceError as exc:
            form.add_error(None, str(exc))
        else:
            verb = "ajouté" if result.created else "déjà présent : informations mises à jour"
            messages.success(request, f"Bruit de fond « {result.clip.title} » {verb}.")
            for warning in result.warnings:
                messages.warning(request, warning)
            return redirect(f"{request.path}#clip-{result.clip.pk}")
        finally:
            if temporary:
                with suppress(OSError):
                    os.remove(path)
    sections = _sections()
    context = {
        "form": form,
        "sections": sections,
        "missing": [s["label"] for s in sections if s["missing"] and s["code"] != CHIME],
        "gain_range": GAIN_RANGE,
    }
    return render(request, "backoffice/ambiences.html", context)


@staff_required
@require_POST
def ambience_update(request, pk):
    """Case « actif » et suppression d'un clip."""
    clip = get_object_or_404(AmbienceClip, pk=pk)
    if request.POST.get("action") == "delete":
        name = clip.file.name
        clip.delete()
        if not AmbienceClip.objects.filter(file=name).exists():
            default_storage.delete(name)
        messages.success(request, f"Bruit de fond « {clip.title} » supprimé.")
        return redirect("backoffice:ambiences")
    clip.is_active = request.POST.get("is_active") == "on"
    clip.save(update_fields=["is_active"])
    state = "activé" if clip.is_active else "désactivé"
    messages.success(request, f"Bruit de fond « {clip.title} » {state}.")
    return redirect(f"/bo/ambiences/#clip-{clip.pk}")


def _parse_gain(raw: str | None) -> float | None:
    try:
        value = float((raw or "").replace(",", "."))
    except ValueError:
        return None
    low, high = GAIN_RANGE
    return value if low <= value <= high else None


@staff_required
@require_POST
def ambience_trial(request, pk):
    """« Essayer » : enregistre le réglage fin (`gain_db`) puis mixe la phrase d'exemple avec ce
    clip, pour chaque canal permis et chaque rapport voix / bruit."""
    clip = get_object_or_404(AmbienceClip, pk=pk)
    error = None
    if "gain_db" in request.POST:
        gain = _parse_gain(request.POST.get("gain_db"))
        if gain is None:
            error = "Réglage fin : un nombre de −12 à +12 (dB)."
        elif gain != clip.gain_db:
            clip.gain_db = gain
            clip.save(update_fields=["gain_db"])
    trials = []
    if error is None:
        try:
            trials = trial_mixes(clip)
        except (QuotaError, TTSError) as exc:
            error = str(exc)
    context = {"clip": clip, "trials": trials, "error": error}
    if request.headers.get("HX-Request") != "true":
        if error:
            messages.error(request, error)
        return render(request, "backoffice/ambience_trial_page.html", context)
    return render(request, "backoffice/includes/ambience_trial.html", context)
