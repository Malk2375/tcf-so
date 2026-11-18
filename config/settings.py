"""Réglages Django. Toutes les valeurs variables sont lues dans `.env` (voir `.env.example`)."""

import os
import sys
from pathlib import Path

import environ
from django.core.exceptions import ImproperlyConfigured
from django.core.management.utils import get_random_secret_key

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"

# Les tests automatisés ignorent `.env` : ils tournent toujours avec les valeurs par défaut
# (fournisseurs factices, aucune clé), même si le propriétaire a renseigné ses clés.
if "pytest" not in sys.modules:
    environ.Env.read_env(BASE_DIR / ".env")

ENV_VARIABLES = [
    "DJANGO_SECRET_KEY",
    "DJANGO_DEBUG",
    "DJANGO_ALLOWED_HOSTS",
    "SITE_URL",
    "CSRF_TRUSTED_ORIGINS",
    "DATABASE_URL",
    "MEDIA_ROOT",
    "TTS_PROVIDER",
    "GOOGLE_TTS_API_KEY",
    "LLM_PROVIDER",
    "ANTHROPIC_API_KEY",
    "EMAIL_ENABLED",
    "EMAIL_HOST",
    "EMAIL_PORT",
    "EMAIL_HOST_USER",
    "EMAIL_HOST_PASSWORD",
    "DEFAULT_FROM_EMAIL",
    "SIGNUP_CODE",
    "PHONE_TUNNEL",
]

# Une variable présente mais vide (copie brute de .env.example) vaut « non renseignée ».
for _name in ENV_VARIABLES:
    if os.environ.get(_name, "").strip() == "":
        os.environ.pop(_name, None)

env = environ.Env()


def _project_path(value: str) -> Path:
    """Chemin absolu ; un chemin relatif s'entend depuis la racine du projet."""
    path = Path(value)
    return path if path.is_absolute() else BASE_DIR / path


# --- Base ---------------------------------------------------------------------------------------

DEBUG = env.bool("DJANGO_DEBUG", default=True)


def _secret_key() -> str:
    key = env.str("DJANGO_SECRET_KEY", default="")
    if key:
        return key
    if not DEBUG:
        raise ImproperlyConfigured("DJANGO_SECRET_KEY est obligatoire quand DJANGO_DEBUG=false.")
    # En développement : clé générée une fois, conservée dans data/ pour garder les sessions.
    key_file = DATA_DIR / "dev_secret_key"
    if not key_file.exists():
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        key_file.write_text(get_random_secret_key(), encoding="utf-8")
    return key_file.read_text(encoding="utf-8").strip()


SECRET_KEY = _secret_key()
ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS", default=["localhost", "127.0.0.1"])
SITE_URL = env.str("SITE_URL", default="http://localhost:8000").rstrip("/")
CSRF_TRUSTED_ORIGINS = env.list("CSRF_TRUSTED_ORIGINS", default=[])

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "allauth",
    "allauth.account",
    "accounts",
    "bank",
    "exam",
    "advice",
    "backoffice",
    "usage",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "allauth.account.middleware.AccountMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "config.context_processors.site",
            ],
        },
    },
]

# --- Base de données ----------------------------------------------------------------------------

DATABASES = {
    "default": env.db_url_config(env.str("DATABASE_URL", default="sqlite:///data/db.sqlite3"))
}

if DATABASES["default"]["ENGINE"] == "django.db.backends.sqlite3":
    _db_path = _project_path(str(DATABASES["default"]["NAME"]))
    _db_path.parent.mkdir(parents=True, exist_ok=True)
    DATABASES["default"]["NAME"] = _db_path
    DATABASES["default"]["OPTIONS"] = {
        "init_command": (
            "PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL; PRAGMA busy_timeout=5000;"
        ),
        "transaction_mode": "IMMEDIATE",
    }

# Tests : base dans un fichier (et non en mémoire), pour que le serveur des tests de bout en bout
# et le navigateur se partagent la base avec les mêmes verrous qu'en vrai (mode WAL, attente).
if "pytest" in sys.modules and DATABASES["default"]["ENGINE"] == "django.db.backends.sqlite3":
    DATABASES["default"]["TEST"] = {"NAME": str(DATA_DIR / "test.sqlite3")}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --- Mots de passe ------------------------------------------------------------------------------

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# --- Langue et fuseau ---------------------------------------------------------------------------

LANGUAGE_CODE = "fr"
LANGUAGES = [("fr", "Français")]
TIME_ZONE = "Europe/Paris"
USE_I18N = True
USE_TZ = True

