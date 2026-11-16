from django.core.management.base import BaseCommand, CommandError

from bank.demo import require_demo
from bank.devtest import make_dev_draft, make_dev_test
from bank.models import ExamFormat


class Command(BaseCommand):
    help = (
        "Crée un test factice (développement uniquement) : publié avec audio, ou brouillon importé."
    )

    def add_arguments(self, parser):
        parser.add_argument("--mode", choices=["rapide", "moyen", "reel"], default="rapide")
        parser.add_argument(
            "--draft",
            action="store_true",
            help="Passe par le brief et l'import : test en brouillon, sans audio, à relire.",
        )

    def handle(self, *args, mode, draft, **options):
        require_demo()
        if not ExamFormat.objects.filter(mode=mode).exists():
            raise CommandError("Formats absents : lancez d'abord « manage.py seed_formats ».")
        if draft:
            test = make_dev_draft(mode)
            self.stdout.write(
                self.style.SUCCESS(
                    f"Test factice importé en brouillon : {test} "
                    f"({test.test_items.count()} items). "
                    "Relisez-le dans le back-office, page Tests."
                )
            )
            return
        test = make_dev_test(mode)
        self.stdout.write(
            self.style.SUCCESS(
                f"Test factice créé et publié : {test} ({test.test_items.count()} items)."
            )
        )
