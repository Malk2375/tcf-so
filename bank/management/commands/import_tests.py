from django.core.management.base import BaseCommand, CommandError

from bank.transfer import TransferError, import_archive


class Command(BaseCommand):
    help = (
        "Importe une archive créée par export_tests : les tests y sont recréés publiés, audio "
        "compris, sans synthèse. Un test dont une question existe déjà est refusé."
    )

    def add_arguments(self, parser):
        parser.add_argument("archive")

    def handle(self, *args, archive, **options):
        try:
            outcome = import_archive(archive)
        except TransferError as exc:
            raise CommandError(str(exc)) from exc
        for label in outcome.imported:
            self.stdout.write(self.style.SUCCESS(f"  Importé : {label}"))
        for reason in outcome.refused:
            self.stdout.write(self.style.WARNING(f"  Refusé : {reason}"))
        self.stdout.write(
            f"{len(outcome.imported)} test(s) importé(s), {len(outcome.refused)} refusé(s)."
        )
