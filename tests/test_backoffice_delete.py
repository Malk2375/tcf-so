"""Back-office : badge et refus de publication d'un audio factice avec Google (7.4, 11) ;
suppression d'un test en brouillon ou archivé (11)."""

import random

import pytest
from django.contrib.auth import get_user_model
from django.core.files.storage import default_storage
from django.urls import reverse

from advice.models import Advice
from bank import audio, synthesis
from bank.deletion import deletion_problems
from bank.devtest import make_dev_draft, make_dev_test
from bank.models import AudioAsset, Brief, ImportBatch, Item, Test
from bank.synthesis import generate_next
from bank.tts import FakeTTSProvider
from exam import services
from exam.models import Attempt
from tests.conftest import PASSWORD

pytestmark = pytest.mark.django_db
HTMX = {"HTTP_HX_REQUEST": "true"}


@pytest.fixture(autouse=True)
def fast_mp3(monkeypatch):
    def fake_encode(pcm: bytes) -> bytes:
        return b"MP3" + len(pcm).to_bytes(8, "big") + pcm[:64]

    monkeypatch.setattr(audio, "encode_mp3", fake_encode)
    monkeypatch.setattr(synthesis, "encode_mp3", fake_encode)


@pytest.fixture
def draft(formats, staff_user):
    return make_dev_draft("rapide", rng=random.Random(8), user=staff_user)


def url(name, *args):
    return reverse(f"backoffice:{name}", args=args)


def ready_to_publish(client, test):
    for item in Item.objects.filter(test_item__test=test):
        client.post(url("item_status", test.pk, item.pk), {"action": "approve"}, **HTMX)
    for _ in range(50):
        if generate_next(test).finished:
            return
    raise AssertionError("La génération ne se termine pas.")


# --- Audio factice et Google --------------------------------------------------------------------


def test_review_shows_engine_and_fake_badge(staff_client, draft):
    ready_to_publish(staff_client, draft)
    page = staff_client.get(url("test_review", draft.pk)).content.decode()
    assert "Audio factice" in page
    assert "Moteur : factice" in page


def test_fake_audio_blocks_publication_with_google(staff_client, draft, settings):
    ready_to_publish(staff_client, draft)
    settings.TTS_PROVIDER = "google"
    page = staff_client.get(url("test_review", draft.pk)).content.decode()
    assert "Audio à refaire avec le moteur actif" in page
    response = staff_client.post(url("publish", draft.pk), follow=True)
    assert "autre moteur" in response.content.decode()
    draft.refresh_from_db()
    assert draft.status == Test.Status.DRAFT

    # Un nouveau clic régénère tout l'audio avec le moteur actif (ici simulé, sans réseau).
    class Google(FakeTTSProvider):
        name = "google_chirp3_hd"

    for _ in range(50):
        if generate_next(draft, Google()).finished:
            break
    assert set(AudioAsset.objects.values_list("engine", flat=True)) == {"google_chirp3_hd"}
    staff_client.post(url("publish", draft.pk))
    draft.refresh_from_db()
    assert draft.status == Test.Status.PUBLISHED


def test_fake_audio_publishes_with_fake_provider(staff_client, draft):
    ready_to_publish(staff_client, draft)
    staff_client.post(url("publish", draft.pk))
    draft.refresh_from_db()
    assert draft.status == Test.Status.PUBLISHED


# --- Suppression -------------------------------------------------------------------------------


def delete(client, test, number=None):
    number = test.number if number is None else number
    return client.post(url("delete", test.pk), {"confirm_number": number}, follow=True)


def test_delete_draft_removes_items_audio_and_files(staff_client, draft):
    ready_to_publish(staff_client, draft)
    paths = list(AudioAsset.objects.values_list("file", flat=True))
    assert paths and all(default_storage.exists(p) for p in paths)
    batch_id = draft.import_batch_id
    response = delete(staff_client, draft)
    assert "supprimé" in response.content.decode()
    assert not Test.objects.filter(pk=draft.pk).exists()
    assert Item.objects.count() == 0
    assert AudioAsset.objects.count() == 0
    assert not any(default_storage.exists(p) for p in paths)
    # Le brief et l'import restent, pour la trace.
    assert ImportBatch.objects.filter(pk=batch_id).exists()
    assert Brief.objects.count() == 1


