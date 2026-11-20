"""Transfert des tests préparés en local vers le serveur (SPEC 18), sans régénérer l'audio.

`export_archive` écrit une archive zip des tests publiés : tests, items, briefs, fichiers MP3 et
leurs `AudioAsset`. `import_archive` les recrée, test par test, chacun tout ou rien, en refusant
un test dont un item existe déjà (même `content_hash`). Aucun compte, passage ni conseil.
"""

import json
import zipfile
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from bank.models import AudioAsset, Brief, ExamFormat, Item, Test, TestItem

ARCHIVE_VERSION = 1
MANIFEST = "manifest.json"
ITEM_FIELDS = [
    "section",
    "level",
    "subtype",
    "stimulus_text",
    "audio_script",
    "question",
    "options",
    "correct_index",
    "options_mode",
    "explanation",
    "tags",
    "word_count",
    "content_hash",
    "status",
]
AUDIO_FIELDS = ["duration_ms", "engine", "voices", "script_hash", "chars_billed", "mix"]
# Valeurs des champs absents des archives plus anciennes.
AUDIO_DEFAULTS = {"mix": {}}


class TransferError(Exception):
    """Archive illisible : message en français."""


@dataclass
class ImportOutcome:
    imported: list[str] = field(default_factory=list)
    refused: list[str] = field(default_factory=list)  # « test : raison »


def _iso(value) -> str | None:
    return value.isoformat() if value else None


# --- Export --------------------------------------------------------------------------------------


def export_archive(path, tests=None) -> list[Test]:
    """Écrit l'archive ; renvoie les tests exportés (tous les tests publiés par défaut)."""
    if tests is None:
        tests = Test.objects.filter(status=Test.Status.PUBLISHED)
    tests = list(tests.select_related("format", "import_batch__brief").order_by("format", "number"))
    entries, briefs, files = [], {}, {}
    for test in tests:
        brief = test.import_batch.brief if test.import_batch else None
        if brief:
            briefs[brief.brief_id] = {
                "brief_id": brief.brief_id,
                "format": brief.format.code,
                "number": brief.number,
                "content": brief.content,
            }
        items = []
        for test_item in test.test_items.select_related("item", "item__audio").order_by("slot"):
            item = test_item.item
            data = {
                "slot": test_item.slot,
                "position": test_item.position,
                **{name: getattr(item, name) for name in ITEM_FIELDS},
                "audio": None,
            }
            asset = getattr(item, "audio", None)
            if asset is not None:
                if not default_storage.exists(asset.file.name):
                    raise TransferError(f"{test} : fichier audio absent ({asset.file.name}).")
                files[asset.file.name] = asset.file.name
                data["audio"] = {
                    "file": asset.file.name,
                    **{f: getattr(asset, f) for f in AUDIO_FIELDS},
                }
            items.append(data)
        entries.append(
            {
                "format": test.format.code,
                "number": test.number,
                "public_number": test.public_number,
                "published_at": _iso(test.published_at),
                "blind_check": test.blind_check,
                "brief_id": brief.brief_id if brief else None,
                "items": items,
            }
        )
    manifest = {
        "version": ARCHIVE_VERSION,
        "exported_at": timezone.now().isoformat(),
        "briefs": list(briefs.values()),
        "tests": entries,
    }
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(MANIFEST, json.dumps(manifest, ensure_ascii=False, indent=2))
        for name in sorted(files):
            with default_storage.open(name, "rb") as source:
                # Les MP3 sont déjà compressés.
                archive.writestr(f"media/{name}", source.read(), compress_type=zipfile.ZIP_STORED)
    return tests


# --- Import --------------------------------------------------------------------------------------


def _read(path) -> tuple[zipfile.ZipFile, dict]:
    try:
        archive = zipfile.ZipFile(path)
        manifest = json.loads(archive.read(MANIFEST).decode("utf-8"))
    except (OSError, KeyError, zipfile.BadZipFile, ValueError) as exc:
        raise TransferError(f"Archive illisible : {exc}.") from exc
    if manifest.get("version") != ARCHIVE_VERSION:
        raise TransferError(f"Version d'archive inconnue : {manifest.get('version')}.")
    return archive, manifest


