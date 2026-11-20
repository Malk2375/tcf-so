"""Bruits de fond, canaux et voix québécoises (SPEC 7.5) ; bibliothèque sonore (7.5.4).

Fournisseur factice et bruits factices générés par ffmpeg : aucun appel réseau.
"""

import json
import random
import struct
import wave
from collections import Counter
from pathlib import Path

import pytest
from django.core.management import call_command
from django.urls import reverse

from bank import ambiences, audio, sound, synthesis
from bank.ambiences import AmbienceError, add_clip, ensure_fake_ambiences
from bank.audio import Plan, SoundChoice, assemble, cast_voices, expected_hash, plan_item
from bank.brief import build_brief
from bank.constants import (
    ACCENT_SUBTYPES,
    AMBIENCE_CATEGORIES,
    allowed_combinations,
)
from bank.content import item_fields, script_hash
from bank.devtest import make_dev_draft
from bank.importer import import_files
from bank.models import AmbienceClip, AudioAsset, ExamFormat, Item, Test
from bank.review import publish
from bank.synthesis import generate_next, trial_mixes
from bank.tts import SAMPLE_RATE, FakeTTSProvider, GoogleChirp3Provider, TTSError, silence_pcm
from tests.conftest import load_fixture
from usage.models import SiteSettings, UsageEntry, default_tts_casting

pytestmark = pytest.mark.django_db

CASTING = default_tts_casting()
ROOT = Path(__file__).resolve().parent.parent


# --- Outils ---------------------------------------------------------------------------------------


def write_wav(path: Path, pcm: bytes) -> Path:
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(SAMPLE_RATE)
        out.writeframes(pcm)
    return path


def noise(seconds: float, amplitude: float, color: str = "pink", seed: int = 1) -> bytes:
    return sound.run(
        [
            "-f",
            "lavfi",
            "-i",
            f"anoisesrc=color={color}:amplitude={amplitude}:sample_rate={SAMPLE_RATE}"
            f":duration={seconds}:seed={seed}",
            *sound.PCM_FORMAT,
            "pipe:1",
        ]
    )


def samples_of(pcm: bytes) -> tuple:
    return struct.unpack(f"<{len(pcm) // 2}h", pcm)


def co_item(subtype="annonce", level="A2", setting=None, channel=None, accents=None, **extra):
    """Item de CO enregistré (brouillon), avec ou sans les nouveaux champs."""
    speakers = {"F1": {"gender": "F"}, "M1": {"gender": "M"}}
    for speaker, accent in (accents or {}).items():
        speakers[speaker]["accent"] = accent
    script = {
        "style": "essai",
        "speakers": speakers,
        "lines": [
            {"speaker": "F1", "text": "Bonjour, le train pour Lyon partira voie trois."},
            {"speaker": "M1", "text": "Merci de votre attention et bon voyage à tous."},
        ],
    }
    if setting is not None:
        script["setting"] = setting
    if channel is not None:
        script["channel"] = channel
    index = Item.objects.count()
    return Item.objects.create(
        section="CO",
        level=level,
        subtype=subtype,
        audio_script=script,
        question="Où part le train ?",
        options=["Lyon", "Paris", "Nice", "Lille"],
        correct_index=0,
        explanation="Le document annonce un départ pour Lyon, voie trois.",
        content_hash=f"hash-son-{index}-{subtype}-{setting}-{channel}",
        **extra,
    )


@pytest.fixture
def fake_library(db):
    ensure_fake_ambiences()
    return {c: ambiences.active_clips(c) for c in AMBIENCE_CATEGORIES}


# --- Items sans les nouveaux champs : rien ne change ----------------------------------------------


def test_item_without_new_fields_sounds_exactly_as_before(db):
    item = co_item()
    plan = plan_item(item, CASTING)
    provider = FakeTTSProvider()
    result = assemble(plan, provider)
    # Assemblage d'avant la révision : signal, silences, répliques, question ; rien d'autre.
    expected = audio.opening_signal()
    for pause, segment in plan.parts:
        expected += silence_pcm(pause) + audio.wav_to_pcm(
            provider.synthesize(segment.text, segment.voice)
        )
    assert result.pcm == expected
    assert result.mix == {}
    assert expected_hash(item) == script_hash(item_fields(item))


