"""Audio des items de CO : distribution des voix (7.2) et assemblage (7.3).

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

from bank.constants import CHARS_PER_SECOND, LETTERS, QUESTION_REPONSE
from bank.content import item_fields, script_hash, tts_chars
from bank.models import AudioAsset, Item
from bank.tts import SAMPLE_RATE, TTSError, TTSProvider, silence_pcm, tone_pcm

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


def cast_voices(content_hash: str, speakers: dict, casting: dict) -> dict[str, str]:
    """Une voix de son genre pour chaque locuteur, déterministe ; deux locuteurs, deux voix."""
    voices: dict[str, str] = {}
    for speaker in sorted(speakers):
        gender = speakers[speaker]["gender"]
        pool = casting.get(gender) or []
        free = [voice for voice in pool if voice not in voices.values()]
        if not free:
            raise TTSError(
                f"Distribution des voix insuffisante : pas assez de voix « {gender} » "
                "pour les locuteurs de cet item (page Voix)."
            )
        digest = int(hashlib.sha256(f"{content_hash}:{speaker}".encode()).hexdigest(), 16)
        start = digest % len(pool)
        # Première voix libre à partir de la position tirée, en tournant dans la liste.
        ordered = pool[start:] + pool[:start]
        voices[speaker] = next(voice for voice in ordered if voice not in voices.values())
    return voices


# --- Assemblage (7.3) ---------------------------------------------------------------------------


@dataclass
class Segment:
    text: str
    voice: str


@dataclass
class Plan:
    """Ce qui sera lu, dans l'ordre, avec les silences qui précèdent chaque réplique."""

    parts: list[tuple[float, Segment]] = field(default_factory=list)
    voices: dict = field(default_factory=dict)

    @property
    def chars(self) -> int:
        return sum(len(segment.text) for _, segment in self.parts)


def plan_item(item: Item, casting: dict) -> Plan:
    script = item.audio_script or {}
    voices = cast_voices(item.content_hash, script.get("speakers", {}), casting)
    instructions = casting.get("instructions")
    if not instructions:
        raise TTSError("Aucune voix « instructions » dans la distribution (page Voix).")
    plan = Plan(voices={**voices, "instructions": instructions})
    for index, line in enumerate(script.get("lines", [])):
        pause = AFTER_SIGNAL if index == 0 else BETWEEN_LINES
        plan.parts.append((pause, Segment(line["text"], voices[line["speaker"]])))
    # La question fixe des questions-réponses n'est pas lue : elle ne s'affiche qu'à l'écran.
    if item.subtype != QUESTION_REPONSE and item.question:
        plan.parts.append((BEFORE_QUESTION, Segment(item.question, instructions)))
    if item.options_mode == Item.OptionsMode.AUDIO_LETTERS:
        for index, (letter, option) in enumerate(zip(LETTERS, item.options, strict=True)):
            pause = BEFORE_OPTIONS if index == 0 else BETWEEN_OPTIONS
            plan.parts.append((pause, Segment(f"{letter}. {option}", instructions)))
    return plan


def assemble(plan: Plan, provider: TTSProvider) -> bytes:
    """PCM de l'item complet : signal, répliques, question, options."""
    chunks = [opening_signal()]
    for pause, segment in plan.parts:
        chunks.append(silence_pcm(pause))
        chunks.append(wav_to_pcm(provider.synthesize(segment.text, segment.voice, None)))
    return b"".join(chunks)


def synthesize_item(item: Item, provider: TTSProvider, casting: dict) -> tuple[AudioAsset, int]:
    """Synthétise et enregistre l'audio d'un item ; renvoie l'audio et les caractères envoyés."""
    plan = plan_item(item, casting)
    pcm = assemble(plan, provider)
    path = save_mp3(encode_mp3(pcm))
    asset, _ = AudioAsset.objects.update_or_create(
        item=item,
        defaults={
            "file": path,
            "duration_ms": pcm_duration_ms(pcm),
            "engine": provider.name,
            "voices": plan.voices,
            "script_hash": script_hash(item_fields(item)),
            "chars_billed": plan.chars,
        },
    )
    return asset, plan.chars


def is_up_to_date(item: Item) -> bool:
    """Vrai si l'item a un audio et que son script n'a pas changé depuis la synthèse."""
    asset = getattr(item, "audio", None)
    return asset is not None and asset.script_hash == script_hash(item_fields(item))


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
            "script_hash": script_hash(fields),
            "chars_billed": 0,
        },
    )
    return asset
