"""
services_status.py — single source of truth for the Admin "Services" control center.

For every toggleable platform service it returns a uniform descriptor so the admin panel
can render a labelled ON/OFF switch + a live status badge WITHOUT the operator ever
touching a command line:

    {
      "key":      stable id,
      "title":    short human title,
      "what":     one plain-language sentence of what it does,
      "toggleable": bool,           # True -> render an on/off switch (POST `set_service`)
      "setting":  app_settings key the switch writes ("on"/"off"), or None,
      "on":       bool,             # current switch state (intended)
      "status":   "active" | "off" | "needs_setup" | "info",
      "reason":   short status explanation (e.g. "No AI API key"),
      "fix":      what to do to make it active (UI action or, rarely, a server step),
    }

status:
  active      — on AND every prerequisite is met (working now)
  off         — switched off on purpose
  needs_setup — switched on but a prerequisite is missing (reason+fix say what/how)
  info        — not a toggle, a status line only (e.g. PDF rendering / OCR availability)

Pure-ish and defensive: it only READS settings + probes availability, never mutates, and
never raises (a probe failure degrades to a sensible default). Imports of optional modules
(sso, vision_capture, …) are guarded so a half-built environment still renders.
"""
import os
import shutil


def _get(setting, default=""):
    try:
        import auth
        return auth.get_setting(setting, default)
    except Exception:
        return default


def _on(setting):
    return str(_get(setting, "off") or "off").lower() in ("on", "1", "true", "yes")


def pdf_rendering_ok():
    """True when poppler's pdftoppm is on PATH — required for AI vision reading AND for OCR
    of scanned PDFs. This is a SYSTEM package (poppler-utils); it cannot be installed from
    the web UI, so the panel shows its status and the one server command to fix it."""
    try:
        return shutil.which("pdftoppm") is not None
    except Exception:
        return False


def ai_key_present():
    """True when a vision-capable AI provider key resolves (panel-stored or environment)."""
    try:
        import ai_verify
        return ai_verify._provider() is not None
    except Exception:
        # fall back to a raw env-var probe
        return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("OPENAI_API_KEY"))


def _sso_ready():
    try:
        import sso
        return bool(sso.enabled())
    except Exception:
        return False


# server-step fix string reused by the PDF-dependent services
_POPPLER_FIX = ("Install poppler on the server (one-time): "
                "sudo apt-get install -y poppler-utils, then restart.")
_KEY_FIX = "Add an AI provider API key in the “AI provider API keys” card above."