def test_import_file_without_new_fields_is_accepted_without_sound_warnings(formats, rng):
    from bank.brief import create_brief
    from bank.devdata import fake_import_file

    brief = create_brief(formats["rapide"], rng=rng)
    data = fake_import_file(brief.content, rng)
    for item in data["items"]:
        if item["audio_script"]:
            for key in ("setting", "channel"):
                item["audio_script"].pop(key, None)
            for speaker in item["audio_script"]["speakers"].values():
                speaker.pop("accent", None)
    result = import_files([("a.json", json.dumps(data).encode())])
    assert result.report.errors == []
    # Sans lieu ni canal déclarés, aucun avertissement de lieu ou de canal (l'emplacement
    # québécois sans locuteur fr-CA reste signalé, comme le veut la SPEC).
    sound_words = ("Lieu", "Canal")
    assert not [w for w in result.report.warnings if any(k in w.message for k in sound_words)]


# --- Brief : lieux, canaux, accents (7.5.2) -------------------------------------------------------


@pytest.mark.parametrize("seed", range(5))
def test_real_brief_has_four_quebec_slots_on_permitted_subtypes(formats, seed):
    brief = build_brief(formats["reel"], 1, random.Random(seed))
    co = [s for s in brief["slots"] if s["section"] == "CO"]
    quebec = [s for s in co if s.get("accent") == "fr-CA"]
    assert len(quebec) == 4
    assert all(s["subtype"] in ACCENT_SUBTYPES for s in quebec)
    assert not [
        s for s in co if s.get("accent") and s["subtype"] in {"annonce", "question_reponse"}
    ]
    for slot in co:
        assert (slot["channel"], slot["setting"]) in allowed_combinations(slot["subtype"])
    assert not [s for s in brief["slots"] if s["section"] != "CO" and "setting" in s]


@pytest.mark.parametrize("mode", ["rapide", "moyen", "reel"])
def test_brief_varies_places_within_a_section(formats, mode):
    brief = build_brief(formats[mode], 1, random.Random(3))
    used: Counter = Counter()
    for slot in (s for s in brief["slots"] if s["section"] == "CO"):
        setting = slot["setting"]
        if setting != "aucun" and used[setting]:
            # Lieu répété : tous les autres lieux permis du sous-type étaient déjà pris.
            others = {s for _, s in allowed_combinations(slot["subtype"])} - {"aucun", setting}
            assert all(used[o] for o in others), slot
        used[setting] += 1


def test_accent_quotas_follow_site_settings(formats):
    site = SiteSettings.load()
    site.accent_quotas = {"rapide": 0, "moyen": 2, "reel": 4}
    site.save()
    rapide = build_brief(formats["rapide"], 1, random.Random(1))
    moyen = build_brief(formats["moyen"], 1, random.Random(1))
    assert not [s for s in rapide["slots"] if s.get("accent")]
    assert len([s for s in moyen["slots"] if s.get("accent")]) == 2


def test_brief_slot_example_shape(formats):
    brief = build_brief(formats["moyen"], 1, random.Random(4))
    slot = next(s for s in brief["slots"] if s.get("accent"))
    keys = list(slot)
    assert keys.index("setting") < keys.index("channel") < keys.index("accent")
    assert keys.index("accent") < keys.index("correct_letter")


# --- Import : règle 12 et avertissements ----------------------------------------------------------


def _import_with(formats, rng, change):
    from bank.brief import create_brief
    from bank.devdata import fake_import_file

    brief = create_brief(formats["rapide"], rng=rng)
    data = fake_import_file(brief.content, rng)
    change(data["items"], brief.content["slots"])
    return import_files([("a.json", json.dumps(data).encode())])


def _first(items, subtype):
    return next(i for i in items if i["subtype"] == subtype)


def test_rule_12_combination_outside_the_table_is_blocking(formats, rng):
    def change(items, slots):
        item = next(i for i in items if i["section"] == "CO" and i["subtype"] == "annonce")
        item["audio_script"]["setting"], item["audio_script"]["channel"] = "gare", "direct"

    result = _import_with(formats, rng, change)
    assert result.test is None
    assert any("impossibles pour « annonce »" in e.message for e in result.report.errors)


