"""« Valider » en CO (4.2, 8.5), numéro public des tests (5), approbation groupée (11)."""

import importlib
import random
from datetime import timedelta

import pytest
from django.apps import apps
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from bank import audio, review, synthesis
from bank.deletion import delete_test
from bank.devtest import make_dev_draft, make_dev_test
from bank.models import ExamFormat, FormatSection, Item, Test
from bank.tts import FakeTTSProvider
from exam import services
from exam.models import AttemptSection
from tests.conftest import PASSWORD

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def fast_mp3(monkeypatch):
    def fake_encode(pcm: bytes) -> bytes:
        return b"MP3" + len(pcm).to_bytes(8, "big") + pcm[:64]

    monkeypatch.setattr(audio, "encode_mp3", fake_encode)
    monkeypatch.setattr(synthesis, "encode_mp3", fake_encode)


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("u", "u@example.com", PASSWORD)


def bo(name, *args):
    return reverse(f"backoffice:{name}", args=args)


# --- « Valider » en compréhension orale (allow_early_next) ---------------------------------------


def test_early_next_disabled_by_default(formats):
    """Pas de passage anticipé (4.2, 19) : l'option existe mais reste désactivée."""
    assert not FormatSection.objects.filter(allow_early_next=True).exists()


def test_migration_disables_early_next_in_existing_bases(formats):
    FormatSection.objects.filter(section="CO").update(allow_early_next=True)
    migration = importlib.import_module("bank.migrations.0007_disable_early_next")
    migration.disable(apps, None)
    assert not FormatSection.objects.filter(allow_early_next=True).exists()


def test_migration_enables_early_next_for_existing_co_sections(formats):
    FormatSection.objects.update(allow_early_next=False)
    migration = importlib.import_module("bank.migrations.0005_formatsection_allow_early_next")
    migration.enable_for_co(apps, None)
    assert set(
        FormatSection.objects.filter(allow_early_next=True).values_list("section", flat=True)
    ) == {"CO"}


def test_player_receives_the_setting(client, user, formats):
    test = make_dev_test("rapide", rng=random.Random(1))
    attempt = services.create_attempt(user, test)
    client.force_login(user)
    state = client.get(reverse("api_attempt_state", args=[attempt.pk])).json()
    assert {s["allow_early_next"] for s in state["sections"]} == {False}
    FormatSection.objects.filter(section="CO").update(allow_early_next=True)  # option activée
    state = client.get(reverse("api_attempt_state", args=[attempt.pk])).json()
    by_section = {s["section"]: s["allow_early_next"] for s in state["sections"]}
    assert by_section == {"CO": True, "MSL": False, "CE": False}
    html = client.get(reverse("attempt_player", args=[attempt.pk])).content.decode()
    assert ">Valider</button>" in html and "allow_early_next" in html


def test_early_next_shortens_the_section_but_not_the_next_items(user, formats):
    test = make_dev_test("rapide", rng=random.Random(1))
    start = timezone.now()
    attempt = services.create_attempt(user, test, now=start)
    section = services.start_section(attempt, 1, now=start)
    first = services.current_co_answer(section)
    window = services.format_section(section).answer_window_seconds
    deadline_before = section.deadline_at
    # Réponse choisie, puis « Valider » 10 s après l'apparition de la question.
    first.selected_index = 1
    first.save(update_fields=["selected_index"])
    clicked = start + timedelta(seconds=10)
    section = services.co_next(attempt, first.test_item_id, now=clicked)
    section.refresh_from_db()
    second = services.current_co_answer(section)
    assert second.test_item_id != first.test_item_id
    assert second.first_seen_at == clicked
    following = services.section_items(section).filter(position__gte=second.test_item.position)
    expected = clicked + timedelta(seconds=services.co_remaining_seconds(section, following))
    assert abs((section.deadline_at - expected).total_seconds()) < 0.01
    assert section.deadline_at < deadline_before  # le temps gagné raccourcit l'épreuve
    # La question suivante garde tout son temps : audio + temps de réponse.
    limit = services.co_item_limit(second, window)
    assert (
        limit - clicked
        == timedelta(seconds=services.co_item_seconds(second.test_item) + window)
        + services.CO_ITEM_GRACE
    )
    assert section.status == AttemptSection.Status.IN_PROGRESS


