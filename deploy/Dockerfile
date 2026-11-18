# Image du site (service « web ») : Django servi par Gunicorn (workers à threads).
# Construite par deploy/deploy.sh sur le serveur ; jamais exécutée par Claude Code.
FROM ghcr.io/astral-sh/uv:0.13.8 AS uv

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH" \
    DJANGO_DEBUG=false

# sqlite3 : sauvegarde cohérente de la base (deploy/backup.sh). ffmpeg vient du paquet imageio-ffmpeg.
RUN apt-get update \
    && apt-get install -y --no-install-recommends sqlite3 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=uv /uv /usr/local/bin/uv

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY . .

# Compte sans droits ; data/ (base SQLite, audio) et staticfiles/ sont des volumes.
RUN useradd --create-home --uid 1000 app \
    && mkdir -p /app/data /app/staticfiles \
    && chown -R app:app /app/data /app/staticfiles
USER app

EXPOSE 8000
CMD ["sh", "-c", "python manage.py collectstatic --noinput --verbosity 0 && exec gunicorn config.wsgi --bind 0.0.0.0:8000 --workers 2 --threads 4 --timeout 180 --access-logfile -"]