def test_rule_12_quebec_accent_on_an_announcement_is_blocking(formats, rng):
    def change(items, slots):
        item = next(i for i in items if i["section"] == "CO" and i["subtype"] == "annonce")
        item["audio_script"]["speakers"]["F1"]["accent"] = "fr-CA"

    result = _import_with(formats, rng, change)
    assert result.test is None
    assert any("Accent fr-CA interdit" in e.message for e in result.report.errors)


def test_other_permitted_place_or_missing_quebec_speaker_is_a_warning(formats, rng):
    changed = {}

    def change(items, slots):
        by_slot = {s["slot"]: s for s in slots}
        for item in items:
            slot = by_slot[item["slot"]]
            script = item["audio_script"]
            if not script:
                continue
            others = [
                (c, s)
                for c, s in allowed_combinations(item["subtype"])
                if (c, s) != (slot["channel"], slot["setting"])
            ]
            if others and "place" not in changed:
                script["channel"], script["setting"] = others[0]
                changed["place"] = item["slot"]
            elif slot.get("accent") == "fr-CA" and "accent" not in changed:
                for speaker in script["speakers"].values():
                    speaker.pop("accent", None)
                changed["accent"] = item["slot"]

    result = _import_with(formats, rng, change)
    assert result.report.errors == []
    messages = {(w.slot, w.message.split(" ")[0]) for w in result.report.warnings}
    assert any(slot == changed["place"] for slot, _ in messages)
    assert any(
        w.slot == changed["accent"] and "sans locuteur fr-CA" in w.message
        for w in result.report.warnings
    )


# --- Voix québécoises (7.2) -----------------------------------------------------------------------


def test_quebec_speaker_gets_a_quebec_voice(db):
    speakers = {"F1": {"gender": "F", "accent": "fr-CA"}, "M1": {"gender": "M"}}
    voices = cast_voices("abc", speakers, CASTING)
    assert voices["F1"] in CASTING["F_CA"]
    assert voices["M1"] in CASTING["M"]
    with pytest.raises(TTSError, match="québécoises"):
        cast_voices("abc", speakers, {**CASTING, "F_CA": []})


def test_google_request_uses_the_voice_language(monkeypatch):
    bodies = []

    class Response:
        def __init__(self, data):
            self.data = data

        def read(self):
            return self.data

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def fake_urlopen(request, timeout):
        bodies.append(json.loads(request.data))
        return Response(json.dumps({"audioContent": ""}).encode())

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    GoogleChirp3Provider("cle").synthesize("Bonjour", "fr-CA-Chirp3-HD-Kore")
    assert bodies[0]["voice"] == {"languageCode": "fr-CA", "name": "fr-CA-Chirp3-HD-Kore"}


def test_tts_list_voices_shows_both_languages(capsys):
    call_command("tts_list_voices")
    out = capsys.readouterr().out
    assert "fr-FR : " in out and "fr-CA : " in out
    assert "fr-CA-Chirp3-HD-Kore" in out


# --- Assemblage avec canal et bruit de fond (7.5.3) -----------------------------------------------


def test_station_announcement_has_chime_speaker_filter_and_ambience_stopping_before_question(
    fake_library,
):
    item = co_item(setting="gare", channel="haut_parleur")
    plan = plan_item(item, CASTING)
    provider = FakeTTSProvider()
    choice = audio.choose_sound(plan)
    assert choice.chime is not None and choice.clip is not None
    with_noise = assemble(plan, provider, choice)
    quiet = assemble(plan, provider, SoundChoice(chime=choice.chime))
    mix = with_noise.mix
    assert mix["setting"] == "gare" and mix["channel"] == "haut_parleur"
    assert mix["chime"] == choice.chime.pk and mix["clip"] == choice.clip.pk
    assert mix["snr_db"] == 20  # niveau A2 : tranche A1-B1
    # Le filtre du haut-parleur a été appliqué aux répliques (elles diffèrent du son direct).
    direct = assemble(Plan(**{**plan.__dict__, "channel": "direct", "setting": "aucun"}), provider)
    assert len(quiet.pcm) != len(direct.pcm) or quiet.pcm != direct.pcm
    # Même longueur avec ou sans bruit ; la question (fin du fichier) est sans bruit de fond :
    # le mixage abaisse tout de 6 dB, la question doit donc valoir exactement la moitié.
    assert len(with_noise.pcm) == len(quiet.pcm)
    # Seulement la question : le bruit finit par un fondu pendant le silence qui la précède.
    question_samples = round(
        sum(len(s.text) for _, s in plan.parts[plan.n_lines :]) / 15 * SAMPLE_RATE
    )
    tail_mixed = samples_of(with_noise.pcm[-question_samples * 2 :])
    tail_quiet = samples_of(quiet.pcm[-question_samples * 2 :])
    assert max(abs(a - b / 2) for a, b in zip(tail_mixed, tail_quiet, strict=True)) <= 1
    # Sous les répliques, en revanche, le bruit est bien là.
    middle = len(quiet.pcm) // 2 // 2
    window = slice(middle - 2000, middle + 2000)
    diffs = [
        abs(a - b / 2)
        for a, b in zip(
            samples_of(with_noise.pcm)[window], samples_of(quiet.pcm)[window], strict=True
        )
    ]
    assert max(diffs) > 5


