"""SEC-1: Content-Disposition header-injection safety.

User-controlled filenames (uploaded attachment names, user-set batch/run
references) must never be interpolated raw into a Content-Disposition header —
a `"` closes the quoted value and a CR/LF splits the HTTP response. The shared
`content_disposition` helper sanitises the ASCII fallback and percent-encodes the
RFC 5987 `filename*`, and every download endpoint routes through it.
"""

import pytest

from app.core.security_headers import content_disposition

ISSUER = {
    "legal_name": "InvoiceIQ Demo BV",
    "vat_number": "NL123456789B01",
    "registration_number": "NL-KVK-12345678",
    "address_line1": "Keizersgracht 1",
    "city": "Amsterdam",
    "postal_code": "1015 CJ",
    "country": "NL",
    "iban": "NL91ABNA0417164300",
    "bic": "ABNANL2A",
    "email": "billing@invoiceiq.test",
}


async def _activate(auth_client):
    await auth_client.put("/api/v1/issuer", json=ISSUER)
    await auth_client.put("/api/v1/modules/issuing", json={"enabled": True})


# --- unit: the helper ----------------------------------------------------------


def test_plain_name_is_preserved():
    cd = content_disposition("invoice.pdf")
    assert cd.startswith('attachment; filename="invoice.pdf"')
    assert "filename*=UTF-8''invoice.pdf" in cd


def test_quote_cannot_break_out_of_the_header():
    # A crafted name with an embedded quote must not produce an unbalanced value.
    cd = content_disposition('evil".pdf')
    # The ASCII fallback strips the quote; the value stays a single quoted token.
    assert cd.count('"') == 2
    assert 'filename="evil_.pdf"' in cd


def test_crlf_is_neutralised():
    cd = content_disposition('a.pdf"\r\nX-Injected: 1')
    # The CR/LF (and the injected `:`) are replaced with `_`, so no real header
    # break survives — the payload is inert text inside the single quoted value.
    assert "\r" not in cd and "\n" not in cd
    assert cd.count('"') == 2


def test_empty_or_none_uses_fallback():
    assert 'filename="download"' in content_disposition(None)
    assert 'filename="report"' in content_disposition("   ", fallback="report")


def test_unicode_survives_via_rfc5987():
    cd = content_disposition("facturé—€.pdf")
    # ASCII fallback is sanitised; the star form carries the real bytes.
    assert "filename*=UTF-8''" in cd
    assert "%E2%82%AC" in cd  # the euro sign, percent-encoded


def test_disposition_kind_is_configurable():
    assert content_disposition("x.pdf", disposition="inline").startswith("inline;")


# --- integration: a malicious upload filename is safe on download --------------


@pytest.mark.asyncio
async def test_malicious_attachment_filename_is_sanitised_on_download(auth_client):
    await _activate(auth_client)
    inv = (
        await auth_client.post(
            "/api/v1/issued",
            json={
                "buyer_name": "Globex",
                "lines": [
                    {"description": "x", "quantity": "1", "unit_price": "10", "vat_rate": "0"}
                ],
            },
        )
    ).json()
    iid = inv["id"]

    evil = 'pwn";\r\nX-Injected: yes\r\n".txt'
    up = await auth_client.post(
        f"/api/v1/issued/{iid}/attachments",
        files={"file": (evil, b"data", "text/plain")},
    )
    assert up.status_code == 201, up.text
    aid = up.json()["id"]

    dl = await auth_client.get(f"/api/v1/issued/{iid}/attachments/{aid}/download")
    assert dl.status_code == 200
    cd = dl.headers["content-disposition"]
    # The injected header name never reaches the sanitised quoted fallback, and the
    # header value carries no raw CR/LF.
    assert "\r" not in cd and "\n" not in cd
    assert cd.count('"') == 2  # exactly one balanced quoted filename
    # And the response did not gain an injected header.
    assert "x-injected" not in {k.lower() for k in dl.headers}
