from django.core.management.base import BaseCommand, CommandError

from bank.ambiences import AmbienceError, import_archive


class Command(BaseCommand):
    help = "Importe une archive créée par export_ambiences (sans doublon)."

    def add_arguments(self, parser):
        parser.add_argument("archive", help="Fichier zip créé par export_ambiences")

    def handle(self, *args, archive, **options):
        try:
            created, updated = import_archive(archive)
        except AmbienceError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(
            self.style.SUCCESS(f"{created} bruit(s) de fond ajouté(s), {updated} mis à jour.")
        )
