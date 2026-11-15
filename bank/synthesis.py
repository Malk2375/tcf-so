"""Génération de l'audio d'un test, item par item, avec quota (SPEC 7.4 et 15).

La page de relecture enchaîne les appels `audio/next` : chacun synthétise au plus un item.
L'opération est reprenable et idempotente : un item dont l'audio a le bon `script_hash` est ignoré.
"""

import hashlib
from dataclasses import dataclass
from decimal import Decimal

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage

from bank.audio import encode_mp3, is_up_to_date, synthesize_item, wav_to_pcm
from bank.content import item_fields, tts_chars
from bank.models import Item, Test
from bank.tts import TTSError, TTSProvider, get_provider
from usage.models import SiteSettings, UsageEntry, tts_chars_remaining, tts_chars_used

QUOTA_MESSAGE = "Quota gratuit du mois insuffisant pour ce test."
# Phrase d'essai de la page /bo/voices/ : 80 caractères.
SAMPLE_TEXT = "Bonjour à tous et bienvenue ! Voici une phrase d'essai, pour écouter cette voix."


class QuotaError(TTSError):
    pass


@dataclass
class AudioProgress:
    total: int
    done: int
    pending_chars: int  # caractères qu'il reste à synthétiser pour finir le test
    remaining_quota: int  # caractères disponibles ce mois-ci, marge de sécurité comprise
    next_item: Item | None

    @property
    def finished(self) -> bool:
        return self.done == self.total

    @property
    def fits(self) -> bool:
        return self.pending_chars <= self.remaining_quota

    @property
    def percent(self) -> int:
        return (200 * self.done + self.total) // (2 * self.total) if self.total else 100


def co_items(test: Test) -> list[Item]:
    return list(
        Item.objects.filter(test_item__test=test, section="CO")
        .select_related("audio")
        .order_by("test_item__position")
    )


def audio_progress(test: Test) -> AudioProgress:
    items = co_items(test)
    pending = [item for item in items if not is_up_to_date(item)]
    return AudioProgress(
        total=len(items),
        done=len(items) - len(pending),
        pending_chars=sum(tts_chars(item_fields(item)) for item in pending),
        remaining_quota=tts_chars_remaining(),
        next_item=pending[0] if pending else None,
    )


def _cost(provider: TTSProvider, chars: int) -> Decimal:
    """Coût au-delà du quota gratuit mensuel (en pratique nul : on s'arrête à 95 %)."""
    if provider.name == "fake":
        return Decimal(0)
    site = SiteSettings.load()
    price = Decimal(str(site.price_table.get(provider.name, {}).get("chars", 0)))
    used = tts_chars_used()
    free = site.tts_monthly_char_quota
    billable = max(0, used + chars - free) - max(0, used - free)
    return price * billable / 1_000_000


def _record(provider: TTSProvider, chars: int, ref: str) -> UsageEntry:
    return UsageEntry.objects.create(
        kind=UsageEntry.Kind.TTS,
        provider=provider.name,
        model="chirp3-hd" if provider.name != "fake" else "fake",
        chars=chars,
        cost_usd=_cost(provider, chars),
        ref=ref[:200],
    )


def synthesize_one(item: Item, provider: TTSProvider | None = None) -> None:
    """Synthèse d'un item (aussi utilisée par le bouton « Régénérer »), comptée dans le quota."""
    needed = tts_chars(item_fields(item))
    if needed > tts_chars_remaining():
        raise QuotaError(QUOTA_MESSAGE)
    provider = provider or get_provider()
    _, chars = synthesize_item(item, provider, SiteSettings.load().tts_casting)
    test_item = getattr(item, "test_item", None)
    ref = f"item {item.pk}" + (f", {test_item.test}" if test_item else "")
    _record(provider, chars, ref)


def generate_next(test: Test, provider: TTSProvider | None = None) -> AudioProgress:
    """Synthétise le prochain item de CO sans audio à jour ; refuse si le test ne tient pas
    dans le quota restant."""
    if test.status != Test.Status.DRAFT:
        raise TTSError("Test publié : son audio est figé.")
    progress = audio_progress(test)
    if progress.finished:
        return progress
    if not progress.fits:
        raise QuotaError(
            f"{QUOTA_MESSAGE} Il faut {progress.pending_chars} caractères, "
            f"il en reste {progress.remaining_quota} ce mois-ci."
        )
    synthesize_one(progress.next_item, provider)
    return audio_progress(test)


def voice_sample_path(voice: str, provider: TTSProvider | None = None) -> str:
    """Chemin du MP3 d'essai d'une voix ; synthétisé une fois, puis servi depuis le cache."""
    provider = provider or get_provider()
    key = hashlib.sha256(f"{provider.name}|{voice}|{SAMPLE_TEXT}".encode()).hexdigest()
    path = f"audio/voices/{key}.mp3"
    if default_storage.exists(path):
        return path
    if len(SAMPLE_TEXT) > tts_chars_remaining():
        raise QuotaError("Quota gratuit du mois atteint : écoute impossible.")
    data = encode_mp3(wav_to_pcm(provider.synthesize(SAMPLE_TEXT, voice, None)))
    default_storage.save(path, ContentFile(data))
    _record(provider, len(SAMPLE_TEXT), f"essai de la voix {voice}")
    return path