def test_voicemail_starts_with_a_beep_and_phone_filter(fake_library):
    item = co_item(subtype="message_vocal", setting="aucun", channel="telephone")
    plan = plan_item(item, CASTING)
    result = assemble(plan, FakeTTSProvider())
    assert result.mix["bip"] is True and result.mix["clip"] is None


def test_clip_and_start_are_chosen_deterministically(fake_library, tmp_path):
    extra = ambiences.add_clip(
        str(write_wav(tmp_path / "gare2.wav", noise(30, 0.1))),
        categories=["gare"],
        title="Seconde gare",
        source_url="https://example.org/2",
        license="CC0",
    ).clip
    item = co_item(setting="gare", channel="haut_parleur")
    plan = plan_item(item, CASTING)
    first, second = audio.choose_sound(plan), audio.choose_sound(plan)
    assert first.clip == second.clip and first.clip in {extra, *fake_library["gare"]}
    one = assemble(plan, FakeTTSProvider(), first).mix
    two = assemble(plan, FakeTTSProvider(), second).mix
    assert one["clip_start_ms"] == two["clip_start_ms"]


def test_remix_after_changing_snr_costs_nothing_and_marks_draft_audio_stale(fake_library):
    item = co_item(setting="gare", channel="haut_parleur")
    synthesis.synthesize_one(item)
    item.refresh_from_db()
    entries = UsageEntry.objects.count()
    assert entries == 1  # première synthèse : les répliques partent à l'API (factice)
    assert audio.is_up_to_date(item)
    site = SiteSettings.load()
    site.ambience_snr_db = {"A1-B1": 12, "B2-C2": 10}
    site.save()
    item = Item.objects.select_related("audio").get(pk=item.pk)
    assert audio.audio_state(item) == audio.AudioState.STALE
    synthesis.synthesize_one(item)
    item = Item.objects.select_related("audio").get(pk=item.pk)
    assert UsageEntry.objects.count() == entries  # tout venait du cache
    assert item.audio.chars_billed == 0 and item.audio.mix["snr_db"] == 12
    assert audio.is_up_to_date(item)


def test_snr_is_measured_on_the_excerpt_and_compensates_volume_differences(fake_library, tmp_path):
    loud = write_wav(tmp_path / "fort.wav", noise(20, 0.9, seed=4))
    soft = write_wav(tmp_path / "faible.wav", noise(20, 0.01, seed=5))
    clips = [
        add_clip(
            str(path),
            categories=["rue"],
            title=path.stem,
            source_url="https://e.org",
            license="CC0",
        ).clip
        for path in (loud, soft)
    ]
    assert clips[0].loudness_lufs - clips[1].loudness_lufs > 30
    beds = [audio.noise_bed(clip, 6.0, "graine", -40.0)[0] for clip in clips]
    for bed in beds:
        # Fondus retirés : mesure sur la partie centrale.
        core = bed[2 * SAMPLE_RATE : 4 * SAMPLE_RATE * 2]
        assert abs(sound.loudness(core) - (-40.0)) < 1.0
    trials = [trial_mixes(clip) for clip in clips]
    for loud_trial, soft_trial in zip(*trials, strict=True):
        assert abs(loud_trial.measured_snr - soft_trial.measured_snr) <= 1.0
        assert abs(loud_trial.measured_snr - loud_trial.snr_db) <= 1.0


