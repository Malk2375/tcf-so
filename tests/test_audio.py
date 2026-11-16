"""Synthèse vocale, assemblage, génération de l'audio d'un test et quota (SPEC 7, 15)."""

import base64
import io
import json
import random
import urllib.error
import wave

import pytest
from django.core.files.storage import default_storage
from django.core.management import call_command

from bank import audio, synthesis
from bank.audio import (
    AFTER_SIGNAL,
    BEFORE_OPTIONS,
    BEFORE_QUESTION,
    BETWEEN_LINES,
    BETWEEN_OPTIONS,
    OPENING_SIGNAL,
    cast_voices,
    is_up_to_date,
    plan_item,
)
from bank.content import item_fields, script_hash, tts_chars
from bank.devtest import make_dev_draft
from bank.models import AudioAsset, Item
from bank.review import update_item
from bank.synthesis import (
    QUOTA_MESSAGE,
    SAMPLE_TEXT,
    QuotaError,
    audio_progress,
    generate_next,
    voice_sample_path,
)
from bank.tts import (
    SAMPLE_RATE,
    FakeTTSProvider,
    GoogleChirp3Provider,
    TTSError,
    Voice,
    get_provider,
)
from usage.models import SiteSettings, UsageEntry, default_tts_casting, tts_chars_used

pytestmark = pytest.mark.django_db

CASTING = default_tts_casting()


@pytest.fixture
def fast_mp3(monkeypatch):
    """Encodage MP3 remplacé par un faux fichier (un test garde le vrai ffmpeg)."""

    def fake_encode(pcm: bytes) -> bytes:
        return b"MP3" + len(pcm).to_bytes(8, "big") + pcm[:64]

    monkeypatch.setattr(audio, "encode_mp3", fake_encode)
    monkeypatch.setattr(synthesis, "encode_mp3", fake_encode)


@pytest.fixture
def draft(formats):
    return make_dev_draft("rapide", rng=random.Random(4))


def co_items(test):
    return list(
        Item.objects.filter(test_item__test=test, section="CO").order_by("test_item__position")
    )


def run_until_done(test, limit=100):
    for _ in range(limit):
        progress = generate_next(test)
        if progress.finished:
            return progress
    raise AssertionError("La génération ne se termine pas.")


def wav_seconds(data: bytes) -> float:
    with wave.open(io.BytesIO(data)) as source:
        assert (source.getnchannels(), source.getsampwidth()) == (1, 2)
        assert source.getframerate() == SAMPLE_RATE
        return source.getnframes() / SAMPLE_RATE


# --- Fournisseur factice (7.1) ------------------------------------------------------------------


def test_fake_provider_returns_mono_wav_of_chars_over_15_seconds():
    provider = FakeTTSProvider()
    assert wav_seconds(provider.synthesize("a" * 150, "fr-FR-Chirp3-HD-Kore", None)) == 10
    assert wav_seconds(provider.synthesize("a" * 30, "fr-FR-Chirp3-HD-Puck", None)) == 2


def test_fake_provider_is_the_default_and_lists_fake_voices(settings):
    assert isinstance(get_provider(), FakeTTSProvider)
    voices = get_provider().list_voices()
    names = {voice.name for voice in voices}
    casting = default_tts_casting()
    assert {casting["instructions"], *casting["F"], *casting["M"]} <= names
    assert {voice.gender for voice in voices} == {"F", "M"}


def test_network_is_blocked_during_tests():
    import socket

    with pytest.raises(RuntimeError, match="réseau"):
        socket.create_connection(("texttospeech.googleapis.com", 443))


