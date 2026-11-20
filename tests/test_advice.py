"""Conseils personnalisés (SPEC 12, révision du 20 novembre 2026) : seuil sur tests distincts,
constats dont `msl_family`, aucun chiffre de réussite, conseils invalides retirés un par un,
modèles de secours, budget."""

import copy
import json
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
from advice.llm import SYSTEM_PROMPT, StaticProvider, model_findings
from advice.models import Advice
from bank import audio
from bank.constants import DOC_LENGTH
from bank.devtest import make_dev_test
from bank.language_points import FAMILIES, family_of
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
        stimulus_text=f"Phrase numéro {item_id} avec un ___ au milieu.",
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
    assert found[0]["intensity"] == "très marqué"
    assert len(found[0]["errors"]) == 3
    rows = rows_with(4, 0, section="CO", level="A2") + rows_with(0, 3, section="CO", level="B1")
    assert findings.level_breaks(rows) == []
    rows = rows_with(4, 0, section="CO", level="A2") + rows_with(3, 1, section="CO", level="B1")
    assert findings.level_breaks(rows) == []


def test_msl_family_needs_3_questions_2_errors_and_at_most_half_right():
    pronouns = {"lp": "pronoms_y_en", "level": "B1"}
    assert ids(findings.msl_families(rows_with(1, 2, **pronouns), "B1")) == ["msl_family:pronoms"]
    # Des points différents d'une même famille se cumulent.
    mixed = rows_with(1, 1, lp="pronoms_y_en") + rows_with(0, 1, lp="pronoms_relatifs_simples")
    assert ids(findings.msl_families(mixed, "B1")) == ["msl_family:pronoms"]
    assert findings.msl_families(rows_with(0, 2, **pronouns), "B1") == []  # 2 questions
    assert findings.msl_families(rows_with(2, 1, **pronouns), "B1") == []  # 1 erreur
    assert findings.msl_families(rows_with(3, 2, **pronouns), "B1") == []  # plus de la moitié
    assert ids(findings.msl_families(rows_with(2, 2, **pronouns), "B1")) == ["msl_family:pronoms"]


def test_msl_family_ignores_questions_above_the_estimated_level_plus_one():
    rows = rows_with(0, 3, lp="concordance_des_temps", level="C1")  # famille verbes, C1
    assert findings.msl_families(rows, "A2") == []  # A2 + 1 = B1 : C1 trop haut
    assert ids(findings.msl_families(rows, "B2")) == ["msl_family:verbes"]  # B2 + 1 = C1
    found = findings.msl_families(rows, "B2")[0]
    assert found["filter"] == {"section": "MSL", "family": "verbes", "max_level": "C1"}


def test_msl_level_counts_below_a1_as_a1():
    class Sections:
        def __init__(self, level):
            self.level = level

        def filter(self, **kwargs):
            return self

        def first(self):
            return SimpleNamespace(level=self.level)

    def attempt(level):
        return SimpleNamespace(sections=Sections(level))

    assert findings.msl_level([attempt("below_A1")]) == "A1"
    assert findings.msl_level([attempt("B2")]) == "B2"
    assert findings.msl_level([]) is None


def test_msl_family_joins_up_to_4_errors_of_different_points():
    rows = (
        rows_with(0, 3, lp="pronoms_y_en", attempt=2)
        + rows_with(0, 1, lp="pronoms_cod_coi", level="A2")
        + rows_with(0, 1, lp="pronoms_relatifs_simples")
    )
    errors = findings.msl_families(rows, "B1")[0]["errors"]
    assert len(errors) == 4
    assert {e["language_point"] for e in errors} == {
        "pronoms_y_en",
        "pronoms_cod_coi",
        "pronoms_relatifs_simples",
    }


def test_doc_type_compares_subtypes_only():
    weak = rows_with(1, 4, section="CE", level="B1", subtype="article")
    strong = rows_with(5, 0, section="CE", level="B1", subtype="courrier")
    found = findings.doc_types(weak + strong)
    assert ids(found) == ["doc_type:CE:subtype:article"]  # plus de constat par longueur
    assert findings.doc_types(weak[1:] + strong) == []
    close = rows_with(4, 1, section="CE", level="B1", subtype="article")
    assert findings.doc_types(close + strong) == []


