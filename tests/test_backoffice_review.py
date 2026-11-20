"""Back-office : relecture, contrôle à l'aveugle, audio en un clic, publication (6.4, 7.4, 11)."""

import json
import random

import pytest
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from bank import audio, synthesis
from bank.devtest import make_dev_draft
from bank.models import Item, Test
from exam import services
from tests.conftest import PASSWORD
from usage.models import SiteSettings, UsageEntry

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


def items_of(test):
    return list(Item.objects.filter(test_item__test=test).order_by("test_item__slot"))


def approve_all(client, test):
    for item in items_of(test):
        response = client.post(url("item_status", test.pk, item.pk), {"action": "approve"}, **HTMX)
        assert response.status_code == 200


def generate_audio_like_the_page(client, test, limit=50):
    """Un clic, puis la page enchaîne les appels HTMX jusqu'à l'ordre de recharger."""
    for calls in range(1, limit):
        response = client.post(url("audio_next", test.pk), **HTMX)
        assert response.status_code == 200
        if response.get("HX-Refresh") == "true":
            return calls, response
        assert 'hx-trigger="load"' in response.content.decode()
    raise AssertionError("La génération ne se termine pas.")


def blind_upload(data):
    raw = json.dumps(data).encode() if not isinstance(data, bytes) else data
    return SimpleUploadedFile("reponses.json", raw, content_type="application/json")


# --- Critère d'acceptation : import → relecture → audio en un clic → publication -------------


def test_import_review_one_click_audio_publish_then_users_see_it(staff_client, draft):
    assert draft.status == Test.Status.DRAFT
    page = staff_client.get(url("test_review", draft.pk)).content.decode()
    assert "Générer l&#x27;audio du test" in page or "Générer l'audio du test" in page
    assert "question(s) non approuvée(s)" in page

    approve_all(staff_client, draft)
    n_co = draft.test_items.filter(section="CO").count()
    calls, _ = generate_audio_like_the_page(staff_client, draft)
    assert calls == n_co
    assert UsageEntry.objects.filter(kind="tts").count() == n_co

    response = staff_client.post(url("publish", draft.pk), follow=True)
    assert "Test publié" in response.content.decode()
    draft.refresh_from_db()
    assert draft.status == Test.Status.PUBLISHED and draft.published_at
    assert {item.status for item in items_of(draft)} == {Item.Status.PUBLISHED}

    user = get_user_model().objects.create_user("c", "c@example.com", PASSWORD)
    assert services.proposed_test(user, draft.format) == draft


def test_closing_the_page_then_clicking_again_resumes(staff_client, draft):
    staff_client.post(url("audio_next", draft.pk), **HTMX)
    staff_client.post(url("audio_next", draft.pk), **HTMX)
    # Page fermée. Rouverte, elle propose de reprendre.
    page = staff_client.get(url("test_review", draft.pk)).content.decode()
    assert "Reprendre la génération de l" in page
    n_co = draft.test_items.filter(section="CO").count()
    assert "2 / " + str(n_co) in page
    calls, _ = generate_audio_like_the_page(staff_client, draft)
    assert calls == n_co - 2
    assert UsageEntry.objects.count() == n_co


def test_quota_message_in_page_and_nothing_synthesized(staff_client, draft):
    site = SiteSettings.load()
    site.tts_monthly_char_quota = 100
    site.save()
    page = staff_client.get(url("test_review", draft.pk)).content.decode()
    assert "Quota gratuit du mois insuffisant pour ce test." in page
    response = staff_client.post(url("audio_next", draft.pk), **HTMX)
    html = response.content.decode()
    assert "Quota gratuit du mois insuffisant pour ce test." in html
    assert 'hx-trigger="load"' not in html
    assert "HX-Refresh" not in response
    assert UsageEntry.objects.count() == 0


# --- Publication refusée ------------------------------------------------------------------------


def test_publication_refused_with_unapproved_item(staff_client, draft):
    approve_all(staff_client, draft)
    generate_audio_like_the_page(staff_client, draft)
    item = items_of(draft)[3]
    staff_client.post(url("item_status", draft.pk, item.pk), {"action": "reject"})
    response = staff_client.post(url("publish", draft.pk), follow=True)
    assert "Publication refusée" in response.content.decode()
    draft.refresh_from_db()
    assert draft.status == Test.Status.DRAFT


