# INSTALL — Setup & Installation

---

## EASIEST INSTALL — no command line, about 2 minutes

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
shouldn't have). Daily guide: USER_MANUAL.md.

### Prefer a guided terminal installer?
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

# PRODUCTION SETUP (server / team)

Primary path: **Ubuntu Server 22.04/24.04 LTS**. A small VM runs the baseline
(2 vCPU, 2–4 GB RAM, 20 GB disk); for a real install **4 vCPU / 8 GB / 150 GB NVMe**
is the comfortable sweet spot — full sizing (and when local OCR needs more) is in
**[DEPLOYMENT_SIZING.md](DEPLOYMENT_SIZING.md)**. Windows alternative at the end.
Estimated time: 30–45 minutes, +30 minutes for team access.

---

## PART 1 — Operating system preparation (Ubuntu)

```bash
# 1.1 Update the OS
sudo apt update && sudo apt upgrade -y

# 1.2 Install Python 3.12+, tools and OpenSSL
sudo apt install -y python3 python3-pip python3-venv unzip openssl curl
python3 --version          # must show 3.12 or newer

# 1.3 Create a dedicated service user (never run as root)
sudo adduser --system --group --home /opt/fleetfuel fleetfuel
```

## PART 2 — Install the software

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

## PART 3 — TLS certificate (pick ONE)

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

## PART 4 — First run & users

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

## PART 5 — Run as a permanent service (systemd)

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

## PART 5b — Dedicated worker tier (recommended for scale / automated capture)

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

## PART 6 — Team access: nginx reverse proxy + firewall

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

## PART 6b — Multiple worker processes (scale-out)

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

## PART 7 — Document vault backend (optional)

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

## PART 7b — Automated portal capture (optional)

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

## PART 7e — Scanned-PDF OCR fallback (optional, on-prem)

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
**[DEPLOYMENT_SIZING.md](DEPLOYMENT_SIZING.md)** for the larger box it wants.

## PART 7c — PostgreSQL (optional, scale-out)

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

Full migration steps and the maturity caveats are in **[SCALING.md](SCALING.md)**.

## PART 7d — Multi-tenant (optional, do NOT flip casually)

The platform ships **single‑tenant** — the `multitenant` admin setting is **OFF** and
nothing is tenant‑scoped. Turning it on is a **P1+ rollout** (tenant‑scoped enforcement at
every query, per‑tenant credential keys via `FFS_KEK_KEY_<TENANT>`, isolation tests) and a
cross‑tenant leak is a GDPR breach — **do not enable it before completing that work**. The
read‑only `/admin/tenants` page shows the current (single‑tenant) state. See
**[MULTI_TENANCY.md](MULTI_TENANCY.md)** before changing it.

## PART 8 — Automatic backups

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

## PART 8a — Post-install configuration checklist (in the Admin panel)

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
   never mutates or gates a figure (see [AI_REVIEW.md](AI_REVIEW.md)).
6. **API keys** (optional) — issue scoped machine keys only if an external system needs
   the `/api/v1` API; it is default‑off (see [API.md](API.md)).

## PART 8b — Credential custody for automated capture (KMS / BYOK)

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
   `keyvault.py` header and [SCALING.md](SCALING.md).

The master key is never written to disk by the app — your KMS/secret manager owns it.

## PART 9 — Verification checklist

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

## PART 10 — Monthly operation (after setup)

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

# WINDOWS SERVER SETUP (first-class)

The system runs natively on Windows Server - no Linux subsystem needed. Every
Linux-only dependency has a Windows fallback built in: PDF text via `pypdf` (no
poppler), certificates via the `cryptography` library (no OpenSSL), `icacls`
hardening (no chmod), and `waitress` as the production server (no gunicorn).

## W1 - Install Python & the app
1. Install **Python 3.12+** from python.org - tick **"Add python.exe to PATH"**.
2. Unzip the package to e.g. `C:\FleetFuel\app`.
3. Open PowerShell in that folder and install dependencies:
   ```powershell
   pip install -r requirements.txt
   ```
   (`flask openpyxl requests cryptography waitress pypdf`)

