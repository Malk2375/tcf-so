from django.core.management.base import BaseCommand, CommandError

from bank.tts import CHIRP3_PREFIXES, LANGUAGES, TTSError, get_provider


class Command(BaseCommand):
    help = (
        "Affiche les voix françaises (fr-FR) et québécoises (fr-CA) du fournisseur vocal "
        "configuré (TTS_PROVIDER)."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--all", action="store_true", help="Toutes les voix, pas seulement Chirp 3 HD."
        )

    def handle(self, *args, all, **options):
        try:
            provider = get_provider()
            voices = provider.list_voices()
        except TTSError as exc:
            raise CommandError(str(exc)) from exc
        if not all:
            voices = [v for v in voices if v.name.startswith(CHIRP3_PREFIXES[v.language])]
        labels = {"F": "féminine", "M": "masculine"}
        for language in LANGUAGES:
            chosen = [v for v in voices if v.language == language]
            self.stdout.write(self.style.MIGRATE_HEADING(f"{language} : {len(chosen)} voix"))
            for voice in chosen:
                self.stdout.write(f"{voice.name}\t{labels.get(voice.gender, 'genre inconnu')}")
        self.stdout.write(self.style.SUCCESS(f"{len(voices)} voix ({provider.name})."))
