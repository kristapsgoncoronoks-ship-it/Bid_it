# Deploy InvoiceIQ on a Hostinger VPS (KVM) — exact setup

A complete, copy-paste procedure to run the whole stack on **one Hostinger KVM
VPS** using `docker-compose.hostinger.yml` (Postgres + API + worker + nginx TLS
origin, document bytes on a local persisted volume — no separate object store).

> Requires a **Hostinger VPS (KVM)** with root SSH. This does **not** run on
> shared/Cloud (hPanel) web hosting — that has no Docker or long-running
> processes. If you're on shared hosting, upgrade to a VPS plan first.

---

## 0. Sizing

The stack is Postgres + API (uvicorn workers) + a worker + nginx. Pick:

| Hostinger plan | RAM | Set in `.env` |
|---|---|---|
| KVM 1 | 4 GB | `WEB_CONCURRENCY=2` **and add swap (below)** |
| KVM 2+ | 8 GB+ | `WEB_CONCURRENCY=4` (default) |

The frontend build (Vite) is memory-hungry; on 4 GB add swap **before** building:

```bash
fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
echo '/swapfile none swap sw 0 0' >> /etc/fstab
```

---

## 1. Create + access the VPS (hPanel → VPS)

1. In **hPanel → VPS**, provision the plan. For the OS template pick
   **Ubuntu 24.04 with Docker** (Docker pre-installed). A plain Ubuntu template
   works too — install Docker with `curl -fsSL https://get.docker.com | sh`.
2. Set the root password (or add your SSH key) in the VPS panel, then connect:
   ```bash
   ssh root@YOUR_VPS_IP
   docker --version && docker compose version   # confirm Docker + Compose v2
   ```

## 2. Firewall

Open **22, 80, 443**. Check BOTH the Hostinger-panel firewall (VPS → Firewall)
and the OS firewall:

```bash
ufw allow 22 && ufw allow 80 && ufw allow 443 && ufw enable
```

## 3. DNS

Point an **A record** for your app hostname at the VPS IP:

- **Domain registered at Hostinger:** hPanel → Domains → DNS Zone → add
  `A  app  →  YOUR_VPS_IP` (host `app` → `app.example.com`).
- **Using Cloudflare** (recommended for TLS, step 6A): add the A record in
  Cloudflare and set the record to **Proxied** (orange cloud).

Verify it resolves before requesting a certificate: `dig +short app.example.com`.

## 4. Get the code + configure

```bash
git clone https://github.com/kristapsgoncoronoks-ship-it/Bid_it.git
cd Bid_it
git checkout main     # production deploys track main; use a release tag if you cut one

cp .env.hostinger.example .env
nano .env
```

Fill in `.env`:

```ini
APP_ORIGIN=https://app.example.com          # your exact public origin
SECRET_KEY=<paste: openssl rand -hex 32>    # run the command, paste the output
POSTGRES_PASSWORD=<a strong password>
WEB_CONCURRENCY=4                            # 2 on a 4 GB VPS
```

Generate the secret with: `openssl rand -hex 32`.

## 5. Prepare the TLS cert directory

```bash
mkdir -p certs
```