def test_publication_refused_with_missing_or_stale_audio(staff_client, draft):
    approve_all(staff_client, draft)
    response = staff_client.post(url("publish", draft.pk), follow=True)
    assert "audio(s) de CO manquant(s)" in response.content.decode()

    generate_audio_like_the_page(staff_client, draft)
    co_item = next(i for i in items_of(draft) if i.section == "CO")
    script = co_item.audio_script
    script["lines"][0]["text"] = script["lines"][0]["text"].rstrip(".") + " donc."
    form = edit_form(co_item, audio_script=json.dumps(script, ensure_ascii=False))
    staff_client.post(url("item_edit", draft.pk, co_item.pk), form)
    staff_client.post(url("item_status", draft.pk, co_item.pk), {"action": "approve"})
    page = staff_client.get(url("test_review", draft.pk)).content.decode()
    assert "Audio périmé" in page
    response = staff_client.post(url("publish", draft.pk), follow=True)
    assert "périmé(s)" in response.content.decode()
    draft.refresh_from_db()
    assert draft.status == Test.Status.DRAFT
    # Régénérer cet item suffit pour publier.
    staff_client.post(url("item_audio", draft.pk, co_item.pk))
    staff_client.post(url("publish", draft.pk))
    draft.refresh_from_db()
    assert draft.status == Test.Status.PUBLISHED


def test_published_test_is_frozen_then_archived(staff_client, draft):
    approve_all(staff_client, draft)
    generate_audio_like_the_page(staff_client, draft)
    staff_client.post(url("publish", draft.pk))
    item = items_of(draft)[0]
    before = item.question
    staff_client.post(url("item_status", draft.pk, item.pk), {"action": "reject"})
    response = staff_client.get(url("item_edit", draft.pk, item.pk))
    assert response.status_code == 302
    staff_client.post(url("item_edit", draft.pk, item.pk), edit_form(item, question="Autre ?"))
    item.refresh_from_db()
    assert (item.status, item.question) == (Item.Status.PUBLISHED, before)
    assert UsageEntry.objects.count() == draft.test_items.filter(section="CO").count()
    staff_client.post(url("audio_next", draft.pk))
    assert UsageEntry.objects.count() == draft.test_items.filter(section="CO").count()

    staff_client.post(url("archive", draft.pk))
    draft.refresh_from_db()
    assert draft.status == Test.Status.ARCHIVED
    user = get_user_model().objects.create_user("c", "c@example.com", PASSWORD)
    assert services.proposed_test(user, draft.format) is None


# --- Modification d'un item ---------------------------------------------------------------------


def edit_form(item, **changes):
    data = {
        "question": item.question,
        "option_0": item.options[0],
        "option_1": item.options[1],
        "option_2": item.options[2],
        "option_3": item.options[3],
        "correct_index": item.correct_index,
        "explanation": item.explanation,
    }
    if item.section == "CO":
        data["audio_script"] = json.dumps(item.audio_script, ensure_ascii=False)
    else:
        data["stimulus_text"] = item.stimulus_text
    data.update(changes)
    return data


def test_edit_item_runs_import_checks(staff_client, draft):
    item = next(i for i in items_of(draft) if i.section == "CE")
    staff_client.post(url("item_status", draft.pk, item.pk), {"action": "approve"})
    response = staff_client.post(
        url("item_edit", draft.pk, item.pk), edit_form(item, option_1=item.options[0])
    )
    assert response.status_code == 200
    # Contrôle du schéma d'import (options toutes différentes).
    assert "non-unique" in response.content.decode()
    response = staff_client.post(
        url("item_edit", draft.pk, item.pk), edit_form(item, stimulus_text="Trop court.")
    )
    assert "Longueur hors fourchette" in response.content.decode()
    unchanged = Item.objects.get(pk=item.pk)
    assert unchanged.status == Item.Status.APPROVED and unchanged.options == item.options


