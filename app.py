"""
FLEET FUEL ANALYTICS - WEB UI
Basic but scalable: server-rendered pages over fuel_history.db (SQLite), with a
parallel JSON API (/api/...) so any future frontend (React, Power BI, mobile)
consumes the same backend without changes.

Run locally:        python3 app.py            -> http://localhost:8050
Team deployment:    gunicorn -w 2 app:app     behind nginx; add auth at the proxy
Scale-up path:      swap DB() for PostgreSQL (one function), keep everything else.

Pages:  /            dashboard (KPIs, diesel benchmark, monthly trend)
        /compare     filterable comparison (period, supplier, country, product, dates)
        /headtohead  same-day same-country supplier overlaps + overpay
        /entities    per-entity totals & reclaimable VAT
        /stations    station scorecard with price drift
Exports: /export/master  /export/history   (download the Excel deliverables)
API:    /api/benchmark /api/compare /api/headtohead /api/entities /api/periods
"""
import sqlite3, os, secrets, threading, time
from flask import Flask, request, jsonify, render_template_string, send_file, session, redirect
from markupsafe import escape as esc
from werkzeug.middleware.proxy_fix import ProxyFix
import auth as _auth
import audit as _audit_mod

WORKDIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(WORKDIR, "fuel_history.db")
app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)
app.secret_key = _auth.secret_key()
import datetime as _dt
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax",
                  MAX_CONTENT_LENGTH=25 * 1024 * 1024,    # cap uploads at 25 MB (DoS guard)
                  PERMANENT_SESSION_LIFETIME=_dt.timedelta(hours=8))   # idle session timeout

# Static, no-secret CSP: scripts only from this origin (/app.js), inline styles
# allowed (the UI uses inline style attributes + inline SVG), no framing.
_CSP = ("default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
        "script-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'; "
        "object-src 'none'")

@app.after_request
def _security_headers(resp):
    resp.headers.setdefault("Content-Security-Policy", _CSP)
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("Referrer-Policy", "no-referrer")
    resp.headers.setdefault("Permissions-Policy", "geolocation=(), microphone=(), camera=()")
    # don't let browsers/proxies cache authenticated pages (the static /app.js sets
    # its own Cache-Control, so setdefault leaves it alone)
    resp.headers.setdefault("Cache-Control", "no-store")
    if request.is_secure:
        resp.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    return resp

APP_JS = r"""/* progressive enhancement: sort + filter + horizontal scroll + keyboard nav */
(function(){
  function val(td){return td?(td.textContent||'').trim():'';}
  function num(s){var n=parseFloat(String(s).replace(/[^0-9.\-]/g,''));return isNaN(n)?null:n;}
  function isTotal(r){return /\bTOTAL\b/i.test(r.textContent||'');}
  function sortTable(t,col,asc){
    var tb=t.tBodies[0]; if(!tb) return;
    var all=Array.prototype.slice.call(tb.rows);
    var body=all.filter(function(r){return !isTotal(r);}), totals=all.filter(isTotal);
    body.sort(function(a,b){
      var x=val(a.cells[col]),y=val(b.cells[col]),nx=num(x),ny=num(y),r;
      r=(nx!==null&&ny!==null)?nx-ny:x.localeCompare(y); return asc?r:-r;
    });
    body.concat(totals).forEach(function(r){tb.appendChild(r);});
  }
  // expose the (responsive, wrapping) nav height so sticky table headers can
  // park right below it; keep it current as the header reflows.
  function navh(){
    var h=document.querySelector('header');
    document.documentElement.style.setProperty('--navh',(h?h.offsetHeight:56)+'px');
  }
  navh(); window.addEventListener('resize',navh);
  document.querySelectorAll('table').forEach(function(t){
    var head=t.tHead;
    // Wrap for horizontal scroll ONLY when the table is genuinely wider than the
    // space it has. An overflow wrapper becomes a scroll container that defeats
    // the sticky header on page scroll, so narrow tables stay unwrapped and get
    // a header that sticks under the nav; wide ones trade that for h-scroll.
    if(t.parentNode && !t.parentNode.classList.contains('tablewrap')
       && t.offsetWidth > t.parentNode.clientWidth + 1){
      var w=document.createElement('div'); w.className='tablewrap';
      t.parentNode.insertBefore(w,t); w.appendChild(t);
    } else {
      t.classList.add('sticky');   // eligible for a sticky header
    }
    if(head&&head.rows.length){
      var ths=head.rows[0].cells, st={col:-1,asc:true};
      Array.prototype.forEach.call(ths,function(th,i){
        th.style.cursor='pointer'; th.title='click to sort';
        th.addEventListener('click',function(){
          st.asc=st.col===i?!st.asc:true; st.col=i; sortTable(t,i,st.asc);
          Array.prototype.forEach.call(ths,function(x){x.removeAttribute('data-sort');});
          th.setAttribute('data-sort',st.asc?'▲':'▼');
        });
      });
    }
    var tb=t.tBodies[0];
    if(tb&&tb.rows.length>=8){
      var inp=document.createElement('input');
      inp.placeholder='filter rows…'; inp.className='rowfilter';
      inp.addEventListener('input',function(){
        var q=inp.value.toLowerCase();
        Array.prototype.forEach.call(tb.rows,function(r){
          r.style.display=(!q||(r.textContent||'').toLowerCase().indexOf(q)>=0)?'':'none';
        });
      });
      var anchor=t.parentNode.classList.contains('tablewrap')?t.parentNode:t;
      anchor.parentNode.insertBefore(inp,anchor);
    }
  });
  // keyboard nav built from the actual menu: press "g" then the first letter of a menu item
  var NAV={}, labels=[];
  document.querySelectorAll('header a[href^="/"]').forEach(function(a){
    var txt=(a.textContent||'').trim(), k=txt.toLowerCase()[0];
    if(k && !NAV[k]){ NAV[k]=a.getAttribute('href'); labels.push(k+' → '+txt); }
  });
  function help(){
    var b=document.getElementById('kh');
    if(b){ b.remove(); return; }
    b=document.createElement('div'); b.id='kh';
    b.innerHTML='<div class="khbox"><b>Keyboard shortcuts</b>'
      +'<p><kbd>/</kbd> filter rows · <kbd>g</kbd> then a letter to jump · click a column to sort · <kbd>?</kbd> toggle this</p>'
      +'<div class="khgrid">'+labels.map(function(l){return '<span><kbd>g</kbd> '+l+'</span>';}).join('')+'</div>'
      +'<p class="note">Esc / ? to close</p></div>';
    b.addEventListener('click',function(e){if(e.target===b)b.remove();});
    document.body.appendChild(b);
  }
  var pend=false;
  document.addEventListener('keydown',function(e){
    var tag=(e.target&&e.target.tagName)||'';
    if(/^(INPUT|SELECT|TEXTAREA)$/.test(tag)) return;
    if(e.key==='Escape'){var b=document.getElementById('kh'); if(b)b.remove(); pend=false; return;}
    if(e.key==='/'){var el=document.querySelector('.rowfilter, form.f input, input'); if(el){e.preventDefault();el.focus();} return;}
    if(e.key==='?'){e.preventDefault(); help(); return;}
    if(pend){ pend=false; var u=NAV[e.key.toLowerCase()]; if(u){e.preventDefault(); location.href=u;} return; }
    if(e.key==='g'){ pend=true; setTimeout(function(){pend=false;},1200); }
  });
})();
"""

@app.route("/app.js")
def app_js():
    from flask import Response
    return Response(APP_JS, mimetype="application/javascript",
                    headers={"Cache-Control": "public, max-age=3600"})

def _csrf_token():
    """Lazily create and return a per-session CSRF token."""
    if not session.get("_csrf"):
        session["_csrf"] = secrets.token_urlsafe(32)
    return session["_csrf"]

def _csrf_input():
    return f'<input type="hidden" name="_csrf" value="{esc(_csrf_token())}">'

LOGIN_HTML = """<!doctype html><html><head><meta charset='utf-8'><title>Fleet Fuel - login</title>
<style>body{font:14px -apple-system,Segoe UI,Arial;background:#f4f6f8;display:flex;align-items:center;justify-content:center;height:100vh;margin:0}
.box{background:#fff;border:1px solid #dde4ea;border-radius:10px;padding:28px 30px;width:300px}
h1{font-size:16px;margin:0 0 14px}input{width:100%;box-sizing:border-box;padding:8px;margin:5px 0 12px;border:1px solid #dde4ea;border-radius:6px}
button{width:100%;background:#0e5fa8;color:#fff;border:0;border-radius:6px;padding:9px;cursor:pointer}
.err{color:#c8102e;font-size:13px;margin-bottom:8px}</style></head><body>
<div class="box"><h1>Fleet Fuel Analytics</h1>{ERR}
<form method="post"><input name="username" placeholder="username" autofocus required>
<input type="password" name="password" placeholder="password" required>
<button>Sign in</button></form></div></body></html>"""

SETUP_HTML = """<!doctype html><html><head><meta charset='utf-8'>
<meta name='viewport' content='width=device-width,initial-scale=1'>
<title>Fleet Fuel — first-time setup</title>
<style>
:root{--ink:#1a2733;--mut:#5f7385;--bd:#dde4ea;--ok:#1d9e75;--bad:#c8102e;--blue:#0e5fa8}
*{box-sizing:border-box}
body{font:15px/1.55 -apple-system,Segoe UI,Roboto,Arial;background:#eef2f5;margin:0;color:var(--ink)}
.wrap{max-width:520px;margin:5vh auto;padding:0 16px}
.card{background:#fff;border:1px solid var(--bd);border-radius:14px;padding:30px 32px;box-shadow:0 1px 3px rgba(0,0,0,.04)}
.logo{width:44px;height:44px;border-radius:10px;background:var(--blue);color:#fff;display:flex;
 align-items:center;justify-content:center;font-weight:700;font-size:20px;margin-bottom:16px}
h1{font-size:21px;margin:0 0 6px}.sub{color:var(--mut);margin:0 0 22px}
.steps{display:flex;gap:8px;margin-bottom:24px}
.dot{flex:1;height:4px;border-radius:2px;background:var(--bd)}.dot.on{background:var(--blue)}
label{display:block;font-size:13px;color:var(--mut);margin:14px 0 5px;font-weight:500}
input{width:100%;padding:11px 12px;border:1px solid var(--bd);border-radius:8px;font-size:15px}
input:focus{outline:2px solid var(--blue);outline-offset:-1px;border-color:var(--blue)}
button{width:100%;background:var(--blue);color:#fff;border:0;border-radius:8px;padding:12px;
 font-size:15px;font-weight:600;cursor:pointer;margin-top:22px}
button:hover{background:#0b4d89}
.err{background:#fdeaea;color:var(--bad);padding:10px 12px;border-radius:8px;font-size:14px;margin-bottom:8px}
.hint{font-size:12.5px;color:var(--mut);margin-top:6px}
.chk{display:flex;align-items:flex-start;gap:8px;margin-top:18px;font-size:14px}
.chk input{width:auto;margin-top:3px}
.done .big{font-size:40px;color:var(--ok);text-align:center}
.row{display:flex;align-items:center;gap:10px;padding:9px 0;border-top:1px solid var(--bd);font-size:14px}
.row .ic{color:var(--ok);font-weight:700}
.muted{color:var(--mut)}
a.btn{display:block;text-align:center;background:var(--blue);color:#fff;text-decoration:none;
 border-radius:8px;padding:12px;font-weight:600;margin-top:22px}
</style></head><body><div class="wrap"><div class="card">{BODY}</div>
<p style="text-align:center;color:#9fb3c4;font-size:12px;margin-top:14px">Fleet Fuel &amp; VAT Refund System</p>
</div></body></html>"""

@app.route("/setup", methods=["GET", "POST"])
def setup():
    # if setup already done, send to login
    if not _needs_setup():
        return redirect("/login")
    err = ""
    if request.method == "POST":
        u = request.form.get("username", "").strip()
        p = request.form.get("password", "")
        p2 = request.form.get("password2", "")
        if not u:
            err = "Please choose a username."
        elif len(p) < 8:
            err = "Password must be at least 8 characters."
        elif p != p2:
            err = "The two passwords do not match."
        if not err:
            _auth.add_user(u, p, "admin")
            _auth.set_role(u, "admin")
            # generate a self-signed cert if none configured and the option was ticked
            cert_made = False
            if request.form.get("makecert"):
                try:
                    import subprocess, sys, os as _os
                    if not (_os.path.exists(f"{WORKDIR}/cert.pem") or _os.environ.get("TLS_CERT")):
                        subprocess.run([sys.executable, f"{WORKDIR}/make_cert.py",
                                        request.form.get("host", "localhost") or "localhost"],
                                       capture_output=True, timeout=60)
                        cert_made = _os.path.exists(f"{WORKDIR}/cert.pem")
                except Exception:
                    cert_made = False
            # try a first backup (best effort)
            try:
                import backup; backup.snapshot()
            except Exception:
                pass
            rows = [("Administrator account created", esc(u)),
                    ("Password stored securely", "salted scrypt hash")]
            rows.append(("HTTPS certificate",
                         "self-signed, ready" if cert_made else "add later (see docs/INSTALL.md)"))
            rows.append(("First backup taken", "yes"))
            inner = ('<div class="done"><div class="big">&#10003;</div>'
                     '<h1 style="text-align:center">You\'re all set</h1>'
                     '<p class="sub" style="text-align:center">Everything is ready. '
                     'Sign in with the account you just created.</p>'
                     + "".join(f'<div class="row"><span class="ic">&#10003;</span>'
                               f'<span>{a}</span><span class="muted" style="margin-left:auto">{b}</span></div>'
                               for a, b in rows)
                     + '<a class="btn" href="/login">Go to sign in &rarr;</a>'
                     '<p class="hint" style="text-align:center;margin-top:16px">'
                     'Next: sign in, then add your colleagues under Admin (start them as '
                     '&ldquo;processor&rdquo; and tune their permissions). Daily guide: '
                     'docs/USER_MANUAL.md</p></div>')
            return SETUP_HTML.replace("{BODY}", inner)
    # GET or error: the create-admin form
    form = (
        '<div class="logo">FF</div>'
        '<div class="steps"><div class="dot on"></div><div class="dot"></div></div>'
        '<h1>Welcome — let\'s set up</h1>'
        '<p class="sub">Create your administrator account. This is a one-time step; '
        'it takes about a minute.</p>'
        + (f'<div class="err">{esc(err)}</div>' if err else '')
        + '<form method="post">'
        '<label>Administrator username</label>'
        f'<input name="username" value="{esc(request.form.get("username","")) if request.method=="POST" else ""}" autofocus required>'
        '<label>Password</label>'
        '<input type="password" name="password" required>'
        '<div class="hint">At least 8 characters. Stored as a salted hash — never in plain text.</div>'
        '<label>Repeat password</label>'
        '<input type="password" name="password2" required>'
        '<div class="chk"><input type="checkbox" name="makecert" id="mc" checked>'
        '<label for="mc" style="margin:0;color:var(--ink)">Create an HTTPS certificate now '
        '(self-signed — your browser will ask you to trust it once). Uncheck if your IT '
        'will install a company certificate.</label></div>'
        '<button>Create account &amp; finish</button></form>')
    return SETUP_HTML.replace("{BODY}", form)

@app.route("/login", methods=["GET", "POST"])
def login():
    err = ""
    if request.method == "POST":
        uname = request.form.get("username", "")
        if _auth.is_locked(uname) or _auth.is_locked_ip(request.remote_addr):
            err = ('<div class="err">Temporarily locked after too many failed attempts. '
                   'Try again in a few minutes.</div>')
        elif _auth.verify(uname, request.form.get("password", ""), remote=request.remote_addr or ""):
            session.clear()                         # session fixation: start fresh on login
            session["user"] = uname
            u = _auth.get_user(uname)
            session["role"] = (u or {}).get("role", "processor")
            session.permanent = True
            return redirect("/")
        else:
            err = '<div class="err">Invalid username or password.</div>'
    return LOGIN_HTML.replace("{ERR}", err)

@app.route("/logout")
def logout():
    session.clear()
    return redirect("/login")

FORBIDDEN = ('<div class="card"><h2>Insufficient permissions</h2>'
             '<p>Your role does not allow this action. An administrator can change '
             'roles in the Admin panel.</p></div>')

def _needs_setup():
    try:
        con = _auth.connect()
        n = con.execute("SELECT COUNT(*) FROM users WHERE active=1").fetchone()[0]
        con.close()
        return n == 0
    except Exception:
        return True

# Endpoint -> capability required to use it. Endpoints not listed are view-only
# (any logged-in user). Admin holds every capability; a processor holds the
# subset an admin has granted (see auth.PERMISSIONS / auth.has_perm).
PERM_BY_ENDPOINT = {
    "extract_batch":   "data_import", "extract_confirm": "data_import",
    "data_manager":    "data_import",
    "intake_queue_page": "data_import", "intake_review": "data_import",
    "doc_mining_page": "data_import",
    "invoice_ctrl":    "invoice_control", "contracts": "invoice_control",
    "vat":             "vat_claims", "api_vat": "vat_claims", "readiness": "vat_claims",
    "customers":       "customers",
    "pricing":         "pricing", "pricing_upload": "pricing", "api_pricing": "pricing",
    "pricing_market":  "pricing", "pricing_portal": "pricing",
    "pricing_adopt_benchmark": "pricing", "export_benchmark": "exports",
    "documents":       "documents", "doc_download": "documents",
    "export_master":   "exports", "export_history": "exports",
    "export_pricing":  "exports", "export_vat": "exports", "export_compare": "exports",
    "export_stations": "exports", "export_summary": "exports", "export_fee": "exports",
    "export_readiness": "exports", "export_fees": "exports",
    "admin":           "user_admin",   # server setup / overall software changes
}

@app.before_request
def _guard():
    if request.endpoint in ("setup", "static", "app_js") or request.endpoint is None:
        return
    if _needs_setup():
        return redirect("/setup")
    if request.endpoint == "login":
        return
    if not session.get("user"):
        return redirect("/login")
    role = session.get("role", "processor")
    # CSRF on every state-changing POST (login/setup are pre-session, exempt).
    if request.method == "POST" and request.endpoint not in ("login", "setup"):
        if request.form.get("_csrf") != session.get("_csrf"):
            return page('<div class="card"><h2>Invalid or missing CSRF token</h2>'
                        '<p>Please reload the page and try again.</p></div>', ""), 400
    # Capability enforcement: block any endpoint whose required permission the
    # current role lacks (covers both the page view and its POST action).
    req_perm = PERM_BY_ENDPOINT.get(request.endpoint)
    if req_perm and not _auth.has_perm(role, req_perm):
        return page(FORBIDDEN, ""), 403
    if request.method == "POST":
        # Actor is thread-local (audit triggers read it via ffs_actor()), so a
        # single set covers every connection this request opens — no per-DB churn.
        _audit_mod.set_actor(None, session["user"])

@app.after_request
def _reset_actor(resp):
    _audit_mod.reset_actor()
    return resp

def _log_exc(context, e):
    """Record a handled exception to the admin error log (keeps the user-facing
    banner unchanged)."""
    import traceback
    _auth.log_error(context, type(e).__name__, str(e),
                    traceback.format_exc(), session.get("user", ""))

@app.errorhandler(Exception)
def _on_error(e):
    """Log any unhandled exception and show a friendly page. Normal HTTP errors
    (404s, redirects, the 403/400 guard pages) pass through untouched."""
    from werkzeug.exceptions import HTTPException
    if isinstance(e, HTTPException):
        return e
    import traceback
    _auth.log_error(f"{request.method} {request.path}", type(e).__name__, str(e),
                    traceback.format_exc(), session.get("user", ""))
    return page('<div class="card"><h2>Something went wrong</h2>'
                '<p>The error has been logged — an administrator can review it in the '
                'Admin panel.</p></div>', ""), 500

def DB():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con

# ---------------------------------------------------------------- auto-backup
# Admin sets how often (security.db setting 'backup_interval_hours'; 0 = off /
# manual only). A daemon thread checks periodically and snapshots when due.
#
# MULTI-PROCESS: every worker process runs this loop, but only ONE may take
# scheduled backups — otherwise N processes would each snapshot when the schedule
# comes due. We elect a leader with a cross-process lease ("backup-scheduler");
# only the leader checks/snapshots, and it renews the lease each tick so if it
# dies another process takes over within one lease window. A second cross-process
# lock ("backup-run") guards the snapshot itself so a manual "Run backup now" in
# one process can never overlap a scheduled (or manual) backup in another.
_backup_lock = threading.Lock()           # in-process guard (fast path)
_sched_started = False
BACKUP_CHECK_SECONDS = 300  # how often the scheduler re-checks the schedule

def backup_interval_hours():
    try:
        return float(_auth.get_setting("backup_interval_hours", "0") or 0)
    except (TypeError, ValueError):
        return 0.0

def run_backup_now():
    """Take a snapshot under both the in-process lock and a cross-process lock, so
    backups never overlap even across worker processes. Returns (path, n_files).
    Raises RuntimeError if another process is already snapshotting."""
    import backup, process_lock
    me = process_lock.whoami()
    with _backup_lock:
        if not process_lock.acquire("backup-run", ttl=900, holder=me):
            raise RuntimeError("a backup is already running in another process")
        try:
            return backup.snapshot()
        finally:
            process_lock.release("backup-run", me)

def _backup_tick():
    """One scheduler iteration: snapshot if a scheduled backup is due. Returns the
    snapshot path if one was taken, else None. Never raises (logs instead)."""
    import backup, traceback
    try:
        hrs = backup_interval_hours()
        if hrs > 0 and backup.due(hrs):
            path, _ = run_backup_now()
            return path
    except Exception as e:
        try:
            _auth.log_error("auto-backup", type(e).__name__, str(e),
                            traceback.format_exc(), "system")
        except Exception:
            pass
    return None

def _backup_loop():
    import process_lock
    me = process_lock.whoami()
    while True:
        # only the elected leader checks the schedule / snapshots; the lease is
        # renewed here each tick and expires if this process dies.
        if process_lock.acquire("backup-scheduler", ttl=2 * BACKUP_CHECK_SECONDS, holder=me):
            _backup_tick()
        time.sleep(BACKUP_CHECK_SECONDS)

