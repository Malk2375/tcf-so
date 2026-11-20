"""Constats calculés sans IA à partir des réponses de l'utilisateur (SPEC 12.2).

Chaque constat est un dictionnaire JSON stocké dans `Advice.findings` : `id`, `type`, `section`,
`label`, `filter` (le groupe de questions concerné), `n`, `rate`, `reference_rate`, `gap`,
`strength` (écart × nombre de questions), `intensity`, `details`, `errors`.
Les taux restent dans la base : le modèle n'en reçoit aucun (voir `advice.llm.model_findings`).
"""

import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from fractions import Fraction

from advice.eligibility import retained_attempts
from advice.labels import SECTION_SHORT, SKILL_LABELS, SUBTYPE_LABELS
from bank.constants import CE_WORDS, LEVELS, MSL_WORDS
from bank.language_points import FAMILY_LABELS, family_of
from bank.models import FormatSection
from exam.models import Answer, Attempt, Level
from exam.scoring import round_half_up

RECENT_ATTEMPTS = 10
MAX_FINDINGS = 3
MAX_ERRORS = 3
MSL_FAMILY_MAX_ERRORS = 4
# Constat `progress` : les 2 derniers passages comparés aux précédents.
PROGRESS_RECENT = 2

# Seuils de la section 12.2.
LEVEL_BREAK_DROP = 30
LEVEL_BREAK_MIN = 4
MSL_FAMILY_MIN = 3
MSL_FAMILY_MIN_ERRORS = 2
MSL_FAMILY_MAX_RATE = 50
GROUP_MIN = 5
GROUP_GAP = 15
TRAILING_UNANSWERED_PER_TEST = 2
FIRST_HALF_SHARE = 60
CHANGES_MIN = 6
UNFAVORABLE_SHARE = 50
# Intensité : « très marqué » à partir de 30 points d'écart, « marqué » sinon.
STRONG_GAP = 30

QCM_TIMED = ("MSL", "CE")  # épreuves à échéance, où le temps se gère


@dataclass
class Row:
    """Une réponse, avec ce qu'il faut savoir de la question."""

    answer: Answer
    attempt_id: int
    finished_at: datetime
    section: str
    level: str
    subtype: str
    tags: dict
    position: int
    correct: bool
    selected: int | None
    correct_index: int
    time_ms: int
    history: list[int] = field(default_factory=list)

    @property
    def item(self):
        return self.answer.test_item.item


def pct(part: int, total: int) -> int:
    return round_half_up(Fraction(100 * part, total)) if total else 0


def recent_attempts(user, limit: int = RECENT_ATTEMPTS) -> list[Attempt]:
    """Les 10 derniers passages retenus : le premier passage terminé de chaque test."""
    return retained_attempts(user)[:limit]


def load_rows(attempts: list[Attempt]) -> list[Row]:
    finished = {a.pk: a.finished_at for a in attempts}
    answers = Answer.objects.filter(attempt__in=attempts).select_related(
        "test_item__item", "test_item__item__audio"
    )
    rows = []
    for answer in answers:
        test_item, item = answer.test_item, answer.test_item.item
        rows.append(
            Row(
                answer=answer,
                attempt_id=answer.attempt_id,
                finished_at=finished[answer.attempt_id],
                section=test_item.section,
                level=item.level,
                subtype=item.subtype,
                tags=item.tags or {},
                position=test_item.position,
                correct=answer.is_correct,
                selected=answer.selected_index,
                correct_index=item.correct_index,
                time_ms=answer.time_spent_ms,
                history=[entry["index"] for entry in answer.choice_history or []],
            )
        )
    return rows


# --- Groupes de questions -----------------------------------------------------------------------


def matches(row: Row, group: dict) -> bool:
    """Filtre d'un constat : épreuve, niveau, sous-type, longueur, compétence, point de langue,
    famille de MSL et niveau maximal."""
    checks = {
        "section": row.section,
        "level": row.level,
        "subtype": row.subtype,
        "doc_length": row.tags.get("doc_length"),
        "skill": row.tags.get("skill"),
        "language_point": row.tags.get("language_point"),
        "family": family_of(row.tags.get("language_point")),
    }
    sections = group.get("sections")
    if sections and row.section not in sections:
        return False
    max_level = group.get("max_level")
    if max_level and LEVELS.index(row.level) > LEVELS.index(max_level):
        return False
    return all(checks[key] == value for key, value in group.items() if key in checks)