def test_skill_concerns_co_and_ce_only():
    weak = rows_with(1, 4, section="CO", level="B2", skill="inference")
    strong = rows_with(5, 0, section="CO", level="B2", skill="explicite")
    assert ids(findings.skills(weak + strong)) == ["skill:CO:inference"]
    assert findings.skills(weak[1:] + strong) == []
    msl_weak = rows_with(1, 4, section="MSL", skill="lexique")
    msl_strong = rows_with(5, 0, section="MSL", skill="grammaire")
    assert findings.skills(msl_weak + msl_strong) == []  # la MSL passe par msl_family


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
    assert found[0]["details"]["unanswered_at_end"] is True
    rows = qcm_section(1, answered=8) + qcm_section(2, answered=9)  # 1,5 par test
    assert findings.time_pressure(rows, []) == []


def test_time_pressure_time_spent_on_the_first_half():
    front = [20_000] * 5 + [12_000] * 5  # 62,5 % sur la première moitié
    found = findings.time_pressure(qcm_section(1, 10, times=front), [])
    assert ids(found) == ["time_pressure:MSL_CE"] and found[0]["details"]["slow_start"]
    even = [12_000] * 5 + [10_000] * 5  # 54,5 %
    assert findings.time_pressure(qcm_section(1, 10, times=even), []) == []


def test_time_targets_split_the_section_duration_by_level(formats):
    attempt = SimpleNamespace(test=SimpleNamespace(format=formats["reel"]))
    msl = findings.target_seconds([attempt])["MSL"]
    assert msl["average_seconds"] == 47  # 1080 s / 23 questions
    distribution = formats["reel"].sections.get(section="MSL").level_distribution
    total = sum(msl["by_level"][level] * n for level, n in distribution.items())
    assert abs(total - 1080) <= len(distribution)
    assert msl["by_level"]["A1"] < msl["by_level"]["C2"]


def test_answer_changes_more_than_half_unfavorable_with_6_changes():
    good_to_bad = [row(correct=False, history=[1, 2], selected=2) for _ in range(4)]
    bad_to_good = [row(correct=True, history=[0, 1], selected=1) for _ in range(2)]
    assert ids(findings.answer_changes(good_to_bad + bad_to_good)) == ["answer_changes:all"]
    assert findings.answer_changes(good_to_bad[:3] + bad_to_good + [bad_to_good[0]]) == []
    assert findings.answer_changes(good_to_bad + good_to_bad[:1]) == []


def test_intensity_is_computed_by_the_code():
    assert findings.intensity(30) == "très marqué"
    assert findings.intensity(29) == "marqué"


def test_progress_compares_the_last_two_tests_and_ignores_old_types():
    previous = findings.msl_families(rows_with(1, 3, lp="pronoms_y_en"), None)
    old = {"id": "language_point:pronoms_y_en", "type": "language_point", "label": "ancien"}
    rows = (
        rows_with(1, 3, lp="pronoms_y_en", attempt=1)
        + rows_with(1, 3, lp="pronoms_y_en", attempt=2)
        + rows_with(3, 1, lp="pronoms_y_en", attempt=3)
        + rows_with(4, 0, lp="pronoms_y_en", attempt=4)
    )
    attempts = [SimpleNamespace(pk=pk) for pk in (4, 3, 2, 1)]
    result = findings.progress(rows, attempts, [*previous, old])
    [entry] = result["details"]["items"]  # l'ancien constat `language_point` est ignoré
    assert entry["finding_id"] == "msl_family:pronoms"
    assert entry["delta"] == 63 and entry["trend"] == "en hausse"


# --- Contenu : schéma, contrôles, modèles de secours --------------------------------------------


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
        + findings.msl_families(rows, "B2")
        + findings.doc_types(rows)
        + findings.skills(rows)
        + findings.time_pressure(rows, [])
        + findings.answer_changes(rows)
    )
    found.append(findings.progress(rows, [SimpleNamespace(pk=7)], found[:1]))
    return found


