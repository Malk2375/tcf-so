"""Conseils personnalisés (SPEC 12) : seuil, constats, validation, modèles de secours, budget."""

import copy
import random
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from advice import content as advice_content
from advice import eligibility, findings, services
from advice.findings import Row
from advice.llm import StaticProvider
from advice.models import Advice
from bank import audio
from bank.constants import DOC_LENGTH
from bank.devtest import make_dev_test
from exam import services as exam_services
from exam.models import Answer, Attempt
from tests.conftest import PASSWORD
from usage.models import SiteSettings, UsageEntry

BASE = timezone.now() - timedelta(days=30)


@pytest.fixture(autouse=True)
def _fast_audio(monkeypatch):
    monkeypatch.setattr(audio, "placeholder_mp3", lambda seconds: f"mp3 {seconds:.2f}".encode())


# --- Constats, sans base de données -------------------------------------------------------------

_ids = iter(range(1, 1_000_000))


def row(
    section="MSL",
    level="B1",
    correct=True,
    *,
    subtype=None,
    skill="grammaire",
    lp=None,
    attempt=1,
    position=1,
    selected="auto",
    time_ms=10_000,
    history=None,
) -> Row:
    item_id = next(_ids)
    correct_index = 1
    if selected == "auto":
        selected = correct_index if correct else 2
    item = SimpleNamespace(
        question=f"Question {item_id}",
        options=["a", "b", "c", "d"],
        correct_index=correct_index,
        explanation="Explication.",
        stimulus_text="Texte ___ .",
        audio_script={"lines": [{"speaker": "F1", "text": "Bonjour."}]},
    )
    answer = SimpleNamespace(test_item_id=item_id, test_item=SimpleNamespace(item=item))
    subtype = subtype or {"MSL": "phrase_a_trou", "CO": "dialogue", "CE": "article"}[section]
    tags = {"skill": skill, "doc_length": DOC_LENGTH[level]}
    if lp:
        tags["language_point"] = lp
    return Row(
        answer=answer,
        attempt_id=attempt,
        finished_at=BASE + timedelta(hours=attempt),
        section=section,
        level=level,
        subtype=subtype,
        tags=tags,
        position=position,
        correct=correct,
        selected=selected,
        correct_index=correct_index,
        time_ms=time_ms,
        history=history or [],
    )


def rows_with(n_ok, n_ko, **kwargs) -> list[Row]:
    return [row(correct=True, **kwargs) for _ in range(n_ok)] + [
        row(correct=False, **kwargs) for _ in range(n_ko)
    ]


def ids(found):
    return [f["id"] for f in found]


def test_level_break_needs_a_30_point_drop_and_4_questions_each_side():
    rows = rows_with(4, 0, section="CO", level="A2") + rows_with(1, 3, section="CO", level="B1")
    found = findings.level_breaks(rows)
    assert ids(found) == ["level_break:CO:B1"]
    assert found[0]["details"]["rate_from"] == 100 and found[0]["details"]["rate_to"] == 25
    assert len(found[0]["errors"]) == 3
    # 3 questions seulement au niveau supérieur : rien.
    rows = rows_with(4, 0, section="CO", level="A2") + rows_with(0, 3, section="CO", level="B1")
    assert findings.level_breaks(rows) == []
    # Chute de 25 points : rien.
    rows = rows_with(4, 0, section="CO", level="A2") + rows_with(3, 1, section="CO", level="B1")
    assert findings.level_breaks(rows) == []


def test_language_point_needs_4_questions_and_at_most_50_percent():
    assert ids(findings.language_points(rows_with(2, 2, lp="pronoms_y_en"))) == [
        "language_point:pronoms_y_en"
    ]
    assert findings.language_points(rows_with(1, 2, lp="pronoms_y_en")) == []
    assert findings.language_points(rows_with(3, 1, lp="pronoms_y_en")) == []


def test_doc_type_compares_with_the_users_rate_at_the_same_level():
    weak = rows_with(1, 4, section="CE", level="B1", subtype="article")
    strong = rows_with(5, 0, section="CE", level="B1", subtype="courrier")
    found = findings.doc_types(weak + strong)
    # Taux B1 en CE : 60 % ; articles : 20 % → écart de 40 points.
    assert ids(found) == ["doc_type:CE:subtype:article"]
    assert (found[0]["rate"], found[0]["reference_rate"], found[0]["gap"]) == (20, 60, 40)
    # 4 questions seulement : rien.
    assert findings.doc_types(weak[1:] + strong) == []
    # Écart de moins de 15 points : rien.
    close = rows_with(4, 1, section="CE", level="B1", subtype="article")
    assert findings.doc_types(close + strong) == []


