"""Blocage et déblocage des connexions (SPEC 13).

Après 5 échecs en 5 minutes, allauth bloque le compte jusqu'à la fin de la fenêtre, même avec le
bon mot de passe. Les compteurs sont dans le cache partagé (data/cache/) : `unlock_login` et le
bouton du back-office les effacent, depuis n'importe quel processus.
"""

from allauth.core import ratelimit
from django.core.cache import cache
from django.test import RequestFactory

from accounts.adapter import login_attempts_key

ACTION = "login_failed"


def _request():
    """Requête fictive : allauth en a besoin pour calculer les clés (la partie par adresse IP
    vise ici une adresse qui n'existe pas, sans effet)."""
    return RequestFactory().post("/accounts/login/", REMOTE_ADDR="192.0.2.1")


def is_locked(email: str) -> bool:
    """Le compte a-t-il atteint la limite des échecs de connexion ?"""
    usage = ratelimit.consume(
        _request(), action=ACTION, key=login_attempts_key(email), dry_run=True
    )
    return not usage


def unlock(email: str) -> None:
    """Efface le compteur d'échecs de connexion de ce compte."""
    ratelimit.clear(_request(), action=ACTION, key=login_attempts_key(email))


def unlock_all() -> None:
    """Efface tous les compteurs de limitation, y compris ceux par adresse IP. Le cache ne sert
    qu'à cela : on le vide entièrement."""
    cache.clear()