def _safe_media_name(name: str) -> str:
    """Un chemin relatif simple : jamais « .. » ni chemin absolu (archive mal formée)."""
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or not name:
        raise TransferError(f"Chemin de fichier refusé : {name}.")
    return name


def _problems(entry: dict, archive: zipfile.ZipFile) -> list[str]:
    problems = []
    exam_format = ExamFormat.objects.filter(code=entry["format"]).first()
    if exam_format is None:
        return [f"format {entry['format']} absent (lancez seed_formats)"]
    if Test.objects.filter(format=exam_format, number=entry["number"]).exists():
        problems.append(f"un test n° {entry['number']} existe déjà dans ce mode")
    hashes = [item["content_hash"] for item in entry["items"]]
    existing = Item.objects.filter(content_hash__in=hashes).count()
    if existing:
        problems.append(f"{existing} question(s) déjà présente(s) (même content_hash)")
    names = set(archive.namelist())
    for item in entry["items"]:
        audio = item["audio"]
        if audio and f"media/{_safe_media_name(audio['file'])}" not in names:
            problems.append(f"fichier audio absent de l'archive : {audio['file']}")
    return problems


def _label(entry: dict) -> str:
    return f"{entry['format']} n° {entry['number']}"


def import_archive(path) -> ImportOutcome:
    archive, manifest = _read(path)
    outcome = ImportOutcome()
    briefs = {b["brief_id"]: b for b in manifest.get("briefs", [])}
    with archive:
        # Les numéros publics sont attribués sur le serveur, dans l'ordre de ceux de l'ordinateur.
        entries = sorted(
            manifest.get("tests", []),
            key=lambda e: (e["format"], e.get("public_number") or 10**9, e["number"]),
        )
        for entry in entries:
            problems = _problems(entry, archive)
            if problems:
                outcome.refused.append(f"{_label(entry)} : {' ; '.join(problems)}")
                continue
            written: list[str] = []
            try:
                with transaction.atomic():
                    _create(entry, briefs.get(entry.get("brief_id")), archive, written)
            except Exception:
                for name in written:  # la base est annulée : les fichiers écrits aussi
                    default_storage.delete(name)
                raise
            outcome.imported.append(_label(entry))
    return outcome


def _create(entry: dict, brief_data: dict | None, archive, written: list[str]) -> None:
    exam_format = ExamFormat.objects.get(code=entry["format"])
    if brief_data and not Brief.objects.filter(brief_id=brief_data["brief_id"]).exists():
        # Le brief suit le test : la numérotation des briefs suivants continue après lui.
        Brief.objects.create(
            format=exam_format,
            number=brief_data["number"],
            brief_id=brief_data["brief_id"],
            content=brief_data["content"],
        )
    test = Test.objects.create(
        format=exam_format,
        number=entry["number"],
        status=Test.Status.PUBLISHED,
        published_at=parse_datetime(entry["published_at"]) if entry["published_at"] else None,
        blind_check=entry.get("blind_check") or {},
    )
    test.assign_public_number()
    test.save(update_fields=["public_number"])
    for data in entry["items"]:
        item = Item.objects.create(**{name: data[name] for name in ITEM_FIELDS})
        TestItem.objects.create(
            test=test,
            item=item,
            section=data["section"],
            position=data["position"],
            slot=data["slot"],
        )
        audio = data["audio"]
        if audio:
            name = _safe_media_name(audio["file"])
            if not default_storage.exists(name):
                saved = default_storage.save(name, ContentFile(archive.read(f"media/{name}")))
                written.append(saved)
                name = saved
            AudioAsset.objects.create(
                item=item,
                file=name,
                **{f: audio.get(f, AUDIO_DEFAULTS.get(f)) for f in AUDIO_FIELDS},
            )
