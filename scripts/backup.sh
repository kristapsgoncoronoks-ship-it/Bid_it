#!/usr/bin/env bash
# Scheduled backups for the single-VPS stack (OPS-004, audit 2026-09-05).
#
# Before this script the ONLY backups were the pre-deploy dump and tarball that
# scripts/vps-deploy.sh takes — and the CI auto-deploy path does not run that
# script. So the recovery point was "the last time a human happened to run a
# command". This script takes the same two VERIFIED backups on a schedule,
# prunes old ones, and optionally copies them off the box.
#
# Install (as root on the VPS), then check `ls ~/backups` the next morning:
#   (crontab -l 2>/dev/null; echo '17 2 * * * cd /root/Bid_it && ./scripts/backup.sh >> /root/backup.log 2>&1') | crontab -
#
# Environment (all optional):
#   BACKUP_DIR        where to write            (default: $HOME/backups)
#   BACKUP_KEEP_DAYS  prune backups older than  (default: 14)
#   RCLONE_REMOTE     e.g. "b2:invoiceiq-backups" — if set and `rclone` is
#                     installed, every new backup is copied there too. A backup
#                     that lives only on the machine it protects is not a backup.
#   COMPOSE_FILE_PATH compose file                (default: docker-compose.hostinger.yml)
#
# The document-bytes volume name is derived from the compose project name
# (`invoiceiq`) + the volume key (`storagedata`), matching vps-deploy.sh.
set -euo pipefail

STAMP="$(date +%F-%H%M)"
BACKUP_DIR="${BACKUP_DIR:-$HOME/backups}"
KEEP_DAYS="${BACKUP_KEEP_DAYS:-14}"
COMPOSE=(docker compose -f "${COMPOSE_FILE_PATH:-docker-compose.hostinger.yml}")
mkdir -p "$BACKUP_DIR"

say()  { printf '\n== %s\n' "$*"; }
fail() { printf '\nBACKUP FAILED: %s\n' "$*" >&2; exit 1; }

# ---- 1. Database: dump, and PROVE it is complete (a backup that is not
#         verified is a hypothesis). pg_dump ends with the marker followed by
#         "--" and a blank line, so check the tail REGION, not the last line.
say "database → $BACKUP_DIR/db-${STAMP}.sql.gz"
db_dump="$BACKUP_DIR/db-${STAMP}.sql.gz"
"${COMPOSE[@]}" exec -T db pg_dump -U "${POSTGRES_USER:-invoiceiq}" "${POSTGRES_DB:-invoiceiq}" | gzip > "$db_dump"
gunzip -c "$db_dump" | tail -5 | grep -q "PostgreSQL database dump complete" \
  || fail "dump is truncated or empty: $db_dump"
echo "  OK $(du -h "$db_dump" | cut -f1)"

# ---- 2. Document bytes (uploaded originals, receipts, logos, archive PDFs).
say "documents → $BACKUP_DIR/docs-${STAMP}.tar.gz"
docs_tar="$BACKUP_DIR/docs-${STAMP}.tar.gz"
docker run --rm -v invoiceiq_storagedata:/data alpine tar czf - -C /data . > "$docs_tar"
entries=$(tar tzf "$docs_tar" | wc -l)
echo "  OK $(du -h "$docs_tar" | cut -f1), $entries entries"
[ "$entries" -gt 0 ] || echo "  note: volume is empty — valid on a fresh instance"

# ---- 3. The .env is part of the restore. SECRET_KEY derives the KEK that
#         seals tenant SSO secrets at rest (default kek_provider=local): a
#         database restored WITHOUT the same SECRET_KEY has unreadable sealed
#         columns. Back it up beside the data — it is a secret, so keep the
#         backup directory private (chmod 700) and encrypt any off-box copy.
if [ -f .env ]; then
  say "environment → $BACKUP_DIR/env-${STAMP}"
  install -m 600 .env "$BACKUP_DIR/env-${STAMP}"
  echo "  OK"
fi
chmod 700 "$BACKUP_DIR"

# ---- 4. Off the box, if configured.
if [ -n "${RCLONE_REMOTE:-}" ]; then
  if command -v rclone >/dev/null; then
    say "copying to $RCLONE_REMOTE"
    rclone copy "$db_dump" "$RCLONE_REMOTE/" && rclone copy "$docs_tar" "$RCLONE_REMOTE/" \
      || fail "rclone copy to $RCLONE_REMOTE failed"
    [ -f "$BACKUP_DIR/env-${STAMP}" ] && rclone copy "$BACKUP_DIR/env-${STAMP}" "$RCLONE_REMOTE/"
    echo "  OK"
  else
    echo "  WARNING: RCLONE_REMOTE is set but rclone is not installed — backups are on this box only"
  fi
else
  echo "  note: RCLONE_REMOTE unset — backups are on this box only (Hostinger snapshots are the coarser fallback)"
fi

# ---- 5. Prune.
say "pruning backups older than ${KEEP_DAYS} days"
find "$BACKUP_DIR" -maxdepth 1 -type f \( -name 'db-*.sql.gz' -o -name 'docs-*.tar.gz' -o -name 'env-*' \) \
  -mtime "+${KEEP_DAYS}" -print -delete || true

say "done: $(date -Is)"
