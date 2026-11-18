import shutil
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import connections

from bank.demo import require_demo
from bank.devtest import make_dev_draft, make_dev_test
from bank.formats import seed_formats

DEMO_EMAIL = "admin@demo.test"
DEMO_PASSWORD = "demo-tcf-2026"


def remove_demo_files(db_path: Path, media_root: Path) -> None:
    """Efface la base de démonstration (avec ses fichiers WAL) et ses médias."""
    for suffix in ("", "-wal", "-shm"):
        Path(f"{db_path}{suffix}").unlink(missing_ok=True)
    if Path(media_root).exists():
        shutil.rmtree(media_root)


# Contenu de démonstration : de quoi passer des tests, obtenir des conseils et relire un brouillon.
DEMO_TESTS = ["rapide", "rapide", "rapide", "moyen"]
DEMO_DRAFTS = ["rapide"]


def populate(with_tests: bool = True) -> list[str]:
    """Formats d'examen, compte administrateur de démonstration et tests factices."""
    seed_formats()
    admin = get_user_model().objects.create_superuser("demo", DEMO_EMAIL, DEMO_PASSWORD)
    if not with_tests:
        return []
    created = [str(make_dev_test(mode)) for mode in DEMO_TESTS]
    created += [f"{make_dev_draft(mode, user=admin)} (brouillon)" for mode in DEMO_DRAFTS]
    return created


class Command(BaseCommand):
    help = (
        "Recrée la base de démonstration (data/demo.sqlite3) : formats d'examen, compte "
        "administrateur de démonstration, tests factices. Ne touche jamais à la base réelle."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--empty",
            action="store_true",
            help="Sans tests factices (formats et compte seulement).",
        )

    def handle(self, *args, empty, **options):
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
        self.stdout.write("Création des tests factices…")
        for label in populate(with_tests=not empty):
            self.stdout.write(f"  {label}")
        self.stdout.write(
            self.style.SUCCESS(
                "Base de démonstration recréée. Compte administrateur : "
                f"{DEMO_EMAIL} / mot de passe {DEMO_PASSWORD}"
            )
        )