def test_published_test_audio_is_never_touched(formats, fake_library, rng):
    test = make_dev_draft("rapide", rng=rng)
    while not generate_next(test).finished:
        pass
    for test_item in test.test_items.all():
        item = test_item.item
        item.status = Item.Status.APPROVED
        item.save()
    publish(test)
    before = {a.pk: (a.file.name, a.script_hash) for a in AudioAsset.objects.all()}
    site = SiteSettings.load()
    site.ambience_snr_db = {"A1-B1": 5, "B2-C2": 5}
    site.save()
    AmbienceClip.objects.update(gain_db=6)
    for item in Item.objects.filter(section="CO").select_related("audio"):
        assert audio.audio_state(item) == audio.AudioState.OK
    with pytest.raises(TTSError):
        generate_next(Test.objects.get(pk=test.pk))
    assert {a.pk: (a.file.name, a.script_hash) for a in AudioAsset.objects.all()} == before


def test_generation_lists_places_without_active_clip(formats, rng):
    test = make_dev_draft("moyen", rng=rng)
    progress = synthesis.audio_progress(test)
    wanted = {
        (ti.item.audio_script or {}).get("setting")
        for ti in test.test_items.filter(section="CO").select_related("item")
    } - {"aucun", None}
    assert wanted and len(progress.missing_settings) == len(wanted)
    ensure_fake_ambiences()
    assert synthesis.audio_progress(test).missing_settings == []


# --- Bibliothèque (7.5.4) -------------------------------------------------------------------------


def test_clip_refused_without_permitted_license_or_source(tmp_path, db):
    path = write_wav(tmp_path / "a.wav", noise(3, 0.1))
    with pytest.raises(AmbienceError, match="Licence"):
        add_clip(
            str(path),
            categories=["rue"],
            title="a",
            source_url="https://e.org",
            license="A_VERIFIER",
        )
    with pytest.raises(AmbienceError, match="source"):
        add_clip(str(path), categories=["rue"], title="a", source_url="", license="CC-BY")
    assert not AmbienceClip.objects.exists()


def test_trim_keeps_only_the_excerpt_and_measures_it(tmp_path, db):
    # 10 s plus faibles (−8 dB), puis 20 s fortes : l'extrait de 12 à 28 s n'a que la partie forte.
    # (Les passages presque silencieux ne compteraient pas : la mesure EBU R128 les écarte.)
    path = write_wav(tmp_path / "source.wav", noise(10, 0.2) + noise(20, 0.5, seed=2))
    whole = add_clip(
        str(path), categories=["rue"], title="entier", source_url="https://e.org", license="CC-BY"
    ).clip
    cut = add_clip(
        str(path),
        categories=["metro_bus", "gare"],
        title="extrait",
        source_url="https://e.org",
        license="CC-BY",
        start=12,
        end=28,
    )
    clip = cut.clip
    assert abs(clip.duration_ms - 16_000) <= 30
    assert (clip.source_start_ms, clip.source_end_ms) == (12_000, 28_000)
    assert clip.categories == ["metro_bus", "gare"]
    assert clip.loudness_lufs > whole.loudness_lufs + 1  # mesure de l'extrait, pas du fichier
    stored = sound.decode(ambiences.clip_path(clip))
    assert abs(len(stored) / 2 / SAMPLE_RATE - 16) < 0.05
    assert cut.warnings == []
    short = add_clip(
        str(path),
        categories=["rue"],
        title="court",
        source_url="https://e.org",
        license="CC0",
        start=0,
        end=5,
    )
    assert short.warnings and "bouclé" in short.warnings[0]


