"""Tests du back-office : relecture, contrôle à l'aveugle, audio, publication (6.4, 7.4, 11)."""

import json

from django.contrib import messages
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from backoffice.decorators import staff_required
from backoffice.forms import BlindAnswersForm, CorrectionsForm, ItemForm
from bank import review
from bank.constants import MAX_IMPORT_FILE_BYTES
from bank.corrections import import_corrections
from bank.deletion import DeletionError, delete_test, deletion_problems
from bank.models import AudioAsset, Item, Test
from bank.synthesis import audio_progress, generate_next, synthesize_one
from bank.tts import TTSError

MAX_BLIND_FILE_BYTES = 1024 * 1024


def _is_htmx(request) -> bool:
    return request.headers.get("HX-Request") == "true"


def _review_url(test: Test, item: Item | None = None) -> str:
    url = reverse("backoffice:test_review", args=[test.pk])
    return f"{url}#item-{item.pk}" if item else url


def _test_item(test_pk: int, item_pk: int) -> tuple[Test, Item]:
    test = get_object_or_404(Test, pk=test_pk)
    item = get_object_or_404(Item, pk=item_pk, test_item__test=test)
    return test, item


@staff_required
def tests(request):
    all_tests = Test.objects.select_related("format").order_by("-created_at")
    rows = []
    for test in all_tests:
        items = Item.objects.filter(test_item__test=test)
        rows.append(
            {
                "test": test,
                "total": items.count(),
                "approved": items.filter(
                    status__in=[Item.Status.APPROVED, Item.Status.PUBLISHED]
                ).count(),
            }
        )
    return render(request, "backoffice/tests.html", {"rows": rows})


def _review_context(test: Test, **extra) -> dict:
    rows = review.rows(test)
    progress = audio_progress(test)
    approved = sum(r.item.status in {Item.Status.APPROVED, Item.Status.PUBLISHED} for r in rows)
    return {
        "test": test,
        "rows": rows,
        "general_warnings": review.general_warnings(test),
        "progress": progress,
        "problems": review.publication_problems(test) if test.status == Test.Status.DRAFT else [],
        "approved": approved,
        "disagreements": sum(r.disagreement for r in rows),
        "doubts": sum(bool(r.blind_doubt) for r in rows),
        "unchecked": sum(r.unchecked for r in rows),
        "flagged": [r for r in rows if r.flagged],
        "blind_form": BlindAnswersForm(),
        "corrections_form": CorrectionsForm(),
        "is_draft": test.status == Test.Status.DRAFT,
        "deletion_problems": deletion_problems(test),
        **extra,
    }


@staff_required
def test_review(request, pk):
    test = get_object_or_404(Test.objects.select_related("format", "import_batch"), pk=pk)
    return render(request, "backoffice/review.html", _review_context(test))


@staff_required
@require_POST
def item_status(request, pk, item_pk):
    test, item = _test_item(pk, item_pk)
    status = {"approve": Item.Status.APPROVED, "reject": Item.Status.REJECTED}.get(
        request.POST.get("action")
    )
    if status is None:
        return HttpResponse("Action inconnue.", status=400)
    try:
        review.set_status(item, status)
    except review.ReviewError as exc:
        messages.error(request, str(exc))
        return redirect(_review_url(test))
    if _is_htmx(request):
        row = next(r for r in review.rows(test) if r.item.pk == item.pk)
        return render(
            request,
            "backoffice/includes/review_item.html",
            {"row": row, "test": test, "is_draft": True},
        )
    return redirect(_review_url(test, item))


@staff_required
def item_edit(request, pk, item_pk):
    test, item = _test_item(pk, item_pk)
    if test.status != Test.Status.DRAFT:
        messages.error(request, "Test publié : ses questions sont figées.")
        return redirect(_review_url(test))
    form = ItemForm(request.POST or None, item=item)
    if request.method == "POST" and form.is_valid():
        errors = review.update_item(item, form.changes())
        if not errors:
            messages.success(request, "Question modifiée : elle repasse en brouillon.")
            return redirect(_review_url(test, item))
        for error in errors:
            form.add_error(None, error)
    return render(
        request,
        "backoffice/item_edit.html",
        {"test": test, "item": item, "form": form, "slot": item.test_item.slot},
    )


@staff_required
@require_POST
def item_audio(request, pk, item_pk):
    """« Régénérer cet audio » : refait l'audio existant d'un seul item (7.4). La première
    synthèse passe toujours par « Générer l'audio du test »."""
    test, item = _test_item(pk, item_pk)
    try:
        review.require_draft(test)
        if not AudioAsset.objects.filter(item=item).exists():
            raise review.ReviewError(
                "Cette question n'a pas encore d'audio : utilisez « Générer l'audio du test »."
            )
        synthesize_one(item)
    except (TTSError, review.ReviewError) as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, "Audio régénéré.")
    return redirect(_review_url(test, item))


