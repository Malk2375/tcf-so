"""Lien de réinitialisation du mot de passe généré par l'administrateur (e-mails désactivés)."""

from django.conf import settings
from django.contrib.auth.tokens import default_token_generator
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode


def make_reset_link(user) -> str:
    """Lien absolu à usage unique.

    Le jeton de Django devient invalide dès que le mot de passe change.
    """
    uidb64 = urlsafe_base64_encode(force_bytes(user.pk))
    token = default_token_generator.make_token(user)
    path = reverse("password_reset_confirm", kwargs={"uidb64": uidb64, "token": token})
    return f"{settings.SITE_URL}{path}"
