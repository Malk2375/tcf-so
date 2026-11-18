"""Réponses du relecteur collées ou en grille (6.4) ; import de corrections (6.4 bis)."""

import json
import random

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from bank import audio, synthesis
from bank.audio import AudioState, audio_state
from bank.content import item_fields
from bank.corrections import import_corrections
from bank.devtest import make_dev_draft
from bank.models import Item, Test
from bank.review import ReviewError, parse_grid, read_blind_answers
from bank.schema import StrictJSONError
from bank.synthesis import generate_next
from bank.tts import FakeTTSProvider


@pytest.fixture(autouse=True)
def fast_mp3(monkeypatch):
    def fake_encode(pcm: bytes) -> bytes:
        return b"MP3" + len(pcm).to_bytes(8, "big") + pcm[:64]

    monkeypatch.setattr(audio, "encode_mp3", fake_encode)
    monkeypatch.setattr(synthesis, "encode_mp3", fake_encode)


@pytest.fixture
def draft(formats, staff_user):
    return make_dev_draft("rapide", rng=random.Random(8), user=staff_user)


FIXED = "Explication corrigée : la bonne option est la seule qui convient au contexte."


def url(name, *args):
    return reverse(f"backoffice:{name}", args=args)


def items_of(test):
    return list(Item.objects.filter(test_item__test=test).order_by("test_item__slot"))


def by_section(test, section):
    return [i for i in items_of(test) if i.section == section]


# --- 6.4 : texte collé, bloc ```json```, ligne « grille » ---------------------------------------


def test_pasted_json_alone_or_in_a_code_block():
    data = {"answers": {"1": 2}, "doubts": {"3": "B et C."}}
    assert read_blind_answers(json.dumps(data)) == data
    pasted = f"Voici mes réponses :\n```json\n{json.dumps(data, indent=2)}\n```\nBonne chance."
    assert read_blind_answers(pasted) == data
    assert read_blind_answers(f"```\n{json.dumps(data)}\n```") == data


@pytest.mark.parametrize(
    "line",
    [
        "1A · 2C · 3B · 10D",
        "1A 2C 3B 10D",
        "1-A, 2-c ; 3.B / 10) D",
        "1 : A | 2 : C | 3 : B | 10 : D",
    ],
)
def test_grid_line_with_free_separators(line):
    assert parse_grid(line) == {"answers": {"1": 0, "2": 2, "3": 1, "10": 3}, "doubts": {}}
    assert read_blind_answers(line)["answers"]["10"] == 3


def test_grid_refuses_a_slot_given_twice_and_text_without_answers():
    with pytest.raises(StrictJSONError, match="deux fois"):
        parse_grid("1A · 1B")
    with pytest.raises(StrictJSONError, match="grille"):
        read_blind_answers("Je ne sais pas.")


def test_invalid_json_is_reported_instead_of_reading_a_grid():
    with pytest.raises(StrictJSONError, match="JSON invalide"):
        read_blind_answers('```json\n{"answers": {"1": 2A}}\n```')


def test_pasted_blind_answers_are_saved(staff_client, draft):
    items = items_of(draft)
    grid = " · ".join(f"{i.test_item.slot}{'ABCD'[i.correct_index]}" for i in items)
    wrong = items[0]
    grid = grid.replace(
        f"{wrong.test_item.slot}{'ABCD'[wrong.correct_index]} ",
        f"{wrong.test_item.slot}{'ABCD'[(wrong.correct_index + 1) % 4]} ",
        1,
    )
    html = staff_client.post(url("blind_answers", draft.pk), {"text": grid}, follow=True)
    assert "1 désaccord(s), 0 doute(s)" in html.content.decode()
    draft.refresh_from_db()
    assert len(draft.blind_check["answers"]) == len(items)


def test_blind_form_needs_exactly_one_source(staff_client, draft):
    upload = SimpleUploadedFile("r.json", b'{"answers": {}}', content_type="application/json")
    for data in ({}, {"text": "  "}, {"text": "1A", "file": upload}):
        response = staff_client.post(url("blind_answers", draft.pk), data, follow=True)
        assert "Choisissez" in response.content.decode()
    draft.refresh_from_db()
    assert draft.blind_check == {}


# --- 6.4 bis : import de corrections ------------------------------------------------------------


