"""Noms des modes (SPEC 4.1, 20 novembre 2026) : « Entraînement rapide », « Entraînement moyen »,
« Test en conditions réelles ». Seul `ExamFormat.label` change ; aucune donnée supprimée."""

from django.db import migrations, models

LABELS = {
    "rapide": "Entraînement rapide",
    "moyen": "Entraînement moyen",
    "reel": "Test en conditions réelles",
}


def rename(apps, schema_editor):
    ExamFormat = apps.get_model("bank", "ExamFormat")
    for exam_format in ExamFormat.objects.all():
        if exam_format.mode in LABELS:
            exam_format.label = LABELS[exam_format.mode]
            exam_format.save(update_fields=["label"])


class Migration(migrations.Migration):
    dependencies = [("bank", "0007_disable_early_next")]

    operations = [
        migrations.AlterField(
            model_name="examformat",
            name="mode",
            field=models.CharField(
                choices=[
                    ("rapide", "Entraînement rapide"),
                    ("moyen", "Entraînement moyen"),
                    ("reel", "Test en conditions réelles"),
                ],
                max_length=10,
                unique=True,
            ),
        ),
        migrations.RunPython(rename, migrations.RunPython.noop),
    ]
