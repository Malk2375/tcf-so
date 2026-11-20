"""Où en est un utilisateur dans un mode : carte du tableau de bord (SPEC 10) et liste des tests
du mode (8.1 bis)."""

from dataclasses import dataclass, field

from bank.models import ExamFormat, Test
from exam import services
from exam.models import Attempt


@dataclass
class TestRow:
    test: Test
    state: str  # "passed", "in_progress", "to_pass"
    attempt: Attempt | None = None  # premier passage terminé, ou passage en cours
    is_next: bool = False


@dataclass
class ModeOverview:
    format: ExamFormat
    names: object
    questions: int
    minutes: int
    available: int  # tests publiés
    passed: int  # tests distincts terminés (archivés compris)
    remaining: int  # tests publiés pas encore terminés
    next_test: Test | None
    current: Attempt | None  # passage en cours dans ce mode
    other_current: bool  # passage en cours dans un autre mode
    all_done: str
    rows: list[TestRow] = field(default_factory=list)


def first_completed(user, exam_format) -> dict[int, Attempt]:
    """Premier passage terminé de chaque test du mode (les doublons anciens sont ignorés)."""
    first: dict[int, Attempt] = {}
    attempts = Attempt.objects.filter(
        user=user, status=Attempt.Status.COMPLETED, test__format=exam_format
    ).order_by("finished_at", "pk")
    for attempt in attempts:
        first.setdefault(attempt.test_id, attempt)
    return first


def mode_overview(user, exam_format: ExamFormat, current: Attempt | None) -> ModeOverview:
    published = list(services.published_tests(exam_format))
    done = first_completed(user, exam_format)
    next_test = next((t for t in published if t.pk not in done), None)
    in_mode = current if current and current.test.format_id == exam_format.pk else None
    remaining = sum(t.pk not in done for t in published)
    names = exam_format.names
    overview = ModeOverview(
        format=exam_format,
        names=names,
        questions=exam_format.n_items,
        minutes=round(exam_format.duration_seconds / 60),
        available=len(published),
        passed=len(done),
        remaining=remaining,
        next_test=next_test,
        current=in_mode,
        other_current=current is not None and in_mode is None,
        all_done=names.all_done(len(published)) if published and not remaining else "",
    )
    # Liste : les tests publiés, plus, à leur place, les tests archivés déjà passés.
    archived = Test.objects.filter(
        format=exam_format, status=Test.Status.ARCHIVED, pk__in=list(done)
    )
    tests = sorted([*published, *archived], key=lambda t: (t.public_number or 0, t.number))
    next_marked = False
    for test in tests:
        if test.pk in done:
            row = TestRow(test, "passed", done[test.pk])
        elif in_mode and in_mode.test_id == test.pk:
            row = TestRow(test, "in_progress", in_mode)
        else:
            row = TestRow(test, "to_pass", is_next=not next_marked)
            next_marked = True
        overview.rows.append(row)
    return overview
