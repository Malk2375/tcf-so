"""Application installable (SPEC 14) : manifeste, service worker, page hors ligne, aide."""

import hashlib
import json
from pathlib import Path

from django.conf import settings
from django.contrib.staticfiles import finders
from django.http import HttpResponse
from django.shortcuts import render
from django.template.loader import render_to_string
from django.templatetags.static import static
from django.urls import reverse
from django.views.decorators.cache import never_cache

THEME_COLOR = "#1e3a8a"
BACKGROUND_COLOR = "#f8fafc"

# Habillage mis en cache par le service worker (chemins dans les fichiers statiques).
SHELL = [
    "css/app.css",
    "vendor/htmx.min.js",
    "vendor/alpine.min.js",
    "js/player.js",
    "icons/icon-192.png",
    "icons/icon-512.png",
    "icons/apple-touch-icon.png",
    "icons/favicon-32.png",
]
# Jamais interceptés par le service worker (en plus de toutes les requêtes autres que GET).
BYPASS = ["/media/", "/api/", "/bo/", "/admin/"]


def manifest(request):
    data = {
        "name": "Entraînement TCF SO",
        "short_name": "TCF SO",
        "description": "Entraînement personnel au TCF tout public sur ordinateur.",
        "lang": "fr",
        "start_url": reverse("dashboard"),
        "scope": "/",
        "display": "standalone",
        "orientation": "any",
        "theme_color": THEME_COLOR,
        "background_color": BACKGROUND_COLOR,
        "icons": [
            {"src": static("icons/icon-192.png"), "sizes": "192x192", "type": "image/png"},
            {"src": static("icons/icon-512.png"), "sizes": "512x512", "type": "image/png"},
            {
                "src": static("icons/icon-maskable-512.png"),
                "sizes": "512x512",
                "type": "image/png",
                "purpose": "maskable",
            },
        ],
    }
    return HttpResponse(
        json.dumps(data, ensure_ascii=False, indent=2),
        content_type="application/manifest+json; charset=utf-8",
    )


def shell_version() -> str:
    """Empreinte de l'habillage : un fichier modifié donne un nouveau service worker, donc un
    nouveau cache (l'ancien est supprimé à l'activation)."""
    digest = hashlib.sha256(render_to_string("pwa/offline.html").encode())
    for path in SHELL:
        found = finders.find(path)
        if found:
            digest.update(Path(found).read_bytes())
    return digest.hexdigest()[:12]


@never_cache
def service_worker(request):
    static_prefix = "/" + settings.STATIC_URL.lstrip("/")
    context = {
        "version": shell_version(),
        "precache": json.dumps([static(path) for path in SHELL] + [reverse("offline")]),
        "offline_url": json.dumps(reverse("offline")),
        "static_prefix": json.dumps(static_prefix),
        "bypass": json.dumps(BYPASS),
    }
    response = render(request, "pwa/sw.js", context, content_type="text/javascript; charset=utf-8")
    response["Service-Worker-Allowed"] = "/"
    return response


def offline(request):
    return render(request, "pwa/offline.html")


def install_help(request):
    return render(request, "pwa/install.html")
