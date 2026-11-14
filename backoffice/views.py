import json

from django.contrib.auth import get_user_model
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_http_methods

from accounts.reset import make_reset_link
from backoffice.decorators import staff_required
from backoffice.forms import BriefForm, ImportForm
from bank.brief import create_brief
from bank.constants import MAX_IMPORT_FILE_BYTES
from bank.importer import import_files
from bank.models import Brief


@staff_required
@require_http_methods(["GET", "POST"])
def users(request):
    User = get_user_model()
    generated = None
    if request.method == "POST":
        user = get_object_or_404(User, pk=request.POST.get("user_id"))
        generated = {"user": user, "link": make_reset_link(user)}
    accounts = User.objects.order_by("-date_joined")
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


@staff_required
@require_http_methods(["GET", "POST"])
def import_view(request):
    form = ImportForm(request.POST or None, request.FILES or None)
    result, show_files = None, False
    if request.method == "POST" and form.is_valid():
        # Lecture bornée : un fichier trop gros est signalé sans être chargé en entier.
        files = [(f.name, f.read(MAX_IMPORT_FILE_BYTES + 1)) for f in form.cleaned_data["files"]]
        result = import_files(files, user=request.user)
        show_files = len(files) > 1
    context = {"form": form, "result": result, "show_files": show_files}
    return render(request, "backoffice/import.html", context)