@staff_required
@require_POST
def audio_next(request, pk):
    """Un appel = au plus un item synthétisé. La page enchaîne les appels (HTMX)."""
    test = get_object_or_404(Test, pk=pk)
    error = None
    try:
        progress = generate_next(test)
    except TTSError as exc:
        error = str(exc)
        progress = audio_progress(test)
    if not _is_htmx(request):
        if error:
            messages.error(request, error)
        return redirect(_review_url(test))
    response = render(
        request,
        "backoffice/includes/audio_panel.html",
        {"test": test, "progress": progress, "error": error, "running": error is None},
    )
    if progress.finished and error is None:
        # Tout est prêt : la page se recharge pour afficher les lecteurs et l'état de publication.
        response["HX-Refresh"] = "true"
    return response


def _pasted_or_uploaded(form, limit: int) -> tuple[bytes | str | None, str, list[str]]:
    """Contenu envoyé (fichier ou texte collé), son origine, ou les erreurs du formulaire."""
    if not form.is_valid():
        return None, "", [e for errors in form.errors.values() for e in errors]
    upload = form.cleaned_data["file"]
    raw = upload.read(limit + 1) if upload else form.cleaned_data["text"]
    source = upload.name if upload else "texte collé"
    if len(raw) > limit:
        return None, source, [f"Contenu de plus de {limit // (1024 * 1024)} Mo."]
    return raw, source, []


@staff_required
def blind_json(request, pk):
    test = get_object_or_404(Test, pk=pk)
    content = json.dumps(review.blind_export(test), ensure_ascii=False, indent=2)
    response = HttpResponse(content, content_type="application/json; charset=utf-8")
    response["Content-Disposition"] = (
        f'attachment; filename="blind_{test.format.code}_{test.number:04d}.json"'
    )
    return response


@staff_required
@require_POST
def blind_answers(request, pk):
    test = get_object_or_404(Test, pk=pk)
    form = BlindAnswersForm(request.POST, request.FILES)
    raw, _, errors = _pasted_or_uploaded(form, MAX_BLIND_FILE_BYTES)
    if raw is not None:
        try:
            errors = review.import_blind_answers(test, raw)
        except review.ReviewError as exc:
            errors = [str(exc)]
    if errors:
        for error in errors:
            messages.error(request, error)
    else:
        rows = review.rows(test)
        disagreements = sum(r.disagreement for r in rows)
        messages.success(
            request,
            f"Réponses du relecteur enregistrées : {disagreements} désaccord(s), "
            f"{sum(bool(r.blind_doubt) for r in rows)} doute(s).",
        )
    return redirect(_review_url(test))


@staff_required
@require_POST
def publish(request, pk):
    test = get_object_or_404(Test, pk=pk)
    try:
        review.publish(test)
    except review.ReviewError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, "Test publié : il est proposé aux utilisateurs.")
    return redirect(_review_url(test))


@staff_required
@require_POST
def archive(request, pk):
    test = get_object_or_404(Test, pk=pk)
    try:
        review.archive(test)
    except review.ReviewError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, "Test archivé : il n'est plus proposé.")
    return redirect(_review_url(test))


@staff_required
@require_POST
def delete(request, pk):
    """« Supprimer » : test en brouillon ou archivé, confirmé en retapant son numéro."""
    test = get_object_or_404(Test.objects.select_related("format"), pk=pk)
    if request.POST.get("confirm_number", "").strip() != str(test.number):
        messages.error(request, f"Pour confirmer, tapez le numéro du test : {test.number}.")
        return redirect(_review_url(test))
    label = str(test)
    try:
        counts = delete_test(test)
    except DeletionError as exc:
        messages.error(request, str(exc))
        return redirect(_review_url(test))
    messages.success(
        request,
        f"{label} supprimé : {counts['items']} question(s), {counts['files']} fichier(s) audio, "
        f"{counts['attempts']} passage(s) d'administrateur et {counts['advice']} conseil(s).",
    )
    return redirect("backoffice:tests")


@staff_required
@require_POST
def corrections(request, pk):
    """« Importer des corrections » (6.4 bis) : tout ou rien ; rapport affiché en cas de refus."""
    test = get_object_or_404(Test.objects.select_related("format", "import_batch"), pk=pk)
    form = CorrectionsForm(request.POST, request.FILES)
    raw, source, errors = _pasted_or_uploaded(form, MAX_IMPORT_FILE_BYTES)
    if raw is None:
        for error in errors:
            messages.error(request, error)
        return redirect(_review_url(test))
    try:
        result = import_corrections(test, raw, source=source)
    except review.ReviewError as exc:
        messages.error(request, str(exc))
        return redirect(_review_url(test))
    if not result.report.ok:
        context = _review_context(
            test, corrections_report=result.report.as_dict(), corrections_form=form
        )
        return render(request, "backoffice/review.html", context, status=422)
    unchanged = ""
    if result.unchanged:
        unchanged = f" Inchangé(s), statut conservé : {_slots(result.unchanged)}."
    if result.replaced:
        messages.success(
            request,
            f"Corrections enregistrées. Emplacement(s) remplacé(s), à relire et approuver : "
            f"{_slots(result.replaced)}.{unchanged}",
        )
    else:
        messages.info(
            request, f"Aucune correction : tous les items envoyés sont identiques.{unchanged}"
        )
    return redirect(_review_url(test))


def _slots(slots: list[int]) -> str:
    return ", ".join(str(slot) for slot in slots)
