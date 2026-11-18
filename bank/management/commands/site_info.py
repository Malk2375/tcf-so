from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db.utils import DatabaseError

from bank.management.commands.reset_demo import DEMO_EMAIL, DEMO_PASSWORD

VOICES = {"fake": "factices (aucun appel à Google)", "google": "Google Chirp 3 HD (quota réel)"}
ADVICE = {"fake": "modèles de secours (aucun appel à Claude)", "anthropic": "API Claude (payant)"}


class Command(BaseCommand):
    help = "Affiche, au démarrage d'un site, la base utilisée, les fournisseurs et le compte."

    def add_arguments(self, parser):
        parser.add_argument("--port", default="8000")

    def handle(self, *args, port, **options):
        title = "SITE DE DÉMONSTRATION" if settings.DEMO else "SITE RÉEL"
        lines = [
            "=" * 64,
            f"  {title}",
            f"  Base de données : {settings.DATABASES['default']['NAME']}",
            f"  Fichiers audio  : {settings.MEDIA_ROOT}",
            f"  Voix            : {VOICES.get(settings.TTS_PROVIDER, settings.TTS_PROVIDER)}",
            f"  Conseils        : {ADVICE.get(settings.LLM_PROVIDER, settings.LLM_PROVIDER)}",
            f"  Compte          : {self.account()}",
            f"  Adresse         : http://localhost:{port}",
            "  Pour arrêter le site : fermez cette fenêtre, ou Ctrl+C.",
            "=" * 64,
        ]
        self.stdout.write("\n".join(lines))

    def account(self) -> str:
        if settings.DEMO:
            return f"{DEMO_EMAIL} / mot de passe {DEMO_PASSWORD}"
        try:
            emails = list(
                get_user_model()
                .objects.filter(is_superuser=True)
                .exclude(email="")
                .values_list("email", flat=True)
            )
        except DatabaseError:
            emails = []
        if not emails:
            return "aucun administrateur : créez-en un avec « createsuperuser »"
        return "votre compte administrateur : " + ", ".join(emails)