def as_import(test, item, **changes):
    fields = {**item_fields(item), "explanation": item.explanation, **changes}
    fields["tags"] = {k: v for k, v in fields["tags"].items() if k != "doc_length"}
    return {"slot": item.test_item.slot, **fields}


def document(test, items):
    return {
        "schema_version": 1,
        "format": test.format.code,
        "brief_id": test.import_batch.brief.brief_id,
        "items": items,
    }


def approve(test):
    Item.objects.filter(test_item__test=test).update(status=Item.Status.APPROVED)


def test_correction_replaces_only_the_given_slot(draft):
    approve(draft)
    msl = by_section(draft, "MSL")
    target, other = msl[0], msl[1]
    slot = target.test_item.slot
    draft.blind_check = {
        "answers": {str(slot): 1, str(other.test_item.slot): 2},
        "doubts": {str(slot): "Ambigu."},
    }
    draft.save()
    new = as_import(draft, target, explanation="Nouvelle explication, bien plus complète.")
    result = import_corrections(draft, json.dumps(document(draft, [new])))
    assert result.report.ok, result.report.as_dict()
    assert result.replaced == [slot]
    target.refresh_from_db()
    other.refresh_from_db()
    assert target.explanation == "Nouvelle explication, bien plus complète."
    assert target.status == Item.Status.DRAFT
    assert other.status == Item.Status.APPROVED
    assert target.test_item.test_id == draft.pk  # même emplacement, même item du test
    draft.refresh_from_db()
    assert draft.blind_check["answers"] == {str(other.test_item.slot): 2}
    assert draft.blind_check["doubts"] == {}
    assert draft.import_batch.report["corrections"][0]["slots"] == [slot]


def test_full_file_replaces_only_changed_items(draft):
    approve(draft)
    items = items_of(draft)
    target = by_section(draft, "MSL")[2]
    entries = [
        as_import(draft, i, explanation=FIXED) if i == target else as_import(draft, i)
        for i in items
    ]
    result = import_corrections(draft, json.dumps(document(draft, entries)))
    assert result.report.ok, result.report.as_dict()
    assert result.replaced == [target.test_item.slot]
    assert len(result.unchanged) == len(items) - 1
    statuses = {i.pk: i.status for i in items_of(draft)}
    assert statuses.pop(target.pk) == Item.Status.DRAFT
    assert set(statuses.values()) == {Item.Status.APPROVED}


def test_near_duplicate_ignores_the_replaced_item_itself(draft):
    target = by_section(draft, "MSL")[0]
    new = as_import(draft, target, stimulus_text=target.stimulus_text + " Vraiment.")
    result = import_corrections(draft, json.dumps(document(draft, [new])))
    assert result.report.ok, result.report.as_dict()
    assert result.replaced == [target.test_item.slot]


def test_near_duplicate_of_another_item_is_refused(draft):
    first, second = by_section(draft, "CE")[:2]
    copy = as_import(draft, second, stimulus_text=first.stimulus_text, question=first.question)
    result = import_corrections(draft, json.dumps(document(draft, [copy])))
    assert any("doublon" in e.message.lower() for e in result.report.errors)


@pytest.mark.parametrize(
    "mutate, message",
    [
        (lambda d, i: {**d, "brief_id": "autre-0001"}, "brief_id"),
        (lambda d, i: {**d, "items": [{**d["items"][0], "slot": 999}]}, "absent de ce test"),
        (lambda d, i: {**d, "items": [d["items"][0], d["items"][0]]}, "présent 2 fois"),
        (
            lambda d, i: {**d, "items": [{**d["items"][0], "correct_index": (i + 1) % 4}]},
            "le brief impose",
        ),
        (
            lambda d, i: {**d, "items": [{**d["items"][0], "options": ["Le", "le", "La", "Les"]}]},
            "double",
        ),
        (lambda d, i: {**d, "items": []}, "schéma"),
    ],
)
def test_invalid_corrections_change_nothing(draft, mutate, message):
    approve(draft)
    target = by_section(draft, "MSL")[0]
    good = as_import(draft, target, explanation=FIXED)
    data = mutate(document(draft, [good]), target.correct_index)
    before = [(i.explanation, i.status, i.options) for i in items_of(draft)]
    result = import_corrections(draft, json.dumps(data))
    assert not result.report.ok
    assert message in " ".join(e.message for e in result.report.errors)
    assert [(i.explanation, i.status, i.options) for i in items_of(draft)] == before