def rate_of(rows: list[Row]) -> int:
    return pct(sum(r.correct for r in rows), len(rows))


def section_level_rates(rows: list[Row]) -> dict[tuple[str, str], Fraction]:
    groups = defaultdict(list)
    for row in rows:
        groups[(row.section, row.level)].append(row.correct)
    return {key: Fraction(sum(values), len(values)) for key, values in groups.items()}


def expected_rate(group_rows: list[Row], by_level: dict) -> int:
    """Taux moyen de l'utilisateur au même niveau (et dans la même épreuve) que ces questions."""
    total = sum((by_level[(r.section, r.level)] for r in group_rows), Fraction(0))
    return round_half_up(100 * total / len(group_rows))


# --- Questions manquées jointes à un constat ----------------------------------------------------


def support_for(row: Row) -> str:
    item = row.item
    if row.section == "CO":
        lines = (item.audio_script or {}).get("lines", [])
        return "\n".join(f"{line['speaker']} : {line['text']}" for line in lines)
    return item.stimulus_text


def error_payload(row: Row) -> dict:
    item = row.item
    return {
        "test_item_id": row.answer.test_item_id,
        "attempt_id": row.attempt_id,
        "section": row.section,
        "level": row.level,
        "support": support_for(row),
        "question": item.question,
        "options": list(item.options),
        "chosen_index": row.selected,
        "correct_index": item.correct_index,
        "explanation": item.explanation,
        "language_point": row.tags.get("language_point") if row.section == "MSL" else None,
    }


def _missed(rows: list[Row]) -> list[Row]:
    missed = [r for r in rows if not r.correct]
    missed.sort(key=lambda r: (-r.finished_at.timestamp(), r.selected is None, r.position))
    return missed


def recent_errors(rows: list[Row], limit: int = MAX_ERRORS) -> list[dict]:
    return [error_payload(r) for r in _missed(rows)[:limit]]


def varied_errors(rows: list[Row], limit: int = MSL_FAMILY_MAX_ERRORS) -> list[dict]:
    """Les erreurs les plus récentes, de points de langue différents si possible."""
    missed = _missed(rows)
    chosen, points = [], set()
    for row in missed:
        point = row.tags.get("language_point")
        if point not in points:
            chosen.append(row)
            points.add(point)
    chosen += [r for r in missed if r not in chosen]
    return [error_payload(r) for r in chosen[:limit]]


def intensity(gap: int) -> str:
    return "très marqué" if gap >= STRONG_GAP else "marqué"


def finding(
    kind: str,
    key: str,
    section: str,
    label: str,
    group: dict,
    rows: list[Row],
    reference: int,
    gap: int,
    n: int | None = None,
    details=None,
    errors=None,
) -> dict:
    n = len(rows) if n is None else n
    return {
        "id": f"{kind}:{key}",
        "type": kind,
        "section": section,
        "label": label,
        "filter": group,
        "n": n,
        "rate": rate_of(rows),
        "reference_rate": reference,
        "gap": gap,
        "strength": gap * n,
        "intensity": intensity(gap),
        "details": details or {},
        "errors": recent_errors(rows) if errors is None else errors,
    }


# --- Les types de constats ----------------------------------------------------------------------


def level_breaks(rows: list[Row]) -> list[dict]:
    """Chute d'au moins 30 points entre deux niveaux consécutifs, 4 questions au moins de chaque
    côté."""
    found = []
    for section in ("CO", "MSL", "CE"):
        for low, high in zip(LEVELS, LEVELS[1:], strict=False):
            below = [r for r in rows if r.section == section and r.level == low]
            above = [r for r in rows if r.section == section and r.level == high]
            if len(below) < LEVEL_BREAK_MIN or len(above) < LEVEL_BREAK_MIN:
                continue
            drop = rate_of(below) - rate_of(above)
            if drop < LEVEL_BREAK_DROP:
                continue
            found.append(
                finding(
                    "level_break",
                    f"{section}:{high}",
                    section,
                    f"{SECTION_SHORT[section].capitalize()} : passage de {low} à {high}",
                    {"section": section, "level": high},
                    above,
                    reference=rate_of(below),
                    gap=drop,
                    n=len(below) + len(above),
                    details={
                        "from_level": low,
                        "to_level": high,
                        "rate_from": rate_of(below),
                        "rate_to": rate_of(above),
                        "n_from": len(below),
                        "n_to": len(above),
                    },
                )
            )
    return found


