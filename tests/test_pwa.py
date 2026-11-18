"""Application installable (SPEC 14) : manifeste, icônes, service worker, pages."""

import json
import struct

import pytest
from django.contrib.staticfiles import finders

pytestmark = pytest.mark.django_db


def png_size(static_path: str) -> tuple[int, int]:
    with open(finders.find(static_path.removeprefix("/static/")), "rb") as file:
        header = file.read(24)
    assert header[:8] == b"\x89PNG\r\n\x1a\n"
    return struct.unpack(">II", header[16:24])


def test_manifest(client):
    response = client.get("/manifest.webmanifest")
    assert response["Content-Type"].startswith("application/manifest+json")
    data = json.loads(response.content)
    assert data["display"] == "standalone"
    assert data["name"] and data["short_name"] and data["theme_color"] and data["background_color"]
    assert data["start_url"] == "/dashboard/" and data["lang"] == "fr"
    sizes = {(icon["sizes"], icon.get("purpose", "any")) for icon in data["icons"]}
    assert {("192x192", "any"), ("512x512", "any"), ("512x512", "maskable")} <= sizes
    for icon in data["icons"]:
        width, height = png_size(icon["src"])
        assert f"{width}x{height}" == icon["sizes"]


def test_ios_tags_and_registration_on_every_page(client):
    html = client.get("/").content.decode()
    assert 'rel="manifest" href="/manifest.webmanifest"' in html
    assert 'rel="apple-touch-icon"' in html
    assert 'name="apple-mobile-web-app-capable" content="yes"' in html
    assert 'name="apple-mobile-web-app-status-bar-style"' in html
    assert 'name="theme-color"' in html
    assert 'serviceWorker.register("/sw.js")' in html
    assert png_size("/static/icons/apple-touch-icon.png") == (180, 180)


def test_service_worker_script(client):
    response = client.get("/sw.js")
    assert response["Content-Type"].startswith("text/javascript")
    assert response["Service-Worker-Allowed"] == "/"
    assert "no-cache" in response["Cache-Control"] or "max-age=0" in response["Cache-Control"]
    js = response.content.decode()
    assert 'request.method !== "GET"' in js
    bypass = json.loads(js.split("const BYPASS = ")[1].split(";")[0])
    assert {"/media/", "/api/", "/bo/", "/admin/"} <= set(bypass)
    precache = json.loads(js.split("const PRECACHE = ")[1].split(";")[0])
    assert "/offline/" in precache and "/static/css/app.css" in precache
    for path in precache:
        if path.startswith("/static/"):
            assert finders.find(path.removeprefix("/static/")), path
        else:
            assert client.get(path).status_code == 200, path


def test_service_worker_version_follows_the_shell(client, monkeypatch):
    from config import pwa

    first = client.get("/sw.js").content
    monkeypatch.setattr(pwa, "SHELL", [*pwa.SHELL[:-1]])
    assert client.get("/sw.js").content != first


def test_offline_and_install_pages(client):
    offline = client.get("/offline/").content.decode()
    assert "Connexion requise pour passer un test." in offline
    install = client.get("/install/").content.decode()
    assert "Installer l'application" in install
    assert "Sur l'écran d'accueil" in install and "Partager" in install
    assert 'href="/install/"' in client.get("/").content.decode()
