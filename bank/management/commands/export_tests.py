from datetime import date
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from bank.transfer import TransferError, export_archive


class Command(BaseCommand):
    help = (
        "Exporte les tests publiés (questions, briefs, fichiers MP3) dans une archive zip, "
        "à importer sur le serveur avec import_tests. Aucun compte, passage ni conseil."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--output",
            default=f"tests_{date.today():%Y-%m-%d}.zip",
            help="Fichier à créer (par défaut : tests_<date>.zip dans le dossier courant).",
        )

    def handle(self, *args, output, **options):
        path = Path(output)
        try:
            tests = export_archive(path)
        except TransferError as exc:
            path.unlink(missing_ok=True)
            raise CommandError(str(exc)) from exc
        for test in tests:
            self.stdout.write(f"  {test}")
        size = path.stat().st_size / 1024 / 1024
        self.stdout.write(
            self.style.SUCCESS(
                f"{len(tests)} test(s) publié(s) exporté(s) dans {path.resolve()} ({size:.1f} Mo)."
            )
        )
