from datetime import date
from pathlib import Path

from django.core.management.base import BaseCommand

from bank.ambiences import export_archive


class Command(BaseCommand):
    help = (
        "Exporte la bibliothèque de bruits de fond (extraits FLAC et informations) dans une "
        "archive zip, à importer sur un autre site avec import_ambiences."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--output",
            default=f"ambiences_{date.today():%Y-%m-%d}.zip",
            help="Fichier à créer (par défaut : ambiences_<date>.zip dans le dossier courant).",
        )

    def handle(self, *args, output, **options):
        path = Path(output)
        clips = export_archive(path)
        for clip in clips:
            self.stdout.write(f"  {clip.title} ({', '.join(clip.categories)})")
        self.stdout.write(
            self.style.SUCCESS(f"{len(clips)} bruit(s) de fond exporté(s) dans {path.resolve()}.")
        )
