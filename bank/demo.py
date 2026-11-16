"""Base de démonstration : seul endroit où écrivent les commandes de développement."""

from pathlib import Path

from django.conf import settings
from django.core.management.base import CommandError

DEMO_ENV = (
    '$env:DATABASE_URL = "sqlite:///data/demo.sqlite3"; $env:MEDIA_ROOT = "data/demo-media"; '
    '$env:TTS_PROVIDER = "fake"; $env:LLM_PROVIDER = "fake"'
)


def _same_path(a, b) -> bool:
    if str(a) == str(b):
        return True
    try:
        return Path(a).resolve() == Path(b).resolve()
    except (OSError, ValueError):
        return False


def is_demo_database() -> bool:
    return _same_path(settings.DATABASES["default"]["NAME"], settings.DEMO_DATABASE_PATH)


def is_demo_media() -> bool:
    return _same_path(settings.MEDIA_ROOT, settings.DEMO_MEDIA_ROOT)


def require_demo() -> None:
    """Refuse une commande de développement hors de la base de démonstration."""
    if not settings.DEBUG:
        raise CommandError("Commande réservée au développement (DJANGO_DEBUG=true).")
    if not (is_demo_database() and is_demo_media()):
        raise CommandError(
            "Commande réservée à la base de démonstration. Tapez d'abord dans cette fenêtre "
            f"PowerShell : {DEMO_ENV}"
        )
    if settings.TTS_PROVIDER != "fake" or settings.LLM_PROVIDER != "fake":
        raise CommandError(
            "Base de démonstration : fournisseurs factices obligatoires. Tapez d'abord : "
            '$env:TTS_PROVIDER = "fake"; $env:LLM_PROVIDER = "fake"'
        )
