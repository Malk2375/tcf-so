from django.conf import settings
from django.db import models

from exam.models import Attempt


class Advice(models.Model):
    """Conseils rédigés après un passage (SPEC 12). Un seul par passage déclencheur."""

    class Source(models.TextChoices):
        LLM = "llm", "API Claude"
        FALLBACK = "fallback", "Modèles de secours"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="advice"
    )
    attempt = models.OneToOneField(Attempt, on_delete=models.CASCADE, related_name="advice")
    # Passages dont les réponses ont servi au calcul des constats (les 10 derniers terminés).
    attempt_ids = models.JSONField(default=list)
    findings = models.JSONField(default=list)
    content = models.JSONField(default=dict)
    source = models.CharField(max_length=10, choices=Source.choices)
    model = models.CharField(max_length=100, blank=True)
    input_tokens = models.PositiveIntegerField(default=0)
    output_tokens = models.PositiveIntegerField(default=0)
    cost_usd = models.DecimalField(max_digits=10, decimal_places=6, default=0)
    # Raison du recours aux modèles de secours (journal, affichée dans l'admin seulement).
    fallback_reason = models.CharField(max_length=300, blank=True)
    # Cases cochées du plan de la semaine : ["<index du conseil>-<index de l'action>", …].
    plan_done = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "conseil"
        verbose_name_plural = "conseils"
        ordering = ["-created_at"]

    def __str__(self):
        return f"Conseils du passage {self.attempt_id}"
