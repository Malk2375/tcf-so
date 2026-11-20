import copy
import json
import random
from unittest import mock

import pytest
from django.db import IntegrityError

from bank.brief import create_brief
from bank.constants import LETTERS, MAX_IMPORT_FILE_BYTES, QUESTION_REPONSE_TEXT
from bank.content import word_count
from bank.devdata import fake_import_file
from bank.importer import import_files
from bank.models import ImportBatch, Item, Test, TestItem
from tests.conftest import as_file
from usage.models import SiteSettings, UsageEntry

pytestmark = pytest.mark.django_db


def run(*documents):
    files = [
        d if isinstance(d, tuple) else as_file(d, f"f{i}.json") for i, d in enumerate(documents)
    ]
    return import_files(files)


def messages(result, kind="errors"):
    return [issue.message for issue in getattr(result.report, kind)]


def has(result, fragment, kind="errors"):
    return any(fragment in message for message in messages(result, kind))


def first(data, section, level=None, subtype=None):
    for item in data["items"]:
        matches = (item["section"], item["level"], item["subtype"])
        if matches == (section, level or item["level"], subtype or item["subtype"]):
            return item
    raise LookupError((section, level, subtype))


def assert_nothing_written():
    """Import refusé : ni item ni test, seulement un ImportBatch « rejected » avec le rapport."""
    assert Item.objects.count() == 0
    assert Test.objects.count() == 0
    assert TestItem.objects.count() == 0
    batch = ImportBatch.objects.get()
    assert batch.status == ImportBatch.Status.REJECTED
    assert batch.report["errors"]


@pytest.fixture
def valid(make_import):
    return make_import("rapide")


@pytest.fixture
def with_question_reponse(formats):
    """Brief rapide dont l'emplacement CO A1 est une question-réponse."""
    rng = random.Random(7)
    brief = create_brief(formats["rapide"], rng=rng)
    slot = next(s for s in brief.content["slots"] if s["section"] == "CO" and s["level"] == "A1")
    slot.update(
        subtype="question_reponse",
        options_mode="audio_letters",
        target={"audio_seconds": [2, 8]},
    )
    brief.save()
    return brief, fake_import_file(brief.content, rng)


# --- Import valide ------------------------------------------------------------------------------


def test_valid_import_creates_draft_test(valid):
    brief, data = valid
    result = run(data)
    assert result.report.errors == []
    test = result.test
    assert test.status == Test.Status.DRAFT
    assert test.number == brief.number
    assert test.format.mode == "rapide"
    assert test.import_batch.status == ImportBatch.Status.ACCEPTED
    assert Item.objects.filter(status=Item.Status.DRAFT).count() == 24

    for section, n in [("CO", 9), ("MSL", 6), ("CE", 9)]:
        test_items = list(test.test_items.filter(section=section).order_by("position"))
        assert [t.position for t in test_items] == list(range(1, n + 1))
        levels = [t.item.level for t in test_items]
        assert levels == sorted(levels)
    by_slot = {item["slot"]: item for item in data["items"]}
    for test_item in test.test_items.select_related("item"):
        source = by_slot[test_item.slot]
        assert test_item.item.correct_index == source["correct_index"]
        assert test_item.item.tags["doc_length"] in {"court", "moyen", "long"}
        assert len(test_item.item.content_hash) == 64
        if test_item.section != "CO":
            assert test_item.item.word_count == word_count(source["stimulus_text"])


def test_doc_length_by_level(valid):
    _, data = valid
    test = run(data).test
    for test_item in test.test_items.select_related("item"):
        expected = {"A1": "court", "A2": "court", "B1": "moyen", "B2": "moyen"}.get(
            test_item.item.level, "long"
        )
        assert test_item.item.tags["doc_length"] == expected


def test_import_split_in_three_files(valid):
    _, data = valid
    files = []
    for section in ["CO", "MSL", "CE"]:
        part = {**data, "items": [i for i in data["items"] if i["section"] == section]}
        files.append(part)
    result = run(*files)
    assert result.report.errors == []
    assert result.test.test_items.count() == 24


def test_question_reponse_import(with_question_reponse):
    _, data = with_question_reponse
    item = first(data, "CO", "A1")
    assert item["question"] == QUESTION_REPONSE_TEXT
    assert run(data).report.errors == []


def test_reference_examples_pass_item_checks(formats):
    """Les 36 exemples de référence passent les contrôles item par item (hors comptes du format)."""
    from bank.importer import ImportReport, _check_item
    from tests.conftest import load_fixture

    report = ImportReport()
    for item in load_fixture("exemples_reference.json")["items"]:
        _check_item({**item, "_file": "ref"}, report)
    assert report.errors == []


# --- Règle 1 : JSON, schéma, taille -------------------------------------------------------------


