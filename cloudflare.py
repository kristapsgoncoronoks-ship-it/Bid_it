"""
CLOUDFLARE EDGE-SAFETY — an OPT-IN, default-OFF package for running the app behind the
Cloudflare proxy (orange-cloud DNS). Two capabilities, BOTH inert until an admin turns
them on, so with the settings OFF the app behaves BYTE-IDENTICALLY to today:

  1. TRUSTED CLIENT IP (`trust_cloudflare`, default OFF). When the site is proxied by
     Cloudflare the immediate socket peer (`request.remote_addr`) is a CLOUDFLARE edge IP,
     not the visitor — which silently defeats the per-IP brute-force throttle
     (`auth.is_locked_ip`) and pollutes every audit/login-log IP. This module resolves the
     REAL client IP from Cloudflare's `CF-Connecting-IP` header, but ONLY when the request
     genuinely arrives FROM Cloudflare (the peer is a published CF range / a configured
     local reverse proxy / loopback). A forged `CF-Connecting-IP` from a NON-Cloudflare
     peer is IGNORED — the header is never blindly trusted.

  2. ORIGIN LOCK (`cloudflare_only`, default OFF). When ON, the app refuses any request
     whose peer is NOT Cloudflare / a trusted proxy / loopback (a plain 403), so nobody can
     bypass the Cloudflare edge (WAF/rate-limit/Bot-Fight) and hit the origin directly. This
     is also what makes `CF-Connecting-IP` UNSPOOFABLE. Loopback is ALWAYS allowed (health
     checks, the worker tier, local curl).

Design constraints (mirroring `autopilot.enabled()` / `vision_capture.enabled()`):
  * DEFAULT OFF — both settings read OFF by default and fail toward OFF on any error.
  * NEVER RAISES — every public function swallows bad input and returns a safe value:
    `client_ip` falls back to `remote_addr`, `is_cloudflare_ip` -> False, `peer_allowed`
    fails OPEN (-> True) so a malformed range list can't lock everyone out.
  * PURE CORE — `client_ip` / `is_cloudflare_ip` / `peer_allowed` import no Flask, so they
    are trivially testable as plain functions.
"""
import ipaddress
import threading

import applog

log = applog.get("cloudflare")

# ── Settings (default OFF; mirror autopilot's read+fail-OFF style) ────────────────────
TRUST_SETTING = "trust_cloudflare"        # capability 1: trust CF-Connecting-IP
ORIGIN_SETTING = "cloudflare_only"        # capability 2: origin lock (403 non-CF peers)
RANGES_SETTING = "cloudflare_ip_ranges"   # admin override of the embedded CF CIDR list
PROXIES_SETTING = "cloudflare_trusted_proxies"  # local reverse proxy CIDRs (nginx, etc.)

# Loopback is ALWAYS a trusted local hop (health checks, the worker, local curl).
_LOOPBACK = ("127.0.0.0/8", "::1/128")

# ── Published Cloudflare IP ranges ───────────────────────────────────────────────────
# Source of truth (these change only rarely — refresh from these URLs, or override at
# runtime via the `cloudflare_ip_ranges` admin setting without a code change):
#   IPv4: https://www.cloudflare.com/ips-v4
#   IPv6: https://www.cloudflare.com/ips-v6
#   (machine-readable: https://api.cloudflare.com/client/v4/ips)
# Last updated: 2026-06 (2026-06).
CF_IPV4 = [
    "173.245.48.0/20",
    "103.21.244.0/22",
    "103.22.200.0/22",
    "103.31.4.0/22",
    "141.101.64.0/18",
    "108.162.192.0/18",
    "190.93.240.0/20",
    "188.114.96.0/20",
    "197.234.240.0/22",
    "198.41.128.0/17",
    "162.158.0.0/15",
    "104.16.0.0/13",
    "104.24.0.0/14",
    "172.64.0.0/13",
    "131.0.72.0/22",
]
CF_IPV6 = [
    "2400:cb00::/32",
    "2606:4700::/32",
    "2803:f800::/32",
    "2405:b500::/32",
    "2405:8100::/32",
    "2a06:98c0::/29",
    "2c0f:f248::/32",
]


def _read(setting, default="off"):
    """Read an app setting, never raising. Mirrors autopilot.enabled()'s read style."""
    try:
        import auth
        return str(auth.get_setting(setting, default) or default)
    except Exception as e:
        log.warning("setting read %s failed — defaulting to %r: %s", setting, default, e)
        return default


def _is_on(setting):
    return _read(setting, "off").strip().lower() in ("on", "1", "true", "yes")


def trust_enabled():
    """True only when the admin opt-in `trust_cloudflare` setting is ON. Never raises ->
    False (fail toward OFF: the CF-Connecting-IP header is then never trusted)."""
    return _is_on(TRUST_SETTING)


