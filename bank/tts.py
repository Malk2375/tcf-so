"""Fournisseurs de synthèse vocale (SPEC 7.1).

`FakeTTSProvider` ne fait aucun appel réseau : c'est le fournisseur par défaut et celui des tests.
`GoogleChirp3Provider` appelle l'API REST Cloud Text-to-Speech (v1) avec une clé d'API ;
il n'est utilisé que si `TTS_PROVIDER=google` et que `GOOGLE_TTS_API_KEY` est renseignée.
"""

import base64
import hashlib
import io
import json
import math
import struct
import urllib.error
import urllib.parse
import urllib.request
import wave
from dataclasses import dataclass
from typing import Protocol

from django.conf import settings

from bank.constants import CHARS_PER_SECOND

SAMPLE_RATE = 24_000
LANGUAGE = "fr-FR"
CHIRP3_PREFIX = "fr-FR-Chirp3-HD-"


class TTSError(Exception):
    """Synthèse impossible : message en français, affichable tel quel."""


@dataclass(frozen=True)
class Voice:
    name: str
    gender: str  # « F » ou « M » ; « » si l'API ne le précise pas


class TTSProvider(Protocol):
    name: str  # moteur, enregistré dans AudioAsset.engine et UsageEntry.provider

    def synthesize(self, text: str, voice: str, speaking_rate: float | None) -> bytes:
        """WAV mono, PCM 16 bits."""
        ...

    def list_voices(self) -> list[Voice]: ...


def wav_bytes(pcm: bytes, sample_rate: int = SAMPLE_RATE) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(sample_rate)
        out.writeframes(pcm)
    return buffer.getvalue()


# --- Fournisseur factice ------------------------------------------------------------------------

# Voix factices : les noms de la distribution par défaut (7.2) et deux voix de plus,
# pour que la page /bo/voices/ et l'assemblage fonctionnent sans clé.
FAKE_VOICES = [
    Voice("fr-FR-Chirp3-HD-Aoede", "F"),
    Voice("fr-FR-Chirp3-HD-Kore", "F"),
    Voice("fr-FR-Chirp3-HD-Leda", "F"),
    Voice("fr-FR-Chirp3-HD-Zephyr", "F"),
    Voice("fr-FR-Chirp3-HD-Achernar", "F"),
    Voice("fr-FR-Chirp3-HD-Gacrux", "F"),
    Voice("fr-FR-Chirp3-HD-Charon", "M"),
    Voice("fr-FR-Chirp3-HD-Puck", "M"),
    Voice("fr-FR-Chirp3-HD-Fenrir", "M"),
    Voice("fr-FR-Chirp3-HD-Orus", "M"),
    Voice("fr-FR-Chirp3-HD-Iapetus", "M"),
]
# Fréquences qui divisent 24 000 : une période tient en un nombre entier d'échantillons.
_FAKE_FREQUENCIES = [200, 240, 250, 300, 320, 375, 400, 480]
_AMPLITUDE = 1600  # environ −26 dBFS : un son discret


def tone_pcm(frequency: int, seconds: float, amplitude: int = _AMPLITUDE) -> bytes:
    """Son pur, construit par répétition d'une période (rapide, sans boucle par échantillon)."""
    period = SAMPLE_RATE // frequency
    one = struct.pack(
        f"<{period}h",
        *(round(amplitude * math.sin(2 * math.pi * i / period)) for i in range(period)),
    )
    total = round(seconds * SAMPLE_RATE)
    repeated = one * (total // period + 1)
    return repeated[: total * 2]


def silence_pcm(seconds: float) -> bytes:
    return b"\x00\x00" * round(seconds * SAMPLE_RATE)


class FakeTTSProvider:
    """WAV de durée caractères / 15 secondes : des « syllabes » de son discret, sans réseau.

    La hauteur du son dépend de la voix, pour distinguer les locuteurs à l'écoute.
    """

    name = "fake"

    def synthesize(self, text: str, voice: str, speaking_rate: float | None = None) -> bytes:
        seconds = len(text) / CHARS_PER_SECOND / (speaking_rate or 1.0)
        digest = int(hashlib.sha256(voice.encode()).hexdigest(), 16)
        frequency = _FAKE_FREQUENCIES[digest % len(_FAKE_FREQUENCIES)]
        syllable = tone_pcm(frequency, 0.22) + silence_pcm(0.08)
        total = round(seconds * SAMPLE_RATE) * 2
        pcm = (syllable * (total // len(syllable) + 1))[:total]
        return wav_bytes(pcm)

    def list_voices(self) -> list[Voice]:
        return list(FAKE_VOICES)


# --- Google Cloud Text-to-Speech, voix Chirp 3 HD ----------------------------------------------

GOOGLE_API = "https://texttospeech.googleapis.com/v1"
_GENDERS = {"FEMALE": "F", "MALE": "M"}


class GoogleChirp3Provider:
    name = "google_chirp3_hd"

    def __init__(self, api_key: str, timeout: float = 60):
        if not api_key:
            raise TTSError("GOOGLE_TTS_API_KEY est vide : renseignez la clé dans .env.")
        self.api_key = api_key
        self.timeout = timeout

    def _request(self, method: str, path: str, body: dict | None = None) -> dict:
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(
            f"{GOOGLE_API}/{path}",
            data=data,
            method=method,
            headers={"x-goog-api-key": self.api_key, "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read())
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:300]
            raise TTSError(f"API Text-to-Speech : erreur {exc.code}. {detail}") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise TTSError(f"API Text-to-Speech injoignable : {exc}") from exc

    def synthesize(self, text: str, voice: str, speaking_rate: float | None = None) -> bytes:
        audio_config = {"audioEncoding": "LINEAR16", "sampleRateHertz": SAMPLE_RATE}
        if speaking_rate is not None:
            audio_config["speakingRate"] = speaking_rate
        body = {
            "input": {"text": text},
            "voice": {"languageCode": LANGUAGE, "name": voice},
            "audioConfig": audio_config,
        }
        payload = self._request("POST", "text:synthesize", body)
        try:
            return base64.b64decode(payload["audioContent"])
        except (KeyError, ValueError) as exc:
            raise TTSError("API Text-to-Speech : réponse sans audio.") from exc

    def list_voices(self) -> list[Voice]:
        query = urllib.parse.urlencode({"languageCode": LANGUAGE})
        payload = self._request("GET", f"voices?{query}")
        voices = [
            Voice(v["name"], _GENDERS.get(v.get("ssmlGender", ""), ""))
            for v in payload.get("voices", [])
            if LANGUAGE in v.get("languageCodes", [])
        ]
        return sorted(voices, key=lambda v: v.name)


def get_provider() -> TTSProvider:
    if settings.TTS_PROVIDER == "google":
        return GoogleChirp3Provider(settings.GOOGLE_TTS_API_KEY)
    if settings.TTS_PROVIDER == "fake":
        return FakeTTSProvider()
    raise TTSError(f"TTS_PROVIDER inconnu : {settings.TTS_PROVIDER} (fake ou google).")


def chirp3_voices(provider: TTSProvider) -> list[Voice]:
    """Voix proposées sur /bo/voices/ : les voix fr-FR Chirp 3 HD."""
    return [v for v in provider.list_voices() if v.name.startswith(CHIRP3_PREFIX)]