## W2 - Quick install (interactive)
```powershell
.\install.bat
```
The wizard checks Python, installs packages, creates the certificate (pure-Python,
no OpenSSL), creates your admin account, hardens files with `icacls`, takes a first
backup and self-checks. Then `.\start.bat` and open `https://localhost:8050`.

## W3 - Certificate (any source, no OpenSSL required)
```powershell
python make_cert.py fuel.yourcompany.local      # self-signed via cryptography lib
```
Or use a commercial/AD certificate by setting **system environment variables**
(Control Panel -> System -> Advanced -> Environment Variables, or `setx /M`):
`TLS_PFX` + `TLS_PFX_PASSWORD` for a .pfx, or `TLS_CERT`/`TLS_KEY`/`TLS_CHAIN`.
Verify: `python tls.py`.

## W4 - Run as a Windows Service (auto-start, survives reboot)
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

## W5 - HTTPS for a team
`serve.py` (waitress) serves HTTP locally; terminate TLS in front:
- **IIS** with Application Request Routing (ARR) + URL Rewrite reverse-proxying to
  `http://127.0.0.1:8050`, with the certificate bound in IIS; or
- **nginx for Windows** using the same reverse-proxy config as the Linux section.
Bind the app to localhost only (`BIND_HOST=127.0.0.1`, the default) so the proxy is
the sole entry point. Open only 443 in Windows Defender Firewall.

## W6 - Backups & disk encryption
```powershell
python backup.py --harden        # icacls: current user + SYSTEM only
python backup.py                 # snapshot (Task Scheduler: daily)
```
Schedule the daily backup in Task Scheduler, sync `backups\` to OneDrive/SharePoint,
and enable **BitLocker** on the drive holding the app (this is the protection if the
machine is lost - see SECURITY.md).

## W7 - Verify
```powershell
python tls.py                                    # TLS: OK
curl.exe -k https://localhost:8050/login         # 200 (if using dev-server TLS)
Get-Service FleetFuel                            # Running
```

---

## Windows quick-reference

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

## Troubleshooting

| Symptom | Fix |
|---|---|
| `Address already in use` on 8050 | `python3 cleanup.py` (never `pkill -f app.py` — it matches itself) |
| Browser warns about certificate | Expected with self-signed; accept once, or install a CA cert (Part 3 B/C/D) |
| `TLS: NOT CONFIGURED` | Run `python3 tls.py` to see what it looked for; generate or point env vars |
| Login loop / signed out after restart | Session key rotated (`.secret_key` recreated) — sign in again; keep the file to persist sessions |
| `MISSING` invoices every month for one supplier | Check its `invoice_cadence` in suppliers.db (Data manager) |
| API source fails | `pip install requests`; token env var set in the systemd unit, then restart |
| Forgot admin password | `sudo -u fleetfuel .../python auth.py add <admin-user>` resets it from the shell |

## Related documentation

- **[DEPLOYMENT_SIZING.md](DEPLOYMENT_SIZING.md)** — what server to buy for a single-box
  install (CPU/RAM/disk, reference workload ~100 invoices/day, local-OCR sizing).
- **[SCALING.md](SCALING.md)** — the single-box → web/worker fleet → PostgreSQL ladder
  (Parts 5b / 7c) and the credential-custody rotation detail (Part 8b).
- **[MULTI_TENANCY.md](MULTI_TENANCY.md)** — the P1+ rollout before turning `multitenant`
  on (Part 7d).
- **[SAFT.md](SAFT.md)** — what the SAF-T export is and how to specialise it per
  jurisdiction (the `/export/saft` core structure).
- **[SECURITY.md](../SECURITY.md)** — disk encryption, password storage, hardening,
  backup integrity.
- **[GIT_SETUP.md](GIT_SETUP.md)** — source control / what must never be committed
  (secrets, `security.db`, runtime DBs).
- **[API.md](API.md)** / **[API_MANUAL.md](API_MANUAL.md)** — the `/api/v1` external API
  (default-off; issue keys in the Admin panel).
- **[AI_REVIEW.md](AI_REVIEW.md)** — the advisory AI review assistant (default-off).
