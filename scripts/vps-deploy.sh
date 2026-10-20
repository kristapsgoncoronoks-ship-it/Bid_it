#!/bin/bash
# One-command production deploy for the single-VPS stack (Hostinger KVM).
#
#   cd /root/Bid_it && ./scripts/vps-deploy.sh [branch]
#
# Default branch: whatever the VPS checkout is already on. The script is the
# executable form of docs/DEPLOY-RUNBOOK §1/§3/§5: build before maintenance,
# stop and verify the old API/extract writers, prove BOTH backups, then start
# the prepared images. Capture migrations require this quiescent window.
# Keep old writers stopped after any migration/start attempt; reconciliation
# is required before reverting a queue consumer. Earlier backup failures were hit
# for real on 2026-08-20 (missing required .env var → compose dead; wrong tar path →
# 45-byte "backup" that looked like a completed step).
set -euo pipefail
umask 077

COMPOSE_FILE="docker-compose.hostinger.yml"
COMPOSE=(docker compose -f "$COMPOSE_FILE")
STAMP="$(date +%F-%H%M%S)"
BRANCH="${1:-$(git rev-parse --abbrev-ref HEAD)}"
HEALTH_TIMEOUT_S=300
BIDIT_DEPLOY_BACKUP_DIR="${BIDIT_DEPLOY_BACKUP_DIR:-$HOME}"
previous_writer_services=()
writers_stop_attempted=0
promotion_attempted=0

say()  { printf '\n== %s\n' "$*"; }
fail() { printf '\nDEPLOY REFUSED: %s\n' "$*" >&2; exit 1; }

on_exit() {
  deploy_status=$?
  trap - EXIT
  if [ "$deploy_status" -ne 0 ] && [ "$writers_stop_attempted" -eq 1 ]; then
    if [ "$promotion_attempted" -eq 0 ]; then
      # No new container or migration was started. `start` resumes existing
      # containers; it must not recreate them from the newly built image tags.
      if [ "${#previous_writer_services[@]}" -gt 0 ]; then
        printf '\nPreparation failed; resuming the previously running containers.\n' >&2
        if ! "${COMPOSE[@]}" start "${previous_writer_services[@]}"; then
          printf 'Automatic resume failed. Manually restore the previous containers; no migration was attempted.\n' >&2
        fi
      fi
    else
      printf '\nStartup may have applied migrations. Old writers are NOT automatically restarted.\n' >&2
      printf 'Rollback requires the verified database/document backups and capture-job reconciliation; see docs/DEPLOY-RUNBOOK-2026-08-15.md §6.\n' >&2
      printf 'Rollback artifacts: %s ; %s ; %s\n' "$db_dump" "$docs_tar" "$images_record" >&2
    fi
  fi
  exit "$deploy_status"
}
trap on_exit EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

[ -f "$COMPOSE_FILE" ] || fail "run from the repo root (no $COMPOSE_FILE here)"
[ -f .env ] || fail "no .env — copy .env.hostinger.example and fill it in"
command -v flock >/dev/null || fail "flock is required to serialize deployment script invocations"
mkdir -p "$BIDIT_DEPLOY_BACKUP_DIR"
# The same backup directory must be used by manual and automatic invocations.
# GitHub's concurrency group alone cannot serialize a simultaneous manual run.
# This lock does not protect checkout mutations a host wrapper performs before
# entering this script; keep those operations exclusive too.
exec 9> "$BIDIT_DEPLOY_BACKUP_DIR/.bidit-vps-deploy.lock"
flock -n 9 || fail "another VPS deployment is running; leave its maintenance window intact"

# ---- 1. Preflight: every ${VAR:?...} the compose file requires must be in .env
say "Preflight: required variables"
missing=0
for var in $(grep -oE '\$\{[A-Z_]+:\?' "$COMPOSE_FILE" | tr -d '${:?' | sort -u); do
  if ! grep -q "^${var}=" .env; then
    case "$var" in
      INBOUND_EMAIL_SECRET|*_TOKEN)
        echo "  MISSING: $var  — generate and add with:"
        echo "    echo \"$var=\$(python3 -c 'import secrets;print(secrets.token_urlsafe(32))')\" >> .env" ;;
      *)
        echo "  MISSING: $var  — add its real value to .env (see .env.hostinger.example)" ;;
    esac
    missing=1
  fi
done
[ "$missing" -eq 0 ] || fail "add the variable(s) above to .env, then re-run"
echo "  all required variables present"

# ---- 2. Preflight: disk space (the image build needs headroom)
say "Preflight: disk"
avail_mb=$(df -Pm . | awk 'NR==2{print $4}')
echo "  ${avail_mb} MB available"
[ "$avail_mb" -ge 2048 ] || fail "under 2 GB free — prune first: docker system prune -f"

# ---- 3. Record existing image identities before build retags local images.
# The documented SSH wrapper resets the checkout before invoking this script,
# so checkout HEAD is evidence, NOT necessarily the previously deployed commit.
checkout_at_start=$(git rev-parse HEAD)
images_record="$BIDIT_DEPLOY_BACKUP_DIR/pre-deploy-images-${STAMP}.txt"
for service in backend worker frontend db; do
  previous_images=$("${COMPOSE[@]}" images -q "$service")
  printf '%s %s\n' "$service" "${previous_images:-<no existing container image>}" >> "$images_record"
