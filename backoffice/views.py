from django.contrib.auth import get_user_model
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_http_methods

from accounts.reset import make_reset_link
from backoffice.decorators import staff_required


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