def test_skill_uses_the_same_rule_as_doc_type():
    weak = rows_with(1, 4, section="CO", level="B2", skill="inference")
    strong = rows_with(5, 0, section="CO", level="B2", skill="explicite")
    assert ids(findings.skills(weak + strong)) == ["skill:CO:inference"]
    assert findings.skills(weak[1:] + strong) == []


def qcm_section(attempt, answered, total=10, times=None, section="MSL"):
    times = times or [10_000] * total
    return [
        row(
            section,
            "B1",
            True,
            attempt=attempt,
            position=p + 1,
            time_ms=times[p],
            selected=1 if p < answered else None,
        )
        for p in range(total)
    ]


def test_time_pressure_unanswered_at_the_end():
    rows = qcm_section(1, answered=7) + qcm_section(2, answered=9)  # 3 + 1 = 2 par test
    found = findings.time_pressure(rows, [])
    assert ids(found) == ["time_pressure:MSL_CE"]
    assert found[0]["details"]["trailing_per_test"] == 2.0
    assert len(found[0]["errors"]) == 3
    rows = qcm_section(1, answered=8) + qcm_section(2, answered=9)  # 1,5 par test
    assert findings.time_pressure(rows, []) == []


def test_time_pressure_time_spent_on_the_first_half():
    front = [20_000] * 5 + [12_000] * 5  # 62,5 % sur la première moitié
    assert ids(findings.time_pressure(qcm_section(1, 10, times=front), [])) == [
        "time_pressure:MSL_CE"
    ]
    even = [12_000] * 5 + [10_000] * 5  # 54,5 %
    assert findings.time_pressure(qcm_section(1, 10, times=even), []) == []


def test_time_targets_split_the_section_duration_by_level(formats):
    attempt = SimpleNamespace(test=SimpleNamespace(format=formats["reel"]))
    targets = findings.target_seconds([attempt])
    msl = targets["MSL"]
    assert msl["average_seconds"] == 47  # 1080 s / 23 questions
    distribution = formats["reel"].sections.get(section="MSL").level_distribution
    total = sum(msl["by_level"][level] * n for level, n in distribution.items())
    assert abs(total - 1080) <= len(distribution)  # arrondis
    assert msl["by_level"]["A1"] < msl["by_level"]["C2"]


def test_answer_changes_more_than_half_unfavorable_with_6_changes():
    good_to_bad = [row(correct=False, history=[1, 2], selected=2) for _ in range(4)]
    bad_to_good = [row(correct=True, history=[0, 1], selected=1) for _ in range(2)]
    found = findings.answer_changes(good_to_bad + bad_to_good)
    assert ids(found) == ["answer_changes:all"]
    assert found[0]["details"]["unfavorable_share"] == 67
    assert len(found[0]["errors"]) == 3
    # Exactement 50 % : rien.
    assert findings.answer_changes(good_to_bad[:3] + bad_to_good + [bad_to_good[0]]) == []
    # 5 changements : rien, même tous défavorables.
    assert findings.answer_changes(good_to_bad + good_to_bad[:1]) == []


def test_progress_compares_the_last_two_tests_with_the_previous_ones():
    previous = findings.language_points(rows_with(1, 3, lp="pronoms_y_en"))
    rows = (
        rows_with(1, 3, lp="pronoms_y_en", attempt=1)
        + rows_with(1, 3, lp="pronoms_y_en", attempt=2)
        + rows_with(3, 1, lp="pronoms_y_en", attempt=3)
        + rows_with(4, 0, lp="pronoms_y_en", attempt=4)
    )
    attempts = [SimpleNamespace(pk=pk) for pk in (4, 3, 2, 1)]  # du plus récent au plus ancien
    result = findings.progress(rows, attempts, previous)
    entry = result["details"]["items"][0]
    assert entry["finding_id"] == "language_point:pronoms_y_en"
    assert (entry["recent"]["rate"], entry["earlier"]["rate"], entry["delta"]) == (88, 25, 63)


