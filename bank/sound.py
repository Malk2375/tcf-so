"""Outils ffmpeg pour le son (SPEC 7.3, 7.5.3) : filtres, mesure de sonie, mixage.

Tout le traitement se fait en PCM 16 bits mono 24 kHz (`s16le`), comme l'assemblage des items.
"""

import json
import os
import re
import subprocess
import tempfile
from contextlib import contextmanager, suppress

import imageio_ffmpeg

from bank.tts import SAMPLE_RATE, TTSError

PCM_FORMAT = ["-f", "s16le", "-ar", str(SAMPLE_RATE), "-ac", "1"]

# Traitement de la voix selon le canal (7.5.3). Paramètres vérifiés avec `ffmpeg -h filter=…` :
# highpass/lowpass en Hz ; acompressor (threshold linéaire, ratio, attack/release en ms) ;
# aecho (gains, délais en ms, décroissances).
CHANNEL_FILTERS = {
    "direct": None,
    # Bande passante réduite (300 Hz à 5 kHz), légère réverbération, légère compression.
    "haut_parleur": (
        "highpass=f=300,lowpass=f=5000,"
        "aecho=in_gain=0.85:out_gain=0.7:delays=45|90:decays=0.25|0.12,"
        "acompressor=threshold=0.1:ratio=3:attack=5:release=150"
    ),
    # Bande téléphonique (300 Hz à 3,4 kHz), légère compression.
    "telephone": (
        "highpass=f=300,lowpass=f=3400,acompressor=threshold=0.1:ratio=3:attack=5:release=150"
    ),
    # Légère compression, coupure des très basses fréquences.
    "radio": "highpass=f=80,acompressor=threshold=0.125:ratio=2.5:attack=10:release=200",
}
# Bruit de fond : compresseur doux puis limiteur qui écrêtent les pics (freinage, porte qui
# claque). Le bruit est d'abord ramené à −30 LUFS, pour que ces seuils aient le même effet sur
# tous les fichiers. `level=0` : le limiteur ne remonte pas le volume de lui-même.
NOISE_REFERENCE_LUFS = -30.0
NOISE_FILTER = (
    "acompressor=threshold=0.063:ratio=3:attack=10:release=300,"
    "alimiter=limit=0.18:attack=5:release=100:level=0"
)


def ffmpeg_exe() -> str:
    return imageio_ffmpeg.get_ffmpeg_exe()


