#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# Fleet Fuel — DEV-PHASE auto-deploy.
#
# Polls the configured git branch; when it moves, it redeploys the app:
#   fetch -> (detect dep change) -> checkout files (PRESERVING the live data DBs)
#   -> best-effort dep install -> restart the service -> health-check.
#
# It is idempotent (a no-op when nothing changed), serialized with a lock, and
# safe to run from a systemd timer every couple of minutes.
#
# ⚠ This is a CONVENIENCE FOR THE DEVELOPMENT PHASE — it auto-ships whatever is on
#   the dev branch with no human gate. REMOVE IT before you go to live production:
#       sudo systemctl disable --now fleetfuel-autodeploy.timer
#   See deploy/AUTODEPLOY.md.
#
# All settings are overridable via environment (defaults match the current box).
# ─────────────────────────────────────────────────────────────────────────────
set -uo pipefail

APP_DIR="${FFS_APP_DIR:-/opt/fleetfuel/app}"
BRANCH="${FFS_DEPLOY_BRANCH:-claude/gracious-curie-c7so3s}"
SERVICE="${FFS_SERVICE:-fleetfuel}"
APP_USER="${FFS_APP_USER:-fleetfuel}"
HEALTH_URL="${FFS_HEALTH_URL:-http://127.0.0.1:8050/login}"
STATE="${FFS_DEPLOY_STATE:-/var/lib/fleetfuel-deploy/last_sha}"
# Data DBs that must NEVER be overwritten by a deploy (live state lives here).
EXCLUDES=( ':!customers.db' ':!suppliers.db' ':!fuel_history.db' )

log(){ echo "$(date -Is) [autodeploy] $*"; }

mkdir -p "$(dirname "$STATE")"

# Only one deploy at a time.
exec 9>/var/lock/fleetfuel-deploy.lock
flock -n 9 || { log "another run in progress; skipping"; exit 0; }

cd "$APP_DIR" || { log "APP_DIR $APP_DIR does not exist"; exit 1; }

# Fetch the dev branch. Non-interactive: the box needs a read-only deploy key or a
# stored credential (see AUTODEPLOY.md) or this will fail rather than prompt.
if ! sudo -u "$APP_USER" GIT_TERMINAL_PROMPT=0 git fetch --quiet origin "$BRANCH"; then
  log "git fetch failed — check the deploy key / network (GIT_TERMINAL_PROMPT=0, no prompt)"
  exit 1
fi

REMOTE_SHA="$(sudo -u "$APP_USER" git rev-parse "origin/$BRANCH" 2>/dev/null || echo '')"
[ -n "$REMOTE_SHA" ] || { log "could not resolve origin/$BRANCH"; exit 1; }
LAST_SHA="$(cat "$STATE" 2>/dev/null || echo none)"

# No change -> quiet success (this is the common case, runs every couple of minutes).
[ "$REMOTE_SHA" = "$LAST_SHA" ] && exit 0

log "branch '$BRANCH' moved: ${LAST_SHA:0:12} -> ${REMOTE_SHA:0:12}; deploying"

# Did requirements.txt change between the deployed SHA and the new one?
REQ_CHANGED=1
if [ "$LAST_SHA" != "none" ] && \
   sudo -u "$APP_USER" git diff --quiet "$LAST_SHA" "$REMOTE_SHA" -- requirements.txt 2>/dev/null; then
  REQ_CHANGED=0
fi

# Check out the new code into the working tree, preserving the live data DBs.
if ! sudo -u "$APP_USER" git checkout "origin/$BRANCH" -- . "${EXCLUDES[@]}"; then
  log "checkout failed — leaving the running version in place"
  exit 1
fi

# Best-effort dependency install when requirements changed. System Python here is
# PEP-668 externally-managed, so --break-system-packages is the dev-box pragmatic
# path. NEVER fail the deploy on this — log and continue; a dep that needs apt is
# surfaced for a human to handle.
if [ "$REQ_CHANGED" = "1" ]; then
  log "requirements.txt changed — installing deps (best-effort)"
  sudo -u "$APP_USER" python3 -m pip install --break-system-packages -q -r requirements.txt \
    || log "WARNING: pip reported an issue — review deps manually (some may need 'apt install python3-<pkg>')"
fi

# Restart and health-check.
systemctl restart "$SERVICE"
sleep 2
CODE="$(curl -fsS -o /dev/null -w '%{http_code}' "$HEALTH_URL" 2>/dev/null || echo 000)"
if [ "$CODE" = "200" ]; then
  echo "$REMOTE_SHA" > "$STATE"
  log "deployed ${REMOTE_SHA:0:12} OK (health $CODE)"
else
  # Do NOT advance the state file, so the next run retries the same SHA.
  log "WARNING: health check returned $CODE after restart — service may be unhealthy. State NOT advanced; will retry. Inspect: journalctl -u $SERVICE -n 50"
  exit 1
fi
