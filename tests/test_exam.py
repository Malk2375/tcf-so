"""Passage de test : règles serveur, points d'entrée JSON, pages (SPEC 8, 9, 10)."""

import json
import random
import re
from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from bank import audio
from bank.devtest import make_dev_test
from bank.models import Test, TestItem
from exam import services
from exam.models import Answer, Attempt, AttemptSection
from tests.conftest import PASSWORD

pytestmark = pytest.mark.django_db

FORBIDDEN_KEYS = {"correct_index", "is_correct", "explanation"}


@pytest.fixture(autouse=True)
def _fast_audio(monkeypatch):
    """Audio factice sans ffmpeg (test_dev_test vérifie le vrai fichier)."""
    monkeypatch.setattr(audio, "placeholder_mp3", lambda seconds: f"mp3 {seconds:.2f}".encode())


@pytest.fixture
def published(formats):
    return make_dev_test("rapide", rng=random.Random(11))


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user(
        username="candidat", email="candidat@example.com", password=PASSWORD
    )


@pytest.fixture
def user_client(client, user):
    client.force_login(user)
    return client


@pytest.fixture
def attempt(user, published):
    return services.create_attempt(user, published)


@pytest.fixture
def frozen(time_machine):
    """Horloge arrêtée : le temps n'avance que lorsqu'on le décale."""
    time_machine.move_to(timezone.now(), tick=False)
    return time_machine


def api(client, attempt, name, *args, body=None):
    url = reverse(name, args=[attempt.pk, *args])
    if name == "api_attempt_state":
        response = client.get(url)
    else:
        response = client.post(url, json.dumps(body or {}), content_type="application/json")
    return response, response.json()


def post_answer(client, attempt, test_item_id, selected_index, **extra):
    body = {"test_item_id": test_item_id, "selected_index": selected_index, **extra}
    return api(client, attempt, "api_answers", body=body)


def keys(data):
    """Toutes les clés d'un document JSON, à toute profondeur."""
    if isinstance(data, dict):
        return set(data) | {k for value in data.values() for k in keys(value)}
    if isinstance(data, list):
        return {k for value in data for k in keys(value)}
    return set()


def section_items(test, section):
    return list(
        TestItem.objects.filter(test=test, section=section)
        .select_related("item")
        .order_by("position")
    )


def wrong_index(test_item):
    return (test_item.item.correct_index + 1) % 4


def play_co(client, attempt, answer=lambda t: t.item.correct_index):
    """Passe toute l'épreuve de CO par l'API ; renvoie les réponses JSON reçues."""
    responses = []
    response, data = api(client, attempt, "api_section_start", 1)
    responses.append(data)
    while not data["section_done"]:
        item = TestItem.objects.select_related("item").get(pk=data["item"]["test_item_id"])
        responses.append(api(client, attempt, "api_co_audio_started")[1])
        responses.append(post_answer(client, attempt, item.pk, answer(item))[1])
        response, data = api(client, attempt, "api_co_next", body={"test_item_id": item.pk})
        assert response.status_code == 200
        responses.append(data)
    return responses


def play_qcm(client, attempt, order, section, answer=lambda t: t.item.correct_index):
    responses = [api(client, attempt, "api_section_start", order)[1]]
    for test_item in section_items(attempt.test, section):
        response, data = post_answer(client, attempt, test_item.pk, answer(test_item))
        assert response.status_code == 200
        responses.append(data)
    responses.append(api(client, attempt, "api_section_finish", order)[1])
    return responses


def play_all(client, attempt, answer=lambda t: t.item.correct_index):
    responses = [api(client, attempt, "api_attempt_state")[1]]
    responses += play_co(client, attempt, answer)
    responses += play_qcm(client, attempt, 2, "MSL", answer)
    responses += play_qcm(client, attempt, 3, "CE", answer)
    return responses


# --- Création et choix du test (8.1, 8.2) --------------------------------------------------------


def test_create_attempt_sets_sections_and_expiry(attempt, published):
    sections = list(attempt.sections.order_by("order"))
    assert [(s.section, s.status) for s in sections] == [
        ("CO", "pending"),
        ("MSL", "pending"),
        ("CE", "pending"),
    ]
    co = services.co_duration_seconds(published, 12)
    total = co + 300 + 780
    expected = attempt.started_at + timedelta(seconds=total, minutes=30)
    assert abs((attempt.expires_at - expected).total_seconds()) < 1


