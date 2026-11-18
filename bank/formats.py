"""Les trois formats d'examen (SPEC 4.1 et 4.2). Valeurs de départ, modifiables dans l'admin."""

from django.db import transaction

from bank.constants import LEVELS
from bank.models import ExamFormat, FormatSection

CO_ANSWER_WINDOW_SECONDS = 12

# Code, mode, libellé, puis par épreuve :
# (épreuve, ordre, items, durée en secondes, répartition A1→C2).
FORMATS = [
    (
        "tcf_tp_so_rapide",
        "rapide",
        "Mode rapide",
        [
            ("CO", 1, 9, 480, [1, 2, 2, 2, 1, 1]),
            ("MSL", 2, 6, 300, [1, 1, 1, 1, 1, 1]),
            ("CE", 3, 9, 780, [1, 2, 2, 2, 1, 1]),
        ],
    ),
    (
        "tcf_tp_so_moyen",
        "moyen",
        "Mode moyen",
        [
            ("CO", 1, 17, 900, [3, 3, 3, 3, 3, 2]),
            ("MSL", 2, 12, 540, [2, 2, 2, 2, 2, 2]),
            ("CE", 3, 17, 1440, [3, 3, 3, 3, 3, 2]),
        ],
    ),
    (
        "tcf_tp_so_reel",
        "reel",
        "Mode réel",
        [
            ("CO", 1, 34, 1740, [5, 6, 6, 6, 6, 5]),
            ("MSL", 2, 23, 1080, [4, 4, 4, 4, 4, 3]),
            ("CE", 3, 34, 2880, [5, 6, 6, 6, 6, 5]),
        ],
    ),
]


@transaction.atomic
def seed_formats() -> list[ExamFormat]:
    """Crée ou remet à jour les trois formats et leurs neuf épreuves."""
    formats = []
    for code, mode, label, sections in FORMATS:
        exam_format, _ = ExamFormat.objects.update_or_create(
            code=code, defaults={"mode": mode, "label": label, "is_active": True}
        )
        for section, order, n_items, duration, distribution in sections:
            is_co = section == "CO"
            format_section = FormatSection(
                format=exam_format,
                section=section,
                order=order,
                n_items=n_items,
                duration_seconds=duration,
                level_distribution=dict(zip(LEVELS, distribution, strict=True)),
                allow_back=not is_co,
                auto_advance=is_co,
                allow_early_next=False,  # option gardée, désactivée (4.2, 19)
                answer_window_seconds=CO_ANSWER_WINDOW_SECONDS if is_co else None,
            )
            format_section.full_clean(exclude=["format"], validate_unique=False)
            FormatSection.objects.update_or_create(
                format=exam_format,
                section=section,
                defaults={
                    field: getattr(format_section, field)
                    for field in [
                        "order",
                        "n_items",
                        "duration_seconds",
                        "level_distribution",
                        "allow_back",
                        "auto_advance",
                        "allow_early_next",
                        "answer_window_seconds",
                    ]
                },
            )
        formats.append(exam_format)
    return formats
