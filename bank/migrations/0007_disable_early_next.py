"""Pas de passage anticipé en compréhension orale : « Valider » désactivé partout (4.2, 19).
L'option `allow_early_next` reste disponible."""

from django.db import migrations


def disable(apps, schema_editor):
    FormatSection = apps.get_model("bank", "FormatSection")
    FormatSection.objects.update(allow_early_next=False)


class Migration(migrations.Migration):
    dependencies = [("bank", "0006_test_public_number")]

    operations = [migrations.RunPython(disable, migrations.RunPython.noop)]