def test_dashboard_starts_oldest_unfinished_test(user_client, user, published, formats):
    newer = make_dev_test("rapide", rng=random.Random(12))
    response = user_client.post(reverse("attempt_create"), {"mode": "rapide"})
    attempt = Attempt.objects.get(user=user)
    assert response.url == reverse("attempt_player", args=[attempt.pk])
    assert attempt.test == published
    assert newer.number > published.number


def completed(user, test, days=1, status=Attempt.Status.COMPLETED):
    now = timezone.now()
    return Attempt.objects.create(
        user=user,
        test=test,
        status=status,
        started_at=now - timedelta(days=days),
        finished_at=now - timedelta(days=days),
        expires_at=now,
    )


# --- Un test ne se passe qu'une fois (8.1) -------------------------------------------------------


def test_completed_test_is_no_longer_proposed(user, published, formats):
    second = make_dev_test("rapide", rng=random.Random(12))
    completed(user, published)
    assert services.proposed_test(user, formats["rapide"]) == second
    completed(user, second)
    assert services.proposed_test(user, formats["rapide"]) is None


def test_abandoned_test_is_still_proposed(user, published, formats):
    completed(user, published, status=Attempt.Status.ABANDONED)
    assert services.proposed_test(user, formats["rapide"]) == published
    assert services.create_attempt(user, published).test == published


def test_dashboard_card_when_every_test_is_passed(user_client, user, published, formats):
    second = make_dev_test("rapide", rng=random.Random(12))
    third = make_dev_test("rapide", rng=random.Random(13))
    html = user_client.get(reverse("dashboard")).content.decode()
    assert "3 tests disponibles · 3 à passer" in html
    for test in (published, second, third):
        completed(user, test)
    html = user_client.get(reverse("dashboard")).content.decode()
    assert (
        "Vous avez passé les 3 tests rapides disponibles. De nouveaux tests seront ajoutés." in html
    )
    assert "Commencer" not in html  # seul le mode rapide a des tests


def test_single_test_card_wording(user_client, user, published):
    completed(user, published)
    html = user_client.get(reverse("dashboard")).content.decode()
    assert "Vous avez passé le seul test rapide disponible." in html


@pytest.mark.parametrize("staff", [False, True])
def test_post_attempts_refuses_a_completed_test(client, published, formats, staff):
    """Requête forgée : le formulaire n'est plus affiché, mais le POST est refusé (409), pour
    tous les comptes, administrateurs compris."""
    user = get_user_model().objects.create_user("x", "x@example.com", PASSWORD, is_staff=staff)
    completed(user, published)
    client.force_login(user)
    response = client.post(reverse("attempt_create"), {"mode": "rapide"})
    assert response.status_code == 409
    assert "Vous avez déjà passé ce test." in response.content.decode()
    assert Attempt.objects.filter(user=user).count() == 1
    with pytest.raises(services.AttemptError) as error:
        services.create_attempt(user, published)
    assert error.value.code == "test_already_completed"


def test_deleting_the_attempt_in_admin_allows_a_new_pass(client, published, formats):
    admin = get_user_model().objects.create_superuser("adm", "adm@example.com", PASSWORD)
    user = get_user_model().objects.create_user("y", "y@example.com", PASSWORD)
    attempt = completed(user, published)
    client.force_login(admin)
    page = client.get(reverse("admin:exam_attempt_changelist")).content.decode()
    assert ">y<" in page
    assert client.get(reverse("admin:exam_attempt_change", args=[attempt.pk])).status_code == 200
    response = client.post(reverse("admin:exam_attempt_delete", args=[attempt.pk]), {"post": "yes"})
    assert response.status_code == 302
    assert not Attempt.objects.exists()
    assert services.proposed_test(user, formats["rapide"]) == published


def test_only_one_attempt_in_progress(user_client, user, attempt):
    response = user_client.post(reverse("attempt_create"), {"mode": "rapide"}, follow=True)
    assert "Un passage est déjà en cours" in response.content.decode()
    assert Attempt.objects.filter(user=user).count() == 1
    html = user_client.get(reverse("dashboard")).content.decode()
    assert "Passage en cours" in html
    assert reverse("attempt_player", args=[attempt.pk]) in html


