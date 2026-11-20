"""Génération de l'audio d'un test, item par item, avec quota (SPEC 7.4 et 15).

La page de relecture enchaîne les appels `audio/next` : chacun synthétise au plus un item.
L'opération est reprenable et idempotente : un item dont l'audio a le bon `script_hash` et le moteur
du fournisseur actif est ignoré. Les synthèses factices sont journalisées mais ne comptent pas
dans le quota (`tts_chars_used`).
"""

import hashlib
from dataclasses import dataclass, field
from decimal import Decimal

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage

from bank import ambiences
from bank.audio import (
    ASSEMBLY_VERSION,
    Plan,
    Segment,
    SoundChoice,
    assemble,
    chars_to_send,
    encode_mp3,
    is_up_to_date,
    plan_item,
    synthesize_item,
    synthesize_segment,
)
from bank.constants import (
    CHANNEL_LABELS,
    CHIME,
    DEFAULT_SETTING,
    LEVELS,
    SETTING_LABELS,
    SOUND_COMBINATIONS,
    script_sound,
)
from bank.content import item_fields, tts_chars
from bank.models import Item, Test
from bank.tts import TTSError, TTSProvider, active_engine, get_provider
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
    # Lieux demandés sans bruit de fond actif : ces documents seront générés sans (7.5.4).
    missing_settings: list = field(default_factory=list)

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


def pending_chars(item: Item, engine: str) -> int:
    """Caractères qu'il faudra envoyer à l'API pour cet item : les répliques déjà en cache
    (7.5.3) ne comptent pas."""
    try:
        return chars_to_send(plan_item(item, SiteSettings.load().tts_casting), engine)
    except TTSError:
        return tts_chars(item_fields(item))


def missing_settings(test: Test) -> list[str]:
    """Lieux demandés par les items de CO du test sans clip actif dans la bibliothèque : ces items
    seront générés sans bruit de fond (7.5.4)."""
    wanted = []
    for item in co_items(test):
        setting, _ = script_sound(item.audio_script)
        if setting != DEFAULT_SETTING and setting not in wanted:
            wanted.append(setting)
    return ambiences.missing_categories(wanted)


