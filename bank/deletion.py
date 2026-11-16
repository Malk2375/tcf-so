"""Suppression d'un test en brouillon ou archivé (SPEC 11).

Supprime le test, ses items, leurs audios (et les fichiers MP3 que plus rien n'utilise),
les passages des comptes administrateurs sur ce test et les conseils qui en dépendent.
Le brief et l'import restent, pour la trace.
"""

from django.core.files.storage import default_storage
from django.db import transaction

from advice.models import Advice
from bank.models import AudioAsset, Item, Test
from exam.models import Answer, Attempt


class DeletionError(Exception):
    """Suppression refusée : message en français, affichable tel quel."""


def _is_admin(user) -> bool:
    return user.is_staff or user.is_superuser


def deletion_problems(test: Test) -> list[str]:
    problems = []
    if test.status == Test.Status.PUBLISHED:
        problems.append("Un test publié ne peut pas être supprimé : archivez-le d'abord.")
    users = {a.user for a in Attempt.objects.filter(test=test).select_related("user")}
    others = sorted(_account_name(user) for user in users if not _is_admin(user))
    if others:
        who = ", ".join(others)
        plural = "s" if len(others) > 1 else ""
        problems.append(
            f"Ce test a été passé par le{plural} compte{plural} non administrateur{plural} "
            f"{who} : il ne peut pas être supprimé."
        )
    return problems


def _account_name(user) -> str:
    """Le compte en cause, tel que l'administrateur le reconnaît : son adresse e-mail."""
    return user.email or user.get_username()


def delete_test(test: Test) -> dict:
    """Supprime le test et ce qui en dépend ; renvoie le décompte de ce qui a été supprimé."""
    with transaction.atomic():
        test = Test.objects.select_for_update().get(pk=test.pk)
        problems = deletion_problems(test)
        if problems:
            raise DeletionError(" ".join(problems))
        attempts = list(Attempt.objects.filter(test=test))
        attempt_ids = {a.pk for a in attempts}
        users = {a.user_id for a in attempts}
        # Conseils déclenchés par ces passages, ou calculés à partir de leurs réponses.
        advice_ids = [
            advice.pk
            for advice in Advice.objects.filter(user__in=users)
            if advice.attempt_id in attempt_ids or attempt_ids & set(advice.attempt_ids)
        ]
        Advice.objects.filter(pk__in=advice_ids).delete()
        Answer.objects.filter(attempt__in=attempt_ids).delete()
        Attempt.objects.filter(pk__in=attempt_ids).delete()
        item_ids = list(Item.objects.filter(test_item__test=test).values_list("pk", flat=True))
        paths = set(AudioAsset.objects.filter(item__in=item_ids).values_list("file", flat=True))
        test.delete()  # supprime les TestItem
        Item.objects.filter(pk__in=item_ids).delete()  # supprime les AudioAsset
        # Un fichier est nommé d'après son contenu : un autre item peut le partager.
        still_used = set(AudioAsset.objects.filter(file__in=paths).values_list("file", flat=True))
        orphans = sorted(paths - still_used)
    # Après la transaction : un échec de la base ne laisse pas d'item sans son fichier.
    _remove_files(orphans)
    return {
        "items": len(item_ids),
        "attempts": len(attempt_ids),
        "advice": len(advice_ids),
        "files": len(orphans),
    }


def _remove_files(paths: list[str]) -> None:
    for path in paths:
        if path and default_storage.exists(path):
            default_storage.delete(path)
