"""
TLS CERTIFICATE SUPPORT - use ANY certificate with the app:
self-signed (make_cert.py), OpenSSL-generated, commercial CA-issued (with
intermediate chain), Let's Encrypt, passphrase-protected keys, or PKCS#12 (.pfx).

Resolution order (first match wins):
  1. TLS_PFX (+ TLS_PFX_PASSWORD)          - commercial .pfx/.p12 bundle
  2. TLS_CERT + TLS_KEY                    - explicit PEM paths
       (+ TLS_CHAIN for the intermediate/CA bundle a commercial CA sends,
        + TLS_KEY_PASSWORD if the private key is encrypted)
  3. <appdir>/fullchain.pem + privkey.pem  - Let's Encrypt naming
  4. <appdir>/cert.pem + key.pem           - make_cert.py self-signed

Examples
  Commercial cert (separate intermediate):
      export TLS_CERT=/etc/ssl/fuel.crt TLS_KEY=/etc/ssl/fuel.key \\
             TLS_CHAIN=/etc/ssl/ca_bundle.crt
  Let's Encrypt:
      export TLS_CERT=/etc/letsencrypt/live/fuel.example.com/fullchain.pem \\
             TLS_KEY=/etc/letsencrypt/live/fuel.example.com/privkey.pem
  PKCS#12 from a commercial CA:
      export TLS_PFX=/etc/ssl/fuel.pfx TLS_PFX_PASSWORD='...'
  Encrypted key:
      export TLS_KEY_PASSWORD='...'

Diagnostics:  python3 tls.py   -> shows which source resolves, subject, issuer,
expiry (warns < 30 days). Enforces TLS >= 1.2.
"""
import os, ssl, tempfile, datetime, subprocess
from datetime import timezone as _tz

WORKDIR = os.path.dirname(os.path.abspath(__file__))


def _ctx():
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    return ctx


def _from_pfx(pfx_path, password):
    """Commercial CAs often deliver .pfx/.p12 - unpack to runtime PEMs (0600)."""
    try:
        from cryptography.hazmat.primitives.serialization import (
            pkcs12, Encoding, PrivateFormat, NoEncryption)
    except ImportError:
        raise RuntimeError(
            "PFX support needs the 'cryptography' package (pip install cryptography), "
            "or convert once with openssl:\n"
            "  openssl pkcs12 -in cert.pfx -clcerts -nokeys -out cert.pem\n"
            "  openssl pkcs12 -in cert.pfx -nocerts -nodes -out key.pem")
    data = open(pfx_path, "rb").read()
    key, cert, extras = pkcs12.load_key_and_certificates(
        data, password.encode() if password else None)
    tmp = tempfile.mkdtemp(prefix="tls_", dir=WORKDIR)
    cert_p, key_p = os.path.join(tmp, "fullchain.pem"), os.path.join(tmp, "privkey.pem")
    with open(cert_p, "wb") as f:
        f.write(cert.public_bytes(Encoding.PEM))
        for c in extras or []:
            f.write(c.public_bytes(Encoding.PEM))      # include the chain
    with open(key_p, "wb") as f:
        f.write(key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()))
    for p in (cert_p, key_p):
        os.chmod(p, 0o600)
    os.chmod(tmp, 0o700)
    return cert_p, key_p


def _combine_chain(cert, chain):
    """Commercial cert + separate intermediate bundle -> one fullchain file."""
    tmp = tempfile.NamedTemporaryFile(prefix="fullchain_", suffix=".pem",
                                      dir=WORKDIR, delete=False)
    tmp.write(open(cert, "rb").read())
    tmp.write(b"\n")
    tmp.write(open(chain, "rb").read())
    tmp.close()
    os.chmod(tmp.name, 0o600)
    return tmp.name


def resolve():
    """-> (certfile, keyfile, password, source_description) or None."""
    if os.environ.get("TLS_PFX"):
        c, k = _from_pfx(os.environ["TLS_PFX"], os.environ.get("TLS_PFX_PASSWORD"))
        return c, k, None, f"PKCS#12 bundle {os.environ['TLS_PFX']}"
    if os.environ.get("TLS_CERT") and os.environ.get("TLS_KEY"):
        cert = os.environ["TLS_CERT"]
        src = f"TLS_CERT={cert}"
        if os.environ.get("TLS_CHAIN"):
            cert = _combine_chain(cert, os.environ["TLS_CHAIN"])
            src += f" + chain {os.environ['TLS_CHAIN']}"
        return cert, os.environ["TLS_KEY"], os.environ.get("TLS_KEY_PASSWORD"), src
    for c, k, label in ((f"{WORKDIR}/fullchain.pem", f"{WORKDIR}/privkey.pem", "Let's Encrypt files"),
                        (f"{WORKDIR}/cert.pem", f"{WORKDIR}/key.pem", "self-signed (make_cert.py)")):
        if os.path.exists(c) and os.path.exists(k):
            return c, k, os.environ.get("TLS_KEY_PASSWORD"), label
    return None


def cert_info(certfile):
    out = subprocess.run(["openssl", "x509", "-in", certfile, "-noout",
                          "-subject", "-issuer", "-enddate"],
                         capture_output=True, text=True).stdout.strip()
    info = dict(line.split("=", 1) for line in out.splitlines() if "=" in line)
    end = info.get("notAfter", "").strip()
    days = None
    if end:
        try:
            exp = datetime.datetime.strptime(end, "%b %d %H:%M:%S %Y %Z")
            days = (exp - datetime.datetime.now(_tz.utc)).days
        except ValueError:
            pass
    return info.get("subject", "").strip(), info.get("issuer", "").strip(), end, days


def build_context():
    """-> (ssl_context, description) or (None, reason)."""
    r = resolve()
    if not r:
        return None, ("no certificate found - run 'python3 make_cert.py' (self-signed) "
                      "or set TLS_CERT/TLS_KEY or TLS_PFX (commercial cert)")
    cert, key, password, source = r
    ctx = _ctx()
    ctx.load_cert_chain(cert, key, password=password)
    subject, issuer, end, days = cert_info(cert)
    desc = f"{source} | {subject} | issuer {issuer} | expires {end}"
    if days is not None and days < 30:
        desc += f"  !! WARNING: certificate expires in {days} days - renew now"
    return ctx, desc


if __name__ == "__main__":
    ctx, desc = build_context()
    print("TLS:", "OK -" if ctx else "NOT CONFIGURED -", desc)
