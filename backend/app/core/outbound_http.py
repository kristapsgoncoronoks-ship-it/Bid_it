"""Safe outbound HTTP transport for user-configurable destinations.

PAT-028 (Paperless-ngx, reference integration 2026-09-07). `webhooks.assert_public_url`
validates a hostname's DNS answers BEFORE the request and httpx then resolves the
name AGAIN when it connects — a rebinding resolver can answer a public address to
the check and a private one to the connect (TOCTOU). This transport closes that
window: it resolves immediately before sending, rejects the whole destination if
ANY answer is non-public, and connects to one vetted IP while preserving the
original Host header and TLS SNI so the receiver still sees its own name.

"Public" is decided by the address-class checks the webhook guard always used
(loopback, link-local, private, multicast, reserved, unspecified) plus
`is_global`, plus two ranges named explicitly for readers and for interpreter
drift: RFC 6598 shared address space (100.64.0.0/10, used inside cloud networks;
`is_global` is False for it on Python 3.11) and the RFC 6052 NAT64 prefix
(64:ff9b::/96, which maps every IPv4 address — including private ones — into
IPv6; refused today by `is_reserved`, while its `is_global` reads True).
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket

import httpx

_NON_PUBLIC_NETWORKS = (
    ipaddress.ip_network("100.64.0.0/10"),  # RFC 6598 shared / CGNAT space
    ipaddress.ip_network("64:ff9b::/96"),  # RFC 6052 NAT64 well-known prefix
)


class UnsafeOutboundUrl(ValueError):
    """The outbound destination can reach a non-public network."""


def is_public_address(addr: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Globally routable unicast only.

    `is_global` alone is not enough: `ipaddress` reports the IPv6 link-local
    multicast group ff02::1 (every node on the link) as global. The explicit
    class checks are the ones `webhooks._addr_is_public` always applied; the
    two networks are the ones it missed."""
    if (
        addr.is_multicast
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_reserved
        or addr.is_unspecified
        or addr.is_private
    ):
        return False
    return addr.is_global and not any(addr in network for network in _NON_PUBLIC_NETWORKS)


def resolve_hostname_ips(hostname: str, port: int) -> list[str]:
    """Every address the resolver answers for `hostname`, in answer order, deduplicated.
    The seam tests patch."""
    try:
        infos = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise httpx.ConnectError(f"Could not resolve hostname: {hostname}") from exc
    ips = list(dict.fromkeys(str(info[4][0]) for info in infos if info and info[4]))
    if not ips:
        raise httpx.ConnectError(f"Could not resolve hostname: {hostname}")
    return ips


def _format_host(host: str) -> str:
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        return host
    return f"[{host}]" if addr.version == 6 else host


def _destination(request: httpx.Request) -> tuple[str, int, int]:
    """(host, port, default_port) — the pieces both the resolver and the pin need."""
    host = request.url.host
    if not host:
        raise UnsafeOutboundUrl("url has no host")
    default_port = 443 if request.url.scheme == "https" else 80
    return host, request.url.port or default_port, default_port


def _is_literal(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


def pin_request(request: httpx.Request, *, resolved: list[str] | None = None) -> httpx.Request:
    """Return a copy of `request` addressed to one vetted public IP.

    `resolved` is the resolver's answer set when the caller already resolved
    (the async transport does, off the event loop); otherwise it is resolved
    here. Raises `UnsafeOutboundUrl` when the host is, or resolves to, a
    non-public address. The Host header and SNI keep the ORIGINAL hostname."""
    host, port, default_port = _destination(request)
    if _is_literal(host):
        ips = [host]
    elif resolved is not None:
        ips = list(resolved)
    else:
        ips = resolve_hostname_ips(host, port)
    if not ips:
        raise httpx.ConnectError(f"Could not resolve hostname: {host}")
    for ip_str in ips:
        try:
            addr = ipaddress.ip_address(ip_str)
        except ValueError as exc:
            raise UnsafeOutboundUrl(f"invalid resolved address for {host}") from exc
        if not is_public_address(addr):
            raise UnsafeOutboundUrl(f"connection blocked: {host} resolves to a non-public address")

    pinned_ip = ips[0]
    headers = httpx.Headers(request.headers)
    headers.pop("host", None)
    host_header = _format_host(host)
    if request.url.port and request.url.port != default_port:
        host_header = f"{host_header}:{request.url.port}"
    headers["Host"] = host_header

    extensions = dict(request.extensions)
    extensions["sni_hostname"] = host
    return httpx.Request(
        method=request.method,
        url=request.url.copy_with(host=pinned_ip),
        headers=headers,
        stream=request.stream,
        extensions=extensions,
    )


class PinnedPublicAsyncHTTPTransport(httpx.AsyncHTTPTransport):
    """`httpx.AsyncHTTPTransport` that pins every request through `pin_request`.

    Name resolution is blocking (`socket.getaddrinfo`), so it runs in a worker
    thread rather than on the event loop — the same rule the rest of the
    backend follows for blocking IO (`tests/test_blocking_io_off_the_loop.py`)."""

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        host, port, _default = _destination(request)
        resolved = (
            None if _is_literal(host) else await asyncio.to_thread(resolve_hostname_ips, host, port)
        )
        return await super().handle_async_request(pin_request(request, resolved=resolved))
