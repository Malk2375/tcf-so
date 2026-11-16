import shutil
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import connections

from bank.demo import require_demo
from bank.formats import seed_formats

DEMO_EMAIL = "admin@demo.test"
DEMO_PASSWORD = "demo-tcf-2026"


def remove_demo_files(db_path: Path, media_root: Path) -> None:
    """Efface la base de démonstration (avec ses fichiers WAL) et ses médias."""
    for suffix in ("", "-wal", "-shm"):
        Path(f"{db_path}{suffix}").unlink(missing_ok=True)
    if Path(media_root).exists():
        shutil.rmtree(media_root)


def populate() -> None:
    """Formats d'examen et compte administrateur de démonstration."""
    seed_formats()
    get_user_model().objects.create_superuser("demo", DEMO_EMAIL, DEMO_PASSWORD)


class Command(BaseCommand):
    help = (
        "Recrée la base de démonstration (data/demo.sqlite3) : formats d'examen et compte "
        "administrateur de démonstration. Ne touche jamais à la base réelle."
    )

    def handle(self, *args, **options):
        require_demo()
        connections.close_all()
        try:
            remove_demo_files(
                Path(settings.DATABASES["default"]["NAME"]), Path(settings.MEDIA_ROOT)
            )
        except PermissionError as exc:
            raise CommandError(
                "La base de démonstration est ouverte par le site : arrêtez-le (Ctrl+C) "
                "puis relancez la commande."
            ) from exc
        call_command("migrate", verbosity=0)
        populate()
        self.stdout.write(
            self.style.SUCCESS(
                "Base de démonstration recréée. Compte administrateur : "
                f"{DEMO_EMAIL} / mot de passe {DEMO_PASSWORD}"
            )
        )