def test_edit_item_saves_and_goes_back_to_draft(staff_client, draft):
    item = next(i for i in items_of(draft) if i.section == "MSL")
    staff_client.post(url("item_status", draft.pk, item.pk), {"action": "approve"})
    old_hash = item.content_hash
    new_correct = (item.correct_index + 1) % 4
    response = staff_client.post(
        url("item_edit", draft.pk, item.pk),
        edit_form(
            item,
            explanation="Nouvelle explication, assez longue pour le schéma.",
            correct_index=new_correct,
        ),
    )
    assert response.status_code == 302
    item.refresh_from_db()
    assert item.status == Item.Status.DRAFT
    assert item.correct_index == new_correct
    assert item.explanation.startswith("Nouvelle explication")
    assert item.content_hash == old_hash  # explication et bonne réponse hors empreinte
    response = staff_client.post(
        url("item_edit", draft.pk, item.pk), edit_form(item, option_2="Une toute autre option")
    )
    item.refresh_from_db()
    assert item.content_hash != old_hash


def test_htmx_status_change_returns_the_item_card(staff_client, draft):
    item = items_of(draft)[0]
    response = staff_client.post(
        url("item_status", draft.pk, item.pk), {"action": "approve"}, **HTMX
    )
    html = response.content.decode()
    assert f'id="item-{item.pk}"' in html and "Approuvé" in html
    assert "<html" not in html
    assert (
        staff_client.post(url("item_status", draft.pk, item.pk), {"action": "x"}).status_code == 400
    )


# --- Contrôle à l'aveugle (6.4) -----------------------------------------------------------------


def test_blind_json_has_no_answers(staff_client, draft):
    response = staff_client.get(url("blind_json", draft.pk))
    assert response["Content-Disposition"].startswith('attachment; filename="blind_')
    data = json.loads(response.content)
    raw = response.content.decode()
    assert "correct_index" not in raw and "explanation" not in raw
    assert len(data["items"]) == draft.test_items.count()
    for entry, item in zip(data["items"], items_of(draft), strict=True):
        assert entry["options"] == item.options
        assert entry["slot"] == item.test_item.slot
        # Aucune explication n'apparaît, même recopiée dans un autre champ.
        assert item.explanation not in raw


def test_blind_answers_mark_disagreements_and_doubts(staff_client, draft):
    items = items_of(draft)
    answers = {str(i.test_item.slot): i.correct_index for i in items}
    wrong = items[2]
    answers[str(wrong.test_item.slot)] = (wrong.correct_index + 1) % 4
    doubt = items[5]
    data = {"answers": answers, "doubts": {str(doubt.test_item.slot): "B et C défendables."}}
    response = staff_client.post(
        url("blind_answers", draft.pk), {"file": blind_upload(data)}, follow=True
    )
    html = response.content.decode()
    assert "1 désaccord(s), 1 doute(s)" in html
    assert html.count(">Désaccord<") == 1
    assert html.count(">Doute<") == 1
    assert "B et C défendables." in html
    draft.refresh_from_db()
    assert draft.blind_check["answers"][str(wrong.test_item.slot)] != wrong.correct_index


@pytest.mark.parametrize(
    "data",
    [
        b"{not json",
        {"doubts": {}},
        {"answers": {"999": 1}},
        {"answers": {"1": 4}},
        {"answers": {"1": "B"}},
        {"answers": {"1": 1}, "doubts": {"1": 3}},
    ],
)
def test_invalid_blind_answers_are_refused(staff_client, draft, data):
    response = staff_client.post(
        url("blind_answers", draft.pk), {"file": blind_upload(data)}, follow=True
    )
    assert response.status_code == 200
    draft.refresh_from_db()
    assert draft.blind_check == {}


# --- Pages et droits ----------------------------------------------------------------------------


def test_backoffice_pages_require_staff(client, draft):
    item = items_of(draft)[0]
    get_pages = [
        url("home"),
        url("tests"),
        url("test_review", draft.pk),
        url("blind_json", draft.pk),
        url("item_edit", draft.pk, item.pk),
        url("voices"),
        url("usage"),
    ]
    post_pages = [
        url("audio_next", draft.pk),
        url("publish", draft.pk),
        url("archive", draft.pk),
        url("blind_answers", draft.pk),
        url("item_status", draft.pk, item.pk),
        url("item_audio", draft.pk, item.pk),
        url("voice_sample"),
    ]
    for page in get_pages:
        assert client.get(page).status_code == 302
    for page in post_pages:
        assert client.post(page).status_code == 302
    user = get_user_model().objects.create_user("c", "c@example.com", PASSWORD)
    client.force_login(user)
    for page in get_pages:
        assert client.get(page).status_code == 403
    for page in post_pages:
        assert client.post(page).status_code == 403
    assert UsageEntry.objects.count() == 0


