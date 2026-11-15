"""Courbe des scores par épreuve, en SVG calculé côté serveur (sans bibliothèque)."""

from exam.scoring import MAX_SCORE, SCORE_LEVELS

WIDTH, HEIGHT = 340, 220
LEFT, RIGHT, TOP, BOTTOM = 86, 10, 10, 24
SERIES = [
    ("CO", "Compréhension orale", "#1d4ed8"),
    ("MSL", "Structures de la langue", "#b45309"),
    ("CE", "Compréhension écrite", "#047857"),
]


def _y(score: int) -> float:
    return round(TOP + (HEIGHT - TOP - BOTTOM) * (1 - score / MAX_SCORE), 1)


def score_chart(attempts: list) -> dict | None:
    """Coordonnées de la courbe : un point par passage terminé, du plus ancien au plus récent.

    `attempts` : passages terminés, avec leurs épreuves préchargées.
    """
    if not attempts:
        return None
    count = len(attempts)
    span = WIDTH - LEFT - RIGHT

    def x(index: int) -> float:
        return round(LEFT + (span / 2 if count == 1 else span * index / (count - 1)), 1)

    series = []
    for code, label, color in SERIES:
        points = []
        for index, attempt in enumerate(attempts):
            section = next((s for s in attempt.sections.all() if s.section == code), None)
            if section is not None and section.score is not None:
                points.append({"x": x(index), "y": _y(section.score), "score": section.score})
        series.append(
            {
                "label": label,
                "color": color,
                "points": points,
                "polyline": " ".join(f"{p['x']},{p['y']}" for p in points),
            }
        )
    # Une bande par niveau (0–99, 100–199, …), étiquetée en son milieu.
    gridlines = [
        {"y": _y(score), "label_y": _y(score + 50), "label": level.label}
        for score, level in zip(range(0, MAX_SCORE + 1, 100), SCORE_LEVELS, strict=True)
    ]
    ticks = [{"x": x(i), "label": str(i + 1)} for i in range(count)]
    return {
        "width": WIDTH,
        "height": HEIGHT,
        "left": LEFT,
        "right": WIDTH - RIGHT,
        "bottom": HEIGHT - BOTTOM,
        "series": series,
        "gridlines": gridlines,
        "ticks": ticks,
    }