def test_all_or_nothing_when_one_of_two_items_is_invalid(draft):
    first, second = by_section(draft, "MSL")[:2]
    good = as_import(draft, first, explanation=FIXED)
    bad = as_import(draft, second, stimulus_text="Phrase sans blanc du tout.")
    result = import_corrections(draft, json.dumps(document(draft, [good, bad])))
    assert not result.report.ok
    first.refresh_from_db()
    assert first.explanation != FIXED


def test_corrected_co_script_makes_audio_stale(draft):
    generate_next(draft, provider=FakeTTSProvider())
    while not synthesis.audio_progress(draft).finished:
        generate_next(draft, provider=FakeTTSProvider())
    co = next(i for i in by_section(draft, "CO") if i.subtype != "question_reponse")
    assert audio_state(co) == AudioState.OK
    script = json.loads(json.dumps(co.audio_script))
    script["lines"][0]["text"] = script["lines"][0]["text"] + " Merci."
    result = import_corrections(
        draft, json.dumps(document(draft, [as_import(draft, co, audio_script=script)]))
    )
    assert result.report.ok, result.report.as_dict()
    co.refresh_from_db()
    assert audio_state(co) == AudioState.STALE


def test_corrections_refused_on_a_published_test(draft):
    Test.objects.filter(pk=draft.pk).update(status=Test.Status.PUBLISHED)
    draft.refresh_from_db()
    with pytest.raises(ReviewError):
        import_corrections(draft, "{}")


def test_corrections_page_paste_and_report(staff_client, draft):
    target = by_section(draft, "MSL")[0]
    page = staff_client.get(url("test_review", draft.pk)).content.decode()
    assert "Importer des corrections" in page
    bad = document(draft, [{**as_import(draft, target), "slot": 999}])
    response = staff_client.post(url("corrections", draft.pk), {"text": json.dumps(bad)})
    assert response.status_code == 422
    html = response.content.decode()
    assert "Corrections refusées" in html and "Emplacement 999" in html
    good = document(draft, [as_import(draft, target, explanation=FIXED)])
    pasted = f"Voici l'item corrigé :\n```json\n{json.dumps(good, ensure_ascii=False)}\n```"
    response = staff_client.post(url("corrections", draft.pk), {"text": pasted}, follow=True)
    assert f"remplacé(s), à relire et approuver : {target.test_item.slot}" in (
        response.content.decode()
    )
    unchanged = staff_client.post(url("corrections", draft.pk), {"text": pasted}, follow=True)
    assert "Aucune correction" in unchanged.content.decode()


# --- Imports antérieurs au lien ImportBatch → Brief -----------------------------------------------


def unlink_brief(test):
    batch = test.import_batch
    brief = batch.brief
    batch.brief = None
    batch.save(update_fields=["brief"])
    test.refresh_from_db()
    return brief


def test_data_migration_fills_the_missing_brief_link(draft):
    import importlib

    from django.apps import apps

    brief = unlink_brief(draft)
    migration = importlib.import_module("bank.migrations.0004_fill_import_batch_brief")
    migration.fill_briefs(apps, None)
    draft.import_batch.refresh_from_db()
    assert draft.import_batch.brief == brief


def test_data_migration_falls_back_on_the_file_name(draft):
    import importlib

    from django.apps import apps

    brief = unlink_brief(draft)
    batch = draft.import_batch
    Test.objects.filter(pk=draft.pk).update(import_batch=None)  # plus de test pour le retrouver
    migration = importlib.import_module("bank.migrations.0004_fill_import_batch_brief")
    migration.fill_briefs(apps, None)
    batch.refresh_from_db()
    assert batch.brief == brief  # trouvé par « items_<brief_id>.json »


def test_corrections_accept_the_file_brief_when_the_link_is_missing(draft):
    brief = unlink_brief(draft)
    target = by_section(draft, "MSL")[0]
    data = {**document_for(draft, brief), "items": [as_import(draft, target, explanation=FIXED)]}
    result = import_corrections(draft, json.dumps(data))
    assert result.report.ok, result.report.as_dict()
    assert result.replaced == [target.test_item.slot]
    draft.import_batch.refresh_from_db()
    assert draft.import_batch.brief == brief  # lien réparé


