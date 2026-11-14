from django.contrib.auth.decorators import login_required
from django.shortcuts import render

from config.views import mode_cards


@login_required
def dashboard(request):
    # Version minimale : les tests, la reprise et les niveaux arrivent à l'étape 3.
    return render(request, "exam/dashboard.html", {"modes": mode_cards()})