def three_findings():
    """Trois constats (le maximum) et `progress` : CO, MSL, CE."""
    found = sample_findings()
    keep = {"level_break", "msl_family", "doc_type", "progress"}
    return [f for f in found if f["type"] in keep]


def by_type(found, kind):
    return next(f for f in found if f["type"] == kind)


def test_every_finding_type_is_found_by_the_sample():
    assert {f["type"] for f in sample_findings()} == {
        "level_break",
        "msl_family",
        "doc_type",
        "skill",
        "time_pressure",
        "answer_changes",
        "progress",
    }


def displayed_texts(content):
    texts = [content["summary"]]
    for item in content["items"]:
        texts += [item["title"], item["diagnostic"], item["lesson"], item["progress_note"] or ""]
        texts += [r["why_wrong"] for r in item["error_reviews"]]
    return texts


def test_fallback_content_is_valid_and_has_no_figure():
    found = sample_findings()
    content = advice_content.fallback_content(found)
    three = three_findings()
    assert advice_content.validate(advice_content.fallback_content(three), three) == []
    assert len(content["items"]) == 6
    for item in content["items"]:
        assert item["lesson"] and item["practice"] == [] and item["resource"] is None
        assert len(item["error_reviews"]) <= 2
    for text in displayed_texts(content):
        assert "%" not in text and advice_content.text_problem(text) is None, text
    assert "progress" not in {i["finding_id"] for i in content["items"]}
    notes = [i["progress_note"] for i in content["items"]]
    assert notes[0] == advice_content.PROGRESS_NOTES["non mesurable"]
    assert notes[1] == "Nouveau constat depuis votre conseil précédent."


def test_time_pressure_fallback_gives_time_landmarks_only():
    finding = by_type(sample_findings(), "time_pressure")
    finding["details"]["targets"] = {"MSL": {"average_seconds": 45, "by_level": {"A1": 20}}}
    item = advice_content.fallback_content([finding])["items"][0]
    assert "environ 45 secondes par question" in item["diagnostic"]
    assert advice_content.text_problem(item["diagnostic"]) is None


def test_every_fallback_template_exists_and_passes_the_filters():
    keys = set(advice_content.fallbacks())
    expected = (
        {f"level_break:{s}" for s in ("CO", "MSL", "CE")}
        | {"doc_type:CO", "doc_type:CE", "time_pressure", "answer_changes"}
        | {f"skill:{s}" for s in ("explicite", "global", "inference", "opinion")}
        | {f"msl_family:{family}" for family in FAMILIES}
    )
    assert keys == expected
    for key, template in advice_content.fallbacks().items():
        words = len(template["lesson"].split())
        assert 60 <= words <= 150, key
        for text in template.values():
            assert advice_content.text_problem(text) is None, key


def test_no_finding_means_no_advice():
    content = advice_content.fallback_content([])
    assert content == {"summary": advice_content.NO_FINDING_SUMMARY, "items": []}
    assert "aucune difficulté nette" in content["summary"]


@pytest.mark.parametrize(
    "text",
    [
        "Vous réussissez 83 % des questions.",
        "Un écart de 50 points avec votre moyenne.",
        "Vous avez trouvé 3 questions sur 5.",
        "Vous réussissez cinquante pour cent des questions.",
        "Refaites les questions manquées.",
        "Repassez ce test dans une semaine.",
    ],
)
def test_text_filters_refuse_figures_and_retakes(text):
    assert advice_content.text_problem(text)


@pytest.mark.parametrize(
    "text",
    [
        "Dès le niveau B2, les textes argumentatifs vous posent souvent problème.",
        "Retenez deux points : la forme et le sens.",
        "Un regard sur un problème de société.",
    ],
)
def test_text_filters_accept_plain_words(text):
    assert advice_content.text_problem(text) is None


def model_response(found):
    """Réponse de modèle valide : le contenu de secours, avec un exercice et une ressource."""
    content = advice_content.fallback_content(found)
    for item in content["items"]:
        if by_id(found)[item["finding_id"]]["section"] != "CO":
            item["practice"] = [
                {
                    "question": "Choisissez la forme qui convient : il faut que tu ___ à l'heure.",
                    "options": ["viens", "viennes", "viendras", "venais"],
                    "correct_index": 1,
                    "explanation": "« Il faut que » impose le subjonctif.",
                }
            ]
    content["items"][0]["resource"] = {"resource_id": "tv5monde_tcf", "what_to_do": "Écoutez."}
    return content