def run(args: list[str], data: bytes | None = None) -> bytes:
    """Lance ffmpeg et renvoie sa sortie standard ; erreur lisible en cas d'échec."""
    command = [ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-nostdin", *args]
    if data is not None:
        command.remove("-nostdin")
    result = subprocess.run(command, input=data, capture_output=True)
    if result.returncode != 0:
        raise TTSError(f"ffmpeg : {result.stderr.decode('utf-8', 'replace')[:300]}")
    return result.stdout


def filter_pcm(pcm: bytes, audio_filter: str | None) -> bytes:
    if not audio_filter or not pcm:
        return pcm
    return run([*PCM_FORMAT, "-i", "pipe:0", "-af", audio_filter, *PCM_FORMAT, "pipe:1"], pcm)


def gain_pcm(pcm: bytes, db: float) -> bytes:
    if not pcm or abs(db) < 1e-9:
        return pcm
    return filter_pcm(pcm, f"volume={db:.3f}dB")


def loudness(pcm: bytes) -> float | None:
    """Sonie intégrée (LUFS) mesurée par le filtre loudnorm ; None pour un silence."""
    if len(pcm) < SAMPLE_RATE:  # moins d'une demi-seconde : mesure impossible
        return None
    command = [
        ffmpeg_exe(),
        "-hide_banner",
        "-nostats",
        "-loglevel",
        "info",
        *PCM_FORMAT,
        "-i",
        "pipe:0",
        "-af",
        "loudnorm=print_format=json",
        "-f",
        "null",
        "-",
    ]
    result = subprocess.run(command, input=pcm, capture_output=True)
    text = result.stderr.decode("utf-8", "replace")
    match = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", text)
    if result.returncode != 0 or not match:
        raise TTSError("ffmpeg : mesure de la sonie impossible.")
    try:
        value = float(json.loads(match.group(0))["input_i"])
    except (KeyError, ValueError):
        return None
    return value if value > -69 else None


def pcm_seconds(pcm: bytes) -> float:
    return len(pcm) / 2 / SAMPLE_RATE


def samples(seconds: float) -> int:
    return round(seconds * SAMPLE_RATE)


def to_flac(pcm: bytes) -> bytes:
    return run(
        [
            *PCM_FORMAT,
            "-i",
            "pipe:0",
            "-c:a",
            "flac",
            "-map_metadata",
            "-1",
            "-f",
            "flac",
            "pipe:1",
        ],
        pcm,
    )


def decode(path: str, start: float | None = None, end: float | None = None) -> bytes:
    """Fichier audio (tout format lu par ffmpeg) → PCM, éventuellement découpé (secondes)."""
    trim = []
    if start is not None or end is not None:
        bounds = []
        if start:
            bounds.append(f"start={start:.3f}")
        if end is not None:
            bounds.append(f"end={end:.3f}")
        trim = ["-af", f"atrim={':'.join(bounds) or 'start=0'},asetpts=N/SR/TB"]
    return run(
        ["-i", path, "-vn", "-ac", "1", "-ar", str(SAMPLE_RATE), *trim, *PCM_FORMAT, "pipe:1"]
    )


@contextmanager
def temp_file(data: bytes, suffix: str = ".pcm"):
    """Fichier temporaire (Windows ne rouvre pas un NamedTemporaryFile encore ouvert)."""
    handle, path = tempfile.mkstemp(suffix=suffix)
    try:
        with os.fdopen(handle, "wb") as out:
            out.write(data)
        yield path
    finally:
        with suppress(OSError):
            os.remove(path)


def loop_excerpt(path: str, clip_seconds: float, length: float, start: float) -> bytes:
    """Extrait de `length` secondes d'un clip, à partir de `start` ; un clip trop court est bouclé
    avec un fondu enchaîné."""
    if clip_seconds >= length:
        return run(
            [
                *("-ss", f"{start:.3f}", "-t", f"{length:.3f}", "-i", path),
                *("-ac", "1", "-ar", str(SAMPLE_RATE), *PCM_FORMAT, "pipe:1"),
            ]
        )
    crossfade = round(min(1.0, clip_seconds / 4), 3)
    copies = int((length - crossfade) // (clip_seconds - crossfade)) + 2
    inputs = [arg for _ in range(copies) for arg in ("-i", path)]
    chain, previous = [], "[0:a]"
    for index in range(1, copies):
        label = f"[x{index}]"
        chain.append(f"{previous}[{index}:a]acrossfade=d={crossfade}:c1=tri:c2=tri{label}")
        previous = label
    chain.append(f"{previous}atrim=duration={length:.3f},asetpts=N/SR/TB[out]")
    graph = ";".join(chain)
    return run(
        [
            *inputs,
            *("-filter_complex", graph, "-map", "[out]"),
            *("-ac", "1", "-ar", str(SAMPLE_RATE), *PCM_FORMAT, "pipe:1"),
        ]
    )


def fade(pcm: bytes, seconds: float) -> bytes:
    total = pcm_seconds(pcm)
    seconds = min(seconds, total / 2)
    return filter_pcm(
        pcm, f"afade=t=in:st=0:d={seconds:.3f},afade=t=out:st={total - seconds:.3f}:d={seconds:.3f}"
    )


def mix_at(base: bytes, overlay: bytes, offset_seconds: float) -> bytes:
    """Ajoute `overlay` à `base` à partir de `offset_seconds` ; la durée reste celle de `base`.

    Les deux entrées sont abaissées de 6 dB avant la somme pour ne jamais saturer : la
    normalisation finale (loudnorm) rétablit le volume, sans changer le rapport voix / bruit."""
    delay = samples(offset_seconds)
    with temp_file(overlay) as overlay_path:
        graph = (
            "[0:a]volume=0.5[a];"
            f"[1:a]volume=0.5,adelay=delays={delay}S:all=1[b];"
            "[a][b]amix=inputs=2:duration=first:normalize=0[out]"
        )
        return run(
            [
                *PCM_FORMAT,
                *("-i", "pipe:0"),
                *PCM_FORMAT,
                *("-i", overlay_path),
                *("-filter_complex", graph, "-map", "[out]"),
                *PCM_FORMAT,
                "pipe:1",
            ],
            base,
        )
