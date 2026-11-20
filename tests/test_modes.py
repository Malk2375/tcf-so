"""Noms des modes (SPEC 4.1), cartes du tableau de bord (10) et liste des tests d'un mode
(8.1 bis) ; résultats et correction toujours consultables (8.1)."""

import importlib
import random
import re
from datetime import timedelta
from pathlib import Path

import pytest
from django.apps import apps
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from advice.eligibility import remaining_message
from bank import audio
from bank.devtest import make_dev_test
from bank.formats import seed_formats
from bank.models import ExamFormat, Test
from bank.modes import mode_names
from bank.review import archive
from exam import services
from exam.models import Attempt
from tests.conftest import PASSWORD
from tests.test_advice import complete

pytestmark = pytest.mark.django_db

ROOT = Path(__file__).resolve().parent.parent
FORBIDDEN = re.compile(r"\btests? (rapides?|moyens?)\b", re.IGNORECASE)


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


# --- Noms -----------------------------------------------------------------------------------------


def test_one_function_gives_name_plural_and_article():
    rapide, reel = mode_names("rapide"), mode_names("reel")
    assert (rapide.name, rapide.count(1), rapide.count(2)) == (
        "Entraînement rapide",
        "1 entraînement rapide",
        "2 entraînements rapides",
    )
    assert mode_names("moyen").plural == "entraînements moyens"
    assert (reel.name, reel.count(2), reel.the) == (
        "Test en conditions réelles",
        "2 tests en conditions réelles",
        "le test en conditions réelles",
    )
    assert rapide.already_done == "Vous avez déjà passé cet entraînement."
    assert reel.already_done == "Vous avez déjà passé ce test."
    assert rapide.all_done(3) == (
        "Vous avez passé les 3 entraînements rapides disponibles. "
        "De nouveaux entraînements seront ajoutés."
    )
    assert (rapide.see_all, reel.see_all) == ("Voir les entraînements", "Voir les tests")


def test_seed_and_migration_rename_the_formats(formats):
    assert {f.mode: f.label for f in ExamFormat.objects.all()} == {
        "rapide": "Entraînement rapide",
        "moyen": "Entraînement moyen",
        "reel": "Test en conditions réelles",
    }
    ExamFormat.objects.update(label="Mode rapide")
    migration = importlib.import_module("bank.migrations.0008_mode_names")
    migration.rename(apps, None)
    assert ExamFormat.objects.get(mode="reel").label == "Test en conditions réelles"
    seed_formats()  # idempotent
    assert ExamFormat.objects.get(mode="moyen").label == "Entraînement moyen"


def test_public_labels_follow_the_mode(formats):
    assert make_dev_test("rapide", rng=random.Random(1)).public_label == "Entraînement 1"
    assert make_dev_test("reel", rng=random.Random(2)).public_label == "Test 1"


def user_facing_files():
    """Gabarits et code dont les textes s'affichent aux utilisateurs (le back-office et l'admin
    gardent leur vocabulaire)."""
    templates = [p for p in (ROOT / "templates").rglob("*.html") if "backoffice" not in p.parts]
    code = [
        p
        for app in ("accounts", "advice", "exam", "config", "usage")
        for p in (ROOT / app).rglob("*.py")
        if "migrations" not in p.parts
    ]
    data = list((ROOT / "advice" / "fallbacks").glob("*.json"))
    return templates + code + data + [ROOT / "bank" / "modes.py"]


def test_no_user_facing_text_says_test_rapide_or_test_moyen():
    offenders = [
        f"{path.relative_to(ROOT)} : {match.group(0)}"
        for path in user_facing_files()
        for match in FORBIDDEN.finditer(path.read_text(encoding="utf-8"))
    ]
    assert offenders == []


def test_rendered_pages_and_messages_use_the_new_names(user_client, user, formats):
    tests = [make_dev_test("rapide", rng=random.Random(i)) for i in range(3)]
    make_dev_test("moyen", rng=random.Random(9))
    done = complete(user, tests[0])
    pages = [
        reverse("dashboard"),
        reverse("mode_tests", args=["rapide"]),
        reverse("mode_tests", args=["reel"]),
        reverse("history"),
        reverse("advice"),
        reverse("attempt_results", args=[done.pk]),
        reverse("attempt_review", args=[done.pk]),
    ]
    for page in pages:
        html = user_client.get(page).content.decode()
        assert not FORBIDDEN.search(html), page
    assert "Entraînement rapide" in user_client.get(reverse("dashboard")).content.decode()
    assert not FORBIDDEN.search(remaining_message(user))
    for test in tests[1:]:
        complete(user, test)
    refused = user_client.post(reverse("attempt_create"), {"mode": "rapide"})
    assert refused.status_code == 409
    assert "Vous avez déjà passé cet entraînement." in refused.content.decode()


# --- Cartes du tableau de bord -----------------------------------------------------------------