def msl_level(attempts: list[Attempt]) -> str | None:
    """Niveau MSL estimé : celui de l'épreuve de MSL du dernier passage retenu qui en a une ;
    « A1 non atteint » compte comme A1."""
    # Le dernier passage retenu peut être une épreuve seule sans MSL (8.1 ter) : on prend le
    # plus récent qui a une épreuve de MSL notée.
    for attempt in attempts:
        section = attempt.sections.filter(section="MSL").first()
        if section is not None and section.level:
            return "A1" if section.level == Level.BELOW_A1 else section.level
    return None


def msl_families(rows: list[Row], estimated_level: str | None) -> list[dict]:
    """Taux par famille de points de langue, sur les questions de MSL de niveau inférieur ou égal
    au niveau MSL estimé + 1 : au moins 3 questions, au moins 2 erreurs, et au plus la moitié
    de bonnes réponses."""
    msl = [r for r in rows if r.section == "MSL"]
    msl_rate = rate_of(msl)
    max_level = None
    if estimated_level:
        max_level = LEVELS[min(LEVELS.index(estimated_level) + 1, len(LEVELS) - 1)]
    groups = defaultdict(list)
    for row in msl:
        family = family_of(row.tags.get("language_point"))
        if family and (max_level is None or LEVELS.index(row.level) <= LEVELS.index(max_level)):
            groups[family].append(row)
    found = []
    for family, group_rows in sorted(groups.items()):
        n, n_correct = len(group_rows), sum(r.correct for r in group_rows)
        if n < MSL_FAMILY_MIN or n - n_correct < MSL_FAMILY_MIN_ERRORS or 2 * n_correct > n:
            continue
        rate = rate_of(group_rows)
        # Écart : à la moyenne de l'utilisateur en MSL, ou au seuil de 50 % s'il est plus grand.
        gap = max(msl_rate - rate, MSL_FAMILY_MAX_RATE - rate)
        group = {"section": "MSL", "family": family}
        if max_level:
            group["max_level"] = max_level
        found.append(
            finding(
                "msl_family",
                family,
                "MSL",
                f"Structures de la langue : {FAMILY_LABELS[family]}",
                group,
                group_rows,
                reference=msl_rate,
                gap=gap,
                details={
                    "family": family,
                    "levels": sorted({r.level for r in group_rows}),
                    "language_points": sorted(
                        {r.tags["language_point"] for r in group_rows if not r.correct}
                    ),
                    "max_level": max_level,
                },
                errors=varied_errors(group_rows),
            )
        )
    return found


def _below_average_groups(rows: list[Row], kind: str, tag: str, sections, labels) -> list[dict]:
    by_level = section_level_rates(rows)
    groups = defaultdict(list)
    for row in rows:
        if row.section not in sections:
            continue
        value = row.subtype if tag == "subtype" else row.tags.get(tag)
        if value:
            groups[(row.section, value)].append(row)
    found = []
    for (section, value), group_rows in sorted(groups.items()):
        if len(group_rows) < GROUP_MIN:
            continue
        expected = expected_rate(group_rows, by_level)
        gap = expected - rate_of(group_rows)
        if gap < GROUP_GAP:
            continue
        key = f"{section}:{value}" if kind == "skill" else f"{section}:{tag}:{value}"
        found.append(
            finding(
                kind,
                key,
                section,
                f"{SECTION_SHORT[section].capitalize()} : {labels.get(value, value)}",
                {"section": section, tag: value},
                group_rows,
                reference=expected,
                gap=gap,
                details={tag: value, "levels": sorted({r.level for r in group_rows})},
            )
        )
    return found


def doc_types(rows: list[Row]) -> list[dict]:
    """Taux par sous-type (CO, CE), comparé au taux moyen de l'utilisateur au même niveau.
    La longueur découle du niveau : elle n'est pas un critère séparé."""
    return _below_average_groups(rows, "doc_type", "subtype", ("CO", "CE"), SUBTYPE_LABELS)


def skills(rows: list[Row]) -> list[dict]:
    """Compétences de CO et de CE seulement : la MSL passe par `msl_family`."""
    return _below_average_groups(rows, "skill", "skill", ("CO", "CE"), SKILL_LABELS)


def _midpoint(section: str, level: str) -> Fraction:
    low, high = (CE_WORDS if section == "CE" else MSL_WORDS)[level]
    return Fraction(low + high, 2)


