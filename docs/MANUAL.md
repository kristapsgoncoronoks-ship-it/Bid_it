# Manual — install, run, use, reference

The complete operator and integrator reference: installation, hosting, deployment sizing, scaling, git setup, reliability, the day-to-day user manual, the external API, SAF-T export, the advisory AI review, and the EU VAT-refund rules reference.

## Consolidated documentation — contents

- [INSTALL — Setup & Installation](#install-setup-installation)
- [Hosting on Hostinger — step by step](#hosting-on-hostinger-step-by-step)
- [Deployment sizing — what server to run](#deployment-sizing-what-server-to-run)
- [Scaling the Fleet Fuel & VAT Refund System](#scaling-the-fleet-fuel-vat-refund-system)
- [Putting this project under git](#putting-this-project-under-git)
- [Process reliability — stuck/stall risks & hardening](#process-reliability-stuckstall-risks-hardening)
- [USER MANUAL — Fleet Fuel & VAT Refund System](#user-manual-fleet-fuel-vat-refund-system)
- [External API — `/api/v1` (token contract)](#external-api-apiv1-token-contract) — short overview; the full reference is the integrator manual below
- [Integrator manual — `/api/v1` (external token API)](#integrator-manual-apiv1-external-token-api) — the authoritative API reference
- [SAF-T export — programmable OECD-core generator (prework)](#saf-t-export-programmable-oecd-core-generator-prework)
- [AI review assistant — advisory validation & analytics](#ai-review-assistant-advisory-validation-analytics)
- [EU cross-border VAT refund — rules reference (Directive 2008/9/EC)](#eu-cross-border-vat-refund-rules-reference-directive-20089ec)

---

## INSTALL — Setup & Installation

---

### EASIEST INSTALL — no command line, about 2 minutes

Works on Windows, macOS and Linux. Only requirement: Python 3.10+
(Windows/macOS: get it from https://python.org — on Windows tick "Add to PATH").

1. **Unzip** `fleet_fuel_system.zip` anywhere (Documents, Desktop, /opt — anywhere).
2. **Double-click the start file:**
   - Windows: **`start.bat`**
   - macOS: **`start.command`**  (first time: right-click → Open, to allow it)
   - Linux: **`./start.sh`**
   It installs anything missing and opens your browser automatically.
3. **Finish in the browser.** A friendly setup page appears — pick an admin
   **username and password**, leave the HTTPS box ticked, click **Create account &
   finish**. That's it. You land on the sign-in page; log in and start using it.
   (A self-signed certificate triggers one browser trust prompt — choose
   "Advanced → Proceed". Replace it with a company certificate any time — Part W3 /
   PART 3.)

No terminal, no config files. To add colleagues later: sign in → Admin → create
users with role **processor** (and, in the Admin panel, untick any capabilities they
shouldn't have). Daily guide: #user-manual-fleet-fuel-vat-refund-system.

#### Prefer a guided terminal installer?
Run **`install.bat`** (Windows) or **`./install.sh`** (macOS/Linux) instead — same
result via a step-by-step console wizard (checks Python, installs packages, creates
the certificate and admin account, hardens files, self-checks).

The one-click launchers are thin wrappers around **`python start.py`** — run that
directly if you prefer (it installs missing deps, opens the browser and starts the dev
server). The browser opens at **`https://127.0.0.1:8050`** (or `http://…` if no
certificate is configured yet), where the first-run **setup wizard** creates the admin.

---

The rest of this document is the **production path** for IT: dedicated server,
permanent service, team access, commercial certificates and automated backups.

---

## PRODUCTION SETUP (server / team)

Primary path: **Ubuntu Server 22.04/24.04 LTS**. A small VM runs the baseline
(2 vCPU, 2–4 GB RAM, 20 GB disk); for a real install **4 vCPU / 8 GB / 150 GB NVMe**
is the comfortable sweet spot — full sizing (and when local OCR needs more) is in
**[#deployment-sizing-what-server-to-run](#deployment-sizing-what-server-to-run)**. Windows alternative at the end.
Estimated time: 30–45 minutes, +30 minutes for team access.

---

### PART 1 — Operating system preparation (Ubuntu)

```bash
# 1.1 Update the OS
sudo apt update && sudo apt upgrade -y

# 1.2 Install Python 3.12+, tools and OpenSSL
sudo apt install -y python3 python3-pip python3-venv unzip openssl curl
python3 --version          # must show 3.12 or newer

# 1.3 Create a dedicated service user (never run as root)
sudo adduser --system --group --home /opt/fleetfuel fleetfuel
```

### PART 2 — Install the software

```bash
# 2.1 Copy fleet_fuel_system.zip to the server (from your PC):
#     scp fleet_fuel_system.zip youruser@SERVER:/tmp/

# 2.2 Unpack into the service user's home
sudo unzip /tmp/fleet_fuel_system.zip -d /opt/fleetfuel/
sudo mv /opt/fleetfuel/fleet_fuel_system /opt/fleetfuel/app
sudo chown -R fleetfuel:fleetfuel /opt/fleetfuel/app

# 2.3 Python dependencies (in a virtual environment)
sudo -u fleetfuel python3 -m venv /opt/fleetfuel/venv
sudo -u fleetfuel /opt/fleetfuel/venv/bin/pip install -r requirements.txt
#    (installs flask, openpyxl, requests, cryptography, waitress, pypdf)
#    For scale-out on Linux you can also add gunicorn (see Part 6b):
#    sudo -u fleetfuel /opt/fleetfuel/venv/bin/pip install gunicorn

# 2.4 Restrictive permissions on data files
cd /opt/fleetfuel/app
sudo -u fleetfuel /opt/fleetfuel/venv/bin/python backup.py --harden
```

### PART 3 — TLS certificate (pick ONE)

```bash
cd /opt/fleetfuel/app

# A) Self-signed (internal use; browsers warn once - accept):
sudo -u fleetfuel /opt/fleetfuel/venv/bin/python make_cert.py fuel.yourcompany.local

# B) Corporate / commercial certificate (PEM + intermediate chain):
#    place files on the server, then set env vars in the service (Part 5):
#    TLS_CERT=/etc/ssl/fuel.crt  TLS_KEY=/etc/ssl/fuel.key  TLS_CHAIN=/etc/ssl/ca_bundle.crt

# C) Commercial PKCS#12 (.pfx):
#    TLS_PFX=/etc/ssl/fuel.pfx  TLS_PFX_PASSWORD='...'

# D) Let's Encrypt (public DNS name required):
#    sudo apt install -y certbot && sudo certbot certonly --standalone -d fuel.example.com
#    TLS_CERT=/etc/letsencrypt/live/fuel.example.com/fullchain.pem
#    TLS_KEY=/etc/letsencrypt/live/fuel.example.com/privkey.pem

# Verify whichever you chose:
sudo -u fleetfuel /opt/fleetfuel/venv/bin/python tls.py
# -> "TLS: OK - ... | expires ..."
```

### PART 4 — First run & users

There is **no built-in default password**. The very first time the app is reached it
shows a one-time **setup page** that creates *your* admin account — so nothing ships
with a known login. Create the admin in whichever way suits the server:

```bash
cd /opt/fleetfuel/app

# 4.1 Create the admin account. Two equivalent ways — pick one:
#   (a) Browser: do a test start (4.2) and open https://127.0.0.1:8050 — the setup
#       page lets you choose the admin username + password. Recommended.
#   (b) Headless: create it from the terminal before starting the service:
sudo -u fleetfuel /opt/fleetfuel/venv/bin/python auth.py add kristaps   # prompts for password
sudo -u fleetfuel /opt/fleetfuel/venv/bin/python -c \
    "import auth; auth.set_role('kristaps','admin')"

# 4.2 Test start (foreground). app.py terminates TLS itself, so this proves the
#     certificate end-to-end before you wrap it in the service/proxy:
sudo -u fleetfuel /opt/fleetfuel/venv/bin/python app.py
#    -> " * TLS enabled -> https://127.0.0.1:8050"  + certificate details
#    From the server: curl -k https://127.0.0.1:8050/login   (expect HTTP 200)
#    Stop with Ctrl+C, then continue to Part 5 to run it permanently (serve.py).

# 4.3 Create colleagues later in the web Admin panel (role 'processor'; an admin can
#     narrow a processor's capabilities there).
```

### PART 5 — Run as a permanent service (systemd)

```bash
sudo tee /etc/systemd/system/fleetfuel.service > /dev/null << 'EOF'
[Unit]
Description=Fleet Fuel & VAT Refund System
After=network.target

[Service]
User=fleetfuel
Group=fleetfuel
WorkingDirectory=/opt/fleetfuel/app
# --- environment (uncomment / fill what you use) ---
# Certificate (option B/C/D from Part 3):
#Environment=TLS_CERT=/etc/ssl/fuel.crt
#Environment=TLS_KEY=/etc/ssl/fuel.key
#Environment=TLS_CHAIN=/etc/ssl/ca_bundle.crt
# Document vault backend (optional, see Part 7) — pick ONE:
#  SharePoint (Microsoft 365):
#Environment=DOC_BACKEND=sharepoint
#Environment=SP_TENANT_ID=...
#Environment=SP_CLIENT_ID=...
#Environment=SP_CLIENT_SECRET=...
#Environment=SP_DRIVE_ID=...
#Environment=SP_FOLDER=FuelVAT/Invoices
#  FTP / FTPS file archive:
#Environment=DOC_BACKEND=ftp
#Environment=FTP_HOST=archive.example.com
#Environment=FTP_USER=...
#Environment=FTP_PASSWORD=...
#Environment=FTP_DIR=fuelvault/invoices
#Environment=FTP_TLS=1          # 1 = FTPS (encrypted, default); 0 = plain FTP (LAN only)
# Background intake worker (waiting room) — on by default; set 0 to disable on this
# process (e.g. when a separate `python waiting_room.py --work` process drains it):
#Environment=INTAKE_WORKER=1
# Worker tier (Part 5b): web|worker|all. Set web on web nodes when a separate worker
# process drains the queue (FFS_ROLE=web == INTAKE_WORKER=0 for the in-process worker):
#Environment=FFS_ROLE=all
# Credential custody for automated capture (Part 8b) — envelope encryption via KMS/BYOK:
#Environment=FFS_KEK_KEY=<base64 32-byte master key>     # with keyvault_provider=env
# Off-site backup copy (Part 8):
#Environment=FFS_BACKUP_SYNC_DIR=/mnt/backup-nas/ffs
# PostgreSQL cutover (Part 7c) — validate in staging first (db.py HONEST STATUS):
#Environment=DB_ENGINE=postgres
#Environment=DB_DSN=postgresql://user:pass@host/dbname
# Supplier APIs (optional):
#Environment=DKV_API_TOKEN=...
ExecStart=/opt/fleetfuel/venv/bin/python serve.py
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now fleetfuel
sudo systemctl status fleetfuel          # active (running)
journalctl -u fleetfuel -n 20            # shows "waitress on ..." (+ certificate line if TLS)
```

`serve.py` runs the production server (waitress) and starts the background workers
(auto‑backup scheduler + intake‑queue drainer). Single user on the server itself: you
are DONE — browse to `https://127.0.0.1:8050`. For team access continue with Part 6;
for several worker processes see Part 6b; for a **dedicated worker tier** (recommended
once you add automated document capture) see Part 5b.

### PART 5b — Dedicated worker tier (recommended for scale / automated capture)

By default `serve.py` drains the intake queue **in‑process**. As you grow — and
especially once you turn on automated document capture (Part 7b) — move that work to a
**separate worker process** so a burst of heavy extraction / portal fetches never
competes with web requests.

The mechanism is one queue (`intake.db`) plus the `FFS_ROLE` env var:

- **`FFS_ROLE=web`** on each web node → it serves pages but does **not** drain the queue.
  (Legacy equivalent: `INTAKE_WORKER=0`, which still forces the in‑process worker off.)
- **`FFS_ROLE=worker`** (or `all`, the default) on the worker node → it drains the queue.
- Run the dedicated drainer as its own service: **`python waiting_room.py --work`**
  (drains forever; `--once` processes the backlog once, `--status` prints queue counts).

The worker dispatches the job **kinds** `extract` (PDF/AI extraction), `register`
(statement registration), `close` (the one‑click monthly close — `engine_close.close`),
and `fetch` (portal/API document pulls). Fetches go through a **per‑supplier
rate‑limiter** (concurrency caps / backoff) so no portal is hammered. The auto‑backup
and e‑mail‑digest schedulers are **leader‑elected** across processes, so they run once
no matter how many web/worker nodes you run.

```bash
# Web node(s): serve pages only, do NOT drain the queue.
#   add to the fleetfuel.service [Service] block:  Environment=FFS_ROLE=web

# Worker node: a second systemd unit that only drains the queue.
sudo tee /etc/systemd/system/fleetfuel-worker.service > /dev/null << 'EOF'
[Unit]
Description=Fleet Fuel intake worker
After=network.target

[Service]
User=fleetfuel
Group=fleetfuel
WorkingDirectory=/opt/fleetfuel/app
Environment=FFS_ROLE=worker
# (mirror the same TLS/DOC_BACKEND/credential env as the web unit if the worker needs it)
ExecStart=/opt/fleetfuel/venv/bin/python waiting_room.py --work
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload
sudo systemctl enable --now fleetfuel-worker
```

Single‑box installs need none of this — the default in‑process worker is fine.

### PART 6 — Team access: nginx reverse proxy + firewall

The app deliberately listens on 127.0.0.1 only. Expose it through nginx:

```bash
sudo apt install -y nginx
sudo tee /etc/nginx/sites-available/fleetfuel > /dev/null << 'EOF'
server {
    listen 443 ssl;
    server_name fuel.yourcompany.local;           # your DNS name
    ssl_certificate     /etc/ssl/fuel.crt;        # proxy's own cert (or LE)
    ssl_certificate_key /etc/ssl/fuel.key;
    client_max_body_size 25m;                     # invoice PDF uploads
    location / {
        proxy_pass https://127.0.0.1:8050;        # app also runs TLS (or http:// if not)
        proxy_ssl_verify off;                     # app cert is internal/self-signed
        proxy_set_header X-Forwarded-For $remote_addr;
        proxy_set_header Host $host;
    }
}
server { listen 80; server_name fuel.yourcompany.local; return 301 https://$host$request_uri; }
EOF
sudo ln -s /etc/nginx/sites-available/fleetfuel /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx

# Firewall: only SSH + HTTPS reachable from the network
sudo apt install -y ufw
sudo ufw allow OpenSSH
sudo ufw allow 443/tcp
sudo ufw enable
sudo ufw status
```

### PART 6a — Putting the app behind Cloudflare

Optional. Cloudflare's proxy (orange-cloud DNS) puts a WAF, rate-limiting, Bot-Fight and
(optionally) Turnstile in front of the origin — complementary to the app's own per-user /
per-IP login lockout. Both Cloudflare-related app settings are **default OFF**: with them
off the app behaves byte-identically to today. Turn them on in **Admin → Access & Security
→ Cloudflare edge safety** once DNS is proxied.

**1. Proxy the DNS record.** In the Cloudflare dashboard, set the A/AAAA record for the
hostname to **Proxied** (orange cloud). Traffic now flows browser → Cloudflare → origin.

**2. SSL/TLS mode = Full (Strict).** Set the zone's SSL/TLS encryption mode to **Full
(Strict)** and present a **valid origin certificate** (a Cloudflare Origin CA cert, or your
existing CA cert) on the origin (nginx or the app's own TLS). Full (Strict) keeps the
Cloudflare↔origin hop encrypted *and* verified — do not use Flexible.

**3. Turn ON `trust_cloudflare` (trust the real client IP).** Once proxied, the app's
immediate socket peer (`request.remote_addr`) is a **Cloudflare edge IP**, not the visitor.
Without this setting the per-IP brute-force throttle (`auth.is_locked_ip`) and every
audit/login-log IP would record Cloudflare instead of the attacker — the defense silently
stops working. With it ON, the app resolves the real client from Cloudflare's
`CF-Connecting-IP` header. **Precedence:** the header is trusted **only** when the request
peer is genuinely Cloudflare (a published CF range), a configured local proxy, or loopback
— so a forged `CF-Connecting-IP` from a direct hit on the origin is always ignored.

**4. Turn ON `cloudflare_only` (origin lock) — or firewall the origin to the CF ranges.**
This refuses any request whose peer is not Cloudflare / a trusted proxy / loopback with a
plain `403`, so nobody can bypass the Cloudflare edge and hit the origin directly. This is
also what makes `CF-Connecting-IP` unspoofable (the only way to reach the app is through
Cloudflare). Loopback is **always** allowed (health checks, the worker tier, local curl), so
you can't lock yourself out of the box. Equivalent at the network layer: firewall the origin
(ufw / security group) to allow inbound only from the published Cloudflare ranges.
**Caution:** only enable this after DNS is actually proxied, or legitimate traffic that
arrives directly will be refused.

**Embedded IP ranges & refresh.** The published Cloudflare ranges
(https://www.cloudflare.com/ips-v4, /ips-v6) are **embedded** in `cloudflare.py` (they
change only rarely). If they ever change, you can override them at runtime — no code change
— via the **Cloudflare IP ranges override** field (newline/comma-separated CIDRs); a
non-empty override **replaces** the built-in list.

**Reverse-proxy topology (nginx in front of waitress).** If a **local** reverse proxy
(nginx) sits between Cloudflare and the app (PART 6), the app's peer for these checks is the
nginx box, which is normally **loopback** (`127.0.0.1`) and therefore always trusted — so the
default works. If nginx runs on a *different* host on the LAN, add that host's IP/CIDR to the
**Local trusted proxy CIDRs** field, otherwise the origin lock will 403 it and the trusted-IP
resolver won't trust the forwarded header. The app keeps its existing `ProxyFix(x_proto,
x_host)` for HTTPS-scheme detection (secure cookies / HSTS) — that is unchanged and still
needed.

**Cloudflare-side hardening (dashboard).** Configure Cloudflare's **WAF**, **Rate Limiting**,
**Bot Fight Mode**, and optionally **Turnstile** on the login route in the Cloudflare
dashboard. These run at the edge and are complementary to — not a replacement for — the app's
own account/IP lockout, CSRF, and audit logging.

### PART 6b — Multiple worker processes (scale-out)

The system is built to run as several worker processes over the same `.db` files
(SQLite is tuned with WAL + busy_timeout, and background singletons coordinate via a
cross‑process lock, so backups never duplicate and the intake queue hands each job to
exactly one worker). For heavier load on Linux, run it under gunicorn with the provided
config (it starts the background workers in each worker process):

```bash
/opt/fleetfuel/venv/bin/pip install gunicorn
# systemd ExecStart:
ExecStart=/opt/fleetfuel/venv/bin/gunicorn -c /opt/fleetfuel/app/gunicorn_conf.py app:app
# tune workers/bind via env: WORKERS, THREADS, BIND   (default workers = 2*CPU + 1)
```

On Windows (no gunicorn/fork), run several `python serve.py` instances behind the
proxy instead — the same locks and tuning apply. For very high concurrent **write**
volume, migrate to PostgreSQL (`db.py` is the seam; see its header).

### PART 7 — Document vault backend (optional)

By default documents are stored locally under `documents/`. To use a network archive,
set `DOC_BACKEND` in the systemd unit and restart. Files are filed under the same
logical tree (`<Customer> <RegNo>/<Year>/<Country>/<Claim period>/<file>`) on every
backend.

**SharePoint (Microsoft 365)** — one‑time, with your M365 admin (full details in the
`document_vault.py` header):
1. Entra ID → App registrations → New ("Fleet Fuel Vault") → client secret.
2. Graph **application** permission `Sites.Selected` → admin consent → grant the app
   write access to the one target site.
3. Find IDs once: `GET /sites/{hostname}:/sites/{SiteName}` → `GET /sites/{id}/drives`.
4. Fill the `SP_*` + `DOC_BACKEND=sharepoint` lines in the systemd unit, then:
   `sudo systemctl restart fleetfuel`.
5. Move existing local documents:
   `sudo -u fleetfuel /opt/fleetfuel/venv/bin/python -c "import vat_refund, document_vault; con=vat_refund.connect(); print(document_vault.migrate_local_to_sharepoint(con, vat_refund.DOCDIR), 'documents migrated')"`

**FTP / FTPS file archive** — set `DOC_BACKEND=ftp` plus `FTP_HOST`, `FTP_USER`,
`FTP_PASSWORD`, `FTP_DIR`, and keep `FTP_TLS=1` (FTPS, encrypted) unless on a trusted
private LAN. No extra dependency (stdlib `ftplib`). Migrate existing local files:
`... -c "import vat_refund, document_vault; con=vat_refund.connect(); print(document_vault.migrate_local_to_ftp(con, vat_refund.DOCDIR), 'documents migrated')"`

### PART 7b — Automated portal capture (optional)

To pull supplier prices/documents automatically from a customer portal:

1. In the web UI (**Pricing → Client portal price scraping**, on `/pricing/portal`), add
   a portal (a no‑code JSON/CSV config, or a custom adapter) and **store the entity's
   login** — credentials are encrypted at rest (harden the key with Part 8b).
2. **Arm scheduled pulls** by enabling the **`scrape_scheduler_enabled`** admin setting
   (`1` to arm, `0` off — the default) plus the per‑portal interval. Pulls then run on the
   worker tier (Part 5b), through the per‑supplier rate‑limiter; **Scrape now** triggers an
   immediate pull. By default no scheduled pulls run.
3. Use **only portals you are authorised to access**. On a locked‑down box, run the
   scraper from a connected machine.

(API‑based ingestion where a supplier offers it, e.g. `DKV_API_TOKEN`, is configured in
the service env — Part 5.) See the strategy/backlog docs for the capture roadmap.

### PART 7e — Scanned-PDF OCR fallback (optional, on-prem)

Extraction is **structured‑first** and needs no extra software: UBL/CII e‑invoices and
Factur‑X/ZUGFeRD hybrid PDFs parse deterministically, the per‑supplier parser handles
known layouts, and only an unstructured PDF falls to the configured AI backend. A
**scanned / image‑only** PDF, however, carries no text — to recover those **on‑prem**
(no bytes leave the host), enable the OCR fallback:

```bash
# system packages: the Tesseract engine + Poppler (PDF→image rasteriser)
sudo apt install -y tesseract-ocr poppler-utils
# python bindings
sudo -u fleetfuel /opt/fleetfuel/venv/bin/pip install pytesseract pdf2image
```

The backend is selected by **`EXTRACT_OCR_BACKEND`** (`auto` = use Tesseract if installed,
else silently skip — the default; `tesseract` = force; `none` = disable). When OCR is used,
the draft is flagged (`ocr=True`, noted, capped below “high” confidence) so the reviewer
scrutinises it. OCR is CPU/RAM‑spiky per page — see
**[#deployment-sizing-what-server-to-run](#deployment-sizing-what-server-to-run)** for the larger box it wants.

### PART 7c — PostgreSQL (optional, scale-out)

SQLite (the default) is fine from a laptop to a busy single server. For very high
concurrent **write** volume, move to PostgreSQL via the `db.py` dialect shim:

1. Provision Postgres and create the database.
2. Set the engine + DSN in the service env (read the **HONEST STATUS** note in the
   `db.py` header first — validate in staging before flipping it in production):
   ```bash
   Environment=DB_ENGINE=postgres
   Environment=DB_DSN=postgresql://user:pass@host/dbname
   # (DB_ENGINE selects the dialect: sqlite [default] | postgres; DB_DSN is the DSN)
   ```
3. Back up with **`pg_dump`** instead of the file‑snapshot path for the migrated DBs.

Full migration steps and the maturity caveats are in **[#scaling-the-fleet-fuel-vat-refund-system](#scaling-the-fleet-fuel-vat-refund-system)**.

### PART 7d — Multi-tenant (optional, do NOT flip casually)

The platform ships **single‑tenant** — the `multitenant` admin setting is **OFF** and
nothing is tenant‑scoped. Turning it on is a **P1+ rollout** (tenant‑scoped enforcement at
every query, per‑tenant credential keys via `FFS_KEK_KEY_<TENANT>`, isolation tests) and a
cross‑tenant leak is a GDPR breach — **do not enable it before completing that work**. The
read‑only `/admin/tenants` page shows the current (single‑tenant) state. See
**[STRATEGY.md#multi-tenancy-program-plan](STRATEGY.md#multi-tenancy-program-plan)** before changing it.

### PART 8 — Automatic backups

Snapshots include every database — `customers.db`, `suppliers.db`, `fuel_history.db`,
the isolated **`vat_claims.db`** (the legal/financial claim records — the most important
to protect), `security.db` — plus the `documents/` vault and the `data_lake/` (each
entry SHA‑256'd, with `verify()`). The first run also migrates legacy claim tables out
of `fuel_history.db` into `vat_claims.db`.

```bash
# Nightly snapshot at 02:30 (rotation keeps 14 inside backups/)
sudo tee /etc/cron.d/fleetfuel-backup > /dev/null << 'EOF'
30 2 * * * fleetfuel /opt/fleetfuel/venv/bin/python /opt/fleetfuel/app/backup.py >> /opt/fleetfuel/backup.log 2>&1
EOF

# Off-machine copy — two ways (use either):
#  (a) Built-in off-site sync: point the app at a mounted NAS / synced cloud folder and
#      every snapshot is ALSO copied there automatically. Set it either way:
#        - env var (systemd):   Environment=FFS_BACKUP_SYNC_DIR=/mnt/backup-nas/ffs
#        - or Admin panel:      set "backup sync folder" (the backup_sync_dir setting)
#      (FFS_BACKUP_SYNC_DIR takes precedence over the admin setting.)
#  (b) Your own sync of backups/ to versioned storage. Examples:
#        rclone (OneDrive/SharePoint):  rclone sync /opt/fleetfuel/app/backups remote:FleetFuelBackups
#        rsync to another server:       rsync -a /opt/fleetfuel/app/backups/ backup-host:/srv/ffs/
#      Add the sync command as a second cron line 15 minutes later.

# Quarterly restore drill (two commands):
LATEST=$(ls /opt/fleetfuel/app/backups/ffs_*.zip | tail -1)
sudo -u fleetfuel /opt/fleetfuel/venv/bin/python /opt/fleetfuel/app/backup.py --verify "$LATEST"
sudo -u fleetfuel /opt/fleetfuel/venv/bin/python /opt/fleetfuel/app/backup.py --restore "$LATEST" --to /tmp/drill
```

Also enable **disk encryption** on the volume (LUKS at install time, or BitLocker /
FileVault if hosting on Windows/macOS) — see SECURITY.md.

### PART 8a — Post-install configuration checklist (in the Admin panel)

Sign in as the admin and work down this list once. All of these are configured in the
**web Admin panel** (no files to edit):

1. **Users & roles** — create each colleague as **processor**; untick any capabilities
   they shouldn't have (data import, invoice control, pricing, documents, exports). The
   whole VAT‑refund module (claims, readiness, recovery, customers/CRM) is **admin‑only**
   regardless.
2. **Modules** — switch whole parts of the app on/off (analytics, intake, compliance,
   VAT refunds, FX). A part that's off disappears from the menu.
3. **E‑mail alerts (SMTP relay)** — set `smtp_host` / `smtp_port` / `smtp_user` /
   `smtp_pass`, a *From* address (`smtp_from`), the **recipients** (`notify_recipients`)
   and the **digest cadence** (`notify_interval_hours`). The system then e‑mails the
   action digest on that cadence and sends per‑event critical alerts (VAT deadlines,
   stuck/dead‑letter intake jobs). Until SMTP is set, alerts surface in‑app only.
4. **Backups** — set the snapshot interval (`backup_interval_hours`) and the **off‑site
   sync folder** (`backup_sync_dir`, or the `FFS_BACKUP_SYNC_DIR` env var — Part 8).
5. **AI review backend** — the advisory AI review assistant is **OFF by default**; only
   turn it on deliberately. It sends derived data only (never the PDF/IBAN/secret) and
   never mutates or gates a figure (see [#ai-review-assistant-advisory-validation-analytics](#ai-review-assistant-advisory-validation-analytics)).
6. **API keys** (optional) — issue scoped machine keys only if an external system needs
   the `/api/v1` API; it is default‑off (see [#external-api-apiv1-token-contract](#external-api-apiv1-token-contract)).

### PART 8b — Credential custody for automated capture (KMS / BYOK)

If you store **portal credentials** for automated document capture (Part 7b), protect
them with **envelope encryption** instead of the default local key:

1. Set the **`keyvault_provider`** admin setting to **`env`** (the default is `local`,
   which derives the key from the server's secret file).
2. Inject a **base64‑encoded 32‑byte master key** as **`FFS_KEK_KEY`** from your secret
   manager / KMS into the service environment (never commit it). Generate one with:
   ```bash
   python3 -c "import os,base64; print(base64.b64encode(os.urandom(32)).decode())"
   ```
   then in the systemd unit: `Environment=FFS_KEK_KEY=<that base64 value>`.
3. **BYOK / per‑tenant keys (optional):** set a per‑tenant key as
   **`FFS_KEK_KEY_<TENANT>`** (upper‑cased tenant id). The provider picks the per‑tenant
   key first and falls back to the global `FFS_KEK_KEY` — so one tenant's key cannot
   decrypt another's secrets.
4. **Rotation caveat:** switching `keyvault_provider` (or rotating the KEK) on an
   **already‑populated** credential store is a **migration** — the stored secrets must be
   **re‑wrapped** under the new key, not just re‑pointed. Do this deliberately; see the
   `keyvault.py` header and [#scaling-the-fleet-fuel-vat-refund-system](#scaling-the-fleet-fuel-vat-refund-system).

The master key is never written to disk by the app — your KMS/secret manager owns it.

### PART 9 — Verification checklist

```bash
curl -k https://127.0.0.1:8050/login -o /dev/null -w "%{http_code}\n"   # 200
sudo -u fleetfuel /opt/fleetfuel/venv/bin/python tls.py                  # TLS: OK
sudo -u fleetfuel /opt/fleetfuel/venv/bin/python auth.py                 # users + logins
ls -la /opt/fleetfuel/app/*.db                                           # -rw------- fleetfuel
sudo systemctl is-active fleetfuel nginx                                 # active active
```
Then in a browser: log in → Admin panel shows "TLS certificate: present", create one
test processor account with the data‑import capability unticked, and confirm it cannot
save in Data manager (403).

### PART 10 — Monthly operation (after setup)

```bash
cd /opt/fleetfuel/app
# 1. drop supplier files, edit month_config.py (period, FILES, FX)
sudo -u fleetfuel /opt/fleetfuel/venv/bin/python consolidate.py      # must PASS all
sudo -u fleetfuel /opt/fleetfuel/venv/bin/python build_master.py
sudo -u fleetfuel /opt/fleetfuel/venv/bin/python history.py
sudo -u fleetfuel /opt/fleetfuel/venv/bin/python invoice_control.py 2026-06
sudo -u fleetfuel /opt/fleetfuel/venv/bin/python backup.py
# 2. web UI: register statements, chase MISSING, attach documents
# Quarterly: python3 vat_refund.py 2026  -> file claims -> set statuses (locks)
```

## WINDOWS SERVER SETUP (first-class)

The system runs natively on Windows Server - no Linux subsystem needed. Every
Linux-only dependency has a Windows fallback built in: PDF text via `pypdf` (no
poppler), certificates via the `cryptography` library (no OpenSSL), `icacls`
hardening (no chmod), and `waitress` as the production server (no gunicorn).

### W1 - Install Python & the app
1. Install **Python 3.12+** from python.org - tick **"Add python.exe to PATH"**.
2. Unzip the package to e.g. `C:\FleetFuel\app`.
3. Open PowerShell in that folder and install dependencies:
   ```powershell
   pip install -r requirements.txt
   ```
   (`flask openpyxl requests cryptography waitress pypdf`)

### W2 - Quick install (interactive)
```powershell
.\install.bat
```
The wizard checks Python, installs packages, creates the certificate (pure-Python,
no OpenSSL), creates your admin account, hardens files with `icacls`, takes a first
backup and self-checks. Then `.\start.bat` and open `https://localhost:8050`.

### W3 - Certificate (any source, no OpenSSL required)
```powershell
python make_cert.py fuel.yourcompany.local      # self-signed via cryptography lib
```
Or use a commercial/AD certificate by setting **system environment variables**
(Control Panel -> System -> Advanced -> Environment Variables, or `setx /M`):
`TLS_PFX` + `TLS_PFX_PASSWORD` for a .pfx, or `TLS_CERT`/`TLS_KEY`/`TLS_CHAIN`.
Verify: `python tls.py`.

### W4 - Run as a Windows Service (auto-start, survives reboot)
Recommended: **NSSM** (Non-Sucking Service Manager) + the production server.
1. Download `nssm.exe` from https://nssm.cc and place it on PATH or in the app folder.
2. In an **elevated** PowerShell, from the app folder:
   ```powershell
   .\install_service_windows.ps1 -ServiceName FleetFuel -Port 8050
   ```
   This registers a service that runs `serve.py` (waitress), auto-starts at boot,
   and logs to `service.log`.
3. Manage with `nssm restart FleetFuel`, `nssm stop FleetFuel`, or `services.msc`.

Alternative without NSSM: **Task Scheduler** -> Create Task -> Trigger "At startup",
Action `python.exe C:\FleetFuel\app\serve.py`, "Run whether user is logged on or
not". Simpler, but NSSM gives proper service lifecycle and restart-on-failure.

### W5 - HTTPS for a team
`serve.py` (waitress) serves HTTP locally; terminate TLS in front:
- **IIS** with Application Request Routing (ARR) + URL Rewrite reverse-proxying to
  `http://127.0.0.1:8050`, with the certificate bound in IIS; or
- **nginx for Windows** using the same reverse-proxy config as the Linux section.
Bind the app to localhost only (`BIND_HOST=127.0.0.1`, the default) so the proxy is
the sole entry point. Open only 443 in Windows Defender Firewall.

### W6 - Backups & disk encryption
```powershell
python backup.py --harden        # icacls: current user + SYSTEM only
python backup.py                 # snapshot (Task Scheduler: daily)
```
Schedule the daily backup in Task Scheduler, sync `backups\` to OneDrive/SharePoint,
and enable **BitLocker** on the drive holding the app (this is the protection if the
machine is lost - see SECURITY.md).

### W7 - Verify
```powershell
python tls.py                                    # TLS: OK
curl.exe -k https://localhost:8050/login         # 200 (if using dev-server TLS)
Get-Service FleetFuel                            # Running
```

---

### Windows quick-reference

| Task | Command |
|---|---|
| Install deps | `pip install -r requirements.txt` |
| Guided setup | `.\install.bat` |
| Make cert | `python make_cert.py <host>` |
| Start (foreground) | `.\start.bat` |
| Start (production) | `python serve.py` |
| Install service | `.\install_service_windows.ps1` |
| Stop stray server | `python cleanup.py` |
| Harden files | `python backup.py --harden` |
| Backup | `python backup.py` |

### Troubleshooting

| Symptom | Fix |
|---|---|
| `Address already in use` on 8050 | `python3 cleanup.py` (never `pkill -f app.py` — it matches itself) |
| Browser warns about certificate | Expected with self-signed; accept once, or install a CA cert (Part 3 B/C/D) |
| `TLS: NOT CONFIGURED` | Run `python3 tls.py` to see what it looked for; generate or point env vars |
| Login loop / signed out after restart | Session key rotated (`.secret_key` recreated) — sign in again; keep the file to persist sessions |
| `MISSING` invoices every month for one supplier | Check its `invoice_cadence` in suppliers.db (Data manager) |
| API source fails | `pip install requests`; token env var set in the systemd unit, then restart |
| Forgot admin password | `sudo -u fleetfuel .../python auth.py add <admin-user>` resets it from the shell |

### Related documentation

- **[#deployment-sizing-what-server-to-run](#deployment-sizing-what-server-to-run)** — what server to buy for a single-box
  install (CPU/RAM/disk, reference workload ~100 invoices/day, local-OCR sizing).
- **[#hosting-on-hostinger-step-by-step](#hosting-on-hostinger-step-by-step)** — step-by-step deploy on a Hostinger
  VPS (plan/OS choice, domain + Let's Encrypt, systemd + nginx, firewall, go-live).
- **[#scaling-the-fleet-fuel-vat-refund-system](#scaling-the-fleet-fuel-vat-refund-system)** — the single-box → web/worker fleet → PostgreSQL ladder
  (Parts 5b / 7c) and the credential-custody rotation detail (Part 8b).
- **[STRATEGY.md#multi-tenancy-program-plan](STRATEGY.md#multi-tenancy-program-plan)** — the P1+ rollout before turning `multitenant`
  on (Part 7d).
- **[#saf-t-export-programmable-oecd-core-generator-prework](#saf-t-export-programmable-oecd-core-generator-prework)** — what the SAF-T export is and how to specialise it per
  jurisdiction (the `/export/saft` core structure).
- **[SECURITY.md](../SECURITY.md)** — disk encryption, password storage, hardening,
  backup integrity.
- **[#putting-this-project-under-git](#putting-this-project-under-git)** — source control / what must never be committed
  (secrets, `security.db`, runtime DBs).
- **[#external-api-apiv1-token-contract](#external-api-apiv1-token-contract)** / **[#integrator-manual-apiv1-external-token-api](#integrator-manual-apiv1-external-token-api)** — the `/api/v1` external API
  (default-off; issue keys in the Admin panel).
- **[#ai-review-assistant-advisory-validation-analytics](#ai-review-assistant-advisory-validation-analytics)** — the advisory AI review assistant (default-off).

---

## Hosting on Hostinger — step by step

A complete, copy‑paste walkthrough to run the Fleet Fuel & VAT Refund System on a
**Hostinger VPS** with a real domain and HTTPS. It is the Hostinger‑specific version of
the generic **[#install-setup-installation](#install-setup-installation)** production path; for hardware sizing see
**[#deployment-sizing-what-server-to-run](#deployment-sizing-what-server-to-run)**.

> **Why a VPS, not Web/Cloud Hosting?** Hostinger's shared **Web Hosting** and **Cloud
> Hosting** plans are managed PHP/WordPress environments — they cannot run a long‑lived
> Python WSGI server (waitress), `systemd` services, or your own nginx. This app needs
> root on a Linux box, which is the **VPS (KVM)** product. Do **not** buy shared hosting
> for it.

Reference workload for the sizing here: **~100 invoices/day** (a single transport group).

---

### STEP 0 — Order the right Hostinger product

1. In Hostinger, choose **VPS Hosting → KVM** plan:

   | Plan | ~Specs (verify current) | Use it for |
   |------|-------------------------|------------|
   | KVM 1 | 1 vCPU / 4 GB / 50 GB NVMe | Bare minimum, baseline only (no local OCR) |
   | **KVM 2** | **2 vCPU / 8 GB / 100 GB NVMe** | **Recommended** for ~100 invoices/day |
   | KVM 4 | 4 vCPU / 16 GB / 200 GB NVMe | If you enable **on‑prem OCR** (Step 8) or want the 4‑vCPU comfort margin |

2. **Operating system template:** pick **Ubuntu 24.04 LTS (plain)**. Do **NOT** pick the
   "Ubuntu + Plesk / CyberPanel / CarePanel / Webmin" templates — a bundled control panel
   conflicts with the nginx + systemd setup below.
3. **Location:** choose an EU datacentre (closest to the Baltics, and keeps EU‑resident
   data in the EU for GDPR).
4. **Get a domain** (buy one in Hostinger or use one you own) — e.g. `fuel.yourcompany.eu`.
   A real domain is what lets you issue a trusted **Let's Encrypt** certificate in Step 5.
5. During VPS setup, set a strong **root password** (or upload an SSH key — recommended).
   Note the VPS **public IP** shown in hPanel.

---

### STEP 1 — Point the domain at the VPS

In Hostinger **hPanel → Domains → DNS / Nameservers** (or your registrar):

- Add an **A record**: host `fuel` (or `@` for the root) → **your VPS IP**.
- Wait for DNS to propagate (minutes to ~1 hour). Verify from your laptop:
  ```bash
  ping fuel.yourcompany.eu          # must resolve to the VPS IP
  ```

Do not continue to Step 5 (TLS) until the domain resolves to the VPS.

---

### STEP 2 — Connect and prepare the server

SSH in from your computer (hPanel also has a **Browser terminal** if you prefer):

```bash
ssh root@YOUR_VPS_IP
```

Then prepare the OS and a dedicated, unprivileged service user (never run the app as root):

```bash
# 2.1 Update the OS
apt update && apt upgrade -y

# 2.2 Base packages
apt install -y python3 python3-pip python3-venv unzip openssl curl nginx ufw

# 2.3 Dedicated service user (no login shell, home in /opt/fleetfuel)
adduser --system --group --home /opt/fleetfuel fleetfuel
python3 --version          # must be 3.12+
```

---

### STEP 3 — Install the application

Upload `fleet_fuel_system.zip` from your PC (run this **on your laptop**):

```bash
scp fleet_fuel_system.zip root@YOUR_VPS_IP:/tmp/
```

Back **on the VPS**, unpack into the service user's home and install dependencies in a
virtual environment:

```bash
unzip /tmp/fleet_fuel_system.zip -d /opt/fleetfuel/
mv /opt/fleetfuel/fleet_fuel_system /opt/fleetfuel/app
chown -R fleetfuel:fleetfuel /opt/fleetfuel/app

sudo -u fleetfuel python3 -m venv /opt/fleetfuel/venv
sudo -u fleetfuel /opt/fleetfuel/venv/bin/pip install -r /opt/fleetfuel/app/requirements.txt
# (installs flask, openpyxl, requests, cryptography, waitress, pypdf)
```

Quick smoke test (optional but worth it):

```bash
cd /opt/fleetfuel/app && sudo -u fleetfuel /opt/fleetfuel/venv/bin/python -m pytest tests/ -q
```

---

### STEP 4 — Local TLS + a session key

The app listens on `127.0.0.1` only and runs its **own** TLS there, so its session cookies
are flagged `Secure`. nginx will hold the public Let's Encrypt cert (Step 5) and proxy to
it. Generate a self‑signed cert for that internal hop, and a stable session‑signing key:

```bash
# 4.1 Self-signed cert for the localhost app→nginx hop (creates cert.pem/key.pem)
cd /opt/fleetfuel/app && sudo -u fleetfuel /opt/fleetfuel/venv/bin/python make_cert.py

# 4.2 A 32-byte secret so logins survive restarts (keep it secret, keep it stable)
openssl rand -hex 32
#  -> copy the output; you'll paste it as FFS_SECRET_KEY in Step 6
```

---

### STEP 5 — Public certificate (Let's Encrypt)

Issue a trusted certificate for your domain. Port 80 must be free right now (nginx isn't
configured yet), so use the standalone method:

```bash
apt install -y certbot
systemctl stop nginx 2>/dev/null || true
certbot certonly --standalone -d fuel.yourcompany.eu --agree-tos -m you@yourcompany.eu -n
# certs land in /etc/letsencrypt/live/fuel.yourcompany.eu/{fullchain.pem,privkey.pem}
```

Renewals are automatic (certbot installs a systemd timer). Add a hook so nginx reloads the
renewed cert:

```bash
mkdir -p /etc/letsencrypt/renewal-hooks/deploy
printf '#!/bin/sh\nsystemctl reload nginx\n' > /etc/letsencrypt/renewal-hooks/deploy/reload-nginx.sh
chmod +x /etc/letsencrypt/renewal-hooks/deploy/reload-nginx.sh
```

---

### STEP 6 — Run the app as a service (systemd)

```bash
sudo tee /etc/systemd/system/fleetfuel.service > /dev/null << 'EOF'
[Unit]
Description=Fleet Fuel & VAT Refund System
After=network.target

[Service]
User=fleetfuel
Group=fleetfuel
WorkingDirectory=/opt/fleetfuel/app
# Stable session-signing key (paste the openssl output from Step 4.2):
Environment=FFS_SECRET_KEY=PASTE_YOUR_32_BYTE_HEX_HERE
# Off-site backup copy (optional; e.g. a mounted volume or attached storage):
#Environment=FFS_BACKUP_SYNC_DIR=/mnt/backup/ffs
# On-prem OCR for scanned PDFs (optional; see Step 8):
#Environment=EXTRACT_OCR_BACKEND=auto
# AI extraction backend (optional; default keeps every byte on the server):
#Environment=EXTRACT_BACKEND=auto
#Environment=ANTHROPIC_API_KEY=...
ExecStart=/opt/fleetfuel/venv/bin/python serve.py
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now fleetfuel
systemctl status fleetfuel          # active (running)
journalctl -u fleetfuel -n 20       # shows "waitress on https://127.0.0.1:8050 ..."
```

---

### STEP 7 — Public access: nginx reverse proxy + firewall

nginx terminates the public HTTPS (Let's Encrypt) and forwards to the app on localhost:

```bash
sudo tee /etc/nginx/sites-available/fleetfuel > /dev/null << 'EOF'
server {
    listen 443 ssl;
    server_name fuel.yourcompany.eu;                                    # your domain
    ssl_certificate     /etc/letsencrypt/live/fuel.yourcompany.eu/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/fuel.yourcompany.eu/privkey.pem;
    client_max_body_size 25m;                                           # invoice PDF uploads
    location / {
        proxy_pass https://127.0.0.1:8050;     # app runs its own (self-signed) TLS
        proxy_ssl_verify off;                  # internal hop; cert is self-signed
        proxy_set_header X-Forwarded-For $remote_addr;
        proxy_set_header Host $host;
    }
}
server { listen 80; server_name fuel.yourcompany.eu; return 301 https://$host$request_uri; }
EOF

ln -s /etc/nginx/sites-available/fleetfuel /etc/nginx/sites-enabled/
rm -f /etc/nginx/sites-enabled/default          # drop the welcome page
nginx -t && systemctl enable --now nginx && systemctl reload nginx
```

Open the firewall — **in two places**:

```bash
# On the VPS (ufw):
ufw allow OpenSSH
ufw allow 80,443/tcp
ufw enable
ufw status
```

Then in **hPanel → VPS → Firewall**, allow **80** and **443** inbound, and restrict **22
(SSH)** to your office/home IP if you can. (Hostinger's network firewall sits in front of
ufw — if a port is blocked there it never reaches the box.)

---

### STEP 8 — (Optional) On‑prem OCR for scanned PDFs

Structured e‑invoices, hybrid Factur‑X/ZUGFeRD PDFs, and recognised supplier layouts need
nothing extra. A **scanned / image‑only** PDF carries no text — to recover those locally
(no bytes leave the VPS), install the OCR stack and turn the backend on:

```bash
apt install -y tesseract-ocr poppler-utils
sudo -u fleetfuel /opt/fleetfuel/venv/bin/pip install pytesseract pdf2image
# then in the systemd unit (Step 6): Environment=EXTRACT_OCR_BACKEND=auto  -> restart
systemctl restart fleetfuel
```

OCR is CPU/RAM‑spiky per page — this is the reason to size up to **KVM 4** if you expect
many scanned documents. The default `auto` silently skips OCR when the engine is absent,
so leaving it off changes nothing.

---

### STEP 9 — First run, backups, and go‑live checklist

1. Browse to **`https://fuel.yourcompany.eu`** → you should see a green padlock (Let's
   Encrypt) and the **first‑run setup wizard**. Create the admin username + password.
2. In **Admin → Backups**, set `backup_interval_hours` (e.g. `24`) and run **Run backup**,
   then **Verify backup** and **Verify documents** — all green.
3. In **hPanel → VPS → Snapshots/Backups**, enable Hostinger's **automatic VPS backups**
   as a second, off‑box layer (the app's own encrypted backups are layer one).
4. Harden: confirm `ufw status` shows only 22 (your IP) / 80 / 443; keep the OS patched
   (`apt upgrade`); keep `FFS_SECRET_KEY` and `.secret_key` private and backed up.

**Go‑live checklist**

- [ ] `https://your-domain` loads with a trusted certificate.
- [ ] `systemctl status fleetfuel` and `systemctl status nginx` are both *active (running)*.
- [ ] `pytest tests/ -q` passed on the box (Step 3).
- [ ] hPanel firewall + ufw expose only SSH (restricted) / 80 / 443.
- [ ] A verified backup exists, and Hostinger VPS snapshots are scheduled.
- [ ] `certbot renew --dry-run` succeeds (auto‑renewal works).

---

### What you do NOT need at this scale

For ~100 invoices/day stay on the **single‑box default**: SQLite + WAL, the in‑process
intake worker, one VPS. You do **not** need PostgreSQL, a separate worker tier, gunicorn,
or a load balancer — those are the higher rungs in **[#scaling-the-fleet-fuel-vat-refund-system](#scaling-the-fleet-fuel-vat-refund-system)** and only
pay off at thousands of invoices/day or when you need high availability.

---

### Troubleshooting (Hostinger specifics)

| Symptom | Fix |
|---|---|
| Domain won't load, but the IP works | DNS A record not propagated, or **hPanel firewall** is blocking 80/443 — check both. |
| `certbot` fails to validate | Port 80 reachable? Stop nginx during `--standalone`, and confirm the hPanel firewall allows 80. DNS must already point at the VPS. |
| Browser shows the nginx welcome page | You didn't remove `/etc/nginx/sites-enabled/default` (Step 7). |
| Login loops / logged out after restart | `FFS_SECRET_KEY` not set (Step 6) — the key rotated. Set it and restart. |
| `502 Bad Gateway` from nginx | The app service is down (`systemctl status fleetfuel`) or not on `127.0.0.1:8050` — check `journalctl -u fleetfuel`. |
| Big invoice ZIP rejected by nginx | Raise `client_max_body_size` in the server block, then `systemctl reload nginx`. |

---

### Related documentation

- **[#install-setup-installation](#install-setup-installation)** — the full generic production guide (this is its Hostinger cut).
- **[#deployment-sizing-what-server-to-run](#deployment-sizing-what-server-to-run)** — choosing CPU/RAM/disk; local‑OCR sizing.
- **[#scaling-the-fleet-fuel-vat-refund-system](#scaling-the-fleet-fuel-vat-refund-system)** — when and how to grow beyond one box.
- **[../SECURITY.md](../SECURITY.md)** — data protection, disk encryption, hardening.

---

## Deployment sizing — what server to run

Companion to `#scaling-the-fleet-fuel-vat-refund-system`. That document is the **ladder** for when you outgrow one box;
this one answers the more common question: **"what do I actually buy to run this
smoothly?"** for a normal single-tenant install processing a modest invoice volume.

The headline: this app is **light**. The web tier (Flask + waitress + WAL-SQLite) sits
well under 1 GB of RAM and near-idle CPU. The only components that move the needle on
sizing are the **extraction path** (CPU/RAM-spiky *only* if you run local OCR or a local
VLM) and **disk growth** (the `documents/` vault + backup snapshots). Everything else is
noise at the volumes a single transport group produces.

---

### Reference workload: ~100 invoices/day

100 invoices/day is ~12–13 per working hour, ~1 every five minutes — even arriving in a
few batch uploads it is a trivial throughput. The transaction databases grow by ~37k rows
per year, nothing for SQLite. **This stays firmly at `#scaling-the-fleet-fuel-vat-refund-system` Stage 0 (one tuned
box).** You do **not** need Postgres, a worker fleet, or a load balancer at this scale —
adding them only adds operational surface area and failure modes.

---

### Recommended specification

| Scenario | vCPU | RAM | Disk (SSD/NVMe) | When |
|----------|:----:|:---:|:---------------:|------|
| **Baseline** | 2 | 4 GB | 80–120 GB | Structured e-invoice + deterministic parser + *cloud* AI fallback only (no local OCR/VLM) |
| **Recommended** | **4** | **8 GB** | **150 GB** | The sweet spot — headroom for OCR, Excel/SAF-T exports, backups, and a few concurrent operators. **Buy this.** |
| **Local OCR / local VLM** | 4–8 | 16 GB | 200 GB | You run Tesseract OCR (`EXTRACT_OCR_BACKEND`) or a local Docling/VLM extractor on-prem |

**OS:** Ubuntu Server LTS (24.04) recommended; any stable Linux is fine. Windows is
supported (waitress is cross-platform), but Linux behind nginx is the smoother production
path. Use an **SSD/NVMe** — never spinning disk — because WAL write latency matters.

A GPU is **not** worth it at this volume: if you want fully on-prem extraction, CPU-based
Docling (~0.5–2 s/page) handles 100 invoices/day with ease; reserve GPUs for the day you
process thousands of *scanned* pages per day.

---

### Why these numbers (where the load actually is)

- **CPU** — structured/Factur-X parsing and the deterministic parsers are milliseconds per
  document. Cloud AI extraction is network-bound (local CPU near-idle). The *only*
  CPU-heavy path is **local OCR/VLM**, and even a scanned batch at 100/day finishes in
  seconds. The extra cores buy burst headroom for a batch upload landing while someone
  runs an Excel/SAF-T export or a backup is zipping.
- **RAM** — Flask + waitress (4 threads) + SQLite-WAL is < 1 GB. The 8 GB is buffer for
  OCR/PDF rasterisation (`pdf2image` holds page bitmaps in memory) and OS page cache for
  fast DB reads. 4 GB is safe **only** if you will never run local OCR/VLM.
- **Disk is the real growth driver, not compute.** At ~250 KB/PDF, 100 invoices/day is
  **~9 GB/year** in `documents/` (SHA-256 dedup trims repeats), plus periodic backup
  snapshots that bundle the DBs + `documents/` + audit CSVs. The databases themselves stay
  tiny. Budget generously and prefer a **separate volume** for `documents/` and `backups/`
  so vault/backup I/O never competes with the live DB.

---

### Configuration for a smooth single box (all built-in — no rewrite)

| What | How |
|------|-----|
| Production server | `python serve.py` (waitress). Bump `THREADS=8` only if many concurrent operators (default 4 is plenty). |
| Database | Keep **SQLite + WAL** — `db_tuning.py` tunes it automatically. Postgres is unnecessary here. |
| HTTPS | Terminate TLS with **nginx** (or IIS on Windows) in front; `serve.py` binds HTTP locally for the proxy to wrap. |
| Background work | Default `FFS_ROLE=all` — the in-process intake worker drains the queue; no separate worker node needed. |
| Backups | Enable the scheduler (admin setting `backup_interval_hours`) + off-machine backup sync; the daily `verify_backup` / `verify_docs` integrity checks are effectively free at this scale. |
| Secrets / KEK | `FFS_KEK_KEY` (or the `local` provider) for the envelope-encrypted credential vault; keep `.secret_key` on the box. |

Relevant env vars are catalogued in `#scaling-the-fleet-fuel-vat-refund-system` → *Configuration reference*.

---

### Concrete host options (pick one)

| Provider | Instance | Spec | Notes |
|----------|----------|------|-------|
| AWS | `t3.large` (`t3.xlarge` if local OCR) | 2–4 vCPU / 8–16 GB | EBS **gp3** ~150 GB; snapshot for DR |
| Azure | `B2ms` (`B4ms` if local OCR) | 2–4 vCPU / 8–16 GB | OS disk + a separate data disk for `documents/` |
| Hetzner | `CX32` / `CX42` | 4–8 vCPU / 8–16 GB | + a Volume; excellent value, ample for this |
| DigitalOcean | Basic/Premium 4 vCPU / 8 GB | 4 vCPU / 8 GB | + a Block Storage volume |
| On-prem | NUC-class mini-PC | 4-core / 16 GB / NVMe | Put it on a **UPS**; back up off-machine |

---

### When to climb the ladder (none of these are near 100/day)

Move to `#scaling-the-fleet-fuel-vat-refund-system` Stage 1+ only when you observe:

1. **Sustained SQLite write contention** — you'd be at *thousands* of invoices/day. → Stage 1: Postgres.
2. **`documents/` outgrowing local disk** — → Stage 2: object store (S3/Azure Blob via `document_vault.py`).
3. **A need for HA / zero-downtime / horizontal throughput** — → Stage 3–4: split `web`/`worker` roles behind a load balancer with a shared `FFS_SECRET_KEY`.

At 100 invoices/day you are an order of magnitude below every one of these triggers.

**Bottom line:** a **4 vCPU / 8 GB / 150 GB NVMe** Linux box behind nginx, running
SQLite-WAL with daily verified backups, runs 100 invoices/day with room to spare.

---

## Scaling the Fleet Fuel & VAT Refund System

When the databases and the PDF/ZIP store grow large and the single box gets slow, you
grow **by configuration, not by rewrite**. The architecture was built for this: the
database layer is abstracted (`db.py`), file storage is a pluggable backend
(`document_vault.py`), cross-process coordination uses time-limited leases
(`process_lock.py`), and the intake queue is a durable, lease-based, reclaimable
worker queue (`waiting_room.py`).

This document is the ladder — each rung is more capacity for more operational effort —
plus an **honest list of what still needs doing** for a fully validated multi-server
deployment.

> Just sizing one box for a normal install (e.g. ~100 invoices/day)? See
> **`#deployment-sizing-what-server-to-run`** — you almost certainly stay at Stage 0 below.

---

### What gets slow first (bottleneck order)

1. **The single SQLite writer.** WAL (`db_tuning.py`) gives unlimited concurrent
   *readers* + *one* writer per DB. Heavy ingest/write volume hits this first.
2. **Local disk for `documents/` and `backups/`.** Grows unbounded; competes for the
   same I/O as everything else.
3. **CPU-heavy extraction** (PDF/ZIP → draft) blocking web workers.
4. **Per-request connections** at high concurrency.

Each has an escape hatch below.

---

### The ladder

#### Stage 0 — one box, tuned (default)
`python serve.py` (waitress) + WAL + the in-process background intake worker.
Vertical scaling (more CPU/RAM/SSD) takes this a long way. Nothing to change.

#### Stage 1 — move the database tier to Postgres  ⟵ highest leverage
Removes the one-writer ceiling: concurrent writers, real pooling, a DB server on its
own machine (or managed RDS / Azure DB for PostgreSQL).

```
pip install "psycopg[binary]"
createdb fuelvat                      # and schemas: customers suppliers fuel_history security
export DB_ENGINE=postgres DB_DSN=postgresql://user:pass@db-host/fuelvat
python db.py --migrate                # copy every table from the .db files into Postgres
# restart the app
```

DB separation is preserved — each logical DB becomes a Postgres **schema**, so the
legal/financial isolation survives. `db.connect()` wraps the Postgres connection in a
paramstyle shim (`_PgShim`) that translates the modules' SQLite `?` placeholders to
psycopg's `%s` automatically. **See "Remaining blockers" before flipping in production.**

#### Stage 2 — move file storage off the app server
`document_vault.py` already supports a pluggable, backend-agnostic store (local disk +
SharePoint/Graph today; S3/Azure Blob is a one-class addition following the same
pattern). Locators are prefix-tagged (`sp://…` vs local) so historical documents keep
resolving after you switch the default.

```
export DOC_BACKEND=sharepoint          # + the SP_* / Graph credentials
python -c "import document_vault as v; v.migrate_local_to_sharepoint()"   # move history once
```

Now PDF/ZIP storage no longer grows the app server's disk, and every node reaches the
same documents.

#### Stage 3 — split web nodes from a worker fleet ("delegate tasks per server")
Run the **same codebase** in different roles, set per node with `FFS_ROLE`:

| Role | What it runs | How to launch |
|------|--------------|---------------|
| `web` | HTTP only; does **not** drain the queue | `FFS_ROLE=web python serve.py` (behind nginx) |
| `worker` | background extraction only | `python waiting_room.py --work` |
| `all` (default) | web **and** the in-process worker | `python serve.py` |

The intake queue (`waiting_room.py`) is **lease-based**: a worker claims a job for a
TTL; if it dies, the lease expires and another worker reclaims the job. That is exactly
what lets **N worker servers drain one shared queue** safely. The backup scheduler
self-elects a single **leader** across all processes (`process_lock`), so it is safe to
leave running everywhere — only one node ever snapshots.

#### Stage 4 — load-balance the web tier
Put nginx / a cloud LB in front of the `web` nodes. **No sticky sessions needed**:
sessions are signed cookies, so any node validates any node's cookie — **provided every
web node shares one signing key**:

```
export FFS_SECRET_KEY=<the-same-32+-byte-secret-on-every-web-node>
```

(When unset, each box generates its own `.secret_key` file — fine for one server, wrong
for a fleet.) Add PgBouncer in front of Postgres for connection pooling.

---

### Target multi-server topology

```
                       ┌─────────────┐
        clients ──────▶│  nginx / LB │
                       └─────┬───────┘
              ┌──────────────┼──────────────┐
         ┌────▼────┐   ┌─────▼───┐    ┌──────▼──┐      FFS_ROLE=web
         │ web #1  │   │ web #2  │ …  │ web #N  │      FFS_SECRET_KEY=<shared>
         └────┬────┘   └────┬────┘    └────┬────┘
              └──────────────┼──────────────┘
                 shared queue │ (intake)         ┌──────────────┐
              ┌───────────────┼──────────────┐   │ worker #1..M │ FFS_ROLE=worker
              │               │              │   │  (extraction)│ waiting_room.py --work
        ┌─────▼─────┐   ┌─────▼──────┐  ┌─────▼───────┐ └──────────────┘
        │ Postgres  │   │ object store│  │ off-machine  │
        │ (+PgBouncer)│ │ (SharePoint │  │ backup sync  │
        │  master +  │  │  / S3 docs) │  │ (OneDrive/   │
        │  queue +   │  └─────────────┘  │  S3 versioned)│
        │  locks)    │                   └──────────────┘
        └────────────┘
```

---

### Configuration reference (env vars)

| Variable | Purpose | Default |
|----------|---------|---------|
| `DB_ENGINE` | `sqlite` or `postgres` | `sqlite` |
| `DB_DSN` | Postgres DSN (when `postgres`) | — |
| `DOC_BACKEND` | `local` or `sharepoint` | `local` |
| `FFS_ROLE` | `all` / `web` / `worker` | `all` |
| `FFS_SECRET_KEY` | shared session signing key for the web fleet | per-box file |
| `INTAKE_WORKER` | `0` forces the in-process worker off | `1` |
| `INTAKE_DB` / `INTAKE_INBOX` | queue DB / inbox location | local files |
| `BIND_HOST` / `BIND_PORT` | waitress bind | `127.0.0.1:8050` |

---

### Remaining blockers to a fully-validated 100% (be honest)

What already works and is tested on SQLite:
- ✅ Node roles (`FFS_ROLE`) — web nodes don't drain the queue; workers do.
- ✅ Shared sessions (`FFS_SECRET_KEY`) — no sticky sessions on the LB.
- ✅ Pluggable file storage (local / SharePoint) with stable historical locators.
- ✅ Lease-based queue + leader-elected scheduler (multi-process safe today).
- ✅ Paramstyle shim `db.qmark_to_pyformat()` (`?` → `%s`) — unit-tested.
- ✅ Mechanical dialect shim `db.translate_dialect()` — `datetime('now')` (no-modifier)
  → `now()` and `INSERT OR IGNORE` → `INSERT … ON CONFLICT DO NOTHING`, unit-tested.

What still needs doing **before** a production Postgres cutover (needs a live Postgres
to validate — not available in CI):
1. **Exercise `_PgShim` against a real psycopg** — the translator is unit-tested, but
   the connection/cursor wiring needs an integration pass on a live DB.
2. **Dialect functions.** The two MECHANICAL dialect-isms are now auto-translated at the
   shim (`db.translate_dialect()`, unit-tested): `datetime('now')` [no-modifier] → `now()`
   and `INSERT OR IGNORE` → `INSERT … ON CONFLICT DO NOTHING`. What REMAINS as per-site
   ports (no mechanical rewrite — must be validated on a live Postgres):
   `datetime('now', <modifiers>)` → interval math (e.g. `now() - interval '1 day'`),
   `INSERT OR REPLACE` → `ON CONFLICT(<target>) DO UPDATE SET`, and the `json_object`
   **audit triggers** (`audit.py`) → a Postgres trigger function.
3. **Migrate the operational DBs too.** `db.py --migrate` copies the four master DBs;
   for a shared fleet the **queue** (`intake`) and the **cross-node leases**
   (`process_lock`) must also live on the shared Postgres, or workers/leaders won't
   coordinate across machines.
4. **Postgres-native backups.** `backup.py` snapshots SQLite files; on Postgres use
   `pg_dump` / managed snapshots, and sync `backups/` (or object-store versioning)
   off-machine so a disk loss can't take the data.
5. **Ops glue (no code):** nginx/LB, PgBouncer, and per-node env wiring.

Items 2–4 are the genuine engineering remainder; 1 and 5 are validation/ops. Until
those land and are validated on a live Postgres, run Stage 0–2 (one app box + Postgres +
offloaded storage), which already scales well past a single-box-SQLite deployment.

---

## Putting this project under git

Run these once, in the project folder, to start tracking changes.

### 1. Initialise
```
git init
git add .
git commit -m "Initial commit: Fleet Fuel & VAT Refund System"
```
`.gitignore` already excludes secrets, certificates, security.db, and runtime
files — verify nothing sensitive is staged before the first commit:
```
git status            # review the list
git ls-files | grep -E "secret|cert|key|security.db"   # should print NOTHING
```

### 2. (Optional) Push to a private remote
Use a PRIVATE repository — this contains business data and master records.
GitHub example:
```
git remote add origin https://github.com/<you>/fleet-fuel.git
git branch -M main
git push -u origin main
```
EU-hosted alternatives if you prefer to keep code in the EU: GitLab.com (or a
self-hosted GitLab), Codeberg (Germany), or your company's internal git server.

### 3. Day-to-day
```
git add -A
git commit -m "describe what changed"     # after each working change
git log --oneline                          # history
git revert <hash>                          # undo a specific change safely
```

### 4. Working with Claude Code
With git in place, Claude Code (claude.ai/code or the CLI) can open this folder,
read CLAUDE.md for context, make changes on a branch, run the app, and commit.
Recommended flow: a branch per task (`git checkout -b add-wholesale-feed`), let
Claude Code work there, review the diff, merge when happy.

### Databases in git — your choice
- customers.db / suppliers.db / fuel_history.db ARE tracked by default so a clone
  is immediately usable with the May 2026 data.
- security.db is NEVER tracked (password hashes).
- If you'd rather keep ALL business data out of git, add the three DBs to
  .gitignore and instead commit a seed script. For a single-maintainer project,
  tracking them is simpler; for a shared public-ish repo, exclude them.

---

## Process reliability — stuck/stall risks & hardening

A reliability audit of the operational processes (intake → queue/worker → extraction →
validate/register → consolidate→build_master→history close → VAT claim → invoicing),
grounding each finding in the code and in established best practice. Goal: keep processes
flowing and **never silently stuck**.

The durable queue is *mechanically* sound (fsync-before-commit durability, content-hash
dedup, `BEGIN IMMEDIATE` claim serialization, lease-expiry reclaim, capped backoff, a
separate transient/quota `waiting`/`held` track). The residual risks are **operational** —
an upload gate one bad job can freeze, reclaim that's too slow, dead-letters that are
visible-but-not-actioned, and a non-atomic monthly close.

### A. Adjust for smoothness — stuck/stall risks (priority order)

| # | Risk | Evidence | Sev | Status | Fix (grounded) |
|---|------|----------|-----|--------|----------------|
| 1 | One stuck job freezes ALL uploads, fleet-wide — terminal `failed`/`held` are in `PENDING_STATES`; uploads block while `pending_count()>0` | `waiting_room.py:413`, `app.py:787` | High, silent | NEW | Exclude terminal states from the *upload gate*. *Shed poison messages to a DLQ so they don't block the queue (AWS).* |
| 2 | Register split-brain — on permanent register failure, PDFs are vaulted but no `supplier_invoices`; failure IS visible in the monitor (Retry available) but not distinctly flagged | `app.py:2126`, `waiting_room._fail_or_retry` | High | NEW (D4 shipped; hardening pending) | Distinct "registration failed — statement X" worklist item + vaulted-doc-without-invoice reconcile sweep. *A silent DLQ is the documented anti-pattern.* |
| 3 | Close stale-pickle period mismatch — `consolidated_rows.pkl` carries no period stamp; editing `month_config` between steps loads the wrong period | `consolidate.py:68`, `history.py:69` | High, silent corruption | NEW (fold into D5) | Stamp PERIOD + row-count/hash into the pickle; assert before load. *Key off a logical period (Airflow `logical_date`).* |
| 4 | `rejected` releases invoice locks — contradicts 3B/3C/3D → invoice re-claimable = duplicate-submission | `vat_refund.py:437` | High, integrity | NEW (in backlog) | `rejected` keeps locks; release only via `withdraw_claim`. |
| 5 | No startup orphan-sweep — a crashed worker leaves a `processing` row stuck for the full 600s lease; a hard kill burns all 5 attempts thrashing | `waiting_room.py:48,247` | Med | Partly covered | Reclaim expired `processing` rows on worker start; distinguish process-crash. *Lease-reclaim sweep is THE crashed-worker unstick mechanism (SQS/Pub-Sub).* |
| 6 | Notify scheduler blind on SMTP failure — `notify_last_sent` advances even when send fails | `app.py:688`, `notify.py:206` | Med, silent | NEW | Stamp `last_sent` only on success; record SMTP failures to the error log. *Dead-man's-switch: alert on absence of a successful run.* |
| 7 | Close partial-run, no checkpoint; `history.py` runs at import | `history.py:38-188` | Med | Covered (D5) | D5 orchestrator: restartable, per-step idempotency, body → `main()`. *Staging + atomic swap.* |
| 8 | No aggregate extract deadline; `LEASE_SECONDS=600` < worst-case batch → reclaim + double-work; pypdf probe unbounded | `extract.py:156,255,536,442` | Med, head-of-line | NEW | Per-job deadline / cap members; lease heartbeat; bound pypdf. *Timeout→bounded-retry→circuit-breaker; lease > p99.* |
| 9 | `process_lock`: wall-clock TTL, no fencing — a paused leader past lease can double-act; clock skew can double-elect on a fleet | `process_lock.py:55` | Med, rare | NEW | Monotonic-clock deadline; monotonic `lease_epoch` (fencing token) + compare-and-set; per-action `-run` guard. *Kleppmann fencing tokens + monotonic clocks; SQLite already gives the single linearizable store.* |
| 10 | Swallowed errors hiding a stuck step — `file_documents_for_claim` `except: pass` after lock (locked-but-doc-unfiled); `_import_log` failure blinds the monitor feed | `vat_refund.py:499`, `waiting_room.py:275` | Med, silent | Partly covered | Log via `applog`/`_log_exc`; surface as integrity/feed warnings. |
| 11 | Backup can archive a torn file mid-close — vault/lake walked with plain `open()`, no lock vs close writes | `backup.py:89` | Low | NEW | mtime-recheck/skip, or take `backup-run` lock around close writes. |

### B. Develop more — completeness for smooth, automated flow

- **Real exception/DLQ lane + alerting** — formalize `failed`/`held` into a dead-letter
  view with **growth-rate alerting** (not just depth) and one-click **redrive/replay**.
  *AWS DLQ + alarm on `≥1`; alert on rate of change.*
- **Stuck-job metric** — expose **oldest-pending-job age** (`MIN(created_at)` of pending)
  and alarm vs an SLO; the single best early-warning of a stalled consumer.
  *SQS `ApproximateAgeOfOldestMessage` / Pub-Sub `oldest_unacked_message_age`.*
- **UNMATCHED in-product resolution UI** — "assign invoice ref" so a blocked claim isn't a
  dead end (planned Phase 4).
- **Confidence-routed exception queue** — deterministic high-confidence auto-flows;
  AI/low-confidence quarantines, never gating (planned Phase 5). *IDP exception-queue.*
- **Idempotency-key discipline** on every deferred side effect — side-effect + dedup-insert
  in ONE transaction (reclaim will occasionally redeliver). *Stripe/Brandur idempotency.*

### C. Recommended reliability sprint (highest impact, each small)
**#1** (un-gate uploads from terminal jobs) → **#5** (startup orphan-sweep) → **#6** (notify
only on success) → **#4** (`rejected` keeps locks) → **#3** (pickle period stamp, fold into
D5). These kill the worst *silent* stalls and are each a focused, low-risk change.

### Sources (best-practice grounding)
- Durable queues: AWS SQS visibility-timeout/DLQ docs; Google Pub/Sub ack-deadline;
  RabbitMQ/Celery/Sidekiq retry+DLQ; Stripe/Brandur idempotency keys; DDIA (at-least-once).
- Pipelines: Google SRE *Data Processing Pipelines* (freshness SLOs); Airflow/Dagster/Prefect
  idempotent re-runs & checkpointing; staging+atomic-swap; timeout→retry→circuit-breaker.
- Scheduling/locks: Kleppmann "How to do distributed locking" (fencing tokens, monotonic
  clocks) vs antirez "Is Redlock safe?"; Google SRE *Distributed Periodic Scheduling*
  (skew toward skipping; identify launch by start time); Kubernetes Lease/CronJob
  `concurrencyPolicy`/`startingDeadlineSeconds`; dead-man's-switch monitoring.

---

## USER MANUAL — Fleet Fuel & VAT Refund System

How to use the software day-to-day. For installation see [#install-setup-installation](#install-setup-installation);
for security policy see [SECURITY.md](../SECURITY.md); for architecture see
[../README.md#architecture](../README.md#architecture).

---

### 0. Starting the app

**Which case are you?**

#### A. On your own computer (one person)

1. **Start it** — double-click the launcher in the program folder:
   `start.bat` (Windows) · `start.command` (macOS) · `start.sh` (Linux).
   The first run installs what it needs (about a minute) and opens your browser
   automatically. *(Only requirement: Python 3.10+ — if it's missing, the launcher
   tells you where to get it. Full steps in [#install-setup-installation](#install-setup-installation).)*
2. **First time only** — a **setup page** appears: choose an admin username and
   password, leave the HTTPS box ticked, click **Create account & finish**.
3. **Open it any time** at **`http://localhost:8050`** (or `https://…` once a
   certificate is in place). Sign in with the account you created.
4. **Stop it** — close the small console window the launcher opened, or press
   **`Ctrl+C`** in it. Your data stays on disk; start again whenever you like.

> The console window must stay open while you use the app — it *is* the running
> program. Closing it shuts the app down cleanly (nothing is lost).

#### B. On a shared server (a team)

On a server the app runs continuously as a background **service**, so nobody has to
keep a window open. IT sets this up once (full walkthrough — Ubuntu and Windows, TLS,
reverse proxy, automatic backups — in [#install-setup-installation](#install-setup-installation)). Day-to-day you just
open the company address in a browser, e.g. **`https://fuel.yourcompany.local`**, and
sign in.

To start / stop / restart the service (Linux, run by IT):

```bash
sudo systemctl start fleetfuel       # start
sudo systemctl stop fleetfuel        # stop
sudo systemctl restart fleetfuel     # restart (e.g. after a config change)
sudo systemctl status fleetfuel      # is it running?
journalctl -u fleetfuel -n 50        # recent log lines
```

The service auto-starts on boot and restarts itself if it ever crashes; backups run on
a nightly schedule. Adding colleagues is done in the web **Admin** panel (create them
as **processor**), not on the server.

> **Growing past one server.** The same software runs unchanged from a single laptop to
> a multi-server fleet (separate web nodes + a worker fleet + a PostgreSQL database +
> off-machine document storage), set up entirely through configuration. That's an IT
> concern, not a day-to-day one — the full ladder and settings are in
> **[#scaling-the-fleet-fuel-vat-refund-system](#scaling-the-fleet-fuel-vat-refund-system)**.

---

### 1. Signing in & roles

Open `https://<server>:8050` (or your company URL) and sign in. Your role is shown
in the top bar next to your name. There are two roles:

| Role | Can do |
|---|---|
| **processor** | The day-to-day worker: import batches, register statements, attach documents, pricing analytics, exports. **Cannot** see the VAT-refund module (claims, readiness, recovery, customers/CRM), do server setup, user administration, or overall software changes. Individual capabilities are configurable by an admin. |
| **admin** | Everything, **plus** the **VAT-refund module** (claims 1A→5, readiness, recovery & fees, the customer CRM) and the **Admin** panel: create users, assign roles, reset passwords, disable accounts, **adjust which capabilities processors have**, **switch whole parts of the app on/off (Modules)**, review the login & error logs, run/verify backups, and check document integrity. |

A processor's capabilities (data import, invoice control, pricing, documents,
exports) are switches an admin sets in the Admin panel — so you can give one
colleague import‑only access and another compliance‑only, for example. The
**VAT‑refund module is always admin‑only**, whatever capabilities a processor holds.

Every change you save is recorded in the audit log **under your username** — visible
on the History page. Sessions are protected (login lockout after repeated failures,
per‑IP throttle, 8‑hour idle timeout). Sign out with the link in the top bar.

### 2. The pages, left to right

**Dashboard** — opens with a **month-close status strip** (data loaded? invoices
received? statements reconciled? anomalies? backed up?) and a **“What needs action”
worklist** — claims ready to submit, claims blocked on documents/activation, claims
aging past 120 days unpaid, and fees ready to invoice — each line links straight to
where you act. Below, the month at a glance: diesel litres, fleet effective €/L, net
spend, reclaimable VAT. Then the diesel benchmark (suppliers ranked cheapest‑first by
effective price) and the month‑over‑month trend. The period selector switches months.

Tables across the app sort on a column click, type‑to‑filter once they pass a few
rows, and keep their header visible while you scroll. Press **?** for keyboard shortcuts.

**Compare** — answer "who was cheapest for X in Y during Z": pick period, supplier,
country, product, and an optional date range; the table recomputes. "ALL" widens any
filter. *Effective* price includes rebates (e.g. Q8 after Port One); *doc* price is
what's printed on the invoice.

**Head-to-head** — the negotiation page. Days where 2+ suppliers bought diesel in the
same country: each supplier's price, the cheapest highlighted green, the spread, and
the **overpay €** versus the cheapest available that day. The total at the top is
your evidence number for supplier talks.

**Entities & VAT** — per legal entity per country: net, reclaimable VAT, gross. One
row here = one potential refund stream.

**Stations** — diesel stations ≥300 L ranked by effective €/L with routing flags:
green **PREFER** (≤1.40), red **AVOID** (≥1.62). Share with dispatchers.

**Savings** — the avoidable‑overpay view as a chart: how much was spent above the
cheapest available option, the headline figure dispatch and procurement act on. A
**Supplier price‑review** card ranks who to renegotiate (most‑overpaid first) and offers
**Download price‑review packet (Excel)** (`/export/overpay`) — the per‑fuelling‑day detail
behind each supplier's overpay. This is a **competitiveness review, not a contractual
claim**: each figure is how much more a supplier charged than the cheapest same‑day,
same‑country diesel rival — evidence for renegotiation or steering volume, not money owed.
NET EUR/L, final.

**Expenses** — the finance‑facing company expense / cost‑allocation report: net, VAT and
gross spend per **entity (cost centre)**, per **product group** and per **vehicle** over the
validated transactions (gross = net + VAT; NET EUR, rebates applied, VAT shown separately).
Three downloads for your accounting/ERP:
- **Download expense report (Excel)** (`/export/expenses`) — the per‑entity / per‑vehicle
  workbook.
- **Download accounting ledger (CSV)** (`/export/accounting`) — one row per transaction,
  decision‑free (no chart‑of‑accounts mapping), for import into your accounting/ERP system.
- **Download SAF‑T (XML, core structure)** (`/export/saft`) — the OECD SAF‑T core structure
  for a period. **Note:** this is a generic core structure, **not** a validated submission
  for any tax authority — specialise the namespace/version/required fields per jurisdiction
  before any real filing (see [#saf-t-export-programmable-oecd-core-generator-prework](#saf-t-export-programmable-oecd-core-generator-prework)).

**FX vs ECB** — the exchange rates used versus the official ECB reference, so any
currency conversion in the claims is transparent and auditable.

**Transactions** — the line‑level detail, filterable by client, supplier, country,
location/station and date. **Anomalies are highlighted in place** (amber) — the flag
sits on the exact transaction it belongs to, learned from each country/period's own
price spread (no fixed threshold). **Discount/adjustment lines** (e.g. MOEVE promo
lines) are highlighted (blue) and related to the supplier/country/period they apply to.
The **Rebate/Discount** column shows the rebate actually applied (e.g. Q8's Port One),
a discount line's value, or — when the separate rebate invoice isn't present — an
**expected** rebate estimated from historic data, so known discounting is never lost.

**Import batch** — upload a supplier's PDF, a ZIP, or a structured **XML e‑invoice**
(UBL/CII, EN 16931 — these parse deterministically at high confidence, no AI). A
**hybrid PDF that carries the e‑invoice inside it (Factur‑X / ZUGFeRD / XRechnung)** is
detected automatically and parsed the same deterministic, no‑AI way — you upload the
ordinary‑looking PDF and the system reads its embedded data, keeping the original PDF on
file. **One safeguard to know:** a hybrid PDF can ship a *cut‑down profile* (MINIMUM /
BASIC‑WL) that legally carries only header totals and **no per‑invoice lines** — when the
system sees one it does **not** pretend the capture is complete: confidence drops to
**medium** and a note asks you to confirm the lines against the PDF. A **scanned /
image‑only** PDF (no text inside) is recovered by **on‑prem OCR** if your server has it
enabled (the draft is then marked as OCR‑sourced — read every figure). Every
upload box is also a **drag‑and‑drop zone**: drag a file from your file manager straight
onto it, or click to browse. The box turns green and shows the file name once it's
attached. Two ways to process: **Extract draft now** (process immediately and review),
or **Queue for later** which parks the file in the **Waiting room** for background
processing (see §3b).

**Every upload is confirmed OK or rejected.** The moment a file arrives it is written
to the durable data lake and immediately **read back and re-hashed** to prove it landed
intact:
- **✓ Upload OK** (green) — the file is safely archived (it can no longer be lost, only
  deleted by a user) and is **sent for processing**. You then review the draft, or it
  goes to the waiting room.
- **✗ Upload failed — batch rejected** (red) — the file could not be stored safely. The
  **bad data is discarded** (nothing is kept and nothing is processed) and you are asked
  to **re-upload the entire batch**. The problem is recorded in the admin error log.

The same OK/failed confirmation applies when attaching an original PDF/scan to an
invoice in the **Documents** vault. Every upload — successful or failed — is recorded
on the **Imports** report.
On confirm, it registers the statement + vaults the source (see §3a). When an AI
backend processes an invoice, its structured output is also archived in a **data lake**
(separate from the PDF) so it can be reused without re-calling the API. Your VAT‑refund
**claim records live in their own isolated database** — protected from the monthly data
rebuild — and are included in every backup.

**Waiting room** — the durable intake queue for uploaded batches (see §3b).

**Invoice control** — two controls on one page (see §4).

**Contracts** — the **contract‑compliance auditor**: checks every invoiced line against
the supplier's contracted discount terms and flags **short discounts** (rebate applied
below contract) and **over‑ceiling** prices, with the **recoverable EUR** per breach to
claw back. Add rules per supplier/country/station (SQL‑LIKE) with an expected discount
(€/L) and/or a max NET price (€/L). Pure data — finds money you're already owed.

**VAT refunds** — the claim matrix and lifecycle (see §5).

**Claims** — the "can we file?" view: per claimable quarter, READY vs BLOCKED with the
exact blocking reasons (activation, missing docs, unresolved refs, threshold), plus the
list of open (submitted, awaiting refund) claims with aging. Export to Excel.

**Monthly close** (`/close`, admin only) — the **one‑click monthly close**. Pick the
period and run it; the close is **enqueued onto the background worker** (it never blocks the
web request) and runs the engine end‑to‑end — consolidate → build master → history → receipt
control → backup — as a single audited, restartable job. Watch progress on the Waiting room
/ Dashboard. (The same close can also be run from the server CLI, `python engine_close.py
[period]` — see §3.)

**Bank reconciliation** (`/recon`, admin only) — **advisory only**. Upload a bank‑statement
**CSV** and the page reconciles its lines against the **expected incoming VAT refunds**
(claims filed but not yet paid), by amount and date, so you can see which refunds have
landed and which are still outstanding. It **never** marks a claim paid or changes any VAT
figure/gate/lock — it shows suggestions only. (An automated read‑only bank feed via a
licensed open‑banking aggregator is a configurable seam; none is enabled by default.)

**UNMATCHED resolution** (`/vat/unmatched`, admin only) — where you clear a claim's
**UNMATCHED** lines. A claim line is tagged UNMATCHED when a transaction's note matches no
registered invoice (and there isn't exactly one obvious invoice for that supplier/country) —
a **hard block on filing**. Here you map that note to an **existing registered invoice**:
this is an **association only** — it never changes a net/VAT amount, and the target is
re‑checked as still‑registered and non‑synthetic at read time. Set/Clear are audited.

**Recovery / Receivables** (admin only) — tracks submitted → approved → paid refund
amounts with aging (unpaid over 120 days flagged red), **and the service‑fee settlement**:
the fee charged per claim, whether we invoice the customer or deduct and remit the net, and
a one‑click **fees statement** (Excel). Issue the fee invoice once a refund is paid (see
§5a). The page also hosts an **embedded‑finance** section (advisory, origination‑only): it
shows the financeable receivable base (the same submitted/approved outstanding total) and
the advance economics at the configured terms (advance %, fee %, provider). The default
provider is **none** — **no money moves**, the figures are informational, and **nothing
here touches a VAT figure, gate, lock or lifecycle**. Terms are set on the page; the
provider seam is admin‑configured (see §5a).

**Pricing intel** — the competitiveness engine. Toggle daily / weekly / monthly;
each supplier's effective NET price per city is compared against three baselines:
your MY Prices (upload as CSV: country,city,date,net_price), the supplier pack (vs
other suppliers same city), and a wholesale index (upload to unlock true margin).
Sorted by EUR impact so the money is at the top; unmatched volume shown openly.
Export the daily/weekly/monthly grid to Excel to build pricing models. All prices
NET final, VAT excluded, rebates applied — stated on the page so it's unarguable.
The **Self‑sourced benchmark** card turns your own data into competitor intelligence:
for each country/period where you used 2+ suppliers, it shows the **best price you
actually achieved** and the **avoidable overpay** vs that best (route volume to the
cheaper supplier you already use) — no external data. **Adopt best‑of as MY benchmark**
loads those prices so the margin/gap columns measure everyone against the best you got.
The **Client portal price scraping** card automates the MY‑Prices benchmark: an admin
adds a supplier portal (a no‑code JSON/CSV config, or a custom adapter) and stores the
entity's login (encrypted at rest); **Scrape now** pulls that account's NET prices
straight into MY Prices (source `portal:<SUPPLIER>`). Use only portals you're
authorized to access; on a locked‑down box run the scraper from a connected machine.

**Anomalies** — a relative scan flagging stations priced above their country average,
month-over-month price jumps, vehicle volume spikes and off-period dates.

**Documents** — the invoice vault (see §6).

**Suppliers / Customers** — the master-data cards. Suppliers: legal identity, VAT
registrations, bank accounts, products, invoice registry. **Customers (CRM, admin
only)** is the mini‑CRM that drives the VAT‑refund lifecycle:
- the **adjustable submission checklist rules** (default: contract, customer data,
  bank account, NACE business activity, trade register, power of attorney) — the
  system verifies these, nobody ticks them by hand, and the claim statuses 1A→1E
  follow them automatically;
- onboarding & **per‑country activation** (request → receive the country's documents);
- **document templates**: upload your own contract / power‑of‑attorney template with
  `{{placeholders}}` (e.g. `{{company_name}}`, `{{reg_number}}`, `{{bank_iban}}`,
  `{{refund_country}}`) as .txt/.html/.md/.docx — the system **generates the document
  per customer** (optionally as PDF), warns on screen about any unfilled fields, and
  can file the draft in the customer's documents;
- documents can carry a **valid‑until date** — an expired power of attorney stops
  satisfying the checklist and is flagged on the dashboard worklist before it expires;
- the **fee terms** (% of refunded VAT, per‑declaration minimum, per‑country
  overrides, and where the refund is paid). Red **INPUT** = data still to be collected.

**Data manager** — direct table editing for master data (processor with data‑import
capability). Pick database →
table; every row is editable inline (Save / Delete), the bottom row adds new
records. Deleted rows are recoverable: their full values stay in History.

**Imports** — the data‑import report: every upload, extraction and statement
registration with its outcome (**received / success / partial / failed**), the user,
file, client, supplier and record count. Filter by channel, status, client, supplier
and date. Append‑only audit of what came in and whether it landed.

**Files** — the permanent file archive (data lake). **Every uploaded file is archived
here on arrival** (SHA‑256, same storage as the PDF vault) and kept forever; it is only
removed by an **explicit Delete** here, or after **Verify integrity** flags it
corrupt/missing — never lost automatically. Whether a file's *data* is processed/used is
a separate concern: the file stays regardless.

**Doc mining** — re‑reads the vaulted documents (PDF text + structured XML), extracts
EU VAT numbers, and **proposes fills for the yellow INPUT gaps** in supplier/customer
master data where the country code matches. Review each proposal and press **Apply**
(audit‑logged) — nothing is written automatically.

**History** — the audit trail of every change in every database: filter by database,
table, **from date / till date**, and record key. Each row shows when, what, the
action, **who (By column)**, and a field-level diff or full snapshot.

**Admin** (admins only) — user management (create users, set roles, reset passwords,
disable accounts) and **adjust processor capabilities**; **Modules — switch whole
parts of the app on/off** (analytics, intake, compliance, VAT refunds, FX): a part
that's off disappears from the menu and its pages answer "turned off" until re‑enabled;
the login log and the **error log**; security status (TLS, password storage); the
backup schedule with one‑click **Run backup**, **Verify last backup** and **Check
document integrity**. The Admin panel also holds:
- **E‑mail alerts (SMTP relay)** — set the SMTP host/port/user/password, a *From*
  address and the **recipients** for the action digest, and the **digest cadence**
  (hours). With a relay configured the system e‑mails the "what needs action" digest on
  that cadence and sends **per‑event critical alerts** (e.g. a VAT submission deadline
  approaching, a stuck/dead‑letter intake job). Until SMTP is set the alerts surface
  in‑app only.
- **Off‑site backup sync** — point a **backup sync folder** (a mounted NAS / synced
  cloud drive) and each snapshot is also copied there, so a backup survives loss of the
  machine. (Can also be set by the `FFS_BACKUP_SYNC_DIR` environment variable — see
  [#install-setup-installation](#install-setup-installation).)
- **AI review backend** — the **advisory** AI review assistant is **off by default**;
  an admin selects a backend here. It sends derived data only (never the PDF/IBAN/secret)
  and never mutates or gates a figure (see [#ai-review-assistant-advisory-validation-analytics](#ai-review-assistant-advisory-validation-analytics)).
- **API keys** — issue/revoke scoped machine keys for the `/api/v1` external API
  (default‑off; see [#external-api-apiv1-token-contract](#external-api-apiv1-token-contract)).

**Confidence scoreboard** (`/admin/confidence`, admin only) — a **read‑only** learning
scoreboard: a per‑(supplier × country) **trust** score that grows with each clean
validation and decays on a discrepancy, plus the recent append‑only validation‑event
ledger. The score is fed by the **deterministic batch validator** at confirm time (the
`validator` source — ground truth) as well as the advisory AI review, so the ledger shows
where the data actually came in clean. Trust governs **only** whether the advisory AI
review runs (a cost saving) — it **never** skips or alters any legal gate (checklist,
thresholds, locks, period‑end, document presence) and never changes a figure.

**Tenants** (`/admin/tenants`, admin only) — a **read‑only** multi‑tenancy registry. The
install is **single‑tenant by default** (the master `multitenant` switch is OFF); this page
makes that explicit and lists no tenant scoping until multi‑tenancy is rolled out. Nothing
here gates or alters any query or figure (see [STRATEGY.md#multi-tenancy-program-plan](STRATEGY.md#multi-tenancy-program-plan)).

### 3. Monthly routine (processor)

Day 1–3 of the new month, when supplier invoices arrive:

1. **Load the data** (server CLI or ask your admin): drop the supplier files, edit
   `month_config.py`, run `consolidate.py` (every supplier must PASS validation
   against its own invoice totals — the system refuses to build on a mismatch),
   then `build_master.py` and `history.py`. *(An admin can instead run the whole
   close in one click from the **Monthly close** page (`/close`) — it enqueues the
   same engine run to the background worker — or from the CLI `python engine_close.py
   [period]`.)*
2. **Invoice control** page → enter the period → Run control. Work the colors:
   - red **MISSING** = activity happened but no invoice registered → chase the supplier
   - red **RECEIVED – DOC MISSING** = invoice registered, PDF not attached → get it,
     attach on the Documents page
   - **NO ACTIVITY** = nothing expected there, fine.
   Check the **orphan transactions** list — fueling not covered by any invoice.
3. **Register each supplier's statement/coversheet** (same page, bottom form):
   supplier code, statement reference, date, then paste one line per issued invoice:
   `invoice_no; date; country; currency; net; vat`. Attach the statement PDF itself
   on the Documents page. The reconciliation table then triages every issued invoice:

   | Verdict | Meaning / your action |
   |---|---|
   | PROCESS – COMPLETE | registered + original on file → flows to the VAT claim. Done. |
   | PROCESS – DOC MISSING | obtain and attach the original PDF |
   | PROCESS – IDENTIFY | read the exact invoice number off the statement, update the registry |
   | PROCESS – NOT REGISTERED | issued per statement but unknown to us → investigate |
   | DISCARD | zero VAT (rebates, settlements) → archive only |
   | DISCARD – DOMESTIC | invoice country = entity's home country → belongs in the regular domestic VAT return, **not** the refund process. Automatic. |

3a. **Or import the whole batch at once** — Import batch page: drop the supplier ZIP
   (coversheet + invoices). The system reads it and shows a **review screen** — parsed
   invoice lines beside the source, every field editable, with a draft gross total to
   check against the coversheet. The screen also gives you two at‑a‑glance trust cues:
   - a **Provenance** badge per line — **structured** (EN‑16931 e‑invoice, trustworthy),
     a parser/file name, or **AI · verify** (an AI‑extracted figure — read it carefully);
   - a **Capture checks** panel (advisory, never blocks) flagging things like a
     **malformed VAT‑ID** or a **duplicate invoice within the batch**, so you catch them
     before committing.

   Fix anything, then **Confirm**: it registers the
   statement, syncs VAT-bearing invoices, and files the PDFs in the vault in one step.
   For recognised suppliers this is offline and instant; for new layouts it uses the
   configured AI extractor (still a draft you confirm). Nothing is saved until you confirm.

3b. **The Waiting room (deferred processing).** Heavy extraction (especially AI) is
   decoupled from upload so a burst of files can't overload the server. Press **Queue
   for later** on Import batch and the file is stored durably on arrival; a background
   worker extracts it one at a time. The Waiting room page shows each job's state —
   *queued → processing → ready* (then review & commit like §3a) — with counts and a
   "Process queued now" button.
   - **No data loss:** the uploaded bytes are written to disk before the job is recorded,
     kept until you've reviewed and committed, and a crash mid‑process is retried.
   - **AI out of tokens/quota:** the job is parked as **waiting** and retried
     automatically every 4 hours, without counting as a failure. After several retries it
     becomes **held** — it stays safely in the waiting room until you top up the API
     credit and press **Send now** (or **Send / restart all** to re‑run the whole
     backlog).
   - **Backlog gate:** while documents are still unprocessed, adding new ones to the
     waiting room is paused so a stuck pile can't grow — an admin can grant a temporary
     override.

4. **Dashboard / Compare / Head-to-head** — review prices, send Stations flags to
   dispatch, note negotiation evidence.
5. **Backup** runs nightly automatically; after a heavy editing day you can run one
   manually (`python3 backup.py`) or from the Admin panel (admins also get a one‑click
   "Verify last backup" and "Check document integrity").

### 4. Reading the receipt control

Expectation = the supplier's **cadence** (from the supplier card: every 14 days,
every 30 days, or monthly-per-country) × **activity** (did transactions actually
happen in that slot/country). So "no invoice from Austria" is only a problem if
there was Austrian fueling — otherwise the row says NO ACTIVITY and nothing is
chased. If a supplier confirms in writing that no invoice exists for a flagged slot,
a processor can set `waived=1` on that row in the Data manager (claims database →
invoice_receipt_control); the waiver survives re-runs.

### 5. VAT refunds, quarter by quarter (admin only)

The whole VAT-refund module — **VAT refunds, Claims readiness, Recovery & fees, and
the Customers (CRM) page** — is visible to **admins only**.

The page shows every stream — **entity × refund country × period (Q1–Q4 + YEAR)** —
with the VAT amount in EUR and local currency, the threshold verdict, document
coverage, home portal, deadline and the **workflow status (1A→5)**.

**Threshold verdicts:** READY (≥ €400 quarterly), DEFER TO ANNUAL (under €400 but the
year total ≥ €50), BELOW ANNUAL MIN (accumulate). Quarters show "months missing"
until all three months are loaded — file only after the quarter is complete.

#### The status workflow (1A → 5)

Every claim carries a status code. The **pre-submission stages are system-controlled**
— nobody can tick them by hand; the claim climbs automatically as the checklist
completes:

| Code | Meaning | Who sets it |
|---|---|---|
| **1A** | Missing documents / checklist incomplete | system |
| **1B** | All documents received — period not ended | system |
| **1C** | Can be submitted (a caveat remains, e.g. defers to annual) | system |
| **1E** | Ready to submit | system |
| **2** | Submitted | you |
| **2A** | Successfully submitted | you |
| **2B** | Document request received *(record the response deadline)* | you |
| **3** | Decision received | you |
| **3A** | Money received *(fee becomes chargeable)* | you |
| **3B** | Rejection *(locks kept — appeal or invoice the fee)* | you |
| **3D** | Under appeal *(locks kept)* | you |
| **3C** | Confiscation by government *(locks kept)* | you |
| **4** | Ready to invoice fee (refund went to the customer) | you |
| **4A** | Ready to invoice credit (refund came to us) | you |
| **5** | Closed | you |

The system checklist behind 1A→1E is **adjustable** (Customers page): by default
*contract, customer data, bank account, NACE business activity, trade register,
power of attorney*, plus the claim-level checks — all invoices received & processed,
all invoice documents attached, and **the claim period has ended** (a hard gate: a
Q2 claim physically cannot be submitted before 30 June).

When you advance a status you can attach a **note** (rejection reason, what was
requested) and — for 2B/3D — a **deadline**; both show on the claim and feed the
dashboard worklist.

**What a claim contains.** A claim is built **from your registered invoices** — every
line ties to **one specific invoice**, and there is **one row per product code** (row 1 =
product code 1, row 2 = product code 2, …), never a combined "ALL" line. A transaction
that can't be tied to a registered invoice shows as **UNMATCHED**, and a claim with any
unmatched or document‑less line **cannot be filed** — resolve it by registering the
invoice and attaching its document (search the vault or upload — see §6). Claim figures
stay **editable** (Data manager → claims), but a row is protected against accidental
change: it opens read‑only and only a deliberate **Edit** + a save **confirm** writes it.

**The quarterly run:**
1. After quarter end, watch the stream reach **1E Ready to submit** (open its
   *checklist* link to see exactly what's missing while it's 1A).
2. Click **Generate claim workbook** (or run `vat_refund.py`) and fix every
   **yellow INPUT cell** — the system refuses to submit otherwise.
3. Confirm document coverage is green ("3/3 docs") — every invoice needs its original.
4. File in the entity's home portal (e-MTA / EDS / Mano VMI), then set the stream to
   **2 Submitted**.

**Locks.** Setting *2 Submitted* **locks every invoice in the claim** — one invoice is
claimed exactly once, ever, including across quarterly vs annual. Rejection (3B),
appeal (3D) and confiscation (3C) **keep the locks** — you contest the decision or
invoice the fee; only an explicit admin **Withdraw (release locks)** frees the
invoices for a corrected re-claim.

**Quarterly vs annual, handled dynamically.** Low‑VAT quarters (under €400) defer into
the **annual** claim, while strong quarters can still be filed quarterly. The annual
claim is the *mop‑up* for whatever wasn't already claimed quarterly: e.g. Q1+Q2 small →
annual, Q3 large → filed as Q3, Q4 → filed as Q4, then the annual claim picks up Q1+Q2
(and any invoices the customer sent late). One invoice is still claimed exactly once.

**Domestic VAT** never appears in these claims — it's discarded at statement triage
(§3) and belongs in the entity's regular home VAT return.

### 5a. Service fees & settlement (Recovery page)

The agency fee is **% of the refunded VAT, floored at a per‑declaration minimum** —
whichever is higher (e.g. 8% of €1,000 = €80, but a €130 minimum → €130 is charged).
Rates are set per customer with optional per‑country overrides on the Customers page.

- The fee **rate is frozen the moment a claim is submitted** — later rate changes only
  affect un‑submitted claims.
- The fee is **charged when the money is received** (status → *3A*), computed on the
  amount actually refunded.
- **Settlement follows where the refund lands** (set per customer): paid to the
  *customer* → advance to **4 Ready to invoice fee** and **issue the fee invoice**;
  paid to *us* → advance to **4A Ready to invoice credit** (we deduct the fee and remit
  the net).
- The Recovery page's **Workflow (2→5)** column shows each claim's status code with
  its decision date / deadline / note and a one‑click **suggested next step** —
  after 3A it offers 4 or 4A (by payout route), then **5 Closed**. Download the
  monthly **fees statement** for the aggregate per customer.

### 6. The document vault

Every invoice must carry its physical document — original PDF or scan. On the
Documents page each known invoice shows its attachments (click to download) or a red
**MISSING** flag with an upload form. Submission of any VAT claim is blocked while a
document is missing. Files are SHA‑256 fingerprinted: re‑uploading the same file is
skipped, and attaching a file that already sits on a *different* invoice triggers a
wrong‑attachment warning.

**How it's organised.** Originals are filed under a logical, human‑navigable tree so
they're easy to locate by hand:

```
<Customer> <RegNo> / <Year> / <Country> / <Claim period Qn|Annual> / <file>
```

The same structure is used whichever backend stores the bytes — **local folder**
(default), **SharePoint**, or **FTP/FTPS** (set by the admin via `DOC_BACKEND`; see
#install-setup-installation). When low‑VAT quarters merge into the annual claim, the documents of the
invoices in that claim are **automatically re‑filed** from their `Qn` folders into the
year's `Annual` folder — so the vault always mirrors the real claim composition.
`verify_documents` (Admin → Check document integrity) re‑hashes every stored file to
detect corruption or a missing original.

### 7. Master data: keeping it right (processor)

- **A supplier changes IBAN / you learn a missing VAT number:** Data manager →
  suppliers → the relevant table → edit → Save. The change is logged with your name
  and flows instantly into claim packs and controls.
- **A new entity (customer):** add the row in customers + a payout bank account +
  its supplier account numbers. Red INPUTs on the Customers page show what's still
  missing; claim packs cannot be submitted until the applicant data is complete.
- **A new supplier:** master data via Data manager (or `supplier_master.py` seed), set
  the **invoice_cadence** (drives receipt control), register its first statement —
  the invoices auto-sync. Transaction-level loading (prices into benchmarks)
  additionally needs a one-time row-map setup in `supplier_specs.py` (see
  [../README.md#file-index-what-every-file-does](../README.md#file-index-what-every-file-does) for where each module lives).

### 8. Excel deliverables — what each file is

| File | Contents |
|---|---|
| **Fleet_Fuel_Master_<month>.xlsx** | Runbook, supplier specs, all transactions, diesel benchmark, interactive supplier comparison (edit the **blue cells**), head-to-head, entity & VAT view, payment calendar, station scorecard |
| **Fleet_Fuel_History_Report.xlsx** | Month-over-month trends from the database: prices, litres, entity VAT, station drift, plus a DB guide with ready-made SQL |
| **VAT_Refund_Claims_<year>.xlsx** | Claim overview + one filing-ready pack per stream; **yellow cells = INPUT to complete**, orange = excluded/locked elsewhere |
| **VAT_Claim_Readiness_<year>.xlsx** | The Claims page export: "Ready to submit" (with blocking reasons) + "Open claims" (aging) sheets |
| **VAT_Fees_Statement_<year>.xlsx** | The Recovery page export: charged fees and net remittances aggregated per customer, plus a per-claim detail sheet |
| **Pricing grid (daily/weekly/monthly)** | The Pricing intel export for building pricing models — effective NET price per supplier/city vs your baselines |
| **Expense report (Excel)** | The Expenses page export — net / VAT / gross spend per entity (cost centre) and per vehicle (`/export/expenses`) |
| **Accounting ledger (CSV)** | One row per transaction for accounting/ERP import — decision‑free, no chart‑of‑accounts (`/export/accounting`) |
| **SAF‑T (XML)** | The OECD SAF‑T **core structure** for a period (`/export/saft`) — specialise per jurisdiction before any real filing; not a validated submission |
| **Price‑review packet (Excel)** | The Savings page export — per‑fuelling‑day overpay detail behind each supplier's renegotiation case (`/export/overpay`) |

Convention everywhere: **blue cells** are interactive inputs you may change;
**yellow cells** are missing data you must supply; nothing else should be edited by
hand — regenerate instead.

### 9. "How do I…" quick answers

- **…see what changed last week and who did it?** History → pick database → from/till
  dates → the By column names the user.
- **…undo a deletion?** History → find the DELETE row → its snapshot holds all
  values → re-add them via Data manager.
- **…check if we can already file Belgium for Vestroidas?** VAT refunds page: the
  stream must be READY, no "months missing", docs green — then file and set submitted.
- **…know which supplier to use in Belgium tomorrow?** Head-to-head (structural
  answer) + Stations (which exact stations to prefer/avoid).
- **…add a colleague with limited access?** Admin → Create user → role *processor*,
  then untick the capabilities they shouldn't have (e.g. leave only VAT claims).
- **…handle a tax office rejection?** Advance the claim to **3B Rejection** — the
  invoice locks are **kept** so you can appeal (**3D**) or invoice the fee. Only use
  **Withdraw (release locks)** if you must re-claim from scratch, then fix and refile
  (see §5).
- **…prove an invoice was only claimed once?** VAT workbook pack: the Duplicate
  control column; or query `vat_claimed_invoices` — each ref appears exactly once.

### 10. What the system will refuse to do (by design)

- Build reports from data that doesn't reconcile to the invoices (validation FAIL stops the pipeline)
- Submit a claim with INPUT invoice refs, missing documents, or an invoice already claimed elsewhere
- Revert a submitted claim to draft without an explicit reject/withdraw
- Let a processor do server/user administration, or any user disable their own admin account
- Include domestic-VAT invoices in refund claims
- Record any change anonymously, or store a password in readable form

If you hit a red BLOCKED banner, it is one of these guardrails — the message says
which, and the fix is always listed in §5 or §6.

---

## External API — `/api/v1` (token contract)

> **This is the short overview. The authoritative, end-to-end reference — auth, key
> issuance, per-endpoint curl examples, the CRM playbook, the error/versioning/security
> model — is the [Integrator manual](#integrator-manual-apiv1-external-token-api) below;
> the machine-readable contract is [`docs/openapi.yaml`](openapi.yaml) (OpenAPI 3.1). The
> committed code is the source of truth for all three.**

A small, **versioned** machine API over the platform's analytics capabilities and
a **basic CRM-sync surface** (read + write to customer master). It is a clean
external contract: **token-only** (never the session cookie), scoped per endpoint,
metered per key, **default-off** (no keys → every call `401`; an admin issues a key
in *Admin → API keys*). The internal session-authed `/api/*` routes (used by the app's
own UI) are unchanged and are **not** part of this contract.

The seven endpoints (each requires exactly one scope; see the [scope table](#scopes) and
[endpoint reference](#2-endpoint-reference) in the integrator manual):

| Scope | Endpoint(s) |
|---|---|
| `api:benchmark` | `GET /api/v1/benchmark` |
| `api:claims` | `GET /api/v1/claim-status` |
| `api:savings` | `GET /api/v1/savings` |
| `api:crm` | `GET /api/v1/customers`, `GET /api/v1/customers/<code>` (read) |
| `api:crm.write` | `POST /api/v1/customers`, `PATCH /api/v1/customers/<code>` (write) |

All prices are **NET EUR/L, VAT excluded, rebates applied**; EUR figures are quantized
(`money.f2`, HALF_UP). No v1 payload carries IBAN / bank / payout / fee / secret / PII.

#### Not in the API: session-auth UI exports

Several useful exports are reached with the **browser session cookie** (the
logged-in UI), **not** a `/api/v1` token, and are therefore **outside** this
contract — do not call them with a bearer key. They are documented in
`#user-manual-fleet-fuel-vat-refund-system`, not here:

| Route | What it is |
|---|---|
| `/expenses`, `/export/expenses` | Company expense / cost-allocation report (page + Excel) |
| `/export/accounting` | Accounting / ERP ledger (CSV, one row per transaction) |
| `/export/saft` | SAF-T export (OECD core structure XML — not a validated per-country filing) |
| `/savings`, `/export/overpay` | Supplier price-review packet (page + Excel) |
| `/close`, `/recon`, `/vat/unmatched`, `/receivables` | Monthly close, bank reconciliation, UNMATCHED-resolution, receivables/financing (admin-only UI) |
| `/admin/confidence`, `/admin/tenants` | Confidence scoreboard, multi-tenancy registry (admin-only UI) |

These were added as UI pages/downloads — the `/api/v1` token surface itself did
**not** change (still the seven endpoints above).

---

## Integrator manual — `/api/v1` (external token API)

This is the **human integrator guide** for the versioned external API. The
**machine-readable contract** is [`docs/openapi.yaml`](openapi.yaml) (OpenAPI 3.1) —
import it into your CRM connector / codegen tool. For the short prose overview see
[`#external-api-apiv1-token-contract`](#external-api-apiv1-token-contract); this manual is the authoritative, end-to-end reference and the
two do not contradict (the **committed code is the source of truth** for both).

The API exposes two capability groups:

- **Analytics reads** — benchmark, VAT claim status, savings intelligence (read-only).
- **Basic CRM sync** — read + write the in-app customer master, so an outsourced /
  external CRM can keep it in step. It is deliberately minimal: richer CRM duties are
  intended to live **in that external CRM**, delegated to it via this seam, not rebuilt
  in the app.

It is a clean machine contract: **token-only** (never the session cookie), **scoped per
endpoint**, **metered per key**. The internal session-authed `/api/*` routes used by the
app's own UI are NOT part of this contract.

> **Not in the API — session-auth UI exports.** The platform also offers browser
> (session-cookie) exports that are **outside** `/api/v1` and must not be called with a
> bearer token: `/export/expenses` (Excel expense report), `/export/accounting` (CSV
> accounting/ERP ledger), `/export/saft` (SAF-T XML — OECD core structure, not a
> validated per-country filing), `/export/overpay` (price-review packet), and the
> session-auth pages `/expenses`, `/savings`, `/close`, `/recon`, `/vat/unmatched`,
> `/receivables`, `/admin/confidence`, `/admin/tenants`. These are end-user UI features
> (see `#user-manual-fleet-fuel-vat-refund-system`), not machine endpoints. The `/api/v1` token surface itself
> is unchanged — still the seven endpoints in §2.

---

### 1. Authentication & key issuance

#### Default-off

No API keys exist on a fresh install, so the whole `/api/v1` surface is **inert** — every
request returns `401` until an admin issues a key. There is no anonymous access and no
implicit key.

#### Minting a scoped key (admin)

Keys are minted from the **Admin panel** (admin-only — the `/admin` route):

1. Open **Admin → "API keys (machine access — /api/v1)"** card.
2. Enter a **label** (free text, for your own bookkeeping) and tick the **scopes** the
   key should carry (one checkbox per scope — see the table below).
3. Submit **"+ Issue API key"**.
4. The plaintext token is shown **exactly once**, inline, as a copy-me code block:

   > API key #N issued (label). **Copy it now — it is shown only once and cannot be
   > recovered:** `…token…`

   Copy it immediately. The server stores only the token's **SHA-256 hash**; the
   plaintext is never recoverable. If you lose it, revoke the key and issue a new one.

Internally `api_keys.issue(label, scopes, owner)` generates a 256-bit token
(`secrets.token_urlsafe(32)`), persists only `sha256(token)` in a BLOB column in
`security.db`, and records the issuing admin as `owner`. Issuance and revocation are
audit-logged.

#### Presenting the token

Send the token on **every** request, by either header (both are accepted and equivalent):

```
Authorization: Bearer <token>
```
or
```
X-API-Key: <token>
```

`Authorization: Bearer` takes precedence if both are present. The token is compared
**constant-time** (`secrets.compare_digest`) against every active key's stored hash, so a
wrong token cannot be distinguished by timing.

#### 401 vs 403 (the auth posture)

| Situation | Status |
|---|---|
| Valid key that holds the endpoint's required scope | `200` / `201` (success) |
| Missing / malformed / unknown token | `401` |
| Revoked key | `401` |
| No keys issued at all (default-off) | `401` |
| Valid key that **lacks** the endpoint's required scope | `403` |

A `401` is a **no-oracle** response: it never reveals whether the token was simply
unknown vs. revoked. A `403` names the scope the key is missing
(`key not authorized for scope <scope>`).

#### Scopes

A key carries a **set** of scopes. Each endpoint requires **exactly one** scope, checked
server-side **regardless of HTTP method** (so a read-only key can never write). The full
issuable vocabulary (`api_keys.SCOPES`):

| Scope | Grants |
|---|---|
| `api:benchmark` | `GET /api/v1/benchmark` — read the internal price benchmark summary |
| `api:claims` | `GET /api/v1/claim-status` — read VAT claim status / readiness (non-sensitive fields only) |
| `api:savings` | `GET /api/v1/savings` — read the savings-intelligence summary |
| `api:crm` | `GET /api/v1/customers`, `GET /api/v1/customers/{code}` — read customer master for CRM sync |
| `api:crm.write` | `POST /api/v1/customers`, `PATCH /api/v1/customers/{code}` — create / update customer master |

`api:crm.write` does **not** imply `api:crm`: grant both on a key that needs to both read
and write the customer master.

---

### 2. Endpoint reference

Base URL is **deployment-relative**: the API lives at `<your-host>/api/v1`. The examples
below use `https://host` as a placeholder. All EUR figures are quantized HALF_UP
(`money.f2`); all prices are **NET EUR/L, VAT excluded, rebates applied**.

The uniform error body for every non-2xx response is `{"error": "<message>"}`.

#### 2.1 `GET /api/v1/benchmark` — scope `api:benchmark`

Internal price benchmark per supplier/country (Diesel) for a period. `?period=YYYY-MM`
is optional; omit it for the latest available period.

```bash
curl -s https://host/api/v1/benchmark?period=2026-03 \
  -H "Authorization: Bearer $TOKEN"
```

```json
{
  "period": "2026-03",
  "basis": "NET EUR/L (VAT excluded)",
  "rows": [
    {"supplier": "Neste", "country": "LT", "litres": 12345, "doc": 1.2345, "eff": 1.2300}
  ]
}
```

`doc` = document NET EUR/L (`SUM(net_eur)/SUM(qty)`, 4 dp); `eff` = effective NET EUR/L
after rebates (`SUM(net_eur_eff)/SUM(qty)`, 4 dp). `period` is `null` when no data exists.

#### 2.2 `GET /api/v1/claim-status` — scope `api:claims`

VAT claim workflow status / readiness per (entity, country, period) for a calendar year.
`?year=YYYY` is optional (defaults to `2026`). Each (entity, country) yields the quarters
`Q1`..`Q4` plus a `YEAR` annual roll-up. **Non-sensitive fields only** — never IBAN /
payout / fee / bank / PII.

```bash
curl -s https://host/api/v1/claim-status?year=2026 \
  -H "X-API-Key: $TOKEN"
```

```json
{
  "claims": [
    {
      "entity": "BALTIC TRANSPORT OU",
      "country": "PL",
      "period": "Q1",
      "vat_eur": 1234.56,
      "vat_local": 1234.56,
      "currency": "EUR",
      "lines": 42,
      "verdict": "READY (>= EUR 400 quarterly min)",
      "missing": [],
      "deadline": "30 Sep 2027"
    }
  ]
}
```

`missing` lists months of the quarter with no loaded data. `verdict` is one of the
readiness strings (`READY …` / `DEFER TO ANNUAL …` / `BELOW ANNUAL MIN …`).

#### 2.3 `GET /api/v1/savings` — scope `api:savings`

The savings-intelligence summary: avoidable overpay + recoverable contract €, addressable
total, per-country breakdown, an anomaly count, and the top-€ actions. `?period=YYYY-MM`
is optional.

```bash
curl -s https://host/api/v1/savings?period=2026-03 \
  -H "Authorization: Bearer $TOKEN"
```

```json
{
  "period": "2026-03",
  "avoidable_overpay_eur": 4210.00,
  "recoverable_contract_eur": 1875.50,
  "anomaly_count": 3,
  "total_addressable_eur": 6085.50,
  "by_country": [
    {"country": "LT", "overpay_eur": 3000.00, "recover_eur": 500.00, "addressable_eur": 3500.00}
  ],
  "top_actions": [
    {"kind": "Route volume to cheaper supplier", "country": "LT",
     "detail": "LT Diesel: best Neste @ 1.2300 €/L vs your avg 1.2600 (10,000 L)",
     "eur": 300.00}
  ]
}
```

`period` is `null` when no data exists. Anomalies carry no € — they appear only as
`anomaly_count`.

#### 2.4 `GET /api/v1/customers` — scope `api:crm`

List the customer master (core, non-secret fields), ordered by `code`.

```bash
curl -s https://host/api/v1/customers \
  -H "Authorization: Bearer $TOKEN"
```

```json
{
  "customers": [
    {"code": "ACME", "company_name": "Acme OU", "country": "LT",
     "status": "active", "active": true, "countries_active": ["LT", "PL"]}
  ]
}
```

`active` is `true` when `status == "active"`. `countries_active` lists the customer's
countries whose per-country registration is `active`.

#### 2.5 `GET /api/v1/customers/{code}` — scope `api:crm`

One customer's detail: core fields + the activation checklist + per-country status. The
`code` is trimmed and upper-cased server-side. `404` if unknown.

```bash
curl -s https://host/api/v1/customers/ACME \
  -H "Authorization: Bearer $TOKEN"
```

```json
{
  "code": "ACME",
  "company_name": "Acme OU",
  "country": "LT",
  "status": "active",
  "reg_number": "123456",
  "vat_number": "LT123456789",
  "legal_address": "Gedimino pr. 1, Vilnius",
  "home_portal": "https://portal.example.lt",
  "phone": "+370 5 555 1234",
  "email": "ops@acme.lt",
  "nace_code": "4941",
  "active": true,
  "is_active": true,
  "activation_checklist": [
    {"label": "Trade registry extract", "ok": true}
  ],
  "countries": [
    {"country": "LT", "status": "active"}
  ]
}
```

`is_active` is an alias of `active`. This same **detail shape** is the body returned by a
successful `POST` (201) and `PATCH` (200).

#### 2.6 `POST /api/v1/customers` — scope `api:crm.write`

Create a customer. **Required** (non-empty after trim): `code`, `company_name`,
`country`. Optional real fields (`reg_number`, `vat_number`, `legal_address`,
`home_portal`, `phone`, `email`) are written immediately, so an API-onboarded customer
carries real values rather than the `INPUT:` placeholders the seed would otherwise set.
`code` is normalised to upper-case.

```bash
curl -s -X POST https://host/api/v1/customers \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"code":"ACME","company_name":"Acme OU","country":"LT",
       "reg_number":"123456","vat_number":"LT123456789","email":"ops@acme.lt"}'
```

- `201` — created; body is the GET-detail shape (section 2.5).
- `400` — a required field is missing/empty, or a supplied optional field failed
  validation (`{"error": "code, company_name and country are required"}`).
- `409` — a customer with that `code` already exists
  (`{"error": "customer ACME already exists"}`).

#### 2.7 `PATCH /api/v1/customers/{code}` — scope `api:crm.write`

Update any subset of the **editable allowlist** (`customer_master.EDITABLE_FIELDS`):
`company_name`, `reg_number`, `vat_number`, `legal_address`, `home_portal`, `phone`,
`email`, `nace_code`, `signatory_name`, `signatory_title`. Keys outside the allowlist are
**ignored** (not an error). The write never touches `status` / fee / route or any other
workflow column, and never runs raw SQL from the body.

```bash
curl -s -X PATCH https://host/api/v1/customers/ACME \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"email":"new@acme.lt","phone":"+370 5 555 1234"}'
```

- `200` — updated; body is the GET-detail shape.
- `400` — no editable field supplied, or a supplied field is empty/whitespace
  (`{"error": "email cannot be empty"}` / `{"error": "no editable fields supplied"}`).
- `404` — no customer with that `code` (`{"error": "customer ACME not found"}`).

---

### 3. CRM integration playbook

The CRM seam lets an outsourced / external CRM own richer customer-relationship duties
while keeping the in-app customer master (which feeds VAT claims) in sync. Two directions:

#### PULL (sync into your CRM)

1. **List** with `GET /api/v1/customers` (scope `api:crm`) to enumerate codes + core
   fields + the `active` flag + `countries_active`.
2. **Hydrate** each with `GET /api/v1/customers/{code}` for the full detail, including the
   `activation_checklist` (so your CRM can show onboarding progress) and per-country
   `status`.

Poll on whatever cadence suits you — the reads are cheap and metered.

#### PUSH (maintain the master from your CRM)

1. **Create** new customers with `POST /api/v1/customers` (scope `api:crm.write`). Send
   the three required fields plus whatever optional real fields you hold.
2. **Update** existing customers with `PATCH /api/v1/customers/{code}` — send only the
   fields that changed; unknown / non-editable keys are ignored.

#### Audit attribution

Every CRM **write** is audit-attributed in `customers.db` as
`changed_by = api:<key-label>` (or `api:<key-id>` when the key has no label), resolved
from the authenticated key. The actor is always reset after the request, even on error.
Use a **distinct, well-labelled key per integration** so the audit trail names the source.

#### Idempotency & duplicate handling

`POST` is **not** idempotent on `code`: re-posting an existing `code` returns `409`, it
does not overwrite. The safe pattern is **create-or-update**:

```
POST /customers            -> 201  (new)            ... done
                           -> 409  (already exists) ... then PATCH /customers/{code}
```

`PATCH` is naturally idempotent (setting the same fields twice yields the same state).

#### Read-key-cannot-write

A key holding only `api:crm` gets `403` on `POST`/`PATCH` — the scope check is per
endpoint, independent of HTTP method. Mint a key with `api:crm.write` for write access
(and add `api:crm` too if the same key also reads).

---

### 4. Error model

All non-2xx responses share one shape:

```json
{"error": "<human-readable message>"}
```

Status codes used across the surface:

| Code | Meaning |
|---|---|
| `200` | Success (GET; PATCH update) |
| `201` | Customer created (POST) |
| `400` | Bad request — missing/empty required field, no editable fields, validation failure |
| `401` | Missing / malformed / unknown / revoked token; or default-off (no keys) |
| `403` | Valid key without the endpoint's required scope |
| `404` | Unknown customer `code` |
| `409` | Duplicate `code` on create |

(`500` `{"error": "internal error"}` is returned only on an unexpected server fault; it is
logged to the app + admin error logs.)

---

### 5. Usage metering

Every `/api/v1` call is metered to `api_usage` (key id, endpoint, timestamp, final HTTP
status), and the key's `last_used` is bumped on each successful verification.
Unauthorized calls are metered too (with their `401`/`403`). The Admin panel's API-keys
card shows each key's **call count** and **last call** time. There is currently **no rate
limit or quota** (a backlog item).

---

### 6. Versioning

- The contract is **`v1`** — pinned in the path prefix `/api/v1` and in
  `docs/openapi.yaml` (`info.version`).
- Changes within `v1` are **additive only**: new endpoints, new optional response fields,
  new optional request fields. Clients MUST tolerate **unknown response fields** (do not
  fail on extra keys) so additive growth never breaks you.
- Anything breaking (removing/renaming a field, changing a status code or required input)
  would ship under a new version prefix, not silently within `v1`.

---

### 7. Security notes

- **TLS.** Tokens are bearer credentials — always call over HTTPS. The app serves HTTPS
  when a TLS certificate is configured; terminate TLS in front of it otherwise.
- **At rest.** Only the SHA-256 hash of a token is stored (`security.db`); the plaintext
  is shown once at issue and is unrecoverable. Constant-time comparison on verify.
- **Least scope.** Grant a key only the scopes it needs. Keep read and write integrations
  on separate keys where practical; never put `api:crm.write` on a key that only reads.
- **Rotation & revocation.** Revoke a key from the Admin panel ("revoke"); it fails on its
  **next** call (`401`), immediately. Rotate by issuing a new key, cutting over, then
  revoking the old one.
- **No PII / secrets in payloads.** No v1 payload carries IBAN / bank / payout / fee /
  secret / PII — neither analytics nor CRM. Writes only touch an allowlist of editable
  customer columns via parameterized writers.
- **Additive, isolated.** The token path owns only `/api/v1/*`; it never weakens or
  bypasses session auth on any other route.

---

### 8. The machine contract

The authoritative, importable schematic is **[`docs/openapi.yaml`](openapi.yaml)** —
OpenAPI 3.1. Its endpoint→scope map mirrors `app.API_V1_SCOPE` exactly; its schemas mirror
the JSON the views return. Point your CRM connector / code generator at it.

---

## SAF-T export — programmable OECD-core generator (prework)

`saft.py` builds an **OECD-SAF-T core structure** (the AuditFile skeleton common to
the national variants) parameterised by a **CountryProfile**, so any jurisdiction can
be specialised later without rewriting the generator.

> The output is a **CORE STRUCTURE, not a validated submission** for any specific tax
> authority. Every file carries this caveat as a top-level XML comment and a
> `Header/HeaderComment` element (`saft.CORE_BANNER`). To file for a country you plug
> in that country's profile (namespace/version/required fields) AND validate against
> the authority's published XSD.

Read-only over the engine-owned product DB. It reuses `queries.q_ledger` — the SAME
ledger math the accounting CSV uses — so the XML totals **reconcile** with
`queries.q_expense`. Basis: **NET EUR, final (rebates applied); gross = net + VAT.**
Stdlib only (`xml.etree.ElementTree`; ET handles XML escaping). No new dependency.

### Entry point

`saft.build_saft(period=None, entity=None, profile=None) -> (filename, xml_bytes)`
- `period` defaults to the latest loaded period; `entity` (a company name) optionally
  restricts the file; `profile` defaults to `DEFAULT_PROFILE`.
- Raises `ValueError("no data loaded - nothing to export")` when there is no
  data/period (or no rows for the entity/period).
- Web: `GET /export/saft?period=&entity=&profile=` (gated `exports` / analytics
  module; not admin-only). A button sits on `/expenses` next to the accounting CSV.

### Data -> SAF-T element mapping (core)

| SAF-T element | Source |
|---|---|
| `Header/AuditFileVersion` | `profile.audit_file_version` |
| `Header/AuditFileCountry` | entity country (`customer_master`), else "" |
| `Header/AuditFileDateCreated` | today (ISO) |
| `Header/SoftwareCompanyName`, `SoftwareID`, `ProductID` | this platform |
| `Header/Company/RegistrationNumber` | `customer_master.reg_number` (entity) / `INPUT` |
| `Header/Company/Name` | `customer_master.company_name` / `(all entities)` |
| `Header/Company/TaxRegistration/TaxRegistrationNumber` | `customer_master.vat_number` |
| `Header/Company/Address/Country` | `customer_master.country` |
| `Header/DefaultCurrencyCode` | `profile.currency` |
| `Header/SelectionCriteria/Period*`/`Selection*Date` | min/max ledger `date` for the period |
| `MasterFiles/Suppliers/Supplier` | one per distinct ledger `supplier` (code/name) |
| ` …/TaxRegistration/TaxRegistrationNumber` | `supplier_master.get_issuer` VAT id |
| `MasterFiles/TaxTable/TaxTableEntry` | one per distinct ledger `vat_rate_pct` |
| `GeneralLedgerEntries/NumberOfEntries` | row count emitted |
| `GeneralLedgerEntries/TotalDebit` / `TotalCredit` | SUM(net_eur) / SUM(vat_eur) |
| `…/Journal/Transaction` | one per ledger ROW |
| `Transaction/TransactionDate` | ledger `date` |
| `Transaction/Description` | `note` / `product` / `product_group` |
| `Transaction/SupplierID`, `Currency` | ledger `supplier`, `currency` |
| `…/DebitLine/DebitAmount` + `TaxInformation/TaxAmount` | `net_eur`, `vat_eur` |
| `Transaction/NetAmount`/`TaxAmount`/`GrossAmount` | `net_eur`, `vat_eur`, `net+vat` |

SelectionCriteria period bounds come from the ACTUAL ledger date range (a fuelling can
fall just outside the label month) rather than parsing the period string.

The transactions section is `GeneralLedgerEntries` (the core picks GL over
`SourceDocuments`); **one `Transaction` per ledger row** — never an aggregate.

### CountryProfile seam — adding a real country

```python
@dataclass(frozen=True)
class CountryProfile:
    code, namespace, schema_version, audit_file_version,
    file_name_pattern="SAFT_{code}_{entity}_{period}.xml",
    sections=("Header","MasterFiles","GeneralLedgerEntries"),
    currency="EUR"
```

`DEFAULT_PROFILE` ("OECD") is **generic** — placeholder namespace
(`urn:oecd:ces:std:saf-t:core:generic`) and version, clearly marked. Register real
profiles in `PROFILES`; `get_profile(code)` resolves them (unknown -> DEFAULT_PROFILE,
warned, not raised). `sections` lets a profile drop blocks it doesn't need.

### What's still needed for a VALID submission per country

A profile only abstracts namespace/version/file-name/sections. For a real filing each
jurisdiction also needs the **correct XSD target namespace + schema version, its
required elements and ordering, and validation against the authority's published
schema**. Examples of the work that remains:

- **Lithuania (SAF-T / i.SAF-T, v2.01):** VMI namespace/version, the LT-required
  MasterFiles/GLEntries shape; validate vs the VMI XSD.
- **Poland (JPK / JPK_VAT):** a different schema family — the element set and XSD
  differ entirely; a profile abstracts only namespace/version/sections.
- **Portugal (SAF-T PT):** the original SAF-T; `AuditFileVersion` and required
  Header/MasterFiles fields per Portaria; validate vs the AT XSD.

This module gives you the reconciled core; the profile + XSD validation make it real.

### Reconciliation guarantee

Every ledger row becomes exactly one GL `Transaction`; **no row is silently dropped**
(a single malformed row is skipped AND logged via `applog.get("saft")`). Each row's
amounts are `money.f2`-quantized (the SAME per-line quantization as `q_ledger`), so
the XML's `TotalDebit`/`TotalCredit` (SUM net / SUM VAT) tie to
`queries.q_expense(period).totals` within per-line rounding (`|diff| <= 0.01 * n`),
the same tolerance the accounting-CSV reconciliation asserts. If the totals ever fail
to tie, that is a sign the entries diverge from the books — stop, don't ship.

---

## AI review assistant — advisory validation & analytics

A post-extraction assistant that helps a reviewer sanity-check an invoice **without ever
sending the source document anywhere**. It is **off by default**, **advisory only**, and
**never touches a figure, a status, or the commit gate**. Module: `ai_review.py`; surfaced
on the draft-review screen.

### The principle
Extraction and reasoning are different jobs with different risk. Capture stays
**deterministic** (e-invoice XML, Factur-X/ZUGFeRD embedded XML, the parser registry — see
`extract.py`). AI moves entirely to the **post-extraction** side, where it reasons about
data that is already structured and checkable:

```
deterministic extraction → deterministic hard-validation → AI advisory flags + analytics → human sign-off
```

### What is sent to AI — and what is NOT
The payload is **minimized derived data**, built by `ai_review.build_payload()`:

**Sent** (a fixed, allow-listed field set):
- the extracted invoice fields and, per line, `{invoice_no, date, country, currency, net, vat}`;
- supplier context — expected VAT, name/aliases, and the **contract price terms**
  (`expected_discount_eur_l` / `price_ceiling_eur_l`, NET EUR/L, from `supplier_discounts`,
  keyed exactly as `contract_audit.py`);
- customer **identity only** (name);
- a recent **price range** from history;
- the expenditure codes and reconciling-transaction summaries;
- `deterministic_findings` — the output of `validate.validate_batch` (so the model is told
  what was already checked and must not redo it).

**Never sent:**
- ❌ the **PDF / scan / document image** — `_pdf_bytes` and **any** key starting `_` are
  dropped unconditionally (recursively);
- ❌ **bank / secret / personal** fields — every key in `REDACT_FIELDS`
  (`iban, swift, bic, bank, account, account_number, beneficiary, password, secret,
  api_key, contact_name, contact_email, phone`) is stripped recursively; a field is sent
  only when a caller passes an explicit `needs=(...)` allow-list for a specific check;
- ❌ the agency **service-fee** terms (`fee_pct`/`fee_min`) — irrelevant to invoice
  validation;
- ❌ anything from `vat_claims.db` on the draft screen.

The original PDF stays in the document vault, on the server.

### What AI returns
Structured JSON only: `{"flags": [{field, severity (info|warn|error), message, suggestion?}],
"note": str|null}`. Flags are the **fuzzy** layer the deterministic rules can't codify —
supplier alias/name mismatch, VAT-id plausibility, price-vs-history anomaly, "this looks
wrong." `note` is a short analytics narrative over the exact figures. The model is
instructed **not** to recompute VAT arithmetic, totals, thresholds, or expenditure codes.

### Hard checks stay deterministic (AI never owns them)
VAT arithmetic (`net × rate = vat`), totals/coversheet reconciliation, the 2008/9/EC
thresholds, Art. 9 expenditure codes, doc-presence, synthetic-line gates — all remain in
`validate.py` and the `vat_refund`/`invoice_control` gates. The AI result feeds **no** gate:
`/extract/confirm`, `validate_batch`, and `can_commit` are unchanged. AI flags are shown to
a human; accept/reject is the human's decision.

### Configuration & privacy
- **Setting `ai_review_backend`** (Admin panel) — `none` (default, OFF) / `claude` / `openai`
  / `azure`. When `none`, `review()` returns the deterministic block and makes **zero**
  network calls. Reuses the extractor's existing API keys/env; Azure stays in-tenant.
- **Audit/provenance** — each non-`none` run archives the **sent field-keys** (not values)
  plus the response via `data_lake.put(kind="ai_review")`; the panel shows the backend,
  model, and "advisory only — nothing was changed."

### Surfaces & scope
v1 is on the **draft-review screen** (`_review_form`, route `/extract/ai-review`,
capability `data_import`, module `intake`). Registered-invoice / VAT-claim review surfaces
are a deferred follow-up (those are admin-only VAT surfaces). Every rendered value is
`esc()`-escaped; any interactivity is CSP-clean from `/app.js`.

---

## EU cross-border VAT refund — rules reference (Directive 2008/9/EC)

The compliance reference the VAT-refund module must honour. It states the verified legal
parameters, the standardised expenditure codes, country-level diesel recoverability, and a
**cross-check against what `vat_config.py` / `vat_refund.py` actually encode** — including
issues to fix.

> **Sourcing caveat.** Compiled June 2026 via multi-source web research. Direct page fetches to
> EUR-Lex and tax-authority sites were blocked (HTTP 403) in the research environment, so
> verbatim article text came from search-engine extracts of those same official pages,
> cross-corroborated across multiple independent sources. Figures are internally consistent and
> multiply-confirmed; **before citing in a filing, confirm exact wording against the EUR-Lex PDF**
> (`https://eur-lex.europa.eu/legal-content/EN/TXT/PDF/?uri=CELEX:32008L0009`). Per-country and
> sub-code specifics from commercial VAT-reclaim vendors are flagged MEDIUM confidence.

---

### 1. The core parameters (what the engine gates on)

| Rule | Value | Article | In our code |
|------|-------|---------|-------------|
| **Eligibility** | Taxable person **not established** in the refund state, who made **no supplies** there in the period — except exempt transport/ancillary services and reverse-charge supplies | Art. 3 (+ Art. 4 exclusions) | implicit (hauliers qualify) |
| **Procedure** | **One electronic application** filed via the **home-state portal**, which validates & forwards to the refund state; no paper invoices up front | Art. 7 | `vat_config.HOME_PORTAL` ✓ |
| **Refund period** | Min **3 calendar months** (a quarter), max **1 calendar year**; a shorter period only if it's the **remainder of the year** | Art. 16 | quarterly + annual + dynamic merge ✓ |
| **Minimum amounts** | **€400** for a 3-month-to-<year period; **€50** for a full year or remainder | Art. 17 | `MIN_QUARTER, MIN_ANNUAL = 400, 50` ✓ |
| **Deadline** | **30 September of the year following** the refund period — a **strict, fatal time-bar** (CJEU C-294/11 *Elsacom*: miss it → right forfeited) | Art. 15 | `filing_deadline()` + hard `period_ended()` gate ✓ |
| **Local currency** | Non-euro refund states set local-currency equivalents of €400/€50; claims filed in the refund state's currency | Art. 17 | `LOCAL_CCY_INPUT`, compliance notes ✓ (EE/LV/LT are euro, so literal figures apply) |

### 2. Required application content (Art. 8 — all mandatory; a claim is "submitted" only when complete, Art. 15)

- **Application-level (Art. 8(1)):** name & address; electronic contact; **business-activity description via harmonised NACE codes** (Art. 11); refund period; the **"no supplies" declaration** (with the Art. 3(b) carve-outs); VAT/tax-reference number; **bank account IBAN + BIC**.
- **Per-invoice (Art. 8(2)):** supplier name/address; supplier VAT number + refund-state prefix; invoice date & number; **taxable amount and VAT amount in the refund state's currency**; **amount of deductible VAT**; **deductible proportion as a % where applicable** (Art. 8(2)(g)); **nature of goods/services by the Art. 9 expenditure code**.

### 3. The expenditure codes (Art. 9; sub-codes per Reg. (EC) 1174/2009 → Reg. (EU) 79/2012 Annex III)

**Top-level codes 1–10** (each application line carries one):

| Code | Meaning |
|------|---------|
| **1** | **Fuel** |
| 2 | Hiring of means of transport |
| 3 | Expenditure relating to means of transport (other than codes 1 & 2) |
| **4** | **Road tolls and road user charge** |
| 5 | Travel expenses (taxi, public transport) |
| 6 | Accommodation |
| 7 | Food, drink and restaurant services |
| 8 | Admissions to fairs and exhibitions |
| **9** | **Expenditure on luxuries, amusements and entertainment** |
| **10** | **Other** |

**Fuel sub-codes (code 1)** — the part that signals *vehicle type* to the refund authority. A
refund state that opts into Art. 9(2) requires these:

- **1.1 — Fuel for means of transport with a mass GREATER than 3 500 kg** (other than for paying passengers) → **1.1.1 petrol · 1.1.2 diesel · 1.1.3 LPG · 1.1.4 natural gas · 1.1.5 biofuel**
  → **A haulier's truck diesel is code `1.1.2`.**
- 1.2 — Fuel for means of transport **≤ 3 500 kg** (other than paying passengers) → .1–.5 by fuel type
- 1.3 — Fuel for means of transport **for paying passengers** → .1–.5
- (1.4 test vehicles · 1.5 lubricants · 1.6 resale · 1.7 goods-transport fuel · 1.8 cars/multipurpose · 1.9 recreational — Member-State-dependent; MEDIUM confidence on exact numbering)

**Codes 2 (hiring) and 3 (other vehicle expenditure) carry the same two-axis split** — mass
> 3 500 kg vs ≤ 3 500 kg, and "for paying passengers" — so vehicle hire/maintenance for a truck is
also classified on the goods-vehicle axis. (Origin Reg. (EC) 1174/2009, superseded by Reg. (EU)
79/2012 with effect 20 Feb 2012; codes carried over essentially unchanged.)

The EU publishes which Member States require the Art. 9(2) sub-codes vs accept the bare top-level
code: https://taxation-customs.ec.europa.eu/system/files/2016-09/information-document_en.pdf

### 4. Invoice copies & documentation (Art. 10)

The refund state **may require a scanned copy** of the invoice/import document where the **taxable
amount is ≥ €1 000** (or local equivalent) — **and ≥ €250 for FUEL** (the lower fuel threshold,
confirmed). We archive every PDF in the vault regardless, so this is satisfied de facto, but the
**€250 fuel / €1 000 general** thresholds are the trigger. Separately, under **Art. 20(1)** the
refund state may demand the invoice (original or copy) **regardless of these thresholds** whenever it
has reasonable doubt — another reason every original stays in the vault.

### 5. Processing timeline, information requests & interest (Arts. 19–27)

- **Receipt:** refund state notifies the receipt date without delay (Art. 19(1)).
- **Decision ladder:** **4 months** base (Art. 19(2)) → **6 months** if it requests additional info (Art. 20/21) → **8 months** maximum if it requests *further* info (Art. 21).
- **Applicant's response window:** **1 month** to supply requested info (Art. 20(2)) — **NOT a preclusive deadline** (CJEU C-133/18 *Sea Chefs*): missing it does **not** auto-forfeit the claim; the info can still be supplied at appeal. **Model 2B as a prompt, never a hard gate.**
- **Payment:** within **10 working days** of the decision-deadline expiry (Art. 22).
- **Refusal:** must be reasoned; appealable under the refund state's **national** rules/time-limits (Art. 23).
- **Interest on late refunds:** owed to the applicant when paid late (Arts. 26–27), at the refund state's national rate — *recoverable money*. Exceptions: applicant failed to supply requested info, or Art. 10 documents not yet received.
- There is **no literal "deemed approval"** clause; it's a derived/national remedy.

### 6. Deductibility is the REFUND STATE's national law — never propagate across countries

- **Art. 5(2):** "entitlement to an input tax refund shall be determined pursuant to Directive 2006/112/EC **as applied in the Member State of refund**." → *What's refundable = what's deductible under the refund country's own VAT law.*
- **Art. 6 / Art. 13:** if the applicant makes exempt supplies at home, the refund is scaled by its **home-state pro-rata**, with year-end adjustment. **Two independent clips:** (a) refund-state category deductibility, (b) home-state pro-rata.
- **Art. 176 standstill (2006/112):** Member States keep pre-existing exclusions (luxuries/entertainment, often passenger-car fuel, restaurants) — *why* the same expense differs by country and why exclusions are sticky.
- **Consequence for the system:** model refundability as a **per-(refund_country, expense_category)** table that **defaults to "needs-confirmation", not "refundable."** Diesel for trucks is the safe case (below); non-fuel items are not.

### 7. Commercial-truck diesel VAT recoverability by country (8th-Directive, 2026)

Truck (goods-vehicle) diesel VAT is **fully recoverable (100%)** in all 12 markets a Baltic haulier
fuels in — the 50%/exclusion caps attach to **passenger cars**, not goods vehicles.

| Country | Truck-diesel VAT | Std VAT 2026 | Note | "Professional diesel" EXCISE refund (separate from VAT) |
|---------|------------------|--------------|------|--------------------------------------------------------|
| Poland | 100% | 23% | car caps don't touch trucks | No |
| Germany | 100% | 19% | authority procedurally strict | No |
| Belgium | 100% | 21% | trucks/vans full; 50% car cap N/A | **Yes** (≥7.5t; customs registration) |
| Netherlands | 100% | 21% | — | No |
| France | 100% | 20% | petrol aligned to diesel by 2022 | **Yes** (ex-TICPE; new 2025 rules for non-FR) |
| Lithuania | 100% | 21% | usually home state (domestic deduction) | No |
| Latvia | 100% | 21% | usually home state | No |
| Estonia | 100% | **24%** (since Jul 2025) | 50% cap on cars only | No |
| Czechia | 100% | 21% | — | No |
| Austria | 100% | 20% | cars non-deductible; lorries full — watch classification | No |
| Italy | ~100% (diesel, AdBlue, tolls) | 22% | — | **Yes** (quarterly *rimborso accise*) |
| Spain | 100% | 21% | — | **Yes** (*gasóleo profesional*; non-resident NIF + approved fuel card since Oct 2022) |

**The excise "professional diesel" rebate is a different regime** — a partial **excise-duty** refund
(not VAT), claimed from each country's **customs** authority, only for trucks **≥ 7.5 t**, not
harmonised, rates change quarterly. Available in **FR, BE, IT, ES, SI, HU** (of the above). Keep it
an **entirely separate workflow** from the VAT engine.

---

### 8. Cross-check: our code vs the rules

#### ✓ Correct
- `MIN_QUARTER/MIN_ANNUAL = 400/50`, `filing_deadline()` = 30 Sep Y+1 with a hard period-end gate, `HOME_PORTAL` forward model, the `LOCAL_CCY_INPUT` note, and the **fuel-card caveat** (Vega International C-235/18) are all accurate.
- **2B (document request) is modelled as a soft worklist reminder, not an auto-reject** — which is exactly right per *Sea Chefs* (the Art. 20 one-month window is non-preclusive). **Do not ever "harden" it into a forfeiture gate.**

#### ⚠ Fix — expenditure mis-codings (`vat_config.GOODS_CODE`, risk of rejection)
1. **Road tolls coded `3`, should be `4`.** `"Toll/Fees": ("3", "Road tolls and road user charges")` — but Art. 9 code **4** *is* "road tolls and road user charge"; code 3 is the generic "expenditure relating to means of transport." The description already says "road tolls," so the **code number is simply wrong**. → change to `("4", …)`.
2. **AdBlue / Parking / Service coded `9`, should be `10`.** These map to code **9**, whose official meaning is **"Expenditure on luxuries, amusements and entertainment"** — the archetypal *non-deductible* category. The intended "Other" bucket is code **10**. Submitting an operating fluid (AdBlue) or parking under the luxuries/entertainment code invites refusal. → remap AdBlue/Parking/Service/Other to `("10", "Other")` (AdBlue arguably fits code 3 "expenditure relating to means of transport" — confirm per refund country).
3. **Diesel emits only the top-level code `1`.** For refund states that require Art. 9(2) sub-codes, truck diesel should be **`1.1.2`** (mass > 3 500 kg, diesel). Consider extending `GOODS_CODE` to carry the sub-code where the refund country requires it (the EC list says which do).

#### ◻ Gaps / opportunities (not bugs for these entities, but note)
- **No home-state pro-rata / deductible-proportion (Art. 8(2)(g), Arts. 6/13).** Fine *only if* every entity makes purely taxable transport supplies (100% deduction) — the normal haulier case. Record the assumption; if any entity has exempt income, the refund must be scaled and the % reported.
- **Non-fuel items aren't checked for per-country deductibility (Art. 5).** Diesel is safe everywhere; tolls/AdBlue/parking deductibility varies by refund country — don't assume refundable.
- **Interest on late refunds (Arts. 26–27) isn't tracked.** The Recovery page flags ">120 days unpaid" heuristically; the statutory model is the 4/6/8-month decision ladder + pay-within-10-working-days, after which **interest is owed**. Tracking it surfaces recoverable money.

#### Key legal references for the engine
2008/9/EC Arts. 3, 4, 5, 6, 7, 8, 9, 10, 11, 13, 15, 16, 17, 19, 20, 21, 22, 23, 26, 27 ·
Reg. (EC) 1174/2009 / Reg. (EU) 79/2012 Annex III (codes) · 2006/112/EC Arts. 168–176 (deduction,
standstill) · CJEU C-294/11 *Elsacom* (deadline is fatal) · CJEU C-133/18 *Sea Chefs* (Art. 20
window not preclusive) · CJEU C-235/18 *Vega International* (fuel-card supply vs financial service).