# --- Validation de la réponse et modèles de secours ---------------------------------------------


def sample_findings():
    rows = (
        rows_with(4, 0, section="CO", level="A2")
        + rows_with(1, 3, section="CO", level="B1")
        + rows_with(1, 4, lp="subjonctif_present")
        + rows_with(1, 4, section="CE", level="B1", subtype="article", skill="inference")
        + rows_with(5, 0, section="CE", level="B1", subtype="courrier", skill="explicite")
        + qcm_section(7, answered=6)
        + [row(correct=False, history=[1, 2], selected=2) for _ in range(6)]
    )
    found = (
        findings.level_breaks(rows)
        + findings.language_points(rows)
        + findings.doc_types(rows)
        + findings.skills(rows)
        + findings.time_pressure(rows, [])
        + findings.answer_changes(rows)
    )
    found.append(findings.progress(rows, [SimpleNamespace(pk=7)], found[:1]))
    return found


def test_every_finding_type_is_found_by_the_sample():
    types = {f["type"] for f in sample_findings()}
    assert types == {
        "level_break",
        "language_point",
        "doc_type",
        "skill",
        "time_pressure",
        "answer_changes",
        "progress",
    }


def test_fallback_content_is_complete_and_valid():
    found = sample_findings()
    content = advice_content.fallback_content(found)
    assert advice_content.validate(content, found) == []
    for item in content["items"]:
        assert item["lesson"]
        assert len(item["practice"]) == 3
        assert all(len(p["options"]) == 4 for p in item["practice"])
        assert 2 <= len(item["week_plan"]) <= 3
    exempt = advice_content.NO_ERROR_REVIEW_TYPES
    by_id = {f["id"]: f for f in found}
    for item in content["items"]:
        if by_id[item["finding_id"]]["type"] not in exempt:
            assert item["error_reviews"]
    # Le constat `progress` ne donne pas de conseil, mais une note.
    assert "progress" not in {i["finding_id"] for i in content["items"]}
    # Constat déjà présent dans le conseil précédent, mais absent du dernier test : non mesurable.
    assert content["items"][0]["progress_note"].startswith("Évolution non mesurable")
    assert content["items"][1]["progress_note"] == "Nouveau constat depuis votre conseil précédent."


def test_every_fallback_template_is_valid():
    """Chaque texte de secours (types, compétences, points de langue) passe la validation."""
    base = sample_findings()[0]
    types = advice_content._fallbacks("types")
    points = advice_content._fallbacks("language_points")
    templates = list(types.values()) + list(points.values())
    for template in templates:
        item = {
            "finding_id": base["id"],
            "title": template["title"],
            "diagnostic": "d",
            "lesson": template["lesson"],
            "error_reviews": [advice_content.error_review(base["errors"][0])],
            "exam_strategy": template["exam_strategy"],
            "practice": template["practice"],
            "week_plan": template["week_plan"],
            "progress_note": None,
        }
        content = {"summary": "s", "next_goal": "g", "items": [item]}
        assert advice_content.validate(content, [base]) == [], template["title"]


@pytest.mark.parametrize(
    "breakage, message",
    [
        (lambda c: c["items"][0].update(finding_id="language_point:inconnu"), "constat inconnu"),
        (lambda c: c["items"][0]["error_reviews"][0].update(test_item_id=-1), "inconnue"),
        (lambda c: c["items"][0]["practice"].pop(), "practice"),
        (lambda c: c["items"][0]["practice"][0]["options"].pop(), "options"),
        (lambda c: c["items"][0]["practice"][0]["options"].__setitem__(1, "a"), "double"),
        (lambda c: c["items"][0]["practice"][0].update(correct_index=4), "correct_index"),
        (lambda c: c["items"][0]["week_plan"][0].update(resource_id="youtube"), "ressource"),
        (lambda c: c["items"][0].update(week_plan=c["items"][0]["week_plan"][:1]), "week_plan"),
        (lambda c: c["items"][0].update(error_reviews=[]), "aucune erreur"),
        (lambda c: c["items"][0].update(lesson=""), "lesson"),
        (lambda c: c.pop("summary"), "summary"),
        (lambda c: c["items"].append(copy.deepcopy(c["items"][0])), "deux fois"),
    ],
)
def test_invalid_model_responses_are_detected(breakage, message):
    found = sample_findings()
    content = advice_content.fallback_content(found)
    content["items"][0]["practice"][0]["options"] = ["a", "b", "c", "d"]
    breakage(content)
    errors = advice_content.validate(content, found)
    assert errors and any(message in error for error in errors), errors


