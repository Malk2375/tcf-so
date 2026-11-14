import pytest
from django.core.files.storage import default_storage
from django.core.management import CommandError, call_command

from bank.content import item_fields, length_check, script_hash, within_tolerance
from bank.models import AudioAsset, Item, Test

pytestmark = pytest.mark.django_db


def test_make_dev_test_rapide(formats, settings):
    settings.DEBUG = True
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


def test_dev_test_items_have_right_length(formats, settings):
    settings.DEBUG = True
    call_command("make_dev_test", "--mode", "moyen")
    for item in Item.objects.all():
        value, low, high, _ = length_check(item_fields(item))
        assert within_tolerance(value, low, high), (item.section, item.level, value)


def test_make_dev_test_twice_gives_two_tests(formats, settings):
    settings.DEBUG = True
    call_command("make_dev_test", "--mode", "rapide")
    call_command("make_dev_test", "--mode", "rapide")
    assert sorted(Test.objects.values_list("number", flat=True)) == [1, 2]


def test_make_dev_test_refused_in_production(formats, settings):
    settings.DEBUG = False
    with pytest.raises(CommandError, match="développement"):
        call_command("make_dev_test", "--mode", "rapide")
    assert Test.objects.count() == 0


def test_make_dev_test_requires_formats(db, settings):
    settings.DEBUG = True
    with pytest.raises(CommandError, match="seed_formats"):
        call_command("make_dev_test", "--mode", "rapide")
