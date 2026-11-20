"""Audio des items de CO : distribution des voix (7.2) et assemblage (7.3, 7.5.3).

L'assemblage se fait en PCM 16 bits mono 24 kHz, puis ffmpeg normalise le volume
(loudnorm, −16 LUFS) et encode en MP3 mono 64 kbit/s. Le fichier est nommé d'après son SHA-256.
"""

import hashlib
import io
import subprocess
import wave
from dataclasses import dataclass, field

import imageio_ffmpeg
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage

from bank import ambiences, sound
from bank.constants import (
    CHARS_PER_SECOND,
    CHIME,
    CHIME_SETTINGS,
    DEFAULT_CHANNEL,
    DEFAULT_SETTING,
    LETTERS,
    LEVELS,
    QUESTION_REPONSE,
    script_sound,
    speaker_accent,
)
from bank.content import item_fields, script_hash, tts_chars
from bank.models import AmbienceClip, AudioAsset, Item
from bank.tts import SAMPLE_RATE, TTSError, TTSProvider, active_engine, silence_pcm, tone_pcm
from usage.models import SiteSettings

# Silences et signal d'ouverture de l'assemblage (7.3), en secondes.
OPENING_SIGNAL = 0.4
AFTER_SIGNAL = 0.5
BETWEEN_LINES = 0.35
BEFORE_QUESTION = 0.8
BEFORE_OPTIONS = 0.5
BETWEEN_OPTIONS = 0.4
FIXED_SECONDS = OPENING_SIGNAL + AFTER_SIGNAL + BEFORE_QUESTION
LOUDNESS_TARGET = -16


def audio_path(data: bytes, folder: str = "audio") -> str:
    digest = hashlib.sha256(data).hexdigest()
    return f"{folder}/{digest[:2]}/{digest}.mp3"


def save_mp3(data: bytes, folder: str = "audio") -> str:
    path = audio_path(data, folder)
    if not default_storage.exists(path):
        default_storage.save(path, ContentFile(data))
    return path


