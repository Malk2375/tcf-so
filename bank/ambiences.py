"""Bibliothèque de bruits de fond et de carillons (SPEC 7.5.4).

Un clip ne garde que l'extrait retenu, converti en FLAC mono 24 kHz, dans `ambiences/`.
Licences acceptées : CC0, CC BY, CC BY-NC (projet non commercial), avec l'adresse de la source.
"""

import hashlib
import json
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.db import transaction

from bank import sound
from bank.constants import AMBIENCE_CATEGORIES, CHIME
from bank.models import AmbienceClip
from bank.tts import SAMPLE_RATE, TTSError, silence_pcm, tone_pcm

MAX_SOURCE_BYTES = 150 * 1024 * 1024
SHORT_CLIP_SECONDS = 15
LICENSES = [choice.value for choice in AmbienceClip.License]


class AmbienceError(Exception):
    """Clip refusé : message en français, affichable tel quel."""


@dataclass
class ClipResult:
    clip: AmbienceClip
    created: bool
    warnings: list[str] = field(default_factory=list)


def _sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as source:
        for block in iter(lambda: source.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _ms(seconds: float | None) -> int | None:
    return None if seconds is None else round(float(seconds) * 1000)


def validate(categories, license: str, source_url: str, title: str, start=None, end=None) -> None:
    if license not in LICENSES:
        raise AmbienceError(
            f"Licence « {license or 'vide'} » refusée : seules CC0, CC-BY et CC-BY-NC sont "
            "acceptées."
        )
    if not (source_url or "").strip():
        raise AmbienceError("L'adresse de la page source est obligatoire.")
    if not (title or "").strip():
        raise AmbienceError("Le titre est obligatoire.")
    unknown = [c for c in categories or [] if c not in AMBIENCE_CATEGORIES]
    if not categories or unknown:
        raise AmbienceError(
            "Catégories inconnues : " + ", ".join(unknown)
            if unknown
            else "Choisissez au moins une catégorie."
        )
    if start is not None and start < 0:
        raise AmbienceError("Le début ne peut pas être négatif.")
    if start is not None and end is not None and end <= start:
        raise AmbienceError("La fin doit venir après le début.")


def save_extract(pcm: bytes) -> str:
    data = sound.to_flac(pcm)
    name = f"ambiences/{hashlib.sha256(data).hexdigest()}.flac"
    if not default_storage.exists(name):
        default_storage.save(name, ContentFile(data))
    return name


@transaction.atomic
def add_clip(
    path: str,
    *,
    categories: list[str],
    title: str,
    source_url: str,
    license: str,
    author: str = "",
    start: float | None = None,
    end: float | None = None,
    notes: str = "",
    source_name: str = "",
    gain_db: float | None = None,
    is_fake: bool = False,
) -> ClipResult:
    """Ajoute un clip depuis un fichier audio. Même fichier et même découpe : mise à jour des
    informations, sans doublon."""
    validate(categories, license, source_url, title, start, end)
    if Path(path).stat().st_size > MAX_SOURCE_BYTES:
        raise AmbienceError("Fichier de plus de 150 Mo.")
    source_sha = _sha256_file(path)
    start_ms, end_ms = _ms(start), _ms(end)
    info = {
        "categories": list(dict.fromkeys(categories)),
        "title": title.strip(),
        "author": (author or "").strip(),
        "source_url": source_url.strip(),
        "license": license,
        "notes": notes or "",
        "source_name": source_name or Path(path).name,
        "is_fake": is_fake,
    }
    existing = AmbienceClip.objects.filter(
        source_sha256=source_sha, source_start_ms=start_ms, source_end_ms=end_ms
    ).first()
    if existing:
        for key, value in info.items():
            setattr(existing, key, value)
        if gain_db is not None:
            existing.gain_db = gain_db
        existing.save()
        return ClipResult(existing, False, short_warning(existing))
    try:
        pcm = sound.decode(path, start, end)
    except TTSError as exc:
        raise AmbienceError(f"Fichier illisible par ffmpeg. {exc}") from exc
    if len(pcm) < SAMPLE_RATE:  # moins d'une demi-seconde
        raise AmbienceError("Extrait vide ou trop court : vérifiez le début et la fin.")
    clip = AmbienceClip.objects.create(
        **info,
        file=save_extract(pcm),
        source_sha256=source_sha,
        source_start_ms=start_ms,
        source_end_ms=end_ms,
        duration_ms=round(sound.pcm_seconds(pcm) * 1000),
        loudness_lufs=sound.loudness(pcm),
        gain_db=gain_db or 0,
    )
    return ClipResult(clip, True, short_warning(clip))


def short_warning(clip: AmbienceClip) -> list[str]:
    if CHIME in clip.categories or clip.duration_ms >= SHORT_CLIP_SECONDS * 1000:
        return []
    return [
        f"Extrait de {clip.duration_ms / 1000:.1f} s : moins de {SHORT_CLIP_SECONDS} secondes, "
        "il sera bouclé avec un fondu enchaîné.".replace(".", ",", 1)
    ]


def clip_path(clip: AmbienceClip) -> str:
    return default_storage.path(clip.file.name)


def active_clips(category: str) -> list[AmbienceClip]:
    """Clips actifs d'une catégorie, dans un ordre stable (pour un choix déterministe)."""
    return sorted(
        (c for c in AmbienceClip.objects.filter(is_active=True) if category in c.categories),
        key=lambda c: c.pk,
    )


def missing_categories(categories) -> list[str]:
    covered = {c for clip in AmbienceClip.objects.filter(is_active=True) for c in clip.categories}
    return [c for c in categories if c not in covered]


def pick(clips: list, seed: str, salt: str):
    """Choix déterministe (hachage de `content_hash`) : régénérer donne le même mixage."""
    if not clips:
        return None
    return clips[_hash(seed, salt) % len(clips)]


def _hash(seed: str, salt: str) -> int:
    return int(hashlib.sha256(f"{seed}:{salt}".encode()).hexdigest(), 16)


def start_offset(seed: str, clip_ms: int, needed_ms: int) -> int:
    """Point de départ déterministe dans le clip (en ms) ; 0 si le clip doit être bouclé."""
    room = clip_ms - needed_ms
    return _hash(seed, "start") % room if room > 0 else 0


# --- Bruits factices : démonstration et tests (sans réseau) ---------------------------------------

FAKE_SECONDS = 20


def fake_noise_pcm(category: str, seconds: float = FAKE_SECONDS) -> bytes:
    """Bruit rose ou brun généré par ffmpeg, différent pour chaque catégorie."""
    index = AMBIENCE_CATEGORIES.index(category)
    color = "pink" if index % 2 else "brown"
    amplitude = 0.05 + 0.03 * (index % 5)
    return sound.run(
        [
            "-f",
            "lavfi",
            "-i",
            f"anoisesrc=color={color}:amplitude={amplitude:.2f}:sample_rate={SAMPLE_RATE}"
            f":duration={seconds}:seed={index + 1}",
            *sound.PCM_FORMAT,
            "pipe:1",
        ]
    )


def fake_chime_pcm() -> bytes:
    """Carillon factice : deux notes."""
    return tone_pcm(660, 0.35, 5000) + tone_pcm(523, 0.6, 5000) + silence_pcm(0.1)


def ensure_fake_ambiences() -> int:
    """Un clip factice actif par catégorie qui n'a aucun clip ; renvoie le nombre créé."""
    created = 0
    for category in missing_categories(AMBIENCE_CATEGORIES):
        pcm = fake_chime_pcm() if category == CHIME else fake_noise_pcm(category)
        AmbienceClip.objects.create(
            categories=[category],
            file=save_extract(pcm),
            source_name=f"factice-{category}",
            duration_ms=round(sound.pcm_seconds(pcm) * 1000),
            loudness_lufs=sound.loudness(pcm),
            title=f"Bruit factice : {category}",
            author="TCF SO",
            source_url="https://example.invalid/bruit-factice",
            license=AmbienceClip.License.CC0,
            is_fake=True,
            notes="Généré par ffmpeg pour la démonstration et les tests.",
        )
        created += 1
    return created


# --- Transfert entre sites : export_ambiences et import_ambiences (7.5.4) -------------------------

ARCHIVE_MANIFEST = "ambiences.json"
CLIP_FIELDS = [
    "categories",
    "source_name",
    "source_sha256",
    "source_start_ms",
    "source_end_ms",
    "duration_ms",
    "loudness_lufs",
    "gain_db",
    "title",
    "author",
    "source_url",
    "license",
    "is_active",
    "notes",
]


def export_archive(path) -> list[AmbienceClip]:
    """Archive zip des clips (hors bruits factices) : fichiers FLAC et leurs informations."""
    clips = list(AmbienceClip.objects.filter(is_fake=False).order_by("pk"))
    entries = []
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for clip in clips:
            name = f"files/{Path(clip.file.name).name}"
            with default_storage.open(clip.file.name, "rb") as source:
                archive.writestr(name, source.read())
            entries.append({"file": name, **{f: getattr(clip, f) for f in CLIP_FIELDS}})
        archive.writestr(
            ARCHIVE_MANIFEST, json.dumps({"version": 1, "clips": entries}, ensure_ascii=False)
        )
    return clips


@transaction.atomic
def import_archive(path) -> tuple[int, int]:
    """Recrée les clips d'une archive ; même fichier d'origine et même découpe : mise à jour des
    informations, sans doublon. Renvoie (créés, mis à jour)."""
    try:
        archive = zipfile.ZipFile(path)
    except (OSError, zipfile.BadZipFile) as exc:
        raise AmbienceError(f"Archive illisible : {exc}") from exc
    created = updated = 0
    with archive:
        try:
            manifest = json.loads(archive.read(ARCHIVE_MANIFEST))
        except (KeyError, ValueError) as exc:
            raise AmbienceError("Archive sans manifeste ambiences.json valide.") from exc
        for entry in manifest.get("clips", []):
            info = {f: entry.get(f) for f in CLIP_FIELDS if f in entry}
            validate(
                info.get("categories"),
                info.get("license"),
                info.get("source_url"),
                info.get("title"),
            )
            existing = AmbienceClip.objects.filter(
                source_sha256=info.get("source_sha256"),
                source_start_ms=info.get("source_start_ms"),
                source_end_ms=info.get("source_end_ms"),
            ).first()
            if existing:
                for key, value in info.items():
                    setattr(existing, key, value)
                existing.save()
                updated += 1
                continue
            name = str(entry.get("file", ""))
            if not name.startswith("files/") or ".." in name:
                raise AmbienceError(f"Chemin de fichier invalide : {name}")
            data = archive.read(name)
            stored = f"ambiences/{hashlib.sha256(data).hexdigest()}.flac"
            if not default_storage.exists(stored):
                default_storage.save(stored, ContentFile(data))
            AmbienceClip.objects.create(file=stored, **info)
            created += 1
    return created, updated
