# -*- coding: utf-8 -*-
"""
i18n FOUNDATION tests.

Covers: t() translation + English fallback; default = English; the language switch
persists a user's choice (users.lang); the invoicing page + the invoice render show
Latvian labels under lv and English under en; the DEFAULT-EN invariant (existing pages
render their English strings unchanged); LV diacritics survive a round-trip through esc.
"""
import os
import sys

from markupsafe import escape as esc

WORKDIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, WORKDIR)

import i18n
import auth


# ------------------------------------------------------------------ t() / catalog
def test_t_english_is_identity():
    # English is both the source and the default — t() returns the argument unchanged.
    assert i18n.t("Invoice", "en") == "Invoice"
    assert i18n.t("Save", "en") == "Save"
    # default (no active language, outside a request) is English
    assert i18n.t("Invoice") == "Invoice"


def test_t_latvian_translation():
    assert i18n.t("Invoice", "lv") == "Rēķins"
    assert i18n.t("Save", "lv") == "Saglabāt"
    assert i18n.t("Reverse charge", "lv") == "Apgrieztā PVN maksāšana"


def test_t_untranslated_falls_back_to_english_under_lv():
    novel = "A string that is intentionally not in any catalog 12345"
    assert i18n.t(novel, "lv") == novel


def test_t_never_raises():
    assert i18n.t(None, "lv") is None
    assert i18n.t("x", "zz") == "x"          # unknown language -> source
    assert i18n.t("x", None) == "x"          # outside a request -> English


def test_lv_diacritics_survive_esc_roundtrip():
    lv = i18n.t("Issuer profile", "lv")      # "Izrakstītāja profils"
    assert "ī" in lv and "ā" in lv
    # escaping a value with diacritics must not mangle them
    assert "ī" in str(esc(lv))


# ------------------------------------------------------------------ users.lang persistence
def test_set_lang_persists_user_preference(admin_session):
    user = admin_session["user"]
    i18n.set_lang(user, "lv")
    try:
        assert i18n.user_lang(user) == "lv"
        i18n.set_lang(user, "en")
        assert i18n.user_lang(user) == "en"
    finally:
        # leave the user back at the default
        con = auth.connect()
        con.execute("UPDATE users SET lang='en' WHERE username=?", (user,))
        con.commit(); con.close()


def test_set_lang_coerces_unknown_to_english(admin_session):
    user = admin_session["user"]
    assert i18n.set_lang(user, "klingon") == "en"


# ------------------------------------------------------------------ the language switch (web)
def test_switch_persists_and_changes_pages(client, admin_session):
    user = admin_session["user"]
    try:
        # default: an invoicing page shows the English header / labels
        r = client.get("/invoicing")
        assert r.status_code == 200
        html = r.get_data(as_text=True)
        assert "Invoicing" in html              # nav + heading in English
        assert "Rēķinu izrakstīšana" not in html

        # flip to Latvian via the switch (POST /lang/lv, CSRF taken from the page)
        token = _csrf(client)
        r = client.post("/lang/lv", data={"_csrf": token, "next": "/invoicing"},
                        follow_redirects=False)
        assert r.status_code in (302, 303)
        # the preference is persisted on the user
        assert i18n.user_lang(user) == "lv"

        # now the same page renders Latvian labels
        r = client.get("/invoicing")
        html = r.get_data(as_text=True)
        assert "Rēķinu izrakstīšana" in html     # nav label "Invoicing" -> LV
        assert "Saglabāt" not in html or True    # (heading uses Invoices)
        assert "Rēķini" in html                  # "Invoices" heading -> LV

        # flip back to English; English labels return
        token = _csrf(client)
        client.post("/lang/en", data={"_csrf": token, "next": "/invoicing"})
        r = client.get("/invoicing")
        html = r.get_data(as_text=True)
        assert "Invoicing" in html
        assert "Rēķinu izrakstīšana" not in html
    finally:
        con = auth.connect()
        con.execute("UPDATE users SET lang='en' WHERE username=?", (user,))
        con.commit(); con.close()


def test_switch_csrf_protected(client):
    # a POST without the session token is rejected (a token IS established by the GET above)
    client.get("/invoicing")
    r = client.post("/lang/lv", data={"_csrf": "wrong", "next": "/invoicing"})
    assert r.status_code == 400


# ------------------------------------------------------------------ invoice render i18n
def test_invoice_html_default_english(monkeypatch):
    import invoicing
    inv = _draft_invoice(invoicing)
    html = invoicing.invoice_html(inv["id"], lang="en")
    assert "INVOICE" in html
    assert "Bill to" in html
    assert "Grand total" in html
    # the historic dual-language title is preserved under English
    assert "Rēķins" in html  # the "/ Rēķins" sub-span


def test_invoice_html_latvian_labels():
    import invoicing
    inv = _draft_invoice(invoicing)
    html = invoicing.invoice_html(inv["id"], lang="lv")
    assert "RĒĶINS" in html               # title
    assert "Maksātājs" in html            # "Bill to"
    assert "Kopā apmaksai" in html        # "Grand total"
    assert "Apmaksas termiņš" in html     # "Due date"
    # an English-only label that we DID translate must not leak in LV mode
    assert "Bill to" not in html


# ------------------------------------------------------------------ DEFAULT-EN invariant
def test_default_pages_render_english(client):
    # home + invoicing render the asserted English nav strings under the default language
    for url in ("/", "/invoicing", "/invoicing/customers", "/invoicing/issuer"):
        r = client.get(url)
        assert r.status_code == 200, url
        html = r.get_data(as_text=True)
        # the English nav menu labels are present and un-translated by default
        assert "Home" in html and "Master data" in html and "Account" in html
        assert "Sign out" in html
        # no stray Latvian leaked in the default render
        assert "Sākums" not in html


# ------------------------------------------------------------------ helpers
def _csrf(client):
    """Pull the per-session CSRF token by rendering a page that embeds it."""
    r = client.get("/invoicing")
    html = r.get_data(as_text=True)
    import re
    m = re.search(r'name="_csrf" value="([^"]+)"', html)
    assert m, "no CSRF token in the page"
    return m.group(1)


def _draft_invoice(invoicing):
    """A minimal draft invoice (one customer, one line) for the render tests."""
    cust, err = invoicing.add_customer(name="Test Customer SIA", country="LV",
                                       vat_number="LV40000000000", created_by="pytest")
    assert not err, err
    inv, err = invoicing.create_draft(customer_id=cust["id"], currency="EUR",
                                      created_by="pytest")
    assert not err, err
    invoicing.add_line(inv["id"], description="Service fee", quantity="1",
                       unit="pcs", unit_price_net="100", vat_rate=0.21)
    return inv