def test_no_published_test(user_client, formats):
    response = user_client.post(reverse("attempt_create"), {"mode": "reel"}, follow=True)
    assert "Aucun test n&#x27;est disponible" in response.content.decode()
    assert not Test.objects.exists()


# --- Confidentialité de la bonne réponse ---------------------------------------------------------


def test_no_correct_answer_leaks_before_the_end(user_client, attempt):
    explanations = [t.item.explanation for t in TestItem.objects.filter(test=attempt.test)]
    player = user_client.get(reverse("attempt_player", args=[attempt.pk])).content.decode()
    for text in explanations:
        assert text not in player

    responses = play_all(user_client, attempt)
    # Toutes les réponses JSON avant la fin, y compris la dernière clôture.
    for data in responses:
        assert not keys(data) & FORBIDDEN_KEYS, data
        raw = json.dumps(data, ensure_ascii=False)
        for text in explanations:
            assert text not in raw
    attempt.refresh_from_db()
    assert attempt.status == Attempt.Status.COMPLETED


def test_audio_letters_items_do_not_send_options(user_client, user, published):
    co_first = section_items(published, "CO")[0].item
    co_first.options_mode = co_first.OptionsMode.AUDIO_LETTERS
    co_first.save()
    attempt = services.create_attempt(user, published)
    _, data = api(user_client, attempt, "api_section_start", 1)
    assert data["item"]["options"] is None
    assert co_first.options[0] not in json.dumps(data, ensure_ascii=False)


# --- Accès ---------------------------------------------------------------------------------------


def test_other_users_cannot_touch_an_attempt(attempt, published):
    other = get_user_model().objects.create_user(
        username="autre", email="autre@example.com", password=PASSWORD
    )
    client = Client()
    client.force_login(other)
    state_url = reverse("api_attempt_state", args=[attempt.pk])
    assert client.get(state_url).status_code == 404
    start_url = reverse("api_section_start", args=[attempt.pk, 1])
    assert client.post(start_url, "{}", content_type="application/json").status_code == 404
    assert client.get(reverse("attempt_player", args=[attempt.pk])).status_code == 404
    assert Client().get(state_url).status_code == 401


def test_api_requires_csrf_token(user, attempt):
    client = Client(enforce_csrf_checks=True)
    client.force_login(user)
    url = reverse("api_section_start", args=[attempt.pk, 1])
    assert client.post(url, "{}", content_type="application/json").status_code == 403
    client.get(reverse("attempt_player", args=[attempt.pk]))  # pose le cookie csrftoken
    token = client.cookies["csrftoken"].value
    response = client.post(
        url, "{}", content_type="application/json", headers={"X-CSRFToken": token}
    )
    assert response.status_code == 200


# --- Compréhension orale -------------------------------------------------------------------------


def test_co_only_accepts_current_item_and_never_goes_back(user_client, attempt):
    co = section_items(attempt.test, "CO")
    _, data = api(user_client, attempt, "api_section_start", 1)
    assert data["item"]["test_item_id"] == co[0].pk
    assert "next_audio_url" in data["item"]

    response, error = post_answer(user_client, attempt, co[1].pk, 0)
    assert response.status_code == 409
    assert error["error"] == "not_current"

    post_answer(user_client, attempt, co[0].pk, 2)
    _, data = api(user_client, attempt, "api_co_next", body={"test_item_id": co[0].pk})
    assert data["item"]["test_item_id"] == co[1].pk

    # Retour impossible : l'ancien item refuse toute réponse…
    response, error = post_answer(user_client, attempt, co[0].pk, 3)
    assert response.status_code == 409
    assert Answer.objects.get(attempt=attempt, test_item=co[0]).selected_index == 2
    # … et un co/next répété pour l'ancien item ne fait pas avancer deux fois.
    _, data = api(user_client, attempt, "api_co_next", body={"test_item_id": co[0].pk})
    assert data["item"]["test_item_id"] == co[1].pk


