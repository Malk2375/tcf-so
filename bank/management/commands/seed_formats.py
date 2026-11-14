from django.core.management.base import BaseCommand

from bank.formats import seed_formats


class Command(BaseCommand):
    help = "Crée ou met à jour les trois formats d'examen (rapide, moyen, réel) et leurs épreuves."

    def handle(self, *args, **options):
        for exam_format in seed_formats():
            sections = ", ".join(
                f"{s.section} {s.n_items}" for s in exam_format.sections.order_by("order")
            )
            self.stdout.write(f"{exam_format.code} : {sections}")
        self.stdout.write(self.style.SUCCESS("Formats à jour."))