def start_backup_scheduler():
    """Start the background auto-backup thread once (called by the server
    entrypoints; not started during imports/tests)."""
    global _sched_started
    if _sched_started:
        return
    _sched_started = True
    threading.Thread(target=_backup_loop, name="backup-scheduler", daemon=True).start()

# ---------------------------------------------------------------- intake worker
# Drains the document "waiting room" in the background, one job at a time, so a
# burst of uploads is processed steadily instead of overloading the server.
#
# MULTI-PROCESS: unlike the backup scheduler this needs NO leader election — the
# queue claim uses BEGIN IMMEDIATE on intake.db, so any number of worker processes
# can drain concurrently and a job is handed to exactly one of them. Running it in
# every process simply adds throughput. A little random jitter on the idle poll
# keeps the processes from waking in lockstep. Started only by the server
# entrypoints (never on import), so tests/CLI are unaffected. Set INTAKE_WORKER=0
# to opt a process out (e.g. when you run a dedicated `python waiting_room.py
# --work` process instead).
_intake_started = False

def _intake_loop():
    import waiting_room as IQ, random
    while True:
        try:
            if IQ.drain() == 0:
                time.sleep(IQ.POLL_SECONDS + random.uniform(0, IQ.POLL_SECONDS))
        except Exception as e:
            try:
                import traceback
                _auth.log_error("intake-worker", type(e).__name__, str(e),
                                traceback.format_exc(), "system")
            except Exception:
                pass
            time.sleep(IQ.POLL_SECONDS)

def start_intake_worker():
    global _intake_started
    if _intake_started or os.environ.get("INTAKE_WORKER", "1") == "0":
        return
    _intake_started = True
    threading.Thread(target=_intake_loop, name="intake-worker", daemon=True).start()

# ---------------------------------------------------------------- intake gating
# New documents may not be added to the waiting room while earlier ones are still
# unprocessed (queued/waiting/held/processing/failed) — so a stuck backlog (e.g.
# an AI token outage) gets cleared before more piles on. An admin can grant a
# TEMPORARY override; it's stored as an expiry timestamp in security.db so it
# applies across all worker processes and lapses on its own.
INTAKE_OVERRIDE_MINUTES = 30

def _intake_override_until():
    try:
        return float(_auth.get_setting("intake_override_until", "0") or 0)
    except (TypeError, ValueError):
        return 0.0

def _intake_override_remaining():
    """Seconds left on the admin's temporary upload override (0 if none/expired)."""
    return max(0, int(_intake_override_until() - time.time()))

def _intake_uploads_blocked():
    """Returns (blocked, pending_count). Blocked when there's an unprocessed
    backlog and no active admin override."""
    import waiting_room as IQ
    pend = IQ.pending_count()
    if pend == 0:
        return False, 0
    return (_intake_override_remaining() <= 0), pend

# ---------------------------------------------------------------- queries
# The read-only aggregations live in queries.py (small brick, easy to test).
from queries import (q_periods, q_filters, where, q_compare, q_compare_totals,
                     q_benchmark, q_kpis, q_trend, q_headtohead, q_entities,
                     q_stations, q_savings)

def svg_hbars(pairs, unit="", width=520, color="#0e5fa8", fmt=",.0f"):
    """Dependency-free inline SVG horizontal bar chart from (label, value) pairs."""
    pairs = [(str(l), float(v or 0)) for l, v in pairs]
    if not pairs:
        return '<p class="note">No data for this selection.</p>'
    mx = max((v for _, v in pairs), default=0) or 1
    rh, gap, lblw = 20, 9, 130
    h = len(pairs) * (rh + gap) + 6
    parts = []
    for i, (l, v) in enumerate(pairs):
        y = i * (rh + gap) + 4
        bw = max(1.0, (width - lblw - 80) * (v / mx))
        parts.append(
            f'<text x="0" y="{y+14}" font-size="12" fill="#1a2733">{esc(l[:20])}</text>'
            f'<rect x="{lblw}" y="{y}" width="{bw:.1f}" height="{rh}" rx="3" fill="{color}"/>'
            f'<text x="{lblw+bw+6:.1f}" y="{y+14}" font-size="12" fill="#5b6b7a">{format(v, fmt)}{esc(unit)}</text>')
    return f'<svg width="{width}" height="{h}" role="img" aria-label="bar chart">{"".join(parts)}</svg>'

# ---------------------------------------------------------------- layout
BASE = """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Fleet Fuel Analytics</title><style>
:root{--ink:#1a2733;--mut:#5b6b7a;--line:#dde4ea;--bg:#f4f6f8;--acc:#0e5fa8;--ok:#1b7340;--bad:#c8102e}
*{box-sizing:border-box}body{margin:0;font:14px/1.45 -apple-system,Segoe UI,Roboto,Arial;color:var(--ink);background:var(--bg)}
header{background:var(--ink);color:#fff;padding:14px 24px;display:flex;gap:26px;align-items:baseline;flex-wrap:wrap;position:sticky;top:0;z-index:20}
header b{font-size:17px}header a{color:#cfe0f0;text-decoration:none;font-size:13.5px}header a.on{color:#fff;border-bottom:2px solid #6db1e8;padding-bottom:3px}
th[data-sort]::after{content:" " attr(data-sort);color:#6db1e8;font-weight:400}
.rowfilter{margin:0 0 8px;padding:6px 9px;border:1px solid var(--line);border-radius:6px;width:240px;font-size:13px;background:#fff}
.tablewrap{overflow-x:auto;margin:0 0 2px}
kbd{background:#eef2f6;border:1px solid var(--line);border-radius:4px;padding:0 5px;font:12px ui-monospace,monospace}
button:hover{filter:brightness(1.08)}
a:focus-visible,button:focus-visible,input:focus-visible,select:focus-visible{outline:2px solid #6db1e8;outline-offset:1px}
#kh{position:fixed;inset:0;background:rgba(10,20,30,.55);display:flex;align-items:flex-start;justify-content:center;z-index:50;padding-top:8vh}
.khbox{background:#fff;border-radius:12px;padding:18px 22px;max-width:600px;box-shadow:0 10px 40px rgba(0,0,0,.3)}
.khgrid{display:grid;grid-template-columns:repeat(2,1fr);gap:3px 18px;font-size:13px;margin-top:6px}
@media (max-width:760px){header{gap:12px;padding:10px 14px}main{padding:0 10px}.kpis{grid-template-columns:repeat(auto-fit,minmax(130px,1fr))}}
@media print{header,form,.exp,button,.rowfilter,#kh{display:none!important}main{max-width:none;margin:0}.card{break-inside:avoid;border:0;box-shadow:none}body{background:#fff}}
main{max-width:1180px;margin:22px auto;padding:0 18px}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px;margin-bottom:20px}
.kpi{background:#fff;border:1px solid var(--line);border-radius:10px;padding:14px 16px}
.kpi .v{font-size:22px;font-weight:700}.kpi .l{color:var(--mut);font-size:12px;margin-top:2px}
.card{background:#fff;border:1px solid var(--line);border-radius:10px;padding:16px 18px;margin-bottom:20px}
h2{font-size:15px;margin:0 0 10px}
table{width:100%;border-collapse:collapse;font-size:13.5px}
th{background:#eef2f6;text-align:left;padding:7px 9px;border-bottom:1px solid var(--line);white-space:nowrap}
table.sticky thead th{position:sticky;top:var(--navh,56px);z-index:10}
td{padding:6px 9px;border-bottom:1px solid #eef1f4}tr:hover td{background:#f7fafc}
.r{text-align:right}.ok{color:var(--ok);font-weight:600}.bad{color:var(--bad);font-weight:600}
form.f{display:flex;gap:10px;flex-wrap:wrap;align-items:end;margin-bottom:14px}
form.f label{display:flex;flex-direction:column;font-size:12px;color:var(--mut);gap:3px}
select,input{padding:6px 8px;border:1px solid var(--line);border-radius:6px;font-size:13.5px;background:#fff}
button{background:var(--acc);color:#fff;border:0;border-radius:6px;padding:8px 16px;cursor:pointer}
.note{color:var(--mut);font-size:12px;margin-top:8px}
.exp a{margin-right:14px}
</style></head><body>
<header><b>Fleet Fuel Analytics</b>
<a href="/" class="{{'on' if page=='dash'}}">Dashboard</a>
<a href="/savings" class="{{'on' if page=='sav'}}">Savings</a>
<a href="/compare" class="{{'on' if page=='cmp'}}">Compare</a>
<a href="/transactions" class="{{'on' if page=='txn'}}">Transactions</a>
<a href="/headtohead" class="{{'on' if page=='h2h'}}">Head-to-head</a>
<a href="/entities" class="{{'on' if page=='ent'}}">Entities &amp; VAT</a>
<a href="/fx" class="{{'on' if page=='fx'}}">FX vs ECB</a>
<a href="/stations" class="{{'on' if page=='stn'}}">Stations</a>
{% if 'invoice_control' in perms %}<a href="/invoices" class="{{'on' if page=='inv'}}">Invoice control</a>
<a href="/contracts" class="{{'on' if page=='con'}}">Contracts</a>{% endif %}
{% if 'data_import' in perms %}<a href="/extract" class="{{'on' if page=='ext'}}">Import batch</a>
<a href="/queue" class="{{'on' if page=='queue'}}">Waiting room</a>{% endif %}
{% if 'vat_claims' in perms %}<a href="/vat" class="{{'on' if page=='vat'}}">VAT refunds</a>
<a href="/readiness" class="{{'on' if page=='rdy'}}">Claims</a>{% endif %}
<a href="/recovery" class="{{'on' if page=='rec'}}">Recovery</a>
<a href="/anomalies" class="{{'on' if page=='ano'}}">Anomalies</a>
{% if 'pricing' in perms %}<a href="/pricing" class="{{'on' if page=='pri'}}">Pricing intel</a>{% endif %}
{% if 'documents' in perms %}<a href="/documents" class="{{'on' if page=='doc'}}">Documents</a>{% endif %}
<a href="/suppliers" class="{{'on' if page=='sup'}}">Suppliers</a>
{% if 'customers' in perms %}<a href="/customers" class="{{'on' if page=='cus'}}">Customers</a>{% endif %}
{% if 'data_import' in perms %}<a href="/data" class="{{'on' if page=='dat'}}">Data manager</a>
<a href="/mining" class="{{'on' if page=='min'}}">Doc mining</a>{% endif %}
<a href="/history" class="{{'on' if page=='his'}}">History</a>
<span style="margin-left:auto" class="exp">
{% if 'exports' in perms %}<a href="/export/summary">⬇ Summary report</a><a href="/export/master">⬇ Master xlsx</a><a href="/export/history">⬇ History report</a>{% endif %}
{% if role == 'admin' %}<a href="/admin" class="{{'on' if page=='adm'}}">Admin</a>{% endif %}
<span class="note" style="color:#9fb3c4">{{ user }} ({{ role }})</span>
<a href="/logout" style="margin-left:10px">Sign out</a></span>
</header><main>{{ body|safe }}</main><script src="/app.js" defer></script></body></html>"""

_BASE_TMPL = None   # compiled once; render_template_string would recompile per call
def page(body, p):
    global _BASE_TMPL
    if _BASE_TMPL is None:
        _BASE_TMPL = app.jinja_env.from_string(BASE)
    role = session.get("role", "processor")
    return _BASE_TMPL.render(body=body, page=p,
                             user=session.get("user", ""), role=role,
                             perms=_auth.permissions_for(role) if session.get("user") else set())

def tbl(headers, rows):
    h = "".join(f"<th>{x}</th>" for x in headers)
    b = "".join("<tr>" + "".join(r) + "</tr>" for r in rows)
    return f"<table><thead><tr>{h}</tr></thead><tbody>{b}</tbody></table>"

def psel(name, options, cur, allow_all=True):
    opts = (["ALL"] if allow_all else []) + list(options)
    o = "".join(f'<option {"selected" if v==cur else ""}>{esc(v)}</option>' for v in opts)
    return f'<label>{esc(name)}<select name="{esc(name)}">{o}</select></label>'

def multisel(name, label, options, selected, size=4):
    """A <select multiple> for picking several values (e.g. several suppliers).
    No selection = no filter (all). `selected` is the list of chosen values."""
    sel = set(selected or [])
    o = "".join(f'<option {"selected" if v in sel else ""}>{esc(v)}</option>' for v in options)
    sz = min(max(len(list(options)), 2), size)
    return (f'<label>{esc(label)} <span class="note" style="font-weight:400">(ctrl/⌘-click for several)</span>'
            f'<select name="{esc(name)}" multiple size="{sz}">{o}</select></label>')

# ---------------------------------------------------------------- pages
@app.route("/")
def dash():
    con = DB(); periods = q_periods(con)
    period = request.args.get("period", periods[0] if periods else None)
    if not period:
        con.close()
        return page('<div class="card"><h2>No data loaded yet</h2><p>Import an invoice batch '
                    'or run the monthly close to populate transactions.</p></div>', "dash")
    k = q_kpis(con, period); bm = q_benchmark(con, period); tr = q_trend(con)
    sv = q_savings(con, period)
    _litres = f"{k['litres']:,.0f}" if k['litres'] is not None else "—"
    _eurl = f"€{k['eurl']:.4f}" if k['eurl'] is not None else "—"
    _net = f"€{k['net']:,.0f}" if k['net'] is not None else "€0"
    _vat = f"€{k['vat']:,.0f}" if k['vat'] is not None else "€0"
    _gross = f"€{k['gross']:,.0f}" if k['gross'] is not None else "€0"
    kpis = f"""<div class="kpis">
      <div class="kpi"><div class="v">{_litres} L</div><div class="l">Diesel litres · {esc(period)}</div></div>
      <div class="kpi"><div class="v">{_eurl}</div><div class="l">Fleet eff. net €/L</div></div>
      <div class="kpi"><div class="v">{_net}</div><div class="l">Net spend</div></div>
      <div class="kpi"><div class="v">{_vat}</div><div class="l">Reclaimable VAT</div></div>
      <a class="kpi" href="/savings" style="text-decoration:none;color:inherit">
        <div class="v bad">€{sv['total']:,.0f}</div><div class="l">Avoidable overpay &rarr;</div></a></div>"""
    # benchmark as a chart (cheapest first) + the table
    bchart = svg_hbars([(f"{r['supplier']} {r['country']}", r['eff']) for r in bm],
                       unit=" €/L", fmt=".4f", color="#1b7340")
    rows = [[f"<td>{esc(r['supplier'])}</td><td>{esc(r['country'])}</td>",
             f"<td class=r>{r['litres']:,.0f}</td><td class=r>{r['doc']:.4f}</td>",
             f"<td class=r><b>{r['eff']:.4f}</b></td>"] for r in bm]
    bench = bchart + tbl(["Supplier","Country","Litres","€/L doc","€/L effective"], rows)
    trows = [[f"<td>{esc(r['period'])}</td><td class=r>{r['litres']:,.0f}</td><td class=r>{r['eurl']:.4f}</td>"] for r in tr]
    trend = tbl(["Period","Diesel litres","Fleet eff. €/L"], trows)
    psw = "".join(f'<option {"selected" if p==period else ""}>{esc(p)}</option>' for p in periods)
    close = _close_status(period)
    worklist = ""
    if "vat_claims" in _auth.permissions_for(session.get("role", "processor")):
        worklist = _worklist_card(int(period[:4]) if period[:4].isdigit() else 2026)
    body = (f'<form class="f" method="get"><label>Period<select name="period" onchange="this.form.submit()">{psw}</select></label></form>'
            + close + worklist + kpis + f'<div class="card"><h2>Diesel benchmark — effective net €/L (cheapest first)</h2>{bench}'
            f'<div class="note">Effective includes rebate layers (Q8/Port One).</div></div>'
            f'<div class="card"><h2>Monthly trend</h2>{trend}<div class="note">Populates as periods are loaded via history.py.</div></div>')
    con.close(); return page(body, "dash")

_close_cache = {}   # period -> (expires_epoch, html); the controls below are heavy
_CLOSE_TTL = 30     # seconds

def _close_status(period):
    """Month-close checklist: one glance at where the period stands. Cached for a
    few seconds so repeated dashboard loads don't re-run the full receipt/anomaly
    controls every time."""
    import sqlite3, os as _os
    _hit = _close_cache.get(period)
    if _hit and _hit[0] > time.time():
        return _hit[1]
    if len(_close_cache) > 24:      # bound the cache (one entry per period)
        _close_cache.clear()
    items = []
    fc = sqlite3.connect(f"{WORKDIR}/fuel_history.db"); fc.row_factory = sqlite3.Row
    n = fc.execute("SELECT COUNT(*) c FROM transactions WHERE period=?", (period,)).fetchone()["c"]
    items.append(("Data loaded", n > 0, f"{n} transactions"))
    try:
        import invoice_control as IC
        rows, orphans = IC.run_control(period)
        miss = sum(1 for r in rows if r["status"] == "MISSING")
        items.append(("Invoices received", miss == 0, f"{miss} missing" if miss else "all received"))
        stmts = IC.reconcile_statements(period)
        nreg = sum(1 for s in stmts if "NOT REGISTERED" in s["verdict"])
        items.append(("Statements reconciled", len(stmts) > 0 and nreg == 0,
                      f"{len(stmts)} lines, {nreg} unregistered" if stmts else "none registered"))
    except Exception as e:
        _log_exc("dashboard close-status", e)
        items.append(("Controls", False, str(e)[:40]))
    try:
        import anomaly
        a = len(anomaly.find(period))
        items.append(("Anomaly scan", True, f"{a} flag(s) to review" if a else "clean"))
    except Exception: pass
    bdir = f"{WORKDIR}/backups"
    has_b = _os.path.isdir(bdir) and any(f.startswith("ffs_") for f in _os.listdir(bdir))
    items.append(("Backup taken", has_b, "yes" if has_b else "run backup.py"))
    cells = ""
    for label, ok_, detail in items:
        ic = "ok" if ok_ else "bad"
        mark = "\u2713" if ok_ else "\u2717"
        cells += (f'<div class="kpi"><div class="v {ic}">{mark}</div>'
                  f'<div class="l">{esc(label)}<br><span class="note">{esc(detail)}</span></div></div>')
    html = f'<div class="card"><h2>Month-close status — {esc(period)}</h2><div class="kpis">{cells}</div></div>'
    _close_cache[period] = (time.time() + _CLOSE_TTL, html)
    return html

_AGING_DAYS = 120   # an unpaid submitted claim older than this needs chasing

def _worklist_card(year):
    """A 'what needs action' worklist for VAT recovery: claims ready to submit,
    claims blocked on documents, aging unpaid claims, and fees ready to invoice.
    Each line links to the page where the action is taken. Returns '' on any
    error (the dashboard must still render) and logs it."""
    try:
        import vat_refund as VR
        ov = VR.claims_overview(year)
        recs, _ = VR.recovery_report(str(year))
    except Exception as e:
        _log_exc("dashboard worklist", e)
        return ""
    items = []   # (severity class, text, href)
    ready = [c for c in ov["to_submit"] if c.get("ready")]
    blocked = [c for c in ov["to_submit"] if not c.get("ready")]
    for c in ready:
        items.append(("ok", f"Submit {esc(c['entity'])} · {esc(c['country'])} "
                      f"{esc(c['period'])} — €{c['vat_eur']:,.0f} VAT ready", "/readiness"))
    for c in blocked:
        why = ", ".join(c.get("issues") or []) or "not ready"
        items.append(("bad", f"Unblock {esc(c['entity'])} · {esc(c['country'])} "
                      f"{esc(c['period'])} — {esc(why)}", "/readiness"))
    for r in recs:
        if r["status"] in ("submitted", "approved") and isinstance(r["age_days"], int) \
           and r["age_days"] >= _AGING_DAYS:
            items.append(("bad", f"Chase {esc(r['entity'])} · {esc(r['country'])} "
                          f"{esc(r['period'])} — submitted {r['age_days']}d ago, unpaid",
                          "/recovery"))
        if r["fee_billed_date"] and (r["payout_to"] or "customer") == "customer" \
           and not r["fee_invoice_no"]:
            items.append(("", f"Invoice fee for {esc(r['entity'])} · {esc(r['country'])} "
                          f"{esc(r['period'])} — €{(r['fee_eur'] or 0):,.0f}", "/recovery"))
    if not items:
        return ('<div class="card"><h2>What needs action</h2>'
                '<p class="note">Nothing outstanding — all claims are submitted, '
                'chased, and settled. 🎉</p></div>')
    SEV = {"bad": "✗", "ok": "▶", "": "•"}
    shown = items[:12]
    lis = "".join(f'<li><a href="{href}" style="text-decoration:none;color:inherit">'
                  f'<span class="{sev}">{SEV[sev]}</span> {txt}</a></li>'
                  for sev, txt, href in shown)
    more = (f'<li class="note">…and {len(items)-len(shown)} more</li>'
            if len(items) > len(shown) else "")
    return ('<div class="card"><h2>What needs action '
            f'<span class="note">({len(items)})</span></h2>'
            f'<ul style="margin:0;padding-left:18px;line-height:1.9">{lis}{more}</ul></div>')

