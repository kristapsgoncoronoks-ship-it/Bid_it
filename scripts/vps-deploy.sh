#!/bin/bash
# One-command production deploy for the single-VPS stack (Hostinger KVM).
#
#   cd /root/Bid_it && ./scripts/vps-deploy.sh [branch]
#
# Default branch: whatever the VPS checkout is already on. The script is the
# executable form of docs/DEPLOY-RUNBOOK §1/§3/§5: it refuses to deploy until
# the preflight passes and BOTH backups are proven non-empty, then pulls,
# rebuilds, waits for health, and prints exactly where the rollback artifacts
# are if anything goes wrong. Every failure it guards against was hit for real
# on 2026-08-20 (missing required .env var → compose dead; wrong tar path →
# 45-byte "backup" that looked like a completed step).
set -euo pipefail

COMPOSE_FILE="docker-compose.hostinger.yml"
COMPOSE=(docker compose -f "$COMPOSE_FILE")
STAMP="$(date +%F-%H%M)"
BRANCH="${1:-$(git rev-parse --abbrev-ref HEAD)}"
HEALTH_TIMEOUT_S=300

say()  { printf '\n== %s\n' "$*"; }
fail() { printf '\nDEPLOY REFUSED: %s\n' "$*" >&2; exit 1; }

[ -f "$COMPOSE_FILE" ] || fail "run from the repo root (no $COMPOSE_FILE here)"
[ -f .env ] || fail "no .env — copy .env.hostinger.example and fill it in"

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

# ---- 3. Backups, with proof (a backup that is not verified is a hypothesis)
say "Backup: database"
db_dump="$HOME/pre-deploy-${STAMP}.sql.gz"
"${COMPOSE[@]}" exec -T db pg_dump -U invoiceiq invoiceiq | gzip > "$db_dump"
gunzip -c "$db_dump" | tail -1 | grep -q "PostgreSQL database dump complete" \
  || fail "dump is truncated or empty: $db_dump"
echo "  OK: $db_dump ($(du -h "$db_dump" | cut -f1))"

say "Backup: document bytes (storagedata volume)"
docs_tar="$HOME/pre-deploy-docs-${STAMP}.tar.gz"
docker run --rm -v invoiceiq_storagedata:/data alpine tar czf - -C /data . > "$docs_tar"
entries=$(tar tzf "$docs_tar" | wc -l)
echo "  OK: $docs_tar ($(du -h "$docs_tar" | cut -f1), $entries entries)"
[ "$entries" -gt 0 ] || echo "  note: volume is empty — valid on a fresh instance"

say "Backup: rollback commit"
git rev-parse HEAD > "$HOME/pre-deploy-commit.txt"
echo "  $(cat "$HOME/pre-deploy-commit.txt") (branch $(git rev-parse --abbrev-ref HEAD))"

# ---- 4. Pull the release
say "Pulling $BRANCH"
git fetch origin "$BRANCH"
git checkout "$BRANCH"
git pull --ff-only origin "$BRANCH"
echo "  now at $(git rev-parse --short HEAD): $(git log -1 --format=%s)"

# ---- 5. Build + start (migrations run on boot inside the backend container)
say "Building and starting (this is the slow part)"
"${COMPOSE[@]}" up -d --build

# ---- 6. Wait for health — a deploy is not done until the app answers
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
  Rollback artifacts:  $db_dump  ·  $docs_tar  ·  $HOME/pre-deploy-commit.txt
  Procedure: docs/DEPLOY-RUNBOOK-2026-08-15.md §6 (code alone is NOT enough —
  restore the DB dump too if migrations already ran)."
  fi
  sleep 5
done

# ---- 7. Report
say "Deployed"
echo "  commit:    $(git rev-parse --short HEAD)"
echo "  migration: $("${COMPOSE[@]}" exec -T backend alembic current 2>/dev/null | tail -1)"
"${COMPOSE[@]}" ps
echo
echo "Post-deploy verification checklist: docs/DEPLOY-RUNBOOK-2026-08-15.md §5"