def target_seconds(attempts: list[Attempt]) -> dict:
    """Temps cible par question, par épreuve et par niveau : la durée de l'épreuve du dernier
    format passé, répartie au prorata de la longueur cible des supports de chaque niveau."""
    if not attempts:
        return {}
    exam_format = attempts[0].test.format
    targets = {}
    for fs in FormatSection.objects.filter(format=exam_format, section__in=QCM_TIMED):
        weights = sum(
            n * _midpoint(fs.section, level) for level, n in fs.level_distribution.items()
        )
        targets[fs.section] = {
            "mode": exam_format.mode,
            "section_seconds": fs.duration_seconds,
            "questions": fs.n_items,
            "average_seconds": round_half_up(Fraction(fs.duration_seconds, fs.n_items)),
            "by_level": {
                level: round_half_up(fs.duration_seconds * _midpoint(fs.section, level) / weights)
                for level in LEVELS
                if fs.level_distribution.get(level)
            },
        }
    return targets


def time_metrics(rows: list[Row]) -> dict:
    """Questions sans réponse en fin d'épreuve et part du temps passée sur la première moitié."""
    by_section = defaultdict(list)
    for row in rows:
        if row.section in QCM_TIMED:
            by_section[(row.attempt_id, row.section)].append(row)
    trailing = 0
    first_half_ms = total_ms = 0
    trailing_rows = []
    for section_rows in by_section.values():
        section_rows.sort(key=lambda r: r.position)
        tail = []
        for row in reversed(section_rows):
            if row.selected is not None:
                break
            tail.append(row)
        trailing += len(tail)
        trailing_rows.extend(tail)
        half = len(section_rows) // 2
        first_half_ms += sum(r.time_ms for r in section_rows[:half])
        total_ms += sum(r.time_ms for r in section_rows)
    n_attempts = len({attempt_id for attempt_id, _ in by_section})
    medians = defaultdict(dict)
    for section in QCM_TIMED:
        for level in LEVELS:
            times = [
                r.time_ms for r in rows if r.section == section and r.level == level and r.time_ms
            ]
            if times:
                medians[section][level] = round_half_up(Fraction(statistics.median(times)) / 1000)
    return {
        "attempts": n_attempts,
        "trailing_unanswered": trailing,
        "trailing_per_test": float(round_half_up(Fraction(10 * trailing, n_attempts)) / 10)
        if n_attempts
        else 0.0,
        "first_half_share": pct(first_half_ms, total_ms),
        "median_seconds": dict(medians),
        "trailing_rows": trailing_rows,
        "questions": sum(len(v) for v in by_section.values()),
    }


def time_pressure(rows: list[Row], attempts: list[Attempt]) -> list[dict]:
    metrics = time_metrics(rows)
    if not metrics["attempts"]:
        return []
    per_test = Fraction(metrics["trailing_unanswered"], metrics["attempts"])
    too_slow_at_end = per_test >= TRAILING_UNANSWERED_PER_TEST
    front_loaded = metrics["first_half_share"] > FIRST_HALF_SHARE
    if not (too_slow_at_end or front_loaded):
        return []
    timed = [r for r in rows if r.section in QCM_TIMED]
    gap = max(
        metrics["first_half_share"] - 50,
        pct(metrics["trailing_unanswered"], metrics["questions"]),
    )
    trailing_rows = metrics.pop("trailing_rows")
    trailing_rows.sort(key=lambda r: (-r.finished_at.timestamp(), r.position))
    return [
        finding(
            "time_pressure",
            "MSL_CE",
            "MSL",
            "Gestion du temps en structures de la langue et en compréhension écrite",
            {"sections": list(QCM_TIMED)},
            timed,
            reference=50,
            gap=gap,
            n=metrics["questions"],
            details={
                **metrics,
                "unanswered_at_end": too_slow_at_end,
                "slow_start": front_loaded,
                "targets": target_seconds(attempts),
            },
            errors=[error_payload(r) for r in trailing_rows[:MAX_ERRORS]],
        )
    ]


def change_metrics(rows: list[Row]) -> dict:
    changes = unfavorable = favorable = 0
    unfavorable_rows = []
    for row in rows:
        hurt = False
        for before, after in zip(row.history, row.history[1:], strict=False):
            changes += 1
            if before == row.correct_index and after != row.correct_index:
                unfavorable += 1
                hurt = True
            elif before != row.correct_index and after == row.correct_index:
                favorable += 1
        if hurt and not row.correct:
            unfavorable_rows.append(row)
    return {
        "changes": changes,
        "unfavorable": unfavorable,
        "favorable": favorable,
        "unfavorable_share": pct(unfavorable, changes),
        "rows": unfavorable_rows,
    }