@app.route("/compare")
def compare():
    con = DB(); f = q_filters(con)
    # Period is single-select (defaults to latest month, or ALL once chosen).
    period = request.args.get("period")
    if period is None:
        period = f["periods"][0] if f["periods"] else "ALL"
    # supplier / country / station are multi-select (pick several).
    sup = [x for x in request.args.getlist("supplier") if x and x != "ALL"]
    ctry = [x for x in request.args.getlist("country") if x and x != "ALL"]
    stn = [x for x in request.args.getlist("station") if x and x != "ALL"]
    prod = request.args.get("product", "ALL")
    df = request.args.get("date_from", ""); dt = request.args.get("date_to", "")
    rows = q_compare(con, request.args, period)
    tot = q_compare_totals(con, request.args, period)

    pcur = period if period in (f["periods"]) else "ALL"
    form = ('<form class="f" method="get">'
            + f'<label>period<select name="period"><option {"selected" if pcur=="ALL" else ""}>ALL</option>'
            + "".join(f'<option {"selected" if p==pcur else ""}>{esc(p)}</option>' for p in f["periods"])
            + '</select></label>'
            + multisel("supplier", "suppliers", f["suppliers"], sup, size=6)
            + multisel("country", "countries", f["countries"], ctry, size=6)
            + multisel("station", "locations", f["stations"], stn, size=6)
            + psel("product", f["products"], prod)
            + f'<label>date from<input type="date" name="date_from" value="{esc(df)}"></label>'
            + f'<label>date to<input type="date" name="date_to" value="{esc(dt)}"></label>'
            + '<button>Apply filters</button>'
            + '<a href="/compare" style="align-self:end;padding:8px 12px;font-size:13px">Reset</a>'
            + '</form>')

    # Active-filter chips + export link carrying the same query string.
    chips = []
    if pcur != "ALL": chips.append(f"period {esc(pcur)}")
    if sup: chips.append("suppliers: " + esc(", ".join(sup)))
    if ctry: chips.append("countries: " + esc(", ".join(ctry)))
    if stn: chips.append(f"{len(stn)} location(s)")
    if prod != "ALL": chips.append("product " + esc(prod))
    if df or dt: chips.append(f"date {esc(df or '…')}→{esc(dt or '…')}")
    chip_html = (' &nbsp;·&nbsp; '.join(chips)) if chips else "no filters (all data)"
    qs = request.query_string.decode()
    export = f'<a href="/export/compare?{esc(qs)}">⬇ Export this view (Excel)</a>'

    def drill(r):
        return (f'/transactions?period={esc(pcur)}&supplier={esc(r["supplier"])}'
                f'&country={esc(r["country"])}')
    trs = [[f"<td>{esc(r['supplier'])}</td><td>{esc(r['country'])}</td><td>{esc(r['product_group'])}</td>",
            f"<td class=r>{r['litres']:,.0f}</td><td class=r>{r['net_eur']:,.2f}</td><td class=r>{r['vat_eur']:,.2f}</td>",
            f"<td class=r>{r['eur_l_doc'] or ''}</td><td class=r><b>{r['eur_l_eff'] or ''}</b></td>",
            f'<td><a href="{drill(r)}">rows →</a></td>'] for r in rows]
    if rows:
        trs.append([f'<td colspan="3"><b>TOTAL ({tot["lines"]:,} lines)</b></td>',
                    f'<td class=r><b>{(tot["litres"] or 0):,.0f}</b></td>'
                    f'<td class=r><b>{(tot["net_eur"] or 0):,.2f}</b></td>'
                    f'<td class=r><b>{(tot["vat_eur"] or 0):,.2f}</b></td>',
                    f'<td></td><td class=r><b>{tot["eur_l_eff"] or ""}</b></td>', '<td></td>'])
    body = (form
            + f'<div class="note" style="margin:-4px 0 12px">Filters: {chip_html} &nbsp;·&nbsp; {export}</div>'
            + f'<div class="card"><h2>Comparison — {len(rows)} group(s)</h2>'
            + tbl(["Supplier","Country","Product","Qty","Net €","VAT €","€/L doc","€/L eff","Detail"], trs)
            + '<div class="note">Prices are NET EUR/L, final (VAT excluded, rebates applied). '
              '€/L eff = net_eur_eff ÷ litres. Pick several suppliers/countries/locations to '
              'compare them side by side; narrow by date range within or across months (period = ALL).</div>'
            + "</div>")
    con.close(); return page(body, "cmp")

