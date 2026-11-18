from django.core.management.base import BaseCommand

from bank.importer import refresh_warnings
from bank.models import Test


class Command(BaseCommand):
    help = (
        "Recalcule les avertissements des tests en brouillon avec les règles en vigueur "
        "(lancé par demarrer.cmd après les migrations)."
    )

    def handle(self, *args, **options):
        drafts = Test.objects.filter(status=Test.Status.DRAFT, import_batch__isnull=False)
        for test in drafts.select_related("format", "import_batch__brief"):
            warnings = refresh_warnings(test)
            self.stdout.write(
                f"  {test.format.label}, {test.admin_label} : {len(warnings)} avertissement(s)"
            )
        self.stdout.write(f"Avertissements recalculés : {drafts.count()} test(s) en brouillon.")
