"""Generate a self-signed TLS certificate for the app (browser warns once - accept,
or install a corporate / Let's Encrypt cert under the same filenames).
    python3 make_cert.py [hostname]   -> cert.pem + key.pem (0600), 825 days"""
import os, subprocess, sys, shutil
W = os.path.dirname(os.path.abspath(__file__))
host = sys.argv[1] if len(sys.argv) > 1 else "fleet-fuel.local"

def _via_openssl():
    subprocess.run(["openssl","req","-x509","-newkey","rsa:2048","-nodes",
        "-keyout",f"{W}/key.pem","-out",f"{W}/cert.pem","-days","825",
        "-subj",f"/CN={host}","-addext",f"subjectAltName=DNS:{host},DNS:localhost,IP:127.0.0.1"],
        check=True, capture_output=True)

def _via_cryptography():
    """Pure-Python cert generation - works on Windows with no OpenSSL binary."""
    from cryptography import x509
    from cryptography.x509.oid import NameOID
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    import datetime, ipaddress
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, host)])
    san = x509.SubjectAlternativeName([x509.DNSName(host), x509.DNSName("localhost"),
                                       x509.IPAddress(ipaddress.IPv4Address("127.0.0.1"))])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now).not_valid_after(now + datetime.timedelta(days=825))
            .add_extension(san, critical=False).sign(key, hashes.SHA256()))
    with open(f"{W}/key.pem","wb") as f:
        f.write(key.private_bytes(serialization.Encoding.PEM,
                serialization.PrivateFormat.TraditionalOpenSSL, serialization.NoEncryption()))
    with open(f"{W}/cert.pem","wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))

if shutil.which("openssl"):
    _via_openssl()
else:
    _via_cryptography()
for f in ("key.pem","cert.pem"):
    try: os.chmod(f"{W}/{f}", 0o600)
    except OSError: pass
print(f"created cert.pem + key.pem (CN={host}, SAN incl. localhost)")