# --- Fichiers statiques et médias ---------------------------------------------------------------

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"

MEDIA_URL = "/media/"
MEDIA_ROOT = _project_path(env.str("MEDIA_ROOT", default="data/media"))

# Base et médias de démonstration : seuls endroits où `make_dev_test` et `reset_demo` s'exécutent.
DEMO_DATABASE_PATH = BASE_DIR / "data" / "demo.sqlite3"
DEMO_MEDIA_ROOT = BASE_DIR / "data" / "demo-media"

# --- Fournisseurs externes (factices par défaut) ------------------------------------------------

TTS_PROVIDER = env.str("TTS_PROVIDER", default="fake")
GOOGLE_TTS_API_KEY = env.str("GOOGLE_TTS_API_KEY", default="")
LLM_PROVIDER = env.str("LLM_PROVIDER", default="fake")
ANTHROPIC_API_KEY = env.str("ANTHROPIC_API_KEY", default="")

# --- Démonstration (demo.cmd) -------------------------------------------------------------------
# Le site de démonstration se reconnaît à sa base. Il ne touche jamais aux médias réels ni aux clés
# d'API, et ses cookies portent d'autres noms : les deux sites partagent `localhost`.

DEMO = Path(str(DATABASES["default"]["NAME"])) == DEMO_DATABASE_PATH
if DEMO:
    MEDIA_ROOT = DEMO_MEDIA_ROOT
    TTS_PROVIDER = LLM_PROVIDER = "fake"
    GOOGLE_TTS_API_KEY = ANTHROPIC_API_KEY = ""
    SESSION_COOKIE_NAME = "tcf_demo_sessionid"
    CSRF_COOKIE_NAME = "tcf_demo_csrftoken"

# Démonstration sur téléphone (telephone.cmd) : le site est joint par un Cloudflare Quick Tunnel,
# en HTTPS, à une adresse *.trycloudflare.com. Ce domaine n'est autorisé que dans ce mode.
PHONE_TUNNEL = DEMO and env.bool("PHONE_TUNNEL", default=False)
if PHONE_TUNNEL:
    ALLOWED_HOSTS = [*ALLOWED_HOSTS, ".trycloudflare.com"]
    CSRF_TRUSTED_ORIGINS = [*CSRF_TRUSTED_ORIGINS, "https://*.trycloudflare.com"]
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# --- E-mails (désactivés au MVP) ----------------------------------------------------------------

EMAIL_ENABLED = env.bool("EMAIL_ENABLED", default=False)
if EMAIL_ENABLED:
    EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
    EMAIL_HOST = env.str("EMAIL_HOST", default="")
    EMAIL_PORT = env.int("EMAIL_PORT", default=587)
    EMAIL_HOST_USER = env.str("EMAIL_HOST_USER", default="")
    EMAIL_HOST_PASSWORD = env.str("EMAIL_HOST_PASSWORD", default="")
    EMAIL_USE_TLS = True
else:
    EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
DEFAULT_FROM_EMAIL = env.str("DEFAULT_FROM_EMAIL", default="webmaster@localhost")

# --- Comptes (django-allauth) -------------------------------------------------------------------

SIGNUP_CODE = env.str("SIGNUP_CODE", default="")

# Seul le backend d'allauth : connexion par e-mail, avec limitation des tentatives.
AUTHENTICATION_BACKENDS = ["allauth.account.auth_backends.AuthenticationBackend"]
LOGIN_URL = "account_login"
LOGIN_REDIRECT_URL = "dashboard"
ACCOUNT_LOGOUT_REDIRECT_URL = "home"

ACCOUNT_LOGIN_METHODS = {"email"}
ACCOUNT_SIGNUP_FIELDS = ["email*", "password1*", "password2*"]
ACCOUNT_EMAIL_VERIFICATION = "none"
ACCOUNT_UNIQUE_EMAIL = True
ACCOUNT_SESSION_REMEMBER = True
ACCOUNT_SIGNUP_FORM_CLASS = "accounts.forms.SignupCodeForm"

# --- Sécurité en production ---------------------------------------------------------------------

SESSION_COOKIE_HTTPONLY = True
if not DEBUG:
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SECURE_HSTS_SECONDS = 60 * 60 * 24 * 365
    SECURE_CONTENT_TYPE_NOSNIFF = True
    # Caddy redirige déjà HTTP vers HTTPS (W008). Sans nom de domaine, ni sous-domaines ni liste
    # de préchargement HSTS n'ont de sens (W005, W021).
    SILENCED_SYSTEM_CHECKS = ["security.W005", "security.W008", "security.W021"]