# --- Numéro public (section 5) -------------------------------------------------------------------


def publish_draft(user, seed):
    test = make_dev_draft("rapide", rng=random.Random(seed), user=user)
    while not synthesis.audio_progress(test).finished:
        synthesis.generate_next(test, provider=FakeTTSProvider())
    Item.objects.filter(test_item__test=test).update(status=Item.Status.APPROVED)
    review.publish(test)
    test.refresh_from_db()
    return test


def test_public_number_given_at_publication_per_mode(formats, staff_user):
    draft = make_dev_draft("rapide", rng=random.Random(1), user=staff_user)
    assert draft.public_number is None
    first = publish_draft(staff_user, 2)
    second = publish_draft(staff_user, 3)
    other_mode = make_dev_test("moyen", rng=random.Random(4))
    assert (first.public_number, second.public_number, other_mode.public_number) == (1, 2, 1)
    assert first.number != first.public_number  # le brouillon a pris le numéro interne 1
    assert second.public_label == "Entraînement 2"
    assert first.admin_label == f"test n° {first.number} · publié comme Entraînement 1"


def test_public_number_is_never_reused(formats, staff_user):
    first = make_dev_test("rapide", rng=random.Random(1))
    last = make_dev_test("rapide", rng=random.Random(2))
    assert last.public_number == 2
    last.status = Test.Status.ARCHIVED
    last.save()
    delete_test(last)
    assert make_dev_test("rapide", rng=random.Random(3)).public_number == 3
    assert first.public_number == 1


def test_migration_numbers_published_tests_in_publication_order(formats, staff_user):
    now = timezone.now()
    tests = [make_dev_test("rapide", rng=random.Random(i)) for i in range(3)]
    draft = make_dev_draft("rapide", rng=random.Random(9), user=staff_user)
    # Publiés dans l'ordre 3, 1, 2 ; le premier publié a été archivé depuis.
    for offset, test in zip((2, 0, 1), tests, strict=True):
        Test.objects.filter(pk=test.pk).update(
            public_number=None, published_at=now + timedelta(days=offset)
        )
    Test.objects.filter(pk=tests[1].pk).update(status=Test.Status.ARCHIVED)
    ExamFormat.objects.update(last_public_number=0)
    migration = importlib.import_module("bank.migrations.0006_test_public_number")
    migration.number_published_tests(apps, None)
    numbers = {t.pk: t.public_number for t in Test.objects.all()}
    assert [numbers[t.pk] for t in tests] == [3, 1, 2]
    assert numbers[draft.pk] is None
    assert ExamFormat.objects.get(mode="rapide").last_public_number == 3


def test_users_only_see_the_public_number(client, user, formats, staff_user):
    make_dev_draft("rapide", rng=random.Random(1), user=staff_user)  # numéro interne 1
    make_dev_draft("rapide", rng=random.Random(2), user=staff_user)  # numéro interne 2
    test = make_dev_test("rapide", rng=random.Random(3))  # interne 3, public 1
    assert (test.number, test.public_number) == (3, 1)
    attempt = services.create_attempt(user, test)
    client.force_login(user)
    pages = [reverse("dashboard")]
    for page in pages:
        html = client.get(page).content.decode()
        assert "Entraînement 1" in html
        assert "n° 3" not in html and "test n°" not in html.lower()
    from tests.test_advice import complete

    attempt.delete()
    done = complete(user, test)
    for name in ("attempt_results", "attempt_review"):
        html = client.get(reverse(name, args=[done.pk])).content.decode()
        assert "Entraînement 1" in html and "n° 3" not in html
    html = client.get(reverse("history")).content.decode()
    assert "Entraînement 1" in html and "n° 3" not in html


def test_backoffice_shows_both_numbers(client, staff_user, formats):
    test = make_dev_test("rapide", rng=random.Random(3))
    client.force_login(staff_user)
    assert "test n° 1 · publié comme Entraînement 1" in client.get(bo("tests")).content.decode()
    assert "publié comme Entraînement 1" in client.get(bo("test_review", test.pk)).content.decode()