def answer_changes(rows: list[Row]) -> list[dict]:
    metrics = change_metrics(rows)
    if metrics["changes"] < CHANGES_MIN or metrics["unfavorable_share"] <= UNFAVORABLE_SHARE:
        return []
    hurt_rows = metrics.pop("rows")
    hurt_rows.sort(key=lambda r: (-r.finished_at.timestamp(), r.position))
    return [
        finding(
            "answer_changes",
            "all",
            "",
            "Changements de réponse",
            {},
            rows,
            reference=UNFAVORABLE_SHARE,
            gap=metrics["unfavorable_share"] - UNFAVORABLE_SHARE,
            n=metrics["changes"],
            details=metrics,
            errors=[error_payload(r) for r in hurt_rows[:MAX_ERRORS]],
        )
    ]


# --- Constat `progress` ------------------------------------------------------------------------


def _metric(previous: dict, rows: list[Row]) -> dict:
    """Mesure d'un constat précédent sur un sous-ensemble de passages."""
    kind = previous["type"]
    if kind == "time_pressure":
        metrics = time_metrics(rows)
        metrics.pop("trailing_rows")
        return {
            "n": metrics["attempts"],
            "trailing_per_test": metrics["trailing_per_test"],
            "first_half_share": metrics["first_half_share"],
        }
    if kind == "answer_changes":
        metrics = change_metrics(rows)
        return {"n": metrics["changes"], "unfavorable_share": metrics["unfavorable_share"]}
    group = [r for r in rows if matches(r, previous.get("filter", {}))]
    return {"n": len(group), "rate": rate_of(group)}


def trend(entry: dict) -> str:
    """Évolution dite avec des mots, pour le modèle et les modèles de secours."""
    if "delta" in entry:
        delta = entry["delta"]
        return "en hausse" if delta > 0 else "en baisse" if delta < 0 else "stable"
    recent, earlier = entry["recent"], entry["earlier"]
    for key, better_when_lower in (("trailing_per_test", True), ("unfavorable_share", True)):
        if key in recent and recent.get("n") and earlier.get("n"):
            if recent[key] == earlier[key]:
                return "stable"
            return "en hausse" if (recent[key] < earlier[key]) == better_when_lower else "en baisse"
    return "non mesurable"


def progress(rows: list[Row], attempts: list[Attempt], previous_findings: list[dict]) -> dict:
    recent_ids = {a.pk for a in attempts[:PROGRESS_RECENT]}
    recent = [r for r in rows if r.attempt_id in recent_ids]
    earlier = [r for r in rows if r.attempt_id not in recent_ids]
    entries = []
    for previous in previous_findings:
        # `language_point` : ancien type de constat, remplacé par `msl_family`.
        if previous["type"] in {"progress", "language_point"}:
            continue
        entry = {
            "finding_id": previous["id"],
            "label": previous["label"],
            "recent": _metric(previous, recent),
            "earlier": _metric(previous, earlier),
        }
        if "rate" in entry["recent"] and entry["recent"]["n"] and entry["earlier"]["n"]:
            entry["delta"] = entry["recent"]["rate"] - entry["earlier"]["rate"]
        entry["trend"] = trend(entry)
        entries.append(entry)
    return {
        "id": "progress",
        "type": "progress",
        "label": "Évolution depuis le conseil précédent",
        "details": {"recent_attempts": len(recent_ids), "items": entries},
        "errors": [],
    }


# --- Sélection ---------------------------------------------------------------------------------


def compute_findings(
    user, previous_findings: list[dict] | None = None
) -> tuple[list[dict], list[int]]:
    """Les 3 constats les plus marqués, `progress` en plus si un conseil précédent existe.
    Aucun constat retenu : aucun conseil (plus de « point le moins solide »).

    Renvoie (constats, identifiants des passages utilisés).
    """
    attempts = recent_attempts(user)
    rows = load_rows(attempts)
    candidates = (
        level_breaks(rows)
        + msl_families(rows, msl_level(attempts))
        + doc_types(rows)
        + skills(rows)
        + time_pressure(rows, attempts)
        + answer_changes(rows)
    )
    candidates.sort(key=lambda f: (-f["strength"], f["id"]))
    selected = candidates[:MAX_FINDINGS]
    if previous_findings:
        selected.append(progress(rows, attempts, previous_findings))
    return selected, [a.pk for a in attempts]