def by_id(found):
    return {f["id"]: f for f in found}


def test_model_response_passes_the_checks():
    found = three_findings()
    assert advice_content.validate(model_response(found), found) == []


@pytest.mark.parametrize(
    "breakage, message",
    [
        (lambda c: c["items"][0].update(finding_id="msl_family:inconnu"), "constat inconnu"),
        (lambda c: c["items"][0]["error_reviews"][0].update(test_item_id=-1), "inconnue"),
        (lambda c: c["items"][1]["error_reviews"].clear(), "aucune erreur commentée"),
        (
            lambda c: c["items"][1]["error_reviews"].extend(c["items"][1]["error_reviews"] * 2),
            "2 au plus",
        ),
        (lambda c: c["items"][1]["practice"].extend(c["items"][1]["practice"] * 3), "3 au plus"),
        (lambda c: c["items"][1]["practice"][0]["options"].pop(), "4 options"),
        (lambda c: c["items"][1]["practice"][0]["options"].__setitem__(1, "viens"), "4 options"),
        (lambda c: c["items"][1]["practice"][0].update(correct_index=4), "correct_index"),
        (lambda c: c["items"][0]["resource"].update(resource_id="youtube"), "inconnue"),
        (lambda c: c["items"][1].update(lesson=""), "vide"),
        (lambda c: c["items"][1].update(lesson="Vous êtes à 83 % au niveau B1."), "83 %"),
        (lambda c: c["items"][1].update(diagnostic="Un écart de 50 points."), "50 points"),
        (lambda c: c["items"][1].update(lesson="Refaites les questions manquées."), "Refaites"),
        (lambda c: c["items"].append(copy.deepcopy(c["items"][1])), "deux fois"),
    ],
)
def test_invalid_advice_is_removed_and_the_others_kept(breakage, message):
    found = three_findings()
    content = model_response(found)
    breakage(content)
    kept, problems = advice_content.screen(content, found)
    assert kept is not None, problems
    assert any(message in p for p in problems), problems
    assert len(kept["items"]) == len(advice_content.fallback_content(found)["items"]) - (
        0 if "deux fois" in message else 1
    )


def test_practice_rules_for_co_and_near_duplicates():
    found = three_findings()
    content = model_response(found)
    co = content["items"][0]  # level_break:CO
    co["practice"] = copy.deepcopy(content["items"][1]["practice"])
    msl = content["items"][1]
    joined = by_id(found)[msl["finding_id"]]["errors"][0]
    msl["practice"][0]["question"] = joined["support"]  # reprise d'une question jointe
    kept, problems = advice_content.screen(content, found)
    assert any("exercice pour un constat de CO" in p for p in problems)
    assert any("trop proche d'une question jointe" in p for p in problems)
    assert len(kept["items"]) == len(content["items"]) - 2


def test_resource_must_match_the_section():
    found = three_findings()
    content = model_response(found)
    content["items"][1]["resource"] = {"resource_id": "rfi_francais_facile", "what_to_do": "x"}
    _, problems = advice_content.screen(content, found)  # RFI : CO seulement ; item 1 en MSL
    assert any("hors épreuve ou niveau" in p for p in problems)


@pytest.mark.parametrize(
    "breakage",
    [
        lambda c: c.update(summary="Vous réussissez 83 % des questions."),
        lambda c: c.update(summary=""),
        lambda c: c.pop("summary"),
        lambda c: [i.update(lesson="Refaites les questions.") for i in c["items"]],
    ],
)
def test_unusable_response(breakage):
    found = three_findings()
    content = model_response(found)
    breakage(content)
    kept, problems = advice_content.screen(content, found)
    assert kept is None and problems


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

    schema = advice_content.output_schema()
    walk(schema)
    item = schema["properties"]["items"]["items"]["properties"]
    assert not {"next_goal", "exam_strategy", "week_plan"} & set(item)
    assert "next_goal" not in schema["properties"]