# --- Fournisseur Google (sans réseau : requêtes simulées) ---------------------------------------


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_google_provider_request_shape(monkeypatch):
    sent = []

    def fake_urlopen(request, timeout):
        sent.append(request)
        audio_bytes = base64.b64encode(b"RIFF....WAVE").decode()
        return FakeResponse(json.dumps({"audioContent": audio_bytes}).encode())

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    provider = GoogleChirp3Provider("cle-de-test")
    assert provider.synthesize("Bonjour.", "fr-FR-Chirp3-HD-Kore", None) == b"RIFF....WAVE"
    request = sent[0]
    assert request.full_url == "https://texttospeech.googleapis.com/v1/text:synthesize"
    assert request.get_method() == "POST"
    assert request.get_header("X-goog-api-key") == "cle-de-test"
    body = json.loads(request.data)
    assert body == {
        "input": {"text": "Bonjour."},
        "voice": {"languageCode": "fr-FR", "name": "fr-FR-Chirp3-HD-Kore"},
        "audioConfig": {"audioEncoding": "LINEAR16", "sampleRateHertz": 24000},
    }
    provider.synthesize("Bonjour.", "fr-FR-Chirp3-HD-Kore", 0.9)
    assert json.loads(sent[1].data)["audioConfig"]["speakingRate"] == 0.9


def test_google_provider_lists_french_voices_with_gender(monkeypatch):
    payload = {
        "voices": [
            {"languageCodes": ["fr-FR"], "name": "fr-FR-Chirp3-HD-Kore", "ssmlGender": "FEMALE"},
            {"languageCodes": ["fr-FR"], "name": "fr-FR-Chirp3-HD-Puck", "ssmlGender": "MALE"},
            {"languageCodes": ["fr-CA"], "name": "fr-CA-Chirp3-HD-Kore", "ssmlGender": "FEMALE"},
        ]
    }
    urls = []

    def fake_urlopen(request, timeout):
        urls.append(request.full_url)
        return FakeResponse(json.dumps(payload).encode())

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    voices = GoogleChirp3Provider("cle").list_voices()
    assert urls == ["https://texttospeech.googleapis.com/v1/voices?languageCode=fr-FR"]
    assert voices == [Voice("fr-FR-Chirp3-HD-Kore", "F"), Voice("fr-FR-Chirp3-HD-Puck", "M")]


def test_google_provider_errors_are_readable(monkeypatch, settings):
    def failing(request, timeout):
        raise urllib.error.HTTPError(request.full_url, 403, "Forbidden", {}, io.BytesIO(b"{}"))

    monkeypatch.setattr("urllib.request.urlopen", failing)
    with pytest.raises(TTSError, match="erreur 403"):
        GoogleChirp3Provider("cle").synthesize("Bonjour.", "fr-FR-Chirp3-HD-Kore", None)

    settings.TTS_PROVIDER = "google"
    settings.GOOGLE_TTS_API_KEY = ""
    with pytest.raises(TTSError, match="GOOGLE_TTS_API_KEY"):
        get_provider()


# --- Distribution des voix (7.2) ----------------------------------------------------------------


def test_casting_is_deterministic_and_by_gender():
    speakers = {"F1": {"gender": "F"}, "F2": {"gender": "F"}, "M1": {"gender": "M"}}
    voices = cast_voices("abc", speakers, CASTING)
    assert voices == cast_voices("abc", speakers, CASTING)
    assert voices["F1"] in CASTING["F"] and voices["F2"] in CASTING["F"]
    assert voices["M1"] in CASTING["M"]
    assert voices["F1"] != voices["F2"]
    assert CASTING["instructions"] not in voices.values()


def test_two_speakers_always_get_two_voices():
    speakers = {"F1": {"gender": "F"}, "F2": {"gender": "F"}, "F3": {"gender": "F"}}
    for n in range(200):
        voices = cast_voices(f"hash-{n}", speakers, CASTING)
        assert len(set(voices.values())) == 3


def test_casting_with_too_few_voices_is_refused():
    speakers = {"M1": {"gender": "M"}, "M2": {"gender": "M"}}
    with pytest.raises(TTSError, match="Distribution des voix insuffisante"):
        cast_voices("abc", speakers, {**CASTING, "M": ["fr-FR-Chirp3-HD-Puck"]})


# --- Assemblage (7.3) ---------------------------------------------------------------------------