def test_backoffice_home_shows_stock_quota_and_imports(staff_client, draft):
    UsageEntry.objects.create(kind="tts", provider="google_chirp3_hd", chars=95_000)
    html = staff_client.get(url("home")).content.decode()
    assert "Stock de tests" in html
    assert "1</span> publié" not in html  # aucun test publié
    assert "Quota vocal du mois" in html
    # 950 000 − 95 000 = 855 000 restants ; 5 000 caractères par test rapide → 171 tests.
    assert "171</span> tests" in html
    assert "Derniers imports" in html


def test_usage_page_lists_entries_and_monthly_totals(staff_client, db):
    UsageEntry.objects.create(kind="tts", provider="fake", chars=1200, ref="item 4")
    UsageEntry.objects.create(kind="tts", provider="fake", chars=800, ref="item 5")
    html = staff_client.get(url("usage")).content.decode()
    assert "Synthèse vocale" in html
    assert "2 opérations" in html
    assert "item 5" in html


def test_voices_page_lists_voices_plays_sample_and_saves_casting(staff_client, db):
    html = staff_client.get(url("voices")).content.decode()
    assert "Kore" in html and "Écouter" in html
    response = staff_client.post(url("voice_sample"), {"voice": "fr-FR-Chirp3-HD-Kore"}, **HTMX)
    assert "<audio" in response.content.decode()
    assert UsageEntry.objects.get().chars == 80
    staff_client.post(url("voice_sample"), {"voice": "fr-FR-Chirp3-HD-Kore"}, **HTMX)
    assert UsageEntry.objects.count() == 1  # en cache
    response = staff_client.post(url("voice_sample"), {"voice": "inconnue"}, **HTMX)
    assert "Voix inconnue" in response.content.decode()

    women = ["fr-FR-Chirp3-HD-Kore", "fr-FR-Chirp3-HD-Leda", "fr-FR-Chirp3-HD-Gacrux"]
    men = ["fr-FR-Chirp3-HD-Puck", "fr-FR-Chirp3-HD-Orus", "fr-FR-Chirp3-HD-Iapetus"]
    women_ca = ["fr-CA-Chirp3-HD-Kore", "fr-CA-Chirp3-HD-Zephyr"]
    men_ca = ["fr-CA-Chirp3-HD-Charon", "fr-CA-Chirp3-HD-Fenrir", "fr-CA-Chirp3-HD-Orus"]
    # Sans voix québécoises : refusé (au moins 2 par genre, 7.2).
    data = {"instructions": "fr-FR-Chirp3-HD-Aoede", "cast": women + men}
    html = staff_client.post(url("voices"), data).content.decode()
    assert "au moins 2 voix québécoises féminines" in html
    data["cast"] = women + men + women_ca + men_ca
    response = staff_client.post(url("voices"), data)
    assert response.status_code == 302
    casting = SiteSettings.load().tts_casting
    assert casting == {
        "instructions": "fr-FR-Chirp3-HD-Aoede",
        "F": women,
        "M": men,
        "F_CA": women_ca,
        "M_CA": men_ca,
    }


def test_casting_needs_three_voices_per_gender(staff_client, db):
    before = SiteSettings.load().tts_casting
    data = {
        "instructions": "fr-FR-Chirp3-HD-Aoede",
        "cast": ["fr-FR-Chirp3-HD-Kore", "fr-FR-Chirp3-HD-Puck"],
    }
    html = staff_client.post(url("voices"), data).content.decode()
    assert "au moins 3 voix féminines" in html
    assert SiteSettings.load().tts_casting == before


def test_make_dev_test_draft_command(formats, demo_db, capsys):
    from django.core.management import call_command

    call_command("make_dev_test", "--mode", "rapide", "--draft")
    test = Test.objects.get()
    assert test.status == Test.Status.DRAFT
    assert test.import_batch.status == "accepted"
    assert not Item.objects.filter(audio__isnull=False).exists()
    assert "brouillon" in capsys.readouterr().out
