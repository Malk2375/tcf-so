#!/usr/bin/env bash
# Sauvegarde quotidienne (cron, en root) : copie cohérente de SQLite (sqlite3 .backup), puis restic
# vers un stockage hors site ; rétention 7 jours et 4 semaines.
# Réglages dans /etc/tcf-so/backup.env (voir docs/DEPLOIEMENT.md) :
#   RESTIC_REPOSITORY, RESTIC_PASSWORD, AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_DEFAULT_REGION
#   (OVHcloud Object Storage, S3, dans une autre région que le VPS).
set -euo pipefail
cd "$(dirname "$0")/.."

set -a
# shellcheck disable=SC1091
. /etc/tcf-so/backup.env
set +a
compose() { docker compose --env-file .env -f deploy/compose.yml "$@"; }

WORK=/var/backups/tcf-so
mkdir -p "$WORK"
chmod 700 "$WORK"

# 1. Copie cohérente de la base, même pendant que le site écrit (API de sauvegarde de SQLite).
compose exec -T web sh -c '
  set -e
  mkdir -p /app/data/backup
  rm -f /app/data/backup/db.sqlite3
  sqlite3 /app/data/db.sqlite3 ".backup /app/data/backup/db.sqlite3"
  test "$(sqlite3 /app/data/backup/db.sqlite3 "PRAGMA integrity_check;")" = "ok"
'
compose cp web:/app/data/backup/db.sqlite3 "$WORK/db.sqlite3"

# 2. Fichiers audio : lus directement dans le volume Docker.
MEDIA="$(docker volume inspect tcf-so_data --format '{{ .Mountpoint }}')/media"

# 3. Envoi hors site, puis rétention.
restic backup --tag tcf-so "$WORK/db.sqlite3" "$MEDIA"
restic forget --tag tcf-so --keep-daily 7 --keep-weekly 4 --prune
restic check
echo "Sauvegarde terminée : $(date '+%Y-%m-%d %H:%M')"