def test_error_review_not_required_for_time_and_answer_changes():
    found = [f for f in sample_findings() if f["type"] in {"time_pressure", "answer_changes"}]
    content = advice_content.fallback_content(found)
    for item in content["items"]:
        item["error_reviews"] = []
    assert advice_content.validate(content, found) == []


def test_output_schema_has_only_supported_keywords():
    """Sortie structurée : pas de contrainte de nombre ou de longueur, tous les objets fermés."""

    def walk(node):
        if isinstance(node, dict):
            assert not {"minItems", "maxItems", "minimum", "maximum", "minLength"} & set(node)
            if node.get("type") == "object":
                assert node["additionalProperties"] is False
                assert set(node["required"]) == set(node["properties"])
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(advice_content.output_schema())


# --- Avec la base de données ---------------------------------------------------------------------


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("candidat", "candidat@example.com", PASSWORD)


@pytest.fixture
def user_client(client, user):
    client.force_login(user)
    return client


@pytest.fixture
def tests_by_mode(formats):
    rng = random.Random(5)
    return {
        "rapide": [make_dev_test("rapide", rng=rng) for _ in range(3)],
        "moyen": [make_dev_test("moyen", rng=rng) for _ in range(2)],
    }


def complete(user, test, wrong_every=3, minutes=0):
    """Passage terminé : une question sur `wrong_every` fausse, réponses enregistrées."""
    now = timezone.now() - timedelta(days=1) + timedelta(minutes=minutes)
    attempt = exam_services.create_attempt(user, test, now=now)
    for index, test_item in enumerate(test.test_items.select_related("item").order_by("pk")):
        correct = test_item.item.correct_index
        selected = (correct + 1) % 4 if index % wrong_every == 0 else correct
        Answer.objects.create(
            attempt=attempt,
            test_item=test_item,
            selected_index=selected,
            first_seen_at=now,
            time_spent_ms=15_000,
            choice_history=[{"index": selected, "at": now.isoformat()}],
        )
    exam_services.complete_attempt(attempt, now + timedelta(minutes=30))
    attempt.refresh_from_db()
    return attempt


def bare_attempt(user, test):
    now = timezone.now()
    return Attempt.objects.create(
        user=user,
        test=test,
        status=Attempt.Status.COMPLETED,
        started_at=now,
        expires_at=now,
        finished_at=now,
    )


@pytest.mark.django_db
def test_eligibility_thresholds_do_not_add_up_across_modes(user, tests_by_mode, formats):
    rapide, moyen = tests_by_mode["rapide"], tests_by_mode["moyen"]
    assert not eligibility.is_eligible(user)
    assert eligibility.remaining_message(user) == (
        "encore 3 tests rapides, ou 2 tests moyens, ou 1 test réel"
    )
    bare_attempt(user, rapide[0])
    bare_attempt(user, rapide[1])
    bare_attempt(user, moyen[0])
    # 2 rapides + 1 moyen : les modes ne se cumulent pas.
    assert not eligibility.is_eligible(user)
    assert eligibility.remaining_message(user) == (
        "encore 1 test rapide, ou 1 test moyen, ou 1 test réel"
    )
    bare_attempt(user, rapide[2])
    assert eligibility.is_eligible(user)
    assert eligibility.remaining_message(user) == ""


@pytest.mark.django_db
def test_two_moyen_or_one_reel_are_enough(formats):
    users = get_user_model().objects
    moyen_user = users.create_user("m", "m@example.com", PASSWORD)
    reel_user = users.create_user("r", "r@example.com", PASSWORD)
    moyen = make_dev_test("moyen", rng=random.Random(1))
    bare_attempt(moyen_user, moyen)
    assert not eligibility.is_eligible(moyen_user)
    bare_attempt(moyen_user, moyen)
    assert eligibility.is_eligible(moyen_user)
    bare_attempt(reel_user, make_dev_test("reel", rng=random.Random(2)))
    assert eligibility.is_eligible(reel_user)


