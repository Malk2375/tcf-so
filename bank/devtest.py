"""Test factice complet et publié (SPEC 6.5). Réservé au développement."""

import random

from django.db import transaction
from django.utils import timezone

from bank.audio import store_placeholder_audio
from bank.brief import build_brief, next_number
from bank.constants import LEVELS
from bank.content import content_hash, doc_length, support_text, word_count
from bank.devdata import fake_item
from bank.models import ExamFormat, Item, Test, TestItem


@transaction.atomic
def make_dev_test(mode: str, rng: random.Random | None = None) -> Test:
    rng = rng or random.Random()
    exam_format = ExamFormat.objects.get(mode=mode)
    number = next_number(exam_format)
    brief = build_brief(exam_format, number, rng)
    test = Test.objects.create(
        format=exam_format,
        number=number,
        status=Test.Status.PUBLISHED,
        published_at=timezone.now(),
    )
    level_rank = {level: rank for rank, level in enumerate(LEVELS)}
    positions: dict[str, int] = {}
    slots = sorted(brief["slots"], key=lambda s: (s["section"], level_rank[s["level"]], s["slot"]))
    for slot in slots:
        data = fake_item(slot, rng)
        section = data["section"]
        item = Item.objects.create(
            section=section,
            level=data["level"],
            subtype=data["subtype"],
            stimulus_text=data["stimulus_text"] or "",
            audio_script=data["audio_script"],
            question=data["question"],
            options=data["options"],
            correct_index=data["correct_index"],
            options_mode=data["options_mode"],
            explanation=data["explanation"],
            tags={**data["tags"], "doc_length": doc_length(data["level"])},
            word_count=word_count(
                support_text(section, data["stimulus_text"], data["audio_script"])
            ),
            content_hash=content_hash(data),
            status=Item.Status.PUBLISHED,
            reviewed_at=timezone.now(),
        )
        positions[section] = positions.get(section, 0) + 1
        TestItem.objects.create(
            test=test, item=item, section=section, position=positions[section], slot=slot["slot"]
        )
        if section == "CO":
            store_placeholder_audio(item)
    return test
