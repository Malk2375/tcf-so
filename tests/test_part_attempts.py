"""Temps de lecture en CO (SPEC 4.2, 8.4, 8.5) et passage d'une seule épreuve (8.1 ter)."""

import random
import re
from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from advice import eligibility
from bank import audio
from bank.devtest import make_dev_test
from bank.models import Item, Test, TestItem
from exam import payloads, services
from exam.models import Answer, Attempt, AttemptSection
from tests.conftest import PASSWORD

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _fast_audio(monkeypatch):
    monkeypatch.setattr(audio, "placeholder_mp3", lambda seconds: f"mp3 {seconds:.2f}".encode())


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("candidat", "candidat@example.com", PASSWORD)


@pytest.fixture
def user_client(client, user):
    client.force_login(user)
    return client


@pytest.fixture
def moyens(formats):
    """Deux entraînements moyens publiés (Entraînement 1 et 2)."""
    return [make_dev_test("moyen", rng=random.Random(i)) for i in (1, 2)]


def finish(attempt: Attempt) -> Attempt:
    """Termine toutes les épreuves du passage (réponses vides)."""
    now = timezone.now()
    for section in attempt.sections.order_by("order"):
        if section.status == AttemptSection.Status.PENDING:
            services.start_section(attempt, section.order, now=now)
            section.refresh_from_db()
        services.close_section(section, now)
    attempt.refresh_from_db()
    return attempt


def row_of(html: str, label: str) -> str:
    """Ligne d'un test dans la liste du mode (jusqu'à la ligne suivante)."""
    for segment in html.split('data-state="')[1:]:
        if re.search(r'font-medium">\s*' + re.escape(label) + r"\s", segment):
            return segment
    raise AssertionError(f"{label} absent de la liste")


def start(client, test, scope=None):
    data = {"test": test.pk}
    if scope:
        data["scope"] = scope
    return client.post(reverse("attempt_create"), data)


# --- Temps de lecture en CO ----------------------------------------------------------------------


def co_items(test):
    return list(
        TestItem.objects.filter(test=test, section="CO").select_related("item", "item__audio")
    )


def test_reading_time_is_15_s_for_written_options_and_0_for_audio_letters(formats):
    test = make_dev_test("rapide", rng=random.Random(3))
    fs = test.format.sections.get(section="CO")
    # Un item à lettres lues, au cas où le tirage n'en aurait pas donné.
    Item.objects.filter(pk=co_items(test)[0].item_id).update(
        options_mode=Item.OptionsMode.AUDIO_LETTERS
    )
    items = co_items(test)
    written = [t for t in items if t.item.options_mode == Item.OptionsMode.TEXT]
    letters = [t for t in items if t.item.options_mode == Item.OptionsMode.AUDIO_LETTERS]
    assert written and letters
    assert {services.co_read_seconds(t, fs) for t in written} == {15}
    assert {services.co_read_seconds(t, fs) for t in letters} == {0}
    # La durée de l'épreuve compte la lecture : lecture + audio + temps de réponse.
    expected = sum(
        services.co_read_seconds(t, fs) + services.co_item_seconds(t) + 12 for t in items
    )
    assert services.co_duration_seconds(test, fs) == pytest.approx(expected)
    assert expected - sum(services.co_item_seconds(t) + 12 for t in items) == 15 * len(written)


def test_reading_time_in_the_player_payload_and_after_reload(user_client, user, formats):
    test = make_dev_test("rapide", rng=random.Random(3))
    attempt = services.create_attempt(user, test)
    url = reverse("api_section_start", args=[attempt.pk, 1])
    first = user_client.post(url, content_type="application/json").json()["item"]
    item = TestItem.objects.get(pk=first["test_item_id"]).item
    read = 0 if item.options_mode == Item.OptionsMode.AUDIO_LETTERS else 15
    assert first["read_before_seconds"] == read
    seen = timezone.datetime.fromisoformat(first["first_seen_at"])
    ends = timezone.datetime.fromisoformat(first["reading_ends_at"])
    assert ends - seen == timedelta(seconds=read)
    # Rechargement : la fin de la lecture ne bouge pas, le décompte reprend où il en était.
    again = user_client.post(url, content_type="application/json").json()["item"]
    assert again["reading_ends_at"] == first["reading_ends_at"]
    # Le résumé de l'épreuve annonce le temps de lecture aux consignes.
    state = user_client.get(reverse("api_attempt_state", args=[attempt.pk])).json()
    assert state["sections"][0]["read_before_seconds"] == 15


