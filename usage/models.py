from django.db import models
from django.db.models import Sum
from django.utils import timezone


def default_price_table():
    # Dollars par million de tokens (modèles Claude) ou de caractères (voix).
    # Modifiable : les prix changent.
    return {
        "claude-sonnet-5-5": {"input": 2.0, "output": 10.0},
        "claude-haiku-5-5": {"input": 0.10, "output": 0.50},
        "google_chirp3_hd": {"chars": 30.0},
    }


def default_tts_casting():
    return {
        "instructions": "fr-FR-Chirp3-HD-Aoede",
        "F": [
            "fr-FR-Chirp3-HD-Kore",
            "fr-FR-Chirp3-HD-Leda",
            "fr-FR-Chirp3-HD-Zephyr",
            "fr-FR-Chirp3-HD-Achernar",
        ],
        "M": [
            "fr-FR-Chirp3-HD-Charon",
            "fr-FR-Chirp3-HD-Puck",
            "fr-FR-Chirp3-HD-Fenrir",
            "fr-FR-Chirp3-HD-Orus",
        ],
    }


def current_month() -> str:
    return timezone.localdate().strftime("%Y-%m")


class SiteSettings(models.Model):
    """Réglages modifiables dans l'admin. Une seule ligne (pk = 1)."""

    tts_monthly_char_quota = models.PositiveIntegerField(
        "quota mensuel de caractères (voix)", default=1_000_000
    )
    tts_quota_safety = models.FloatField("marge de sécurité du quota", default=0.95)
    api_monthly_budget_usd = models.DecimalField(
        "plafond mensuel de l'API Claude ($)", max_digits=8, decimal_places=2, default=2
    )
    advice_model = models.CharField(
        "modèle des conseils", max_length=100, default="claude-sonnet-5-5"
    )
    price_table = models.JSONField("table des prix", default=default_price_table)
    keycheck_enabled = models.BooleanField("contrôle de la clé par l'API", default=False)
    keycheck_model = models.CharField(
        "modèle du contrôle de la clé", max_length=100, default="claude-haiku-5-5"
    )
    tts_casting = models.JSONField("distribution des voix", default=default_tts_casting)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "réglages du site"
        verbose_name_plural = "réglages du site"

    def __str__(self):
        return "Réglages du site"

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def load(cls) -> "SiteSettings":
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    @property
    def tts_effective_quota(self) -> int:
        return int(self.tts_monthly_char_quota * self.tts_quota_safety)


class UsageEntry(models.Model):
    class Kind(models.TextChoices):
        TTS = "tts", "Synthèse vocale"
        LLM_ADVICE = "llm_advice", "Conseils"
        LLM_KEYCHECK = "llm_keycheck", "Contrôle de la clé"

    kind = models.CharField(max_length=20, choices=Kind.choices)
    provider = models.CharField(max_length=50)
    model = models.CharField(max_length=100, blank=True)
    chars = models.PositiveIntegerField(default=0)
    input_tokens = models.PositiveIntegerField(default=0)
    output_tokens = models.PositiveIntegerField(default=0)
    cost_usd = models.DecimalField(max_digits=10, decimal_places=6, default=0)
    month = models.CharField(max_length=7, default=current_month, db_index=True)
    ref = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "consommation"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.get_kind_display()} {self.month}"


FAKE_PROVIDER = "fake"


def tts_chars_used(month: str | None = None) -> int:
    """Caractères facturables du mois : les synthèses factices sont journalisées sans compter."""
    entries = UsageEntry.objects.filter(
        kind=UsageEntry.Kind.TTS, month=month or current_month()
    ).exclude(provider=FAKE_PROVIDER)
    return entries.aggregate(total=Sum("chars"))["total"] or 0


def tts_chars_remaining() -> int:
    """Caractères encore disponibles ce mois-ci, marge de sécurité comprise."""
    return max(0, SiteSettings.load().tts_effective_quota - tts_chars_used())
