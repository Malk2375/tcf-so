"""Remplit le lien ImportBatch → Brief des imports enregistrés avant qu'il existe (étape 2).

Le brief se retrouve par le test créé par l'import (même format, même numéro), ou, à défaut,
par un brief_id présent dans le nom d'un des fichiers importés.
"""

from django.db import migrations


def fill_briefs(apps, schema_editor):
    ImportBatch = apps.get_model("bank", "ImportBatch")
    Brief = apps.get_model("bank", "Brief")
    Test = apps.get_model("bank", "Test")
    for batch in ImportBatch.objects.filter(brief__isnull=True):
        brief = None
        test = Test.objects.filter(import_batch=batch).first()
        if test is not None:
            brief = Brief.objects.filter(format=test.format_id, number=test.number).first()
        if brief is None:
            for name in batch.filenames or []:
                brief = next(
                    (b for b in Brief.objects.filter(format=batch.format_id) if b.brief_id in name),
                    None,
                )
                if brief:
                    break
        if brief is not None:
            batch.brief = brief
            batch.save(update_fields=["brief"])


class Migration(migrations.Migration):
    dependencies = [("bank", "0003_test_blind_check")]

    operations = [migrations.RunPython(fill_briefs, migrations.RunPython.noop)]