RATE_KEYS = {"rate", "reference_rate", "gap", "strength", "n", "rate_from", "rate_to"}


def test_model_receives_no_rate():
    found = sample_findings()
    payload = model_findings(found)
    text = json.dumps(payload, ensure_ascii=False)
    assert "%" not in text

    def walk(node):
        if isinstance(node, dict):
            assert not RATE_KEYS & set(node), set(node)
            assert "first_half_share" not in node and "unfavorable_share" not in node
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(payload)
    msl = next(f for f in payload if f["type"] == "msl_family")
    assert msl["intensity"] in {"marqué", "très marqué"} and msl["family"] == "verbes"
    assert msl["missed_questions"][0]["language_hint"]
    progress = next(f for f in payload if f["type"] == "progress")
    assert progress["items"][0]["trend"] == "non mesurable"


def test_system_prompt_states_the_new_rules():
    for rule in ("Aucun chiffre de réussite", "De 0 à 3 conseils", "repasser un test", "QCM"):
        assert rule in SYSTEM_PROMPT


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


def never(test_item):
    return False


def families(*names):
    """Réponses fausses sur les questions de MSL de ces familles."""

    def wrong(test_item):
        return family_of(test_item.item.tags.get("language_point")) in names

    return wrong


def complete(user, test, wrong=never, minutes=0):
    """Passage terminé ; `wrong(test_item)` dit quelles questions sont fausses."""
    now = timezone.now() - timedelta(days=1) + timedelta(minutes=minutes)
    attempt = exam_services.create_attempt(user, test, now=now)
    for test_item in test.test_items.select_related("item").order_by("pk"):
        correct = test_item.item.correct_index
        selected = (correct + 1) % 4 if wrong(test_item) else correct
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


def bare_attempt(user, test, minutes=0):
    now = timezone.now() + timedelta(minutes=minutes)
    return Attempt.objects.create(
        user=user,
        test=test,
        status=Attempt.Status.COMPLETED,
        started_at=now,
        expires_at=now,
        finished_at=now,
    )


@pytest.mark.django_db
def test_eligibility_counts_distinct_tests(user, tests_by_mode):
    rapide = tests_by_mode["rapide"]
    bare_attempt(user, rapide[0])
    bare_attempt(user, rapide[0], minutes=1)  # doublon enregistré avant la règle 8.1
    bare_attempt(user, rapide[1], minutes=2)
    assert not eligibility.is_eligible(user)  # 2 tests distincts, pas 3
    assert eligibility.remaining_message(user) == (
        "encore 1 entraînement rapide, ou 2 entraînements moyens, ou 1 test en conditions réelles"
    )
    assert [a.test for a in eligibility.retained_attempts(user)] == [rapide[1], rapide[0]]
    bare_attempt(user, rapide[2], minutes=3)
    assert eligibility.is_eligible(user)


@pytest.mark.django_db
def test_eligibility_thresholds_do_not_add_up_across_modes(user, tests_by_mode):
    rapide, moyen = tests_by_mode["rapide"], tests_by_mode["moyen"]
    assert eligibility.remaining_message(user) == (
        "encore 3 entraînements rapides, ou 2 entraînements moyens, ou 1 test en conditions réelles"
    )
    bare_attempt(user, rapide[0])
    bare_attempt(user, rapide[1])
    bare_attempt(user, moyen[0])
    assert not eligibility.is_eligible(user)
    bare_attempt(user, moyen[1])
    assert eligibility.is_eligible(user)


@pytest.mark.django_db
def test_one_reel_is_enough(user, formats):
    bare_attempt(user, make_dev_test("reel", rng=random.Random(2)))
    assert eligibility.is_eligible(user)


@pytest.mark.django_db
def test_before_the_threshold_pages_say_what_is_left(user_client, user, tests_by_mode):
    attempt = complete(user, tests_by_mode["rapide"][0])
    html = user_client.post(reverse("attempt_advice", args=[attempt.pk])).content.decode()
    assert (
        "2 entraînements rapides, ou 2 entraînements moyens, ou 1 test en conditions réelles"
        in html
    )
    assert Advice.objects.count() == 0
    page = user_client.get(reverse("advice")).content.decode()
    assert "Il vous reste à passer encore 2 entraînements rapides" in page


