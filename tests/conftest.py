import json
import random
import socket
from pathlib import Path

import pytest
from django.contrib.auth import get_user_model

from bank.brief import create_brief
from bank.devdata import fake_import_file
from bank.formats import seed_formats

FIXTURES = Path(__file__).resolve().parent / "fixtures"
PASSWORD = "Entrainement-TCF-2026"


@pytest.fixture(autouse=True)
def _media_root(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path / "media"


LOOPBACK = {"127.0.0.1", "::1", "localhost"}


def _is_loopback(address) -> bool:
    host = address[0] if isinstance(address, tuple) else address
    return isinstance(host, str) and host in LOOPBACK


@pytest.fixture(autouse=True)
def _no_network(settings, monkeypatch):
    """Aucun appel réseau pendant les tests : fournisseurs factices, connexions refusées.
    Seule la machine locale reste joignable (serveur de test et navigateur de Playwright)."""
    settings.TTS_PROVIDER = "fake"
    settings.GOOGLE_TTS_API_KEY = ""
    settings.LLM_PROVIDER = "fake"
    real_connect = socket.socket.connect
    real_create_connection = socket.create_connection

    def connect(sock, address):
        if not _is_loopback(address):
            raise RuntimeError("Appel réseau interdit pendant les tests.")
        return real_connect(sock, address)

    def create_connection(address, *args, **kwargs):
        if not _is_loopback(address):
            raise RuntimeError("Appel réseau interdit pendant les tests.")
        return real_create_connection(address, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket, "create_connection", create_connection)


@pytest.fixture
def demo_db(settings):
    """Fait passer la base de test pour la base de démonstration (commandes de développement)."""
    settings.DEBUG = True
    settings.DEMO_DATABASE_PATH = settings.DATABASES["default"]["NAME"]
    settings.DEMO_MEDIA_ROOT = settings.MEDIA_ROOT


@pytest.fixture
def formats(db):
    return {f.mode: f for f in seed_formats()}


@pytest.fixture
def rng():
    return random.Random(2026)


@pytest.fixture
def staff_user(db):
    return get_user_model().objects.create_user(
        username="admin", email="admin@example.com", password=PASSWORD, is_staff=True
    )


@pytest.fixture
def staff_client(client, staff_user):
    client.force_login(staff_user)
    return client


@pytest.fixture
def make_import(formats, rng):
    """Brief du mode demandé et fichier d'import factice valide qui y répond."""

    def _make(mode="rapide"):
        brief = create_brief(formats[mode], rng=rng)
        return brief, fake_import_file(brief.content, rng)

    return _make


def as_file(data, name="items.json") -> tuple[str, bytes]:
    return name, json.dumps(data, ensure_ascii=False).encode()


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))
