# TLS / SSL + Cloudflare

How to put InvoiceIQ behind **Cloudflare** with a valid **SSL certificate**.
The nginx container is the *origin* (it serves the SPA and proxies `/api` to the
backend); Cloudflare sits in front as the edge.

```
Browser ──HTTPS──▶ Cloudflare edge ──HTTPS──▶ nginx origin ──▶ backend (uvicorn)
                    (SSL: Full Strict)         (Origin cert)      (internal only)
```

## 1. DNS
In Cloudflare, add an **A/AAAA record** for your app hostname (e.g. `app`) → your
server's IP, **proxied** (orange cloud ON). Proxying is what gives you Cloudflare's
edge TLS, DDoS protection, and caching.

## 2. SSL certificate (recommended: Cloudflare Origin Certificate)
This is the simplest robust setup — a long-lived cert that only Cloudflare trusts,
so the edge↔origin hop is also encrypted (required for *Full (Strict)*).

1. Cloudflare dashboard → **SSL/TLS → Origin Server → Create Certificate**.
2. Keep the private key; download the certificate. Save them on the server as:
   - `origin.pem`  (the certificate)
   - `origin.key`  (the private key)
   into a directory, e.g. `/etc/invoiceiq/certs/`.
3. Cloudflare dashboard → **SSL/TLS → Overview → set encryption mode to
   `Full (Strict)`**. (Never use *Flexible* — it leaves edge↔origin on plain HTTP.)
4. **SSL/TLS → Edge Certificates**: turn on **Always Use HTTPS**, **Minimum TLS 1.2**,
   and (once you're confident) **HSTS**.

### Alternative: Let's Encrypt at the origin
If you don't use Cloudflare's origin cert, issue a Let's Encrypt cert (certbot or
Caddy) and point `ssl_certificate`/`ssl_certificate_key` at it in
`frontend/nginx.prod.conf`. With Cloudflare proxying, use the **DNS-01** challenge
(HTTP-01 is intercepted by the proxy).

## 3. Run it
```bash
export SECRET_KEY=$(openssl rand -hex 32)
export APP_ORIGIN=https://app.example.com
export TLS_CERT_DIR=/etc/invoiceiq/certs        # holds origin.pem + origin.key

docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
```
The prod override (`docker-compose.prod.yml`):
- mounts `nginx.prod.conf` + your certs, and publishes **:80 (redirect) and :443**;
- runs the backend with `--proxy-headers --forwarded-allow-ips=*` so it trusts the
  `X-Forwarded-Proto`/`X-Forwarded-For` from the nginx origin (the backend is not
  published to the host — only nginx reaches it);
- sets `HSTS_ENABLED=true` and `ENVIRONMENT=production`.

## 4. What the app does behind the proxy
- **Real client IP** — `nginx.prod.conf` trusts Cloudflare's IP ranges and reads
  `CF-Connecting-IP`, so logs and headers see the actual visitor, not the edge.
  Keep the ranges current from <https://www.cloudflare.com/ips/>.
- **HTTPS awareness + HSTS** — with `hsts_enabled` on, the API emits
  `Strict-Transport-Security` on HTTPS requests (detected via `X-Forwarded-Proto`).
  It also always sends `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`,
  and a `Referrer-Policy`. nginx adds the same headers to the SPA responses.
- **Upload size** — nginx allows 20 MB bodies so scanned-PDF uploads reach the app
  (which caps at 15 MB); the default 1 MB would 413 them.

## 5. Hardening (recommended)
- **Authenticated Origin Pulls** — so the origin only accepts traffic that came
  through *your* Cloudflare zone (blocks anyone hitting the origin IP directly).
  Enable it in Cloudflare (**SSL/TLS → Origin Server**), then uncomment the
  `ssl_client_certificate` / `ssl_verify_client` lines in `nginx.prod.conf` and
  mount Cloudflare's origin-pull CA.
- Restrict the server's firewall so **:443/:80 accept only Cloudflare IP ranges**.
- Rotate `SECRET_KEY` out of band; never commit certs or keys.
- The email-intake webhook secret `INBOUND_EMAIL_SECRET` is **mandatory** —
  production refuses to boot without it and the webhook 401s until your email
  provider presents it (header `X-Inbound-Secret`). Generate one with
  `python -c "import secrets;print(secrets.token_urlsafe(32))"`.
