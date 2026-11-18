from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

from bank.constants import LEVELS


class Section(models.TextChoices):
    CO = "CO", "Compréhension orale"
    MSL = "MSL", "Maîtrise des structures de la langue"
    CE = "CE", "Compréhension écrite"


class Level(models.TextChoices):
    A1 = "A1"
    A2 = "A2"
    B1 = "B1"
    B2 = "B2"
    C1 = "C1"
    C2 = "C2"


class ExamFormat(models.Model):
    class Mode(models.TextChoices):
        RAPIDE = "rapide", "Rapide"
        MOYEN = "moyen", "Moyen"
        REEL = "reel", "Réel"

    code = models.SlugField(unique=True)
    mode = models.CharField(max_length=10, choices=Mode.choices, unique=True)
    label = models.CharField(max_length=100)
    is_active = models.BooleanField(default=True)
    # Dernier numéro public attribué dans ce mode : un numéro public n'est jamais réutilisé,
    # même après la suppression d'un test archivé.
    last_public_number = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "format d'examen"
        verbose_name_plural = "formats d'examen"
        ordering = ["id"]

    def __str__(self):
        return self.label

    @property
    def n_items(self) -> int:
        return sum(s.n_items for s in self.sections.all())

    @property
    def duration_seconds(self) -> int:
        return sum(s.duration_seconds for s in self.sections.all())


class FormatSection(models.Model):
    format = models.ForeignKey(ExamFormat, on_delete=models.CASCADE, related_name="sections")
    section = models.CharField(max_length=3, choices=Section.choices)
    order = models.PositiveSmallIntegerField()
    n_items = models.PositiveSmallIntegerField()
    duration_seconds = models.PositiveIntegerField()
    level_distribution = models.JSONField(help_text='Par exemple {"A1": 5, "A2": 6, …}')
    allow_back = models.BooleanField()
    auto_advance = models.BooleanField()
    # CO : bouton « Valider » pour passer avant la fin du temps de réponse (4.2, 8.5).
    allow_early_next = models.BooleanField(default=False)
    answer_window_seconds = models.PositiveSmallIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "épreuve d'un format"
        verbose_name_plural = "épreuves d'un format"
        ordering = ["format", "order"]
        constraints = [
            models.UniqueConstraint(fields=["format", "section"], name="unique_format_section"),
            models.UniqueConstraint(fields=["format", "order"], name="unique_format_order"),
        ]

    def __str__(self):
        return f"{self.format.code} — {self.section}"

    def clean(self):
        distribution = self.level_distribution or {}
        if set(distribution) - set(LEVELS):
            raise ValidationError({"level_distribution": "Niveaux inconnus."})
        if sum(distribution.values()) != self.n_items:
            raise ValidationError(
                {"level_distribution": "La somme de la répartition doit valoir le nombre d'items."}
            )
        if self.section == Section.CO and self.answer_window_seconds is None:
            raise ValidationError({"answer_window_seconds": "Obligatoire en CO."})
        if self.section != Section.CO and self.answer_window_seconds is not None:
            raise ValidationError({"answer_window_seconds": "Réservé à la CO."})


class Brief(models.Model):
    """Brief de génération téléchargé (6.1), conservé pour valider l'import qui y répond."""

    format = models.ForeignKey(ExamFormat, on_delete=models.CASCADE, related_name="briefs")
    number = models.PositiveIntegerField()
    brief_id = models.CharField(max_length=60, unique=True)
    content = models.JSONField()
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["format", "number"], name="unique_brief_number"),
        ]

    def __str__(self):
        return self.brief_id

    @property
    def filename(self) -> str:
        return f"brief_{self.format.code}_{self.number:04d}.json"


class ImportBatch(models.Model):
    class Status(models.TextChoices):
        ACCEPTED = "accepted", "Accepté"
        REJECTED = "rejected", "Refusé"

    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True
    )
    # Nuls quand l'import est refusé avant d'avoir pu les identifier.
    brief = models.ForeignKey(
        Brief, on_delete=models.SET_NULL, null=True, blank=True, related_name="imports"
    )
    filenames = models.JSONField(default=list)
    format = models.ForeignKey(ExamFormat, on_delete=models.PROTECT, null=True, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices)
    report = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "import"
        ordering = ["-created_at"]

    def __str__(self):
        return f"Import {self.pk} ({self.get_status_display()})"


