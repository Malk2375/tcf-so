from django.conf import settings
from django.db import models

from bank.models import Section, Test, TestItem


class Level(models.TextChoices):
    """Niveau estimé (SPEC 9), y compris « A1 non atteint » sous 100 points."""

    BELOW_A1 = "below_A1", "A1 non atteint"
    A1 = "A1"
    A2 = "A2"
    B1 = "B1"
    B2 = "B2"
    C1 = "C1"
    C2 = "C2"


class Scope(models.TextChoices):
    FULL = "full", "Test complet"
    CO = "CO", "Compréhension orale"
    MSL = "MSL", "Maîtrise des structures de la langue"
    CE = "CE", "Compréhension écrite"


class Attempt(models.Model):
    Scope = Scope

    class Status(models.TextChoices):
        IN_PROGRESS = "in_progress", "En cours"
        COMPLETED = "completed", "Terminé"
        ABANDONED = "abandoned", "Abandonné"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="attempts"
    )
    test = models.ForeignKey(Test, on_delete=models.PROTECT, related_name="attempts")
    # Test complet, ou une seule épreuve (8.1 ter).
    scope = models.CharField(max_length=4, choices=Scope.choices, default=Scope.FULL)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.IN_PROGRESS)
    started_at = models.DateTimeField()
    finished_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField()
    global_score = models.PositiveSmallIntegerField(null=True, blank=True)
    global_level = models.CharField(max_length=8, choices=Level.choices, blank=True)
    level_low = models.CharField(max_length=8, choices=Level.choices, blank=True)
    level_high = models.CharField(max_length=8, choices=Level.choices, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "passage"
        ordering = ["-started_at"]
        constraints = [
            # Un seul passage en cours par utilisateur (8.1).
            models.UniqueConstraint(
                fields=["user"],
                condition=models.Q(status="in_progress"),
                name="one_attempt_in_progress_per_user",
            ),
        ]

    def __str__(self):
        return f"Passage {self.pk} — {self.test}"

    @property
    def mode(self) -> str:
        return self.test.format.mode

    @property
    def is_part(self) -> bool:
        """Passage d'une seule épreuve (8.1 ter) : pas de score global."""
        return self.scope != Scope.FULL

    @property
    def scope_label(self) -> str:
        """« compréhension orale » pour un passage d'épreuve ; vide pour un test complet."""
        return "" if not self.is_part else Scope(self.scope).label.lower()

    @property
    def display_label(self) -> str:
        """Historique : « Entraînement moyen, Entraînement 2 — compréhension orale »."""
        label = f"{self.test.format.names.name}, {self.test.public_label}"
        return f"{label} — {self.scope_label}" if self.is_part else label

    def _part_section(self):
        return self.sections.first() if self.is_part else None

    @property
    def part_score(self) -> int | None:
        section = self._part_section()
        return section.score if section else None

    @property
    def part_level(self) -> str:
        section = self._part_section()
        return section.get_level_display() if section and section.level else ""

    @property
    def level_range(self) -> str:
        """« B1 à B2 » quand la fourchette couvre deux niveaux, sinon le niveau seul."""
        if not self.level_low:
            return ""
        low, high = Level(self.level_low).label, Level(self.level_high).label
        return low if low == high else f"{low} à {high}"


class AttemptSection(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "À venir"
        IN_PROGRESS = "in_progress", "En cours"
        DONE = "done", "Terminée"

    attempt = models.ForeignKey(Attempt, on_delete=models.CASCADE, related_name="sections")
    section = models.CharField(max_length=3, choices=Section.choices)
    order = models.PositiveSmallIntegerField()
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.PENDING)
    started_at = models.DateTimeField(null=True, blank=True)
    # MSL et CE : échéance ferme. CO : fin estimée (minuteur affiché), recalculée à chaque item.
    deadline_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    current_position = models.PositiveSmallIntegerField(null=True, blank=True)
    n_correct = models.PositiveSmallIntegerField(null=True, blank=True)
    score = models.PositiveSmallIntegerField(null=True, blank=True)
    level = models.CharField(max_length=8, choices=Level.choices, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "épreuve d'un passage"
        verbose_name_plural = "épreuves d'un passage"
        ordering = ["attempt", "order"]
        constraints = [
            models.UniqueConstraint(fields=["attempt", "order"], name="unique_attempt_order"),
        ]

    def __str__(self):
        return f"{self.attempt} — {self.section}"


class Answer(models.Model):
    attempt = models.ForeignKey(Attempt, on_delete=models.CASCADE, related_name="answers")
    test_item = models.ForeignKey(TestItem, on_delete=models.PROTECT, related_name="answers")
    selected_index = models.PositiveSmallIntegerField(null=True, blank=True)
    is_correct = models.BooleanField(default=False)
    time_spent_ms = models.PositiveIntegerField(default=0)
    change_count = models.PositiveSmallIntegerField(default=0)
    # Index choisis, dans l'ordre, avec leur horodatage : [{"index": 2, "at": "…"}, …].
    # Sert au constat `answer_changes` (SPEC 12.2).
    choice_history = models.JSONField(default=list, blank=True)
    flagged = models.BooleanField(default=False)
    first_seen_at = models.DateTimeField()
    answered_at = models.DateTimeField(null=True, blank=True)
    audio_starts = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "réponse"
        ordering = ["id"]
        constraints = [
            models.UniqueConstraint(fields=["attempt", "test_item"], name="unique_attempt_item"),
        ]

    def __str__(self):
        return f"{self.attempt} — {self.test_item}"