done

# ---- 4. Prepare all images while the existing containers are still running.
say "Pulling $BRANCH"
git fetch origin "$BRANCH"
git checkout "$BRANCH"
git pull --ff-only origin "$BRANCH"
echo "  now at $(git rev-parse --short HEAD): $(git log -1 --format=%s)"
say "Building images before the maintenance window"
"${COMPOSE[@]}" build

# ---- 5. Quiesce every API/extract writer in this single-VPS Compose project.
# Includes one-off containers in verification: any leftover writer must refuse
# the migration. External workers must be stopped by their operator separately.
say "Stopping and verifying API/extract writers"
running_services=$("${COMPOSE[@]}" ps --status running --services backend worker)
if [ -n "$running_services" ]; then
  while IFS= read -r service; do
    case "$service" in
      backend|worker) previous_writer_services+=("$service") ;;
      *) fail "unexpected writer service in Compose status" ;;
    esac
  done <<< "$running_services"
fi
writers_stop_attempted=1
"${COMPOSE[@]}" stop --timeout 120 backend worker
remaining_writers=$("${COMPOSE[@]}" ps --all --status running --status restarting --status paused -q backend worker)
[ -z "$remaining_writers" ] || fail "API/extract writer containers are still running; no migration attempted"

# ---- 6. Backups after quiescence, with proof. No new migrations have run.
say "Backup: database"
db_dump="$BIDIT_DEPLOY_BACKUP_DIR/pre-deploy-${STAMP}.sql.gz"
# Resolve the effective identity inside the database container. PGDATABASE is
# a literal fallback value; pg_dump's --dbname can instead parse a name with
# '=' as connection information. Keep configuration values out of shell code.
"${COMPOSE[@]}" exec -T db sh -ec '
  : "${POSTGRES_USER:?POSTGRES_USER is required}"
  : "${POSTGRES_DB:?POSTGRES_DB is required}"
  export PGUSER="$POSTGRES_USER" PGDATABASE="$POSTGRES_DB"
  exec pg_dump
' | gzip > "$db_dump"
# pg_dump ends with the marker followed by "--" and a blank line, so check the
# TAIL REGION, not the literal last line (tail -1 here refused a good dump on
# the real VPS, 2026-08-23).
gunzip -c "$db_dump" | tail -5 | grep -q "PostgreSQL database dump complete" \
  || fail "dump is truncated or empty: $db_dump"
echo "  OK: $db_dump ($(du -h "$db_dump" | cut -f1))"

say "Backup: document bytes (storagedata volume)"
docs_tar="$BIDIT_DEPLOY_BACKUP_DIR/pre-deploy-docs-${STAMP}.tar.gz"
docker run --rm -v invoiceiq_storagedata:/data alpine tar czf - -C /data . > "$docs_tar"
entries=$(tar tzf "$docs_tar" | wc -l)
echo "  OK: $docs_tar ($(du -h "$docs_tar" | cut -f1), $entries entries)"
[ "$entries" -gt 0 ] || echo "  note: volume is empty — valid on a fresh instance"

say "Backup: checkout and image evidence"
printf '%s\n' "$checkout_at_start" > "$BIDIT_DEPLOY_BACKUP_DIR/pre-deploy-commit.txt"
echo "  checkout at script entry: $checkout_at_start (not a deployed-revision assertion)"
echo "  previous container image IDs: $images_record"

# ---- 7. Start only prepared images; backend migrations precede the new worker.
say "Starting prepared images (migrations run before the new worker)"
promotion_attempted=1
"${COMPOSE[@]}" up -d --no-build

# ---- 8. Wait for health — a deploy is not done until the app answers
say "Waiting for /health and /health/ready (up to ${HEALTH_TIMEOUT_S}s)"
deadline=$(( $(date +%s) + HEALTH_TIMEOUT_S ))
until "${COMPOSE[@]}" exec -T backend python -c "
import urllib.request,sys
for p in ('/health','/health/ready'):
    r=urllib.request.urlopen('http://localhost:8000'+p,timeout=5)
    assert r.status==200, p
" 2>/dev/null; do
  if [ "$(date +%s)" -ge "$deadline" ]; then
    echo; echo "---- backend log tail ----"
    "${COMPOSE[@]}" logs --tail 40 backend || true
    fail "backend not healthy after ${HEALTH_TIMEOUT_S}s.
  Rollback artifacts:  $db_dump  ·  $docs_tar  ·  $BIDIT_DEPLOY_BACKUP_DIR/pre-deploy-commit.txt
  Procedure: docs/DEPLOY-RUNBOOK-2026-08-15.md §6 (code alone is NOT enough —
  restore the DB dump too if migrations already ran)."
  fi
  sleep 5
done

# ---- 9. Report
say "Deployed"
echo "  commit:    $(git rev-parse --short HEAD)"
echo "  migration: $("${COMPOSE[@]}" exec -T backend alembic current 2>/dev/null | tail -1)"
"${COMPOSE[@]}" ps
echo
echo "Post-deploy verification checklist: docs/DEPLOY-RUNBOOK-2026-08-15.md §5"