def eligible_user(user, tests_by_mode, wrong=never):
    attempts = [
        complete(user, test, wrong=wrong, minutes=i)
        for i, test in enumerate(tests_by_mode["rapide"])
    ]
    return attempts[-1]


@pytest.mark.django_db
def test_eligible_without_finding_sees_the_summary_alone(user_client, user, tests_by_mode):
    attempt = eligible_user(user, tests_by_mode)
    block = user_client.post(reverse("attempt_advice", args=[attempt.pk])).content.decode()
    advice = Advice.objects.get()
    assert advice.content == {"summary": advice_content.NO_FINDING_SUMMARY, "items": []}
    assert advice.fallback_reason == "Aucun constat à rédiger."
    assert "aucune difficulté nette" in block and "Voir vos conseils" not in block
    page = user_client.get(reverse("advice")).content.decode()
    assert "aucune difficulté nette" in page and "Diagnostic" not in page
    # Les trois entraînements rapides sont passés : plus de bouton, la mention le dit.
    assert "Vous avez passé les 3 entraînements rapides disponibles." in page


@pytest.mark.django_db
def test_summary_alone_offers_the_next_test(user_client, user, tests_by_mode, formats):
    eligible_user(user, tests_by_mode)
    make_dev_test("rapide", rng=random.Random(40))
    services.advice_for(Attempt.objects.order_by("-finished_at").first())
    page = user_client.get(reverse("advice")).content.decode()
    assert "Passer l&#x27;entraînement suivant (entraînement rapide)" in page
    assert reverse("attempt_create") in page


@pytest.mark.django_db
def test_grouped_msl_errors_give_a_msl_family_advice(user_client, user, tests_by_mode):
    attempt = eligible_user(user, tests_by_mode, wrong=families("pronoms"))
    user_client.post(reverse("attempt_advice", args=[attempt.pk]))
    advice = Advice.objects.get()
    assert advice.source == Advice.Source.FALLBACK
    assert advice.fallback_reason == "LLM_PROVIDER=fake."
    assert ids(advice.findings) == ["msl_family:pronoms"]
    [item] = advice.content["items"]
    assert item["title"] == "Les pronoms"
    assert advice_content.validate(advice.content, advice.findings) == []
    for text in displayed_texts(advice.content):
        assert "%" not in text
    page = user_client.get(reverse("advice")).content.decode()
    for heading in ("Diagnostic", "La leçon", "Retour sur vos erreurs", "rédigés par IA"):
        assert heading in page
    for absent in ("Votre plan de la semaine", "Objectif du prochain test", "Le jour de"):
        assert absent not in page
    review = item["error_reviews"][0]
    error = next(
        e for e in advice.findings[0]["errors"] if e["test_item_id"] == review["test_item_id"]
    )
    link = reverse("attempt_review", args=[error["attempt_id"]]) + f"#q-{review['test_item_id']}"
    assert link in page


@pytest.mark.django_db
def test_duplicate_attempt_triggers_no_advice(user, tests_by_mode):
    eligible_user(user, tests_by_mode)
    duplicate = bare_attempt(user, tests_by_mode["rapide"][0], minutes=60 * 24)
    assert services.advice_for(duplicate) is None
    assert Advice.objects.count() == 0


@pytest.mark.django_db
def test_advice_is_generated_once_per_attempt(user, tests_by_mode):
    attempt = eligible_user(user, tests_by_mode, wrong=families("pronoms"))
    provider = StaticProvider(None)
    assert services.advice_for(attempt, provider).pk == services.advice_for(attempt, provider).pk
    assert len(provider.calls) == 1


@pytest.fixture
def no_level_filter(monkeypatch):
    """Les familles tirées au hasard peuvent tomber au-dessus du niveau MSL estimé + 1 :
    ces tests portent sur la rédaction, pas sur le filtre de niveau (testé plus haut)."""
    monkeypatch.setattr(findings, "msl_level", lambda attempts: None)


def two_findings_user(user, tests_by_mode):
    attempt = eligible_user(user, tests_by_mode, wrong=families("pronoms", "verbes"))
    found, _ = findings.compute_findings(user)
    assert {f["type"] for f in found} == {"msl_family"} and len(found) == 2
    return attempt, found