def test_invalid_json():
    result = import_files([("cassé.json", b'{"schema_version": 1,')])
    assert has(result, "JSON invalide")
    assert_nothing_written()


def test_duplicate_keys_are_rejected():
    result = import_files([("double.json", b'{"format": "a", "format": "b"}')])
    assert has(result, "clé en double")


def test_nan_is_rejected():
    result = import_files([("nan.json", b'{"slot": NaN}')])
    assert has(result, "NaN")


def test_not_utf8():
    result = import_files([("latin1.json", '{"é": 1}'.encode("latin-1"))])
    assert has(result, "UTF-8")


def test_schema_violation(valid):
    _, data = valid
    del data["items"][0]["explanation"]
    data["items"][1]["correct_index"] = 4
    result = run(data)
    assert has(result, "Non conforme au schéma — items[0]")
    assert has(result, "Non conforme au schéma — items[1].correct_index")
    assert_nothing_written()


def test_file_too_large(valid):
    _, data = valid
    raw = json.dumps(data).encode()
    raw += b" " * (MAX_IMPORT_FILE_BYTES + 1 - len(raw))
    result = import_files([("gros.json", raw)])
    assert messages(result) == ["Fichier de plus de 2 Mo."]
    assert_nothing_written()


def test_too_many_files(valid):
    _, data = valid
    result = run(data, data, data, data)
    assert has(result, "1 à 3 fichiers")


# --- Règle 2 : format et brief_id ---------------------------------------------------------------


def test_unknown_format(valid, formats):
    _, data = valid
    formats["rapide"].is_active = False
    formats["rapide"].save()
    assert has(run(data), "Format inconnu : tcf_tp_so_rapide")
    assert_nothing_written()


def test_different_brief_ids(valid):
    _, data = valid
    other = {**data, "brief_id": "tcf_tp_so_rapide-0099", "items": data["items"][12:]}
    result = run({**data, "items": data["items"][:12]}, other)
    assert has(result, "brief_id différents")
    assert_nothing_written()


def test_unknown_brief(valid):
    _, data = valid
    data["brief_id"] = "tcf_tp_so_rapide-0042"
    assert has(run(data), "Brief inconnu")


def test_brief_already_imported(valid):
    _, data = valid
    assert run(data).test is not None
    result = run(data)
    assert has(result, "a déjà donné lieu à un test")
    assert Test.objects.count() == 1


# --- Règle 3 : nombre d'items par épreuve et par niveau -----------------------------------------


def test_missing_item(valid):
    _, data = valid
    item = first(data, "CE", "B1")
    data["items"].remove(item)
    assert has(run(data), "CE B1 : 1 item(s) au lieu de 2.")
    assert_nothing_written()


def test_item_at_wrong_level(valid):
    _, data = valid
    item = first(data, "MSL", "C2")
    item["level"] = "C1"
    result = run(data)
    assert has(result, "MSL C1 : 2 item(s) au lieu de 1.")
    assert has(result, "Le brief attend MSL C2 à cet emplacement")


def test_duplicate_and_unknown_slots(valid):
    _, data = valid
    data["items"][1]["slot"] = data["items"][0]["slot"]
    data["items"][2]["slot"] = 99
    result = run(data)
    assert has(result, "Emplacement présent 2 fois.")
    assert has(result, "Emplacement absent du brief.")


# --- Règle 4 : options et bonne réponse ---------------------------------------------------------


def test_duplicate_options_after_normalization(valid):
    _, data = valid
    item = first(data, "CE")
    item["options"][1] = "  " + item["options"][0].upper() + " "
    assert has(run(data), "Options en double.")
    assert_nothing_written()


def test_correct_index_differs_from_brief_letter(valid):
    brief, data = valid
    item = data["items"][0]
    expected = brief.content["slots"][0]["correct_letter"]
    item["correct_index"] = (LETTERS.index(expected) + 1) % 4
    result = run(data)
    assert has(result, f"le brief impose {expected}")
    assert_nothing_written()


def test_wrong_options_mode(valid):
    _, data = valid
    first(data, "CO", "B1")["options_mode"] = "audio_letters"
    assert has(run(data), "options_mode doit valoir « text »")


def test_question_reponse_requires_fixed_question(with_question_reponse):
    _, data = with_question_reponse
    first(data, "CO", "A1")["question"] = "Que répondez-vous ?"
    assert has(run(data), f"La question doit être « {QUESTION_REPONSE_TEXT} »")


def test_subtype_not_allowed_for_level(valid):
    _, data = valid
    first(data, "CE", "A1")["subtype"] = "texte_litteraire"
    result = run(data)
    assert has(result, "Sous-type « texte_litteraire » non autorisé en CE A1.")


# --- Règle 5 : un seul blanc en MSL -------------------------------------------------------------