def test_plan_of_question_reponse_with_audio_letters(draft):
    item = next(i for i in co_items(draft) if i.subtype == "question_reponse")
    assert item.options_mode == "audio_letters"
    plan = plan_item(item, CASTING)
    texts = [segment.text for _, segment in plan.parts]
    # La question fixe n'est pas lue ; les options sont lues avec leur lettre.
    assert item.question not in texts
    assert texts[-4:] == [
        f"{letter}. {option}" for letter, option in zip("ABCD", item.options, strict=True)
    ]
    assert [pause for pause, _ in plan.parts][-4:] == [BEFORE_OPTIONS] + [BETWEEN_OPTIONS] * 3
    assert {segment.voice for _, segment in plan.parts[-4:]} == {CASTING["instructions"]}
    assert plan.chars == tts_chars(item_fields(item))


def test_plan_of_document_reads_question_with_instructions_voice(draft):
    item = next(i for i in co_items(draft) if i.subtype != "question_reponse")
    plan = plan_item(item, CASTING)
    pauses = [pause for pause, _ in plan.parts]
    lines = item.audio_script["lines"]
    assert pauses == [AFTER_SIGNAL] + [BETWEEN_LINES] * (len(lines) - 1) + [BEFORE_QUESTION]
    assert plan.parts[-1][1].text == item.question
    assert plan.parts[-1][1].voice == CASTING["instructions"]
    assert plan.chars == tts_chars(item_fields(item))


def test_synthesized_item_is_a_real_normalized_mp3(draft):
    """Vrai ffmpeg : MP3 nommé d'après son SHA-256, durée réelle, voix enregistrées."""
    item = next(i for i in co_items(draft) if i.subtype == "question_reponse")
    synthesis.synthesize_one(item)
    asset = AudioAsset.objects.get(item=item)
    data = default_storage.open(asset.file.name).read()
    assert data[:3] == b"ID3" or data[0] == 0xFF  # en-tête MP3
    assert asset.file.name.startswith("audio/") and asset.file.name.endswith(".mp3")
    plan = plan_item(item, CASTING)
    expected = OPENING_SIGNAL + sum(pause for pause, _ in plan.parts) + plan.chars / 15
    assert abs(asset.duration_ms / 1000 - expected) < 0.05
    assert asset.engine == "fake"
    assert asset.chars_billed == plan.chars
    assert asset.script_hash == script_hash(item_fields(item))
    assert asset.voices["instructions"] == CASTING["instructions"]


# --- Génération du test, reprise, idempotence (7.4) ---------------------------------------------


def test_generate_whole_test_then_rerun_does_nothing(draft, fast_mp3):
    items = co_items(draft)
    progress = audio_progress(draft)
    assert (progress.done, progress.total) == (0, len(items))
    expected_chars = sum(tts_chars(item_fields(item)) for item in items)
    assert progress.pending_chars == expected_chars

    progress = run_until_done(draft)
    assert progress.done == progress.total == len(items)
    entries = UsageEntry.objects.filter(kind="tts")
    assert entries.count() == len(items)
    # Synthèses factices : journalisées avec leurs caractères, mais hors quota.
    assert {e.provider for e in entries} == {"fake"}
    assert sum(e.chars for e in entries) == expected_chars
    assert tts_chars_used() == 0

    files = {a.item_id: (a.file.name, a.created_at) for a in AudioAsset.objects.all()}
    assert generate_next(draft).finished
    assert UsageEntry.objects.count() == len(items)
    assert files == {a.item_id: (a.file.name, a.created_at) for a in AudioAsset.objects.all()}


def test_generation_resumes_where_it_stopped(draft, fast_mp3):
    items = co_items(draft)
    generate_next(draft)
    generate_next(draft)
    # Page fermée ici. Un nouveau clic reprend au troisième item.
    progress = audio_progress(draft)
    assert progress.done == 2
    assert progress.next_item == items[2]
    run_until_done(draft)
    assert UsageEntry.objects.count() == len(items)


def test_modified_script_makes_audio_stale_and_only_it_is_redone(draft, fast_mp3):
    run_until_done(draft)
    item = next(i for i in co_items(draft) if i.subtype != "question_reponse")
    script = json.loads(json.dumps(item.audio_script))
    script["lines"][0]["text"] = script["lines"][0]["text"].rstrip(".") + " ici."
    assert update_item(item, {"audio_script": script}) == []
    item.refresh_from_db()
    assert not is_up_to_date(item)
    progress = audio_progress(draft)
    assert (progress.done, progress.next_item) == (progress.total - 1, item)
    before = UsageEntry.objects.count()
    assert run_until_done(draft).finished
    assert UsageEntry.objects.count() == before + 1
    item.refresh_from_db()
    assert is_up_to_date(item)


