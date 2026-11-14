"""Audio des items de CO.

Étape 2 : seul l'audio factice de `make_dev_test` existe (un son discret de la durée estimée).
La synthèse vocale et l'assemblage complet (SPEC 7) arrivent à l'étape 4.
"""

import hashlib
import subprocess

import imageio_ffmpeg
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage

from bank.constants import CHARS_PER_SECOND
from bank.content import item_fields, script_hash, tts_chars
from bank.models import AudioAsset, Item

# Signal d'ouverture et silences de l'assemblage (7.3), pour une durée factice réaliste.
FIXED_SECONDS = 0.4 + 0.5 + 0.8


def audio_path(data: bytes) -> str:
    digest = hashlib.sha256(data).hexdigest()
    return f"audio/{digest[:2]}/{digest}.mp3"


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
    path = audio_path(data)
    if not default_storage.exists(path):
        default_storage.save(path, ContentFile(data))
    asset, _ = AudioAsset.objects.update_or_create(
        item=item,
        defaults={
            "file": path,
            "duration_ms": round(seconds * 1000),
            "engine": "fake",
            "voices": {},
            "script_hash": script_hash(fields),
            "chars_billed": 0,
        },
    )
    return asset