def test_co_audio_can_restart_only_once(user_client, attempt):
    api(user_client, attempt, "api_section_start", 1)
    _, data = api(user_client, attempt, "api_co_audio_started")
    assert data["audio_starts"] == 1
    _, session = api(user_client, attempt, "api_section_start", 1)  # rechargement
    assert session["item"]["can_play_audio"] is True
    api(user_client, attempt, "api_co_audio_started")
    _, session = api(user_client, attempt, "api_section_start", 1)
    assert session["item"]["audio_starts"] == 2
    assert session["item"]["can_play_audio"] is False


def test_co_item_left_open_is_closed_lazily(user_client, attempt, frozen):
    co = section_items(attempt.test, "CO")
    _, data = api(user_client, attempt, "api_section_start", 1)
    first_limit = services.co_item_seconds(co[0]) + 12 + 30

    frozen.shift(timedelta(seconds=first_limit - 1))
    _, state = api(user_client, attempt, "api_attempt_state")
    assert AttemptSection.objects.get(attempt=attempt, order=1).current_position == 1

    frozen.shift(timedelta(seconds=2))
    response, _ = post_answer(user_client, attempt, co[0].pk, 1)
    assert response.status_code == 409  # l'item a été clos d'office
    assert AttemptSection.objects.get(attempt=attempt, order=1).current_position == 2
    assert Answer.objects.get(attempt=attempt, test_item=co[0]).selected_index is None


def test_co_section_runs_out_while_user_is_away(user_client, attempt, frozen):
    api(user_client, attempt, "api_section_start", 1)
    co_total = services.co_duration_seconds(attempt.test, 12) + 9 * 30
    frozen.shift(timedelta(seconds=co_total + 1))
    _, state = api(user_client, attempt, "api_attempt_state")
    assert [s["status"] for s in state["sections"]] == ["done", "pending", "pending"]
    assert Answer.objects.filter(attempt=attempt, test_item__section="CO").count() == 9


def test_co_cannot_be_finished_by_hand(user_client, attempt):
    api(user_client, attempt, "api_section_start", 1)
    response, data = api(user_client, attempt, "api_section_finish", 1)
    assert response.status_code == 409


# --- MSL et CE -----------------------------------------------------------------------------------


def start_msl(client, attempt):
    play_co(client, attempt)
    return api(client, attempt, "api_section_start", 2)[1]


def test_sections_must_follow_the_order(user_client, attempt):
    response, data = api(user_client, attempt, "api_section_start", 2)
    assert response.status_code == 409
    assert data["error"] == "section_locked"


def test_msl_answer_refused_after_deadline(user_client, attempt, frozen):
    session = start_msl(user_client, attempt)
    msl = section_items(attempt.test, "MSL")
    assert len(session["items"]) == 6
    frozen.shift(timedelta(seconds=300 + 2))  # échéance + 2 s : encore accepté
    assert post_answer(user_client, attempt, msl[0].pk, 1)[0].status_code == 200

    frozen.shift(timedelta(seconds=2))  # échéance + 4 s : refusé, épreuve close
    response, data = post_answer(user_client, attempt, msl[1].pk, 1)
    assert response.status_code == 409
    assert data["error"] == "section_closed"
    section = AttemptSection.objects.get(attempt=attempt, order=2)
    assert section.status == AttemptSection.Status.DONE
    assert section.finished_at == section.deadline_at + services.ANSWER_GRACE
    assert Answer.objects.get(attempt=attempt, test_item=msl[1]).selected_index is None


def test_resume_after_reload(user_client, attempt):
    session = start_msl(user_client, attempt)
    msl = section_items(attempt.test, "MSL")
    post_answer(user_client, attempt, msl[2].pk, 3, flagged=True, time_spent_ms=500)

    _, state = api(user_client, attempt, "api_attempt_state")
    assert [s["status"] for s in state["sections"]] == ["done", "in_progress", "pending"]
    assert state["sections"][1]["n_answered"] == 1
    _, again = api(user_client, attempt, "api_section_start", 2)
    assert again["section"]["deadline_at"] == session["section"]["deadline_at"]
    restored = {i["test_item_id"]: i for i in again["items"]}[msl[2].pk]
    assert (restored["selected_index"], restored["flagged"]) == (3, True)


