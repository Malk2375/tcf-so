from allauth.account import views as allauth_views
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import logout
from django.contrib.auth import views as auth_views
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.urls import reverse_lazy
from django.views.decorators.http import require_http_methods

from accounts.forms import DeleteAccountForm


def password_reset(request):
    """« Mot de passe oublié » : parcours d'allauth par e-mail, ou page informative si désactivé."""
    if settings.EMAIL_ENABLED:
        return allauth_views.password_reset(request)
    return render(request, "accounts/password_reset_disabled.html")


class PasswordResetConfirmView(auth_views.PasswordResetConfirmView):
    """Page ouverte par le lien généré dans le back-office."""

    template_name = "accounts/password_reset_confirm.html"
    success_url = reverse_lazy("password_reset_complete")


class PasswordResetCompleteView(auth_views.PasswordResetCompleteView):
    template_name = "accounts/password_reset_complete.html"


@login_required
def profile(request):
    return render(request, "accounts/profile.html")


@login_required
@require_http_methods(["GET", "POST"])
def delete_account(request):
    form = DeleteAccountForm(request.user, request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = request.user
        logout(request)
        # Les passages, réponses et conseils sont liés à l'utilisateur et supprimés en cascade.
        user.delete()
        messages.success(request, "Votre compte a été supprimé.")
        return redirect("home")
    return render(request, "accounts/delete_account.html", {"form": form})
