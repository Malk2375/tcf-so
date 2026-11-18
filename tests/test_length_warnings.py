"""Avertissements de longueur (6.3) : CO et CE seulement, bonne réponse nettement plus longue."""

import random
from pathlib import Path

import pytest
from django.core.management import call_command

from bank.devtest import make_dev_draft
from bank.importer import ImportReport, _warnings, correct_length_warning, refresh_warnings
from bank.models import Item

ROOT_CMD = Path(__file__).resolve().parent.parent / "demarrer.cmd"


def item(section, options, correct=0, slot=1):
    return {
        "section": section,
        "level": "B1",
        "subtype": "x",
        "options": options,
        "correct_index": correct,
        "options_mode": "text",
        "tags": {"skill": "inference", "theme": "travail"},
        "slot": slot,
        "_file": None,
    }


@pytest.mark.parametrize(
    "options, warned",
    [
        (
            ["peut-être", "oui", "non", "si"],
            False,
        ),  # options d'un mot : moins de 12 caractères d'écart
        (["Il vient demain matin", "Il part", "Il reste", "Il rentre"], True),
        (["Il vient", "Il part en voyage pour longtemps", "Il reste", "Il rentre"], False),
        (["a" * 22, "a" * 10, "a" * 10, "a" * 10], True),  # +120 %, +12 caractères
        (["a" * 21, "a" * 10, "a" * 10, "a" * 10], False),  # +11 caractères seulement
        (["a" * 55, "a" * 40, "a" * 40, "a" * 40], False),  # +15 caractères mais +37,5 %
        (["a" * 56, "a" * 40, "a" * 40, "a" * 40], True),  # +40 % et +16 caractères
    ],
)
def test_correct_answer_rule(options, warned):
    assert bool(correct_length_warning(item("CE", options))) is warned
    assert bool(correct_length_warning(item("CO", options))) is warned
    assert correct_length_warning(item("MSL", options)) is None  # jamais en MSL


@pytest.mark.django_db
def test_section_warning_counts_strictly_longest_only():
    tie = ["a" * 30, "a" * 30, "b", "c"]
    longest = ["a" * 30, "b", "c", "d"]
    report = ImportReport()
    _warnings([item("CE", tie, slot=n) for n in range(1, 6)], report)
    assert not any("strictement" in w.message for w in report.warnings)
    report = ImportReport()
    _warnings([item("CE", longest, slot=n) for n in range(1, 6)], report)
    assert any(
        w.message.startswith("CE : la bonne réponse est strictement") for w in report.warnings
    )
    report = ImportReport()
    _warnings([item("MSL", longest, slot=n) for n in range(1, 6)], report)
    assert not any("plus longue" in w.message for w in report.warnings)


@pytest.mark.django_db
def test_refresh_warnings_replaces_old_rule_warnings(formats, staff_user, capsys):
    draft = make_dev_draft("rapide", rng=random.Random(8), user=staff_user)
    batch = draft.import_batch
    batch.report = {
        **batch.report,
        "warnings": [{"file": "x.json", "slot": 1, "message": "L'option A est bien plus longue."}],
    }
    batch.save()
    ce = Item.objects.filter(test_item__test=draft, section="CE").first()
    ce.options = [*ce.options]
    ce.options[ce.correct_index] = ce.options[ce.correct_index] + " " + "x" * 60
    ce.save()
    call_command("refresh_warnings")
    assert "1 test(s) en brouillon" in capsys.readouterr().out
    batch.refresh_from_db()
    messages = [(w["slot"], w["message"]) for w in batch.report["warnings"]]
    assert (1, "L'option A est bien plus longue.") not in messages
    slot = ce.test_item.slot
    assert any(s == slot and m.startswith("La bonne réponse") for s, m in messages)
    assert refresh_warnings(draft)  # appelable directement, même résultat


def test_demarrer_recomputes_warnings_after_migrations():
    text = ROOT_CMD.read_bytes().decode("ascii")
    assert text.index("manage.py migrate") < text.index("manage.py refresh_warnings")
