"""
INTERNATIONALIZATION (i18n) — gettext-style, ENGLISH-TEXT-AS-KEY translation.

The product's source language is ENGLISH and English is ALSO the fallback: every
user-facing string in the code stays written in plain English. To translate, you
wrap that string in ``t(...)`` and add the English→Latvian pair to the LV catalog
(``translations_lv.CATALOG``). Nothing else changes — an UNtranslated string keeps
rendering in English, so coverage can grow page-by-page without ever breaking a page.

    from i18n import t
    t("Save")                 # -> "Save"      (lang en, the default)
    t("Save")                 # -> "Saglabāt"  (lang lv, when the active language is lv)
    t("Some new label")       # -> "Some new label"  (lang lv, no catalog entry -> English)

WHY ENGLISH-AS-KEY (not invented key names like "btn.save"): we can wrap strings
incrementally and translate them later without first inventing/threading a key
namespace; the English source IS the key, so a missing translation is an obvious,
harmless fall-through to readable English.

ACTIVE LANGUAGE RESOLUTION (``current_lang``):
  1. an explicit ``lang=`` argument to ``t()`` (used by the invoice renderer, which
     carries its own language);
  2. otherwise the CURRENT request's language, resolved ONCE per request and cached
     on Flask's ``g`` (``request_lang``): the logged-in user's ``users.lang`` column
     (via ``auth``), else a ``lang`` session/cookie value (pre-login pages), else 'en'.
  3. outside any request context (CLI, worker, a unit test calling ``t`` bare) -> 'en'.

DEFAULT = ENGLISH, ALWAYS. With no language ever selected every call returns its
English argument unchanged, so existing behaviour/tests are byte-identical. Only an
ACTIVE language of 'lv' (a user/session preference) changes output.

PURITY / SAFETY. ``t()`` is pure and NEVER raises — any lookup/resolution failure
returns the English ``text`` unchanged. The returned string is an ordinary ``str``;
callers escape it exactly like any other dynamic value (``esc(t("Save"))``) — ``t``
does NOT mark anything HTML-safe.

HOW TO TRANSLATE THE NEXT PAGE (the incremental loop):
  1. In the page's code, wrap each user-facing literal: ``"Suppliers"`` -> ``t("Suppliers")``.
     (Escape as usual: ``esc(t("Suppliers"))`` for a DB-adjacent value, or just
     ``t("Suppliers")`` inside an already-static HTML fragment.)
  2. Add the English→Latvian pair to ``translations_lv.CATALOG``.
  3. That's it — English stays the fallback for anything you didn't translate yet.

LANGUAGES. ``LANGS`` is the supported set; today {'en', 'lv'}. Adding a third language
is a new catalog module + an entry here; ``t()`` and the switch generalise unchanged.
"""
import applog

log = applog.get("i18n")

# Supported UI languages. 'en' is the source AND the universal fallback.
DEFAULT_LANG = "en"
LANGS = ("en", "lv")
# Human labels for the language switch (shown in the header toggle).
LANG_LABELS = {"en": "EN", "lv": "LV"}

# The Latvian catalog: {english_source: latvian}. Kept in a plain-dict data module
# (no external dep). Anything NOT in here renders in English under lv.
try:
    from translations_lv import CATALOG as _LV
except Exception as e:                      # pragma: no cover - never break import
    log.warning("could not load translations_lv: %s", e)
    _LV = {}

# english-source -> {lang: translation}. Only 'lv' is populated today; 'en' is never
# stored (the source IS the English string).
_CATALOGS = {"lv": _LV}


def normalize(lang):
    """Coerce an arbitrary value to one of LANGS, defaulting to DEFAULT_LANG. Never raises."""
    try:
        l = (lang or "").strip().lower()
    except Exception:
        return DEFAULT_LANG
    return l if l in LANGS else DEFAULT_LANG


