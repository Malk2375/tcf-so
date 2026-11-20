"""Débloque la connexion d'un compte après trop d'échecs (SPEC 13).

uv run python manage.py unlock_login adresse@exemple.fr
uv run python manage.py unlock_login --all
"""

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from accounts import lockout


class Command(BaseCommand):
    help = (
        "Efface les compteurs d'échecs de connexion d'un compte (ou de tous, avec --all, "
        "y compris ceux par adresse IP) : la connexion redevient possible aussitôt."
    )

    def add_arguments(self, parser):
        parser.add_argument("email", nargs="?", help="Adresse e-mail du compte à débloquer.")
        parser.add_argument(
            "--all",
            action="store_true",
            help="Effacer tous les compteurs de limitation, y compris ceux par adresse IP.",
        )

    def handle(self, *args, email=None, all=False, **options):
        if all:
            lockout.unlock_all()
            self.stdout.write(
                self.style.SUCCESS(
                    "Tous les compteurs de limitation des connexions ont été effacés "
                    "(par compte et par adresse IP)."
                )
            )
            return
        if not email:
            raise CommandError(
                "Indiquez l'adresse e-mail du compte, ou --all. "
                "Exemple : manage.py unlock_login adresse@exemple.fr"
            )
        if not get_user_model().objects.filter(email__iexact=email.strip()).exists():
            raise CommandError(f"Aucun compte avec l'adresse « {email} ». Rien n'a été effacé.")
        was_locked = lockout.is_locked(email)
        lockout.unlock(email)
        state = "était bloqué" if was_locked else "n'était pas bloqué"
        self.stdout.write(
            self.style.SUCCESS(
                f"Compteur d'échecs de connexion effacé pour {email} (le compte {state}). "
                "La connexion est de nouveau possible."
            )
        )
