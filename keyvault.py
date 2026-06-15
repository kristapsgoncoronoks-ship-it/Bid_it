"""
ENVELOPE-ENCRYPTION CREDENTIAL CUSTODY — the cryptographic primitive under stored
secrets (portal credentials today; any secret-at-rest tomorrow).

WHY ENVELOPE: instead of encrypting every secret directly with one long-lived key, we
generate a fresh random DATA-ENCRYPTION KEY (DEK) per `seal`, encrypt the plaintext
under that DEK, and then WRAP the DEK with a KEY-ENCRYPTION KEY (KEK). The KEK never
touches the plaintext and is the only thing a KMS/HSM/BYOK provider has to hold. This
is the standard shape that lets us later move the KEK behind a cloud KMS (or per-tenant
BYOK keys) WITHOUT re-encrypting any stored blob — only the (small) wrapped-DEK changes.

  blob = b"FFSv1" | u16(len(wrapped_dek)) | wrapped_dek | nonce(12) | ciphertext
         where ciphertext = AES-256-GCM(DEK, nonce, plaintext, aad)
         and   wrapped_dek = KEK.wrap(DEK)

AAD BINDING: `seal(text, aad)` binds the blob to a caller-chosen context string (e.g.
"<SUPPLIER>:<ENTITY>"); opening with a different aad fails the GCM tag check, so a blob
cannot be lifted from one row and replayed into another.

PLUGGABLE KEK: `get_kek(provider, tenant)` selects a KEK provider from the
`keyvault_provider` app setting (default "local"). `LocalKEK` derives the KEK from the
app secret key. `EnvKEK` ("env" provider) takes the raw 32-byte master key from the
ENVIRONMENT — `FFS_KEK_KEY` (or per-tenant `FFS_KEK_KEY_<TENANT>`), base64 of exactly
32 bytes — so the platform's secret manager / KMS (AWS Secrets Manager, HashiCorp Vault,
Azure Key Vault, KMS-decrypt-at-boot, …) can inject the KEK without keyvault carrying any
SDK. EnvKEK fails LOUD if the env key is missing/malformed — it never silently downgrades
to the on-disk local key. The `tenant` argument is a forward seam for per-tenant/BYOK
keys; the local provider ignores it, EnvKEK uses it to pick `FFS_KEK_KEY_<TENANT>` first.
A real `KmsKEK` plugs in at the marked seam below.

ROTATION CAVEAT: stored DEKs are wrapped under whatever KEK was active at `seal`-time.
Switching `keyvault_provider` on an EXISTING, populated credential store is a MIGRATION
that requires re-wrapping every blob (the old wrapped-DEKs are unreadable under the new
KEK). Selecting "env" is for deployments that choose it FROM THE START; do not toggle the
provider on a populated store without a re-wrap pass.

SECURITY: never log or echo a plaintext secret or the secret key. GCM authentication
means tamper/auth failures RAISE — we never silently return "".
"""
import base64
import binascii
import hashlib
import os
import struct

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

import applog

log = applog.get("keyvault")

WORKDIR = os.path.dirname(os.path.abspath(__file__))

MAGIC = b"FFSv1"          # versioned envelope-blob marker (1 = this format)
_NONCE = 12               # AES-GCM standard nonce length
_DEK_BYTES = 32           # AES-256
_KEK_BYTES = 32           # AES-256


# ---------------------------------------------------------------- KEK providers
class KEK:
    """Key-encryption-key interface. A provider wraps/unwraps a raw DEK; it never
    sees the plaintext. Implementations: LocalKEK (now), KmsKEK (future seam)."""
    def wrap(self, dek: bytes) -> bytes:
        raise NotImplementedError

    def unwrap(self, wrapped: bytes) -> bytes:
        raise NotImplementedError


class LocalKEK(KEK):
    """Default provider: derive a 32-byte KEK from the app secret key and AES-256-GCM
    wrap/unwrap the DEK. No external dependency; the KEK lives only in memory, derived
    on demand. `tenant` is accepted for API parity but ignored (single local key)."""
    def __init__(self, tenant=None):
        self.tenant = tenant

    def _key(self) -> bytes:
        import auth
        sk = auth.secret_key()
        if isinstance(sk, str):
            sk = sk.encode()
        return hashlib.sha256(b"ffs-kek-v1" + sk).digest()

    def wrap(self, dek: bytes) -> bytes:
        nonce = os.urandom(_NONCE)
        ct = AESGCM(self._key()).encrypt(nonce, dek, b"ffs-kek-wrap")
        return nonce + ct

    def unwrap(self, wrapped: bytes) -> bytes:
        nonce, ct = wrapped[:_NONCE], wrapped[_NONCE:]
        return AESGCM(self._key()).decrypt(nonce, ct, b"ffs-kek-wrap")


def _decode_b64_key(raw: str) -> bytes:
    """Decode a base64 (std OR urlsafe) string to EXACTLY 32 raw bytes, or raise.
    Never logs the key bytes."""
    s = (raw or "").strip()
    for decoder in (base64.b64decode, base64.urlsafe_b64decode):
        try:
            key = decoder(s)
        except (binascii.Error, ValueError):
            continue
        if len(key) == _KEK_BYTES:
            return key
    raise ValueError(
        "EnvKEK: env key is not base64 of exactly %d bytes" % _KEK_BYTES)


