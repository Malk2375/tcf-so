"""Où en est un utilisateur dans un mode : carte du tableau de bord (SPEC 10) et liste des tests
du mode (8.1 bis), avec une ligne par épreuve quand le format permet de les passer une par une
(8.1 ter)."""

from dataclasses import dataclass, field

from bank.constants import SECTION_LABELS
from bank.models import ExamFormat, Test
from exam import services
from exam.models import Attempt


@dataclass
class PartRow:
    """Une épreuve d'un test, passée seule ou à passer seule (8.1 ter)."""

    section: str
    label: str
    state: str  # "passed", "in_progress", "to_pass"
    attempt: Attempt | None = None
    can_start: bool = False


@dataclass
class TestRow:
    test: Test
    state: str  # "passed", "in_progress", "to_pass"
    attempt: Attempt | None = None  # premier passage complet terminé, ou passage complet en cours
    is_next: bool = False
    can_start_full: bool = False  # bouton « Commencer » (ou « Test complet »)
    parts: list[PartRow] = field(default_factory=list)


@dataclass
class ModeOverview:
    format: ExamFormat
    names: object
    questions: int
    minutes: int
    available: int  # tests publiés
    passed: int  # tests distincts passés (archivés compris), en entier ou épreuve par épreuve
    remaining: int  # tests publiés pas encore passés
    next_test: Test | None  # prochain test dont aucune épreuve n'est faite
    current: Attempt | None  # passage en cours dans ce mode
    other_current: bool  # passage en cours dans un autre mode
    all_done: str
    parts_allowed: bool = False
    rows: list[TestRow] = field(default_factory=list)

    @property
    def parts_left(self) -> bool:
        """Plus de test entier à lancer, mais des épreuves à passer : la carte mène à la liste."""
        return self.parts_allowed and self.next_test is None and self.remaining > 0


def first_completed(user, exam_format) -> dict[int, Attempt]:
    """Premier passage complet terminé de chaque test du mode (les doublons anciens sont
    ignorés)."""
    return {
        pk: progress.full
        for pk, progress in services.test_progress(user, exam_format).items()
        if progress.full is not None
    }


def mode_overview(user, exam_format: ExamFormat, current: Attempt | None) -> ModeOverview:
    published = list(services.published_tests(exam_format))
    progress = services.test_progress(user, exam_format)
    passed_ids = {pk for pk, p in progress.items() if p.passed}
    touched_ids = {pk for pk, p in progress.items() if p.touched}
    next_test = next((t for t in published if t.pk not in touched_ids), None)
    in_mode = current if current and current.test.format_id == exam_format.pk else None
    remaining = sum(t.pk not in passed_ids for t in published)
    names = exam_format.names
    parts_allowed = exam_format.allow_part_attempts
    overview = ModeOverview(
        format=exam_format,
        names=names,
        questions=exam_format.n_items,
        minutes=exam_format.duration_minutes,
        available=len(published),
        passed=len(passed_ids),
        remaining=remaining,
        next_test=next_test,
        current=in_mode,
        other_current=current is not None and in_mode is None,
        all_done=names.all_done(len(published)) if published and not remaining else "",
        parts_allowed=parts_allowed,
    )
    # Liste : les tests publiés, plus, à leur place, les tests archivés dont l'utilisateur a
    # terminé quelque chose (leurs résultats restent consultables).
    archived = Test.objects.filter(
        format=exam_format, status=Test.Status.ARCHIVED, pk__in=list(touched_ids)
    )
    tests = sorted([*published, *archived], key=lambda t: (t.public_number or 0, t.number))
    sections = [fs.section for fs in exam_format.sections.order_by("order")]
    for test in tests:
        overview.rows.append(_row(test, progress.get(test.pk), in_mode, sections, parts_allowed))
    # « prochain » : le premier test à passer qu'on peut lancer en entier (8.1 bis).
    first = next((row for row in overview.rows if row.can_start_full), None)
    if first is not None:
        first.is_next = True
    return overview


def _row(test, progress, current, sections, parts_allowed) -> TestRow:
    is_published = test.status == Test.Status.PUBLISHED
    running = current if current and current.test_id == test.pk else None
    if progress and progress.full:
        return TestRow(test, "passed", progress.full)
    if running and running.scope == Attempt.Scope.FULL:
        return TestRow(test, "in_progress", running)
    passed = progress is not None and progress.passed
    row = TestRow(
        test,
        "passed" if passed else "to_pass",
        can_start_full=is_published and not (progress and progress.touched) and running is None,
    )
    if parts_allowed or (progress and progress.parts):
        for section in sections:
            part = PartRow(section, SECTION_LABELS[section], "to_pass")
            if progress and section in progress.parts:
                part.state, part.attempt = "passed", progress.parts[section]
            elif running and running.scope == section:
                part.state, part.attempt = "in_progress", running
            else:
                part.can_start = is_published and parts_allowed
            row.parts.append(part)
    return row