def audio_progress(test: Test, engine: str | None = None) -> AudioProgress:
    engine = engine or active_engine()
    items = co_items(test)
    pending = [item for item in items if not is_up_to_date(item, engine)]
    return AudioProgress(
        total=len(items),
        done=len(items) - len(pending),
        pending_chars=sum(pending_chars(item, engine) for item in pending),
        remaining_quota=tts_chars_remaining(),
        next_item=pending[0] if pending else None,
        missing_settings=[SETTING_LABELS[s] for s in missing_settings(test)] if pending else [],
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
    """Synthèse d'un item (aussi utilisée par le bouton « Régénérer »), comptée dans le quota.

    Seules les répliques absentes du cache partent à l'API et créent un `UsageEntry` : refaire un
    mixage ne coûte rien (7.5.3)."""
    provider = provider or get_provider()
    if pending_chars(item, provider.name) > tts_chars_remaining():
        raise QuotaError(QUOTA_MESSAGE)
    _, chars = synthesize_item(item, provider, SiteSettings.load().tts_casting)
    if chars:
        test_item = getattr(item, "test_item", None)
        ref = f"item {item.pk}" + (f", {test_item.test}" if test_item else "")
        _record(provider, chars, ref)


def generate_next(test: Test, provider: TTSProvider | None = None) -> AudioProgress:
    """Synthétise le prochain item de CO sans audio à jour ; refuse si le test ne tient pas
    dans le quota restant."""
    if test.status != Test.Status.DRAFT:
        raise TTSError("Test publié : son audio est figé.")
    engine = provider.name if provider else None
    progress = audio_progress(test, engine)
    if progress.finished:
        return progress
    if not progress.fits:
        raise QuotaError(
            f"{QUOTA_MESSAGE} Il faut {progress.pending_chars} caractères, "
            f"il en reste {progress.remaining_quota} ce mois-ci."
        )
    synthesize_one(progress.next_item, provider)
    return audio_progress(test, engine)


def voice_sample_path(voice: str, provider: TTSProvider | None = None) -> str:
    """Chemin du MP3 d'essai d'une voix ; synthétisé une fois, puis servi depuis le cache."""
    provider = provider or get_provider()
    key = hashlib.sha256(f"{provider.name}|{voice}|{SAMPLE_TEXT}".encode()).hexdigest()
    path = f"audio/voices/{key}.mp3"
    if default_storage.exists(path):
        return path
    if len(SAMPLE_TEXT) > tts_chars_remaining():
        raise QuotaError("Quota gratuit du mois atteint : écoute impossible.")
    pcm, chars = synthesize_segment(Segment(SAMPLE_TEXT, voice), provider)
    default_storage.save(path, ContentFile(encode_mp3(pcm)))
    if chars:
        _record(provider, chars, f"essai de la voix {voice}")
    return path


# --- Essai de mixage d'un bruit de fond (/bo/ambiences/, 7.5.4) ----------------------------------

TRIAL_TEXT = (
    "Bonjour à tous. Le prochain départ est prévu dans dix minutes : merci de vérifier votre "
    "billet et de rester attentifs aux annonces."
)


@dataclass
class Trial:
    channel: str
    snr_label: str
    snr_db: float | None
    url_path: str
    speech_lufs: float | None
    noise_lufs: float | None

    @property
    def url(self) -> str:
        return default_storage.url(self.url_path)

    @property
    def channel_label(self) -> str:
        return f"Canal {CHANNEL_LABELS[self.channel].lower()}"

    @property
    def measured_snr(self) -> float | None:
        if self.speech_lufs is None or self.noise_lufs is None:
            return None
        return round(self.speech_lufs - self.noise_lufs, 1)


def _trial_cases(clip) -> list[tuple[str, str, str, float | None]]:
    """(canal, lieu, tranche, rapport) à essayer : chaque canal permis pour les lieux du clip, et
    chacun des rapports voix / bruit ; un carillon s'essaie avant une annonce en gare."""
    if CHIME in clip.categories:
        return [("haut_parleur", "gare", "", None)]
    channels: dict[str, str] = {}
    for table in SOUND_COMBINATIONS.values():
        for channel, settings_ in table.items():
            for setting in settings_:
                if setting in clip.categories:
                    channels.setdefault(channel, setting)
    table = SiteSettings.load().ambience_snr_db
    return [
        (channel, setting, label, float(value))
        for channel, setting in channels.items()
        for label, value in table.items()
    ]


def trial_mixes(clip, provider: TTSProvider | None = None) -> list[Trial]:
    """Une phrase d'exemple (synthétisée une fois, puis en cache) mixée avec ce clip, pour chaque
    canal permis et chaque rapport voix / bruit ; la sonie mesurée accompagne chaque essai."""
    provider = provider or get_provider()
    casting = SiteSettings.load().tts_casting
    voice = (casting.get("F") or casting.get("M") or [casting.get("instructions")])[0]
    segment = Segment(TRIAL_TEXT, voice)
    trials = []
    for channel, setting, label, snr in _trial_cases(clip):
        level = label.partition("-")[0] if label.partition("-")[0] in LEVELS else "B1"
        plan = Plan(
            parts=[(0.5, segment)],
            n_lines=1,
            subtype="annonce" if channel == "haut_parleur" else "dialogue",
            level=level,
            setting=setting,
            channel=channel,
            seed=f"essai-{clip.pk}",
        )
        is_chime = snr is None
        choice = SoundChoice(
            clip=None if is_chime else clip, chime=clip if is_chime else None, snr_db=snr
        )
        key = hashlib.sha256(
            f"{ASSEMBLY_VERSION}|{provider.name}|{voice}|{clip.pk}|{clip.gain_db}|{channel}|{snr}".encode()
        ).hexdigest()
        path = f"audio/trials/{key}.mp3"
        # La phrase est en cache : refaire le mixage (pour afficher la sonie) ne coûte rien.
        result = assemble(plan, provider, choice)
        if result.chars_sent:
            _record(provider, result.chars_sent, f"essai du bruit de fond {clip.pk}")
        mix_info = result.mix
        if not default_storage.exists(path):
            default_storage.save(path, ContentFile(encode_mp3(result.pcm)))
        trials.append(
            Trial(
                channel=channel,
                snr_label=label,
                snr_db=snr,
                url_path=path,
                speech_lufs=mix_info.get("speech_lufs"),
                noise_lufs=mix_info.get("noise_lufs"),
            )
        )
    return trials