def services():
    """The ordered list of service descriptors for the control center."""
    out = []

    # ---- AI capture / verify / review (need an AI key; vision/verify also need poppler) --
    key = ai_key_present()
    poppler = pdf_rendering_ok()

    def _ai_status(on, needs_poppler):
        if not on:
            return ("off", "Switched off", "")
        if not key:
            return ("needs_setup", "No AI provider API key is configured", _KEY_FIX)
        if needs_poppler and not poppler:
            return ("needs_setup", "PDF rendering (poppler) is not installed on the server",
                    _POPPLER_FIX)
        return ("active", "On and ready", "")

    vc_on = _on("ai_vision_capture_enabled")
    st, rs, fx = _ai_status(vc_on, needs_poppler=True)
    out.append({"key": "ai_vision_capture", "title": "AI invoice reading (vision capture)",
                "what": "Reads scanned / unknown-layout invoices by sending the page images to "
                        "the AI, then turns them into a draft you confirm. Default off.",
                "toggleable": True, "setting": "ai_vision_capture_enabled",
                "on": vc_on, "status": st, "reason": rs, "fix": fx})

    av_on = _on("ai_verify_enabled")
    st, rs, fx = _ai_status(av_on, needs_poppler=True)
    out.append({"key": "ai_verify", "title": "AI verification against the PDF",
                "what": "Independently checks the captured draft against the original PDF and "
                        "flags any mismatch. Advisory — never changes a figure. Default off.",
                "toggleable": True, "setting": "ai_verify_enabled",
                "on": av_on, "status": st, "reason": rs, "fix": fx})

    rev_be = str(_get("ai_review_backend", "none") or "none").lower()
    rev_on = rev_be != "none"
    if not rev_on:
        st, rs, fx = ("off", "Switched off", "")
    elif not key:
        st, rs, fx = ("needs_setup", "No AI provider API key is configured", _KEY_FIX)
    else:
        st, rs, fx = ("active", f"On ({rev_be})", "")
    out.append({"key": "ai_review", "title": "AI review assistant",
                "what": "An advisory second opinion over already-extracted data (plausibility, "
                        "price-vs-history). Sends derived data only — never the PDF. Default off.",
                "toggleable": False, "setting": "ai_review_backend",
                "on": rev_on, "status": st, "reason": rs, "fix": fx})

    dc_on = _on("ai_doc_chat_enabled")
    if not dc_on:
        st, rs, fx = ("off", "Switched off", "")
    elif rev_be == "none" or not key:
        st, rs, fx = ("needs_setup", "Needs an AI backend / key (uses the review backend)", _KEY_FIX)
    else:
        st, rs, fx = ("active", "On and ready", "")
    out.append({"key": "ai_doc_chat", "title": "AI document assistant (chat)",
                "what": "Ask free-form questions about a document’s extracted data. Sends derived "
                        "data only. Default off.",
                "toggleable": True, "setting": "ai_doc_chat_enabled",
                "on": dc_on, "status": st, "reason": rs, "fix": fx})

    # ---- Auto-pilot intake -------------------------------------------------------------
    ap_on = _on("intake_autopilot_enabled")
    if not ap_on:
        st, rs, fx = ("off", "Switched off — every upload waits for your review", "")
    else:
        st, rs, fx = ("active", "On — high-confidence, verified, valid documents file "
                      "themselves; everything else waits for review", "")
    out.append({"key": "autopilot", "title": "Auto-pilot intake",
                "what": "Upload and walk away: documents that are high-confidence AND pass AI "
                        "verification AND the validation checks are filed automatically; only "
                        "exceptions wait for you. Default off.",
                "toggleable": True, "setting": "intake_autopilot_enabled",
                "on": ap_on, "status": st, "reason": rs, "fix": fx})

    # ---- Single sign-on ---------------------------------------------------------------
    sso_on = _on("sso_enabled")
    if not sso_on:
        st, rs, fx = ("off", "Switched off — username/password login only", "")
    elif not _sso_ready():
        st, rs, fx = ("needs_setup", "Enabled but issuer / client id / secret not fully set",
                      "Fill in the Single sign-on card (issuer, client id, secret).")
    else:
        st, rs, fx = ("active", "On — staff can sign in with their company account", "")
    out.append({"key": "sso", "title": "Single sign-on (SSO)",
                "what": "Let staff log in with Google or Microsoft instead of a separate "
                        "password. Local/admin login always still works. Default off.",
                "toggleable": True, "setting": "sso_enabled",
                "on": sso_on, "status": st, "reason": rs, "fix": fx})

    # ---- Invoice issuance & Dokobit signing -------------------------------------------
    dk_on = _on("dokobit_enabled")
    dk_token = False
    try:
        import dokobit
        dk_token = bool(dokobit.has_token())
    except Exception:
        dk_token = False
    if not dk_on:
        st, rs, fx = ("off", "Switched off — invoices are built/stored but not signed", "")
    elif not dk_token:
        st, rs, fx = ("needs_setup", "Enabled but no Dokobit API token is saved",
                      "Add the API access token in the “Invoice issuance & Dokobit” card.")
    else:
        st, rs, fx = ("active", "On — issued fee invoices can be signed & e-delivered", "")
    out.append({"key": "dokobit", "title": "Invoice signing & e-delivery (Dokobit)",
                "what": "Sign and e-deliver the customer service-fee invoice via the Dokobit "
                        "Gateway. Off = the invoice is still built and stored, just not signed. "
                        "Default off.",
                "toggleable": True, "setting": "dokobit_enabled",
                "on": dk_on, "status": st, "reason": rs, "fix": fx})

    # ---- Schedulers -------------------------------------------------------------------
    try:
        hrs = int(float(_get("backup_interval_hours", "0") or 0))
    except (TypeError, ValueError):
        hrs = 0
    out.append({"key": "backup_scheduler", "title": "Automatic backups",
                "what": "Periodically snapshots the databases + documents to a verified, "
                        "hash-checked backup zip. Set the interval in the Backups card.",
                "toggleable": False, "setting": "backup_interval_hours",
                "on": hrs > 0,
                "status": ("active" if hrs > 0 else "off"),
                "reason": (f"Every {hrs} hour(s)" if hrs > 0 else "Manual backups only"),
                "fix": "" if hrs > 0 else "Set an interval (hours) in the Backups card."})

    sc_on = _on("scrape_scheduler_enabled")
    out.append({"key": "scrape_scheduler", "title": "Automatic supplier fetch (scheduler)",
                "what": "Periodically pulls documents from configured supplier portals on the "
                        "worker tier (rate-limited). Needs configured portals. Default off.",
                "toggleable": True, "setting": "scrape_scheduler_enabled",
                "on": sc_on,
                "status": ("active" if sc_on else "off"),
                "reason": ("On" if sc_on else "Switched off"), "fix": ""})

    # Company onboarding gate — require the operating company's own legal entity (issuer
    # profile) to be registered before the service can be used at all. ON by default.
    cg_on = str(_get("require_company_profile", "on")).lower() not in ("off", "0", "false",
                                                                       "no", "")
    out.append({"key": "company_profile_gate",
                "title": "Require company profile before use",
                "what": "Locks the whole service until your company’s own legal entity (legal "
                        "name, registered address, VAT number) is set under Invoicing → Issuer "
                        "profile. Mandatory company identity for every invoice (EU Art. 226). "
                        "Default ON.",
                "toggleable": True, "setting": "require_company_profile",
                "on": cg_on,
                "status": ("active" if cg_on else "off"),
                "reason": ("Enforced" if cg_on else "Switched off"), "fix": ""})

    # ---- Info-only status lines (not switches) ----------------------------------------
    out.append({"key": "pdf_rendering", "title": "PDF reading (poppler)",
                "what": "The tool that turns PDF pages into images so the AI can read them and "
                        "so scanned PDFs can be OCR’d. Required for AI invoice reading.",
                "toggleable": False, "setting": None, "on": poppler,
                "status": ("info" if poppler else "needs_setup"),
                "reason": ("Installed and available" if poppler
                           else "NOT installed — AI reading & scan-OCR will fall back / fail"),
                "fix": "" if poppler else _POPPLER_FIX})

    langs = os.environ.get("EXTRACT_OCR_LANGS", "eng")
    out.append({"key": "ocr", "title": "OCR languages",
                "what": "Languages the on-server OCR can read from scanned PDFs.",
                "toggleable": False, "setting": None, "on": True, "status": "info",
                "reason": f"Active: {langs}", "fix": ""})

    return out


# settings that the generic `set_service` admin action is allowed to flip (defence in depth:
# the panel may only toggle these exact on/off keys, nothing arbitrary).
TOGGLEABLE_SETTINGS = {s["setting"] for s in services()
                       if s.get("toggleable") and s.get("setting")}