@pytest.mark.parametrize(
    "sentence", ["Il ___ venu et il ___ parti hier soir.", "Il ____ venu hier."]
)
def test_msl_blank_count(valid, sentence):
    _, data = valid
    first(data, "MSL", "A1")["stimulus_text"] = sentence
    assert has(run(data), "exactement un blanc « ___ »")
    assert_nothing_written()


# --- Règle 6 : script de CO ---------------------------------------------------------------------


def test_undeclared_speaker(valid):
    _, data = valid
    first(data, "CO")["audio_script"]["lines"][0]["speaker"] = "M9"
    assert has(run(data), "Locuteur non déclaré : M9.")
    assert_nothing_written()


def test_more_than_three_speakers(valid):
    _, data = valid
    script = first(data, "CO", "C2")["audio_script"]
    script["speakers"] = {k: {"gender": "F"} for k in ["F1", "F2", "F3", "M1"]}
    script["lines"][0]["speaker"] = "F1"
    assert has(run(data), "audio_script")  # maxProperties du schéma


def test_missing_script(valid):
    _, data = valid
    first(data, "CO")["audio_script"] = None
    assert has(run(data), "Non conforme au schéma")


# --- Règle 7 : longueur ---------------------------------------------------------------------------


def test_ce_text_too_long(valid):
    _, data = valid
    item = first(data, "CE", "A1")  # 20 à 40 mots, tolérance : 46 au plus
    item["stimulus_text"] = " ".join(["mot"] * 47)
    assert has(run(data), "Longueur hors fourchette : 47 mots pour 20 à 40 mots")
    assert_nothing_written()


def test_ce_text_within_tolerance(valid):
    _, data = valid
    first(data, "CE", "A1")["stimulus_text"] = " ".join(f"mot{i}" for i in range(46))
    assert not has(run(data), "Longueur hors fourchette")


def test_msl_sentence_too_short(valid):
    _, data = valid
    first(data, "MSL", "C2")["stimulus_text"] = "Il ___ parti."  # 2 mots pour 10 à 35
    assert has(run(data), "Longueur hors fourchette : 2 mots pour 10 à 35 mots")


def test_co_script_too_short(valid):
    _, data = valid
    script = first(data, "CO", "C2")["audio_script"]  # 45 à 70 s, soit 38,25 s au moins
    script["lines"] = [{"speaker": "F1", "text": "Bonjour à tous."}]
    assert has(run(data), "Longueur hors fourchette : 1 s pour 45 à 70 s")


def test_question_reponse_too_long(with_question_reponse):
    _, data = with_question_reponse
    script = first(data, "CO", "A1")["audio_script"]
    script["lines"] = [{"speaker": "F1", "text": "x" * 150}]  # 10 s pour 2 à 8 s
    assert has(run(data), "Longueur hors fourchette : 10 s pour 2 à 8 s")


# --- Règles 8 et 9 : doublons -------------------------------------------------------------------


def test_exact_duplicate_in_bank(make_import):
    _, data = make_import()
    run(data)
    brief, data2 = make_import()
    data2["items"][5] = {**copy.deepcopy(data["items"][5]), "slot": data2["items"][5]["slot"]}
    data2["items"][5]["correct_index"] = LETTERS.index(brief.content["slots"][5]["correct_letter"])
    # Bonne réponse remise à la lettre du nouveau brief : le hachage peut rester identique.
    data2["items"][5]["options"] = data["items"][5]["options"]
    result = run(data2)
    assert has(result, "Doublon exact d'un item déjà en banque.")
    assert Test.objects.count() == 1


def test_exact_duplicate_in_file(valid):
    _, data = valid
    a2_items = [i for i in data["items"] if i["section"] == "CE" and i["level"] == "A2"]
    duplicate = {**copy.deepcopy(a2_items[0]), "slot": a2_items[1]["slot"]}
    duplicate["correct_index"] = a2_items[1]["correct_index"]
    duplicate["options"] = a2_items[0]["options"]
    data["items"][data["items"].index(a2_items[1])] = duplicate
    result = run(data)
    assert has(result, f"Doublon exact de l'emplacement {a2_items[0]['slot']}.")
    assert_nothing_written()


def test_near_duplicate_in_file(valid):
    _, data = valid
    b1_items = [i for i in data["items"] if i["section"] == "CE" and i["level"] == "B1"]
    words = b1_items[0]["stimulus_text"].split()
    words[3] = "différent"
    b1_items[1]["stimulus_text"] = " ".join(words)
    b1_items[1]["question"] = b1_items[0]["question"]
    result = run(data)
    assert has(result, f"Quasi-doublon de l'emplacement {b1_items[0]['slot']}")
    assert_nothing_written()


