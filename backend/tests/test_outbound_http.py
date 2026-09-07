"""PAT-028 (Paperless-ngx, reference integration 2026-09-07): the connect-time
public-address pin for user-configured outbound destinations."""

import ipaddress

import httpx
import pytest

from app.core import outbound_http


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("8.8.8.8", True),
        ("93.184.216.34", True),
        ("2606:4700::1111", True),
        ("127.0.0.1", False),
        ("10.0.0.1", False),
        ("172.16.5.5", False),
        ("192.168.1.1", False),
        ("169.254.169.254", False),  # cloud metadata
        ("100.64.0.1", False),  # RFC 6598 shared space — is_global is False but be explicit
        ("100.127.255.254", False),
        ("64:ff9b::7f00:1", False),  # NAT64-mapped 127.0.0.1
        ("64:ff9b::a00:1", False),  # NAT64-mapped 10.0.0.1
        ("::1", False),
        ("::", False),
        ("fe80::1", False),
        ("fc00::1", False),
        ("ff02::1", False),
        ("0.0.0.0", False),
    ],
)
def test_public_address_classification(raw, expected):
    assert outbound_http.is_public_address(ipaddress.ip_address(raw)) is expected


def test_pin_request_preserves_host_and_tls_sni(monkeypatch):
    monkeypatch.setattr(outbound_http, "resolve_hostname_ips", lambda host, port: ["93.184.216.34"])
    req = httpx.Request(
        "POST",
        "https://example.com:8443/hook?x=1",
        headers={"Host": "attacker.example", "X-Test": "1"},
        content=b"body",
    )
    pinned = outbound_http.pin_request(req)
    assert pinned.url.host == "93.184.216.34"
    assert pinned.url.port == 8443
    assert pinned.headers["Host"] == "example.com:8443"  # a caller-supplied Host is overwritten
    assert pinned.headers["X-Test"] == "1"
    assert pinned.extensions["sni_hostname"] == "example.com"
    assert pinned.url.path == "/hook"
    assert pinned.url.query == b"x=1"
    assert pinned.method == "POST"


def test_pin_request_default_port_host_header_has_no_port(monkeypatch):
    monkeypatch.setattr(outbound_http, "resolve_hostname_ips", lambda host, port: ["93.184.216.34"])
    pinned = outbound_http.pin_request(httpx.Request("POST", "https://example.com/hook"))
    assert pinned.headers["Host"] == "example.com"


def test_pin_request_resolves_with_the_url_port(monkeypatch):
    seen = []

    def _resolve(host, port):
        seen.append((host, port))
        return ["93.184.216.34"]

    monkeypatch.setattr(outbound_http, "resolve_hostname_ips", _resolve)
    outbound_http.pin_request(httpx.Request("POST", "http://example.com/hook"))
    outbound_http.pin_request(httpx.Request("POST", "https://example.com:8443/hook"))
    assert seen == [("example.com", 80), ("example.com", 8443)]


def test_pin_request_rejects_private_rebind(monkeypatch):
    monkeypatch.setattr(outbound_http, "resolve_hostname_ips", lambda host, port: ["127.0.0.1"])
    with pytest.raises(outbound_http.UnsafeOutboundUrl):
        outbound_http.pin_request(httpx.Request("POST", "https://example.com/hook"))


def test_any_private_dns_answer_rejects_whole_destination(monkeypatch):
    """A resolver that answers one public and one private address is the
    rebinding shape — the whole destination is refused, not just that answer."""
    monkeypatch.setattr(
        outbound_http, "resolve_hostname_ips", lambda host, port: ["93.184.216.34", "10.0.0.1"]
    )
    with pytest.raises(outbound_http.UnsafeOutboundUrl):
        outbound_http.pin_request(httpx.Request("POST", "https://example.com/hook"))


def test_literal_private_ip_is_refused_without_resolving(monkeypatch):
    def _never(host, port):  # pragma: no cover - the assertion is that it is not called
        raise AssertionError("a literal address must not be resolved")

    monkeypatch.setattr(outbound_http, "resolve_hostname_ips", _never)
    with pytest.raises(outbound_http.UnsafeOutboundUrl):
        outbound_http.pin_request(httpx.Request("POST", "http://169.254.169.254/latest/meta-data"))
    pinned = outbound_http.pin_request(httpx.Request("POST", "http://93.184.216.34/hook"))
    assert pinned.url.host == "93.184.216.34"


def test_literal_ipv6_host_header_is_bracketed(monkeypatch):
    pinned = outbound_http.pin_request(httpx.Request("POST", "https://[2606:4700::1111]:8443/h"))
    assert pinned.headers["Host"] == "[2606:4700::1111]:8443"


def test_unresolvable_hostname_is_a_connect_error_not_a_block(monkeypatch):
    """NXDOMAIN is a transient network failure (retry), not an SSRF verdict."""
    import socket

    def _fail(*a, **k):
        raise socket.gaierror("no such host")

    monkeypatch.setattr(socket, "getaddrinfo", _fail)
    with pytest.raises(httpx.ConnectError):
        outbound_http.resolve_hostname_ips("nowhere.invalid", 443)


@pytest.mark.asyncio
async def test_transport_refuses_before_any_socket_is_opened(monkeypatch):
    monkeypatch.setattr(outbound_http, "resolve_hostname_ips", lambda host, port: ["10.1.2.3"])
    transport = outbound_http.PinnedPublicAsyncHTTPTransport()
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(outbound_http.UnsafeOutboundUrl):
            await client.post("https://example.com/hook", content=b"{}")