def test_quota_stops_synthesis_with_clear_message(draft, fast_mp3):
    site = SiteSettings.load()
    needed = audio_progress(draft).pending_chars
    # Consommations Google simulées : il reste un caractère de moins que ce que demande le test.
    UsageEntry.objects.create(
        kind="tts", provider=GoogleChirp3Provider.name, chars=site.tts_effective_quota - needed + 1
    )
    progress = audio_progress(draft)
    assert not progress.fits
    with pytest.raises(QuotaError, match=QUOTA_MESSAGE):
        generate_next(draft)
    assert AudioAsset.objects.count() == 0
    assert UsageEntry.objects.count() == 1  # la consommation simulée seulement


def test_quota_counts_previous_usage(draft, fast_mp3):
    """Consommations Google simulées : elles comptent dans le quota, pas les factices."""
    site = SiteSettings.load()
    needed = audio_progress(draft).pending_chars
    google = GoogleChirp3Provider.name
    UsageEntry.objects.create(kind="tts", provider=google, chars=site.tts_effective_quota - needed)
    UsageEntry.objects.create(kind="tts", provider="fake", chars=10 * needed)
    assert audio_progress(draft).fits
    UsageEntry.objects.create(kind="tts", provider=google, chars=1)
    assert not audio_progress(draft).fits
    with pytest.raises(QuotaError):
        generate_next(draft)


def test_regenerate_one_item_refused_at_quota(draft, fast_mp3):
    site = SiteSettings.load()
    site.tts_monthly_char_quota = 0
    site.save()
    with pytest.raises(QuotaError):
        synthesis.synthesize_one(co_items(draft)[0])


def test_published_test_audio_is_frozen(draft, fast_mp3):
    draft.status = "published"
    draft.save()
    with pytest.raises(TTSError, match="figé"):
        generate_next(draft)


# --- Écoute des voix (7.2) ----------------------------------------------------------------------


def test_voice_sample_is_80_chars_cached_and_counted(fast_mp3):
    assert len(SAMPLE_TEXT) == 80
    path = voice_sample_path("fr-FR-Chirp3-HD-Kore")
    assert default_storage.exists(path)
    assert voice_sample_path("fr-FR-Chirp3-HD-Kore") == path
    entry = UsageEntry.objects.get()
    assert (entry.kind, entry.chars) == ("tts", 80)
    assert voice_sample_path("fr-FR-Chirp3-HD-Puck") != path
    assert UsageEntry.objects.count() == 2


def test_tts_list_voices_command(capsys):
    call_command("tts_list_voices")
    out = capsys.readouterr().out
    assert "fr-FR-Chirp3-HD-Kore\tféminine" in out
    assert "fr-FR-Chirp3-HD-Puck\tmasculine" in out


# --- Moteur de l'audio (7.4) --------------------------------------------------------------------


def test_fake_audio_is_never_up_to_date_with_google(draft, fast_mp3, settings):
    run_until_done(draft)
    assert audio_progress(draft).finished
    settings.TTS_PROVIDER = "google"  # aucun appel : on ne fait que mesurer l'état
    item = co_items(draft)[0]
    assert audio.audio_state(item) == audio.AudioState.ENGINE
    assert not is_up_to_date(item)
    progress = audio_progress(draft)
    assert progress.done == 0 and progress.next_item == item


def test_fake_audio_is_regenerated_by_the_active_engine(draft, fast_mp3):
    run_until_done(draft)

    class OtherEngine(FakeTTSProvider):
        name = "google_chirp3_hd"  # se fait passer pour Google, sans réseau

    progress = generate_next(draft, OtherEngine())
    assert progress.done == 1
    item = co_items(draft)[0]
    assert item.audio.engine == "google_chirp3_hd"
    assert UsageEntry.objects.filter(provider="google_chirp3_hd").count() == 1
