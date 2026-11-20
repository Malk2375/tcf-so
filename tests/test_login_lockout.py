"""Blocage des connexions et déblocage (SPEC 13, révision du 20 novembre 2026)."""

import os
import subprocess
import sys
from importlib.metadata import version
from pathlib import Path

import pytest
from django.conf import settings as django_settings
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.urls import reverse

from accounts import lockout
from accounts.adapter import AccountAdapter
from tests.conftest import PASSWORD

pytestmark = pytest.mark.django_db

EMAIL = "bloque@example.com"
ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def account(db):
    return get_user_model().objects.create_user("bloque", EMAIL, PASSWORD)


def attempt(client, password, email=EMAIL, **extra):
    client.post(reverse("account_login"), {"login": email, "password": password}, **extra)
    logged_in = "_auth_user_id" in client.session
    client.logout()
    return logged_in


def lock(client, email=EMAIL, **extra):
    for _ in range(5):
        assert not attempt(client, "mauvais-mot-de-passe", email, **extra)


def test_allauth_is_recent_enough():
    """65.19.4 corrige un contournement de la limite des échecs de connexion."""
    major, minor, patch = (int(part) for part in version("django-allauth").split(".")[:3])
    assert (major, minor, patch) >= (65, 19, 4)


def test_cache_is_shared_between_processes():
    """Un cache par processus compterait les échecs worker par worker (SPEC 13)."""
    assert "FileBasedCache" in django_settings.CACHES["default"]["BACKEND"]


def test_five_failures_lock_the_account_even_with_the_right_password(client, account):
    assert attempt(client, PASSWORD)
    lock(client)
    assert lockout.is_locked(EMAIL)
    assert not attempt(client, PASSWORD)  # bon mot de passe, mais compte bloqué


def test_the_lock_does_not_depend_on_the_site_address(client, account):
    """Le compteur porte sur l'adresse e-mail, pas sur l'hôte : changer d'adresse du site
    (IP, nom de domaine) ne contourne pas le blocage."""
    lock(client, HTTP_HOST="localhost")
    assert not attempt(client, PASSWORD, HTTP_HOST="testserver")
    assert AccountAdapter()._get_login_attempts_cache_key(None, email=" Bloque@Example.com ") == (
        EMAIL
    )


def test_unlock_login_command_unlocks_immediately(client, account, capsys):
    lock(client)
    call_command("unlock_login", EMAIL.upper())
    assert "était bloqué" in capsys.readouterr().out
    assert not lockout.is_locked(EMAIL)
    assert attempt(client, PASSWORD)


def test_unlock_login_unknown_address_or_missing_argument(account):
    with pytest.raises(CommandError, match="Aucun compte"):
        call_command("unlock_login", "inconnu@example.com")
    with pytest.raises(CommandError, match="--all"):
        call_command("unlock_login")


def test_unlock_login_all_clears_ip_counters_too(client, account):
    lock(client)
    call_command("unlock_login", "--all")
    assert attempt(client, PASSWORD)


def test_unlock_login_works_from_another_process(client, account, settings, tmp_path):
    """Le serveur et la commande sont deux processus : ils partagent les fichiers du cache."""
    lock(client)
    cache_dir = settings.CACHES["default"]["LOCATION"]
    script = (
        "import django; from django.conf import settings; "
        f"settings.CACHES['default']['LOCATION'] = {cache_dir!r}; "
        "from accounts import lockout; lockout.unlock_all()"
    )
    env = {
        **os.environ,
        "DJANGO_SETTINGS_MODULE": "config.settings",
        # Base jetable : la commande ne touche jamais la base réelle.
        "DATABASE_URL": f"sqlite:///{(tmp_path / 'autre.sqlite3').as_posix()}",
        "TTS_PROVIDER": "fake",
        "LLM_PROVIDER": "fake",
    }
    code = f"import django; django.setup(); exec({script!r})"
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
    assert not lockout.is_locked(EMAIL)
    assert attempt(client, PASSWORD)


def test_backoffice_unlock_button(client, account, staff_user):
    lock(client)
    client.force_login(staff_user)
    html = client.get(reverse("backoffice:users")).content.decode()
    assert "connexion bloquée" in html and "Débloquer la connexion" in html
    assert "confirm(" in html
    response = client.post(
        reverse("backoffice:users"), {"user_id": account.pk, "action": "unlock"}, follow=True
    )
    assert f"Connexion débloquée pour {EMAIL}" in response.content.decode()
    client.logout()
    assert attempt(client, PASSWORD)


def test_admin_login_goes_through_the_same_limits(client, account, staff_user):
    """`/admin/login/` renvoie vers la connexion d'allauth : un mot de passe envoyé à l'admin ne
    contourne pas le compteur."""
    response = client.post(
        reverse("admin:login"), {"username": EMAIL, "password": "mauvais-mot-de-passe"}
    )
    assert response.status_code == 302 and reverse("account_login") in response["Location"]
    staff_user.set_password(PASSWORD)
    staff_user.save()
    lock(client, email=staff_user.email)
    assert not attempt(client, PASSWORD, email=staff_user.email)
    response = client.post(
        reverse("admin:login"), {"username": staff_user.email, "password": PASSWORD}
    )
    assert "_auth_user_id" not in client.session


def test_real_ip_behind_caddy(settings, rf):
    """En production, Caddy remplace X-Forwarded-For par l'adresse du visiteur : allauth la lit
    (ALLAUTH_TRUSTED_PROXY_COUNT = 1). En développement, l'en-tête est ignoré."""
    from allauth.account.adapter import get_adapter

    request = rf.post("/", HTTP_X_FORWARDED_FOR="198.51.100.7", REMOTE_ADDR="172.18.0.3")
    settings.ALLAUTH_TRUSTED_PROXY_COUNT = 0
    assert get_adapter().get_client_ip(request) == "172.18.0.3"
    settings.ALLAUTH_TRUSTED_PROXY_COUNT = 1
    assert get_adapter().get_client_ip(request) == "198.51.100.7"