def test_change_count_and_time_cap(user_client, attempt, frozen):
    start_msl(user_client, attempt)
    item = section_items(attempt.test, "MSL")[0]
    frozen.shift(timedelta(seconds=20))
    post_answer(user_client, attempt, item.pk, 0, time_spent_ms=999_999)
    post_answer(user_client, attempt, item.pk, 0)
    post_answer(user_client, attempt, item.pk, 2)
    post_answer(user_client, attempt, item.pk, 1)
    answer = Answer.objects.get(attempt=attempt, test_item=item)
    assert answer.change_count == 2
    assert answer.time_spent_ms == 20_000  # plafonné au temps écoulé depuis la première apparition


def test_choice_history_records_each_choice_in_order(user_client, attempt, frozen):
    start_msl(user_client, attempt)
    item = section_items(attempt.test, "MSL")[0]
    right, wrong = item.item.correct_index, wrong_index(item)
    t0 = timezone.now()
    post_answer(user_client, attempt, item.pk, right)
    frozen.shift(timedelta(seconds=5))
    post_answer(user_client, attempt, item.pk, right, flagged=True)  # même choix : rien de neuf
    post_answer(user_client, attempt, item.pk, wrong)
    post_answer(user_client, attempt, item.pk, None)  # pas de choix : rien de neuf
    answer = Answer.objects.get(attempt=attempt, test_item=item)
    assert [c["index"] for c in answer.choice_history] == [right, wrong]
    assert answer.choice_history[0]["at"] == t0.isoformat()
    assert answer.choice_history[1]["at"] == (t0 + timedelta(seconds=5)).isoformat()
    assert answer.change_count == len(answer.choice_history) - 1
    # Un changement d'une bonne réponse vers une mauvaise se lit dans l'historique (SPEC 12.2).
    assert answer.choice_history[0]["index"] == right and answer.selected_index == wrong


def test_choice_history_is_never_sent_to_the_browser(user_client, attempt):
    start_msl(user_client, attempt)
    item = section_items(attempt.test, "MSL")[0]
    _, data = post_answer(user_client, attempt, item.pk, 1)
    _, again = api(user_client, attempt, "api_section_start", 2)
    assert "choice_history" not in keys(data) | keys(again)


def test_invalid_answer_payloads(user_client, attempt):
    start_msl(user_client, attempt)
    item = section_items(attempt.test, "MSL")[0]
    assert post_answer(user_client, attempt, item.pk, 4)[0].status_code == 409
    assert post_answer(user_client, attempt, item.pk, "1")[0].status_code == 409
    assert post_answer(user_client, attempt, 999_999, 1)[0].status_code == 409


# --- Abandon et expiration -----------------------------------------------------------------------


def test_abandon(user_client, user, attempt):
    _, state = api(user_client, attempt, "api_abandon")
    assert state["attempt_status"] == "abandoned"
    assert state["redirect"] == reverse("dashboard")
    response, data = api(user_client, attempt, "api_section_start", 1)
    assert response.status_code == 409
    # Un nouveau passage devient possible.
    user_client.post(reverse("attempt_create"), {"mode": "rapide"})
    assert Attempt.objects.filter(user=user, status="in_progress").count() == 1


def test_abandon_from_dashboard(user_client, attempt):
    user_client.post(reverse("attempt_abandon", args=[attempt.pk]))
    attempt.refresh_from_db()
    assert attempt.status == Attempt.Status.ABANDONED


def test_expired_attempt_becomes_abandoned(user_client, attempt, frozen):
    api(user_client, attempt, "api_section_start", 1)
    frozen.move_to(attempt.expires_at + timedelta(seconds=1), tick=False)
    _, state = api(user_client, attempt, "api_attempt_state")
    assert state["attempt_status"] == "abandoned"
    assert state["redirect"] == reverse("dashboard")
    response = user_client.get(reverse("attempt_player", args=[attempt.pk]))
    assert response.url == reverse("dashboard")


# --- Fin du passage, résultats, correction, historique -------------------------------------------


def test_full_attempt_all_right(user_client, attempt):
    play_all(user_client, attempt)
    attempt.refresh_from_db()
    assert attempt.status == Attempt.Status.COMPLETED
    assert attempt.global_score == 699
    assert (attempt.global_level, attempt.level_low, attempt.level_high) == ("C2", "C2", "C2")
    assert [s.score for s in attempt.sections.all()] == [699, 699, 699]
    assert Answer.objects.filter(attempt=attempt, is_correct=True).count() == 24


