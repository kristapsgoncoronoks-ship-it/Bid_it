"""SAML 2.0 — Service-Provider (SP) request side + metadata (ADR-0021).

What is IMPLEMENTED here is the safe, deterministic, offline-provable half:
  • build_authn_request  — the `<samlp:AuthnRequest>` XML (SP-initiated),
  • redirect_binding_url — HTTP-Redirect binding (DEFLATE + base64 + urlencode),
  • sp_metadata_xml      — our SP metadata for the customer to register.

What is DELIBERATELY NOT implemented is **assertion consumption** — validating a
signed SAML `Response`/`Assertion` (XML-DSig: signature, canonicalization,
signature-wrapping defences, conditions/audience/NotOnOrAfter). A bespoke
validator here would be an authentication bypass. `consume_assertion` raises
`SamlNotReady`: finishing it requires a vetted XML-DSig library (pysaml2 / xmlsec)
and a real IdP's metadata — the ADR-0021 boundary. No unvalidated path ships.
"""

from __future__ import annotations

import base64
import zlib
from urllib.parse import urlencode
from xml.sax.saxutils import escape

_NS_SAMLP = "urn:oasis:names:tc:SAML:2.0:protocol"
_NS_SAML = "urn:oasis:names:tc:SAML:2.0:assertion"
_NS_MD = "urn:oasis:names:tc:SAML:2.0:metadata"
_BINDING_REDIRECT = "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect"
_BINDING_POST = "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST"
_NAMEID_EMAIL = "urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress"


class SamlError(Exception):
    """A recoverable SAML failure."""


class SamlNotReady(SamlError):
    """SAML assertion consumption is not yet available on this build (ADR-0021)."""


def build_authn_request(
    *, request_id: str, issue_instant: str, sp_entity_id: str, acs_url: str, idp_sso_url: str
) -> str:
    """The SP-initiated `<samlp:AuthnRequest>` XML (unsigned; redirect binding
    signs at the query-string layer when required)."""
    return (
        f'<samlp:AuthnRequest xmlns:samlp="{_NS_SAMLP}" xmlns:saml="{_NS_SAML}" '
        f'ID="{escape(request_id)}" Version="2.0" IssueInstant="{escape(issue_instant)}" '
        f'Destination="{escape(idp_sso_url)}" '
        f'ProtocolBinding="{_BINDING_POST}" '
        f'AssertionConsumerServiceURL="{escape(acs_url)}">'
        f"<saml:Issuer>{escape(sp_entity_id)}</saml:Issuer>"
        f'<samlp:NameIDPolicy Format="{_NAMEID_EMAIL}" AllowCreate="true"/>'
        f"</samlp:AuthnRequest>"
    )


def redirect_binding_url(
    idp_sso_url: str, authn_request_xml: str, *, relay_state: str | None = None
) -> str:
    """Encode an AuthnRequest for the HTTP-Redirect binding: raw-DEFLATE, base64,
    URL-encode as `SAMLRequest` (+ optional RelayState)."""
    deflated = _deflate(authn_request_xml.encode())
    params = {"SAMLRequest": base64.b64encode(deflated).decode()}
    if relay_state:
        params["RelayState"] = relay_state
    sep = "&" if "?" in idp_sso_url else "?"
    return f"{idp_sso_url}{sep}{urlencode(params)}"


def decode_redirect_saml(param_value: str) -> str:
    """Inverse of the redirect binding (base64 → raw-inflate) — for tests/tools."""
    return _inflate(base64.b64decode(param_value)).decode()


def sp_metadata_xml(*, sp_entity_id: str, acs_url: str) -> str:
    """Our SP metadata XML — the customer registers this with their IdP."""
    return (
        f'<md:EntityDescriptor xmlns:md="{_NS_MD}" entityID="{escape(sp_entity_id)}">'
        f'<md:SPSSODescriptor AuthnRequestsSigned="false" WantAssertionsSigned="true" '
        f'protocolSupportEnumeration="{_NS_SAMLP}">'
        f"<md:NameIDFormat>{_NAMEID_EMAIL}</md:NameIDFormat>"
        f'<md:AssertionConsumerService Binding="{_BINDING_POST}" '
        f'Location="{escape(acs_url)}" index="0" isDefault="true"/>'
        f"</md:SPSSODescriptor></md:EntityDescriptor>"
    )


def consume_assertion(*args, **kwargs):
    """DELIBERATE BOUNDARY (ADR-0021): validating a signed SAML assertion needs a
    vetted XML-DSig library (pysaml2 / xmlsec) + a real IdP. We refuse rather than
    ship an unvalidated (bypassable) path."""
    raise SamlNotReady(
        "SAML assertion validation is not enabled on this build — it requires the "
        "pinned XML-DSig library and the IdP's metadata (see ADR-0021)."
    )


def _deflate(data: bytes) -> bytes:
    c = zlib.compressobj(9, zlib.DEFLATED, -zlib.MAX_WBITS)  # raw DEFLATE (no zlib header)
    return c.compress(data) + c.flush()


def _inflate(data: bytes) -> bytes:
    d = zlib.decompressobj(-zlib.MAX_WBITS)
    return d.decompress(data) + d.flush()