def test_forced_close_is_shifted_by_the_reading_time(formats, user):
    test = make_dev_test("rapide", rng=random.Random(3))
    fs = test.format.sections.get(section="CO")
    attempt = services.create_attempt(user, test)
    now = timezone.now()
    for test_item in co_items(test):
        answer = Answer(attempt=attempt, test_item=test_item, first_seen_at=now)
        read = services.co_read_seconds(test_item, fs)
        limit = services.co_item_limit(answer, fs)
        assert (
            limit - now
            == timedelta(seconds=read + services.co_item_seconds(test_item) + 12)
            + services.CO_ITEM_GRACE
        )


def test_published_tests_get_the_reading_time_without_regeneration(formats, user):
    """Le temps de lecture est géré par le lecteur : l'audio d'un test publié ne change pas."""
    test = make_dev_test("rapide", rng=random.Random(5))
    files = {t.item.audio.file.name for t in co_items(test)}
    attempt = services.create_attempt(user, test)
    section = services.start_section(attempt, 1)
    payload = payloads.section_session(section)
    assert "reading_ends_at" in payload["item"]
    assert {t.item.audio.file.name for t in co_items(test)} == files


def test_player_shows_the_reading_countdown(user_client, user, formats):
    test = make_dev_test("rapide", rng=random.Random(5))
    attempt = services.create_attempt(user, test)
    html = user_client.get(reverse("attempt_player", args=[attempt.pk])).content.decode()
    assert "L'enregistrement commence dans" in html
    assert "pour lire la question et les options" in html


# --- Épreuve seule : règles de refus --------------------------------------------------------------


def test_part_attempt_has_one_section_and_its_own_expiry(user, moyens):
    test = moyens[1]
    before = timezone.now()
    attempt = services.create_attempt(user, test, scope="MSL")
    assert attempt.scope == "MSL" and attempt.is_part
    assert list(attempt.sections.values_list("section", flat=True)) == ["MSL"]
    msl = test.format.sections.get(section="MSL")
    expected = before + timedelta(seconds=msl.duration_seconds, minutes=30)
    assert abs((attempt.expires_at - expected).total_seconds()) < 2


def test_the_four_refusal_rules(user, user_client, moyens):
    first, second = moyens
    # 1. Test complet refusé si une de ses épreuves est terminée.
    finish(services.create_attempt(user, second, scope="CO"))
    response = start(user_client, second)
    assert response.status_code == 409
    assert "passez les autres épreuves une par une" in response.content.decode()
    # 2. Épreuve refusée si elle est déjà terminée…
    response = start(user_client, second, "CO")
    assert response.status_code == 409
    assert "Vous avez déjà passé cette épreuve." in response.content.decode()
    # … ou si le test entier est terminé (3 et 4 : test complet déjà passé).
    finish(services.create_attempt(user, first))
    assert start(user_client, first).status_code == 409
    assert start(user_client, first, "CE").status_code == 409
    # Un abandon ne compte pas.
    abandoned = services.create_attempt(user, second, scope="MSL")
    services.abandon(abandoned)
    assert start(user_client, second, "MSL").status_code == 302
    assert Attempt.objects.filter(user=user, status="in_progress", scope="MSL").exists()


@pytest.mark.parametrize("mode", ["rapide", "reel"])
def test_part_attempts_refused_for_other_modes(user, user_client, formats, mode):
    test = make_dev_test(mode, rng=random.Random(8))
    assert start(user_client, test, "CO").status_code == 404
    assert start(user_client, test, "XX").status_code == 404
    assert not Attempt.objects.exists()
    html = user_client.get(reverse("mode_tests", args=[mode])).content.decode()
    assert 'name="scope"' not in html and "Test complet" not in html


def test_part_attempts_refused_when_the_admin_disables_them(user, user_client, moyens):
    moyens[0].format.__class__.objects.filter(mode="moyen").update(allow_part_attempts=False)
    assert start(user_client, moyens[0], "CO").status_code == 404


# --- Critère d'acceptation : test 2 épreuve par épreuve -------------------------------------------


def test_test_two_section_by_section(user, user_client, moyens):
    first, second = moyens
    # CO seule, puis MSL seule.
    response = start(user_client, second, "CO")
    co = Attempt.objects.get(user=user, status="in_progress")
    assert response["Location"] == reverse("attempt_player", args=[co.pk])
    finish(co)
    finish(services.create_attempt(user, second, scope="MSL"))
    # Le test complet 2 est refusé.
    assert start(user_client, second).status_code == 409
    assert eligibility.completed_by_mode(user)["moyen"] == 0
    # Après la CE seule, le test 2 est « passé » et compte pour le seuil des conseils.
    finish(services.create_attempt(user, second, scope="CE"))
    assert eligibility.completed_by_mode(user)["moyen"] == 1
    assert second.pk in services.completed_test_ids(user)
    html = user_client.get(reverse("mode_tests", args=["moyen"])).content.decode()
    row = row_of(html, "Entraînement 2")
    assert "Passé, épreuve par épreuve" in row
    assert row.count("Passée le") == 3
    # Avec le test 1 en entier, le seuil des entraînements moyens (2) est atteint.
    finish(services.create_attempt(user, first))
    assert eligibility.is_eligible(user)
    retained = {a.scope for a in eligibility.retained_attempts(user)}
    assert retained == {"full", "CO", "MSL", "CE"}


