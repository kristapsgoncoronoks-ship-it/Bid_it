# Hosting on Hostinger — step by step

A complete, copy‑paste walkthrough to run the Fleet Fuel & VAT Refund System on a
**Hostinger VPS** with a real domain and HTTPS. It is the Hostinger‑specific version of
the generic **[INSTALL.md](INSTALL.md)** production path; for hardware sizing see
**[DEPLOYMENT_SIZING.md](DEPLOYMENT_SIZING.md)**.

> **Why a VPS, not Web/Cloud Hosting?** Hostinger's shared **Web Hosting** and **Cloud
> Hosting** plans are managed PHP/WordPress environments — they cannot run a long‑lived
> Python WSGI server (waitress), `systemd` services, or your own nginx. This app needs
> root on a Linux box, which is the **VPS (KVM)** product. Do **not** buy shared hosting
> for it.

Reference workload for the sizing here: **~100 invoices/day** (a single transport group).

---

## STEP 0 — Order the right Hostinger product

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

## STEP 1 — Point the domain at the VPS

In Hostinger **hPanel → Domains → DNS / Nameservers** (or your registrar):

- Add an **A record**: host `fuel` (or `@` for the root) → **your VPS IP**.
- Wait for DNS to propagate (minutes to ~1 hour). Verify from your laptop:
  ```bash
  ping fuel.yourcompany.eu          # must resolve to the VPS IP
  ```

Do not continue to Step 5 (TLS) until the domain resolves to the VPS.

---

## STEP 2 — Connect and prepare the server

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

## STEP 3 — Install the application

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

## STEP 4 — Local TLS + a session key

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

## STEP 5 — Public certificate (Let's Encrypt)

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

## STEP 6 — Run the app as a service (systemd)

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

## STEP 7 — Public access: nginx reverse proxy + firewall

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

## STEP 8 — (Optional) On‑prem OCR for scanned PDFs

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

## STEP 9 — First run, backups, and go‑live checklist

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

## What you do NOT need at this scale

For ~100 invoices/day stay on the **single‑box default**: SQLite + WAL, the in‑process
intake worker, one VPS. You do **not** need PostgreSQL, a separate worker tier, gunicorn,
or a load balancer — those are the higher rungs in **[SCALING.md](SCALING.md)** and only
pay off at thousands of invoices/day or when you need high availability.

---

## Troubleshooting (Hostinger specifics)

| Symptom | Fix |
|---|---|
| Domain won't load, but the IP works | DNS A record not propagated, or **hPanel firewall** is blocking 80/443 — check both. |
| `certbot` fails to validate | Port 80 reachable? Stop nginx during `--standalone`, and confirm the hPanel firewall allows 80. DNS must already point at the VPS. |
| Browser shows the nginx welcome page | You didn't remove `/etc/nginx/sites-enabled/default` (Step 7). |
| Login loops / logged out after restart | `FFS_SECRET_KEY` not set (Step 6) — the key rotated. Set it and restart. |
| `502 Bad Gateway` from nginx | The app service is down (`systemctl status fleetfuel`) or not on `127.0.0.1:8050` — check `journalctl -u fleetfuel`. |
| Big invoice ZIP rejected by nginx | Raise `client_max_body_size` in the server block, then `systemctl reload nginx`. |

---

## Related documentation

- **[INSTALL.md](INSTALL.md)** — the full generic production guide (this is its Hostinger cut).
- **[DEPLOYMENT_SIZING.md](DEPLOYMENT_SIZING.md)** — choosing CPU/RAM/disk; local‑OCR sizing.
- **[SCALING.md](SCALING.md)** — when and how to grow beyond one box.
- **[../SECURITY.md](../SECURITY.md)** — data protection, disk encryption, hardening.