def test_delete_requires_the_test_number(staff_client, draft):
    response = delete(staff_client, draft, number=draft.number + 1)
    assert "tapez le numéro du test" in response.content.decode()
    assert Test.objects.filter(pk=draft.pk).exists()


def test_published_test_cannot_be_deleted(staff_client, formats):
    test = make_dev_test("rapide", rng=random.Random(1))
    response = delete(staff_client, test)
    assert "archivez-le" in response.content.decode()
    assert Test.objects.filter(pk=test.pk).exists()


def finish(user, test):
    attempt = services.create_attempt(user, test)
    attempt.status = Attempt.Status.COMPLETED
    attempt.save()
    return attempt


def test_archived_test_passed_by_a_user_cannot_be_deleted(staff_client, formats):
    test = make_dev_test("rapide", rng=random.Random(1))
    user = get_user_model().objects.create_user("u", "u@example.com", PASSWORD)
    finish(user, test)
    test.status = Test.Status.ARCHIVED
    test.save()
    page = staff_client.get(url("test_review", test.pk)).content.decode()
    refusal = "passé par le compte non administrateur u@example.com"
    assert refusal in page
    response = delete(staff_client, test)
    assert refusal in response.content.decode()  # le refus nomme le compte en cause
    assert Test.objects.filter(pk=test.pk).exists()


def test_refusal_names_every_user_account(formats):
    test = make_dev_test("rapide", rng=random.Random(1))
    users = get_user_model().objects
    finish(users.create_user("b", "b@example.com", PASSWORD), test)
    finish(users.create_user("a", "", PASSWORD), test)  # sans e-mail : son identifiant
    test.status = Test.Status.ARCHIVED
    test.save()
    (problem,) = deletion_problems(test)
    assert "les comptes non administrateurs a, b@example.com :" in problem


def test_abandoned_attempt_by_superuser_does_not_block_deletion(staff_client, formats):
    """Cas du test n° 1 : passé puis abandonné par le compte administrateur."""
    test = make_dev_test("rapide", rng=random.Random(1))
    owner = get_user_model().objects.create_superuser("owner", "owner@example.com", PASSWORD)
    attempt = finish(owner, test)
    attempt.status = Attempt.Status.ABANDONED
    attempt.save()
    test.status = Test.Status.ARCHIVED
    test.save()
    assert deletion_problems(test) == []
    delete(staff_client, test)
    assert not Test.objects.filter(pk=test.pk).exists()


def test_archived_test_passed_by_admin_is_deleted_with_attempts_and_advice(
    staff_client, staff_user, formats
):
    test = make_dev_test("rapide", rng=random.Random(1))
    other = make_dev_test("rapide", rng=random.Random(2))
    attempt = finish(staff_user, test)
    kept_attempt = finish(staff_user, other)
    Advice.objects.create(user=staff_user, attempt=attempt, source="fallback")
    # Conseil déclenché par un autre test, mais calculé avec les réponses du test supprimé.
    Advice.objects.create(
        user=staff_user,
        attempt=kept_attempt,
        attempt_ids=[kept_attempt.pk, attempt.pk],
        source="fallback",
    )
    test.status = Test.Status.ARCHIVED
    test.save()
    delete(staff_client, test)
    assert not Test.objects.filter(pk=test.pk).exists()
    assert not Attempt.objects.filter(pk=attempt.pk).exists()
    assert Advice.objects.count() == 0
    assert Attempt.objects.filter(pk=kept_attempt.pk).exists()
    assert Item.objects.filter(test_item__test=other).count() == 24


def test_shared_audio_file_is_kept(staff_client, formats, staff_user):
    first = make_dev_draft("rapide", rng=random.Random(8), user=staff_user)
    second = make_dev_test("rapide", rng=random.Random(3))
    asset = AudioAsset.objects.filter(item__test_item__test=second).first()
    for _ in range(50):
        if generate_next(first).finished:
            break
    # Le même fichier MP3 sert à un item de l'autre test (fichiers nommés par leur contenu).
    shared = AudioAsset.objects.filter(item__test_item__test=first).first()
    AudioAsset.objects.filter(pk=asset.pk).update(file=shared.file.name)
    delete(staff_client, first)
    assert default_storage.exists(shared.file.name)