def test_card_shows_name_and_duration_together_and_counts(user_client, user, formats):
    first = make_dev_test("rapide", rng=random.Random(1))
    make_dev_test("rapide", rng=random.Random(2))
    complete(user, first)
    html = user_client.get(reverse("dashboard")).content.decode()
    # Même élément, donc même taille de police ; durées avec la lecture en CO (28, 52, 103 min).
    assert re.search(r"<h3[^>]*>Entraînement rapide · 28 min</h3>", html)
    assert re.search(r"<h3[^>]*>Entraînement moyen · 52 min</h3>", html)
    assert re.search(r"<h3[^>]*>Test en conditions réelles · 103 min</h3>", html)
    assert "1 passé · 1 à passer" in html
    assert reverse("mode_tests", args=["rapide"]) in html and "Voir les entraînements" in html


def test_card_offers_resume_for_the_mode_in_progress(user_client, user, formats):
    test = make_dev_test("rapide", rng=random.Random(1))
    make_dev_test("moyen", rng=random.Random(2))
    attempt = services.create_attempt(user, test)
    html = user_client.get(reverse("dashboard")).content.decode()
    assert reverse("attempt_player", args=[attempt.pk]) in html
    assert "Un autre passage est en cours." in html  # carte du mode moyen


# --- Liste des tests d'un mode -----------------------------------------------------------------


def finished(user, test, days, status=Attempt.Status.COMPLETED, score=420):
    now = timezone.now() - timedelta(days=days)
    return Attempt.objects.create(
        user=user,
        test=test,
        status=status,
        started_at=now,
        finished_at=now,
        expires_at=now,
        global_score=score,
        global_level="B2",
        level_low="B2",
        level_high="B2",
    )


@pytest.fixture
def four_tests(formats):
    """Quatre tests publiés ; numéros publics dans un autre ordre que les numéros internes."""
    tests = [make_dev_test("rapide", rng=random.Random(i)) for i in range(4)]
    for test, public in zip(tests, (3, 1, 4, 2), strict=True):
        Test.objects.filter(pk=test.pk).update(public_number=public + 10)
    for test, public in zip(tests, (3, 1, 4, 2), strict=True):
        Test.objects.filter(pk=test.pk).update(public_number=public)
    return {t.public_number: t for t in Test.objects.all()}


def rows_of(html):
    return re.findall(
        r'data-state="(\w+)"[^>]*>.*?<p class="font-medium">\s*([^<]+?)\s*<', html, re.S
    )


def test_list_in_public_number_order_with_states(user_client, user, four_tests):
    by_public = four_tests
    passed = finished(user, by_public[2], days=3, score=432)
    finished(user, by_public[2], days=1, score=600)  # doublon ancien : le premier compte
    finished(user, by_public[1], days=2, status=Attempt.Status.ABANDONED)
    current = services.create_attempt(user, by_public[1])
    html = user_client.get(reverse("mode_tests", args=["rapide"])).content.decode()
    assert rows_of(html) == [
        ("in_progress", "Entraînement 1"),
        ("passed", "Entraînement 2"),
        ("to_pass", "Entraînement 3"),
        ("to_pass", "Entraînement 4"),
    ]
    assert "prochain" in html.split("Entraînement 3")[1].split("</li>")[0]
    assert "prochain" not in html.split("Entraînement 4")[1].split("</li>")[0]
    line = html.split("Entraînement 2")[1].split("</li>")[0]
    assert "Passé le " in line
    assert "432 / 699" in line and "B2" in line and "600" not in line
    assert reverse("attempt_results", args=[passed.pk]) in line
    assert reverse("attempt_review", args=[passed.pk]) in line
    assert reverse("attempt_player", args=[current.pk]) in html
    assert ">Reprendre<" in html


def test_start_always_launches_the_smallest_public_number_not_passed(user_client, user, four_tests):
    finished(user, four_tests[1], days=2)
    finished(user, four_tests[3], days=1)
    finished(user, four_tests[2], days=3, status=Attempt.Status.ABANDONED)
    user_client.post(reverse("attempt_create"), {"mode": "rapide"})
    attempt = Attempt.objects.get(user=user, status=Attempt.Status.IN_PROGRESS)
    assert attempt.test == four_tests[2]  # abandonné : toujours à passer, et le plus petit
    html = user_client.get(reverse("mode_tests", args=["rapide"])).content.decode()
    assert ">Reprendre<" in html


def start_buttons(html):
    """Identifiants des tests que la liste propose de lancer, dans l'ordre."""
    return [int(pk) for pk in re.findall(r'name="test" value="(\d+)"', html)]


