from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from bank.devtest import make_dev_test
from bank.models import ExamFormat


class Command(BaseCommand):
    help = "Crée un test factice complet et publié, avec audio factice (développement uniquement)."

    def add_arguments(self, parser):
        parser.add_argument("--mode", choices=["rapide", "moyen", "reel"], default="rapide")

    def handle(self, *args, mode, **options):
        if not settings.DEBUG:
            raise CommandError("Commande réservée au développement (DJANGO_DEBUG=true).")
        if not ExamFormat.objects.filter(mode=mode).exists():
            raise CommandError("Formats absents : lancez d'abord « manage.py seed_formats ».")
        test = make_dev_test(mode)
        self.stdout.write(
            self.style.SUCCESS(
                f"Test factice créé et publié : {test} ({test.test_items.count()} items)."
            )
        )
