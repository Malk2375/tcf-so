from django.shortcuts import redirect, render

from bank.models import ExamFormat


def mode_cards() -> list[dict]:
    """Nombre de questions et durée de chaque mode, lus dans les formats en base (SPEC 4.1)."""
    formats = ExamFormat.objects.filter(is_active=True).prefetch_related("sections")
    return [
        {
            "format": exam_format,
            "label": exam_format.names.name,
            "questions": exam_format.n_items,
            "minutes": round(exam_format.duration_seconds / 60),
        }
        for exam_format in formats
    ]


def home(request):
    if request.user.is_authenticated:
        return redirect("dashboard")
    return render(request, "home.html", {"modes": mode_cards()})