def test_each_test_to_pass_has_its_own_start_button(user_client, user, formats):
    tests = [make_dev_test("rapide", rng=random.Random(i)) for i in range(3)]
    html = user_client.get(reverse("mode_tests", args=["rapide"])).content.decode()
    assert start_buttons(html) == [t.pk for t in tests]
    assert 'name="mode"' not in html  # plus de bouton global en tête de la liste
    response = user_client.post(reverse("attempt_create"), {"test": tests[2].pk})
    attempt = Attempt.objects.get(user=user, status=Attempt.Status.IN_PROGRESS)
    assert attempt.test == tests[2]  # Entraînement 3, pas le prochain
    assert response["Location"] == reverse("attempt_player", args=[attempt.pk])
    # La carte du tableau de bord garde son bouton, qui lance le prochain test.
    services.abandon(attempt)
    dashboard = user_client.get(reverse("dashboard")).content.decode()
    assert 'name="mode" value="rapide"' in dashboard
    user_client.post(reverse("attempt_create"), {"mode": "rapide"})
    current = Attempt.objects.get(user=user, status=Attempt.Status.IN_PROGRESS)
    assert current.test == tests[0]


def test_passed_and_archived_tests_have_no_start_button(user_client, user, formats):
    passed, archived_done, to_pass, archived = (
        make_dev_test("rapide", rng=random.Random(i)) for i in range(4)
    )
    finished(user, passed, days=2)
    finished(user, archived_done, days=1)
    archive(archived_done)
    archive(archived)
    html = user_client.get(reverse("mode_tests", args=["rapide"])).content.decode()
    assert start_buttons(html) == [to_pass.pk]
    assert [state for state, _ in rows_of(html)] == ["passed", "passed", "to_pass"]


@pytest.mark.parametrize("staff", [False, True])
def test_forged_requests_are_refused(client, formats, staff):
    user = get_user_model().objects.create_user("x", "x@example.com", PASSWORD, is_staff=staff)
    client.force_login(user)
    passed, archived = (make_dev_test("rapide", rng=random.Random(i)) for i in range(2))
    finished(user, passed, days=1)
    archive(archived)
    draft = Test.objects.create(format=formats["rapide"], number=99)
    response = client.post(reverse("attempt_create"), {"test": passed.pk})
    assert response.status_code == 409
    assert "Vous avez déjà passé cet entraînement." in response.content.decode()
    for value in (archived.pk, draft.pk, 999999, "abc", "-1"):
        assert client.post(reverse("attempt_create"), {"test": value}).status_code == 404, value
    assert not Attempt.objects.filter(status=Attempt.Status.IN_PROGRESS).exists()


def test_start_while_another_pass_is_in_progress(user_client, user, formats):
    first, second = (make_dev_test("rapide", rng=random.Random(i)) for i in range(2))
    reel = make_dev_test("reel", rng=random.Random(5))
    current = services.create_attempt(user, reel)
    html = user_client.get(reverse("mode_tests", args=["rapide"])).content.decode()
    assert start_buttons(html) == [first.pk, second.pk]
    assert "Un autre passage est en cours" in html
    response = user_client.post(reverse("attempt_create"), {"test": second.pk}, follow=True)
    assert response.redirect_chain[-1][0] == reverse("dashboard")
    page = response.content.decode()
    assert "reprenez-le ou abandonnez-le" in page
    assert reverse("attempt_player", args=[current.pk]) in page
    assert reverse("attempt_abandon", args=[current.pk]) in page
    assert list(Attempt.objects.filter(user=user)) == [current]


def test_passed_test_stays_viewable_after_archiving(user_client, user, formats):
    test = make_dev_test("rapide", rng=random.Random(5))
    make_dev_test("rapide", rng=random.Random(6))
    done = complete(user, test)
    archive(test)
    html = user_client.get(reverse("mode_tests", args=["rapide"])).content.decode()
    assert rows_of(html)[0] == ("passed", "Entraînement 1")
    for name in ("attempt_results", "attempt_review"):
        response = user_client.get(reverse(name, args=[done.pk]))
        assert response.status_code == 200, name
    # Un autre compte n'y a pas accès.
    other = get_user_model().objects.create_user("autre", "autre@example.com", PASSWORD)
    user_client.force_login(other)
    assert user_client.get(reverse("attempt_results", args=[done.pk])).status_code == 404


def test_all_done_message_and_reel_wording(user_client, user, formats):
    test = make_dev_test("reel", rng=random.Random(7))
    finished(user, test, days=1)
    html = user_client.get(reverse("mode_tests", args=["reel"])).content.decode()
    assert "Test en conditions réelles · 103 min" in html
    assert "Vous avez passé le seul test en conditions réelles disponible." in html
    assert "Commencer" not in html and start_buttons(html) == []
    assert "Test 1" in html


def test_unknown_mode_is_a_404(user_client, formats):
    assert user_client.get(reverse("mode_tests", args=["inconnu"])).status_code == 404


def test_mode_list_requires_login(client, formats):
    response = client.get(reverse("mode_tests", args=["rapide"]))
    assert response.status_code == 302