@pytest.mark.django_db
def test_valid_model_response_is_kept_and_billed(user, tests_by_mode, no_level_filter):
    attempt, found = two_findings_user(user, tests_by_mode)
    response = model_response(found)
    response["summary"] = "Rédigé par le modèle."
    provider = StaticProvider(response, input_tokens=5000, output_tokens=3000)
    advice = services.generate(attempt, provider)
    assert advice.source == Advice.Source.LLM and advice.fallback_reason == ""
    assert advice.content["summary"] == "Rédigé par le modèle."
    entry = UsageEntry.objects.get()
    assert (entry.kind, entry.model, entry.input_tokens, entry.output_tokens) == (
        "llm_advice",
        "claude-sonnet-5-5",
        5000,
        3000,
    )
    assert entry.cost_usd == Decimal("0.04") == advice.cost_usd
    call = provider.calls[0]
    assert "candidat" not in call["user"] and "@" not in call["user"]
    assert call["schema"] == advice_content.output_schema()
    sent = json.loads(call["user"].split("\n\n", 1)[1])
    assert "%" not in call["user"]
    assert not RATE_KEYS & {key for f in sent["findings"] for key in f}


@pytest.mark.django_db
@pytest.mark.parametrize(
    "bad", ["Vous êtes à 83 %.", "Un écart de 50 points.", "Refaites les questions manquées."]
)
def test_a_faulty_model_advice_is_removed_the_other_kept(user, tests_by_mode, no_level_filter, bad):
    attempt, found = two_findings_user(user, tests_by_mode)
    response = model_response(found)
    response["items"][0]["lesson"] = bad
    advice = services.generate(attempt, StaticProvider(response))
    assert advice.source == Advice.Source.LLM
    assert [i["finding_id"] for i in advice.content["items"]] == [
        response["items"][1]["finding_id"]
    ]
    assert "conseil 1 retiré" in advice.fallback_reason


@pytest.mark.django_db
@pytest.mark.parametrize("kind", ["not_json", "summary_with_figure", "every_item_invalid"])
def test_unusable_model_response_falls_back(user, tests_by_mode, no_level_filter, kind):
    attempt, found = two_findings_user(user, tests_by_mode)
    response = model_response(found)
    if kind == "not_json":
        response = None
    elif kind == "summary_with_figure":
        response["summary"] = "Vous réussissez 3 questions sur 5."
    else:
        for item in response["items"]:
            item["finding_id"] = "skill:CO:telepathie"
    advice = services.generate(attempt, StaticProvider(response))
    assert advice.source == Advice.Source.FALLBACK and advice.fallback_reason
    assert advice_content.validate(advice.content, advice.findings) == []
    assert UsageEntry.objects.filter(kind="llm_advice").count() == 1  # appel facturé, journalisé


@pytest.mark.django_db
def test_monthly_budget_reached_uses_fallback_without_calling(user, tests_by_mode, no_level_filter):
    attempt, found = two_findings_user(user, tests_by_mode)
    budget = SiteSettings.load().api_monthly_budget_usd
    UsageEntry.objects.create(kind="llm_advice", provider="anthropic", cost_usd=budget)
    provider = StaticProvider(model_response(found))
    advice = services.generate(attempt, provider)
    assert provider.calls == []
    assert advice.fallback_reason == "Plafond mensuel de l'API atteint."


@pytest.mark.django_db
def test_no_finding_means_no_call(user, tests_by_mode):
    attempt = eligible_user(user, tests_by_mode)
    provider = StaticProvider(None)
    advice = services.generate(attempt, provider)
    assert provider.calls == [] and advice.content["items"] == []


@pytest.mark.django_db
def test_anthropic_provider_without_key_falls_back(user, tests_by_mode, settings):
    attempt = eligible_user(user, tests_by_mode, wrong=families("pronoms"))
    settings.LLM_PROVIDER = "anthropic"
    settings.ANTHROPIC_API_KEY = ""
    advice = services.advice_for(attempt)
    assert advice.source == Advice.Source.FALLBACK
    assert "ANTHROPIC_API_KEY" in advice.fallback_reason