def test_full_attempt_scores_by_level(user_client, attempt):
    # Juste seulement en A1 et A2.
    def answer(test_item):
        ok = test_item.item.level in {"A1", "A2"}
        return test_item.item.correct_index if ok else wrong_index(test_item)

    play_all(user_client, attempt, answer)
    attempt.refresh_from_db()
    scores = {s.section: (s.n_correct, s.score, s.level) for s in attempt.sections.all()}
    # CO et CE, répartition 1/2/2/2/1/1 : 699 × (3 + 18) / 152 = 96,6 → 97.
    assert scores["CO"] == (3, 97, "below_A1")
    assert scores["CE"] == (3, 97, "below_A1")
    # MSL, répartition 1/1/1/1/1/1 : 699 × (3 + 9) / 107 = 78,4 → 78.
    assert scores["MSL"] == (2, 78, "below_A1")
    # Global : (97 + 78 + 97) / 3 = 90,7 → 91 ; marge de 60 en mode rapide : 31 à 151.
    assert attempt.global_score == 91
    assert attempt.level_range == "A1 non atteint à A1"


def test_unanswered_items_count_as_wrong(user_client, attempt):
    play_co(user_client, attempt, answer=lambda t: None)
    api(user_client, attempt, "api_section_start", 2)
    api(user_client, attempt, "api_section_finish", 2)
    api(user_client, attempt, "api_section_start", 3)
    _, data = api(user_client, attempt, "api_section_finish", 3)
    assert data["redirect"] == reverse("attempt_results", args=[attempt.pk])
    attempt.refresh_from_db()
    assert attempt.global_score == 0
    assert Answer.objects.filter(attempt=attempt).count() == 24
    assert attempt.level_range == "A1 non atteint"


def test_results_and_review_pages(user_client, attempt):
    play_all(user_client, attempt, answer=wrong_index)
    results = user_client.get(reverse("attempt_results", args=[attempt.pk])).content.decode()
    assert "Estimation non officielle. Questions générées par IA." in results
    assert "Réussite par niveau" in results
    review = user_client.get(reverse("attempt_review", args=[attempt.pk])).content.decode()
    first_co = section_items(attempt.test, "CO")[0].item
    assert first_co.explanation in review
    assert first_co.audio_script["lines"][0]["text"] in review  # transcription
    assert "<audio" in review and "controls" in review  # réécoute libre
    assert "Votre réponse" in review and "Bonne réponse" in review


def test_results_unavailable_before_the_end(user_client, attempt):
    for name in ["attempt_results", "attempt_review"]:
        response = user_client.get(reverse(name, args=[attempt.pk]))
        assert response.url == reverse("attempt_player", args=[attempt.pk])


def test_player_redirects_to_results_once_completed(user_client, attempt):
    play_all(user_client, attempt)
    response = user_client.get(reverse("attempt_player", args=[attempt.pk]))
    assert response.url == reverse("attempt_results", args=[attempt.pk])


def test_history_and_dashboard_after_completion(user_client, attempt):
    play_all(user_client, attempt)
    history = user_client.get(reverse("history")).content.decode()
    assert "<svg" in history and "<circle" in history
    # Coordonnées en notation SVG (point décimal), jamais au format français.
    coordinates = re.findall(r'\s(?:cx|cy|x|y|y1|y2)="([^"]*)"', history)
    assert coordinates and all("," not in value for value in coordinates)
    dashboard = user_client.get(reverse("dashboard")).content.decode()
    assert "Dernier niveau estimé" in dashboard
    assert "C2" in dashboard


# --- Lecteur -------------------------------------------------------------------------------------


def test_player_has_no_audio_controls(user_client, attempt):
    html = user_client.get(reverse("attempt_player", args=[attempt.pk])).content.decode()
    audio_tags = [part.split(">")[0] for part in html.split("<audio")[1:]]
    assert len(audio_tags) == 2
    for tag in audio_tags:
        assert "controls" not in tag
    for forbidden in ["Pause", "pause()", 'type="range"', "currentTime"]:
        assert forbidden not in html
    assert "vendor/alpine.min.js" in html and "js/player.js" in html