class Item(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Brouillon"
        APPROVED = "approved", "Approuvé"
        REJECTED = "rejected", "Rejeté"
        PUBLISHED = "published", "Publié"
        RETIRED = "retired", "Retiré"

    class OptionsMode(models.TextChoices):
        TEXT = "text", "Texte"
        AUDIO_LETTERS = "audio_letters", "Lettres lues"

    section = models.CharField(max_length=3, choices=Section.choices)
    level = models.CharField(max_length=2, choices=Level.choices)
    subtype = models.CharField(max_length=30)
    stimulus_text = models.TextField(blank=True)
    audio_script = models.JSONField(null=True, blank=True)
    question = models.TextField(blank=True)
    options = models.JSONField()
    correct_index = models.PositiveSmallIntegerField()
    options_mode = models.CharField(
        max_length=15, choices=OptionsMode.choices, default=OptionsMode.TEXT
    )
    explanation = models.TextField()
    tags = models.JSONField(default=dict)
    word_count = models.PositiveIntegerField(default=0)
    content_hash = models.CharField(max_length=64, unique=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.DRAFT)
    import_batch = models.ForeignKey(
        ImportBatch, on_delete=models.SET_NULL, null=True, blank=True, related_name="items"
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["id"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(correct_index__lte=3), name="item_correct_index_0_3"
            ),
        ]

    def __str__(self):
        return f"{self.section} {self.level} {self.subtype} #{self.pk}"

    @property
    def support_text(self) -> str:
        """Le document : texte (CE, MSL) ou répliques mises bout à bout (CO)."""
        from bank.content import support_text

        return support_text(self.section, self.stimulus_text, self.audio_script)


class AudioAsset(models.Model):
    item = models.OneToOneField(Item, on_delete=models.CASCADE, related_name="audio")
    file = models.FileField(max_length=200)
    duration_ms = models.PositiveIntegerField()
    engine = models.CharField(max_length=50)
    voices = models.JSONField(default=dict)
    script_hash = models.CharField(max_length=64)
    chars_billed = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "audio"

    def __str__(self):
        return self.file.name


class Test(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Brouillon"
        PUBLISHED = "published", "Publié"
        ARCHIVED = "archived", "Archivé"

    format = models.ForeignKey(ExamFormat, on_delete=models.PROTECT, related_name="tests")
    number = models.PositiveIntegerField()
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.DRAFT)
    import_batch = models.ForeignKey(
        ImportBatch, on_delete=models.SET_NULL, null=True, blank=True, related_name="tests"
    )
    published_at = models.DateTimeField(null=True, blank=True)
    # Numéro montré aux utilisateurs (« Test 1 »), attribué à la publication ; `number` est le
    # numéro interne, lié au brief (section 5).
    public_number = models.PositiveIntegerField(null=True, blank=True)
    # Contrôle à l'aveugle (6.4) : {"answers": {"<slot>": index}, "doubts": {"<slot>": "raison"},
    # "received_at": "…"} ; vide tant que les réponses du relecteur n'ont pas été envoyées.
    blind_check = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["format", "number"]
        constraints = [
            models.UniqueConstraint(fields=["format", "number"], name="unique_test_number"),
            models.UniqueConstraint(
                fields=["format", "public_number"], name="unique_test_public_number"
            ),
        ]

    def __str__(self):
        return f"{self.format.label} — test {self.number}"

    @property
    def public_label(self) -> str:
        """Ce que voient les utilisateurs : « Test 3 »."""
        return f"Test {self.public_number}" if self.public_number else "Test"

    @property
    def admin_label(self) -> str:
        """Back-office : « test n° 2 · publié comme Test 1 »."""
        label = f"test n° {self.number}"
        return f"{label} · publié comme {self.public_label}" if self.public_number else label

    def assign_public_number(self) -> None:
        """Numéro public suivant du mode (à appeler dans une transaction, à la publication)."""
        if self.public_number:
            return
        exam_format = ExamFormat.objects.select_for_update().get(pk=self.format_id)
        highest = (
            Test.objects.filter(format=exam_format).aggregate(m=models.Max("public_number"))["m"]
            or 0
        )
        self.public_number = max(exam_format.last_public_number, highest) + 1
        exam_format.last_public_number = self.public_number
        exam_format.save(update_fields=["last_public_number"])


class TestItem(models.Model):
    test = models.ForeignKey(Test, on_delete=models.CASCADE, related_name="test_items")
    item = models.OneToOneField(Item, on_delete=models.PROTECT, related_name="test_item")
    section = models.CharField(max_length=3, choices=Section.choices)
    position = models.PositiveSmallIntegerField()
    # Numéro de l'emplacement du brief : sert de référence au contrôle à l'aveugle (6.4).
    slot = models.PositiveSmallIntegerField()

    class Meta:
        ordering = ["test", "slot"]
        constraints = [
            models.UniqueConstraint(
                fields=["test", "section", "position"], name="unique_test_section_position"
            ),
            models.UniqueConstraint(fields=["test", "slot"], name="unique_test_slot"),
        ]

    def __str__(self):
        return f"{self.test} — {self.section} {self.position}"