@pytest.mark.django_db
def test_second_advice_has_progress(user, tests_by_mode):
    attempt = eligible_user(user, tests_by_mode, wrong=families("pronoms"))
    services.advice_for(attempt)
    later = complete(user, tests_by_mode["moyen"][0], wrong=families("pronoms"), minutes=60)
    advice = services.advice_for(later)
    assert advice.findings[-1]["type"] == "progress"
    assert advice.findings[-1]["details"]["items"][0]["trend"]
    assert advice.content["items"][0]["progress_note"]


@pytest.mark.django_db
def test_advice_belongs_to_its_user(client, user, tests_by_mode):
    attempt = eligible_user(user, tests_by_mode, wrong=families("pronoms"))
    advice = services.advice_for(attempt)
    other = get_user_model().objects.create_user("autre", "autre@example.com", PASSWORD)
    client.force_login(other)
    assert client.post(reverse("attempt_advice", args=[attempt.pk])).status_code == 404
    assert client.get(reverse("advice_detail", args=[advice.pk])).status_code == 404
    response = client.post(reverse("advice_plan", args=[advice.pk]), {"key": "0-0", "done": "1"})
    assert response.status_code == 404


OLD_CONTENT = {
    "summary": "Ancien résumé.",
    "next_goal": "Ancien objectif.",
    "items": [
        {
            "finding_id": "language_point:pronoms_y_en",
            "title": "Les pronoms y et en",
            "diagnostic": "Ancien diagnostic.",
            "lesson": "Ancienne leçon.",
            "error_reviews": [],
            "exam_strategy": "Ancienne stratégie.",
            "practice": [
                {
                    "question": "Q ?",
                    "options": ["a", "b", "c", "d"],
                    "correct_index": 0,
                    "explanation": "E.",
                }
            ],
            "week_plan": [
                {
                    "action": "Action 1",
                    "minutes": 10,
                    "frequency": "chaque jour",
                    "resource_id": None,
                },
                {
                    "action": "Action 2",
                    "minutes": 15,
                    "frequency": "deux fois",
                    "resource_id": "tv5monde_tcf",
                },
            ],
            "progress_note": None,
        }
    ],
}


@pytest.mark.django_db
def test_old_advice_is_still_displayed_with_its_plan(user_client, user, tests_by_mode):
    attempt = complete(user, tests_by_mode["rapide"][0])
    advice = Advice.objects.create(
        user=user,
        attempt=attempt,
        attempt_ids=[attempt.pk],
        findings=[
            {
                "id": "language_point:pronoms_y_en",
                "type": "language_point",
                "label": "Structures de la langue : les pronoms y et en",
                "errors": [],
            }
        ],
        content=OLD_CONTENT,
        source=Advice.Source.FALLBACK,
    )
    page = user_client.get(reverse("advice_detail", args=[advice.pk])).content.decode()
    for text in ("Ancien objectif.", "Ancienne stratégie.", "Votre plan de la semaine", "Action 2"):
        assert text in page
    plan = reverse("advice_plan", args=[advice.pk])
    assert user_client.post(plan, {"key": "0-1", "done": "1"}).status_code == 204
    advice.refresh_from_db()
    assert advice.plan_done == ["0-1"]
    assert user_client.post(plan, {"key": "9-9", "done": "1"}).status_code == 400


@pytest.mark.django_db
def test_previous_advice_stays_available(user_client, user, tests_by_mode):
    first = services.advice_for(eligible_user(user, tests_by_mode, wrong=families("pronoms")))
    services.advice_for(complete(user, tests_by_mode["moyen"][0], minutes=60))
    page = user_client.get(reverse("advice")).content.decode()
    assert "Conseils précédents" in page
    assert reverse("advice_detail", args=[first.pk]) in page
    assert user_client.get(reverse("advice_detail", args=[first.pk])).status_code == 200


@pytest.mark.django_db
def test_deleting_the_account_deletes_advice(user, tests_by_mode):
    services.advice_for(eligible_user(user, tests_by_mode, wrong=families("pronoms")))
    user.delete()
    assert Advice.objects.count() == 0