def test_corrections_refuse_a_brief_whose_slots_differ(draft, staff_user):
    from bank.brief import create_brief

    unlink_brief(draft)
    other = create_brief(draft.format, user=staff_user, rng=random.Random(3))
    other.content = {**other.content, "slots": other.content["slots"][:-1]}
    other.save()
    target = by_section(draft, "MSL")[0]
    data = {**document_for(draft, other), "items": [as_import(draft, target, explanation=FIXED)]}
    result = import_corrections(draft, json.dumps(data))
    assert "ne correspondent pas" in " ".join(e.message for e in result.report.errors)
    target.refresh_from_db()
    assert target.explanation != FIXED


def document_for(test, brief):
    return {"schema_version": 1, "format": test.format.code, "brief_id": brief.brief_id}


# --- Question sans réponse du relecteur : « Non contrôlée » (6.4) ---------------------------------


def test_corrected_item_is_unchecked_not_a_disagreement(staff_client, draft):
    items = items_of(draft)
    draft.blind_check = {
        "answers": {str(i.test_item.slot): i.correct_index for i in items},
        "doubts": {},
        "received_at": "2026-11-18T10:00:00+00:00",
    }
    draft.save()
    target = by_section(draft, "MSL")[0]
    new = as_import(draft, target, explanation=FIXED)
    assert import_corrections(draft, json.dumps(document(draft, [new]))).report.ok

    rows = {r.item.pk: r for r in review_rows(draft)}
    assert rows[target.pk].unchecked and not rows[target.pk].disagreement
    assert not rows[target.pk].flagged
    assert sum(r.disagreement for r in rows.values()) == 0

    html = staff_client.get(url("test_review", draft.pk)).content.decode()
    card = html.split(f'id="item-{target.pk}"')[1].split("</article>")[0]
    assert "Non contrôlée" in card and "Désaccord" not in card and "pas de réponse" not in card
    assert "1 question non contrôlée par le relecteur (hors désaccords)" in html
    bilan = html.split('id="bilan-titre"')[1].split("</section>")[0]
    assert 'Désaccords</dt><dd class="text-xl font-bold tabular-nums ">0<' in bilan


def test_no_unchecked_badge_before_the_reviewer_answers(staff_client, draft):
    html = staff_client.get(url("test_review", draft.pk)).content.decode()
    assert "Non contrôlée" not in html
    assert all(not r.unchecked for r in review_rows(draft))


def review_rows(test):
    from bank.review import rows

    test.refresh_from_db()
    return rows(test)


# --- Audio : un bouton pour le test, en haut ; « Régénérer cet audio » si l'audio existe (7.4) --


def test_generate_button_sits_in_the_bilan_and_no_per_item_button_without_audio(
    staff_client, draft
):
    html = staff_client.get(url("test_review", draft.pk)).content.decode()
    bilan = html.split('id="bilan-titre"')[1].split("</section>")[0]
    assert "Générer l'audio du test" in bilan
    first_card = html.index('<article id="item-')
    assert html.index("Générer l'audio du test") < first_card
    assert "Régénérer cet audio" not in html
    assert "Générer l'audio</button>" not in html  # plus de bouton question par question


def test_regenerate_button_appears_once_audio_exists(staff_client, draft):
    while not synthesis.audio_progress(draft).finished:
        generate_next(draft, provider=FakeTTSProvider())
    html = staff_client.get(url("test_review", draft.pk)).content.decode()
    co = by_section(draft, "CO")
    assert html.count("Régénérer cet audio") == len(co)
    for item in co:
        card = html.split(f'id="item-{item.pk}"')[1].split("</article>")[0]
        assert "Régénérer cet audio" in card


def test_per_item_synthesis_refused_without_existing_audio(staff_client, draft):
    from bank.models import AudioAsset

    co = by_section(draft, "CO")[0]
    response = staff_client.post(url("item_audio", draft.pk, co.pk), follow=True)
    assert "pas encore d&#x27;audio" in response.content.decode()
    assert not AudioAsset.objects.filter(item=co).exists()