def t(text, lang=None):
    """Translate ``text`` into the active language. Returns ``text`` UNCHANGED when the
    active language is English (the source) OR when no translation exists (English is the
    fallback). ``lang=None`` resolves the CURRENT request's language (see current_lang).
    Pure; NEVER raises — any failure returns ``text``."""
    try:
        if text is None:
            return text
        l = normalize(lang) if lang is not None else current_lang()
        if l == DEFAULT_LANG:
            return text
        return _CATALOGS.get(l, {}).get(text, text)
    except Exception as e:                  # pragma: no cover - defensive
        log.debug("t() failed for %r/%r: %s", text, lang, e)
        return text


def has(text, lang):
    """True iff a non-empty translation of ``text`` exists in ``lang``'s catalog (used by
    tests / tooling). 'en' always returns True (the source is its own translation)."""
    l = normalize(lang)
    if l == DEFAULT_LANG:
        return True
    return text in _CATALOGS.get(l, {})


# ------------------------------------------------------------------ request-context glue
# These helpers are Flask-aware but import lazily so i18n stays importable with NO Flask
# (CLI, worker, a unit test). Outside a request the language is always DEFAULT_LANG.

def current_lang():
    """The active language for the CURRENT Flask request, or DEFAULT_LANG outside a request.
    Resolved ONCE per request and cached on ``flask.g`` (request_lang). NEVER raises."""
    try:
        from flask import g, has_request_context
        if not has_request_context():
            return DEFAULT_LANG
        cached = getattr(g, "_ffs_lang", None)
        if cached is not None:
            return cached
        lang = _resolve_request_lang()
        g._ffs_lang = lang
        return lang
    except Exception as e:                  # pragma: no cover - defensive
        log.debug("current_lang resolution failed: %s", e)
        return DEFAULT_LANG


def _resolve_request_lang():
    """Resolve the active language from (in order) the logged-in user's preference, then a
    session/cookie 'lang' (pre-login pages), then DEFAULT_LANG. Never raises."""
    from flask import session, request
    # 1) logged-in user preference (users.lang)
    try:
        user = session.get("user")
        if user:
            pref = user_lang(user)
            if pref:
                return normalize(pref)
    except Exception as e:
        log.debug("user lang lookup failed: %s", e)
    # 2) session value (set by the switch, survives login) then cookie (pre-login)
    try:
        sess_lang = session.get("lang")
        if sess_lang:
            return normalize(sess_lang)
    except Exception:
        pass
    try:
        ck = request.cookies.get("lang")
        if ck:
            return normalize(ck)
    except Exception:
        pass
    return DEFAULT_LANG


def invalidate():
    """Drop the per-request cached language (call after set_lang within a request so the
    new choice takes effect for the rest of that request). Best-effort, never raises."""
    try:
        from flask import g, has_request_context
        if has_request_context() and hasattr(g, "_ffs_lang"):
            delattr(g, "_ffs_lang")
    except Exception:
        pass


# ------------------------------------------------------------------ user preference (DB)
def user_lang(username):
    """The stored UI-language preference for a user ('en'/'lv'), or None if unset/unknown.
    Reads the ``users.lang`` column via auth.connect(). Never raises — returns None."""
    if not username:
        return None
    try:
        import auth
        con = auth.connect()
        row = con.execute("SELECT lang FROM users WHERE username=?", (username,)).fetchone()
        con.close()
        if row is None:
            return None
        val = row["lang"] if "lang" in row.keys() else None
        return (val or None)
    except Exception as e:
        log.debug("user_lang lookup failed for %r: %s", username, e)
        return None


def set_lang(username, lang):
    """Persist a user's UI-language preference (users.lang). Coerces to a supported lang.
    Best-effort; returns the stored value. Never raises (logs on failure)."""
    l = normalize(lang)
    if username:
        try:
            import auth
            con = auth.connect()
            con.execute("UPDATE users SET lang=? WHERE username=?", (l, username))
            con.commit(); con.close()
        except Exception as e:
            log.warning("set_lang persist failed for %r: %s", username, e)
    return l
