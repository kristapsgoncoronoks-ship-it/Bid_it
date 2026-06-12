# SECURITY — Fleet Fuel & VAT Refund System

What is implemented in code, what must be done on the host, and why.

## Implemented in the application

| Measure | Where | Notes |
|---|---|---|
| Login required on every page & API | `app.py` + `auth.py` | Session cookie (HttpOnly, SameSite=Lax; Secure under HTTPS); 1s delay on failed logins; attempts logged in `security.db` |
| Role-based permissions | `app.py` guard | viewer = read-only, editor = operational changes (POST), admin = + user management. Enforced centrally before every request |
| Admin panel | `/admin` | Create users, set roles, enable/disable, reset passwords, see login log + security status. Self-demotion/self-disable blocked; all actions audit-logged with the admin's name |
| TLS / HTTPS - any certificate | `tls.py` + `app.py` | Auto-resolves in order: `TLS_PFX` (commercial .pfx/.p12 + `TLS_PFX_PASSWORD`) -> `TLS_CERT`+`TLS_KEY` (+`TLS_CHAIN` for the intermediate bundle, +`TLS_KEY_PASSWORD` for encrypted keys) -> `fullchain.pem`/`privkey.pem` (Let's Encrypt naming) -> `cert.pem`/`key.pem` (self-signed via `make_cert.py`). TLS >= 1.2 enforced; startup prints subject/issuer/expiry and warns < 30 days. Diagnose with `python3 tls.py` |
| No secrets in audit log | `audit.py` | BLOB columns (password salt/hash) are structurally excluded from change snapshots |
| Password storage | `auth.py` | scrypt, per-user random salt; never plain text. Manage users: `python3 auth.py add/disable/list` |
| User attribution | `audit.py` triggers | Every insert/update/delete carries `changed_by`; web changes = username, scripts/CLI = `system`. Shown in the History page ("By" column) |
| Tamper-evident audit copies | `backup.py` | Each snapshot embeds full CSV exports of every `audit_log` — editing the live log cannot rewrite yesterday's snapshot |
| Backups w/ integrity + rotation | `backup.py` | SHA-256 manifest per snapshot; `--verify`; `--restore`; keeps last 14 |
| Output escaping | `app.py` | Stored values are HTML-escaped on render (master data, history, documents, data manager) |
| SQL safety | everywhere | Parameterized queries; Data manager restricted to whitelisted tables |
| Secrets | env vars only | `SP_CLIENT_SECRET`, API tokens never in code/DB; Flask session key in `.secret_key` (0600) |
| File permissions | `backup.py --harden` | 0600 on databases & key, 0700 on documents/ and backups/ |
| Network exposure | `app.py` | Binds 127.0.0.1 only by default |

## Required on the host (cannot be done from code)

1. **Disk encryption** — BitLocker (Windows) / FileVault (macOS) / LUKS (Linux) on the
   volume holding this folder. This is the protection if the machine is lost or stolen.
   Optional stronger step: SQLCipher for the .db files if OS-level file access cannot
   be restricted.
2. **Backup off-machine** — sync `backups/` to versioned storage (OneDrive/SharePoint
   library with versioning + recycle bin). Run a restore drill quarterly:
   `python3 backup.py --restore backups/<latest>.zip --to /tmp/drill && python3 backup.py --verify backups/<latest>.zip`
3. **Multi-user / remote access** — keep the app on 127.0.0.1 and put a reverse proxy
   in front. Minimal nginx:

       server {
         listen 443 ssl;
         server_name fuel.internal.example;
         ssl_certificate     /etc/ssl/fuel.crt;
         ssl_certificate_key /etc/ssl/fuel.key;
         location / { proxy_pass http://127.0.0.1:8050; proxy_set_header X-Forwarded-For $remote_addr; }
       }

   Run the app under `gunicorn -w 2 -b 127.0.0.1:8050 app:app`. Corporate SSO can be
   added at the proxy (oauth2-proxy) on top of the built-in login.
4. **Secret rotation** — rotate the SharePoint client secret and supplier API tokens on
   a schedule (e.g. 6 months); they live only in environment variables, so rotation is
   an env change + restart. To rotate the session key delete `.secret_key` and restart
   (signs everyone out).
5. **Account hygiene** — one account per person (attribution depends on it); disable
   accounts on offboarding via the Admin panel or `python3 auth.py disable <user>`.
   Assign `viewer` by default; grant `editor`/`admin` deliberately.
6. **TLS certificate** — any source works (see table). Quick recipes:
   - Self-signed (internal use): `python3 make_cert.py` (one browser warning, accept once)
   - Commercial CA (separate intermediate): `export TLS_CERT=fuel.crt TLS_KEY=fuel.key TLS_CHAIN=ca_bundle.crt`
   - Commercial PKCS#12: `export TLS_PFX=fuel.pfx TLS_PFX_PASSWORD='...'`
   - Let's Encrypt: point `TLS_CERT`/`TLS_KEY` at `live/<domain>/fullchain.pem` and `privkey.pem`,
     renew via certbot; or terminate TLS at nginx instead.
   Private keys never ship in the system package - install per host. Chain validation
   was test-proven end-to-end (curl --cacert -> verify result 0).

## Batch extraction (PDF/ZIP import)

- Default `EXTRACT_BACKEND=auto` uses the deterministic parser for recognised
  suppliers - fully offline, nothing leaves the server. AI backends
  (`claude`/`openai`/`azure`) are used only as fallback and only if configured.
- AI backends send invoice PDF text to that processor over TLS for the extraction
  call. Permitted under your DPA; covered by the provider's no-training-on-API-data
  terms. Use `azure` to keep it inside your own M365 tenant.
- Extraction output is always a DRAFT shown for human confirmation; it is never
  committed automatically and never overrides the totals-vs-coversheet reconciliation.
- API keys live in environment variables only (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`,
  `AZURE_OPENAI_*`). No key, no AI call - the parser/manual paths still work.

## GDPR / retention

- Vehicle plates + timestamps + locations constitute movement profiles → personal data
  where vehicles map to drivers. Lawful basis: legitimate interest (accounting & tax
  compliance). Consequences: access control is an obligation (login + host measures
  above), and include this processing in your records of processing activities.
- VAT documentation must be RETAINED per refund-state rules (commonly 7–10 years) —
  the duty is controlled access, not deletion. SharePoint backend (document_vault.py,
  `Sites.Selected` grant) adds tenant permissions, versioning and retention policies
  to the vault.

## Known residual risks (accepted, documented)

- SQLite has no row-level security: anyone with file access reads everything → host
  measures 1–2 are the real boundary.
- The live `audit_log` is writable by anyone with direct DB access; snapshots are the
  tamper evidence, so backup frequency bounds the exposure window.
- Actor attribution is per-database, not per-connection: simultaneous web writes by
  two users within the same request window could mis-attribute. Acceptable at this
  team size; move to PostgreSQL + per-session context before heavy multi-user use.
