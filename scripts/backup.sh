#!/bin/sh
set -eu
TASK_BACKUP_DIR=${1:-./backups}
mkdir -p "$TASK_BACKUP_DIR"
chmod 700 "$TASK_BACKUP_DIR"
umask 077
TASK_BACKUP_STAMP=$(date -u +%Y%m%dT%H%M%SZ)
docker compose exec -T postgres pg_dump -U tone_correction -d tone_correction > "$TASK_BACKUP_DIR/database-$TASK_BACKUP_STAMP.sql"
docker compose exec -T app tar -C /app/data -czf - . > "$TASK_BACKUP_DIR/audio-$TASK_BACKUP_STAMP.tar.gz"
echo '数据库与录音备份已完成。'