@app.route("/export/compare")
def export_compare():
    """Excel export of the current filtered Compare view (same query string)."""
    import io
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
    con = DB()
    period = request.args.get("period")
    if period is None:
        ps = q_periods(con); period = ps[0] if ps else "ALL"
    rows = q_compare(con, request.args, period)
    tot = q_compare_totals(con, request.args, period)
    con.close()
    import reports as R
    from openpyxl.formatting.rule import ColorScaleRule
    wb = Workbook(); ws = wb.active; ws.title = "Compare"
    headers = ["Supplier","Country","Product","Litres","Net EUR","VAT EUR","EUR/L doc","EUR/L eff"]
    ws.append(headers); R.style_header(ws, 1, len(headers))
    for r in rows:
        ws.append([r["supplier"], r["country"], r["product_group"], r["litres"],
                   r["net_eur"], r["vat_eur"], r["eur_l_doc"], r["eur_l_eff"]])
    last = ws.max_row
    R.band_rows(ws, 2, last, len(headers))
    R.number_format(ws, 4, 2, last, R.FMT_INT)
    for col in (5, 6): R.number_format(ws, col, 2, last, R.FMT_EUR)
    for col in (7, 8): R.number_format(ws, col, 2, last, R.FMT_PRICE)
    trow = last + 1
    ws.cell(trow, 1, "TOTAL"); ws.cell(trow, 3, f'{tot["lines"]} lines')
    ws.cell(trow, 4, tot["litres"] or 0).number_format = R.FMT_INT
    ws.cell(trow, 5, tot["net_eur"] or 0).number_format = R.FMT_EUR
    ws.cell(trow, 6, tot["vat_eur"] or 0).number_format = R.FMT_EUR
    ws.cell(trow, 8, tot["eur_l_eff"] or 0).number_format = R.FMT_PRICE
    R.totals_row(ws, trow, len(headers))
    if last >= 2:
        ws.conditional_formatting.add(f"H2:H{last}",
            ColorScaleRule(start_type="min", start_color="63BE7B",
                           mid_type="percentile", mid_value=50, mid_color="FFEB84",
                           end_type="max", end_color="F8696B"))
    R.set_widths(ws, [12, 14, 12, 12, 14, 12, 11, 11])
    ws.freeze_panes = "A2"; ws.sheet_view.showGridLines = False
    buf = io.BytesIO(); wb.save(buf); buf.seek(0)
    return send_file(buf, as_attachment=True, download_name="Fleet_Fuel_Compare.xlsx",
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

@app.route("/headtohead")
def h2h():
    con = DB(); periods = q_periods(con)
    period = request.args.get("period", periods[0] if periods else None)
    data = q_headtohead(con, period) if period else []
    tot = sum(d["overpay"] for d in data)
    psw = "".join(f'<option {"selected" if p==period else ""}>{esc(p)}</option>' for p in periods)
    rows = [[f"<td>{esc(d['date'])}</td><td>{esc(d['country'])}</td><td>{esc(d['prices'])}</td>",
             f"<td class=ok>{esc(d['cheapest'])}</td><td class=r>{d['spread']:.4f}</td>",
             f"<td class=r>{d['litres']:,}</td><td class='r bad'>{d['overpay']:,.0f}</td>"] for d in data]
    body = (f'<form class="f" method="get"><label>Period<select name="period" onchange="this.form.submit()">{psw}</select></label></form>'
            f'<div class="card"><h2>Same-day, same-country diesel overlaps — total overpay vs cheapest: <span class="bad">€{tot:,.0f}</span></h2>'
            + tbl(["Date","Country","Effective €/L by supplier","Cheapest","Spread","Litres","Overpay €"], rows)
            + '<div class="note">Apples-to-apples: days where 2+ suppliers fueled diesel in the same country.</div></div>')
    con.close(); return page(body, "h2h")

@app.route("/entities")
def entities():
    con = DB(); periods = q_periods(con)
    period = request.args.get("period", periods[0] if periods else None)
    rows = q_entities(con, period) if period else []
    psw = "".join(f'<option {"selected" if p==period else ""}>{esc(p)}</option>' for p in periods)
    trs = [[f"<td>{esc(r['entity'])}</td><td>{esc(r['country'])}</td>",
            f"<td class=r>{r['net']:,.2f}</td><td class=r>{r['vat']:,.2f}</td><td class=r><b>{r['gross']:,.2f}</b></td>"] for r in rows]
    body = (f'<form class="f" method="get"><label>Period<select name="period" onchange="this.form.submit()">{psw}</select></label></form>'
            f'<div class="card"><h2>Per-entity totals &amp; reclaimable VAT (EUR) — {esc(period) if period else "no data"}</h2>'
            + tbl(["Entity","Country","Net","VAT reclaimable","Gross"], trs)
            + '<div class="note">One VAT refund stream per entity registration per country.</div></div>')
    con.close(); return page(body, "ent")

@app.route("/stations")
def stations():
    con = DB(); periods = q_periods(con)
    period = request.args.get("period", periods[0] if periods else None)
    rows = q_stations(con, period) if period else []
    psw = "".join(f'<option {"selected" if p==period else ""}>{esc(p)}</option>' for p in periods)
    trs = []
    for r in rows:
        eurl = r["eurl"]
        flag = ('<td class="ok">PREFER</td>' if eurl is not None and eurl<=1.40
                else ('<td class="bad">AVOID</td>' if eurl is not None and eurl>=1.62 else "<td></td>"))
        trs.append([f"<td>{esc(r['supplier'])}</td><td>{esc(r['country'])}</td><td>{esc(r['station'])}</td>",
                    f"<td class=r>{r['litres']:,.0f}</td><td class=r><b>{(eurl or 0):.4f}</b></td>{flag}"])
    body = (f'<form class="f" method="get"><label>Period<select name="period" onchange="this.form.submit()">{psw}</select></label>'
            f'<a href="/export/stations?period={esc(period or "")}" style="align-self:end;padding:8px 12px;font-size:13px">⬇ Routing export (Excel)</a></form>'
            f'<div class="card"><h2>Diesel station scorecard ≥300 L — cheapest first ({esc(period) if period else "no data"})</h2>'
            + tbl(["Supplier","Country","Station","Litres","Eff. €/L","Routing"], trs)
            + '<div class="note">PREFER ≤ €1.40/L · AVOID ≥ €1.62/L (NET eff). Export gives drivers '
              'the routing list per station.</div></div>')
    con.close(); return page(body, "stn")

@app.route("/export/stations")
def export_stations():
    import io
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
    con = DB(); ps = q_periods(con)
    period = request.args.get("period") or (ps[0] if ps else None)
    rows = q_stations(con, period) if period else []
    con.close()
    import reports as R
    from openpyxl.styles import Font, PatternFill
    from openpyxl.formatting.rule import ColorScaleRule
    wb = Workbook(); ws = wb.active; ws.title = "Routing"
    ws.append(["Supplier", "Country", "Station", "Litres", "Eff EUR/L", "Routing", "Period"])
    R.style_header(ws, 1, 7)
    for r in rows:
        e = r["eurl"]
        flag = "PREFER" if e is not None and e <= 1.40 else ("AVOID" if e is not None and e >= 1.62 else "")
        ws.append([r["supplier"], r["country"], r["station"], r["litres"], e, flag, period])
    last = ws.max_row
    R.band_rows(ws, 2, last, 7)
    R.number_format(ws, 4, 2, last, R.FMT_INT)
    R.number_format(ws, 5, 2, last, R.FMT_PRICE)
    # colour the PREFER / AVOID flags
    for rr in range(2, last + 1):
        f = ws.cell(rr, 6).value
        if f == "PREFER":
            ws.cell(rr, 6).font = Font(bold=True, color=R.OKG)
        elif f == "AVOID":
            ws.cell(rr, 6).font = Font(bold=True, color=R.BADR)
    if last >= 2:
        ws.conditional_formatting.add(f"E2:E{last}",
            ColorScaleRule(start_type="min", start_color="63BE7B",
                           mid_type="percentile", mid_value=50, mid_color="FFEB84",
                           end_type="max", end_color="F8696B"))
    R.set_widths(ws, [12, 12, 34, 11, 11, 10, 10])
    ws.freeze_panes = "A2"; ws.sheet_view.showGridLines = False
    buf = io.BytesIO(); wb.save(buf); buf.seek(0)
    return send_file(buf, as_attachment=True, download_name=f"Fleet_Fuel_Routing_{period or 'all'}.xlsx",
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

@app.route("/savings")
def savings():
    con = DB(); periods = q_periods(con)
    period = request.args.get("period", periods[0] if periods else None)
    s = q_savings(con, period) if period else {"total": 0, "by_country": [], "by_supplier": []}
    con.close()
    psw = "".join(f'<option {"selected" if p==period else ""}>{esc(p)}</option>' for p in periods)
    body = (f'<form class="f" method="get"><label>Period<select name="period" onchange="this.form.submit()">{psw}</select></label></form>'
            f'<div class="kpis"><div class="kpi"><div class="v bad">€{s["total"]:,.0f}</div>'
            f'<div class="l">avoidable overpay vs cheapest same-day rival · {esc(period) if period else "no data"}</div></div></div>'
            + '<div class="card"><h2>Overpay by country</h2>'
            + svg_hbars(s["by_country"], unit=" €") + '</div>'
            + '<div class="card"><h2>Overpay by supplier</h2>'
            + svg_hbars(s["by_supplier"], unit=" €", color="#c8102e") + '</div>'
            + '<div class="note">Apples-to-apples: only days where 2+ suppliers fueled diesel in the '
              'same country count. The premium is attributed to the dearer supplier. Drill into any '
              'slice on the <a href="/transactions">Transactions</a> page. Prices NET EUR/L, final.</div>')
    return page(body, "sav")

@app.route("/transactions")
def transactions():
    con = DB(); f = q_filters(con)
    period = request.args.get("period")
    if period is None:
        period = f["periods"][0] if f["periods"] else "ALL"
    sup = [x for x in request.args.getlist("supplier") if x and x != "ALL"]
    ctry = [x for x in request.args.getlist("country") if x and x != "ALL"]
    df = request.args.get("date_from", ""); dt = request.args.get("date_to", "")
    w, p = where(request.args, period)
    rows = con.execute(f"""SELECT date, supplier, country, station, vehicle, product,
        ROUND(qty,2) qty, ROUND(net_eur,2) net_eur, ROUND(vat_eur,2) vat_eur,
        ROUND(net_eur_eff/NULLIF(qty,0),4) eurl
        FROM transactions WHERE {w} ORDER BY date, supplier, station LIMIT 2000""", p).fetchall()
    con.close()
    pcur = period if period in f["periods"] else "ALL"
    form = ('<form class="f" method="get">'
            + f'<label>period<select name="period"><option {"selected" if pcur=="ALL" else ""}>ALL</option>'
            + "".join(f'<option {"selected" if pp==pcur else ""}>{esc(pp)}</option>' for pp in f["periods"])
            + '</select></label>'
            + multisel("supplier", "suppliers", f["suppliers"], sup, size=6)
            + multisel("country", "countries", f["countries"], ctry, size=6)
            + f'<label>date from<input type="date" name="date_from" value="{esc(df)}"></label>'
            + f'<label>date to<input type="date" name="date_to" value="{esc(dt)}"></label>'
            + '<button>Apply filters</button>'
            + '<a href="/transactions" style="align-self:end;padding:8px 12px;font-size:13px">Reset</a></form>')
    trs = [[f"<td>{esc(r['date'])}</td><td>{esc(r['supplier'])}</td><td>{esc(r['country'])}</td>"
            f"<td>{esc(r['station'])}</td><td>{esc(r['vehicle'])}</td><td>{esc(r['product'])}</td>",
            f"<td class=r>{(r['qty'] or 0):,.2f}</td><td class=r>{(r['net_eur'] or 0):,.2f}</td>"
            f"<td class=r>{(r['vat_eur'] or 0):,.2f}</td><td class=r>{r['eurl'] or ''}</td>"] for r in rows]
    body = (form
            + f'<div class="card"><h2>Transactions — {len(rows)} line(s)'
            + (' <span class="note">(capped at 2,000 — narrow the filters)</span>' if len(rows) == 2000 else '')
            + '</h2>'
            + tbl(["Date","Supplier","Country","Station","Vehicle","Product","Qty","Net €","VAT €","€/L eff"], trs)
            + '<div class="note">The line-level detail behind every report. Filter by period, '
              'supplier(s), country(ies), and date range. Prices NET EUR/L, final.</div></div>')
    return page(body, "txn")

@app.route("/fx", methods=["GET", "POST"])
def fx():
    """Compare each invoice's effective exchange rate (net_local / net_eur) against
    the official ECB euro reference rate for the period, fetched on demand."""
    import ecb_rates as ECB, money
    banner = ""
    is_admin = session.get("role") == "admin"
    if request.method == "POST" and request.form.get("__act") == "refresh":
        try:
            info = ECB.fetch_and_store()
            banner = (f'<div class="card"><b class="ok">ECB rates scraped via {esc(info["source"])} '
                      f'— as of {esc(info["asof"])}: {info["days"]} day(s), '
                      f'{len(info["currencies"])} currencies cached.</b></div>')
        except Exception as e:
            _log_exc("ECB scrape", e)
            banner = (f'<div class="card"><b class="bad">Could not scrape ECB rates: '
                      f'{esc(str(e))}</b><div class="note">The server needs outbound HTTPS to a rate '
                      f'source (ECB, Frankfurter, exchangerate.host or er-api), or an admin can upload '
                      f'rates instead. Cached rates (if any) are still shown.</div></div>')
    elif request.method == "POST" and request.form.get("__act") == "upload":
        if not is_admin:
            banner = '<div class="card"><b class="bad">Only an admin can upload exchange rates.</b></div>'
        else:
            f = request.files.get("file")
            try:
                if not f or not f.filename:
                    raise ValueError("no file selected")
                data = f.read().decode("utf-8-sig", "replace")
                rows = (ECB._parse(data) if f.filename.lower().endswith(".xml")
                        else ECB.parse_csv(data))
                info = ECB.store(rows, source=f"upload: {f.filename[:40]}")
                banner = (f'<div class="card"><b class="ok">Uploaded {info["rows"]} rate(s) from '
                          f'{esc(f.filename)} — as of {esc(info["asof"])}, '
                          f'{len(info["currencies"])} currencies.</b></div>')
            except Exception as e:
                _log_exc("ECB rate upload", e)
                banner = (f'<div class="card"><b class="bad">Upload failed: {esc(str(e))}</b>'
                          f'<div class="note">CSV columns: <b>date,currency,rate</b> '
                          f'(rate = foreign units per 1 EUR), or upload an ECB eurofxref .xml.</div></div>')
    con = DB()
    rows = con.execute("""
        SELECT supplier, currency, period,
               ROUND(SUM(net_local),2) net_local, ROUND(SUM(net_eur),2) net_eur,
               ROUND(SUM(net_local)/NULLIF(SUM(net_eur),0),5) implied,
               MAX(date) last_date
        FROM transactions WHERE currency<>'EUR'
        GROUP BY supplier, currency, period ORDER BY period DESC, supplier""").fetchall()
    con.close()
    asof, asof_src = ECB.latest_asof()
    trs, total_diff, flagged = [], 0.0, 0
    for r in rows:
        ecb_rate, ecb_date = ECB.rate_for(r["currency"], r["last_date"])
        if ecb_rate:
            dev = (r["implied"] - ecb_rate) / ecb_rate * 100 if ecb_rate else None
            eur_at_ecb = money.f2((r["net_local"] or 0) / ecb_rate)
            eur_diff = money.f2((r["net_eur"] or 0) - eur_at_ecb)
            total_diff += eur_diff
            bad = abs(dev) >= 2.0
            flagged += 1 if bad else 0
            cls = "bad" if bad else "ok"
            ecb_cell = f"<td class=r>{ecb_rate:.5f}</td><td class=note>{esc(ecb_date or '')}</td>"
            dev_cell = f"<td class='r {cls}'>{dev:+.2f}%</td>"
            eur_cell = f"<td class=r>{eur_at_ecb:,.2f}</td><td class='r {cls}'>{eur_diff:+,.2f}</td>"
        else:
            ecb_cell = '<td class=r>—</td><td class=note>not fetched</td>'
            dev_cell = '<td class=r>—</td>'
            eur_cell = '<td class=r>—</td><td class=r>—</td>'
        trs.append([f"<td>{esc(r['supplier'])}</td><td>{esc(r['currency'])}</td><td>{esc(r['period'])}</td>",
                    f"<td class=r>{(r['net_local'] or 0):,.2f}</td><td class=r>{(r['net_eur'] or 0):,.2f}</td>",
                    f"<td class=r><b>{r['implied']:.5f}</b></td>" + ecb_cell + dev_cell + eur_cell])
    refresh = ('<form method="post" style="display:inline">' + _csrf_input()
               + '<button name="__act" value="refresh">↻ Scrape ECB rates (live)</button></form>')
    upload = ('<form method="post" enctype="multipart/form-data" style="display:inline-flex;gap:8px;align-items:center">'
              + _csrf_input()
              + '<input type="file" name="file" accept=".csv,.xml" required>'
              + '<button name="__act" value="upload">⬆ Upload rates</button></form>') if is_admin else ""
    head = (f'<div class="kpis">'
            f'<div class="kpi"><div class="v">{esc(asof) if asof else "—"}</div>'
            f'<div class="l">rates as of{(" · " + esc(asof_src)) if asof_src else ""}</div></div>'
            f'<div class="kpi"><div class="v {"bad" if abs(total_diff)>=1 else ""}">'
            f'€{total_diff:+,.0f}</div><div class="l">invoiced EUR vs EUR at ECB</div></div>'
            f'<div class="kpi"><div class="v {"bad" if flagged else "ok"}">{flagged}</div>'
            f'<div class="l">streams ≥2% off ECB</div></div></div>')
    body = (banner
            + f'<div class="f" style="margin-bottom:12px;align-items:center">{refresh}{upload}'
            + (('<span class="note" style="align-self:center">No rates cached yet — '
                + ('scrape live or upload a CSV/ECB-XML.' if is_admin
                   else 'ask an admin to scrape or upload rates.') + '</span>') if not asof else '')
            + '</div>'
            + head
            + '<div class="card"><h2>Invoice exchange rate vs ECB reference rate</h2>'
            + tbl(["Supplier", "Ccy", "Period", "Net local", "Net EUR", "Invoice rate",
                   "ECB rate", "ECB date", "Deviation", "EUR @ECB", "EUR diff"], trs)
            + '<div class="note">Rates are foreign units per 1 EUR. <b>Invoice rate</b> = '
              'net_local ÷ net_eur (the rate the invoice effectively applied). <b>ECB rate</b> '
              'is the official euro reference rate on/just before the last fuelling date of the '
              'period. Deviation ≥ 2% is flagged — it can signal an FX markup or, at the extreme '
              '(e.g. an implied rate of 1.0), lines that were never converted. Amounts NET EUR, '
              'final. Rates are <b>scraped live</b> from the ECB (eurofxref / SDMX), with '
              'Frankfurter, exchangerate.host and er-api as fallbacks — or, on a network without '
              'outbound access, an <b>admin can upload</b> them (CSV <code>date,currency,rate</code> '
              'or an ECB eurofxref .xml).</div></div>')
    return page(body, "fx")

# ---------------------------------------------------------------- exports + API
@app.route("/export/master")
def export_master():
    import glob
    f = sorted(glob.glob(os.path.join(WORKDIR, "Fleet_Fuel_Master_*.xlsx")))[-1]
    return send_file(f, as_attachment=True)

@app.route("/export/history")
def export_history():
    return send_file(os.path.join(WORKDIR, "Fleet_Fuel_History_Report.xlsx"), as_attachment=True)

@app.route("/export/fee")
def export_fee():
    """Service-fee calculation report for one VAT claim."""
    import vat_refund as VR, reports
    ent = request.args.get("entity", ""); ctry = request.args.get("country", "")
    per = request.args.get("period", "")
    con = VR.connect()
    r = con.execute("""SELECT * FROM vat_applications WHERE entity=? AND refund_country=?
                       AND ref_period=?""", (ent, ctry, per)).fetchone()
    con.close()
    if not r:
        return page('<div class="card"><b class="bad">No such claim.</b></div>', ""), 404
    path = reports.fee_report_workbook(dict(r))
    return send_file(path, as_attachment=True, download_name=os.path.basename(path),
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

@app.route("/export/summary")
def export_summary():
    """Executive summary workbook: KPIs, per-supplier/country/entity breakdowns,
    trend and savings, with charts and conditional formatting."""
    import reports
    con = DB(); ps = q_periods(con); con.close()
    period = request.args.get("period") or (ps[0] if ps else None)
    path = reports.summary_workbook(period)
    return send_file(path, as_attachment=True,
                     download_name=os.path.basename(path),
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

@app.route("/api/periods")
def api_periods():
    con = DB(); out = q_periods(con); con.close(); return jsonify(out)

@app.route("/api/benchmark")
def api_benchmark():
    con = DB(); ps = q_periods(con); period = request.args.get("period", ps[0] if ps else None)
    out = [dict(r) for r in q_benchmark(con, period)] if period else []
    con.close(); return jsonify(out)

@app.route("/api/compare")
def api_compare():
    con = DB(); out = [dict(r) for r in q_compare(con, request.args)]; con.close(); return jsonify(out)

@app.route("/api/headtohead")
def api_h2h():
    con = DB(); ps = q_periods(con); period = request.args.get("period", ps[0] if ps else None)
    out = q_headtohead(con, period) if period else []; con.close(); return jsonify(out)

@app.route("/api/entities")
def api_entities():
    con = DB(); ps = q_periods(con); period = request.args.get("period", ps[0] if ps else None)
    out = [dict(r) for r in q_entities(con, period)] if period else []
    con.close(); return jsonify(out)


@app.route("/extract", methods=["GET", "POST"])
def extract_batch():
    import extract as EX
    # access is enforced centrally in _guard (capability: data_import)
    backend_env = EX.EXTRACT_BACKEND
    body = ""
    if request.method == "POST" and "file" in request.files:
        f = request.files["file"]
        data = f.read()
        if request.form.get("__mode") == "queue":
            # waiting room: store durably now, extract later in the background worker
            import waiting_room as IQ
            blocked, pend = _intake_uploads_blocked()
            if blocked:
                hint = ('An administrator can grant a temporary override on the '
                        'waiting-room page.' if session.get("role") != "admin"
                        else 'Use “Allow uploads (override)” on the waiting-room page '
                             'to add anyway.')
                return page('<div class="card"><b class="bad">New uploads are paused.</b>'
                            f'<p>{pend} document(s) in the waiting room still need to be '
                            'processed successfully. Clear them first — open the waiting '
                            'room and press <b>Send / restart all</b>. ' + esc(hint) + '</p>'
                            '<p><a href="/queue">→ Go to the waiting room</a></p></div>'
                            + _upload_form(backend_env), "ext")
            try:
                jid, st = IQ.enqueue(data, f.filename, backend=request.form.get("backend") or None,
                                     period=request.form.get("period") or None,
                                     user=session.get("user", "system"))
            except Exception as e:
                _log_exc("intake enqueue", e)
                return page(f'<div class="card"><b class="bad">Could not queue file: {esc(str(e))}</b></div>'
                            + _upload_form(backend_env), "ext")
            return redirect(f"/queue?msg=Queued+{esc(f.filename)}+(job+{jid},+{st})")
        try:
            draft = EX.extract(data, f.filename, backend=request.form.get("backend") or None)
        except Exception as e:
            _log_exc("import batch / extraction", e)
            return page(f'<div class="card"><b class="bad">Extraction error: {esc(str(e))}</b></div>'
                        + _upload_form(backend_env), "ext")
        if draft.get("error"):
            return page(f'<div class="card"><b class="bad">{esc(draft["error"])}</b></div>'
                        + _upload_form(backend_env), "ext")
        # stash pdf bytes in a temp dir keyed by token; never put bytes in the form
        import tempfile, pickle, os as _os
        token = _os.urandom(8).hex()
        tmp = _os.path.join(WORKDIR, ".extract_tmp"); _os.makedirs(tmp, exist_ok=True); _os.chmod(tmp, 0o700)
        with open(_os.path.join(tmp, token + ".pkl"), "wb") as pf:
            pickle.dump(draft.get("_pdf_bytes", []), pf)
        return page(_review_form(draft, token), "ext")
    return page(_upload_form(backend_env), "ext")


def _upload_form(backend_env):
    privacy = ("All processing stays on this server (deterministic parser)."
               if backend_env in ("parser", "none", "auto")
               else f"Unrecognised suppliers are sent to the configured AI processor "
                    f"({esc(backend_env)}) for a draft - permitted under your DPA. "
                    f"The draft is always reviewed before commit.")
    opts = "".join(f'<option {"selected" if b==backend_env else ""}>{b}</option>'
                   for b in ("auto", "parser", "claude", "openai", "azure", "none"))
    blocked, pend = _intake_uploads_blocked()
    block_note = ""
    if pend:
        if blocked:
            block_note = ('<div class="card"><b class="bad">Queue uploads are paused.</b> '
                          f'{pend} document(s) in the <a href="/queue">waiting room</a> still '
                          'need to be processed successfully — clear them with <b>Send / '
                          'restart all</b> first. (“Extract draft now” still works.)'
                          + ('' if session.get("role") != "admin" else
                             ' An admin can grant a temporary override there.') + '</div>')
        else:
            mins = _intake_override_remaining() // 60
            block_note = ('<div class="card"><b class="ok">Upload override active</b> '
                          f'(~{mins} min left): new queue uploads are allowed despite '
                          f'{pend} pending document(s).</div>')
    return (block_note + '<div class="card"><h2>Import an invoice batch (PDF or ZIP)</h2>'
            '<form method="post" enctype="multipart/form-data" class="f">'
            + _csrf_input() +
            '<label>file (.pdf, .zip or .xml)<input type="file" name="file" accept=".pdf,.zip,.xml" required></label>'
            f'<label>extractor<select name="backend">{opts}</select></label>'
            f'<label>period (YYYY-MM)<input name="period" value="{esc(request.values.get("period","2026-05"))}" style="width:100px"></label>'
            '<button name="__mode" value="now">Extract draft now</button>'
            '<button name="__mode" value="queue" style="background:var(--mut)">Queue for later</button>'
            '</form>'
            f'<div class="note">{privacy} <b>Structured e-invoices (UBL/CII/XML, EN 16931)</b> '
            'parse deterministically at high confidence — no AI. '
            'Deterministic PDF parser is free and offline; '
            'auto uses it when the supplier is recognised and falls back to AI otherwise. '
            'Nothing is saved until you review and confirm on the next screen. '
            '<b>Queue for later</b> parks the file in the <a href="/queue">waiting room</a> '
            '(durably stored, processed in the background) so a burst of uploads never '
            'overloads the server.</div></div>')


def _review_form(draft, token, intake_job=None, period=None):
    rows = ""
    for i, ln in enumerate(draft.get("lines", [])):
        rows += ('<tr>'
                 f'<td><input name="inv_{i}" value="{esc(ln.get("invoice_no") or "")}" style="width:160px"></td>'
                 f'<td><input name="date_{i}" value="{esc(ln.get("date") or draft.get("statement_date") or "")}" style="width:100px" placeholder="YYYY-MM-DD"></td>'
                 f'<td><input name="ctry_{i}" value="{esc(ln.get("country") or "")}" style="width:100px"></td>'
                 f'<td><input name="ccy_{i}" value="{esc(ln.get("currency") or "EUR")}" style="width:55px"></td>'
                 f'<td><input name="net_{i}" value="{ln.get("net",0)}" style="width:90px" class="r"></td>'
                 f'<td><input name="vat_{i}" value="{ln.get("vat",0)}" style="width:90px" class="r"></td>'
                 f'<td class="note">{esc(ln.get("_source",""))}</td></tr>')
    gross = sum((ln.get("net",0) or 0) + (ln.get("vat",0) or 0) for ln in draft.get("lines", []))
    conf = draft.get("confidence","low")
    ccls = {"high":"ok","medium":"","low":"bad"}.get(conf,"")
    return ('<div class="card"><h2>Review extracted draft — confirm before anything is saved</h2>'
            f'<div class="note">Source: <b>{esc(draft.get("backend",""))}</b> · '
            f'confidence <span class="{ccls}">{esc(conf)}</span> · '
            f'{len(draft.get("files",[]))} PDF(s). {esc(draft.get("notes",""))}</div>'
            '<form method="post" action="/extract/confirm" class="f" style="margin-top:10px">'
            + _csrf_input() +
            f'<input type="hidden" name="token" value="{esc(token)}">'
            + (f'<input type="hidden" name="intake_job" value="{esc(str(intake_job))}">' if intake_job else "")
            + f'<label>supplier code<input name="supplier" value="{esc(draft.get("supplier") or "")}" required></label>'
            f'<label>statement ref<input name="stmt_ref" value="{esc(draft.get("statement_ref") or "")}" required></label>'
            f'<label>statement date<input type="date" name="stmt_date" value="{esc(draft.get("statement_date") or "")}"></label>'
            f'<label>customer<input name="customer" value="{esc((draft.get("customer") or "").strip())}"></label>'
            f'<label>period (YYYY-MM)<input name="period" value="{esc(period or request.values.get("period","2026-05"))}" required></label>'
            '</label></div>'
            + '<table style="margin-top:10px"><thead><tr>'
            + "".join(f"<th>{h}</th>" for h in ["Invoice no","Date","Country","Ccy","Net","VAT","Source PDF"])
            + f'</tr></thead><tbody>{rows}</tbody></table>'
            f'<div class="note" style="margin-top:8px">Draft gross total: <b>{gross:,.2f}</b> — '
            'check this equals the coversheet total before confirming.</div>'
            f'<input type="hidden" name="nlines" value="{len(draft.get("lines",[]))}">'
            '<div style="margin-top:10px">'
            '<button name="__do" value="confirm">Confirm &amp; register statement</button> '
            '<button name="__do" value="cancel" style="background:var(--mut)">Discard draft</button>'
            '</div></form>'
            '<div class="note">Confirming registers the statement (VAT-bearing invoices '
            'auto-sync), attaches every source PDF to the document vault, and runs the '
            'normal triage. You can still edit any field above first.</div></div>')


@app.route("/extract/confirm", methods=["POST"])
def extract_confirm():
    import extract as EX, invoice_control as IC, vat_refund as VR
    import os as _os, pickle
    # access is enforced centrally in _guard (capability: data_import)
    token = request.form["token"]
    tmpf = _os.path.join(WORKDIR, ".extract_tmp", token + ".pkl")
    if request.form.get("__do") == "cancel":
        if _os.path.exists(tmpf): _os.unlink(tmpf)
        return redirect("/extract")
    n = int(request.form["nlines"])
    lines = []
    for i in range(n):
        inv = request.form.get(f"inv_{i}", "").strip()
        if not inv:
            continue
        lines.append((inv, request.form.get(f"date_{i}", "").strip(),
                      request.form.get(f"ctry_{i}", "").strip(),
                      request.form.get(f"ccy_{i}", "EUR").strip(),
                      request.form.get(f"net_{i}", "0"), request.form.get(f"vat_{i}", "0")))
    supplier = request.form["supplier"].strip()
    period = request.form["period"].strip()
    customer = request.form.get("customer", "").strip() or None
    import validate as VAL
    vlines = [{"invoice_no": l[0], "date": l[1], "country": l[2], "currency": l[3],
               "net": l[4], "vat": l[5]} for l in lines]
    vr = VAL.validate_batch(vlines)
    if not vr["can_commit"]:
        rows_html = ""
        for res in vr["lines"]:
            cls = {"ok":"ok","warn":"","error":"bad"}[res["verdict"]]
            rows_html += (f'<tr><td>{esc(res["line"].get("invoice_no") or "")}</td>'
                          f'<td>{esc(res["line"].get("country") or "")}</td>'
                          f'<td class="r">{res["line"].get("net")}</td>'
                          f'<td class="r">{res["line"].get("vat")}</td>'
                          f'<td class="{cls}">{res["verdict"]}</td>'
                          f'<td class="note">{esc("; ".join(res["messages"]))}</td></tr>')
        thead = "".join(f"<th>{h}</th>" for h in ["Invoice","Country","Net","VAT","Check","Issue"])
        return page('<div class="card"><b class="bad">Commit blocked - fix the errors '
                    f'({vr["errors"]} error, {vr["warnings"]} warning) and re-import:</b>'
                    f'<table><thead><tr>{thead}</tr></thead><tbody>{rows_html}</tbody></table>'
                    + "</div>", "ext")
    synced = IC.register_statement(supplier, request.form["stmt_ref"].strip(), period,
                                   request.form.get("stmt_date", "").strip(), lines,
                                   notes="imported via batch extraction", customer=customer)
    VAL.save_baseline(supplier, request.form["stmt_ref"].strip(), vlines)
    # attach source PDFs to the vault against their invoice refs
    attached = 0
    if _os.path.exists(tmpf):
        pdfs = pickle.load(open(tmpf, "rb"))
        fcon = VR.connect()
        ent = customer or supplier
        by_name = {nm: b for nm, b in pdfs}
        for i in range(n):
            inv = request.form.get(f"inv_{i}", "").strip()
            src = request.form.get(f"src_{i}", "")
            # match the line's source PDF if the name was carried; else attach all to first
            cand = next((b for nm, b in pdfs if inv and inv[:8] in nm), None)
            if inv and cand:
                ok, _ = VR.attach_document(fcon, ent, supplier, inv, file_bytes=cand,
                                           filename=f"{inv}.pdf", kind="original_pdf",
                                           country=request.form.get(f"ctry_{i}", "").strip() or None,
                                           period=period)
                if ok: attached += 1
        fcon.close()
        _os.unlink(tmpf)
    # if this draft came from the waiting room, mark the job done (frees its bytes)
    if request.form.get("intake_job"):
        try:
            import waiting_room as IQ
            IQ.complete(int(request.form["intake_job"]))
        except Exception as e:
            _log_exc("intake complete", e)
    banner = (f'<div class="card"><b class="ok">Statement {esc(request.form["stmt_ref"])} '
              f'registered: {len(lines)} invoices ({synced} VAT-bearing synced), '
              f'{attached} PDFs vaulted. Review triage on the Invoice control page.</b></div>')
    return page(banner + f'<p><a href="/invoices?period={esc(period)}">→ Invoice control</a></p>', "ext")

@app.route("/queue", methods=["GET", "POST"])
def intake_queue_page():
    """The 'waiting room': uploaded batches parked for deferred extraction. Shows
    queue state and lets you process the backlog now, review a ready draft, re-queue
    a failure, or discard a job. Access: data_import (enforced in _guard)."""
    import waiting_room as IQ
    is_admin = session.get("role") == "admin"
    banner = ""
    if request.method == "POST":
        act = request.form.get("__act")
        if act == "process":
            try:
                n = IQ.drain(limit=int(request.form.get("limit", "5")))
                banner = f'<div class="card"><b class="ok">Processed {n} job(s) from the queue.</b></div>'
            except Exception as e:
                _log_exc("intake drain", e)
                banner = f'<div class="card"><b class="bad">Processing error: {esc(str(e))}</b></div>'
        elif act == "send_all":
            # bulk "manual send / restart workflow": reset every stuck job
            # (waiting/held/failed) back to queued and run the whole backlog now.
            try:
                reset = IQ.requeue_all(("waiting", "held", "failed"))
                done = IQ.drain(limit=500)
                banner = (f'<div class="card"><b class="ok">Restarted {reset} stuck job(s) '
                          f'and processed {done} document(s) from the waiting room.</b></div>')
            except Exception as e:
                _log_exc("intake send_all", e)
                banner = f'<div class="card"><b class="bad">Bulk send failed: {esc(str(e))}</b></div>'
        elif act == "discard":
            IQ.discard(int(request.form.get("job", "0")))
            banner = '<div class="card"><b class="ok">Job discarded.</b></div>'
        elif act == "requeue":
            if IQ.requeue(int(request.form.get("job", "0"))):
                banner = '<div class="card"><b class="ok">Job re-queued for processing.</b></div>'
            else:
                banner = '<div class="card"><b class="bad">Could not re-queue (job or file missing).</b></div>'
        elif act == "override_on" and is_admin:
            _auth.set_setting("intake_override_until",
                              str(time.time() + INTAKE_OVERRIDE_MINUTES * 60))
            banner = (f'<div class="card"><b class="ok">Upload override enabled for '
                      f'{INTAKE_OVERRIDE_MINUTES} minutes — new queue uploads are allowed '
                      'despite the backlog.</b></div>')
        elif act == "override_off" and is_admin:
            _auth.set_setting("intake_override_until", "0")
            banner = '<div class="card"><b class="ok">Upload override turned off.</b></div>'
    msg = request.args.get("msg")
    if msg:
        banner = f'<div class="card"><b class="ok">{esc(msg)}</b></div>' + banner
    c = IQ.counts()
    kpis = ('<div class="kpis">'
            + f'<div class="kpi"><div class="v">{c["queued"]}</div><div class="l">queued</div></div>'
            + f'<div class="kpi"><div class="v">{c["processing"]}</div><div class="l">processing</div></div>'
            + f'<div class="kpi"><div class="v {"bad" if c["waiting"] else ""}">{c["waiting"]}</div><div class="l">waiting for API tokens</div></div>'
            + f'<div class="kpi"><div class="v {"bad" if c["held"] else ""}">{c["held"]}</div><div class="l">held — needs manual send</div></div>'
            + f'<div class="kpi"><div class="v ok">{c["ready"]}</div><div class="l">ready to review</div></div>'
            + f'<div class="kpi"><div class="v {"bad" if c["failed"] else ""}">{c["failed"]}</div><div class="l">failed</div></div>'
            + f'<div class="kpi"><div class="v">{c["done"]}</div><div class="l">done</div></div></div>')
    rows = []
    for j in IQ.jobs(limit=100):
        st = j["status"]
        stcls = {"ready": "ok", "failed": "bad", "waiting": "bad",
                 "held": "bad", "done": "note"}.get(st, "")
        label = "Send now" if st == "held" else "Retry now"
        retry_btn = ('<form method="post" style="display:inline">' + _csrf_input()
                     + f'<input type="hidden" name="job" value="{j["id"]}">'
                     + f'<button name="__act" value="requeue">{label}</button></form>')
        if st == "ready":
            act_cell = f'<a href="/queue/review/{j["id"]}">Review &amp; commit →</a>'
        elif st in ("failed", "waiting", "held"):
            act_cell = retry_btn
        else:
            act_cell = '<span class="note">—</span>'
        disc = ('<form method="post" style="display:inline">' + _csrf_input()
                + f'<input type="hidden" name="job" value="{j["id"]}">'
                + '<button name="__act" value="discard" style="background:var(--mut)">Discard</button></form>')
        # show the retry schedule for waiting jobs and a manual-send hint for held
        statetxt = esc(st)
        if st == "waiting" and j.get("next_attempt_at"):
            statetxt = f'{esc(st)}<br><span class="note">retry ≥ {esc(j["next_attempt_at"])} UTC</span>'
        elif st == "held":
            statetxt = f'{esc(st)}<br><span class="note">auto-retry stopped · press Send</span>'
        rows.append([
            f'<td>{j["id"]}</td><td>{esc(j["filename"] or "")}</td>',
            f'<td>{esc(j["backend"] or "auto")}</td><td>{esc(j["period"] or "")}</td>',
            f'<td>{esc(j["uploaded_by"] or "")}</td><td class="note">{esc(j["uploaded_at"] or "")}</td>',
            f'<td class="{stcls}">{statetxt}</td><td class="r">{j["attempts"]}</td>',
            f'<td class="note">{esc((j["error"] or "")[:60])}</td>',
            f'<td>{act_cell} {disc}</td>'])
    # bulk "manual send / restart workflow" for every pending document
    send_all_btn = ('<form method="post" style="display:inline;margin-right:10px">' + _csrf_input()
                    + '<button name="__act" value="send_all">↻ Send / restart all</button></form>')
    process_form = ('<form method="post" class="f" style="margin:0">' + _csrf_input()
                    + '<label>batch size<input name="limit" value="5" style="width:60px" class="r"></label>'
                    + '<button name="__act" value="process" style="background:var(--mut)">Process queued only</button></form>')
    # upload-gating status + the admin temporary override controls
    blocked, pend = _intake_uploads_blocked()
    rem = _intake_override_remaining()
    if pend == 0:
        gate = ('<div class="note"><b class="ok">No backlog</b> — new documents can be '
                'added to the waiting room.</div>')
    elif rem > 0:
        off = (('<form method="post" style="display:inline;margin-left:8px">' + _csrf_input()
                + '<button name="__act" value="override_off" style="background:var(--mut)">'
                  'Turn off override</button></form>') if is_admin else "")
        gate = ('<div class="note"><b class="ok">Upload override active</b> '
                f'(~{rem // 60} min left): new queue uploads allowed despite {pend} pending '
                f'document(s).{off}</div>')
    else:
        ov = (('<form method="post" style="display:inline;margin-left:8px">' + _csrf_input()
               + '<button name="__act" value="override_on">Allow uploads (override '
               + f'{INTAKE_OVERRIDE_MINUTES} min)</button></form>') if is_admin else
              '<span class="note"> An admin can grant a temporary override.</span>')
        gate = ('<div class="note"><b class="bad">New uploads are paused</b> — '
                f'{pend} document(s) here still need to be processed successfully. '
                'Clear them with “Send / restart all”.' + ov + '</div>')
    body = (banner + '<div class="card"><h2>Document waiting room</h2>' + kpis
            + '<div class="note">Uploaded batches are stored durably on arrival and '
              'extracted later, one at a time, so a burst of uploads never overloads the '
              'server. A background worker drains this automatically; you can also process '
              'on demand below.<br>If the AI extractor runs out of tokens/quota, the job '
              f'is parked as <b>waiting</b> and retried automatically every '
              f'{IQ.RETRY_AFTER_TOKENS // 3600}h. After {IQ.MAX_TOKEN_RETRIES} retries the '
              'auto-processing stops and the job is <b>held</b> in the waiting room — it '
              'stays safe until you top up the API credit and press <b>Send now</b>.</div></div>'
            + '<div class="card"><h2>Process backlog</h2>'
            + '<div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap;margin-bottom:8px">'
            + send_all_btn + process_form + '</div>'
            + '<div class="note"><b>Send / restart all</b> resets every waiting/held/failed '
              'document and runs the whole backlog now. Or run a dedicated worker process: '
              '<kbd>python waiting_room.py --work</kbd>.</div>'
            + gate + '</div>'
            + '<div class="card"><h2>Jobs</h2>'
            + (tbl(["#", "File", "Extractor", "Period", "By", "Uploaded", "Status",
                    "Tries", "Last error", "Action"], rows) if rows
               else '<p class="note">The waiting room is empty.</p>')
            + '</div>')
    return page(body, "queue")

@app.route("/queue/review/<int:job_id>")
def intake_review(job_id):
    """Open a ready queue job in the standard review/confirm screen. The source
    PDF bytes are re-derived from the kept inbox file and stashed for the existing
    confirm path; on commit the job is marked done."""
    import waiting_room as IQ, extract as EX
    import os as _os, pickle
    job = IQ.get_job(job_id)
    if not job or job["status"] != "ready":
        return page('<div class="card"><b class="bad">Job is not ready to review (it may '
                    'still be queued, processing, or failed).</b></div>'
                    '<p><a href="/queue">← back to the waiting room</a></p>', "queue")
    draft, _ = IQ.get_draft(job_id)
    # re-derive (name, bytes) pairs from the kept inbox file so confirm can attach
    # the source PDFs to the document vault exactly like the live path does.
    pairs = EX.unpack(IQ.read_bytes(job["stored_path"]), job["filename"])
    token = _os.urandom(8).hex()
    tmp = _os.path.join(WORKDIR, ".extract_tmp"); _os.makedirs(tmp, exist_ok=True); _os.chmod(tmp, 0o700)
    with open(_os.path.join(tmp, token + ".pkl"), "wb") as pf:
        pickle.dump(pairs, pf)
    return page(_review_form(draft, token, intake_job=job_id, period=job.get("period")), "queue")

@app.route("/mining", methods=["GET", "POST"])
def doc_mining_page():
    """Mine the vaulted documents for VAT numbers and propose fills for the INPUT
    gaps in master data. Capability: data_import (it writes master data on apply)."""
    import doc_mining as DM
    banner = ""
    if request.method == "POST" and request.form.get("__act") == "apply":
        try:
            if request.form.get("kind") == "supplier":
                DM.apply_supplier_vat(request.form["who"], request.form["country"],
                                      request.form["value"])
            else:
                DM.apply_customer_vat(request.form["who"], request.form["value"])
            banner = (f'<div class="card"><b class="ok">Applied {esc(request.form["value"])} '
                      f'to {esc(request.form["who"])}. Logged in History.</b></div>')
        except Exception as e:
            _log_exc("doc mining apply", e)
            banner = f'<div class="card"><b class="bad">Could not apply: {esc(str(e))}</b></div>'
    try:
        props = DM.proposals()
    except Exception as e:
        _log_exc("doc mining scan", e)
        props = []
    trs = []
    for p in props:
        who = p.get("supplier") or p.get("customer")
        applyf = ('<form method="post" style="display:inline">' + _csrf_input()
                  + f'<input type="hidden" name="kind" value="{esc(p["kind"])}">'
                  + f'<input type="hidden" name="who" value="{esc(who)}">'
                  + f'<input type="hidden" name="country" value="{esc(p.get("country") or "")}">'
                  + f'<input type="hidden" name="value" value="{esc(p["value"])}">'
                  + '<button name="__act" value="apply">Apply</button></form>')
        trs.append([f"<td>{esc(p['kind'])}</td><td>{esc(who)}</td><td>{esc(p.get('country') or '')}</td>",
                    f"<td>{esc(p['field'])}</td><td><b>{esc(p['value'])}</b></td>",
                    f"<td class=note>{esc(p['reason'])}</td><td>{applyf}</td>"])
    body = (banner + '<div class="card"><h2>Document mining — fill INPUT gaps from your vault</h2>'
            + f'<div class="kpis"><div class="kpi"><div class="v">{len(props)}</div>'
              '<div class="l">proposed fills</div></div></div>'
            + (tbl(["For", "Who", "Country", "Field", "Proposed value", "Why", ""], trs) if trs
               else '<p class="note">No proposals — either no gaps, or no matching VAT numbers found in the '
                    'vaulted documents. (PDF mining needs poppler/pypdf; structured XML always works.)</p>')
            + '<div class="note">Re-reads every vaulted invoice document (PDF text + structured XML), extracts '
              'EU VAT numbers, and proposes them only where the master field is still empty/INPUT and the country '
              'code matches. Nothing is written until you press <b>Apply</b> (which is audit-logged).</div></div>')
    return page(body, "min")

@app.route("/contracts", methods=["GET", "POST"])
def contracts():
    """Contract-compliance auditor: invoiced prices vs contracted discount terms,
    with the recoverable EUR per breach. Capability: invoice_control."""
    import contract_audit as CA, supplier_master as SM
    banner = ""
    if request.method == "POST":
        act = request.form.get("__act")
        try:
            if act == "add_rule":
                def _f(name):
                    v = request.form.get(name, "").strip()
                    return float(v) if v else None
                SM.set_discount_rule(request.form["supplier"].strip(),
                                     request.form.get("country", "%").strip() or "%",
                                     request.form.get("station_like", "%").strip() or "%",
                                     request.form.get("product_group", "Diesel").strip() or "Diesel",
                                     expected_discount_eur_l=_f("expected_discount_eur_l"),
                                     max_net_eur_l=_f("max_net_eur_l"),
                                     note=request.form.get("note", "").strip())
                banner = '<div class="card"><b class="ok">Contract rule added.</b></div>'
            elif act == "del_rule":
                SM.delete_discount_rule(int(request.form.get("rule_id", "0")))
                banner = '<div class="card"><b class="ok">Rule removed.</b></div>'
        except Exception as e:
            _log_exc("contract rule", e)
            banner = f'<div class="card"><b class="bad">{esc(str(e))}</b></div>'
    period = request.args.get("period", "").strip() or None
    flags, summ = CA.audit(period)
    ftrs = [[f"<td>{esc(f['supplier'])}</td><td>{esc(f['country'])}</td><td>{esc(f['station'] or '')}</td>",
             f"<td>{esc(f['period'])}</td><td class='{'bad'}'>{esc(f['issue'])}</td>",
             f"<td class=r>{f['expected']:.3f}</td><td class=r>{f['actual']:.3f}</td>",
             f"<td class=r>{f['litres']:,.0f}</td><td class='r bad'>{f['recover_eur']:,.2f}</td>",
             f"<td class=note>{esc(f['note'])}</td>"] for f in flags[:200]]
    rules = SM.discount_rules()
    rtrs = []
    for r in rules:
        delf = ('<form method="post" style="display:inline">' + _csrf_input()
                + f'<input type="hidden" name="rule_id" value="{r["id"]}">'
                + '<button name="__act" value="del_rule" style="background:var(--mut)">×</button></form>')
        rtrs.append([f"<td>{esc(r['supplier'])}</td><td>{esc(r['country'])}</td><td>{esc(r['station_like'])}</td>",
                     f"<td>{esc(r['product_group'])}</td>",
                     f"<td class=r>{('%.3f'%r['expected_discount_eur_l']) if r['expected_discount_eur_l'] is not None else '—'}</td>",
                     f"<td class=r>{('%.3f'%r['max_net_eur_l']) if r['max_net_eur_l'] is not None else '—'}</td>",
                     f"<td class=note>{esc(r['note'] or '')}</td><td>{delf}</td>"])
    bysup = " · ".join(f"{esc(s)} €{v:,.0f}" for s, v in sorted(summ["by_supplier"].items(),
                                                               key=lambda kv: -kv[1]))
    body = (banner
            + f'<form class="f" method="get"><label>Period (blank = all)<input name="period" value="{esc(period or "")}" style="width:110px"></label><button>Audit</button></form>'
            + '<div class="card"><h2>Contract-compliance audit</h2>'
            + '<div class="kpis">'
            + f'<div class="kpi"><div class="v bad">EUR {summ["total_recover"]:,.0f}</div><div class="l">recoverable (rule breaches)</div></div>'
            + f'<div class="kpi"><div class="v">{summ["flags"]}</div><div class="l">flags</div></div>'
            + f'<div class="kpi"><div class="v">{summ["rules"]}</div><div class="l">active rules</div></div></div>'
            + (f'<div class="note">By supplier: {bysup}</div>' if bysup else '')
            + (tbl(["Supplier", "Country", "Station", "Period", "Issue", "Expected", "Actual",
                    "Litres", "Recover €", "Note"], ftrs) if ftrs
               else '<p class="note">No breaches against the current rules. Add rules below to audit against your contracts.</p>')
            + '<div class="note">“short discount” = the rebate applied (net − effective, per litre) is below the '
              'contracted EUR/L; “over ceiling” = effective NET price above a contracted max. Recover € = shortfall × litres.</div></div>'
            + '<div class="card"><h2>Contract rules</h2>'
            + (tbl(["Supplier", "Country", "Station LIKE", "Product", "Disc €/L", "Max €/L", "Note", ""], rtrs)
               if rtrs else '<p class="note">No rules yet.</p>')
            + '<form method="post" class="f" style="margin-top:10px">' + _csrf_input()
            + '<label>supplier<input name="supplier" required style="width:90px"></label>'
            + '<label>country LIKE<input name="country" value="%" style="width:90px"></label>'
            + '<label>station LIKE<input name="station_like" value="%" style="width:120px"></label>'
            + '<label>product<input name="product_group" value="Diesel" style="width:80px"></label>'
            + '<label>expected discount €/L<input name="expected_discount_eur_l" style="width:90px" placeholder="e.g. 0.205"></label>'
            + '<label>max NET €/L<input name="max_net_eur_l" style="width:80px" placeholder="optional"></label>'
            + '<label>note<input name="note" style="width:160px"></label>'
            + '<button name="__act" value="add_rule">Add rule</button></form>'
            + '<div class="note">Country/station are SQL LIKE patterns (% = any). Use <b>expected discount</b> '
              'for rebate-style suppliers (Q8/Port One, where effective &lt; doc price) and <b>max NET</b> where the '
              'discount is in the doc price.</div></div>')
    return page(body, "con")

@app.route("/invoices", methods=["GET", "POST"])
def invoice_ctrl():
    import invoice_control
    period = request.values.get("period", "2026-05")
    banner = ""
    if request.method == "POST" and request.form.get("__stmt"):
        try:
            lines = []
            for ln in request.form["lines"].strip().splitlines():
                p = [x.strip() for x in ln.split(";")]
                if len(p) >= 6:
                    lines.append((p[0], p[1], p[2], p[3], p[4], p[5]))
            n = invoice_control.register_statement(
                request.form["supplier"], request.form["stmt_ref"], period,
                request.form.get("stmt_date",""), lines, notes=request.form.get("notes"))
            banner = (f'<div class="card"><b class="ok">Statement registered: {len(lines)} issued '
                      f'invoices, {n} VAT-bearing auto-synced to the registry. Attach the statement '
                      f'PDF via the Documents page.</b></div>')
        except Exception as e:
            _log_exc("statement register", e)
            banner = f'<div class="card"><b class="bad">Statement error: {esc(str(e))}</b></div>'
    rows, orphans = invoice_control.run_control(period)
    order = {"MISSING": 0, "RECEIVED - DOC MISSING": 1}
    rows.sort(key=lambda x: (order.get(x["status"], 2), x["supplier"]))
    trs = []
    for r in rows:
        cls = ("bad" if r["status"]=="MISSING" else
               "bad" if "DOC MISSING" in r["status"] else
               "" if r["status"]=="NO ACTIVITY" else "ok")
        trs.append([f"<td>{esc(r['supplier'])}</td><td>{esc(r['country'])}</td><td>{esc(r['slot'])}</td>",
                    f"<td>{esc(r['expected'])}</td><td>{esc(r['invoice_no'] or '—')}</td>",
                    f"<td class='{cls}'>{esc(r['status'])}</td><td class='note'>{esc(r['note'])}</td>"])
    miss = sum(1 for r in rows if r["status"]=="MISSING")
    orph = "".join(f"<li>{esc(o)}</li>" for o in orphans)
    stmts = invoice_control.reconcile_statements(period)
    s_trs = []
    for r in stmts:
        cls = ("ok" if r["verdict"]=="PROCESS - COMPLETE" else
               "" if r["verdict"].startswith("DISCARD") else "bad")
        s_trs.append([f"<td>{esc(r['supplier'])}</td><td>{esc(r['statement'])}</td><td>{esc(r['invoice'])}</td>",
                      f"<td>{esc(r['country'])}</td><td class=r>{r['net']:,.2f}</td><td class=r>{r['vat']:,.2f}</td>",
                      f"<td class='{cls}'>{esc(r['verdict'])}</td><td class='note'>{esc(r['action'])}</td>"])
    stmt_html = (('<div class="card"><h2>Statement reconciliation - every invoice the supplier issued, triaged by VAT</h2>'
                  + tbl(["Supplier","Statement","Issued invoice","Country","Net","VAT","Verdict","Action"], s_trs)
                  + '<div class="note">VAT &gt; 0 -> PROCESS (original required, feeds the refund claim). '
                    'VAT = 0 -> DISCARD (archive only). VAT-bearing lines auto-register so the VAT module '
                    'and receipt control see them.</div></div>') if stmts else "")
    reg_form = ('<div class="card"><h2>Register a summary statement</h2>'
                f'<form method="post" action="/invoices?period={esc(period)}">'
                + _csrf_input() +
                '<input type="hidden" name="__stmt" value="1">'
                '<div class="f"><label>supplier code<input name="supplier" placeholder="Q8" required></label>'
                '<label>statement ref<input name="stmt_ref" required></label>'
                '<label>statement date<input type="date" name="stmt_date"></label>'
                '<label>notes<input name="notes"></label></div>'
                '<label style="display:block;font-size:12px;color:var(--mut)">issued invoice lines - one per line: '
                'invoice_no; date; country; currency; net; vat</label>'
                '<textarea name="lines" rows="5" style="width:100%" '
                'placeholder="BEOI00118939; 2026-05-31; Belgium; EUR; 37955.70; 7970.70"></textarea>'
                '<div style="margin-top:8px"><button>Register statement</button></div></form></div>')
    body = (banner + stmt_html + reg_form + f'<form class="f" method="get"><label>Period (YYYY-MM)'
            f'<input name="period" value="{esc(period)}"></label><button>Run control</button></form>'
            f'<div class="card"><h2>Invoice receipt control — {esc(period)}: '
            + (f'<span class="bad">{miss} missing to chase</span>' if miss else '<span class="ok">complete</span>')
            + '</h2>'
            + tbl(["Supplier","Country","Slot","Expected cadence","Invoice received","Status","Note"], trs)
            + (f'<h2 style="margin-top:12px">Orphan transactions (not covered by any invoice)</h2><ul>{orph}</ul>' if orphans else '')
            + '<div class="note">Expectation = supplier cadence (every 14 / 30 days, from suppliers.db) '
              'x activity from transactions. NO ACTIVITY = no transactions, no invoice expected (OK). '
              'Results persist audited in invoice_receipt_control; waive a slot via Data manager '
              '(set waived=1).</div></div>')
    return page(body, "inv")

# ---------------------------------------------------------------- VAT refunds
@app.route("/pricing")
def pricing():
    import pricing_intelligence as PI
    grain = request.args.get("grain", "month")
    country = request.args.get("country", "")
    rows, summ = PI.margin_report(None, grain)
    if country:
        rows = [r for r in rows if r["country"] == country]
    countries = sorted({r["country"] for r in PI.margin_report(None, grain)[0]})
    csel = '<option value="">All countries</option>' + "".join(
        f'<option {"selected" if c==country else ""}>{esc(c)}</option>' for c in countries)
    gtab = "".join(
        f'<a href="/pricing?grain={g}&country={esc(country)}" '
        f'style="font-size:13px;padding:5px 12px;border-radius:6px;margin-right:6px;'
        f'{"background:var(--info);color:#fff" if g==grain else "border:1px solid var(--bd)"}">{g}</a>'
        for g in ("day", "week", "month"))
    trs = []
    for r in rows[:200]:
        gmy = r["gap_vs_my"]
        cls = "bad" if gmy and gmy > 0 else ("ok" if gmy and gmy < 0 else "")
        mbadge = {"exact":"ok","near-date":"","city-avg":"","country-avg":"","no-benchmark":"bad"}.get(r["match"],"")
        trs.append([
            f"<td>{esc(r['country'])}</td><td>{esc(r['city'] or '')}</td>",
            f"<td>{esc(r['bucket'])}</td><td>{esc(r['supplier'])}</td>",
            f"<td class=r>{r['eff_price']:.3f}</td>",
            f"<td class=r>{(r['my_price'] or 0):.3f}</td>",
            f"<td class='r {cls}'>{(gmy if gmy is not None else 0):+.3f}</td>",
            f"<td class=r>{(r['pack_avg'] or 0):.3f}</td>",
            f"<td class=r>{(r['margin_vs_wholesale'] if r['margin_vs_wholesale'] is not None else 0):.3f}</td>",
            f"<td class='r {cls}'>{(r['eur_impact'] or 0):,.0f}</td>",
            f"<td class='{mbadge}'>{esc(r['match'])}</td>"])
    body = (
        '<div class="card"><h2>Pricing intelligence — competitor NET price &amp; margin</h2>'
        '<div class="note">All prices NET EUR/L, final (rebates applied, VAT excluded). '
        'gap vs MY = supplier − your benchmark; pack = vs other suppliers same city; '
        'margin vs wholesale = true margin if index loaded.</div>'
        f'<div style="margin:10px 0">{gtab}'
        f'<form method="get" style="display:inline;margin-left:10px">'
        f'<input type="hidden" name="grain" value="{esc(grain)}">'
        f'<select name="country" onchange="this.form.submit()">{csel}</select></form></div>'
        f'<div class="kpis"><div class="kpi"><div class="v bad">EUR {summ["total_overpay"]:,.0f}</div>'
        f'<div class="l">overpay vs MY Prices</div></div>'
        f'<div class="kpi"><div class="v">{summ["matched_litres"]:,} L</div><div class="l">matched to benchmark</div></div>'
        f'<div class="kpi"><div class="v">{summ["unmatched_litres"]:,} L</div><div class="l">no benchmark (shown openly)</div></div>'
        f'<div class="kpi"><div class="v">{summ["rows"]}</div><div class="l">price points ({esc(grain)})</div></div></div>'
        + tbl(["Country","City","Period","Supplier","Eff NET","MY","gap vs MY","Pack avg",
               "Margin vs whsl","EUR impact","Match"], trs)
        + f'<p style="margin-top:10px"><a href="/export/pricing?grain={esc(grain)}">⬇ Export daily/weekly/monthly grid (Excel)</a></p>'
        + '</div>'
        + '<div class="card"><h2>Upload MY Prices (your NET benchmark)</h2>'
        '<form method="post" action="/pricing/upload" enctype="multipart/form-data" class="f">'
        + _csrf_input() +
        '<label>CSV file<input type="file" name="file" accept=".csv" required></label>'
        '<label><input type="checkbox" name="replace"> replace existing for these months</label>'
        '<button>Upload MY Prices</button></form>'
        '<div class="note">CSV columns: <b>country,city,date,net_price</b> (date YYYY-MM-DD, '
        'price NET final EUR/L). Optional 5th column product_group (default Diesel). '
        'Also accepts wholesale index via columns <b>country,date,net_price</b> using the '
        'wholesale upload below.</div>'
        '<form method="post" action="/pricing/upload?kind=wholesale" enctype="multipart/form-data" class="f" style="margin-top:8px">'
        + _csrf_input() +
        '<label>Wholesale index CSV<input type="file" name="file" accept=".csv" required></label>'
        '<button>Upload wholesale index</button></form>'
        + '<form method="post" action="/pricing/market" style="margin-top:6px">' + _csrf_input()
        + '<button>↻ Scrape market prices (official/open data)</button></form>'
        '<div class="note">Wholesale columns: country,date,net_price (NET, pre-tax). Enables the '
        'true-margin (margin vs wholesale) column. <b>Scrape</b> pulls from official open-data '
        'sources (EU Weekly Oil Bulletin / national portals) configured via MARKET_JSON_URL / '
        'MARKET_CSV_URL — or upload a CSV on a locked-down network.</div></div>'
        + _benchmark_card(grain)
        + _portals_card())
    return page(body, "pri")

def _benchmark_card(grain):
    """Self-sourced competitor benchmark: the best price you actually achieved per
    country/city and the avoidable overpay — built only from your own multi-supplier
    purchases, no external data."""
    import pricing_intelligence as PI
    try:
        rows, summ = PI.internal_benchmark(None, grain)
    except Exception as e:
        _log_exc("internal benchmark", e)
        return ""
    trs = []
    for r in [x for x in rows if x["suppliers"] > 1][:50]:
        trs.append([f"<td>{esc(r['country'])}</td><td>{esc(r['bucket'])}</td>",
                    f"<td class=r>{r['suppliers']}</td><td>{esc(r['best_supplier'])}</td>",
                    f"<td class=r>{r['best_price']:.3f}</td><td class=r>{(r['your_avg'] or 0):.3f}</td>",
                    f"<td class=r>{r['spread']:.3f}</td><td class=r>{r['litres']:,.0f}</td>",
                    f"<td class='r {'bad' if r['overpay_eur'] else ''}'>{r['overpay_eur']:,.0f}</td>"])
    return ('<div class="card"><h2>Self-sourced benchmark (from your own purchases)</h2>'
            '<div class="kpis">'
            f'<div class="kpi"><div class="v bad">EUR {summ["total_overpay"]:,.0f}</div>'
            '<div class="l">avoidable overpay vs best you achieved</div></div>'
            f'<div class="kpi"><div class="v">{summ["multi_supplier_cells"]}</div>'
            '<div class="l">comparable cells (2+ suppliers)</div></div>'
            f'<div class="kpi"><div class="v">{summ["litres"]:,.0f} L</div><div class="l">litres covered</div></div></div>'
            + (tbl(["Country", "Period", "Suppliers", "Best", "Best €/L",
                    "Your avg", "Spread", "Litres", "Overpay €"], trs)
               if trs else '<p class="note">Need two or more suppliers in the same country/period to compare.</p>')
            + '<div style="display:flex;gap:8px;align-items:center;margin-top:10px;flex-wrap:wrap">'
            + f'<a href="/export/benchmark?grain={esc(grain)}">⬇ Export benchmark (Excel)</a>'
            + '<form method="post" action="/pricing/adopt-benchmark" style="display:inline">' + _csrf_input()
            + f'<input type="hidden" name="grain" value="{esc(grain)}">'
            + '<button>Adopt best-of as MY benchmark</button></form></div>'
            '<div class="note">Each supplier you used is a price point — the lowest you actually paid '
            'for the same product/place/period is your benchmark, and the overpay is what routing volume '
            'to the cheaper supplier would have saved. No external/scraped data. '
            '<b>Adopt</b> loads these best prices into MY Prices so the margin grid measures everyone '
            'against them.</div></div>')

def _portals_card():
    """Client supplier-portal scrapers: configured portals with a 'Scrape now' button,
    and (admin) forms to add a portal + store its encrypted credentials. Populating MY
    Prices automatically from the entities' own authorized accounts."""
    import portal_scraper as PS
    is_admin = session.get("role") == "admin"
    try:
        portals = PS.list_portals()
    except Exception as e:
        _log_exc("portal list", e)
        portals = []
    rows = []
    for p in portals:
        lr = p["last_run"]
        last = (f'<span class="{"ok" if lr["status"]=="ok" else "bad" if lr["status"]=="failed" else ""}">'
                f'{esc(lr["status"])}</span> · {esc(lr.get("finished") or "")} · {lr["rows"]} rows'
                if lr else '<span class="note">never</span>')
        creds = ('<span class="ok">stored</span>' if p["has_creds"]
                 else '<span class="bad">none</span>')
        scrape_btn = ('<form method="post" action="/pricing/portal" style="display:inline">' + _csrf_input()
                      + f'<input type="hidden" name="supplier" value="{esc(p["supplier"])}">'
                      + f'<input type="hidden" name="entity" value="{esc(p["entity"] or "")}">'
                      + '<button name="__act" value="scrape">↻ Scrape now</button></form>'
                      if p["enabled"] and (p["has_creds"] or p["kind"] == "demo") else
                      '<span class="note">configure creds</span>')
        rows.append([f"<td>{esc(p['supplier'])}</td><td>{esc(p['entity'] or '—')}</td>",
                     f"<td>{esc(p['kind'])}</td><td>{'on' if p['enabled'] else 'off'}</td>",
                     f"<td>{creds}</td><td class=note>{last}</td><td>{scrape_btn}</td>"])
    table = (tbl(["Supplier", "Entity", "Kind", "Enabled", "Credentials", "Last run", ""], rows)
             if rows else '<p class="note">No portals configured yet.</p>')
    admin_forms = ""
    if is_admin:
        admin_forms = (
            '<details style="margin-top:10px"><summary><b>Add / update a portal (admin)</b></summary>'
            '<form method="post" action="/pricing/portal" class="f" style="margin-top:8px">' + _csrf_input()
            + '<label>supplier code<input name="supplier" required style="width:110px"></label>'
            + '<label>kind<select name="kind">'
            + ''.join(f'<option>{k}</option>' for k in ("demo", "http_json", "csv", "custom"))
            + '</select></label>'
            + '<label>base URL<input name="base_url" style="width:220px" placeholder="https://portal.supplier.com"></label>'
            + '<label><input type="checkbox" name="enabled" checked> enabled</label>'
            + '<label style="flex-basis:100%">config JSON (endpoints / field map; see portal_scraper.py)'
              '<textarea name="config" rows="3" style="width:100%;font-family:monospace" '
              'placeholder=\'{"price_url":"/api/prices","rows_path":"data","map":{"country":"ctry","city":"station","date":"day","net_price":"net"}}\'></textarea></label>'
            + '<button name="__act" value="save_config">Save portal</button></form>'
            '<form method="post" action="/pricing/portal" class="f" style="margin-top:6px">' + _csrf_input()
            + '<label>supplier<input name="supplier" required style="width:90px"></label>'
            + '<label>entity<input name="entity" required style="width:120px"></label>'
            + '<label>username<input name="username" autocomplete="off"></label>'
            + '<label>password / token<input name="secret" type="password" autocomplete="new-password"></label>'
            + '<button name="__act" value="save_creds">Store credentials (encrypted)</button></form>'
            '</details>')
    note = ('<div class="note">Pulls each entity\'s own NET prices from its authorized supplier '
            'portal into <b>MY Prices</b> (source <code>portal:&lt;SUPPLIER&gt;</code>), so the '
            'benchmark stays current without manual CSV uploads. Credentials are encrypted at rest; '
            'use only portals you are authorized to access. Live portals need outbound network — on a '
            'locked-down box, scrape from a connected machine or keep using CSV upload. '
            'Run on a schedule with <kbd>python portal_scraper.py --scrape &lt;SUP&gt; &lt;ENT&gt;</kbd> '
            'via cron/Task Scheduler.</div>')
    return ('<div class="card"><h2>Client portal price scraping</h2>' + table + admin_forms + note + '</div>')

@app.route("/pricing/market", methods=["POST"])
def pricing_market():
    """Scrape official/open-data diesel market prices into the wholesale index."""
    import market_prices as MP
    try:
        info = MP.fetch_and_store()
        banner = (f'<div class="card"><b class="ok">Loaded {info["rows"]} market price(s) via '
                  f'{esc(info["source"])} — as of {esc(info["asof"])}, '
                  f'{len(info["countries"])} countries. The margin-vs-market column now reflects '
                  f'them.</b></div>')
    except Exception as e:
        _log_exc("market-price scrape", e)
        banner = (f'<div class="card"><b class="bad">Could not scrape market prices: '
                  f'{esc(str(e))}</b></div>')
    return page(banner + '<p><a href="/pricing">→ Back to Pricing intel</a></p>', "pri")

@app.route("/pricing/portal", methods=["POST"])
def pricing_portal():
    """Configure client supplier-portal scrapers and run them. Scraping needs the
    pricing capability; storing credentials / portal config is admin-only (secrets)."""
    import portal_scraper as PS
    is_admin = session.get("role") == "admin"
    act = request.form.get("__act")
    banner = ""
    try:
        if act == "scrape":
            res = PS.scrape(request.form["supplier"].strip(),
                            request.form.get("entity", "").strip(),
                            request.form.get("date_from") or None,
                            request.form.get("date_to") or None)
            banner = (f'<div class="card"><b class="ok">Scraped {esc(res["supplier"])}: '
                      f'loaded {res["loaded"]} price row(s) into MY Prices '
                      f'(from {res["fetched"]} fetched). The grid &amp; margin columns now '
                      f'reflect them.</b></div>')
        elif act == "save_config" and is_admin:
            cfg_raw = request.form.get("config", "").strip() or "{}"
            import json as _json
            PS.set_config(request.form["supplier"].strip(), request.form.get("kind", "demo"),
                          base_url=request.form.get("base_url", "").strip(),
                          config=_json.loads(cfg_raw),
                          enabled=bool(request.form.get("enabled")))
            banner = '<div class="card"><b class="ok">Portal configuration saved.</b></div>'
        elif act == "save_creds" and is_admin:
            PS.set_credentials(request.form["supplier"].strip(), request.form["entity"].strip(),
                               request.form.get("username", "").strip(), request.form.get("secret", ""))
            banner = '<div class="card"><b class="ok">Portal credentials stored (encrypted).</b></div>'
        elif act == "delete_creds" and is_admin:
            PS.delete_credentials(request.form["supplier"].strip(), request.form["entity"].strip())
            banner = '<div class="card"><b class="ok">Credentials removed.</b></div>'
        elif not is_admin and act in ("save_config", "save_creds", "delete_creds"):
            banner = '<div class="card"><b class="bad">Only an admin can change portal credentials/config.</b></div>'
    except Exception as e:
        _log_exc("portal scrape/config", e)
        banner = f'<div class="card"><b class="bad">Portal action failed: {esc(str(e))}</b></div>'
    return page(banner + '<p><a href="/pricing">→ Back to Pricing intel</a></p>', "pri")

@app.route("/pricing/adopt-benchmark", methods=["POST"])
def pricing_adopt_benchmark():
    """Adopt the self-sourced best-of benchmark as the MY-Prices baseline."""
    import pricing_intelligence as PI
    grain = request.form.get("grain", "month")
    try:
        n = PI.adopt_internal_benchmark(None, grain)
        banner = (f'<div class="card"><b class="ok">Adopted {n} best-of price point(s) as your '
                  f'MY-Prices benchmark (source: internal). The gap/overpay columns now measure '
                  f'every supplier against the best price you actually achieved.</b></div>')
    except Exception as e:
        _log_exc("adopt internal benchmark", e)
        banner = f'<div class="card"><b class="bad">Could not adopt benchmark: {esc(str(e))}</b></div>'
    return page(banner + '<p><a href="/pricing">→ Back to Pricing intel</a></p>', "pri")

@app.route("/export/benchmark")
def export_benchmark():
    import pricing_intelligence as PI
    path = PI.internal_benchmark_workbook(None, request.args.get("grain", "month"))
    return send_file(path, as_attachment=True, download_name=os.path.basename(path))

@app.route("/pricing/upload", methods=["POST"])
def pricing_upload():
    import pricing_intelligence as PI, csv, io
    # access is enforced centrally in _guard (capability: pricing)
    kind = request.args.get("kind", "myprices")
    f = request.files.get("file")
    if not f:
        return redirect("/pricing")
    text = f.read().decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    fields = [h.strip().lower() for h in (reader.fieldnames or [])]
    rows = []
    try:
        if kind == "wholesale":
            for row in reader:
                row = {k.strip().lower(): v for k, v in row.items()}
                rows.append({"country": row["country"], "date": row["date"],
                             "net_price": float(row["net_price"])})
            n = PI.load_wholesale(rows)
            msg = f"{n} wholesale index rows loaded — true-margin column now active."
        else:
            for row in reader:
                row = {k.strip().lower(): v for k, v in row.items()}
                rows.append({"country": row["country"], "city": row["city"],
                             "date": row["date"], "net_price": float(row["net_price"]),
                             "product_group": row.get("product_group", "Diesel")})
            n = PI.load_my_prices(rows)
            msg = f"{n} MY Price rows loaded."
    except (KeyError, ValueError) as e:
        return page(f'<div class="card"><b class="bad">CSV error: {esc(str(e))}. '
                    f'Found columns: {esc(", ".join(fields))}</b></div>', "pri")
    return page(f'<div class="card"><b class="ok">{esc(msg)}</b> '
                '<a href="/pricing">→ back to pricing intel</a></div>', "pri")

@app.route("/export/pricing")
def export_pricing():
    import pricing_intelligence as PI
    path, _ = PI.export_excel(grain=request.args.get("grain", "month"))
    return send_file(path, as_attachment=True)

@app.route("/api/pricing")
def api_pricing():
    import pricing_intelligence as PI
    rows, summ = PI.margin_report(None, request.args.get("grain", "month"),
                                  request.args.get("pg", "Diesel"))
    return jsonify({"summary": summ, "rows": rows})

@app.route("/recovery", methods=["GET", "POST"])
def recovery():
    import vat_refund as VR, customer_master as CD
    year = request.args.get("year", "2026")
    banner = ""
    if request.method == "POST" and request.form.get("__act") == "issue_invoice":
        con = VR.connect()
        ok, res = VR.issue_fee_invoice(con, request.form.get("entity", ""),
                                       request.form.get("country", ""), request.form.get("period", ""))
        con.close()
        banner = (f'<div class="card"><b class="{"ok" if ok else "bad"}">'
                  + (f"Fee invoice {esc(res)} issued." if ok else f"Could not issue invoice: {esc(res)}")
                  + '</b></div>')
    rows, summ = VR.recovery_report(year)
    trs = []
    total_charged = total_net = total_recv = 0.0
    for r in rows:
        agecls = "bad" if isinstance(r["age_days"], int) and r["age_days"] > 120 else ""
        vat = r["vat_eur"] or 0
        if r.get("fee_eur") is None:   # legacy rows predating fee-freezing
            fee, basis = CD.compute_fee(vat, *CD.fee_for(r["entity"], r["country"]))
        else:
            fee = r["fee_eur"]
            pct_fee = round((r.get("fee_pct") or 0) / 100 * vat, 2)
            basis = "percent" if pct_fee >= (r.get("fee_min") or 0) else "minimum"
        billed = r.get("fee_billed_date")
        refund = r.get("paid_amount") or vat
        st = VR.settlement(r.get("payout_to"), refund, fee)
        if billed:
            total_charged += fee
            total_net += st["net_to_customer"]; total_recv += st["fee_receivable"]
        if st["route"] == "us":
            settle = f'to us; remit <b>€{st["net_to_customer"]:,.2f}</b> net to customer'
        else:
            settle = f'to customer; invoice <b>€{st["fee_receivable"]:,.2f}</b> fee'
        link = (f'/export/fee?entity={esc(r["entity"])}&country={esc(r["country"])}'
                f'&period={esc(r["period"])}')
        inv_no = r.get("fee_invoice_no")
        hid = (f'<input type="hidden" name="entity" value="{esc(r["entity"])}">'
               f'<input type="hidden" name="country" value="{esc(r["country"])}">'
               f'<input type="hidden" name="period" value="{esc(r["period"])}">')
        if inv_no:
            inv_cell = f'{esc(inv_no)} <a href="{link}">⬇ invoice</a>'
        elif billed:
            inv_cell = ('<form method="post" style="display:inline">' + _csrf_input() + hid
                        + '<button name="__act" value="issue_invoice">Issue invoice</button></form> '
                        + f'<a href="{link}">⬇ report</a>')
        else:
            inv_cell = f'<a href="{link}">⬇ report</a>'
        trs.append([f"<td>{esc(r['entity'])}</td><td>{esc(r['country'])}</td><td>{esc(r['period'])}</td>",
                    f"<td class=r>{vat:,.2f}</td>",
                    f"<td class=r>{fee:,.2f}</td><td class='{'ok' if billed else 'note'}'>{esc(basis)} · {'charged' if billed else 'pending'}</td>",
                    f"<td class=note>{settle}</td>",
                    f"<td>{esc(r['status'])}</td>",
                    f"<td class='{agecls}'>{r['age_days'] if r['age_days']!='' else ''}</td>",
                    f"<td>{esc(r['paid'] or '')}</td><td>{inv_cell}</td>"])
    body = (banner + f'<form class="f" method="get"><label>Year<input name="year" value="{esc(year)}" style="width:80px"></label>'
            f'<a href="/export/fees?year={esc(year)}" style="align-self:end;padding:8px 12px;font-size:13px">⬇ Fees statement (Excel)</a></form>'
            + f'<div class="card"><h2>VAT recovery &amp; fee settlement {esc(year)}</h2>'
            f'<div class="kpis"><div class="kpi"><div class="v">EUR {summ["submitted"]:,.0f}</div>'
            f'<div class="l">submitted</div></div>'
            f'<div class="kpi"><div class="v ok">EUR {summ["paid"]:,.0f}</div><div class="l">paid back</div></div>'
            f'<div class="kpi"><div class="v bad">EUR {summ["outstanding"]:,.0f}</div>'
            f'<div class="l">outstanding</div></div>'
            f'<div class="kpi"><div class="v ok">EUR {total_charged:,.0f}</div><div class="l">fees charged</div></div>'
            f'<div class="kpi"><div class="v">EUR {total_net:,.0f}</div><div class="l">net remitted to customers</div></div></div>'
            + tbl(["Entity","Country","Period","VAT EUR","Our fee","Settlement","Status",
                   "Age (days)","Paid","Fee invoice / report"], trs)
            + '<div class="note">Fee rate is frozen at submission and <b>charged when the refund is '
              'paid</b>. Settlement depends on where the refund lands (set per customer): to the '
              '<b>customer</b> → we issue a fee invoice; to <b>us</b> → we deduct the fee and remit '
              'the net. Issue the fee invoice once paid; the report becomes the invoice. Age over 120 '
              'days flagged red.</div></div>')
    return page(body, "rec")

@app.route("/readiness")
def readiness():
    """Can we submit? Per claimable quarter: READY vs BLOCKED (with reasons), plus
    a report of currently open (submitted/awaiting-refund) claims."""
    import vat_refund as VR
    year = request.args.get("year", "2026")
    ov = VR.claims_overview(year)
    ts, op = ov["to_submit"], ov["open"]
    nready = sum(1 for c in ts if c["ready"])
    trs1 = []
    for c in ts:
        verdict = ('<span class="ok">READY ✓</span>' if c["ready"]
                   else '<span class="bad">BLOCKED ✗</span>')
        trs1.append([f"<td>{esc(c['entity'])}</td><td>{esc(c['country'])}</td><td>{esc(c['period'])}</td>",
                     f"<td class=r>{(c['vat_eur'] or 0):,.2f}</td>",
                     f"<td>{verdict}</td><td class='note'>{esc('; '.join(c['issues']))}</td>"])
    trs2 = []
    open_vat = 0.0
    for c in op:
        open_vat += c["vat_eur"] or 0
        agecls = "bad" if isinstance(c["age_days"], int) and c["age_days"] > 120 else ""
        trs2.append([f"<td>{esc(c['entity'])}</td><td>{esc(c['country'])}</td><td>{esc(c['period'])}</td>",
                     f"<td class=r>{(c['vat_eur'] or 0):,.2f}</td>",
                     f"<td>{esc(c['status'])}</td><td>{esc(c['submitted'] or '')}</td>",
                     f"<td class='{agecls}'>{c['age_days'] if c['age_days'] != '' else ''}</td>"])
    body = (f'<form class="f" method="get"><label>Year<input name="year" value="{esc(year)}" style="width:80px"></label>'
            f'<a href="/export/readiness?year={esc(year)}" style="align-self:end;padding:8px 12px;font-size:13px">⬇ Export (Excel)</a></form>'
            + '<div class="kpis">'
            + f'<div class="kpi"><div class="v ok">{nready}</div><div class="l">ready to submit</div></div>'
            + f'<div class="kpi"><div class="v {"bad" if len(ts)-nready else ""}">{len(ts)-nready}</div><div class="l">blocked</div></div>'
            + f'<div class="kpi"><div class="v">{len(op)}</div><div class="l">open claims</div></div>'
            + f'<div class="kpi"><div class="v">EUR {open_vat:,.0f}</div><div class="l">open VAT</div></div></div>'
            + '<div class="card"><h2>Ready to submit — can we file this claim?</h2>'
            + tbl(["Entity", "Country", "Period", "VAT EUR", "Submission", "Blocking reasons"], trs1)
            + '<div class="note">READY = customer &amp; refund country activated, all invoice refs '
              'resolved, all documents attached, no duplicate locks, and the quarterly threshold met. '
              'Then submit on the VAT refunds page.</div></div>'
            + '<div class="card"><h2>Open claims — submitted, awaiting refund</h2>'
            + tbl(["Entity", "Country", "Period", "VAT EUR", "Status", "Submitted", "Age (days)"], trs2)
            + '<div class="note">Open = submitted/approved, not yet paid. Age over 120 days '
              'flagged red — chase the tax authority.</div></div>')
    return page(body, "rdy")

@app.route("/export/readiness")
def export_readiness():
    import vat_refund as VR, reports
    year = request.args.get("year", "2026")
    path = reports.claims_overview_workbook(VR.claims_overview(year), year)
    return send_file(path, as_attachment=True, download_name=os.path.basename(path),
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

@app.route("/export/fees")
def export_fees():
    import vat_refund as VR, reports
    year = request.args.get("year", "2026")
    rows, _ = VR.recovery_report(year)
    path = reports.fees_statement_workbook(rows, year)
    return send_file(path, as_attachment=True, download_name=os.path.basename(path),
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

@app.route("/anomalies")
def anomalies_page():
    import anomaly
    period = request.args.get("period", "2026-05")
    flags = anomaly.find(period)
    trs = [[f"<td>{esc(k)}</td>", f"<td class='{'bad' if lvl=='error' else ''}'>{esc(lvl)}</td>",
            f"<td>{esc(msg)}</td>"] for k, lvl, msg in flags]
    body = (f'<form class="f" method="get"><label>Period<input name="period" value="{esc(period)}"></label><button>Scan</button></form>'
            f'<div class="card"><h2>Anomaly scan — {esc(period)}: '
            + (f'<span class="bad">{len(flags)} flag(s)</span>' if flags else '<span class="ok">clean</span>') + '</h2>'
            + (tbl(["Type","Level","Detail"], trs) if flags else '<p>No anomalies detected.</p>')
            + '<div class="note">Relative checks: station price vs country average, month-over-month '
              'price jumps, vehicle volume spikes, off-period dates. Tune thresholds in anomaly.py.</div></div>')
    return page(body, "ano")

@app.route("/api/recovery")
def api_recovery():
    import vat_refund as VR
    rows, summ = VR.recovery_report(request.args.get("year", "2026"))
    return jsonify({"summary": summ, "claims": rows})

@app.route("/vat", methods=["GET", "POST"])
def vat():
    import vat_refund as VR
    con = VR.connect()
    banner = ""
    if request.method == "POST":
        ok, msg = VR.set_status(con, request.form["entity"], request.form["country"],
                                request.form["ref_period"], request.form["status"])
        cls = "ok" if ok else "bad"
        banner = (f'<div class="card"><b class="{cls}">{msg}</b></div>')
    year = request.args.get("year", "2026")
    matrix = VR.claim_matrix(con, year)
    sts = {(r["entity"], r["refund_country"], r["ref_period"]): r["status"]
           for r in con.execute("SELECT * FROM vat_applications")}
    docidx = VR.docs_index(con)        # one query instead of docs_for() per invoice
    inv_cache = {}                     # shared supplier conn + issuer/invoice memo
    rows = []
    for m in matrix:
        status = sts.get((m["entity"], m["country"], m["period"]), "draft")
        v = m["verdict"]
        vcls = "ok" if v.startswith("READY") else ("bad" if "BELOW" in v else "")
        opts = "".join(f'<option {"selected" if s==status else ""}>{s}</option>'
                       for s in ["draft","ready","submitted","approved","paid","rejected","withdrawn"])
        frm = (f'<form method="post" style="margin:0">'
               + _csrf_input() +
               f'<input type="hidden" name="entity" value="{esc(m["entity"])}">'
               f'<input type="hidden" name="country" value="{esc(m["country"])}">'
               f'<input type="hidden" name="ref_period" value="{esc(m["period"])}">'
               f'<select name="status" onchange="this.form.submit()">{opts}</select></form>')
        invs = VR.stream_invoices(con, m["entity"], m["country"], m["period"], inv_cache) if not m["period"].endswith("YEAR") else []
        nd = sum(1 for s, ref in invs if (m["entity"], s, ref) not in docidx)
        doccov = ("" if m["period"].endswith("YEAR") else
                  (f'<span class="ok">{len(invs)}/{len(invs)} docs</span>' if invs and nd==0
                   else f'<span class="bad">{len(invs)-nd}/{len(invs)} docs</span>'))
        rows.append([f"<td>{esc(m['entity'])}</td><td>{esc(m['country'])}</td><td>{esc(m['period'])}</td>",
                     f"<td class=r>{m['vat_eur']:,.2f}</td><td class=r>{m['vat_local']:,.2f} {esc(m['currency'])}</td>",
                     f"<td class='{vcls}'>{esc(v)}</td><td>{esc(', '.join(m['missing']))}</td>",
                     f"<td>{doccov}</td><td>{esc(m['home'])}</td><td>{esc(m['deadline'])}</td><td>{frm}</td>"])
    total_ready = sum(m["vat_eur"] for m in matrix
                      if m["verdict"].startswith("READY") and not m["period"].endswith("YEAR"))
    body = (banner + f'<div class="card"><h2>VAT refund applications {esc(year)} (2008/9/EC) — '
            f'quarterly READY total: <span class="ok">€{total_ready:,.0f}</span> &nbsp; '
            f'<a href="/export/vat?year={esc(year)}">⬇ Generate claim workbook</a></h2>'
            + tbl(["Entity","Refund country","Period","VAT EUR","VAT local","Threshold verdict",
                   "Months missing","Documents","Home portal","Deadline","Status"], rows)
            + '<div class="note">Statuses persist in the database. Yellow caveats and per-invoice '
              'claim packs are in the exported workbook. Quarterly min €400, annual min €50 '
              '(national equivalents apply).</div></div>')
    if inv_cache.get("_scon") is not None:
        inv_cache["_scon"].close()
    con.close(); return page(body, "vat")


@app.route("/documents", methods=["GET", "POST"])
def documents():
    import vat_refund as VR, supplier_master
    from supplier_specs import SPECS
    ENTITY_OVERRIDE = {"PORTONE": "Jupiter Plus AS", "EUROWAG": "Adverza Germany SIA"}
    scon = supplier_master.connect()
    INVOICES = {}
    for r in scon.execute("SELECT supplier, country, invoice_no, invoice_date FROM supplier_invoices ORDER BY supplier, invoice_date"):
        INVOICES.setdefault((r["supplier"], r["country"]), []).append((r["invoice_no"], r["invoice_date"]))
    scon.close()
    con = VR.connect()
    banner = ""
    if request.method == "POST":
        f = request.files["doc"]
        ok, msg = VR.attach_document(con, request.form["entity"], request.form["supplier"],
                                     request.form["invoice_ref"], file_bytes=f.read(),
                                     filename=f.filename, kind=request.form.get("kind","scan"))
        banner = f'<div class="card"><b class="{"ok" if ok else "bad"}">{msg}</b></div>'
    rows = []
    for (sup, ctry), invs in sorted(INVOICES.items()):
        ent = SPECS[sup]["entity"][0] if sup in SPECS else ENTITY_OVERRIDE.get(sup, sup)
        for ref, dt in invs:
            docs = VR.docs_for(con, ent, sup, ref)
            dl = " ".join(f'<a href="/doc/{d["id"]}">{esc(d["filename"])}</a> <span class="note">[{esc(d["sha256"][:8])}, {esc(d["kind"])}]</span>'
                          for d in docs) or '<span class="bad">MISSING</span>'
            up = (f'<form method="post" enctype="multipart/form-data" style="margin:0;display:flex;gap:6px">'
                  + _csrf_input() +
                  f'<input type="hidden" name="entity" value="{esc(ent)}">'
                  f'<input type="hidden" name="supplier" value="{esc(sup)}">'
                  f'<input type="hidden" name="invoice_ref" value="{esc(ref)}">'
                  f'<input type="file" name="doc" accept=".pdf,.jpg,.png,.tif" required>'
                  f'<select name="kind"><option>original_pdf</option><option>scan</option></select>'
                  f'<button>Attach</button></form>')
            rows.append([f"<td>{esc(ent)}</td><td>{esc(sup)}</td><td>{esc(ctry)}</td><td>{esc(ref)}</td><td>{esc(dt)}</td>",
                         f"<td>{dl}</td><td>{up}</td>"])
    body = banner + ('<div class="card"><h2>Invoice document vault — every invoice needs its '
                     'original PDF or scan before submission</h2>'
                     + tbl(["Entity","Supplier","Country","Invoice ref","Date","Attached document(s)","Upload"], rows)
                     + '<div class="note">Files are SHA-256 hashed; identical files on different '
                       'invoices trigger a wrong-attachment warning; submission is blocked while '
                       'any invoice in the application has no document.</div></div>')
    con.close(); return page(body, "doc")

@app.route("/suppliers")
def suppliers():
    import supplier_master
    con = supplier_master.connect()
    cards = []
    for s in con.execute("SELECT * FROM suppliers ORDER BY code"):
        meta = "".join(f"<tr><td style='color:var(--mut);width:140px'>{k.replace('_',' ')}</td><td>{esc(str(s[k]))}</td></tr>"
                       for k in ("legal_name","group_name","address","home_country","company_reg",
                                 "phone","email","portal","payment_terms","payment_notes","notes") if s[k])
        sect = ""
        for title, q, cols in (
            ("VAT registrations","SELECT country, COALESCE(vat_number,'<span class=bad>INPUT</span>') v, source FROM supplier_vat_registrations WHERE supplier=?",("country","v","source")),
            ("Bank accounts","SELECT beneficiary, iban, COALESCE(swift,'') s, bank, currency FROM supplier_bank_accounts WHERE supplier=?",("beneficiary","iban","s","bank","currency")),
            ("Products","SELECT COALESCE(product_code,'') c, product_name, product_group, vat_rate, discount_terms FROM supplier_products WHERE supplier=?",("c","product_name","product_group","vat_rate","discount_terms")),
            ("Invoices","SELECT country, invoice_no, invoice_date, currency, gross_total FROM supplier_invoices WHERE supplier=?",("country","invoice_no","invoice_date","currency","gross_total"))):
            rows = con.execute(q, (s["code"],)).fetchall()
            if rows:
                body_rows = "".join("<tr>" + "".join(f"<td>{esc(r[col])}</td>" for col in cols) + "</tr>" for r in rows)
                sect += f"<h2 style='margin-top:12px'>{esc(title)}</h2><table><tbody>{body_rows}</tbody></table>"
        cards.append(f'<div class="card"><h2>{esc(s["code"])} — {esc(s["legal_name"])} '
                     f'<span class="{"ok" if s["status"]=="active" else "bad"}">[{esc(s["status"])}]</span></h2>'
                     f"<table><tbody>{meta}</tbody></table>{sect}</div>")
    con.close()
    body = ('<div class="note" style="margin-bottom:10px">Supplier master data lives in '
            '<b>suppliers.db</b> — a separate database from the VAT refund claim database '
            '(fuel_history.db). Transactions and claims reference suppliers by code only.</div>'
            + "".join(cards))
    return page(body, "sup")

@app.route("/customers", methods=["GET", "POST"])
def customers():
    import customer_master as CD
    banner = ""
    if request.method == "POST":
        try:
            act = request.form["__act"]; code = request.form.get("code", "").strip().upper()
            if act == "add_customer":
                CD.add_customer(code, request.form.get("company_name", ""),
                                request.form.get("country", ""), request.form.get("reg_number", ""),
                                request.form.get("vat_number", ""), request.form.get("home_portal", ""))
                msg = f"Customer {esc(code)} created — pending activation."
            elif act == "upload_doc":
                f = request.files.get("file")
                if not f or not f.filename:
                    raise ValueError("no file selected")
                con = CD.connect()
                CD.add_document(con, code, request.form.get("kind", "other"), f.filename, f.read())
                con.close()
                msg = f"Document ({esc(request.form.get('kind',''))}) uploaded for {esc(code)}."
            elif act == "set_fee":
                con = CD.connect()
                CD.set_fee(con, code, request.form.get("fee_pct"), request.form.get("fee_min"))
                con.close()
                msg = f"Default fee for {esc(code)} set to {esc(request.form.get('fee_pct') or '0')}% (min €{esc(request.form.get('fee_min') or '0')})."
            elif act == "set_payout":
                con = CD.connect()
                CD.set_payout_route(con, code, request.form.get("payout_route", "customer")); con.close()
                msg = f"Payout route for {esc(code)} set."
            elif act == "set_country_fee":
                country = request.form.get("fee_country", "").strip()
                if not country:
                    raise ValueError("country is required for a per-country fee")
                con = CD.connect()
                CD.set_country_fee(con, code, country, request.form.get("fee_pct"), request.form.get("fee_min"))
                con.close()
                msg = f"Per-country fee for {esc(code)} / {esc(country)} saved."
            elif act in ("activate", "deactivate"):
                con = CD.connect()
                if act == "activate":
                    _items, ready = CD.activation_checklist(con, code)
                    if not ready:
                        con.close()
                        raise ValueError("cannot activate — required documents / bank account not complete")
                CD.set_activation(con, code, act == "activate"); con.close()
                msg = f"Customer {esc(code)} {'ACTIVATED' if act=='activate' else 'set to pending'}."
            elif act == "request_country":
                country = request.form.get("country2", "").strip()
                if not country:
                    raise ValueError("refund country is required")
                con = CD.connect(); CD.request_country(con, code, country); con.close()
                msg = f"Documents requested for {esc(code)} / {esc(country)}."
            elif act == "upload_country_doc":
                country = request.form.get("country2", "").strip()
                f = request.files.get("file")
                if not country or not f or not f.filename:
                    raise ValueError("country and file are required")
                con = CD.connect()
                CD.add_country_document(con, code, country,
                                        request.form.get("kind", "power_of_attorney"), f.filename, f.read())
                con.close()
                msg = f"Country document received for {esc(code)} / {esc(country)}."
            elif act in ("activate_country", "deactivate_country"):
                country = request.form.get("country2", "").strip()
                con = CD.connect()
                if act == "activate_country":
                    _i, cready = CD.country_doc_checklist(con, code, country)
                    if not cready:
                        con.close()
                        raise ValueError(f"cannot activate {country} — required country documents not received")
                CD.activate_country(con, code, country, act == "activate_country"); con.close()
                msg = f"Refund country {esc(country)} {'activated' if act=='activate_country' else 'set to pending'} for {esc(code)}."
            elif act == "set_country_reqs":
                country = request.form.get("req_country", "").strip()
                if not country:
                    raise ValueError("refund country is required")
                kinds = [k for k in CD.DOC_KINDS if request.form.get(f"req_{k}") == "on"]
                con = CD.connect(); CD.set_country_requirements(con, country, kinds); con.close()
                msg = f"Document requirements for {esc(country)} updated ({len(kinds) or 'default'} required)."
            else:
                raise ValueError("unknown action")
            banner = f'<div class="card"><b class="ok">{msg}</b></div>'
        except Exception as e:
            _log_exc("customer management", e)
            banner = f'<div class="card"><b class="bad">Error: {esc(str(e))}</b></div>'

    con = CD.connect()
    cards = []
    for c in con.execute("SELECT * FROM customers ORDER BY status DESC, code"):
        code = c["code"]
        active = c["status"] == "active"
        items, ready = CD.activation_checklist(con, code)
        status_badge = (f'<span class="ok">● ACTIVE</span>' if active
                        else f'<span class="bad">● PENDING ACTIVATION</span>')
        chk = "".join(f'<div class="row"><span class="{"ok" if ok else "bad"}">'
                      f'{"✓" if ok else "✗"}</span> {esc(lbl)}</div>' for lbl, ok in items)
        def fld(v):
            v2 = esc(str(v)) if v is not None else ""
            return f'<span class="bad">{v2}</span>' if v and "INPUT" in str(v) else v2
        meta = "".join(f"<tr><td style='color:var(--mut);width:160px'>{lbl}</td><td>{fld(c[k])}</td></tr>"
                       for lbl, k in (("Company name","company_name"),("Registration number","reg_number"),
                                      ("VAT number","vat_number"),("Country","country"),
                                      ("Home tax portal","home_portal"),("Notes","notes")) if c[k])
        banks = "".join(f"<tr><td>{fld(r['iban'])}</td><td>{esc(r['bank'])}</td><td>{esc(r['currency'])}</td></tr>"
                        for r in con.execute("SELECT * FROM customer_bank_accounts WHERE customer=?", (code,)))
        docs = "".join(f"<tr><td>{esc(d['kind'])}</td><td>{esc(d['filename'])}</td>"
                       f"<td class='note'>sha {esc((d['sha256'] or '')[:8])}</td></tr>"
                       for d in CD.documents(con, code))
        fee_pct = c["fee_pct"] or 0; fee_min = c["fee_min"] or 0
        # management forms
        opt = lambda v: "".join(f'<option value="{k}" {"selected" if k==v else ""}>{esc(lbl)}</option>'
                                for k, lbl in {**CD.REQUIRED_DOCS, "other": "Other"}.items())
        hid = f'<input type="hidden" name="code" value="{esc(code)}">'
        upload_f = ('<form method="post" enctype="multipart/form-data" class="f" style="margin-top:8px">'
                    + _csrf_input() + hid + '<input type="hidden" name="__act" value="upload_doc">'
                    + f'<label>document<select name="kind">{opt("trade_registry")}</select></label>'
                    + '<label>file<input type="file" name="file" required></label>'
                    + '<button>Upload</button></form>')
        fee_f = ('<form method="post" class="f" style="margin-top:6px">' + _csrf_input() + hid
                 + '<input type="hidden" name="__act" value="set_fee">'
                 + f'<label>default fee %<input name="fee_pct" type="number" step="0.1" value="{fee_pct:g}" style="width:80px"></label>'
                 + f'<label>min € / declaration<input name="fee_min" type="number" step="0.01" value="{fee_min:g}" style="width:110px"></label>'
                 + '<button>Save default fee</button></form>')
        _route = (c["payout_route"] if "payout_route" in c.keys() else None) or "customer"
        payout_f = ('<form method="post" class="f" style="margin-top:6px">' + _csrf_input() + hid
                    + '<input type="hidden" name="__act" value="set_payout">'
                    + '<label>refund paid to<select name="payout_route">'
                    + f'<option value="customer" {"selected" if _route!="us" else ""}>Customer account (we invoice the fee)</option>'
                    + f'<option value="us" {"selected" if _route=="us" else ""}>Our account (we deduct fee, remit net)</option>'
                    + '</select></label><button>Save payout route</button></form>')
        cf_rows = "".join(f"<tr><td>{esc(r['country'])}</td><td class=r>{(r['fee_pct'] or 0):g}%</td>"
                          f"<td class=r>€{(r['fee_min'] or 0):,.2f}</td></tr>"
                          for r in CD.country_fees(con, code))
        cfee_f = ('<form method="post" class="f" style="margin-top:6px">' + _csrf_input() + hid
                  + '<input type="hidden" name="__act" value="set_country_fee">'
                  + '<label>country<input name="fee_country" required style="width:120px" placeholder="Belgium"></label>'
                  + '<label>fee %<input name="fee_pct" type="number" step="0.1" style="width:80px"></label>'
                  + '<label>min €<input name="fee_min" type="number" step="0.01" style="width:100px"></label>'
                  + '<button>Save country fee</button></form>')
        act_btn = (f'<form method="post" style="display:inline">' + _csrf_input() + hid
                   + f'<button name="__act" value="{"deactivate" if active else "activate"}" '
                   + ('' if (active or ready) else 'disabled title="complete the checklist first" ')
                   + f'style="background:{"var(--bad)" if active else "var(--ok)"}">'
                   + f'{"Deactivate" if active else "Activate"}</button></form>')
        # per refund country: request -> receive documents -> activate
        ctry_html = ""
        for cr in CD.country_rows(con, code):
            cc = cr["country"]; cact = cr["status"] == "active"
            citems, cready = CD.country_doc_checklist(con, code, cc)
            cbadge = ('<span class="ok">● ACTIVE</span>' if cact
                      else f'<span class="bad">● {esc((cr["status"] or "pending").upper())}</span>')
            cchk = " · ".join(f'<span class="{"ok" if ok else "bad"}">{"✓" if ok else "✗"} {esc(lbl)}</span>'
                              for lbl, ok in citems)
            chid = hid + f'<input type="hidden" name="country2" value="{esc(cc)}">'
            kopt = "".join(f'<option value="{k}">{esc(lbl)}</option>' for k, lbl in CD.DOC_KINDS.items())
            cup = ('<form method="post" enctype="multipart/form-data" style="display:inline">' + _csrf_input()
                   + chid + '<input type="hidden" name="__act" value="upload_country_doc">'
                   + f'<select name="kind">{kopt}</select>'
                   + '<input type="file" name="file" required style="width:140px"><button>Receive doc</button></form> ')
            cbtn = ('<form method="post" style="display:inline">' + _csrf_input() + chid
                    + f'<button name="__act" value="{"deactivate_country" if cact else "activate_country"}" '
                    + ('' if (cact or cready) else 'disabled title="receive the documents first" ')
                    + f'style="background:{"var(--bad)" if cact else "var(--ok)"}">'
                    + f'{"Deactivate" if cact else "Activate"}</button></form>')
            ctry_html += (f'<div class="row" style="flex-wrap:wrap;gap:8px;align-items:center">'
                          f'<b>{esc(cc)}</b> {cbadge} <span class="note">{cchk}</span> {cup}{cbtn}</div>')
        addc = ('<form method="post" class="f" style="margin-top:6px">' + _csrf_input() + hid
                + '<input type="hidden" name="__act" value="request_country">'
                + '<label>refund country<input name="country2" required style="width:150px" placeholder="Belgium"></label>'
                + '<button>Request documents</button></form>')
        cards.append(
            f'<div class="card"><h2>{esc(code)} — {esc(c["company_name"])} &nbsp; {status_badge}</h2>'
            + f'<table><tbody>{meta}</tbody></table>'
            + (f"<h2 style='margin-top:12px'>Bank accounts</h2><table><tbody>{banks}</tbody></table>" if banks else "")
            + '<h2 style="margin-top:12px">Activation checklist</h2>' + (chk or '<p class="note">—</p>')
            + '<div style="margin-top:8px">' + act_btn + '</div>'
            + '<h2 style="margin-top:12px">Refund countries (activate separately)</h2>'
            + '<div class="note" style="margin-top:0">Request the country documents (power of '
              'attorney), receive them, then activate that country. Claims can only be submitted '
              'for activated countries.</div>'
            + (ctry_html or '<p class="note">no countries requested yet</p>') + addc
            + '<h2 style="margin-top:12px">Documents</h2>'
            + (f'<table><tbody>{docs}</tbody></table>' if docs else '<p class="note">none yet</p>')
            + upload_f
            + f'<h2 style="margin-top:12px">Our fee — default {fee_pct:g}% of refunded VAT, min €{fee_min:,.2f}/declaration</h2>'
            + '<div class="note" style="margin-top:0">Priority is the % fee; if it falls below the '
              'minimum, the minimum is charged. Adjustable per declaration and per country — but '
              '<b>frozen once a claim is submitted</b>. Computed fees show on the Recovery page.</div>'
            + fee_f + payout_f
            + (f'<table style="margin-top:6px"><thead><tr><th>Country override</th><th>Fee %</th>'
               f'<th>Min €</th></tr></thead><tbody>{cf_rows}</tbody></table>' if cf_rows else '')
            + cfee_f + '</div>')
    # global per-country document requirements (which docs each country needs)
    req_rows = "".join(
        f"<tr><td>{esc(cy)}</td><td>{esc(', '.join(CD.DOC_KINDS.get(k, k) for k in ks))}</td></tr>"
        for cy, ks in CD.all_country_requirements(con).items())
    con.close()
    req_checks = "".join(
        f'<label class="chk" style="display:inline-flex;gap:5px;margin:0 14px 4px 0;font-size:13px">'
        f'<input type="checkbox" name="req_{esc(k)}"> {esc(lbl)}</label>' for k, lbl in CD.DOC_KINDS.items())
    req_card = ('<div class="card"><h2>Country document requirements</h2>'
                '<div class="note" style="margin-top:0">Set which documents each refund country '
                'requires for activation — some need only a power of attorney, others several. '
                'Countries with no rule default to a power of attorney.</div>'
                + (f'<table style="margin-top:6px"><thead><tr><th>Country</th>'
                   f'<th>Required documents</th></tr></thead><tbody>{req_rows}</tbody></table>'
                   if req_rows else '')
                + '<form method="post" style="margin-top:8px">' + _csrf_input()
                + '<input type="hidden" name="__act" value="set_country_reqs">'
                + '<label class="f" style="margin-bottom:6px">refund country'
                  '<input name="req_country" required style="width:160px" placeholder="Germany"></label>'
                + '<div style="margin:4px 0">' + req_checks + '</div>'
                + '<button>Save requirements</button></form></div>')
    new_f = ('<div class="card"><h2>Onboard a new VAT-refund customer</h2>'
             '<div class="note" style="margin-top:0">Created as <b>pending</b>; activate once the '
             'trade registry, bank account and signed contract are on file.</div>'
             '<form method="post" class="f">' + _csrf_input()
             + '<input type="hidden" name="__act" value="add_customer">'
             '<label>code<input name="code" required style="width:90px"></label>'
             '<label>company name<input name="company_name" required></label>'
             '<label>country<input name="country" style="width:70px" placeholder="LV"></label>'
             '<label>reg number<input name="reg_number"></label>'
             '<label>VAT number<input name="vat_number"></label>'
             '<button>+ Create customer</button></form></div>')
    body = (banner + new_f + req_card
            + '<div class="note" style="margin-bottom:10px">Customer (entity) master data lives in '
              '<b>customers.db</b>. Each new customer must be <b>activated</b> — requires a trade '
              'registry extract, a bank account, and a signed contract — before claims can be '
              'submitted for them.</div>'
            + "".join(cards))
    return page(body, "cus")


# ---------------------------------------------------------------- data manager + history
import customer_master as _cdb, supplier_master as _sdb
import vat_refund as _vr
import audit as _audit

DATA_DBS = {
    "customers": (_cdb.connect, ["customers","customer_bank_accounts","customer_supplier_accounts"]),
    "suppliers": (_sdb.connect, ["suppliers","supplier_vat_registrations","supplier_bank_accounts",
                                 "supplier_products","supplier_invoices"]),
    "claims":    (_vr.connect,  ["vat_applications","vat_claimed_invoices","invoice_documents"]),
}

def _meta(con, table):
    info = con.execute(f"PRAGMA table_info({table})").fetchall()
    cols = [r[1] for r in info]
    pks = [r[1] for r in info if r[5]] or [cols[0]]
    return cols, pks

@app.route("/data", methods=["GET", "POST"])
def data_manager():
    dbk = request.values.get("db", "customers")
    opener, tables = DATA_DBS[dbk]
    table = request.values.get("table", tables[0])
    if table not in tables: table = tables[0]
    con = opener()
    cols, pks = _meta(con, table)
    banner = ""
    if request.method == "POST":
        act = request.form["__action"]
        try:
            if act == "delete":
                w = " AND ".join(f"{k}=?" for k in pks)
                con.execute(f"DELETE FROM {table} WHERE {w}", [request.form[f"__pk_{k}"] for k in pks])
                banner = '<div class="card"><b class="ok">Row deleted (old values kept in History).</b></div>'
            else:  # save = new or edit
                vals = [request.form.get(f"c_{c}") or None for c in cols]
                con.execute(f"INSERT OR REPLACE INTO {table} ({','.join(cols)}) "
                            f"VALUES ({','.join('?'*len(cols))})", vals)
                banner = '<div class="card"><b class="ok">Saved (change logged in History).</b></div>'
            con.commit()
        except Exception as e:
            _log_exc("data manager", e)
            banner = f'<div class="card"><b class="bad">Error: {esc(str(e))}</b></div>'
    # selectors
    dbsel = "".join(f'<option value="{k}" {"selected" if k==dbk else ""}>{k}</option>' for k in DATA_DBS)
    tsel = "".join(f'<option {"selected" if t==table else ""}>{t}</option>' for t in tables)
    form = (f'<form class="f" method="get"><label>database<select name="db" onchange="this.form.submit()">{dbsel}</select></label>'
            f'<label>table<select name="table" onchange="this.form.submit()">{tsel}</select></label></form>')
    # rows with inline edit
    rows_html = ""
    for r in con.execute(f"SELECT {','.join(cols)} FROM {table} LIMIT 200"):
        inputs = "".join(f'<td><input name="c_{c}" value="{esc("" if v is None else str(v))}" '
                         f'style="width:{max(70,min(200,len(str(v or ""))*8))}px"></td>' for c, v in zip(cols, r))
        hpk = "".join(f'<input type="hidden" name="__pk_{k}" value="{esc(r[cols.index(k)])}">' for k in pks)
        rows_html += (f'<tr><form method="post" action="/data?db={esc(dbk)}&table={esc(table)}">'
                      + _csrf_input() + inputs +
                      f'<td style="white-space:nowrap">{hpk}'
                      f'<button name="__action" value="save">Save</button> '
                      f'<button name="__action" value="delete" style="background:var(--bad)" '
                      f'onclick="return confirm(\'Delete this row? The audit log keeps it.\')">Delete</button>'
                      f'</td></form></tr>')
    new_inputs = "".join(f'<td><input name="c_{c}" placeholder="{c}" style="width:90px"></td>' for c in cols)
    rows_html += (f'<tr><form method="post" action="/data?db={esc(dbk)}&table={esc(table)}">'
                  + _csrf_input() + new_inputs +
                  f'<td><button name="__action" value="save">+ Add new</button></td></form></tr>')
    head_html = "".join(f"<th>{c}</th>" for c in cols) + "<th>actions</th>"
    body = (banner + form + f'<div class="card"><h2>{esc(dbk)}.db / {esc(table)} — write new, edit, delete '
            f'(every change is audit-logged)</h2><table><thead><tr>{head_html}</tr></thead>'
            f'<tbody>{rows_html}</tbody></table>'
            '<div class="note">Primary-key edits create a new row (old one can be deleted); '
            'deletes are reversible from History (old values preserved).</div></div>')
    con.close(); return page(body, "dat")

@app.route("/history")
def history_page():
    dbk = request.args.get("db", "customers")
    opener, tables = DATA_DBS[dbk]
    con = opener()
    table = request.args.get("table", "")
    f_, t_ = request.args.get("from",""), request.args.get("till","")
    key = request.args.get("key","")
    rows = _audit.history(con, table=table or None, key_like=key or None,
                          dt_from=f_ or None, dt_till=t_ or None)
    dbsel = "".join(f'<option value="{k}" {"selected" if k==dbk else ""}>{k}</option>' for k in DATA_DBS)
    tsel = '<option value="">all tables</option>' + "".join(
        f'<option {"selected" if t==table else ""}>{t}</option>' for t in tables)
    form = (f'<form class="f" method="get"><label>database<select name="db" onchange="this.form.submit()">{dbsel}</select></label>'
            f'<label>table<select name="table">{tsel}</select></label>'
            f'<label>from date<input type="date" name="from" value="{esc(f_)}"></label>'
            f'<label>till date<input type="date" name="till" value="{esc(t_)}"></label>'
            f'<label>record key<input name="key" value="{esc(key)}" placeholder="e.g. OMUSS"></label>'
            f'<button>Apply</button></form>')
    trs = []
    for r in rows:
        cls = {"INSERT":"ok","DELETE":"bad"}.get(r["action"], "")
        change = (_audit.diff(r["old_data"], r["new_data"]) if r["action"]=="UPDATE"
                  else (r["new_data"] or r["old_data"] or ""))
        trs.append([f"<td>{esc(r['ts'])}</td><td>{esc(r['tbl'])}</td><td>{esc(r['rowkey'])}</td>",
                    f"<td class='{cls}'>{esc(r['action'])}</td><td>{esc(r['changed_by'])}</td>"
                    f"<td class='note'>{esc(str(change)[:220])}</td>"])
    body = (form + f'<div class="card"><h2>Change history — {dbk}.db ({len(rows)} entries'
            + (f", {f_ or 'start'} → {t_ or 'now'}" if f_ or t_ else "") + ')</h2>'
            + tbl(["Timestamp (UTC)","Table","Record key","Action","By","Change / snapshot"], trs)
            + '<div class="note">BASELINE = state captured at audit installation. DELETE rows keep the '
              'full old record (restore by re-adding via Data manager). As-of reconstruction: '
              'audit.as_of(con, table, timestamp).</div></div>')
    con.close(); return page(body, "his")

@app.route("/admin", methods=["GET", "POST"])
def admin():
    banner = ""
    if request.method == "POST":
        try:
            act = request.form["__act"]
            tgt = request.form.get("username", "").strip()
            scon = _auth.connect(); _audit_mod.set_actor(scon, session["user"]); scon.close()
            if act == "add":
                _auth.add_user(tgt, request.form["password"], request.form.get("role", "processor"))
                banner = f"User <b>{esc(tgt)}</b> created/updated (password scrypt-hashed)."
            elif act == "role":
                if tgt == session["user"]:
                    raise ValueError("you cannot change your own role")
                _auth.set_role(tgt, request.form["role"])
                banner = f"Role of <b>{esc(tgt)}</b> set to {esc(request.form['role'])}."
            elif act == "perms":
                # admin adjusts the processor role's capabilities (checkbox = granted)
                for perm in _auth.PERMISSIONS:
                    _auth.set_permission("processor", perm,
                                         request.form.get(f"perm_{perm}") == "on")
                banner = "Processor permissions updated."
            elif act == "toggle":
                if tgt == session["user"]:
                    raise ValueError("you cannot disable your own account")
                u = _auth.get_user(tgt)
                _auth.set_active(tgt, 0 if u["active"] else 1)
                banner = f"<b>{esc(tgt)}</b> {'disabled' if u['active'] else 'enabled'}."
            elif act == "reset":
                _auth.add_user(tgt, request.form["password"])
                banner = f"Password of <b>{esc(tgt)}</b> reset."
            elif act == "clear_errors":
                _auth.clear_errors()
                banner = "Error log cleared."
            elif act == "run_backup":
                bpath, bn = run_backup_now()
                banner = (f"Backup created: <b>{esc(os.path.basename(bpath))}</b> "
                          f"({bn} files hashed — DBs + documents/ + audit CSVs).")
            elif act == "set_backup_schedule":
                hrs = request.form.get("interval", "0")
                try:
                    hrs_i = int(float(hrs))
                except ValueError:
                    hrs_i = 0
                _auth.set_setting("backup_interval_hours", str(hrs_i))
                banner = ("Automatic backups turned OFF (manual only)." if hrs_i <= 0
                          else f"Automatic backups set to run every {hrs_i} hour(s).")
            elif act == "verify_backup":
                import backup as _bk, glob as _g
                snaps = sorted(_g.glob(os.path.join(WORKDIR, "backups", "ffs_*.zip")))
                if not snaps:
                    raise ValueError("no backup snapshot found — run a backup first")
                bad = _bk.verify(snaps[-1])
                if bad:
                    _auth.log_error("backup integrity", "IntegrityError",
                                    f"{len(bad)} file(s) failed the SHA-256 check in "
                                    f"{os.path.basename(snaps[-1])}", "; ".join(bad[:30]),
                                    session["user"])
                    raise ValueError(f"{len(bad)} file(s) FAILED integrity check — see error log")
                banner = f"Backup <b>{esc(os.path.basename(snaps[-1]))}</b> verified — all hashes match."
            elif act == "verify_docs":
                import vat_refund as _vr
                drows, dsum = _vr.verify_documents()
                bad = [d for d in drows if d["status"] != "OK"]
                if bad:
                    _auth.log_error("document integrity", "IntegrityError",
                                    f"{dsum['corrupt']} corrupt, {dsum['missing']} missing of "
                                    f"{dsum['total']} document(s)",
                                    "; ".join(f"{d['filename']} [{d['status']}] {d['detail']}"
                                              for d in bad[:30]), session["user"])
                    raise ValueError(f"document integrity FAILED — {dsum['corrupt']} corrupt, "
                                     f"{dsum['missing']} missing — see error log")
                banner = (f"All {dsum['total']} stored document(s) verified — "
                          f"PDF/ZIP files intact (SHA-256 match).")
            scon = _auth.connect(); _audit_mod.reset_actor(scon); scon.close()
            banner = f'<div class="card"><b class="ok">{banner}</b></div>'
        except Exception as e:
            _log_exc("admin action", e)
            banner = f'<div class="card"><b class="bad">Error: {esc(str(e))}</b></div>'
    users, logins = _auth.list_users()
    rsel = lambda cur: "".join(f'<option {"selected" if r == cur else ""}>{r}</option>'
                               for r in _auth.ROLES)
    utr = []
    for u in users:
        me = u["username"] == session["user"]
        actions = ("" if me else
            f'<form method="post" style="display:inline">'
            + _csrf_input() +
            f'<input type="hidden" name="username" value="{esc(u["username"])}">'
            f'<select name="role">{rsel(u["role"])}</select> '
            f'<button name="__act" value="role">Set role</button> '
            f'<button name="__act" value="toggle" style="background:var(--bad)">'
            f'{"Disable" if u["active"] else "Enable"}</button></form> '
            f'<form method="post" style="display:inline">'
            + _csrf_input() +
            f'<input type="hidden" name="username" value="{esc(u["username"])}">'
            f'<input type="password" name="password" placeholder="new password" required '
            f'style="width:110px"> <button name="__act" value="reset">Reset pw</button></form>')
        utr.append([f'<td>{esc(u["username"])}{" <b>(you)</b>" if me else ""}</td>'
                    f'<td>{esc(u["role"])}</td>',
                    f'<td class="{ "ok" if u["active"] else "bad"}">'
                    f'{"active" if u["active"] else "DISABLED"}</td>',
                    f'<td>{esc(u["last_login"])}</td><td>{actions}</td>'])
    addf = ('<form method="post" class="f">'
            + _csrf_input() +
            '<label>username<input name="username" required></label>'
            '<label>password<input type="password" name="password" required></label>'
            f'<label>role<select name="role">{rsel("processor")}</select></label>'
            '<button name="__act" value="add">+ Create user</button></form>'
            '<div class="note">Passwords are stored as salted scrypt hashes - never in '
            'plain text. <b>Admin</b> = full control incl. server setup &amp; this panel; '
            '<b>Processor</b> = day-to-day operations with the capabilities granted below '
            '(never server setup or user administration). All changes are audit-logged.</div>')
    # Processor capability editor (admin-configurable).
    cur = _auth.get_role_permissions("processor")
    pchecks = "".join(
        f'<label class="chk" style="display:flex;gap:7px;align-items:center;font-size:13px;'
        f'flex-direction:row;color:var(--ink);margin:3px 0">'
        f'<input type="checkbox" name="perm_{esc(k)}" {"checked" if cur.get(k) else ""}> '
        f'<b>{esc(k)}</b> — {esc(desc)}</label>'
        for k, desc in _auth.PERMISSIONS.items())
    permf = ('<div class="card"><h2>Processor permissions</h2>'
             '<div class="note" style="margin-top:0">Tick what the <b>Processor</b> role may do. '
             'Server setup and user / permission administration are reserved for Admin and '
             'cannot be granted here.</div>'
             '<form method="post" style="margin-top:8px">'
             + _csrf_input() + pchecks
             + '<div style="margin-top:10px"><button name="__act" value="perms">Save processor permissions</button></div>'
             + '</form></div>')
    ltr = [[f'<td>{esc(l["ts"])}</td><td>{esc(l["username"])}</td>',
            f'<td class="{ "ok" if l["success"] else "bad"}">'
            f'{"OK" if l["success"] else "FAILED"}</td><td>{esc(l["remote"] or "")}</td>']
           for l in logins]
    # error log
    errs = _auth.recent_errors(200)
    etr = []
    for er in errs:
        msg = er.get("message") or ""
        if er.get("detail"):
            cell = (f'<details><summary>{esc(msg[:140]) or "(no message)"}</summary>'
                    f'<pre style="white-space:pre-wrap;font-size:11px;color:var(--mut);'
                    f'margin:6px 0 0">{esc(er["detail"])}</pre></details>')
        else:
            cell = esc(msg)
        etr.append([f'<td class="note">{esc(er["ts"])}</td><td>{esc(er["username"] or "")}</td>',
                    f'<td>{esc(er["context"] or "")}</td><td class="bad">{esc(er["etype"] or "")}</td>',
                    f'<td>{cell}</td>'])
    clearf = ('<form method="post" style="display:inline">' + _csrf_input()
              + '<button name="__act" value="clear_errors" style="background:var(--bad)">'
                'Clear error log</button></form>')
    errcard = ('<div class="card"><h2>Error log</h2>'
               + (tbl(["Time (UTC)", "User", "Context", "Type", "Message / traceback"], etr)
                  if errs else '<p class="note">No errors logged.</p>')
               + (f'<div style="margin-top:10px">{clearf}</div>' if errs else '')
               + '<div class="note">Unhandled exceptions and failed operations (imports, ECB '
                 'scrape/upload, statement register, data edits, admin actions) are recorded here '
                 'with a traceback. Newest first; last 200 shown.</div></div>')
    import os as _os, glob as _glob, datetime as _dt
    tls = _os.path.exists(_os.path.join(WORKDIR, "cert.pem"))
    # backups & data-integrity status
    snaps = sorted(_glob.glob(_os.path.join(WORKDIR, "backups", "ffs_*.zip")))
    if snaps:
        _when = _dt.datetime.fromtimestamp(_os.path.getmtime(snaps[-1])).strftime("%Y-%m-%d %H:%M")
        last_bk = f'<span class="ok">{esc(_os.path.basename(snaps[-1]))}</span> ({esc(_when)}, {len(snaps)} kept)'
    else:
        last_bk = '<span class="bad">none yet — run a backup</span>'
    dbstat = []
    for _db in ("customers.db", "suppliers.db", "fuel_history.db", "security.db"):
        _p = _os.path.join(WORKDIR, _db)
        if not _os.path.exists(_p):
            continue
        try:
            _cx = sqlite3.connect(_p); _ok = _cx.execute("PRAGMA quick_check").fetchone()[0]; _cx.close()
            dbstat.append(f'{esc(_db)} <span class="{"ok" if _ok=="ok" else "bad"}">{esc(_ok)}</span>')
        except Exception as _e:
            dbstat.append(f'{esc(_db)} <span class="bad">{esc(str(_e)[:30])}</span>')
    try:
        import vat_refund as _vr2
        _vc = _vr2.connect(); ndocs = _vc.execute("SELECT COUNT(*) FROM invoice_documents").fetchone()[0]; _vc.close()
    except Exception:
        ndocs = 0
    _bkbtn = lambda a, lab, st="": (f'<form method="post" style="display:inline">{_csrf_input()}'
                                    f'<button name="__act" value="{a}" style="{st}">{lab}</button></form> ')
    # auto-backup schedule
    _cur_hrs = int(backup_interval_hours())
    _opts = [(0, "Off (manual only)"), (6, "Every 6 hours"), (12, "Every 12 hours"),
             (24, "Daily (24h)"), (48, "Every 2 days"), (168, "Weekly")]
    _osel = "".join(f'<option value="{v}" {"selected" if v==_cur_hrs else ""}>{esc(lab)}</option>'
                    for v, lab in _opts)
    _sched_txt = ("manual only" if _cur_hrs <= 0 else f"every {_cur_hrs}h")
    schedule_form = ('<form method="post" class="f" style="margin:6px 0">' + _csrf_input()
                     + f'<label>Automatic backup<select name="interval">{_osel}</select></label>'
                     + '<button name="__act" value="set_backup_schedule">Save schedule</button></form>')
    backupcard = ('<div class="card"><h2>Backups &amp; data integrity</h2>'
                  f'<p>Last snapshot: {last_bk} &nbsp;·&nbsp; Schedule: <b>{esc(_sched_txt)}</b>'
                  f'<br>DB integrity (quick_check): {" &nbsp; ".join(dbstat) or "—"}'
                  f'<br>{ndocs} document file(s) tracked.</p>'
                  + schedule_form
                  + f'<div style="margin:8px 0">{_bkbtn("run_backup","↓ Run backup now")}'
                  f'{_bkbtn("verify_backup","✓ Verify last backup")}'
                  f'{_bkbtn("verify_docs","✓ Check document integrity")}</div>'
                  '<div class="note">Backups run <b>automatically</b> on the schedule above (a '
                  'background task snapshots when one is due) and can also be taken on demand. Each '
                  'snapshot bundles the databases (crash-consistent copies), the physical '
                  '<b>documents/</b> store (PDF/ZIP originals) and write-once audit CSVs, every file '
                  'carrying a SHA-256 hash so corruption is detectable; the last 14 are kept. Sync the '
                  '<b>backups/</b> folder to off-machine storage (OneDrive/SharePoint) so files survive '
                  'disk loss. "Check document integrity" re-hashes every stored PDF/ZIP against the hash '
                  'recorded at upload — any mismatch or missing file is written to the error log '
                  'above.</div></div>')
    body = (banner
            + '<div class="card"><h2>Users &amp; permissions</h2>'
            + tbl(["Username", "Role", "Status", "Last login", "Actions"], utr)
            + addf + "</div>"
            + permf
            + f'<div class="card"><h2>Security status</h2>'
              f'<p>TLS certificate: '
              f'{"<span class=ok>cert.pem present - app serves HTTPS</span>" if tls else "<span class=bad>none - run python3 make_cert.py (self-signed) or install a CA cert</span>"}'
              f' &nbsp;|&nbsp; Password storage: <span class="ok">scrypt (salted)</span>'
              f' &nbsp;|&nbsp; Session cookies: HttpOnly, SameSite'
              f'{", Secure (HTTPS)" if tls else ""}</p></div>'
            + backupcard
            + '<div class="card"><h2>Recent logins</h2>'
            + tbl(["Timestamp (UTC)", "Username", "Result", "From"], ltr) + "</div>"
            + errcard)
    return page(body, "adm")

@app.route("/doc/<int:doc_id>")
def doc_download(doc_id):
    import vat_refund as VR, document_vault, io
    con = VR.connect()
    d = con.execute("SELECT * FROM invoice_documents WHERE id=?", (doc_id,)).fetchone()
    con.close()
    data = document_vault.get_bytes(d["stored_path"], VR.DOCDIR)
    return send_file(io.BytesIO(data), as_attachment=True, download_name=d["filename"])

@app.route("/export/vat")
def export_vat():
    import vat_refund as VR
    con = VR.connect()
    path, _ = VR.build_workbook(con, request.args.get("year", "2026"))
    con.close(); return send_file(path, as_attachment=True)

@app.route("/api/vat")
def api_vat():
    import vat_refund as VR
    con = VR.connect()
    out = VR.claim_matrix(con, request.args.get("year", "2026"))
    con.close(); return jsonify(out)

if __name__ == "__main__":
    import tls
    start_backup_scheduler()
    start_intake_worker()      # drain the document waiting room in the background
    _ctx, _desc = tls.build_context()
    if _ctx:
        app.config.update(SESSION_COOKIE_SECURE=True)
        print(f" * TLS enabled -> https://127.0.0.1:8050\n * Certificate: {_desc}")
        app.run(host="127.0.0.1", port=8050, debug=False, ssl_context=_ctx)
    else:
        print(f" * HTTP on localhost only ({_desc})")
        app.run(host="127.0.0.1", port=8050, debug=False)