class EnvKEK(KEK):
    """Production provider: take the raw 32-byte master KEK from the ENVIRONMENT, where
    the deployment's secret manager / KMS injected it (vendor-neutral, SDK-free). Per
    `tenant` first (`FFS_KEK_KEY_<TENANT>`), else the global `FFS_KEK_KEY`; the value is
    base64 (std or urlsafe) of EXACTLY 32 bytes. AES-256-GCM wrap/unwrap, same mechanism
    as LocalKEK (random 12-byte nonce; nonce||ct; GCM auth on unwrap).

    Fails LOUD: if no env key is set or it does not decode to 32 bytes, construction
    raises — an admin who selected the env provider must NOT silently fall back to the
    weaker on-disk local key."""
    def __init__(self, tenant=None):
        self.tenant = tenant
        raw = None
        if tenant:
            safe = "".join(c if c.isalnum() else "_" for c in str(tenant)).upper()
            raw = os.environ.get("FFS_KEK_KEY_" + safe)
        if raw is None:
            raw = os.environ.get("FFS_KEK_KEY")
        if not raw:
            raise RuntimeError(
                "EnvKEK: FFS_KEK_KEY not set or not a base64 32-byte key "
                "(env provider selected but no key injected into the environment)")
        self._k = _decode_b64_key(raw)

    def wrap(self, dek: bytes) -> bytes:
        nonce = os.urandom(_NONCE)
        ct = AESGCM(self._k).encrypt(nonce, dek, b"ffs-kek-wrap")
        return nonce + ct

    def unwrap(self, wrapped: bytes) -> bytes:
        nonce, ct = wrapped[:_NONCE], wrapped[_NONCE:]
        return AESGCM(self._k).decrypt(nonce, ct, b"ffs-kek-wrap")


# ---- SEAM: a future cloud-KMS / BYOK provider plugs in HERE -------------------
# EnvKEK above already covers the common KMS pattern (the platform decrypts the key
# via its KMS/secret-manager and injects it as FFS_KEK_KEY into the environment). The
# seam below is for an SDK-native provider that calls the KMS Encrypt/Decrypt of the
# DEK directly (the data key wrapped REMOTELY; the KEK never leaves the KMS/HSM).
# class KmsKEK(KEK):
#     """Endpoint + key-id driven (env/app-setting), per-tenant key for BYOK. wrap()/
#     unwrap() would call the KMS Encrypt/Decrypt of the DEK (the data key is wrapped
#     remotely; the KEK itself never leaves the KMS/HSM). NOT implemented now — no live
#     cloud call is made in this environment."""
#     def __init__(self, tenant=None):
#         self.tenant = tenant
#         self.endpoint = os.environ.get("FFS_KMS_ENDPOINT") or auth.get_setting("kms_endpoint")
#         self.key_id = os.environ.get("FFS_KMS_KEY_ID") or auth.get_setting("kms_key_id")
#     def wrap(self, dek): ...   # kms.encrypt(key_id, dek)
#     def unwrap(self, wrapped): ...   # kms.decrypt(wrapped)
# ------------------------------------------------------------------------------

_PROVIDERS = {"local": LocalKEK, "env": EnvKEK}   # + "kms": KmsKEK once implemented


def get_kek(provider=None, tenant=None) -> KEK:
    """Select the KEK provider. Defaults to the `keyvault_provider` app setting
    ("local"). `tenant` is forwarded for per-tenant/BYOK keys.

    An UNKNOWN provider NAME falls back to LocalKEK (with a warning). But once a KNOWN
    provider class is resolved, its construction error PROPAGATES — e.g. EnvKEK with no
    injected env key raises, and we deliberately do NOT swallow it into a local fallback:
    that would be a silent security downgrade for an admin who chose the env provider."""
    if provider is None:
        try:
            import auth
            provider = auth.get_setting("keyvault_provider", "local")
        except Exception as e:
            log.warning("get_kek: could not read keyvault_provider setting, using local: %s", e)
            provider = "local"
    cls = _PROVIDERS.get((provider or "local").lower())
    if cls is None:
        log.warning("get_kek: unknown provider %r, falling back to local", provider)
        cls = LocalKEK
    return cls(tenant=tenant)


# ---------------------------------------------------------------- envelope API
def seal(plaintext: str, aad: str = "") -> bytes:
    """Envelope-encrypt `plaintext`: fresh random DEK, AES-256-GCM under it bound to
    `aad`, then wrap the DEK with the KEK. Returns a versioned, self-describing blob."""
    dek = os.urandom(_DEK_BYTES)
    nonce = os.urandom(_NONCE)
    ct = AESGCM(dek).encrypt(nonce, (plaintext or "").encode("utf-8"), aad.encode("utf-8"))
    wrapped = get_kek().wrap(dek)
    return MAGIC + struct.pack(">H", len(wrapped)) + wrapped + nonce + ct


def open(blob: bytes, aad: str = "") -> str:
    """Reverse `seal`: unwrap the DEK with the KEK and AES-GCM decrypt under `aad`.
    Raises on a bad magic/version, truncation, wrong aad, or any tamper (GCM tag)."""
    blob = bytes(blob)
    if not is_envelope(blob):
        raise ValueError("keyvault.open: not a FFSv1 envelope blob")
    off = len(MAGIC)
    (wlen,) = struct.unpack(">H", blob[off:off + 2]); off += 2
    wrapped = blob[off:off + wlen]; off += wlen
    if len(wrapped) != wlen:
        raise ValueError("keyvault.open: truncated wrapped DEK")
    nonce = blob[off:off + _NONCE]; off += _NONCE
    ct = blob[off:]
    if len(nonce) != _NONCE:
        raise ValueError("keyvault.open: truncated nonce")
    dek = get_kek().unwrap(wrapped)
    pt = AESGCM(dek).decrypt(nonce, ct, aad.encode("utf-8"))
    return pt.decode("utf-8")


def is_envelope(blob) -> bool:
    """True iff `blob` is a FFSv1 envelope (distinguishes from legacy Fernet tokens,
    which are urlsafe-base64 and start with 'gAAAAA')."""
    if blob is None:
        return False
    return bytes(blob).startswith(MAGIC)
