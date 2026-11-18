from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from django.core.files.storage import default_storage
from django.core.management import CommandError, call_command

from bank.content import item_fields, length_check, script_hash, within_tolerance
from bank.management.commands.reset_demo import (
    DEMO_EMAIL,
    DEMO_PASSWORD,
    populate,
    remove_demo_files,
)
from bank.models import AudioAsset, ExamFormat, Item, Test

pytestmark = pytest.mark.django_db


def test_make_dev_test_rapide(formats, demo_db):
    call_command("make_dev_test", "--mode", "rapide")
    test = Test.objects.get()
    assert test.status == Test.Status.PUBLISHED
    assert test.published_at is not None
    assert test.format.mode == "rapide"
    assert test.test_items.count() == 24
    assert Item.objects.filter(status=Item.Status.PUBLISHED).count() == 24

    co_items = Item.objects.filter(section="CO")
    assert co_items.count() == 9
    for item in co_items:
        asset = item.audio
        assert asset.engine == "fake"
        assert asset.file.name.startswith("audio/")
        assert asset.file.name.endswith(".mp3")
        assert default_storage.exists(asset.file.name)
        assert default_storage.open(asset.file.name).read(3) in (b"ID3", b"\xff\xfb", b"\xff\xf3")
        assert asset.duration_ms > 0
        assert asset.script_hash == script_hash(item_fields(item))
    assert AudioAsset.objects.count() == 9


def test_dev_test_items_have_right_length(formats, demo_db):
    call_command("make_dev_test", "--mode", "moyen")
    for item in Item.objects.all():
        value, low, high, _ = length_check(item_fields(item))
        assert within_tolerance(value, low, high), (item.section, item.level, value)


def test_make_dev_test_twice_gives_two_tests(formats, demo_db):
    call_command("make_dev_test", "--mode", "rapide")
    call_command("make_dev_test", "--mode", "rapide")
    assert sorted(Test.objects.values_list("number", flat=True)) == [1, 2]


def test_make_dev_test_refused_in_production(formats, demo_db, settings):
    settings.DEBUG = False
    with pytest.raises(CommandError, match="développement"):
        call_command("make_dev_test", "--mode", "rapide")
    assert Test.objects.count() == 0


def test_make_dev_test_requires_formats(db, demo_db):
    with pytest.raises(CommandError, match="seed_formats"):
        call_command("make_dev_test", "--mode", "rapide")


def test_make_dev_test_refused_outside_demo_database(formats, settings):
    """La base de test n'est pas la base de démonstration : refus, rien n'est créé."""
    settings.DEBUG = True
    with pytest.raises(CommandError, match="base de démonstration"):
        call_command("make_dev_test", "--mode", "rapide")
    assert Test.objects.count() == 0


def test_make_dev_test_refused_with_real_providers(formats, demo_db, settings):
    settings.TTS_PROVIDER = "google"
    with pytest.raises(CommandError, match="factices"):
        call_command("make_dev_test", "--mode", "rapide")
    assert Test.objects.count() == 0


def test_reset_demo_refused_outside_demo_database(db, settings):
    settings.DEBUG = True
    with pytest.raises(CommandError, match="base de démonstration"):
        call_command("reset_demo")


def test_reset_demo_removes_database_and_media(tmp_path):
    db_path = tmp_path / "demo.sqlite3"
    for suffix in ("", "-wal", "-shm"):
        Path(f"{db_path}{suffix}").write_text("x")
    media = tmp_path / "demo-media" / "audio"
    media.mkdir(parents=True)
    (media / "a.mp3").write_bytes(b"ID3")
    remove_demo_files(db_path, tmp_path / "demo-media")
    assert list(tmp_path.iterdir()) == []


def test_reset_demo_creates_formats_and_admin(db):
    assert populate(with_tests=False) == []
    assert ExamFormat.objects.count() == 3
    admin = get_user_model().objects.get(email=DEMO_EMAIL)
    assert admin.is_superuser and admin.is_staff
    assert admin.check_password(DEMO_PASSWORD)


def test_reset_demo_creates_tests_to_pass_and_a_draft(db, monkeypatch):
    from bank import audio

    monkeypatch.setattr(audio, "encode_mp3", lambda pcm: b"MP3" + pcm[:64])
    created = populate()
    assert len(created) == 5
    published = Test.objects.filter(status=Test.Status.PUBLISHED)
    assert sorted(t.format.mode for t in published) == ["moyen", "rapide", "rapide", "rapide"]
    draft = Test.objects.get(status=Test.Status.DRAFT)
    assert draft.import_batch.uploaded_by.email == DEMO_EMAIL  # corrections possibles