def test_near_duplicate_in_bank(make_import):
    _, data = make_import()
    run(data)
    _, data2 = make_import()
    source = first(data, "CE", "C1")
    target = first(data2, "CE", "C1")
    target["stimulus_text"] = source["stimulus_text"] + " Ajout."
    target["question"] = source["question"]
    result = run(data2)
    assert has(result, "Quasi-doublon de l'item")
    assert Test.objects.count() == 1


def test_different_items_are_not_near_duplicates(make_import):
    _, data = make_import()
    run(data)
    _, data2 = make_import()
    assert run(data2).report.errors == []


# --- Atomicité ----------------------------------------------------------------------------------


def test_single_error_in_last_item_writes_nothing(valid):
    _, data = valid
    data["items"][-1]["options"][3] = data["items"][-1]["options"][0]
    result = run(data)
    assert len(result.report.errors) >= 1
    assert_nothing_written()


def test_database_error_during_save_writes_nothing(valid):
    _, data = valid
    real_create = TestItem.objects.create
    calls = {"n": 0}

    def flaky_create(**kwargs):
        calls["n"] += 1
        if calls["n"] == 10:
            raise IntegrityError("simulée")
        return real_create(**kwargs)

    with mock.patch.object(TestItem.objects, "create", side_effect=flaky_create):
        result = run(data)
    assert result.test is None
    assert has(result, "Enregistrement impossible")
    assert_nothing_written()


def test_report_lists_every_error(valid):
    _, data = valid
    first(data, "MSL", "A1")["stimulus_text"] = "Pas de blanc ici mais assez de mots ___ ___."
    first(data, "CE", "A1")["stimulus_text"] = "trop court"
    first(data, "CO")["audio_script"]["lines"][0]["speaker"] = "X1"
    result = run(data)
    assert len(result.report.errors) >= 3
    assert all(issue.slot for issue in result.report.errors)


# --- Avertissements -----------------------------------------------------------------------------


def test_warning_long_correct_answer(valid):
    _, data = valid
    item = first(data, "CE", "B2")
    correct = item["correct_index"]
    item["options"][correct] += " avec beaucoup de mots en plus pour la rallonger"
    result = run(data)
    assert result.test is not None
    assert has(result, f"La bonne réponse ({'ABCD'[correct]},", "warnings")


def test_no_warning_for_a_long_wrong_answer(valid):
    _, data = valid
    item = first(data, "CE", "B2")
    wrong = (item["correct_index"] + 1) % 4
    item["options"][wrong] += " avec beaucoup de mots en plus pour la rallonger"
    assert not has(run(data), "bien plus longue", "warnings")


def test_warning_correct_answer_often_strictly_longest_in_co_and_ce_only(valid):
    _, data = valid
    for item in data["items"]:
        if item["section"] in ("MSL", "CE"):
            item["options"][item["correct_index"]] += " et un peu plus"
    result = run(data)
    assert has(result, "CE : la bonne réponse est strictement l'option la plus longue", "warnings")
    assert not has(result, "MSL : la bonne réponse", "warnings")
    assert not any(
        "MSL" in w.message and "plus longue" in w.message for w in result.report.warnings
    )


def test_warning_tags_out_of_vocabulary(valid):
    _, data = valid
    first(data, "CE", "A1")["tags"]["theme"] = "theme_inconnu"
    first(data, "CO", "B2")["tags"]["skill"] = "grammaire"
    result = run(data)
    assert result.test is not None
    assert has(result, "Thème « theme_inconnu » hors de la liste", "warnings")
    assert has(result, "Compétence « grammaire » hors vocabulaire en CO.", "warnings")


def test_language_point_valid_when_the_item_level_is_in_its_levels(valid):
    """bank/data/language_points.json : un point vaut pour les niveaux de sa ligne (4.4)."""
    _, data = valid
    c1 = first(data, "MSL", "C1")
    c1["tags"]["language_point"] = "prepositions_soutenues"  # C1 et C2 : valide
    a1 = first(data, "MSL", "A1")
    a1["tags"]["language_point"] = "paronymes"  # C1 seulement : hors liste en A1
    result = run(data)
    assert result.test is not None
    assert not has(result, "« prepositions_soutenues » hors de la liste", "warnings")
    assert has(result, "Point de langue « paronymes » hors de la liste du niveau A1.", "warnings")


def test_warning_tts_quota(valid):
    _, data = valid
    settings_row = SiteSettings.load()
    settings_row.tts_monthly_char_quota = 1000
    settings_row.save()
    UsageEntry.objects.create(kind="tts", provider="fake", chars=900)
    result = run(data)
    assert result.test is not None
    assert has(result, "Synthèse vocale :", "warnings")


def test_no_warning_when_quota_is_enough(valid):
    _, data = valid
    assert not has(run(data), "Synthèse vocale", "warnings")


def test_fake_file_has_no_option_length_warning(valid):
    _, data = valid
    assert not has(run(data), "bien plus longue", "warnings")
