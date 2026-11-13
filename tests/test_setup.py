import sqlite3

import pytest
from django.conf import settings
from django.urls import reverse


def test_language_and_timezone():
    assert settings.LANGUAGE_CODE == "fr"
    assert settings.TIME_ZONE == "Europe/Paris"
    assert settings.USE_TZ is True


def test_external_providers_default_to_fake():
    assert settings.TTS_PROVIDER == "fake"
    assert settings.LLM_PROVIDER == "fake"


def test_emails_disabled_by_default():
    # pytest-django remplace EMAIL_BACKEND par locmem : seul le drapeau est vérifiable ici.
    assert settings.EMAIL_ENABLED is False


def test_sqlite_options():
    db = settings.DATABASES["default"]
    assert db["ENGINE"] == "django.db.backends.sqlite3"
    init = db["OPTIONS"]["init_command"]
    assert "journal_mode=WAL" in init
    assert "synchronous=NORMAL" in init
    assert "busy_timeout=5000" in init
    assert db["OPTIONS"]["transaction_mode"] == "IMMEDIATE"


def test_sqlite_init_command_enables_wal(tmp_path):
    """La commande d'initialisation, appliquée à un fichier, passe bien la base en WAL."""
    connection = sqlite3.connect(tmp_path / "db.sqlite3")
    connection.executescript(settings.DATABASES["default"]["OPTIONS"]["init_command"])
    assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert connection.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
    connection.close()


@pytest.mark.django_db
def test_home_page_in_french(client):
    response = client.get(reverse("home"))
    assert response.status_code == 200
    html = response.content.decode()
    assert '<html lang="fr">' in html
    assert "Préparez le TCF" in html
    assert "Estimation non officielle. Questions générées par IA." in html


@pytest.mark.django_db
def test_home_page_is_responsive_and_uses_local_scripts(client):
    html = client.get(reverse("home")).content.decode()
    assert 'name="viewport" content="width=device-width, initial-scale=1"' in html
    assert "/static/vendor/htmx.min.js" in html
    assert "/static/vendor/alpine.min.js" in html
    assert "/static/css/app.css" in html
    assert "cdn" not in html.lower()
    assert "unpkg" not in html.lower()


def test_vendor_files_exist():
    vendor = settings.BASE_DIR / "static" / "vendor"
    assert (vendor / "htmx.min.js").stat().st_size > 0
    assert (vendor / "alpine.min.js").stat().st_size > 0
    assert (settings.BASE_DIR / "static" / "css" / "app.css").stat().st_size > 0