@pytest.mark.django_db
def test_before_the_threshold_pages_say_what_is_left(user_client, user, tests_by_mode):
    attempt = complete(user, tests_by_mode["rapide"][0])
    response = user_client.post(reverse("attempt_advice", args=[attempt.pk]))
    html = response.content.decode()
    assert "2 tests rapides, ou 2 tests moyens, ou 1 test réel" in html
    assert Advice.objects.count() == 0
    page = user_client.get(reverse("advice")).content.decode()
    assert "Il vous reste à passer encore 2 tests rapides" in page
    dashboard = user_client.get(reverse("dashboard")).content.decode()
    assert "2 tests rapides" in dashboard


def eligible_user(user, tests_by_mode):
    attempts = [
        complete(user, test, wrong_every=2 + i, minutes=i)
        for i, test in enumerate(tests_by_mode["rapide"])
    ]
    return attempts[-1]


@pytest.mark.django_db
def test_fake_provider_gives_complete_fallback_advice(user_client, user, tests_by_mode):
    attempt = eligible_user(user, tests_by_mode)
    results = user_client.get(reverse("attempt_results", args=[attempt.pk])).content.decode()
    assert reverse("attempt_advice", args=[attempt.pk]) in results  # bloc chargé par HTMX

    block = user_client.post(reverse("attempt_advice", args=[attempt.pk])).content.decode()
    assert "Vos conseils personnalisés" in block
    advice = Advice.objects.get()
    assert advice.source == Advice.Source.FALLBACK
    assert advice.fallback_reason == "LLM_PROVIDER=fake."
    assert advice.attempt_ids[0] == attempt.pk and len(advice.attempt_ids) == 3
    assert advice.content["items"]
    assert advice_content.validate(advice.content, advice.findings) == []
    assert UsageEntry.objects.count() == 0

    page = user_client.get(reverse("advice")).content.decode()
    for heading in (
        "Diagnostic",
        "La leçon",
        "Retour sur vos erreurs",
        "Entraînement",
        "Votre plan de la semaine",
        "Leçons et exercices rédigés par IA, non relus.",
    ):
        assert heading in page
    # Chaque erreur commentée renvoie à la question dans la correction du bon passage.
    review = advice.content["items"][0]["error_reviews"][0]
    error = next(
        e
        for f in advice.findings
        for e in f["errors"]
        if e["test_item_id"] == review["test_item_id"]
    )
    link = reverse("attempt_review", args=[error["attempt_id"]]) + f"#q-{review['test_item_id']}"
    assert link in page


@pytest.mark.django_db
def test_advice_is_generated_once_per_attempt(user, tests_by_mode):
    attempt = eligible_user(user, tests_by_mode)
    provider = StaticProvider(None)
    first = services.advice_for(attempt, provider)
    second = services.advice_for(attempt, provider)
    assert first.pk == second.pk
    assert len(provider.calls) == 1


def valid_response(user):
    found, _ = findings.compute_findings(user)
    return advice_content.fallback_content(found)


@pytest.mark.django_db
def test_valid_model_response_is_kept_and_billed(user, tests_by_mode):
    attempt = eligible_user(user, tests_by_mode)
    response = valid_response(user)
    response["summary"] = "Rédigé par le modèle."
    provider = StaticProvider(response, input_tokens=5000, output_tokens=3000)
    advice = services.generate(attempt, provider)
    assert advice.source == Advice.Source.LLM
    assert advice.content["summary"] == "Rédigé par le modèle."
    entry = UsageEntry.objects.get()
    assert (entry.kind, entry.model, entry.input_tokens, entry.output_tokens) == (
        "llm_advice",
        "claude-sonnet-5-5",
        5000,
        3000,
    )
    # 5 000 × 2 $ + 3 000 × 10 $ par million de tokens.
    assert entry.cost_usd == Decimal("0.04") == advice.cost_usd
    # Aucune donnée personnelle dans la requête.
    call = provider.calls[0]
    assert "candidat" not in call["user"] and "@" not in call["user"]
    assert call["schema"] == advice_content.output_schema()


