"""Fichiers de production (SPEC 18, PLAN étape 7) : présents, cohérents, jamais exécutés ici."""

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DEPLOY = ROOT / "deploy"


def read(name: str) -> str:
    return (DEPLOY / name).read_text(encoding="utf-8")


def test_compose_file_is_valid():
    if shutil.which("docker") is None:
        pytest.skip("Docker absent : validation de compose.yml impossible ici.")
    result = subprocess.run(
        ["docker", "compose", "-f", str(DEPLOY / "compose.yml"), "config", "--quiet"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_compose_services_and_volumes():
    compose = read("compose.yml")
    for fragment in ("  web:", "  caddy:", "data:/app/data", "data:/srv/data:ro", '"443:443"'):
        assert fragment in compose


def test_caddyfile_https_on_ip_and_media_caching():
    caddy = read("Caddyfile")
    assert "profile shortlived" in caddy and "issuer acme" in caddy
    assert "default_sni {$SITE_ADDRESS}" in caddy
    assert "path /media/audio/*" in caddy and "immutable" in caddy
    assert "reverse_proxy web:8000" in caddy


def test_backup_is_consistent_and_kept_7_days_4_weeks():
    backup = read("backup.sh")
    assert 'sqlite3 /app/data/db.sqlite3 ".backup' in backup
    assert "integrity_check" in backup
    assert "--keep-daily 7 --keep-weekly 4" in backup
    assert "restic backup" in backup


def test_scripts_use_unix_line_endings():
    for name in ("deploy.sh", "backup.sh", "Caddyfile"):
        raw = (DEPLOY / name).read_bytes()
        assert b"\r\n" not in raw, name
    assert (DEPLOY / "deploy.sh").read_text(encoding="utf-8").startswith("#!/usr/bin/env bash")


def test_image_never_contains_secrets_or_local_data():
    ignored = read("Dockerfile.dockerignore").splitlines()
    for entry in (".env", "data", ".git", ".venv"):
        assert entry in ignored
    dockerfile = read("Dockerfile")
    assert "gunicorn config.wsgi" in dockerfile and "--threads" in dockerfile
    assert "USER app" in dockerfile


def test_production_env_example_lists_every_setting_without_secret():
    example = read("env.production.example")
    values = dict(
        line.split("=", 1) for line in example.splitlines() if "=" in line and line[0] != "#"
    )
    assert values["DJANGO_DEBUG"] == "false"
    assert values["DJANGO_SECRET_KEY"] == values["GOOGLE_TTS_API_KEY"] == ""
    assert values["ANTHROPIC_API_KEY"] == ""
    for key in ("SITE_ADDRESS", "SITE_URL", "DJANGO_ALLOWED_HOSTS", "CSRF_TRUSTED_ORIGINS"):
        assert key in values
