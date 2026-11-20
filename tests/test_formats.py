import pytest
from django.core.exceptions import ValidationError
from django.core.management import call_command

from bank.models import ExamFormat, FormatSection

pytestmark = pytest.mark.django_db

# SPEC 4.1 : (code, épreuve) → (ordre, items, durée, répartition A1→C2).
TABLE = {
    ("tcf_tp_so_rapide", "CO"): (1, 9, 615, [1, 2, 2, 2, 1, 1]),
    ("tcf_tp_so_rapide", "MSL"): (2, 6, 300, [1, 1, 1, 1, 1, 1]),
    ("tcf_tp_so_rapide", "CE"): (3, 9, 780, [1, 2, 2, 2, 1, 1]),
    ("tcf_tp_so_moyen", "CO"): (1, 17, 1155, [3, 3, 3, 3, 3, 2]),
    ("tcf_tp_so_moyen", "MSL"): (2, 12, 540, [2, 2, 2, 2, 2, 2]),
    ("tcf_tp_so_moyen", "CE"): (3, 17, 1440, [3, 3, 3, 3, 3, 2]),
    ("tcf_tp_so_reel", "CO"): (1, 34, 2250, [5, 6, 6, 6, 6, 5]),
    ("tcf_tp_so_reel", "MSL"): (2, 23, 1080, [4, 4, 4, 4, 4, 3]),
    ("tcf_tp_so_reel", "CE"): (3, 34, 2880, [5, 6, 6, 6, 6, 5]),
}


def test_seed_formats_matches_spec_table():
    call_command("seed_formats")
    assert ExamFormat.objects.count() == 3
    assert FormatSection.objects.count() == 9
    for section in FormatSection.objects.select_related("format"):
        order, n_items, duration, distribution = TABLE[(section.format.code, section.section)]
        assert section.order == order
        assert section.n_items == n_items
        assert section.duration_seconds == duration
        assert list(section.level_distribution.values()) == distribution
        assert list(section.level_distribution) == ["A1", "A2", "B1", "B2", "C1", "C2"]
        assert sum(section.level_distribution.values()) == section.n_items


def test_section_rules():
    call_command("seed_formats")
    for section in FormatSection.objects.all():
        if section.section == "CO":
            assert (section.allow_back, section.auto_advance) == (False, True)
            assert section.answer_window_seconds == 12
        else:
            assert (section.allow_back, section.auto_advance) == (True, False)
            assert section.answer_window_seconds is None


@pytest.mark.parametrize(
    ("mode", "questions", "minutes"), [("rapide", 24, 28), ("moyen", 46, 52), ("reel", 91, 103)]
)
def test_totals_per_mode(mode, questions, minutes):
    call_command("seed_formats")
    exam_format = ExamFormat.objects.get(mode=mode)
    assert exam_format.n_items == questions
    assert exam_format.duration_minutes == minutes


def test_co_reading_time_and_part_attempts_are_seeded(formats):
    """15 s de lecture avant chaque document de CO (4.2) ; épreuve seule en moyen (8.1 ter)."""
    for exam_format in formats.values():
        co = exam_format.sections.get(section="CO")
        assert co.read_before_seconds == 15
        assert {s.read_before_seconds for s in exam_format.sections.exclude(section="CO")} == {None}
    assert {mode: f.allow_part_attempts for mode, f in formats.items()} == {
        "rapide": False,
        "moyen": True,
        "reel": False,
    }


def test_seed_formats_keeps_the_admin_choice_for_part_attempts(formats):
    ExamFormat.objects.filter(mode="moyen").update(allow_part_attempts=False)
    call_command("seed_formats")
    assert not ExamFormat.objects.get(mode="moyen").allow_part_attempts


def test_migration_moves_old_co_durations(formats):
    """Les bases existantes passent de 480, 900 et 1740 s à 615, 1155 et 2250 s."""
    import importlib

    from django.apps import apps

    migration = importlib.import_module("bank.migrations.0009_sound_reading_parts")
    for mode, (old, _) in migration.CO_DURATIONS.items():
        FormatSection.objects.filter(format__mode=mode, section="CO").update(
            duration_seconds=old, read_before_seconds=None
        )
    ExamFormat.objects.update(allow_part_attempts=False)
    migration.reading_and_parts(apps, None)
    assert {
        s.format.mode: (s.duration_seconds, s.read_before_seconds)
        for s in FormatSection.objects.filter(section="CO")
    } == {"rapide": (615, 15), "moyen": (1155, 15), "reel": (2250, 15)}
    assert ExamFormat.objects.get(mode="moyen").allow_part_attempts


def test_seed_formats_is_idempotent():
    call_command("seed_formats")
    section = FormatSection.objects.get(format__mode="rapide", section="CO")
    section.answer_window_seconds = 20
    section.save()
    call_command("seed_formats")
    assert ExamFormat.objects.count() == 3
    assert FormatSection.objects.count() == 9


def test_distribution_must_sum_to_n_items(formats):
    section = formats["rapide"].sections.get(section="MSL")
    section.level_distribution = {**section.level_distribution, "A1": 2}
    with pytest.raises(ValidationError):
        section.full_clean()


def test_home_shows_modes_from_database(client, formats):
    html = client.get("/").content.decode()
    assert "24 questions" in html
    assert "28 minutes" in html
    assert "91 questions" in html
    assert "103 minutes" in html