def test_part_results_show_the_section_score_without_global_score(user, user_client, moyens):
    attempt = finish(services.create_attempt(user, moyens[0], scope="CE"))
    assert attempt.global_score is None and attempt.level_low == ""
    section = attempt.sections.get()
    assert section.score is not None
    html = user_client.get(reverse("attempt_results", args=[attempt.pk])).content.decode()
    assert "Niveau global estimé" not in html and "pas de score global" in html
    assert "Compréhension écrite" in html and f"{section.score} / 699" in html
    review = user_client.get(reverse("attempt_review", args=[attempt.pk])).content.decode()
    assert 'id="CE"' in review and 'id="CO"' not in review
    # Historique : « Entraînement moyen, Entraînement 1 — compréhension écrite ».
    history = user_client.get(reverse("history")).content.decode()
    assert "Entraînement moyen, Entraînement 1 — compréhension écrite" in history
    # Le niveau global du tableau de bord ne vient que des passages complets.
    dashboard = user_client.get(reverse("dashboard")).content.decode()
    assert "Voir les résultats" not in dashboard
    finish(services.create_attempt(user, moyens[1]))
    dashboard = user_client.get(reverse("dashboard")).content.decode()
    assert "Voir les résultats" in dashboard


# --- Liste du mode et carte -----------------------------------------------------------------------


def test_mode_list_shows_full_button_then_one_line_per_section(user, user_client, moyens):
    first, second = moyens
    html = user_client.get(reverse("mode_tests", args=["moyen"])).content.decode()
    assert html.count(">Test complet<") == 2
    assert html.count('name="scope"') == 6  # trois épreuves par test
    finish(services.create_attempt(user, first, scope="CO"))
    html = user_client.get(reverse("mode_tests", args=["moyen"])).content.decode()
    row = row_of(html, "Entraînement 1")
    assert ">Test complet<" not in row  # une épreuve est faite
    assert 'data-part="CO" data-part-state="passed"' in row
    co = Attempt.objects.get(user=user, scope="CO")
    assert reverse("attempt_results", args=[co.pk]) in row
    assert reverse("attempt_review", args=[co.pk]) in row
    assert row.count('name="scope"') == 2  # MSL et CE restent à passer
    # « prochain » : le test 2, le seul encore lançable en entier.
    assert "prochain" in row_of(html, "Entraînement 2")
    # Épreuve en cours : « Reprendre » sur sa ligne.
    current = services.create_attempt(user, first, scope="MSL")
    html = user_client.get(reverse("mode_tests", args=["moyen"])).content.decode()
    assert 'data-part="MSL" data-part-state="in_progress"' in html
    assert reverse("attempt_player", args=[current.pk]) in html


def test_card_launches_an_untouched_test_then_points_to_the_list(user, user_client, moyens):
    first, second = moyens
    finish(services.create_attempt(user, first, scope="CO"))
    user_client.post(reverse("attempt_create"), {"mode": "moyen"})
    attempt = Attempt.objects.get(user=user, status="in_progress")
    assert attempt.test == second and attempt.scope == "full"  # le test 1 est entamé
    services.abandon(attempt)
    finish(services.create_attempt(user, second, scope="MSL"))
    html = user_client.get(reverse("dashboard")).content.decode()
    assert "Voir les épreuves à passer" in html
    assert "0 passé · 2 à passer" in html
    response = user_client.post(reverse("attempt_create"), {"mode": "moyen"})
    assert response.status_code == 409


def test_list_buttons_are_44_px_targets(user_client, moyens):
    """Mêmes classes que les autres boutons (contrôlés à 44 px dans le test du navigateur)."""
    html = user_client.get(reverse("mode_tests", args=["moyen"])).content.decode()
    assert 'class="btn btn-secondary w-full sm:w-auto"' in html


def test_archived_test_keeps_its_section_results(user, user_client, moyens):
    from bank.review import archive

    first = moyens[0]
    co = finish(services.create_attempt(user, first, scope="CO"))
    archive(first)
    html = user_client.get(reverse("mode_tests", args=["moyen"])).content.decode()
    row = row_of(html, "Entraînement 1")
    assert reverse("attempt_results", args=[co.pk]) in row
    assert 'name="scope"' not in row  # test archivé : plus rien à lancer
    assert start(user_client, Test.objects.get(pk=first.pk), "MSL").status_code == 404