def _ffmpeg(args: list[str], data: bytes) -> bytes:
    command = [imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-loglevel", "error", *args]
    result = subprocess.run(command, input=data, capture_output=True)
    if result.returncode != 0:
        raise TTSError(f"ffmpeg : {result.stderr.decode('utf-8', 'replace')[:300]}")
    return result.stdout


def encode_mp3(pcm: bytes) -> bytes:
    """PCM 24 kHz mono → MP3 normalisé (−16 LUFS), mono, 64 kbit/s, 24 kHz, sans métadonnées."""
    return _ffmpeg(
        [
            *("-f", "s16le", "-ar", str(SAMPLE_RATE), "-ac", "1", "-i", "pipe:0"),
            *("-af", f"loudnorm=I={LOUDNESS_TARGET}:TP=-1.5:LRA=11"),
            *("-ar", str(SAMPLE_RATE), "-ac", "1", "-b:a", "64k"),
            *("-map_metadata", "-1", "-fflags", "+bitexact", "-f", "mp3", "pipe:1"),
        ],
        pcm,
    )


def wav_to_pcm(data: bytes) -> bytes:
    """WAV renvoyé par le fournisseur → PCM 16 bits mono 24 kHz (rééchantillonné si besoin)."""
    try:
        with wave.open(io.BytesIO(data)) as source:
            if (source.getnchannels(), source.getsampwidth(), source.getframerate()) == (
                1,
                2,
                SAMPLE_RATE,
            ):
                return source.readframes(source.getnframes())
    except (wave.Error, EOFError):
        pass
    return _ffmpeg(
        ["-i", "pipe:0", "-ar", str(SAMPLE_RATE), "-ac", "1", "-f", "s16le", "pipe:1"], data
    )


def pcm_duration_ms(pcm: bytes) -> int:
    return (len(pcm) // 2 * 1000 + SAMPLE_RATE // 2) // SAMPLE_RATE


def opening_signal() -> bytes:
    return tone_pcm(800, OPENING_SIGNAL, amplitude=6000)


# --- Distribution des voix (7.2) ----------------------------------------------------------------


def casting_key(speaker: dict) -> str:
    """Liste de la distribution d'un locuteur : « F », « M », ou « F_CA », « M_CA » (fr-CA)."""
    suffix = "_CA" if speaker_accent(speaker) == "fr-CA" else ""
    return f"{speaker['gender']}{suffix}"


CASTING_LABELS = {
    "F": "féminines",
    "M": "masculines",
    "F_CA": "féminines québécoises",
    "M_CA": "masculines québécoises",
}


def cast_voices(content_hash: str, speakers: dict, casting: dict) -> dict[str, str]:
    """Une voix de son genre (et de son accent) pour chaque locuteur, déterministe ; deux
    locuteurs, deux voix."""
    voices: dict[str, str] = {}
    for speaker in sorted(speakers):
        key = casting_key(speakers[speaker])
        pool = casting.get(key) or []
        free = [voice for voice in pool if voice not in voices.values()]
        if not free:
            raise TTSError(
                "Distribution des voix insuffisante : pas assez de voix "
                f"{CASTING_LABELS.get(key, key)} pour les locuteurs de cet item (page Voix)."
            )
        digest = int(hashlib.sha256(f"{content_hash}:{speaker}".encode()).hexdigest(), 16)
        start = digest % len(pool)
        # Première voix libre à partir de la position tirée, en tournant dans la liste.
        ordered = pool[start:] + pool[:start]
        voices[speaker] = next(voice for voice in ordered if voice not in voices.values())
    return voices


# --- Assemblage (7.3, 7.5.3) --------------------------------------------------------------------

# Version de l'assemblage, gardée dans AudioAsset.mix et dans le `script_hash` des items qui ont un
# lieu, un canal ou un accent : la changer rend périmés leurs audios (tests en brouillon).
ASSEMBLY_VERSION = 2
# Bip de répondeur avant un message vocal ; carillon avant une annonce ; puis un court silence.
BIP_FREQUENCY, BIP_SECONDS = 1000, 0.3
AFTER_PREFIX = 0.3
# Le bruit de fond commence 0,8 s avant la première réplique et finit 0,8 s après la dernière.
AMBIENCE_MARGIN = 0.8
# Carillon : 3 dB sous la voix, avant le réglage fin du clip.
CHIME_BELOW_SPEECH = 3.0
FALLBACK_SPEECH_LUFS = -20.0


@dataclass
class Segment:
    text: str
    voice: str


@dataclass
class Plan:
    """Ce qui sera lu, dans l'ordre, avec les silences qui précèdent chaque réplique.

    Les `n_lines` premières parties sont les répliques du document ; les suivantes, la question et
    les options lues par la voix `instructions`, jamais sous le bruit de fond."""

    parts: list[tuple[float, Segment]] = field(default_factory=list)
    voices: dict = field(default_factory=dict)
    n_lines: int = 0
    subtype: str = ""
    level: str = ""
    setting: str = DEFAULT_SETTING
    channel: str = DEFAULT_CHANNEL
    accents: dict = field(default_factory=dict)
    seed: str = ""

    @property
    def chars(self) -> int:
        return sum(len(segment.text) for _, segment in self.parts)


def plan_item(item: Item, casting: dict) -> Plan:
    script = item.audio_script or {}
    speakers = script.get("speakers", {})
    voices = cast_voices(item.content_hash, speakers, casting)
    instructions = casting.get("instructions")
    if not instructions:
        raise TTSError("Aucune voix « instructions » dans la distribution (page Voix).")
    setting, channel = script_sound(script)
    plan = Plan(
        voices={**voices, "instructions": instructions},
        subtype=item.subtype,
        level=item.level,
        setting=setting,
        channel=channel,
        accents={speaker: speaker_accent(info) for speaker, info in speakers.items()},
        seed=item.content_hash,
    )
    for index, line in enumerate(script.get("lines", [])):
        pause = AFTER_SIGNAL if index == 0 else BETWEEN_LINES
        plan.parts.append((pause, Segment(line["text"], voices[line["speaker"]])))
    plan.n_lines = len(plan.parts)
    # La question fixe des questions-réponses n'est pas lue : elle ne s'affiche qu'à l'écran.
    if item.subtype != QUESTION_REPONSE and item.question:
        plan.parts.append((BEFORE_QUESTION, Segment(item.question, instructions)))
    if item.options_mode == Item.OptionsMode.AUDIO_LETTERS:
        for index, (letter, option) in enumerate(zip(LETTERS, item.options, strict=True)):
            pause = BEFORE_OPTIONS if index == 0 else BETWEEN_OPTIONS
            plan.parts.append((pause, Segment(f"{letter}. {option}", instructions)))
    return plan


# --- Cache des répliques (7.5.3) : refaire un mixage ne consomme aucun caractère ------------------


def cache_name(engine: str, segment: Segment, rate: float | None = None) -> str:
    """`tts_cache/<sha256>.flac`, clé : moteur, voix, débit et texte."""
    key = hashlib.sha256(f"{engine}|{segment.voice}|{rate}|{segment.text}".encode()).hexdigest()
    return f"tts_cache/{key}.flac"


def synthesize_segment(segment: Segment, provider: TTSProvider) -> tuple[bytes, int]:
    """PCM d'une réplique, depuis le cache si possible ; renvoie aussi les caractères envoyés à
    l'API (0 si la réplique était en cache)."""
    name = cache_name(provider.name, segment)
    if default_storage.exists(name):
        return sound.decode(default_storage.path(name)), 0
    pcm = wav_to_pcm(provider.synthesize(segment.text, segment.voice, None))
    default_storage.save(name, ContentFile(sound.to_flac(pcm)))
    return pcm, len(segment.text)


def chars_to_send(plan: Plan, engine: str) -> int:
    """Caractères qu'une synthèse enverrait vraiment à l'API (répliques absentes du cache)."""
    return sum(
        len(segment.text)
        for _, segment in plan.parts
        if not default_storage.exists(cache_name(engine, segment))
    )


# --- Lieu, canal et bruit de fond (7.5.3) --------------------------------------------------------


def snr_for(level: str, table: dict) -> float | None:
    """Rapport voix / bruit d'un niveau ; clés « A1-B1 », « B2-C2 »… ; None si absent."""
    rank = LEVELS.index(level) if level in LEVELS else 0
    for key, value in table.items():
        low, _, high = str(key).partition("-")
        high = high or low
        if low in LEVELS and high in LEVELS and LEVELS.index(low) <= rank <= LEVELS.index(high):
            return float(value)
    return None


def has_sound_settings(script: dict | None) -> bool:
    """Vrai si le script a un lieu, un canal ou un accent autre que les valeurs par défaut."""
    script = script or {}
    setting, channel = script_sound(script)
    accents = {speaker_accent(s) for s in script.get("speakers", {}).values()}
    return (setting, channel) != (DEFAULT_SETTING, DEFAULT_CHANNEL) or "fr-CA" in accents


def _plan_has_sound(plan: Plan) -> bool:
    return (plan.setting, plan.channel) != (DEFAULT_SETTING, DEFAULT_CHANNEL) or (
        "fr-CA" in plan.accents.values()
    )


@dataclass
class SoundChoice:
    """Clips et niveau retenus pour un item, de façon déterministe."""

    clip: AmbienceClip | None = None
    chime: AmbienceClip | None = None
    snr_db: float | None = None
    bip: bool = False

    def key(self) -> dict:
        """Ce qui change le son au-delà du script : entre dans le `script_hash`."""
        return {
            "version": ASSEMBLY_VERSION,
            "clip": [self.clip.pk, self.clip.gain_db] if self.clip else None,
            "snr_db": self.snr_db if self.clip else None,
            "chime": [self.chime.pk, self.chime.gain_db] if self.chime else None,
        }


def choose_sound(plan: Plan, snr_table: dict | None = None) -> SoundChoice:
    choice = SoundChoice()
    if plan.setting != DEFAULT_SETTING:
        choice.clip = ambiences.pick(ambiences.active_clips(plan.setting), plan.seed, "clip")
    if plan.channel == "haut_parleur" and plan.setting in CHIME_SETTINGS:
        choice.chime = ambiences.pick(ambiences.active_clips(CHIME), plan.seed, "chime")
    choice.bip = plan.channel == "telephone" and plan.subtype == "message_vocal"
    if choice.clip:
        table = snr_table if snr_table is not None else SiteSettings.load().ambience_snr_db
        choice.snr_db = snr_for(plan.level, table)
        if choice.snr_db is None:
            choice.clip = None  # aucun rapport réglé pour ce niveau : pas de bruit de fond
    return choice


def noise_bed(clip: AmbienceClip, seconds: float, seed: str, target_lufs: float) -> tuple:
    """Bruit de fond de `seconds` secondes : ramené à −30 LUFS, compressé et limité, puis réglé à
    `target_lufs` (+ réglage fin du clip), avec fondus d'entrée et de sortie. La sonie est
    mesurée sur l'extrait réellement utilisé, pas sur le fichier entier."""
    start_ms = ambiences.start_offset(seed, clip.duration_ms, round(seconds * 1000))
    raw = sound.loop_excerpt(
        ambiences.clip_path(clip), clip.duration_ms / 1000, seconds, start_ms / 1000
    )
    measured = sound.loudness(raw) or sound.NOISE_REFERENCE_LUFS
    tamed = sound.filter_pcm(
        raw, f"volume={sound.NOISE_REFERENCE_LUFS - measured:.3f}dB,{sound.NOISE_FILTER}"
    )
    level = sound.loudness(tamed) or sound.NOISE_REFERENCE_LUFS
    gain = target_lufs + clip.gain_db - level
    bed = sound.fade(sound.gain_pcm(tamed, gain), AMBIENCE_MARGIN)
    return bed, {"clip_start_ms": start_ms, "noise_lufs": round(level + gain, 2)}


def _chime(clip: AmbienceClip, speech_lufs: float) -> bytes:
    pcm = sound.decode(ambiences.clip_path(clip))
    level = sound.loudness(pcm)
    if level is None:
        return pcm
    return sound.gain_pcm(pcm, speech_lufs - CHIME_BELOW_SPEECH + clip.gain_db - level)


@dataclass
class Assembly:
    pcm: bytes
    chars_sent: int
    mix: dict


def assemble(plan: Plan, provider: TTSProvider, choice: SoundChoice | None = None) -> Assembly:
    """PCM de l'item complet : signal, [carillon ou bip], répliques traitées selon le canal (sous
    le bruit de fond), puis question et options, sans bruit de fond.

    Un item sans lieu, sans canal et sans accent (valeurs par défaut) donne exactement le même son
    qu'avant la révision du 20 novembre 2026."""
    sent = 0
    synthesized = []
    for pause, segment in plan.parts:
        pcm, chars = synthesize_segment(segment, provider)
        synthesized.append((pause, pcm))
        sent += chars
    head = opening_signal()
    lines = synthesized[: plan.n_lines]
    tail = b"".join(silence_pcm(pause) + pcm for pause, pcm in synthesized[plan.n_lines :])
    if not _plan_has_sound(plan):
        body = b"".join(silence_pcm(pause) + pcm for pause, pcm in lines)
        return Assembly(head + body + tail, sent, {})

    choice = choice or choose_sound(plan)
    first_pause = lines[0][0] if lines else AFTER_SIGNAL
    block = b"".join(
        (silence_pcm(pause) if index else b"") + pcm for index, (pause, pcm) in enumerate(lines)
    )
    block = sound.filter_pcm(block, sound.CHANNEL_FILTERS.get(plan.channel))
    speech_lufs = sound.loudness(block)
    reference = speech_lufs if speech_lufs is not None else FALLBACK_SPEECH_LUFS
    prefix = b""
    if choice.chime:
        prefix = _chime(choice.chime, reference) + silence_pcm(AFTER_PREFIX)
    elif choice.bip:
        prefix = tone_pcm(BIP_FREQUENCY, BIP_SECONDS, amplitude=6000) + silence_pcm(AFTER_PREFIX)
    before_lines = head + silence_pcm(first_pause) + prefix
    pcm = before_lines + block + tail
    mix = {
        "version": ASSEMBLY_VERSION,
        "setting": plan.setting,
        "channel": plan.channel,
        "accents": plan.accents,
        "voices": plan.voices,
        "chime": choice.chime.pk if choice.chime else None,
        "bip": choice.bip,
        "clip": None,
        "speech_lufs": round(speech_lufs, 2) if speech_lufs is not None else None,
    }
    if choice.clip:
        seconds = sound.pcm_seconds(block) + 2 * AMBIENCE_MARGIN
        bed, info = noise_bed(choice.clip, seconds, plan.seed, reference - choice.snr_db)
        pcm = sound.mix_at(pcm, bed, sound.pcm_seconds(before_lines) - AMBIENCE_MARGIN)
        mix.update(
            clip=choice.clip.pk,
            clip_title=choice.clip.title,
            gain_db=choice.clip.gain_db,
            snr_db=choice.snr_db,
            **info,
        )
    return Assembly(pcm, sent, mix)


def _sound_plan(item: Item) -> Plan:
    setting, channel = script_sound(item.audio_script)
    return Plan(
        subtype=item.subtype,
        level=item.level,
        setting=setting,
        channel=channel,
        seed=item.content_hash,
    )


def expected_hash(item: Item) -> str:
    """`script_hash` qu'aurait l'audio de l'item s'il était généré maintenant (7.5.3) : le script
    et, si l'item a un lieu, un canal ou un accent, les clips choisis et le rapport voix / bruit."""
    fields = item_fields(item)
    if not has_sound_settings(item.audio_script):
        return script_hash(fields)
    return script_hash(fields, choose_sound(_sound_plan(item)).key())


def synthesize_item(item: Item, provider: TTSProvider, casting: dict) -> tuple[AudioAsset, int]:
    """Synthétise et enregistre l'audio d'un item ; renvoie l'audio et les caractères vraiment
    envoyés à l'API (les répliques en cache ne comptent pas)."""
    plan = plan_item(item, casting)
    choice = choose_sound(plan) if _plan_has_sound(plan) else None
    result = assemble(plan, provider, choice)
    path = save_mp3(encode_mp3(result.pcm))
    fields = item_fields(item)
    asset, _ = AudioAsset.objects.update_or_create(
        item=item,
        defaults={
            "file": path,
            "duration_ms": pcm_duration_ms(result.pcm),
            "engine": provider.name,
            "voices": plan.voices,
            "script_hash": script_hash(fields, choice.key() if choice else None),
            "chars_billed": result.chars_sent,
            "mix": {**result.mix, "base_hash": script_hash(fields)} if choice else result.mix,
        },
    )
    return asset, result.chars_sent


def base_hash(asset: AudioAsset) -> str:
    """Empreinte du seul contenu lu (sans les choix de mixage) : sert aux corrections (6.4 bis)."""
    return (asset.mix or {}).get("base_hash", asset.script_hash)


class AudioState:
    OK = "ok"
    MISSING = "missing"
    STALE = "stale"  # script ou réglage du mixage modifié depuis la synthèse
    ENGINE = "engine"  # produit par un autre moteur que le fournisseur actif (7.4)


FROZEN_STATUSES = {Item.Status.PUBLISHED, Item.Status.RETIRED}


def audio_state(item: Item, engine: str | None = None) -> str:
    asset = getattr(item, "audio", None)
    if asset is None:
        return AudioState.MISSING
    if item.status in FROZEN_STATUSES:
        # Test publié ou archivé : son audio est figé, jamais « périmé » (7.5.3).
        return AudioState.OK
    if asset.script_hash != expected_hash(item):
        return AudioState.STALE
    if asset.engine != (engine or active_engine()):
        # Un audio factice n'est jamais à jour quand TTS_PROVIDER=google : il sera régénéré.
        return AudioState.ENGINE
    return AudioState.OK


def is_up_to_date(item: Item, engine: str | None = None) -> bool:
    """Vrai si l'item a un audio du moteur actif et que son script n'a pas changé depuis."""
    return audio_state(item, engine) == AudioState.OK


# --- Audio factice de `make_dev_test` (SPEC 6.5) ------------------------------------------------


def placeholder_mp3(seconds: float) -> bytes:
    """MP3 mono 24 kHz, 64 kbit/s : un son discret de la durée demandée."""
    command = [
        imageio_ffmpeg.get_ffmpeg_exe(),
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "lavfi",
        "-i",
        f"sine=frequency=330:sample_rate=24000:duration={seconds:.2f}",
        "-af",
        "volume=0.05",
        "-ac",
        "1",
        "-b:a",
        "64k",
        "-map_metadata",
        "-1",
        "-fflags",
        "+bitexact",
        "-f",
        "mp3",
        "pipe:1",
    ]
    return subprocess.run(command, capture_output=True, check=True).stdout


def store_placeholder_audio(item: Item) -> AudioAsset:
    fields = item_fields(item)
    seconds = FIXED_SECONDS + tts_chars(fields) / CHARS_PER_SECOND
    data = placeholder_mp3(seconds)
    path = save_mp3(data)
    asset, _ = AudioAsset.objects.update_or_create(
        item=item,
        defaults={
            "file": path,
            "duration_ms": int(seconds * 1000 + 0.5),
            "engine": "fake",
            "voices": {},
            "script_hash": expected_hash(item),
            "chars_billed": 0,
        },
    )
    return asset