def _folder(tmp_path) -> Path:
    folder = tmp_path / "sons"
    folder.mkdir()
    write_wav(folder / "111__a__gare.wav", noise(20, 0.2))
    write_wav(folder / "222__b__cafe.wav", noise(20, 0.1, seed=3))
    write_wav(folder / "333__c__douteux.wav", noise(20, 0.1, seed=4))
    write_wav(folder / "444__d__x1.wav", noise(5, 0.1))
    write_wav(folder / "444__d__x2.wav", noise(5, 0.1))
    entries = [
        {
            "file": "111__*",
            "categories": ["metro_bus", "gare"],
            "start": 0,
            "end": 16,
            "title": "Gare",
            "author": "a",
            "source_url": "https://e.org/111",
            "license": "CC0",
        },
        {
            "file": "222__*",
            "categories": ["cafe_restaurant"],
            "start": None,
            "end": None,
            "title": "Café",
            "author": "b",
            "source_url": "https://e.org/222",
            "license": "CC-BY",
        },
        {
            "file": "333__*",
            "categories": ["rue"],
            "title": "Douteux",
            "author": "c",
            "source_url": "https://e.org/333",
            "license": "A_VERIFIER",
        },
        {
            "file": "444__*",
            "categories": ["rue"],
            "title": "Ambigu",
            "author": "d",
            "source_url": "https://e.org/444",
            "license": "CC0",
        },
    ]
    (folder / "ambiences.json").write_text(json.dumps(entries), encoding="utf-8")
    return folder


def test_import_folder_refuses_unverified_licences_and_reruns_without_duplicates(
    tmp_path, capsys, db
):
    folder = _folder(tmp_path)
    call_command("import_ambiences_folder", str(folder))
    out = capsys.readouterr().out
    assert "REFUSÉ  3. 333__*" in out and "A_VERIFIER" in out
    assert "REFUSÉ  4. 444__*" in out and "exactement un fichier" in out
    assert "2 importé(s), 2 refusé(s)." in out
    assert "LUFS" in out and "16,0 s" in out
    assert AmbienceClip.objects.count() == 2
    gare = AmbienceClip.objects.get(title="Gare")
    assert gare.categories == ["metro_bus", "gare"] and abs(gare.duration_ms - 16_000) <= 30
    call_command("import_ambiences_folder", str(folder))
    assert AmbienceClip.objects.count() == 2
    assert "MIS À JOUR" in capsys.readouterr().out


@pytest.mark.skipif(
    not (ROOT / "assets" / "sound" / "ambiences.json").exists()
    or not list((ROOT / "assets" / "sound").glob("262638__*")),
    reason="bruits de fond du propriétaire absents (ils ne vont pas dans le dépôt)",
)
def test_import_of_the_owner_folder(capsys, db):
    """Critère de l'étape 12 sur le dossier fourni : les entrées A_VERIFIER sont refusées, les
    autres importées ; le clip 262638 découpé de 0 à 16 s dure 16 s, en metro_bus et gare."""
    folder = ROOT / "assets" / "sound"
    entries = json.loads((folder / "ambiences.json").read_text(encoding="utf-8"))
    call_command("import_ambiences_folder", str(folder))
    out = capsys.readouterr().out
    unverified = [e for e in entries if e["license"] not in {"CC0", "CC-BY", "CC-BY-NC"}]
    assert out.count("REFUSÉ") == len(unverified)
    assert AmbienceClip.objects.count() == len(entries) - len(unverified)
    clip = AmbienceClip.objects.get(source_name__startswith="262638__")
    assert abs(clip.duration_ms - 16_000) <= 30
    assert clip.categories == ["metro_bus", "gare"]


def test_export_and_import_ambiences_round_trip(tmp_path, db, settings):
    folder = _folder(tmp_path)
    call_command("import_ambiences_folder", str(folder))
    ensure_fake_ambiences()  # les bruits factices ne partent pas
    archive = tmp_path / "ambiences.zip"
    call_command("export_ambiences", "--output", str(archive))
    AmbienceClip.objects.all().delete()
    settings.MEDIA_ROOT = tmp_path / "autre-site"
    call_command("import_ambiences", str(archive))
    assert sorted(AmbienceClip.objects.values_list("title", flat=True)) == ["Café", "Gare"]
    call_command("import_ambiences", str(archive))
    assert AmbienceClip.objects.count() == 2
    clip = AmbienceClip.objects.get(title="Gare")
    assert abs(len(sound.decode(ambiences.clip_path(clip))) / 2 / SAMPLE_RATE - 16) < 0.05


def test_gitignore_keeps_source_sounds_out_of_the_repository():
    rules = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "/assets/sound/*" in rules and "!/assets/sound/ambiences.json" in rules


# --- Pages ----------------------------------------------------------------------------------------