def origin_lock_enabled():
    """True only when the admin opt-in `cloudflare_only` setting is ON. Never raises ->
    False (fail toward OFF: the before_request hook is then a no-op)."""
    return _is_on(ORIGIN_SETTING)


def _split_cidrs(raw):
    """Split a newline/comma-separated CIDR string into a clean list (drops blanks)."""
    if not raw:
        return []
    out = []
    for tok in str(raw).replace(",", "\n").splitlines():
        tok = tok.strip()
        if tok:
            out.append(tok)
    return out


# Parsed-network cache, keyed by the tuple of CIDR strings it was built from, so an admin
# changing the override setting rebuilds it without restarting. Guarded for thread safety.
_NET_CACHE = {}
_NET_LOCK = threading.Lock()


def _networks(cidrs):
    """Parsed `ip_network` objects for a list of CIDR strings, cached. Bad entries are
    skipped (logged once-ish via the cache miss). Never raises."""
    key = tuple(cidrs)
    nets = _NET_CACHE.get(key)
    if nets is not None:
        return nets
    parsed = []
    for c in cidrs:
        try:
            parsed.append(ipaddress.ip_network(c, strict=False))
        except (ValueError, TypeError) as e:
            log.warning("ignoring invalid CIDR %r: %s", c, e)
    with _NET_LOCK:
        _NET_CACHE[key] = parsed
    return parsed


def _cf_cidrs():
    """The active Cloudflare CIDR list: the admin `cloudflare_ip_ranges` override when set,
    else the embedded published lists."""
    override = _split_cidrs(_read(RANGES_SETTING, ""))
    if override:
        return override
    return CF_IPV4 + CF_IPV6


def trusted_proxies():
    """CIDRs for a LOCAL reverse proxy sitting BETWEEN Cloudflare and the app (e.g. an
    nginx terminating TLS in front of waitress). Loopback is ALWAYS included. The optional
    `cloudflare_trusted_proxies` setting appends to it."""
    return list(_LOOPBACK) + _split_cidrs(_read(PROXIES_SETTING, ""))


def _in_networks(ip, cidrs):
    """True when `ip` (a string) is inside any of the `cidrs`. Never raises -> False on a
    bad candidate or empty list."""
    try:
        addr = ipaddress.ip_address(str(ip).strip())
    except (ValueError, TypeError):
        return False
    for net in _networks(cidrs):
        try:
            if addr.version == net.version and addr in net:
                return True
        except (ValueError, TypeError):
            continue
    return False


def is_cloudflare_ip(ip):
    """True when `ip` is inside a published (or admin-overridden) Cloudflare range. Never
    raises -> False on a bad input."""
    return _in_networks(ip, _cf_cidrs())


def _is_trusted_hop(remote_addr):
    """True when `remote_addr` is a Cloudflare edge IP, a configured local trusted proxy,
    or loopback — i.e. a peer we accept as a legitimate front for the real client."""
    return is_cloudflare_ip(remote_addr) or _in_networks(remote_addr, trusted_proxies())


def client_ip(remote_addr, header_val):
    """Resolve the REAL client IP. PURE function (no Flask import).

    Returns `header_val` (the CF-Connecting-IP value) ONLY when ALL hold:
      * `trust_enabled()` (the `trust_cloudflare` setting is ON),
      * `remote_addr` is a trusted hop (a Cloudflare IP / a configured trusted proxy /
        loopback), AND
      * `header_val` parses as a valid IP address.
    Otherwise returns `remote_addr` UNCHANGED — so with the setting OFF (the default) this
    is byte-identical to using `request.remote_addr`, and a forged CF-Connecting-IP from a
    NON-Cloudflare peer is ignored. Never raises -> returns `remote_addr` on any error."""
    try:
        if not trust_enabled():
            return remote_addr
        if not _is_trusted_hop(remote_addr):
            return remote_addr
        cand = (header_val or "").strip()
        try:
            ipaddress.ip_address(cand)
        except (ValueError, TypeError):
            return remote_addr
        return cand
    except Exception as e:
        log.warning("client_ip resolve failed — using remote_addr: %s", e)
        return remote_addr


def peer_allowed(remote_addr):
    """For the ORIGIN LOCK: True when `remote_addr` is a Cloudflare IP, a configured trusted
    proxy, or loopback. Never raises -> True (fail OPEN: a malformed range list must NOT lock
    everyone out; the SETTING being OFF already means this isn't called in the hot path)."""
    try:
        return _is_trusted_hop(remote_addr)
    except Exception as e:
        log.warning("peer_allowed check failed — failing OPEN: %s", e)
        return True
