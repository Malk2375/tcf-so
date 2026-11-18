"""Transfert des tests préparés en local : export_tests, import_tests (SPEC 18, PLAN étape 7)."""

import json
import random
import zipfile

import pytest
from django.contrib.auth import get_user_model
from django.core.files.storage import default_storage
from django.core.management import CommandError, call_command
from django.urls import reverse

from advice.models import Advice
from bank import audio, review, synthesis
from bank.audio import AudioState, audio_state
from bank.devtest import make_dev_draft, make_dev_test
from bank.models import AudioAsset, Brief, ImportBatch, Item, Test, TestItem
from bank.synthesis import generate_next
from bank.transfer import TransferError, export_archive, import_archive
from bank.tts import FakeTTSProvider
from exam import services
from exam.models import Attempt
from tests.conftest import PASSWORD
from usage.models import UsageEntry

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def fast_mp3(monkeypatch):
    def fake_encode(pcm: bytes) -> bytes:
        return b"MP3" + len(pcm).to_bytes(8, "big") + pcm[:64]

    monkeypatch.setattr(audio, "encode_mp3", fake_encode)
    monkeypatch.setattr(synthesis, "encode_mp3", fake_encode)


def published_from_import(user):
    """Test passé par le vrai circuit : brief, import, audio, approbation, publication."""
    test = make_dev_draft("rapide", rng=random.Random(8), user=user)
    while not synthesis.audio_progress(test).finished:
        generate_next(test, provider=FakeTTSProvider())
    Item.objects.filter(test_item__test=test).update(status=Item.Status.APPROVED)
    review.publish(test)
    test.refresh_from_db()
    return test


def snapshot(test: Test) -> dict:
    """Ce qui doit être identique après transfert : questions, ordre, audio (octets compris)."""
    rows = {}
    for test_item in test.test_items.select_related("item", "item__audio"):
        item = test_item.item
        asset = getattr(item, "audio", None)
        rows[test_item.slot] = {
            "position": (test_item.section, test_item.position),
            "fields": {
                name: getattr(item, name)
                for name in (
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
                    "content_hash",
                    "status",
                )
            },
            "audio": None
            if asset is None
            else (
                asset.file.name,
                default_storage.open(asset.file.name).read(),
                asset.duration_ms,
                asset.script_hash,
                asset.engine,
                asset.voices,
            ),
        }
    return {"format": test.format.code, "number": test.number, "items": rows}


def empty_bank(settings, tmp_path):
    """Base « vide » côté serveur : plus aucun test, item, brief ni fichier audio."""
    Advice.objects.all().delete()
    Attempt.objects.all().delete()
    TestItem.objects.all().delete()
    Test.objects.all().delete()
    Item.objects.all().delete()
    Brief.objects.all().delete()
    ImportBatch.objects.all().delete()
    UsageEntry.objects.all().delete()
    get_user_model().objects.all().delete()
    settings.MEDIA_ROOT = tmp_path / "server-media"


def test_exported_test_is_identical_on_an_empty_server(formats, staff_user, settings, tmp_path):
    test = published_from_import(staff_user)
    plain = make_dev_test("moyen", rng=random.Random(2))  # sans brief
    make_dev_draft("rapide", rng=random.Random(9), user=staff_user)  # brouillon : non exporté
    before = [snapshot(test), snapshot(plain)]
    brief = test.import_batch.brief
    archive = tmp_path / "tests.zip"
    exported = export_archive(archive)
    assert {t.pk for t in exported} == {test.pk, plain.pk}

    empty_bank(settings, tmp_path)
    outcome = import_archive(archive)
    assert outcome.refused == [] and len(outcome.imported) == 2

    after = [
        snapshot(Test.objects.get(format__code=s["format"], number=s["number"])) for s in before
    ]
    assert after == before
    assert Brief.objects.get().brief_id == brief.brief_id
    assert Brief.objects.get().content == brief.content
    assert UsageEntry.objects.count() == 0  # aucune synthèse
    for asset in AudioAsset.objects.select_related("item"):
        assert audio_state(asset.item) == AudioState.OK

    # Le test importé se passe : la CO renvoie l'audio transféré.
    user = get_user_model().objects.create_user("u", "u@example.com", PASSWORD)
    imported = Test.objects.get(format__code="tcf_tp_so_rapide")
    attempt = services.create_attempt(user, imported)
    from django.test import Client

    client = Client()
    client.force_login(user)
    data = client.post(
        reverse("api_section_start", args=[attempt.pk, 1]), "{}", content_type="application/json"
    ).json()
    first_co = imported.test_items.get(pk=data["item"]["test_item_id"]).item
    assert data["item"]["audio_url"].endswith(first_co.audio.file.name)
    assert default_storage.exists(first_co.audio.file.name)


def test_archive_holds_no_account_attempt_or_advice(formats, staff_user, tmp_path):
    test = make_dev_test("rapide", rng=random.Random(1))
    attempt = services.create_attempt(staff_user, test)
    Advice.objects.create(user=staff_user, attempt=attempt, source="fallback")
    archive = tmp_path / "tests.zip"
    export_archive(archive)
    with zipfile.ZipFile(archive) as zf:
        manifest = zf.read("manifest.json").decode()
        names = zf.namelist()
    assert staff_user.email not in manifest
    assert set(json.loads(manifest)) == {"version", "exported_at", "briefs", "tests"}
    assert all(name == "manifest.json" or name.startswith("media/audio/") for name in names)


def test_test_whose_item_exists_is_refused(formats, tmp_path):
    make_dev_test("rapide", rng=random.Random(1))
    archive = tmp_path / "tests.zip"
    export_archive(archive)
    Test.objects.update(number=99)  # autre numéro : seul le content_hash bloque
    before = Item.objects.count()
    outcome = import_archive(archive)
    assert outcome.imported == []
    assert "déjà présente(s) (même content_hash)" in outcome.refused[0]
    assert Item.objects.count() == before


def test_unsafe_file_name_is_refused(formats, tmp_path):
    archive = tmp_path / "bad.zip"
    test = make_dev_test("rapide", rng=random.Random(1))
    export_archive(archive)
    with zipfile.ZipFile(archive) as zf:
        manifest = json.loads(zf.read("manifest.json"))
    co = next(i for i in manifest["tests"][0]["items"] if i["audio"])
    co["audio"]["file"] = "../../config/settings.py"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("manifest.json", json.dumps(manifest))
    test.delete()
    with pytest.raises(TransferError, match="refusé"):
        import_archive(archive)


def test_unreadable_archive(tmp_path):
    bad = tmp_path / "x.zip"
    bad.write_bytes(b"pas une archive")
    with pytest.raises(TransferError, match="illisible"):
        import_archive(bad)


def test_commands(formats, tmp_path, capsys):
    make_dev_test("rapide", rng=random.Random(1))
    archive = tmp_path / "out.zip"
    call_command("export_tests", "--output", str(archive))
    assert "1 test(s) publié(s) exporté(s)" in capsys.readouterr().out
    call_command("import_tests", str(archive))
    assert "Refusé" in capsys.readouterr().out  # déjà présent ici
    with pytest.raises(CommandError):
        call_command("import_tests", str(tmp_path / "absent.zip"))