nginx expects `certs/origin.pem` (full chain) + `certs/origin.key` (private key).
Fill it via **6A (Cloudflare)** or **6B (Let's Encrypt)**.

## 6A. TLS with Cloudflare (recommended — no renewal to manage)

1. Move the domain onto Cloudflare (free plan) and set SSL/TLS mode to
   **Full (Strict)**.
2. Cloudflare dashboard → **SSL/TLS → Origin Server → Create Certificate**
   (15-year origin cert). Save the two PEM blocks on the VPS:
   ```bash
   nano certs/origin.pem      # paste the "Origin Certificate"
   nano certs/origin.key      # paste the "Private Key"
   chmod 600 certs/origin.key
   ```
3. `nginx.prod.conf` already restores the real visitor IP from Cloudflare and
   redirects HTTP→HTTPS. Nothing else to do. Renewal: none for 15 years.

## 6B. TLS with Let's Encrypt (no Cloudflare)

Issue a free cert directly on the VPS with certbot (nginx must not be running on
:80 yet, so do this **before** `up`):

```bash
docker run --rm -p 80:80 \
  -v /etc/letsencrypt:/etc/letsencrypt \
  certbot/certbot certonly --standalone \
  -d app.example.com --agree-tos -m you@example.com --no-eff-email

# copy the issued cert into the names nginx expects:
cp /etc/letsencrypt/live/app.example.com/fullchain.pem certs/origin.pem
cp /etc/letsencrypt/live/app.example.com/privkey.pem   certs/origin.key
chmod 600 certs/origin.key
```

**Auto-renewal** (Let's Encrypt certs last 90 days) — add a cron that renews and
refreshes the copies, briefly stopping nginx so certbot can bind :80:

```bash
crontab -e
# add (runs 03:30 on the 1st of each month):
30 3 1 * * cd /root/Bid_it && docker compose -f docker-compose.hostinger.yml stop frontend && docker run --rm -p 80:80 -v /etc/letsencrypt:/etc/letsencrypt certbot/certbot renew --standalone --quiet && cp /etc/letsencrypt/live/app.example.com/fullchain.pem certs/origin.pem && cp /etc/letsencrypt/live/app.example.com/privkey.pem certs/origin.key && docker compose -f docker-compose.hostinger.yml start frontend
```

## 7. Launch

```bash
docker compose -f docker-compose.hostinger.yml up -d --build
```

The backend runs `alembic upgrade head`, then serves; nginx fronts TLS. First
build takes a few minutes. Watch it come up:

```bash
docker compose -f docker-compose.hostinger.yml ps
docker compose -f docker-compose.hostinger.yml logs -f backend
```

## 8. Create the first login

Either register a fresh workspace in the UI, or seed a demo tenant:

```bash
docker compose -f docker-compose.hostinger.yml exec backend python -m app.seed
# demo@invoiceiq.app / demo1234   — change/remove for real production
```

## 9. Verify

```bash
curl -fsS https://app.example.com/health          # {"status":"ok"} (process up)
curl -fsS https://app.example.com/health/ready     # ready (DB reachable)
```

Then open `https://app.example.com` and log in. API docs at `/docs`.

**Boot-time safety net:** in production the app refuses to start if `SECRET_KEY`
is the dev default, the database is SQLite, or `CORS_ORIGINS` is `*` — a
misconfigured deploy fails fast instead of running insecure.

---

## Operate

**Update to a new version — one command**
```bash
cd /root/Bid_it && ./scripts/vps-deploy.sh          # deploys the branch you're on
./scripts/vps-deploy.sh some-branch                  # or an explicit branch
```
The script refuses to proceed until its preflight passes (every `${VAR:?}` the
compose file requires is present in `.env`, ≥2 GB disk free) and BOTH backups
are taken **and verified** (dump ends with pg_dump's completion marker; the
document-volume tarball lists real entries). It then pulls, rebuilds, waits up
to 5 minutes for `/health` + `/health/ready`, and prints the rollback
artifacts if the app never comes up. The manual equivalent remains below and
in `docs/DEPLOY-RUNBOOK-2026-08-15.md`:

```bash
cd /root/Bid_it && git pull
docker compose -f docker-compose.hostinger.yml up -d --build   # migrations run automatically
```

**Backups (do this — on a schedule).** Two things hold state — the Postgres
volume and the document-bytes volume — and the `.env` is part of any restore
(`SECRET_KEY` derives the key that seals tenant SSO secrets; a database
restored under a different `SECRET_KEY` has unreadable sealed columns).
`scripts/backup.sh` takes all three with the same verification
`vps-deploy.sh` uses (the dump must end with pg_dump's completion marker),
prunes after 14 days, and copies off the box when `RCLONE_REMOTE` is set.
Install it once (audit 2026-09-05, OPS-004 — until then the only backups were
the pre-deploy ones, and the CI auto-deploy never took any):
```bash
(crontab -l 2>/dev/null; echo '17 2 * * * cd /root/Bid_it && ./scripts/backup.sh >> /root/backup.log 2>&1') | crontab -
ls -lh ~/backups          # the next morning: db-*.sql.gz, docs-*.tar.gz, env-*
```
The manual equivalents, for a one-off:
```bash
# database → a gzipped SQL dump
docker compose -f docker-compose.hostinger.yml exec -T db \
  pg_dump -U invoiceiq invoiceiq | gzip > backup-db-$(date +%F).sql.gz

# document bytes (uploaded originals, receipts, logos)
docker run --rm -v invoiceiq_storagedata:/data -v "$PWD":/out alpine \
  tar czf /out/backup-docs-$(date +%F).tar.gz -C /data .
```
Copy these off the VPS (Hostinger snapshots are a coarser fallback). After any
restore, prove integrity: log in as an admin and
`POST /api/v1/integrity/documents/verify` — it re-hashes every stored object
(receipts, logos, email attachments, **and the original supplier-invoice
uploads**) against the database and reports anything missing or corrupted.

**Logs / restart**
```bash
docker compose -f docker-compose.hostinger.yml logs -f            # all services
docker compose -f docker-compose.hostinger.yml restart backend    # one service
```

---

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `backend` restarts, logs show *"Insecure production configuration"* | `SECRET_KEY` still the dev default, or `CORS_ORIGINS`/`APP_ORIGIN` is `*`/empty. Set real values in `.env`, then `up -d`. |
| Build killed / OOM during frontend build | Not enough RAM. Add swap (§0) and lower `WEB_CONCURRENCY`. |
| `502 Bad Gateway` at the domain | Backend not healthy yet — `docker compose ... logs backend`. It waits for Postgres; give it ~30 s on first boot. |
| Browser TLS error | `certs/origin.pem` / `origin.key` missing or wrong domain. Re-do §6; `origin.pem` must be the **full chain**. |
| Uploads succeed but extraction says *"stored upload missing"* | The `storagedata` volume isn't shared with the worker. Use the provided compose unchanged (both `backend` and `worker` mount it). |
| Site works on the IP but not the domain | DNS not propagated, or the Hostinger-panel firewall still blocks 80/443. Recheck §2–§3. |

---

## Notes / when to grow beyond one VPS

- This single-node stack keeps document bytes on a **local** disk volume — simple
  and fine for one box. To run **multiple** app replicas (horizontal scale) you
  must move bytes to shared object storage (`STORAGE_BACKEND=s3`, AWS S3 / MinIO)
  and Postgres to a managed HA instance — see `docs/DEPLOYMENT.md` and
  `deploy/k8s/` for that path.
- Postgres here runs in a container with a local volume. For stronger durability,
  point `DATABASE_URL` at a managed Postgres with automated backups + PITR.

---

## The "few clicks" ladder — how deploys get easier from here

Where deployment effort actually goes, in order of payoff:

1. **Today (shipped):** `./scripts/vps-deploy.sh` — one command on the VPS
   does preflight → verified backups → pull → build → health-gate. No step
   can be silently skipped or half-done.
2. **One click (blocked only by GitHub Actions billing):** everything for the
   CI-gated auto-deploy below already exists in the repo — the `deploy` job,
   the restricted single-command SSH key design, the `DEPLOY_ENABLED` switch.
   The account's Actions billing has been dead since 2026-08-12; once it is
   restored and the secrets are set, deploying = merging to `main`. That IS
   the few-clicks deploy, and no new code is needed for it.
3. **Faster + smaller (next code change, only worth it after #2 lives):**
   have CI build the images and push them to GHCR, and change the VPS update
   to `docker compose pull && up -d` — the 4 GB box stops compiling
   the frontend entirely (today's slowest, most OOM-prone step) and an update
   drops from minutes to seconds. Requires adding a CI job (and bumping the
   README's job count, which is machine-checked).
4. **Not the path:** click-to-deploy PaaS (Vercel/Netlify-style) doesn't fit —
   the stack needs Postgres, a worker, and a persistent document volume on
   one machine; the VPS + compose shape is already the simple version.

## Automated CI-gated deploy

The CI workflow has a `deploy` job that runs **only after all six checks pass**,
**only** on a push to the production branch, and **only** once you opt in with the
repo variable `DEPLOY_ENABLED=true`. Until then it is skipped (CI stays green).
It SSHes to this VPS and runs a fixed deploy script; the key is restricted to that
one command on the server side, so a leaked key can't run arbitrary root commands.

### 1. On the VPS — deploy script + a restricted key

```bash
# The exact commands a deploy runs. The forced command hands off to
# scripts/vps-deploy.sh, so the automatic path gets the SAME preflight,
# verified pre-deploy backups, rollback stamp and 5-minute health gate as a
# manual deploy. (Audit 2026-09-05, OPS-001: an earlier version of this file
# ran `up -d --build && docker image prune -f` directly — no backup, no health
# check, and the previous image pruned, so nothing to roll back to. If your
# VPS still carries that version, replace it with the one below; the file
# lives on the host, so a repo change alone does not update it.)
cat > /root/deploy.sh <<'SH'
#!/bin/bash
set -euo pipefail
cd /root/Bid_it
git fetch origin main
git reset --hard origin/main      # picks up a changed vps-deploy.sh itself
exec ./scripts/vps-deploy.sh main
SH
chmod +x /root/deploy.sh

# A dedicated key pair GitHub Actions will use to reach this box.
ssh-keygen -t ed25519 -f /root/.ssh/ci_deploy -N "" -C "github-actions-deploy"

# Authorise the PUBLIC key, but FORCE it to only ever run deploy.sh — no shell,
# no port/agent forwarding. Even if the private key leaks, this is all it can do.
printf 'command="/root/deploy.sh",no-port-forwarding,no-agent-forwarding,no-X11-forwarding,no-pty %s\n' \
  "$(cat /root/.ssh/ci_deploy.pub)" >> /root/.ssh/authorized_keys

# Print the PRIVATE key to paste into the GitHub secret (next step), then you can
# clear your screen. It never needs to leave your control.
echo "----- copy everything below into the DEPLOY_SSH_KEY secret -----"
cat /root/.ssh/ci_deploy
echo "----- end -----"
```

### 2. In GitHub — secrets + the enable switch

Repo → **Settings → Secrets and variables → Actions**:

- **Secrets** (New repository secret):
  - `DEPLOY_SSH_KEY` — the private key printed above (the whole block).
  - `DEPLOY_HOST` — `YOUR_VPS_HOST`
  - `DEPLOY_USER` — `root`
- **Variables** (the Variables tab → New repository variable):
  - `DEPLOY_ENABLED` — `true`  ← this is the master switch; set it last.

### 3. Done

The next green push to `main` deploys itself within ~a minute of CI passing. To
pause auto-deploy, set `DEPLOY_ENABLED` to `false` (or delete it) — the job goes
back to being skipped. A failing build never deploys.
