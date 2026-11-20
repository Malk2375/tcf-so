from django.shortcuts import redirect, render

from bank.models import AmbienceClip, ExamFormat


def mode_cards() -> list[dict]:
    """Nombre de questions et durée de chaque mode, lus dans les formats en base (SPEC 4.1)."""
    formats = ExamFormat.objects.filter(is_active=True).prefetch_related("sections")
    return [
        {
            "format": exam_format,
            "label": exam_format.names.name,
            "questions": exam_format.n_items,
            "minutes": exam_format.duration_minutes,
        }
        for exam_format in formats
    ]


def home(request):
    if request.user.is_authenticated:
        return redirect("dashboard")
    return render(request, "home.html", {"modes": mode_cards()})


def credits(request):
    """Crédits sonores (7.5.4) : les bruits de fond actifs et leurs licences. Page publique.

    Les clips CC BY et CC BY-NC doivent y figurer ; les clips CC0 y figurent aussi, par
    courtoisie. Les bruits factices de la démonstration n'y sont pas."""
    clips = AmbienceClip.objects.filter(is_active=True, is_fake=False).order_by("title")
    return render(request, "credits.html", {"clips": clips})
