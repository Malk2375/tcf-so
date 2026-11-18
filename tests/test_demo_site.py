"""Site de démonstration (CLAUDE.md) : réglages, bandeau, fenêtre de démarrage, lanceurs."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from django.core.management import call_command

ROOT = Path(__file__).resolve().parent.parent

PROBE = (
    "import json, django; django.setup(); from django.conf import settings as s; "
    "print(json.dumps({'demo': s.DEMO, 'media': str(s.MEDIA_ROOT), 'tts': s.TTS_PROVIDER, "
    "'llm': s.LLM_PROVIDER, 'keys': bool(s.GOOGLE_TTS_API_KEY or s.ANTHROPIC_API_KEY), "
    "'session': s.SESSION_COOKIE_NAME, 'csrf': s.CSRF_COOKIE_NAME}))"
)


def settings_with(env: dict) -> dict:
    """Réglages calculés dans un processus séparé, avec ces variables d'environnement."""
    clean = {k: v for k, v in os.environ.items() if k not in {"DATABASE_URL", "MEDIA_ROOT"}}
    result = subprocess.run(
        [sys.executable, "-c", PROBE],
        cwd=ROOT,
        env={**clean, "DJANGO_SETTINGS_MODULE": "config.settings", **env},
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(result.stdout.strip().splitlines()[-1])


def test_demo_database_forces_fake_providers_and_own_cookies():
    values = settings_with(
        {
            "DATABASE_URL": "sqlite:///data/demo.sqlite3",
            "MEDIA_ROOT": "data/media",  # même mal réglé : jamais les médias réels
            "TTS_PROVIDER": "google",
            "LLM_PROVIDER": "anthropic",
            "GOOGLE_TTS_API_KEY": "cle-factice",
            "ANTHROPIC_API_KEY": "cle-factice",
        }
    )
    assert values["demo"] is True
    assert Path(values["media"]) == ROOT / "data" / "demo-media"
    assert (values["tts"], values["llm"], values["keys"]) == ("fake", "fake", False)
    assert values["session"] != "sessionid" and values["csrf"] != "csrftoken"


def test_real_database_keeps_default_cookies():
    values = settings_with({"DATABASE_URL": "sqlite:///data/autre.sqlite3"})
    assert values["demo"] is False
    assert (values["session"], values["csrf"]) == ("sessionid", "csrftoken")


@pytest.mark.django_db
def test_demo_banner_on_every_page(client, settings):
    settings.DEMO = False
    assert "DÉMONSTRATION" not in client.get("/").content.decode()
    settings.DEMO = True
    html = client.get("/").content.decode()
    assert "DÉMONSTRATION" in html
    assert 'name="csrf-token"' in html  # le lecteur lit le jeton ici, pas dans le cookie


@pytest.mark.django_db
def test_site_info_shows_database_and_account(settings, capsys, staff_user):
    settings.DEMO = False
    staff_user.is_superuser = True
    staff_user.save()
    call_command("site_info", "--port", "8000")
    out = capsys.readouterr().out
    assert "SITE RÉEL" in out and "admin@example.com" in out and "localhost:8000" in out
    settings.DEMO = True
    call_command("site_info", "--port", "8001")
    out = capsys.readouterr().out
    assert "DÉMONSTRATION" in out and "admin@demo.test" in out and "localhost:8001" in out


@pytest.mark.parametrize(
    "name, port, expected",
    [
        ("demarrer.cmd", "8000", ['set "DATABASE_URL="', "migrate", "runserver 8000"]),
        (
            "demo.cmd",
            "8001",
            [
                'set "DATABASE_URL=sqlite:///data/demo.sqlite3"',
                'set "TTS_PROVIDER=fake"',
                'set "LLM_PROVIDER=fake"',
                'if not exist "data\\demo.sqlite3"',
                "reset_demo",
                "runserver 8001",
            ],
        ),
    ],
)
def test_launchers(name, port, expected):
    raw = (ROOT / name).read_bytes()
    assert b"\r\n" in raw and b"\n" not in raw.replace(b"\r\n", b"")  # fin de ligne Windows
    text = raw.decode("ascii")
    for fragment in expected:
        assert fragment in text
    assert f"site_info --port {port}" in text
    assert f"http://localhost:{port}" in text


# --- Démonstration sur téléphone (telephone.cmd) -------------------------------------------------


def load_phone():
    import importlib.util

    spec = importlib.util.spec_from_file_location("phone", ROOT / "scripts" / "phone.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


HOSTS_PROBE = (
    "import json, django; django.setup(); from django.conf import settings as s; "
    "print(json.dumps({'hosts': s.ALLOWED_HOSTS, 'origins': s.CSRF_TRUSTED_ORIGINS, "
    "'proxy': bool(getattr(s, 'SECURE_PROXY_SSL_HEADER', None))}))"
)


def hosts_with(env: dict) -> dict:
    clean = {k: v for k, v in os.environ.items() if k not in {"DATABASE_URL", "PHONE_TUNNEL"}}
    result = subprocess.run(
        [sys.executable, "-c", HOSTS_PROBE],
        cwd=ROOT,
        env={**clean, "DJANGO_SETTINGS_MODULE": "config.settings", "DJANGO_DEBUG": "true", **env},
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(result.stdout.strip().splitlines()[-1])


def test_tunnel_domain_allowed_only_in_phone_demo_mode():
    phone = hosts_with({"DATABASE_URL": "sqlite:///data/demo.sqlite3", "PHONE_TUNNEL": "1"})
    assert ".trycloudflare.com" in phone["hosts"]
    assert "https://*.trycloudflare.com" in phone["origins"] and phone["proxy"]
    demo = hosts_with({"DATABASE_URL": "sqlite:///data/demo.sqlite3"})
    real = hosts_with({"DATABASE_URL": "sqlite:///data/autre.sqlite3", "PHONE_TUNNEL": "1"})
    for values in (demo, real):
        assert ".trycloudflare.com" not in values["hosts"]
        assert not any("trycloudflare" in o for o in values["origins"])


def test_tunnel_url_is_read_from_cloudflared_output():
    phone = load_phone()
    line = "2026-11-18T10:00:00Z INF |  https://quiet-lake-blue-river.trycloudflare.com  |"
    assert phone.tunnel_url(line) == "https://quiet-lake-blue-river.trycloudflare.com"
    assert phone.tunnel_url("INF Requesting new quick Tunnel on trycloudflare.com...") is None
    assert "https://quiet-lake-blue-river.trycloudflare.com" in phone.banner(
        "https://quiet-lake-blue-river.trycloudflare.com"
    )


def test_cloudflared_download_is_checked(tmp_path, monkeypatch):
    import io

    phone = load_phone()
    monkeypatch.setattr(
        phone.urllib.request, "urlopen", lambda url: io.BytesIO(b"programme altere")
    )
    with pytest.raises(SystemExit, match="SHA-256"):
        phone.download(tmp_path / "cloudflared.exe")
    assert not (tmp_path / "cloudflared.exe").exists()
    import hashlib

    good = b"programme"
    monkeypatch.setattr(phone.urllib.request, "urlopen", lambda url: io.BytesIO(good))
    path = phone.download(tmp_path / "cloudflared.exe", sha256=hashlib.sha256(good).hexdigest())
    assert path.read_bytes() == good
    assert phone.DOWNLOAD_URL.startswith("https://github.com/cloudflare/cloudflared/releases/")


def test_telephone_launcher():
    raw = (ROOT / "telephone.cmd").read_bytes()
    assert b"\r\n" in raw and b"\n" not in raw.replace(b"\r\n", b"")
    text = raw.decode("ascii")
    for fragment in (
        'set "DATABASE_URL=sqlite:///data/demo.sqlite3"',
        'set "TTS_PROVIDER=fake"',
        'set "LLM_PROVIDER=fake"',
        'set "PHONE_TUNNEL=1"',
        "reset_demo",
        "site_info --port 8001",
        r"scripts\phone.py",
    ):
        assert fragment in text
