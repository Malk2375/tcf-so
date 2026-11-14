"""Brief de génération (SPEC 6.1), construit sans IA."""

import random
from collections import Counter

from django.db import transaction
from django.db.models import Max

from bank.constants import (
    AVOID_MAX_LINES,
    AVOID_SUPPORT_CHARS,
    LANGUAGE_POINTS,
    LETTERS,
    LEVELS,
    expected_options_mode,
    length_target,
    subtypes_for,
)
from bank.models import Brief, ExamFormat, Item, Test
from bank.themes import themes


def balanced_letters(n: int, rng: random.Random) -> list[str]:
    """n lettres A à D, chacune présente ⌊n/4⌋ ou ⌈n/4⌉ fois, dans un ordre aléatoire."""
    letters = LETTERS * (n // len(LETTERS)) + rng.sample(LETTERS, n % len(LETTERS))
    rng.shuffle(letters)
    return letters


def _least_used(candidates: list[str], usage: Counter, rng: random.Random) -> str:
    lowest = min(usage[c] for c in candidates)
    return rng.choice([c for c in candidates if usage[c] == lowest])


def next_number(exam_format: ExamFormat) -> int:
    """Numéro suivant pour ce format : ni un test ni un brief ne l'utilisent déjà."""
    highest = max(
        Test.objects.filter(format=exam_format).aggregate(n=Max("number"))["n"] or 0,
        Brief.objects.filter(format=exam_format).aggregate(n=Max("number"))["n"] or 0,
    )
    return highest + 1


def _avoid_lines(theme_ids: set[str]) -> list[str]:
    lines = []
    for item in Item.objects.exclude(status=Item.Status.REJECTED).order_by("-created_at"):
        if item.tags.get("theme") not in theme_ids:
            continue
        support = " ".join(item.support_text.split())[:AVOID_SUPPORT_CHARS]
        question = item.question.strip()
        lines.append(f"{support} — {question}" if question else support)
        if len(lines) >= AVOID_MAX_LINES:
            break
    return lines


def build_brief(exam_format: ExamFormat, number: int, rng: random.Random | None = None) -> dict:
    rng = rng or random.Random()
    theme_ids = list(themes())
    bank = list(Item.objects.exclude(status=Item.Status.REJECTED).values("section", "tags"))

    slots = []
    for format_section in exam_format.sections.order_by("order"):
        section = format_section.section
        theme_usage = Counter(i["tags"].get("theme") for i in bank if i["section"] == section)
        point_usage = Counter(
            i["tags"].get("language_point") for i in bank if i["section"] == section
        )
        letters = balanced_letters(format_section.n_items, rng)
        used_themes: set[str] = set()

        for level in LEVELS:
            for _ in range(format_section.level_distribution.get(level, 0)):
                subtype = rng.choice(subtypes_for(section, level))
                # Thème le moins utilisé en banque pour cette épreuve,
                # jamais deux fois dans l'épreuve.
                theme = _least_used(
                    [t for t in theme_ids if t not in used_themes], theme_usage, rng
                )
                used_themes.add(theme)
                slot = {
                    "slot": len(slots) + 1,
                    "section": section,
                    "level": level,
                    "subtype": subtype,
                    "theme": theme,
                }
                if section == "MSL":
                    point = _least_used(LANGUAGE_POINTS[level], point_usage, rng)
                    point_usage[point] += 1
                    slot["language_point"] = point
                slot["correct_letter"] = letters.pop()
                slot["target"] = length_target(section, level, subtype)
                slot["options_mode"] = expected_options_mode(section, level, subtype)
                slots.append(slot)

    return {
        "brief_id": f"{exam_format.code}-{number:04d}",
        "format": exam_format.code,
        "slots": slots,
        "avoid": _avoid_lines({s["theme"] for s in slots}),
    }


@transaction.atomic
def create_brief(exam_format: ExamFormat, user=None, rng: random.Random | None = None) -> Brief:
    number = next_number(exam_format)
    content = build_brief(exam_format, number, rng)
    return Brief.objects.create(
        format=exam_format,
        number=number,
        brief_id=content["brief_id"],
        content=content,
        created_by=user,
    )
