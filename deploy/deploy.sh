#!/usr/bin/env bash
# Premier déploiement et mises à jour, sur le serveur, depuis la racine du projet :
#   bash deploy/deploy.sh
# Récupère le code, sauvegarde la base (si la sauvegarde est configurée), reconstruit l'image,
# applique les migrations, met à jour les formats d'examen et relance les services.
set -euo pipefail
cd "$(dirname "$0")/.."

if [ ! -f .env ]; then
  echo "Fichier .env absent : cp deploy/env.production.example .env, puis remplissez-le." >&2
  exit 1
fi
compose() { docker compose --env-file .env -f deploy/compose.yml "$@"; }

echo "== Récupération du code"
git pull --ff-only

if [ -f /etc/tcf-so/backup.env ] && compose ps --status running --services | grep -qx web; then
  echo "== Sauvegarde avant mise à jour"
  bash deploy/backup.sh
fi

echo "== Construction de l'image"
compose build web

echo "== Migrations et formats d'examen"
compose run --rm web python manage.py migrate --noinput
compose run --rm web python manage.py seed_formats

echo "== Démarrage"
compose up -d --remove-orphans
compose exec -T web python manage.py check --deploy --fail-level ERROR
compose ps
echo "Site : https://$(grep -E '^SITE_ADDRESS=' .env | cut -d= -f2)"
