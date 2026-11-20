"""Brief de génération (SPEC 6.1), construit sans IA."""

import random
from collections import Counter, deque

from django.db import transaction
from django.db.models import Max

from bank.constants import (
    ACCENT_SUBTYPES,
    AVOID_MAX_LINES,
    AVOID_SUPPORT_CHARS,
    DEFAULT_SETTING,
    LETTERS,
    LEVELS,
    allowed_combinations,
    expected_options_mode,
    length_target,
    subtypes_for,
)
from bank.language_points import FAMILY_QUOTAS, language_points, points_at
from bank.models import Brief, ExamFormat, Item, Test
from bank.themes import themes
from usage.models import SiteSettings


class BriefError(Exception):
    """Brief impossible à construire : message en français, affichable tel quel."""


def balanced_letters(n: int, rng: random.Random) -> list[str]:
    """n lettres A à D, chacune présente ⌊n/4⌋ ou ⌈n/4⌉ fois, dans un ordre aléatoire."""
    letters = LETTERS * (n // len(LETTERS)) + rng.sample(LETTERS, n % len(LETTERS))
    rng.shuffle(letters)
    return letters


def _least_used(candidates: list[str], usage: Counter, rng: random.Random) -> str:
    lowest = min(usage[c] for c in candidates)
    return rng.choice([c for c in candidates if usage[c] == lowest])


# --- MSL : familles puis points de langue (6.1) --------------------------------------------------


def _max_flow(graph: dict, source, sink) -> int:
    """Flot maximal (Edmonds-Karp) ; `graph[u][v]` est la capacité restante, modifiée sur place."""
    flow = 0
    while True:
        parent = {source: None}
        queue = deque([source])
        while queue and sink not in parent:
            node = queue.popleft()
            for neighbour, capacity in graph.get(node, {}).items():
                if capacity > 0 and neighbour not in parent:
                    parent[neighbour] = node
                    queue.append(neighbour)
        if sink not in parent:
            return flow
        node = sink
        while parent[node] is not None:
            previous = parent[node]
            graph[previous][node] -= 1
            graph.setdefault(node, {}).setdefault(previous, 0)
            graph[node][previous] += 1
            node = previous
        flow += 1


def _feasible(levels: list[str], quotas: dict, families: dict, points: dict) -> bool:
    """Peut-on donner à chaque emplacement un point disponible à son niveau, jamais deux fois
    le même, en respectant exactement les quotas de familles et les choix déjà faits ?

    Flot : source → emplacement → point → famille (capacité = quota) → puits.
    """
    graph: dict = {"source": {}}
    for index, level in enumerate(levels):
        graph["source"][("slot", index)] = 1
        candidates = [points[index]] if index in points else points_at(level, families.get(index))
        graph[("slot", index)] = {("point", p): 1 for p in candidates}
    for point_id, point in language_points().items():
        graph[("point", point_id)] = {("family", point["family"]): 1}
    for family, quota in quotas.items():
        graph[("family", family)] = {"sink": quota}
    return _max_flow(graph, "source", "sink") == len(levels)


def assign_language_points(
    levels: list[str], quotas: dict, usage: Counter, rng: random.Random
) -> list[tuple[str, str]]:
    """(famille, point) pour chaque emplacement de MSL, dans l'ordre de `levels`.

    1. Famille : quotas du mode exactement respectés, une famille seulement à un niveau où elle a
       encore un point libre ; tirage au sort parmi les attributions possibles.
    2. Point : le moins utilisé en banque d'abord, jamais deux fois le même dans le test.
    À chaque choix, on vérifie qu'il reste une solution complète : on ne se bloque jamais en route.
    """
    if sum(quotas.values()) != len(levels) or not _feasible(levels, quotas, {}, {}):
        raise BriefError(
            "Brief impossible : les quotas de familles de MSL ne peuvent pas être respectés avec "
            "les points de langue disponibles à ces niveaux."
        )
    families: dict[int, str] = {}
    for index in rng.sample(range(len(levels)), len(levels)):
        for family in rng.sample(sorted(quotas), len(quotas)):
            if quotas[family] and _feasible(levels, quotas, {**families, index: family}, {}):
                families[index] = family
                break
    points: dict[int, str] = {}
    for index in range(len(levels)):
        candidates = points_at(levels[index], families[index])
        rng.shuffle(candidates)
        candidates.sort(key=lambda p: usage[p])
        for point in candidates:
            if point not in points.values() and _feasible(
                levels, quotas, families, {**points, index: point}
            ):
                points[index] = point
                break
    return [(families[i], points[i]) for i in range(len(levels))]


# --- CO : lieu, canal et accent (7.5.2) ----------------------------------------------------------


def pick_sound(subtype: str, used: Counter, rng: random.Random) -> tuple[str, str]:
    """(lieu, canal) tiré parmi les combinaisons du sous-type, en variant : jamais deux fois le
    même lieu dans une épreuve tant qu'un autre lieu permis reste libre (« aucun » n'est pas un
    lieu et peut revenir)."""
    combinations = allowed_combinations(subtype)
    lowest = min(0 if setting == DEFAULT_SETTING else used[setting] for _, setting in combinations)
    candidates = [
        (channel, setting)
        for channel, setting in combinations
        if setting == DEFAULT_SETTING or used[setting] == lowest
    ]
    channel, setting = rng.choice(candidates)
    used[setting] += 1
    return setting, channel


def accent_slots(co_slots: list[dict], quota: int, rng: random.Random) -> set[int]:
    """Numéros des emplacements de CO qui reçoivent l'accent québécois, parmi les sous-types
    permis."""
    eligible = [slot["slot"] for slot in co_slots if slot["subtype"] in ACCENT_SUBTYPES]
    return set(rng.sample(eligible, min(max(quota, 0), len(eligible))))


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
        letters = balanced_letters(format_section.n_items, rng)
        used_themes: set[str] = set()
        levels = [
            level
            for level in LEVELS
            for _ in range(format_section.level_distribution.get(level, 0))
        ]
        if section == "MSL":
            point_usage = Counter(
                i["tags"].get("language_point") for i in bank if i["section"] == section
            )
            language = assign_language_points(
                levels, FAMILY_QUOTAS[exam_format.mode], point_usage, rng
            )

        used_settings: Counter = Counter()
        for index, level in enumerate(levels):
            subtype = rng.choice(subtypes_for(section, level))
            # Thème le moins utilisé en banque pour cette épreuve, jamais deux fois dans l'épreuve.
            theme = _least_used([t for t in theme_ids if t not in used_themes], theme_usage, rng)
            used_themes.add(theme)
            slot = {
                "slot": len(slots) + 1,
                "section": section,
                "level": level,
                "subtype": subtype,
                "theme": theme,
            }
            if section == "CO":
                slot["setting"], slot["channel"] = pick_sound(subtype, used_settings, rng)
            if section == "MSL":
                family, point = language[index]
                slot["language_family"] = family
                slot["language_point"] = point
                slot["language_hint"] = language_points()[point]["hint"]
            slot["correct_letter"] = letters.pop()
            slot["target"] = length_target(section, level, subtype)
            slot["options_mode"] = expected_options_mode(section, level, subtype)
            slots.append(slot)

    quota = SiteSettings.load().accent_quotas.get(exam_format.mode, 0)
    chosen = accent_slots([s for s in slots if s["section"] == "CO"], quota, rng)
    slots = [_with_accent(slot) if slot["slot"] in chosen else slot for slot in slots]

    return {
        "brief_id": f"{exam_format.code}-{number:04d}",
        "format": exam_format.code,
        "slots": slots,
        "avoid": _avoid_lines({s["theme"] for s in slots}),
    }


def _with_accent(slot: dict) -> dict:
    """Ajoute "accent": "fr-CA" juste après le canal, comme dans l'exemple de la SPEC."""
    ordered = {}
    for key, value in slot.items():
        ordered[key] = value
        if key == "channel":
            ordered["accent"] = "fr-CA"
    return ordered


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