def test_transferred_tests_get_server_public_numbers(formats, tmp_path):
    from bank.transfer import export_archive, import_archive

    make_dev_test("rapide", rng=random.Random(1))
    make_dev_test("rapide", rng=random.Random(2))
    archive = tmp_path / "t.zip"
    export_archive(archive)
    Test.objects.all().delete()
    Item.objects.all().delete()
    ExamFormat.objects.update(last_public_number=0)
    import_archive(archive)
    assert sorted(Test.objects.values_list("number", "public_number")) == [(1, 1), (2, 2)]


# --- Approbation groupée (section 11) ------------------------------------------------------------


@pytest.fixture
def reviewed(formats, staff_user):
    """Brouillon relu à l'aveugle : un désaccord, un doute, une question non contrôlée."""
    test = make_dev_draft("rapide", rng=random.Random(8), user=staff_user)
    items = list(Item.objects.filter(test_item__test=test).order_by("test_item__slot"))
    answers = {str(i.test_item.slot): i.correct_index for i in items}
    wrong, doubt, unchecked = items[0], items[1], items[2]
    answers[str(wrong.test_item.slot)] = (wrong.correct_index + 1) % 4
    del answers[str(unchecked.test_item.slot)]
    test.blind_check = {
        "answers": answers,
        "doubts": {str(doubt.test_item.slot): "B et C défendables."},
        "received_at": timezone.now().isoformat(),
    }
    test.save()
    already = items[3]
    already.status = Item.Status.APPROVED
    already.save()
    rows = review.rows(test)
    warned = {r.item.pk for r in rows if r.warnings}
    excluded = {wrong.pk, doubt.pk, unchecked.pk, already.pk} | warned
    return test, items, excluded


def test_approvable_rows_exclude_every_alert(reviewed):
    test, items, excluded = reviewed
    approvable = {r.item.pk for r in review.approvable(review.rows(test))}
    assert approvable == {i.pk for i in items} - excluded
    assert approvable  # le test a bien des questions sans alerte


def test_bulk_approval_button_and_confirmation(client, staff_user, reviewed):
    test, items, excluded = reviewed
    count = len(items) - len(excluded)
    client.force_login(staff_user)
    html = client.get(bo("test_review", test.pk)).content.decode()
    assert f"Approuver les {count} questions sans alerte" in html
    assert "Oui, approuver" in html and f'name="expected" value="{count}"' in html

    response = client.post(bo("approve_clean", test.pk), {"expected": count}, follow=True)
    assert f"{count} question(s) sans alerte approuvée(s)" in response.content.decode()
    statuses = {i.pk: Item.objects.get(pk=i.pk).status for i in items}
    for pk, status in statuses.items():
        if pk in excluded and pk != items[3].pk:
            assert status == Item.Status.DRAFT
        else:
            assert status == Item.Status.APPROVED
    html = client.get(bo("test_review", test.pk)).content.decode()
    assert "sans alerte" not in html.split('id="bilan-titre"')[1].split("</section>")[0]


def test_bulk_approval_refused_when_the_list_changed(client, staff_user, reviewed):
    test, items, excluded = reviewed
    count = len(items) - len(excluded)
    client.force_login(staff_user)
    response = client.post(bo("approve_clean", test.pk), {"expected": count + 1}, follow=True)
    assert "rien n&#x27;a été approuvé" in response.content.decode()
    assert (
        not Item.objects.filter(test_item__test=test, status=Item.Status.APPROVED)
        .exclude(pk=items[3].pk)
        .exists()
    )


def test_bulk_approval_needs_reviewer_answers_and_a_draft(client, staff_user, formats):
    test = make_dev_draft("rapide", rng=random.Random(8), user=staff_user)
    assert review.approvable(review.rows(test)) == []  # rien de contrôlé : rien d'approuvable
    published = make_dev_test("rapide", rng=random.Random(2))
    with pytest.raises(review.ReviewError):
        review.approve_clean(published, 0)