def test_credits_page_lists_active_attributed_clips(client, tmp_path, db):
    path = write_wav(tmp_path / "a.wav", noise(3, 0.1))
    for title, licence, active in (
        ("Rue BY", "CC-BY", True),
        ("Marché BY-NC", "CC-BY-NC", True),
        ("Café zéro", "CC0", True),
        ("Inactif BY", "CC-BY", False),
    ):
        clip = add_clip(
            str(path),
            categories=["rue"],
            title=title,
            author="Auteur",
            source_url=f"https://e.org/{title}",
            license=licence,
            start=len(title) / 10,
        ).clip
        clip.is_active = active
        clip.save()
    ensure_fake_ambiences()
    html = client.get(reverse("credits")).content.decode()
    assert "Rue BY" in html and "Marché BY-NC" in html and "Café zéro" in html
    assert "Inactif BY" not in html and "factice" not in html
    assert "CC BY-NC" in html and "https://e.org/Rue BY" in html
    assert reverse("credits") in client.get("/").content.decode()  # lien du pied de page


def test_ambiences_page_upload_trial_and_toggle(staff_client, tmp_path, db):
    from django.core.files.uploadedfile import SimpleUploadedFile

    path = write_wav(tmp_path / "rue.wav", noise(8, 0.2))
    page = staff_client.get(reverse("backoffice:ambiences")).content.decode()
    assert "Lieux sans bruit de fond actif" in page
    upload = SimpleUploadedFile("rue.wav", path.read_bytes(), content_type="audio/wav")
    response = staff_client.post(
        reverse("backoffice:ambiences"),
        {
            "file": upload,
            "categories": ["rue", "marche"],
            "title": "Rue animée",
            "author": "Quelqu'un",
            "source_url": "https://freesound.org/s/1/",
            "license": "CC-BY",
            "start": "1",
            "end": "7",
        },
        follow=True,
    )
    html = response.content.decode()
    assert "Rue animée" in html and "bouclé" in html  # extrait de 6 s
    clip = AmbienceClip.objects.get()
    response = staff_client.post(
        reverse("backoffice:ambience_trial", args=[clip.pk]),
        {"gain_db": "-3"},
        HTTP_HX_REQUEST="true",
    )
    trial = response.content.decode()
    clip.refresh_from_db()
    assert clip.gain_db == -3
    # Rue et marché : canaux direct (dialogue, interview) et téléphone (message vocal) ; deux
    # rapports voix / bruit chacun.
    assert 'data-trials="4"' in trial and "LUFS" in trial and "écart mesuré" in trial
    staff_client.post(reverse("backoffice:ambience_update", args=[clip.pk]), {})
    clip.refresh_from_db()
    assert not clip.is_active
    staff_client.post(reverse("backoffice:ambience_update", args=[clip.pk]), {"action": "delete"})
    assert not AmbienceClip.objects.exists()


def test_ambiences_upload_refuses_other_licences(staff_client, tmp_path, db):
    from django.core.files.uploadedfile import SimpleUploadedFile

    path = write_wav(tmp_path / "rue.wav", noise(2, 0.2))
    response = staff_client.post(
        reverse("backoffice:ambiences"),
        {
            "file": SimpleUploadedFile("rue.wav", path.read_bytes()),
            "categories": ["rue"],
            "title": "Rue",
            "source_url": "",
            "license": "CC-BY-SA",
        },
    )
    assert response.status_code == 200
    assert not AmbienceClip.objects.exists()


def test_ambiences_page_is_staff_only(client, db):
    from django.contrib.auth import get_user_model

    user = get_user_model().objects.create_user("x", "x@example.com", "Mot-de-passe-2026")
    client.force_login(user)
    assert client.get(reverse("backoffice:ambiences")).status_code in {302, 403}


def test_voices_page_has_a_quebec_block(staff_client):
    html = staff_client.get(reverse("backoffice:voices")).content.decode()
    assert "Voix québécoises (fr-CA)" in html and 'data-language="fr-CA"' in html
    assert "fr-CA-Chirp3-HD-Charon" in html


def test_formats_exist_for_import_fixture(formats):
    """L'exemple d'import de la documentation déclare lieu et canal : il reste valide."""
    data = load_fixture("items_exemple.json")
    co = [i for i in data["items"] if i["section"] == "CO"]
    assert any("setting" in i["audio_script"] for i in co)
    assert ExamFormat.objects.count() == 3
