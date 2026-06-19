# Auto-deploy (development phase)

A **pull-based** deployer: the server polls the dev branch every ~2 minutes and, when
it moves, redeploys automatically (checkout → deps → restart → health-check), preserving
the live data DBs (`customers.db`, `suppliers.db`, `fuel_history.db`).

> ⚠ **Development convenience only.** It ships whatever is on the dev branch with **no human
> gate**. **Disable it before live production** (see *Turn it off* below).

No GitHub secrets, no inbound ports — the box pulls. Files:
- `deploy/auto_deploy.sh` — the deploy logic (idempotent, locked, health-checked)
- `deploy/fleetfuel-autodeploy.service` / `.timer` — systemd units
- defaults match the current box; override via `/etc/default/fleetfuel-autodeploy`

---

## One-time setup (run on the server as root)

### 1. Non-interactive git auth (read-only deploy key)
Auto-deploy can't type a password, so give the box a read-only key for the repo:

```bash
sudo -u fleetfuel ssh-keygen -t ed25519 -N "" -f /home/fleetfuel/.ssh/id_ed25519 -C "fleetfuel-deploy"
sudo -u fleetfuel cat /home/fleetfuel/.ssh/id_ed25519.pub
```
Copy that public key → GitHub repo → **Settings → Deploy keys → Add deploy key**
(leave **Allow write access** UNchecked — read-only). Then point the remote at SSH and
verify the fetch is non-interactive:
```bash
sudo -u fleetfuel git -C /opt/fleetfuel/app remote set-url origin \
  git@github.com:kristapsgoncoronoks-ship-it/fleet_fuel_system.git
sudo -u fleetfuel ssh -o StrictHostKeyChecking=accept-new -T git@github.com   # trust github (prints a greeting; that's fine)
sudo -u fleetfuel git -C /opt/fleetfuel/app fetch origin                       # must NOT prompt
```

### 2. Get these files onto the box (one last manual deploy)
```bash
sudo -u fleetfuel git -C /opt/fleetfuel/app checkout \
  origin/claude/gracious-curie-c7so3s -- deploy/
sudo chmod +x /opt/fleetfuel/app/deploy/auto_deploy.sh
```

### 3. Install + start the timer
```bash
sudo cp /opt/fleetfuel/app/deploy/fleetfuel-autodeploy.service /etc/systemd/system/
sudo cp /opt/fleetfuel/app/deploy/fleetfuel-autodeploy.timer   /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now fleetfuel-autodeploy.timer
sudo systemctl start fleetfuel-autodeploy.service     # run once now to seed it
journalctl -u fleetfuel-autodeploy.service -n 30 --no-pager
```

From now on, every push to the dev branch is live within ~2 minutes.

---

## Watch it
```bash
journalctl -u fleetfuel-autodeploy.service -f          # follow deploys
systemctl list-timers fleetfuel-autodeploy.timer       # next run
```

## Configure (optional) — `/etc/default/fleetfuel-autodeploy`
```ini
FFS_DEPLOY_BRANCH=claude/gracious-curie-c7so3s
FFS_APP_DIR=/opt/fleetfuel/app
FFS_SERVICE=fleetfuel
FFS_APP_USER=fleetfuel
FFS_HEALTH_URL=http://127.0.0.1:8050/login
```
Change the polling cadence in the `.timer` (`OnUnitActiveSec`).

## Turn it off (do this before live production)
```bash
sudo systemctl disable --now fleetfuel-autodeploy.timer
```
That's it — the app keeps running the last deployed version; updates simply go back to
being manual. Re-enable any time with `systemctl enable --now`.

## Notes
- A failed health-check does **not** advance the deployed marker, so the next run retries
  the same commit rather than leaving you stuck on a bad restart.
- A dependency that needs an OS package (e.g. `python3-defusedxml`) is logged, not
  auto-installed — install it with `apt` once and the next deploy proceeds.
