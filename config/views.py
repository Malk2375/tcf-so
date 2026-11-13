from django.shortcuts import render

# Durées et nombres de questions affichés sur l'accueil (SPEC 4.1).
# Ils seront lus dans les formats en base une fois `seed_formats` en place (étape 2).
MODES = [
    {"label": "Rapide", "questions": 24, "minutes": 26},
    {"label": "Moyen", "questions": 46, "minutes": 48},
    {"label": "Réel", "questions": 91, "minutes": 95},
]


def home(request):
    return render(request, "home.html", {"modes": MODES})