@pytest.mark.django_db
@pytest.mark.parametrize("kind", ["unknown_finding", "missing_exercise", "not_json"])
def test_invalid_model_response_falls_back(user, tests_by_mode, kind):
    attempt = eligible_user(user, tests_by_mode)
    response = valid_response(user)
    if kind == "unknown_finding":
        response["items"][0]["finding_id"] = "skill:CO:telepathie"
    elif kind == "missing_exercise":
        response["items"][0]["practice"] = response["items"][0]["practice"][:2]
    else:
        response = None
    advice = services.generate(attempt, StaticProvider(response))
    assert advice.source == Advice.Source.FALLBACK
    assert advice.fallback_reason
    assert advice_content.validate(advice.content, advice.findings) == []
    # L'appel a été facturé : il est journalisé même si la réponse est rejetée.
    assert UsageEntry.objects.filter(kind="llm_advice").count() == 1


@pytest.mark.django_db
def test_monthly_budget_reached_uses_fallback_without_calling(user, tests_by_mode):
    attempt = eligible_user(user, tests_by_mode)
    budget = SiteSettings.load().api_monthly_budget_usd
    UsageEntry.objects.create(kind="llm_advice", provider="anthropic", cost_usd=budget)
    provider = StaticProvider(valid_response(user))
    advice = services.generate(attempt, provider)
    assert provider.calls == []
    assert advice.source == Advice.Source.FALLBACK
    assert advice.fallback_reason == "Plafond mensuel de l'API atteint."


@pytest.mark.django_db
def test_anthropic_provider_without_key_falls_back(user, tests_by_mode, settings):
    attempt = eligible_user(user, tests_by_mode)
    settings.LLM_PROVIDER = "anthropic"
    settings.ANTHROPIC_API_KEY = ""
    advice = services.advice_for(attempt)
    assert advice.source == Advice.Source.FALLBACK
    assert "ANTHROPIC_API_KEY" in advice.fallback_reason


@pytest.mark.django_db
def test_second_advice_has_progress(user, tests_by_mode):
    attempt = eligible_user(user, tests_by_mode)
    services.advice_for(attempt)
    later = complete(user, tests_by_mode["moyen"][0], minutes=60)
    advice = services.advice_for(later)
    assert advice.findings[-1]["type"] == "progress"
    assert advice.findings[-1]["details"]["items"]


@pytest.mark.django_db
def test_advice_belongs_to_its_user(client, user, tests_by_mode):
    attempt = eligible_user(user, tests_by_mode)
    advice = services.advice_for(attempt)
    other = get_user_model().objects.create_user("autre", "autre@example.com", PASSWORD)
    client.force_login(other)
    assert client.post(reverse("attempt_advice", args=[attempt.pk])).status_code == 404
    assert client.get(reverse("advice_detail", args=[advice.pk])).status_code == 404
    response = client.post(reverse("advice_plan", args=[advice.pk]), {"key": "0-0", "done": "1"})
    assert response.status_code == 404


@pytest.mark.django_db
def test_week_plan_checkboxes_are_saved(user_client, user, tests_by_mode):
    attempt = eligible_user(user, tests_by_mode)
    advice = services.advice_for(attempt)
    plan = reverse("advice_plan", args=[advice.pk])
    assert user_client.post(plan, {"key": "0-1", "done": "1"}).status_code == 204
    advice.refresh_from_db()
    assert advice.plan_done == ["0-1"]
    page = user_client.get(reverse("advice")).content.decode()
    assert page.count("checked") == 1
    user_client.post(plan, {"key": "0-1"})
    advice.refresh_from_db()
    assert advice.plan_done == []
    assert user_client.post(plan, {"key": "9-9", "done": "1"}).status_code == 400


@pytest.mark.django_db
def test_previous_advice_stays_available(user_client, user, tests_by_mode):
    first = services.advice_for(eligible_user(user, tests_by_mode))
    services.advice_for(complete(user, tests_by_mode["moyen"][0], minutes=60))
    page = user_client.get(reverse("advice")).content.decode()
    assert "Conseils précédents" in page
    assert reverse("advice_detail", args=[first.pk]) in page
    assert user_client.get(reverse("advice_detail", args=[first.pk])).status_code == 200


@pytest.mark.django_db
def test_deleting_the_account_deletes_advice(user_client, user, tests_by_mode):
    services.advice_for(eligible_user(user, tests_by_mode))
    user.delete()
    assert Advice.objects.count() == 0
