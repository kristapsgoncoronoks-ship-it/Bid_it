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
import tenancy as _tenancy
import dataproduct
import applog
_log = applog.get("app")

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

  // First-run setup & login: make the password fields friendly — a show/hide
  // toggle and a live "is it long enough / do they match" hint. Runs only on the
  // pages that opt in with [data-setup]; no-op everywhere else.
  (function(){
    var f=document.querySelector('form[data-setup]'); if(!f) return;
    f.querySelectorAll('.pwtoggle').forEach(function(t){
      var inp=document.getElementById(t.getAttribute('data-for')); if(!inp) return;
      t.addEventListener('click',function(){
        var hidden=inp.type==='password';
        inp.type=hidden?'text':'password'; t.textContent=hidden?'hide':'show';
      });
    });
    var p=document.getElementById('pw'), p2=document.getElementById('pw2'),
        msg=document.getElementById('pwmsg');
    function check(){
      if(!msg||!p) return;
      var v=p.value, v2=p2?p2.value:'';
      if(!v){ msg.textContent=''; msg.className='pwmsg'; return; }
      if(v.length<8){ msg.textContent='A little longer — '+v.length+'/8 characters';
                      msg.className='pwmsg bad'; return; }
      if(p2&&v2&&v!==v2){ msg.textContent='Passwords don’t match yet';
                          msg.className='pwmsg bad'; return; }
      if(p2&&!v2){ msg.textContent='Looks good — now repeat it below';
                   msg.className='pwmsg ok'; return; }
      msg.textContent='Looks good ✓'; msg.className='pwmsg ok';
    }
    if(p) p.addEventListener('input',check);
    if(p2) p2.addEventListener('input',check);
  })();

  // Drag-and-drop for file uploads. Every <input type=file> is wrapped in a friendly
  // drop zone: click to browse, or drag a file onto it. Progressive enhancement — the
  // plain input still works if this never runs. Dropped files are placed back on the
  // input via a DataTransfer, so the existing multipart form POST is unchanged.
  function bytes(n){
    if(n<1024) return n+' B';
    if(n<1048576) return (n/1024).toFixed(0)+' KB';
    return (n/1048576).toFixed(1)+' MB';
  }
  document.querySelectorAll('input[type=file]').forEach(function(inp){
    if(inp.dataset.dz) return; inp.dataset.dz='1';
    var multi=inp.multiple, accept=(inp.getAttribute('accept')||'').trim();
    var inLabel=!!(inp.closest&&inp.closest('label'));
    var zone=document.createElement('div'); zone.className='dropzone';
    var icon=document.createElement('div'); icon.className='dzicon'; icon.textContent='↑';
    var main=document.createElement('div');
    main.innerHTML='<b class="dzlink">Choose a file</b> or drag it here';
    var name=document.createElement('div'); name.className='dzname';
    zone.appendChild(icon); zone.appendChild(main);
    if(accept){ var h=document.createElement('div'); h.className='dzhint';
                h.textContent='Accepts '+accept; zone.appendChild(h); }
    zone.appendChild(name);
    inp.parentNode.insertBefore(zone, inp); zone.appendChild(inp);  // input kept (hidden) for submit
    function show(){
      var fs=inp.files;
      if(fs&&fs.length){
        name.textContent=fs.length>1 ? (fs.length+' files selected')
                                      : (fs[0].name+'  ('+bytes(fs[0].size)+')');
        zone.classList.add('has');
      } else { name.textContent=''; zone.classList.remove('has'); }
    }
    // If the input sits inside a <label>, a native click already opens the dialog —
    // don't add our own (it would open it twice). Otherwise make the zone clickable.
    if(!inLabel) zone.addEventListener('click',function(e){ if(e.target!==inp) inp.click(); });
    inp.addEventListener('change',show);
    ['dragenter','dragover'].forEach(function(ev){
      zone.addEventListener(ev,function(e){e.preventDefault();e.stopPropagation();zone.classList.add('drag');});
    });
    ['dragleave','dragend'].forEach(function(ev){
      zone.addEventListener(ev,function(e){e.preventDefault();e.stopPropagation();zone.classList.remove('drag');});
    });
    zone.addEventListener('drop',function(e){
      e.preventDefault(); e.stopPropagation(); zone.classList.remove('drag');
      var dropped=e.dataTransfer&&e.dataTransfer.files; if(!dropped||!dropped.length) return;
      try{
        var dt=new DataTransfer(), lim=multi?dropped.length:1;
        for(var i=0;i<lim;i++) dt.items.add(dropped[i]);
        inp.files=dt.files;
        inp.dispatchEvent(new Event('change',{bubbles:true}));
      }catch(err){ /* very old browser without DataTransfer: keep click-to-pick */ }
      show();
    });
  });

  // /documents?ref=… : scroll the matching invoice row into view and highlight it,
  // so a "Resolve" link from /vat lands the operator on the right invoice. Opt-in via
  // a [data-doc-focus] marker carrying the ref; the row cell carries [data-doc-ref].
  (function(){
    var marker=document.querySelector('[data-doc-focus]');
    if(!marker) return;
    var ref=(marker.getAttribute('data-doc-focus')||'').trim(); if(!ref) return;
    var cells=document.querySelectorAll('[data-doc-ref]');
    Array.prototype.forEach.call(cells,function(td){
      if((td.getAttribute('data-doc-ref')||'')!==ref) return;
      var row=td.closest('tr'); if(!row) return;
      row.style.outline='2px solid var(--accent, #0e5fa8)';
      try{ row.scrollIntoView({block:'center'}); }catch(e){ row.scrollIntoView(); }
    });
  })();

  // Data manager (claims DB): accidental-edit guard. Rows render read-only with an
  // explicit Edit toggle that clears readonly and reveals Save; the row form carries
  // [data-confirm] so a deliberate confirm is required before the (unchanged) POST.
  document.addEventListener('click',function(e){
    var b=e.target&&e.target.closest&&e.target.closest('[data-edit-row]'); if(!b) return;
    var form=b.closest('form'); if(!form) return;
    form.querySelectorAll('input[readonly]').forEach(function(inp){ inp.removeAttribute('readonly'); });
    var save=form.querySelector('[data-save-row]'); if(save) save.style.display='';
    b.style.display='none';
    var first=form.querySelector('input:not([type=hidden])'); if(first) first.focus();
  });
  document.addEventListener('submit',function(e){
    var form=e.target; if(!form||!form.hasAttribute('data-confirm')) return;
    if(!window.confirm(form.getAttribute('data-confirm'))) e.preventDefault();
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
<style>body{font:14px/1.5 -apple-system,BlinkMacSystemFont,Segoe UI,Roboto,Arial;background:#eef2f5;color:#1a2733;display:flex;align-items:center;justify-content:center;height:100vh;margin:0;-webkit-font-smoothing:antialiased}
.box{background:#fff;border:1px solid #dde4ea;border-radius:12px;padding:28px 30px;width:300px;box-shadow:0 1px 3px rgba(26,39,51,.06),0 6px 24px rgba(26,39,51,.08)}
h1{font-size:17px;margin:0 0 16px;letter-spacing:-.01em}
input{width:100%;box-sizing:border-box;padding:9px 11px;margin:5px 0 12px;border:1px solid #dde4ea;border-radius:7px;font-size:14px;transition:border-color .12s,box-shadow .12s}
input:focus{outline:none;border-color:#0e5fa8;box-shadow:0 0 0 3px rgba(14,95,168,.12)}
button{width:100%;background:#0e5fa8;color:#fff;border:0;border-radius:7px;padding:10px;font-size:14px;font-weight:600;cursor:pointer;transition:background .12s}
button:hover{background:#0b4d89}
.err{color:#c8102e;font-size:13px;margin-bottom:8px}
.pwwrap{position:relative}.pwwrap input{padding-right:54px}
.pwtoggle{position:absolute;right:11px;top:15px;font-size:12px;color:#0e5fa8;cursor:pointer;user-select:none;font-weight:500}</style></head><body>
<div class="box"><h1>Fleet Fuel Analytics</h1>{ERR}
<form method="post" data-setup><input name="username" placeholder="username" autofocus required>
<div class="pwwrap"><input type="password" name="password" id="pw" placeholder="password" required>
<span class="pwtoggle" data-for="pw">show</span></div>
<button>Sign in</button></form></div><script src="/app.js" defer></script></body></html>"""

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
.pwwrap{position:relative}.pwwrap input{padding-right:58px}
.pwtoggle{position:absolute;right:11px;top:50%;transform:translateY(-50%);font-size:12.5px;
 color:var(--blue);cursor:pointer;user-select:none;font-weight:500}
.pwmsg{font-size:12.5px;margin-top:6px;min-height:16px}
.pwmsg.ok{color:var(--ok)}.pwmsg.bad{color:var(--bad)}
.done .big{font-size:40px;color:var(--ok);text-align:center}
.row{display:flex;align-items:center;gap:10px;padding:9px 0;border-top:1px solid var(--bd);font-size:14px}
.row .ic{color:var(--ok);font-weight:700}
.muted{color:var(--mut)}
a.btn{display:block;text-align:center;background:var(--blue);color:#fff;text-decoration:none;
 border-radius:8px;padding:12px;font-weight:600;margin-top:22px}
</style></head><body><div class="wrap"><div class="card">{BODY}</div>
<p style="text-align:center;color:#9fb3c4;font-size:12px;margin-top:14px">Fleet Fuel &amp; VAT Refund System</p>
</div><script src="/app.js" defer></script></body></html>"""

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
            except Exception as e:
                _log.warning("setup: first backup failed (non-blocking): %s", e)
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
        + '<form method="post" data-setup>'
        '<label>Administrator username</label>'
        f'<input name="username" value="{esc(request.form.get("username","")) if request.method=="POST" else ""}" autofocus required>'
        '<label>Password</label>'
        '<div class="pwwrap"><input type="password" name="password" id="pw" required>'
        '<span class="pwtoggle" data-for="pw">show</span></div>'
        '<div class="hint">At least 8 characters. Stored as a salted hash — never in plain text.</div>'
        '<div id="pwmsg" class="pwmsg"></div>'
        '<label>Repeat password</label>'
        '<div class="pwwrap"><input type="password" name="password2" id="pw2" required>'
        '<span class="pwtoggle" data-for="pw2">show</span></div>'
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
    "extract_ai_review": "data_import",
    "data_manager":    "data_import",
    "intake_queue_page": "data_import", "intake_review": "data_import",
    "doc_mining_page": "data_import", "imports": "data_import", "files_archive": "data_import",
    "invoice_ctrl":    "invoice_control", "contracts": "invoice_control",
    "vat":             "vat_claims", "api_vat": "vat_claims", "readiness": "vat_claims",
    "receivables":     "vat_claims", "export_receivables": "exports",
    "recon":           "vat_claims",
    "customers":       "customers", "cust_doc_download": "customers",
    "pricing":         "pricing", "pricing_upload": "pricing", "api_pricing": "pricing",
    "pricing_market":  "pricing", "pricing_portal": "pricing",
    "pricing_adopt_benchmark": "pricing", "export_benchmark": "exports",
    "export_peer":     "exports",
    "intel":           "pricing", "export_intel": "exports",
    "export_overpay":  "exports",
    "export_expenses": "exports",
    "export_accounting": "exports",
    "export_saft": "exports",
    "documents":       "documents", "doc_download": "documents",
    "export_master":   "exports", "export_history": "exports",
    "export_pricing":  "exports", "export_vat": "exports", "export_compare": "exports",
    "export_stations": "exports", "export_summary": "exports", "export_fee": "exports",
    "export_readiness": "exports", "export_fees": "exports",
    "export_evidence": "exports",
    "admin":           "user_admin",   # server setup / overall software changes
    "admin_confidence": "user_admin",  # confidence-learning scoreboard (read-only)
    "admin_tenants":   "user_admin",   # multi-tenancy registry (read-only, P0)
}

# The VAT-refund module (claims, readiness, recovery/fees + their exports/API) is
# restricted to admins regardless of any processor capability.
ADMIN_ONLY = {"vat", "vat_unmatched", "api_vat", "readiness", "recovery", "receivables",
              "recon",
              "export_vat", "export_readiness", "export_fees", "export_fee",
              "export_receivables", "export_evidence",
              # the one-click monthly close is an engine-orchestration action (it
              # ENQUEUES engine_close.close onto the worker), so it is admin-only.
              "monthly_close",
              # customer/CRM data (checklist, templates, document generation) is part of
              # the VAT-refund module, so the same admin-only access applies.
              "customers", "cust_doc_download",
              # the confidence-learning scoreboard is a read-only admin surface.
              "admin_confidence",
              # the multi-tenancy registry is a read-only admin surface (P0).
              "admin_tenants"}

# Switchable PARTS of the app. An admin turns these on/off in the Admin panel; a
# disabled part is hidden from the menu and its pages return "turned off". Core pages
# (dashboard, entities, customers, suppliers, admin) are always on.
MODULES = {
    "analytics":  ("Analytics — savings, compare, stations, anomalies, pricing",
                   {"savings", "compare", "transactions", "h2h", "stations", "anomalies_page",
                    "pricing", "pricing_market", "pricing_portal", "pricing_adopt_benchmark",
                    "pricing_upload", "api_pricing", "export_compare", "export_stations",
                    "export_pricing", "export_benchmark", "export_peer", "intel", "export_intel",
                    "export_overpay", "expenses", "export_expenses", "export_accounting",
                    "export_saft"}),
    "intake":     ("Intake — import, waiting room, files, document mining",
                   {"extract_batch", "extract_confirm", "extract_ai_review",
                    "intake_queue_page", "intake_review",
                    "imports", "files_archive", "doc_mining_page", "data_manager"}),
    "compliance": ("Compliance — invoice control, contract audit, documents",
                   {"invoice_ctrl", "contracts", "documents", "doc_download"}),
    "vat":        ("VAT refunds — claims, readiness, recovery & fees (admin only)",
                   {"vat", "api_vat", "readiness", "recovery", "receivables", "recon",
                    "export_vat", "export_readiness", "export_fees", "export_fee",
                    "export_receivables", "export_evidence"}),
    "fx":         ("FX vs ECB exchange rates", {"fx"}),
}
_ENDPOINT_MODULE = {ep: k for k, (_lbl, eps) in MODULES.items() for ep in eps}

def module_enabled(key):
    return _auth.get_setting(f"module_{key}", "on") != "off"

def enabled_modules():
    return {k for k in MODULES if module_enabled(k)}

# ---------------------------------------------------------------- /api/v1 token auth
# The versioned external API is TOKEN-ONLY (a clean machine contract) — never
# session/cookie. Each endpoint requires one capability SCOPE; a key holding it
# may call it. This map is the single source of truth for "endpoint -> scope".
API_V1_SCOPE = {
    "api_v1_benchmark": "api:benchmark",
    "api_v1_claim_status": "api:claims",
    "api_v1_savings": "api:savings",
    # Basic CRM-sync surface (read + write to customer master). The write endpoints
    # require the separate api:crm.write scope; a read-only api:crm key cannot reach
    # them (the guard checks scope regardless of HTTP method).
    "api_v1_customers_list": "api:crm",
    "api_v1_customer_get": "api:crm",
    "api_v1_customer_create": "api:crm.write",
    "api_v1_customer_update": "api:crm.write",
}

def _bearer_token():
    """Read the API token from Authorization: Bearer <t> or X-API-Key. Returns the
    raw token string or ''. Never logs the token."""
    h = request.headers.get("Authorization", "")
    if h[:7].lower() == "bearer ":
        return h[7:].strip()
    return (request.headers.get("X-API-Key", "") or "").strip()

def _api_err(status, message):
    """Uniform JSON error for the v1 API (no HTML, no session)."""
    return jsonify({"error": message}), status

@app.before_request
def _api_v1_guard():
    """Token-auth gate for /api/v1/* ONLY. Runs BEFORE the session _guard (registered
    first) and fully owns these endpoints — it returns a response on every path so
    _guard never touches them. It NEVER affects any other route: a non-/api/v1
    endpoint falls straight through (returns None) to the unchanged session _guard.

    Posture: missing/invalid/revoked token -> 401; valid token lacking the endpoint's
    scope -> 403. Default-off: with no keys issued every call is 401."""
    ep = request.endpoint
    if ep not in API_V1_SCOPE:
        return  # not a v1 endpoint — leave session auth fully intact
    import api_keys
    need = API_V1_SCOPE[ep]
    try:
        key = api_keys.verify(_bearer_token())
    except Exception as e:
        _log_exc("api_v1 token verify", e)
        return _api_err(500, "internal error")
    if key is None:
        # missing / invalid / revoked / unknown all collapse to 401 (no oracle)
        api_keys.log_usage(None, ep, 401)
        return _api_err(401, "invalid or missing API key")
    if not api_keys.has_scope(key, need):
        api_keys.log_usage(key["id"], ep, 403)
        return _api_err(403, f"key not authorized for scope {need}")
    # authorized — stash the key so the after-hook can meter the final status, and
    # let the view run.
    request.environ["ffs_api_key_id"] = key["id"]
    return

@app.after_request
def _api_v1_meter(resp):
    """Meter an AUTHORIZED v1 call with its final status. Unauthorized calls were
    already metered (with their 401/403) in the guard."""
    kid = request.environ.get("ffs_api_key_id")
    if kid is not None and request.endpoint in API_V1_SCOPE:
        try:
            import api_keys
            api_keys.log_usage(kid, request.endpoint, resp.status_code)
        except Exception as e:
            _log_exc("api_v1 metering", e)
    return resp

@app.before_request
def _guard():
    if request.endpoint in API_V1_SCOPE:
        return  # /api/v1 is fully owned by _api_v1_guard (token-only)
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
    # A session that has never rendered a form has no established token; an empty
    # session token must FAIL the check (compare_digest("","") is True), otherwise
    # a tokenless cross-site POST would slip through.
    if request.method == "POST" and request.endpoint not in ("login", "setup"):
        sess_tok = session.get("_csrf") or ""
        if not sess_tok or not secrets.compare_digest(
                request.form.get("_csrf") or "", sess_tok):
            return page('<div class="card"><h2>Invalid or missing CSRF token</h2>'
                        '<p>Please reload the page and try again.</p></div>', ""), 400
    # VAT-refund module is admin-only, whatever capabilities a processor may hold.
    if request.endpoint in ADMIN_ONLY and role != "admin":
        return page(FORBIDDEN, ""), 403
    # A switched-off part is unavailable to everyone (an admin re-enables it in /admin).
    mod = _ENDPOINT_MODULE.get(request.endpoint)
    if mod and not module_enabled(mod):
        return page('<div class="card"><h2>This part is turned off</h2>'
                    f'<p>An administrator has switched off the <b>{esc(MODULES[mod][0])}</b> '
                    'part. It can be turned back on in the Admin panel → Modules.</p></div>', ""), 403
    # Capability enforcement: block any endpoint whose required permission the
    # current role lacks (covers both the page view and its POST action).
    req_perm = PERM_BY_ENDPOINT.get(request.endpoint)
    if req_perm and not _auth.has_perm(role, req_perm):
        return page(FORBIDDEN, ""), 403
    if request.method == "POST":
        # Actor is thread-local (audit triggers read it via ffs_actor()), so a
        # single set covers every connection this request opens — no per-DB churn.
        _audit_mod.set_actor(None, session["user"])
    # Multi-tenancy (P0 foundation) — bind the tenant context for this request,
    # but ONLY when the `multitenant` switch is ON. While OFF (the default
    # single-tenant install) this branch is never entered, so the tenant context
    # is never set and every tenancy enforcement helper stays inert: ZERO change
    # to any existing query/route/figure. See docs/MULTI_TENANCY.md.
    if _tenancy.multitenant_enabled():
        # Resolve the principal: the platform OPERATOR (owner) gets the audited
        # cross-tenant READ scope; a client-tenant user is bound to its tenant.
        # Mutually exclusive — owner XOR tenant. OFF path (above) never runs this.
        if _is_owner_principal():
            _tenancy.set_owner_scope()
        else:
            _tenancy.set_tenant(_resolve_tenant())

def _is_owner_principal():
    """Is the logged-in principal the platform OPERATOR/owner (vs a client-tenant
    user)? This is the documented OWNER-resolution seam (the real onboarding/role
    model is P4); for now it is minimal: a user is the owner iff their username is
    listed in the `owner_users` app setting (comma/space separated). Empty/unset =
    no owner principal, so every user is treated as a client-tenant user (safe
    default). Only ever CALLED when multitenant is ON; never raises."""
    try:
        user = (session.get("user") or "").strip()
        if not user:
            return False
        raw = (_auth.get_setting("owner_users", "") or "")
        owners = {p.strip() for p in raw.replace(",", " ").split() if p.strip()}
        return user in owners
    except Exception as e:
        _log.debug("_is_owner_principal failed, treating as non-owner: %s", e)
        return False

def _resolve_tenant():
    """Resolve the tenant for the current request. This is the documented
    resolver seam for P4 (subdomain/session tenant resolution); for P0 it reads
    an explicit session tenant if present and otherwise falls back to the single
    bootstrap tenant. Only ever CALLED when multitenant is ON, so it has no effect
    on the default single-tenant deployment."""
    return session.get("tenant_id") or "default"

@app.after_request
def _reset_actor(resp):
    _audit_mod.reset_actor()
    _tenancy.reset_tenant()   # inert no-op when nothing was bound (multitenant OFF)
    return resp

def _log_exc(context, e):
    """Record a handled exception to the admin error log (keeps the user-facing
    banner unchanged). Safe to call OUTSIDE a request context (e.g. from the
    leader-elected scheduler loop) — the actor falls back to "system" when there is
    no active session rather than raising a 'working outside of request context'."""
    import traceback
    try:
        actor = session.get("user", "")
    except RuntimeError:
        actor = "system"
    _auth.log_error(context, type(e).__name__, str(e),
                    traceback.format_exc(), actor)

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
    # fuel_history.db is OWNED and written by the data-processing engine (history.py).
    # The app only READS it — open a read-only handle via the dataproduct accessor so
    # any stray write from a web route surfaces as an error instead of corrupting a
    # DB the app does not own. queries.py SELECTs work unchanged on a read-only handle.
    return dataproduct.connect("fuel_history")

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
_CLOSE_LOCK = "close-run"   # must match engine_close.LOCK_NAME

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
            # Defer to an in-progress monthly close: engine_close holds _CLOSE_LOCK
            # while it WRITES the product DBs (consolidate→build_master→history→…),
            # and backup.snapshot() copies several DBs + the document store under ONE
            # MANIFEST. Snapshotting mid-close could capture DB-A post-write and DB-B
            # pre-write — a torn cross-DB snapshot. Check held_by() (a non-acquiring
            # peek) in the narrowest window, immediately before snapshot(), and bail.
            # The close's OWN final backup.snapshot() calls backup directly (not this
            # helper), so this guard never deadlocks the close. RESIDUAL: the reverse
            # window — a close STARTING during an already-in-flight backup — is NOT
            # closed by this advisory check and is accepted: each per-DB sqlite
            # .backup() is internally consistent and the NEXT scheduled backup is clean.
            # Full two-phase exclusion (the close yielding to a backup) is out of scope.
            if process_lock.held_by(_CLOSE_LOCK):
                raise RuntimeError("a monthly close is in progress — backup deferred "
                                   "to avoid a torn cross-DB snapshot")
            return backup.snapshot()
        finally:
            process_lock.release("backup-run", me)

def _backup_tick():
    """One scheduler iteration: snapshot if a scheduled backup is due. Returns the
    snapshot path if one was taken, else None. Never raises (logs instead)."""
    import backup, process_lock, traceback
    try:
        # A scheduled backup deferred because a monthly close is mid-write is NORMAL,
        # not an error — skip silently (do NOT log_error) and let the next due tick
        # snapshot once the close releases _CLOSE_LOCK. (run_backup_now() also guards
        # this, but there it RAISES so the manual /admin path shows a clear banner;
        # here we want a quiet no-op rather than a logged "auto-backup" failure.)
        if process_lock.held_by(_CLOSE_LOCK):
            return None
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

# ---------------------------------------------------------------- notify scheduler
# Sends the action-digest e-mail (notify.send_digest) on an admin-set cadence. Like
# the backup scheduler it self-elects ONE leader across all worker processes
# (process_lock), so exactly one digest goes out per interval no matter how many
# processes run. notify_interval_hours = 0 turns it off; the last send is tracked in
# a setting so a restart doesn't re-send immediately. Started only by the server
# entrypoints (never on import), so tests/CLI never auto-send.
_notify_started = False

def notify_interval_hours():
    try:
        return float(_auth.get_setting("notify_interval_hours", "0") or 0)
    except (TypeError, ValueError):
        return 0.0

def _notify_due(hrs):
    """True if at least `hrs` hours have passed since the last recorded digest send
    (or none has ever been recorded). hrs<=0 means the scheduler is off."""
    if hrs <= 0:
        return False
    last = _auth.get_setting("notify_last_sent", "") or ""
    if not last:
        return True
    try:
        import datetime as _dt
        prev = _dt.datetime.fromisoformat(last)
        return (_dt.datetime.utcnow() - prev).total_seconds() >= hrs * 3600
    except (TypeError, ValueError):
        return True

def scrape_scheduler_on():
    """The GLOBAL kill-switch for off-by-default scheduled portal pulls. Returns True
    ONLY when an admin has explicitly armed it (`scrape_scheduler_enabled` == "1").
    Default install: "0" (OFF) -> the scheduler is inert no matter what intervals are
    set. This is the master gate on an unattended outbound-network action."""
    return (_auth.get_setting("scrape_scheduler_enabled", "0") or "0") == "1"

def _scrape_tick():
    """One scheduled-pull iteration: enqueue a fetch for every portal that is DUE.
    Never raises (logs via _log_exc).

    CARDINAL SAFETY GATE: if the global kill-switch is OFF this returns immediately and
    enqueues NOTHING — nothing is ever auto-pulled unless an admin has (a) armed the
    global switch AND (b) set a per-portal interval > 0 on an enabled portal with stored
    credentials (the per-portal conditions are enforced by due_portal_fetches). The
    enqueue is idempotent and the jobs are per-supplier rate-limited downstream, so
    re-enqueuing a still-pending due portal is harmless."""
    if not scrape_scheduler_on():
        return                                   # master gate — inert by default
    try:
        import portal_scraper as PS, waiting_room as IQ
        due = PS.due_portal_fetches()
        n = 0
        for supplier, entity in due:
            try:
                IQ.enqueue_fetch(supplier, entity, user="scheduler")
                n += 1
            except Exception as e:
                _log_exc("scrape-scheduler enqueue", e)
        if n:
            applog.get("app").info(
                "scrape-scheduler: enqueued %s scheduled portal fetch(es)", n)
    except Exception as e:
        _log_exc("scrape-scheduler tick", e)

def _notify_tick():
    """One scheduler iteration: send the digest if it is due. Returns True if a send
    was attempted, else False. Never raises (logs instead)."""
    import traceback
    try:
        import notify as _notify, datetime as _dt
        # PER-EVENT critical alert — checked EVERY tick (not gated on the digest cadence)
        # so a NEW critical condition (DLQ growth, a stalled worker, an overdue filing
        # deadline) is e-mailed near-real-time. critical_alert dedups internally (only
        # fires when the critical SET changes), so it never spams. Never raises.
        crit = _notify.critical_alert()
        if crit == _notify.FAILED:
            _auth.log_error("notify-scheduler", "AlertSendFailed",
                            "critical_alert reported a transport failure; alert NOT "
                            "sent and will retry next tick", "", "system")
        hrs = notify_interval_hours()
        if _notify_due(hrs):
            result = _notify.send_digest()
            if result == _notify.FAILED:
                # transport (SMTP) error: do NOT advance last_sent so the digest is
                # retried next tick, AND surface the blind spot to the admin error log
                # — a broken SMTP must not silently mute all alerting.
                _auth.log_error("notify-scheduler", "DigestSendFailed",
                                "send_digest reported a transport failure; "
                                "digest NOT sent and will retry next tick",
                                "", "system")
                return True
            # success OR nothing-to-report: record the attempt so a quiet period
            # (nothing outstanding) doesn't re-fire every tick.
            _auth.set_setting("notify_last_sent", _dt.datetime.utcnow().isoformat())
            return True
    except Exception as e:
        try:
            _auth.log_error("notify-scheduler", type(e).__name__, str(e),
                            traceback.format_exc(), "system")
        except Exception:
            pass
    return False

def _notify_loop():
    import process_lock
    me = process_lock.whoami()
    while True:
        # only the elected leader checks the schedule / sends the digest.
        if process_lock.acquire("notify-scheduler", ttl=2 * BACKUP_CHECK_SECONDS, holder=me):
            # accumulate the DLQ growth-rate history every tick (cheap, never raises),
            # so the queue-health metric has a sampled baseline even when no digest is
            # due. record_health_sample swallows its own errors, but guard anyway.
            try:
                import waiting_room as _wr
                _wr.record_health_sample()
            except Exception as e:
                _log_exc("intake health sample", e)
            _notify_tick()
            # off-by-default scheduled portal pulls: enqueue DUE fetches onto the worker
            # tier (rate-limited downstream). Inert unless the admin armed the global
            # kill-switch AND a portal has interval>0 + creds. Never raises.
            _scrape_tick()
        time.sleep(BACKUP_CHECK_SECONDS)

def start_notify_scheduler():
    """Start the background notify-digest thread once (called by the server
    entrypoints; not started during imports/tests)."""
    global _notify_started
    if _notify_started:
        return
    _notify_started = True
    threading.Thread(target=_notify_loop, name="notify-scheduler", daemon=True).start()

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
    try:
        IQ.reclaim_orphans()      # reclaim jobs left 'processing' by a crashed worker
    except Exception as e:
        _log_exc("intake-orphan-sweep", e)
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

def node_role():
    """Deployment role of THIS process, for horizontal (multi-server) scaling:
      all    (default) — serve web + run the intake worker (single-box / small fleet)
      web    — serve web only; a dedicated worker fleet drains the queue elsewhere
      worker — background processing only (run via `waiting_room.py --work`)
    Set FFS_ROLE per node. The backup scheduler is NOT gated here: it self-elects a
    single leader across all processes (process_lock), so it is safe everywhere."""
    return os.environ.get("FFS_ROLE", "all").strip().lower()

def start_intake_worker():
    global _intake_started
    # Web-only nodes never drain the queue — that is the worker fleet's job. The
    # legacy INTAKE_WORKER=0 switch still forces the in-process worker off too.
    if _intake_started or os.environ.get("INTAKE_WORKER", "1") == "0":
        return
    if node_role() == "web":
        return
    _intake_started = True
    threading.Thread(target=_intake_loop, name="intake-worker", daemon=True).start()

# ---------------------------------------------------------------- intake gating
# New documents may not be added to the waiting room while earlier ones are still
# GENUINELY in flight (queued/waiting/processing — see waiting_room.BLOCKING_STATES)
# — so a stuck backlog (e.g. an AI token outage) gets cleared before more piles on.
# Terminal failed/held jobs need a human, not the worker, and do NOT gate uploads.
# An admin can grant a
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
    """Returns (blocked, pending_count). Blocked when there's GENUINELY in-flight
    work and no active admin override. Terminal failed/held jobs need a human but
    are not in-flight, so they must NOT freeze fleet-wide uploads (BLOCKING_STATES
    excludes them); they still surface as backlog elsewhere via PENDING_STATES."""
    import waiting_room as IQ
    pend = IQ.pending_count(IQ.BLOCKING_STATES)
    if pend == 0:
        return False, 0
    return (_intake_override_remaining() <= 0), pend

# ---------------------------------------------------------------- queries
# The read-only aggregations live in queries.py (small brick, easy to test).
from queries import (q_periods, q_filters, where, q_compare, q_compare_totals,
                     q_benchmark, q_kpis, q_trend, q_headtohead, q_entities,
                     q_stations, q_savings, q_savings_lines, q_expense, q_ledger)
import metrics
import money

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
:root{--ink:#1a2733;--mut:#5b6b7a;--line:#dde4ea;--line2:#e7ecf1;--bg:#f4f6f8;--acc:#0e5fa8;--acc2:#0b4d89;--ok:#1b7340;--bad:#c8102e;--card-sh:0 1px 2px rgba(26,39,51,.05),0 1px 3px rgba(26,39,51,.04);--radius:11px}
*{box-sizing:border-box}body{margin:0;font:14px/1.55 -apple-system,BlinkMacSystemFont,Segoe UI,Roboto,Helvetica,Arial,sans-serif;color:var(--ink);background:var(--bg);-webkit-font-smoothing:antialiased;text-rendering:optimizeLegibility}
header{background:linear-gradient(180deg,#2a3c4f,#1a2733);color:#fff;padding:11px 22px;display:flex;gap:6px 18px;align-items:center;flex-wrap:wrap;position:sticky;top:0;z-index:20;box-shadow:0 2px 10px rgba(10,20,30,.20),inset 0 -1px 0 rgba(255,255,255,.06)}
header b{font-size:16px;margin-right:6px;letter-spacing:.2px}
header a{color:#cfe0f0;text-decoration:none;font-size:13.5px;transition:color .12s}
header>a:hover,.menu:hover .mlabel,.menu:focus-within .mlabel{color:#fff}
header>a.on,.mlabel.on{color:#fff;border-bottom:2px solid #6db1e8;padding-bottom:2px;text-shadow:0 0 1px rgba(109,177,232,.4)}
.menu{position:relative}
.mlabel{color:#cfe0f0;font-size:13.5px;cursor:pointer;user-select:none;padding:2px 0;white-space:nowrap}
.mlabel::after{content:"▾";color:#6db1e8;font-size:10px;margin-left:4px}
.mdrop{position:absolute;top:100%;left:0;padding-top:8px;display:none;flex-direction:column;gap:1px;z-index:30}
.menu:hover .mdrop,.menu:focus-within .mdrop{display:flex}
.mdrop>span{background:#223240;border:1px solid #34485a;border-radius:10px;padding:6px;min-width:185px;display:flex;flex-direction:column;gap:1px;box-shadow:0 14px 34px rgba(0,0,0,.45)}
.mdrop a{color:#cfe0f0;padding:7px 11px;border-radius:6px;white-space:nowrap;font-size:13px;transition:background .1s,color .1s}
.mdrop a:hover{background:#31485a;color:#fff}
.mdrop a.on{background:var(--acc);color:#fff}
.rightnav{margin-left:auto;display:flex;align-items:center;gap:14px}
th[data-sort]::after{content:" " attr(data-sort);color:#6db1e8;font-weight:400}
.rowfilter{margin:0 0 8px;padding:7px 10px;border:1px solid var(--line);border-radius:7px;width:240px;font-size:13px;background:#fff;transition:border-color .12s,box-shadow .12s}
.rowfilter:focus{border-color:var(--acc);box-shadow:0 0 0 3px rgba(14,95,168,.12);outline:none}
.tablewrap{overflow-x:auto;margin:0 0 2px}
kbd{background:#eef2f6;border:1px solid var(--line);border-radius:4px;padding:0 5px;font:12px ui-monospace,monospace}
a:focus-visible,button:focus-visible,input:focus-visible,select:focus-visible{outline:2px solid #6db1e8;outline-offset:2px}
#kh{position:fixed;inset:0;background:rgba(10,20,30,.55);display:flex;align-items:flex-start;justify-content:center;z-index:50;padding-top:8vh}
.khbox{background:#fff;border-radius:12px;padding:18px 22px;max-width:600px;box-shadow:0 10px 40px rgba(0,0,0,.3)}
.khgrid{display:grid;grid-template-columns:repeat(2,1fr);gap:3px 18px;font-size:13px;margin-top:6px}
@media (max-width:760px){header{gap:12px;padding:10px 14px}main{padding:0 10px}.kpis{grid-template-columns:repeat(auto-fit,minmax(130px,1fr))}}
@media print{header,form,.exp,button,.rowfilter,#kh{display:none!important}main{max-width:none;margin:0}.card{break-inside:avoid;border:0;box-shadow:none}body{background:#fff}}
main{max-width:1180px;margin:22px auto;padding:0 18px}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px;margin-bottom:20px}
.kpi{background:#fff;border:1px solid var(--line);border-radius:10px;padding:14px 16px;box-shadow:var(--card-sh)}
.kpi .v{font-size:22px;font-weight:700;line-height:1.1;letter-spacing:-.01em}.kpi .l{color:var(--mut);font-size:12px;margin-top:3px}
/* dashboard: metric KPI row reads as the focal "at a glance" header */
.kpis.metrics{margin-bottom:14px}
.kpis.metrics .kpi{display:flex;flex-direction:column;justify-content:space-between;min-height:78px;padding:13px 16px 12px}
.kpis.metrics .kpi .v{font-size:25px;letter-spacing:-.01em}
.kpis.metrics .kpi.link{transition:border-color .12s,box-shadow .12s;display:flex}
.kpis.metrics .kpi.link:hover{border-color:#f0b5bd;box-shadow:0 1px 3px rgba(200,16,46,.12)}
.kpis.metrics .kpi.link .l{color:var(--bad)}
/* dashboard: close-status strip is a checklist, lighter than the metric row */
.kpis.status{grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:9px;margin-bottom:0}
.kpis.status .kpi{display:flex;align-items:center;gap:10px;padding:10px 13px;background:#fafbfc}
.kpis.status .kpi .v{font-size:18px}.kpis.status .kpi .l{margin-top:0}
.dashlabel{font-size:13px;letter-spacing:.04em;text-transform:uppercase;color:var(--mut);margin:0 0 8px 2px;font-weight:600}
.card{background:#fff;border:1px solid var(--line);border-radius:var(--radius);padding:18px 20px;margin-bottom:20px;box-shadow:var(--card-sh)}
.card>h2:first-child{margin-top:0}
.card>:last-child{margin-bottom:0}
h2{font-size:15px;font-weight:700;line-height:1.3;margin:0 0 12px;letter-spacing:-.01em}
table{width:100%;border-collapse:collapse;font-size:13.5px}
th{background:#eaeff4;text-align:left;padding:9px 11px;border-bottom:2px solid #cdd8e2;white-space:nowrap;font-weight:600;font-size:12px;letter-spacing:.03em;text-transform:uppercase;color:#475766}
table.sticky thead th{position:sticky;top:var(--navh,56px);z-index:10}
td{padding:7px 11px;border-bottom:1px solid var(--line2)}
tbody tr:last-child td{border-bottom:0}
tbody tr:nth-child(even) td{background:#fafbfc}
tbody tr:hover td{background:#eef5fc}
tr.anom td{background:#fff3cd}tr.anom:hover td{background:#ffe9a8}
tr.disc td{background:#e7f0ff}tr.disc:hover td{background:#d7e6ff}
.r{text-align:right;font-variant-numeric:tabular-nums}.ok{color:var(--ok);font-weight:600}.bad{color:var(--bad);font-weight:600}
.warn{color:#9a6700;font-weight:600}
form.f{display:flex;gap:10px;flex-wrap:wrap;align-items:end;margin-bottom:14px}
form.f label{display:flex;flex-direction:column;font-size:12px;color:var(--mut);gap:3px}
select,input{padding:7px 9px;border:1px solid var(--line);border-radius:7px;font-size:13.5px;background:#fff;color:var(--ink);transition:border-color .12s,box-shadow .12s}
select:hover,input:hover{border-color:#c4cfda}
select:focus,input:focus{border-color:var(--acc);box-shadow:0 0 0 3px rgba(14,95,168,.12)}
button{background:var(--acc);color:#fff;border:0;border-radius:7px;padding:8px 16px;font-size:13.5px;font-weight:600;cursor:pointer;transition:background .12s,box-shadow .12s,transform .04s}
button:hover{background:var(--acc2);box-shadow:0 1px 3px rgba(14,95,168,.3)}
button:active{transform:translateY(1px)}
a.btn{display:inline-block;background:var(--acc);color:#fff;text-decoration:none;border:0;border-radius:7px;padding:8px 16px;font-size:13.5px;font-weight:600;cursor:pointer;transition:background .12s,box-shadow .12s,transform .04s}
a.btn:hover{background:var(--acc2);box-shadow:0 1px 3px rgba(14,95,168,.3)}
a.btn:active{transform:translateY(1px)}
.note{color:var(--mut);font-size:12px;margin-top:8px;line-height:1.5}
main a:not(.btn):not(.kpi){color:var(--acc);text-decoration:none}
main a:not(.btn):not(.kpi):hover{text-decoration:underline}
.subnav a:hover{text-decoration:none}
.exp a{margin-right:14px}
.subnav{display:flex;flex-wrap:wrap;gap:8px;margin:0 0 20px;padding:10px 12px;background:#fff;border:1px solid var(--line);border-radius:var(--radius);position:sticky;top:var(--navh,56px);z-index:9;box-shadow:var(--card-sh)}
.subnav a{color:var(--acc);text-decoration:none;font-size:13px;font-weight:600;padding:5px 12px;border-radius:999px;background:#eef5fc;white-space:nowrap;transition:background .1s,color .1s}
.subnav a:hover{background:#dceafa}
.subnav a.on{background:var(--acc);color:#fff}
h2.section{font-size:13px;letter-spacing:.04em;text-transform:uppercase;color:var(--mut);margin:30px 0 12px;padding-bottom:6px;border-bottom:1px solid var(--line)}
h2.section:first-of-type{margin-top:4px}
.dropzone{display:flex;flex-direction:column;align-items:center;justify-content:center;gap:4px;
 border:1.5px dashed var(--line);border-radius:8px;padding:13px 16px;background:#fafbfc;cursor:pointer;
 color:var(--mut);font-size:12.5px;text-align:center;transition:border-color .12s,background .12s}
.dropzone:hover{border-color:var(--acc)}
.dropzone.drag{border-color:var(--acc);background:#eef5fc;color:var(--acc)}
.dropzone .dzicon{font-size:19px;line-height:1}
.dropzone .dzname{color:var(--ink);font-weight:600;word-break:break-all}
.dropzone .dzhint{font-size:11.5px}
.dropzone .dzlink{color:var(--acc)}
.dropzone.has{border-style:solid;border-color:var(--ok);background:#f2faf5}
.dropzone input[type=file]{position:absolute;width:1px;height:1px;opacity:0;clip:rect(0 0 0 0)}
</style></head><body>
<header><b>⛽ Fleet Fuel</b>
<a href="/" class="{{'on' if page=='dash'}}">Dashboard</a>
{% if 'analytics' in modules %}<div class="menu" tabindex="0"><span class="mlabel {{'on' if page in ['sav','exp','int','cmp','txn','h2h','stn','ano','pri'] else ''}}">Analytics</span><div class="mdrop"><span>
  <a href="/savings" class="{{'on' if page=='sav'}}">Savings</a>
  <a href="/expenses" class="{{'on' if page=='exp'}}">Expenses</a>
  {% if 'pricing' in perms %}<a href="/intel" class="{{'on' if page=='int'}}">Savings &amp; intel</a>{% endif %}
  <a href="/compare" class="{{'on' if page=='cmp'}}">Compare</a>
  <a href="/transactions" class="{{'on' if page=='txn'}}">Transactions</a>
  <a href="/headtohead" class="{{'on' if page=='h2h'}}">Head-to-head</a>
  <a href="/stations" class="{{'on' if page=='stn'}}">Stations</a>
  <a href="/anomalies" class="{{'on' if page=='ano'}}">Anomalies</a>
  {% if 'pricing' in perms %}<a href="/pricing" class="{{'on' if page=='pri'}}">Pricing intel</a>{% endif %}
</span></div></div>{% endif %}
<div class="menu" tabindex="0"><span class="mlabel {{'on' if page in ['ent','vat','rdy','rec','rcv','fx'] else ''}}">VAT &amp; fees</span><div class="mdrop"><span>
  <a href="/entities" class="{{'on' if page=='ent'}}">Entities &amp; VAT</a>
  {% if is_admin and 'vat' in modules %}<a href="/vat" class="{{'on' if page=='vat'}}">VAT refunds</a>
  <a href="/readiness" class="{{'on' if page=='rdy'}}">Claims readiness</a>
  <a href="/recovery" class="{{'on' if page=='rec'}}">Recovery &amp; fees</a>
  <a href="/receivables" class="{{'on' if page=='rcv'}}">Receivables &amp; forecast</a>
  <a href="/recon" class="{{'on' if page=='rcn'}}">Bank reconciliation</a>{% endif %}
  {% if 'fx' in modules %}<a href="/fx" class="{{'on' if page=='fx'}}">FX vs ECB</a>{% endif %}
</span></div></div>
{% if 'compliance' in modules and ('invoice_control' in perms or 'documents' in perms) %}<div class="menu" tabindex="0"><span class="mlabel {{'on' if page in ['inv','con','doc'] else ''}}">Compliance</span><div class="mdrop"><span>
  {% if 'invoice_control' in perms %}<a href="/invoices" class="{{'on' if page=='inv'}}">Invoice control</a>
  <a href="/contracts" class="{{'on' if page=='con'}}">Contract audit</a>{% endif %}
  {% if 'documents' in perms %}<a href="/documents" class="{{'on' if page=='doc'}}">Documents</a>{% endif %}
</span></div></div>{% endif %}
{% if 'intake' in modules and 'data_import' in perms %}<div class="menu" tabindex="0"><span class="mlabel {{'on' if page in ['ext','queue','imp','fil','min'] else ''}}">Intake</span><div class="mdrop"><span>
  <a href="/extract" class="{{'on' if page=='ext'}}">Import batch</a>
  <a href="/queue" class="{{'on' if page=='queue'}}">Waiting room</a>
  <a href="/imports" class="{{'on' if page=='imp'}}">Import log</a>
  <a href="/files" class="{{'on' if page=='fil'}}">File archive</a>
  <a href="/mining" class="{{'on' if page=='min'}}">Doc mining</a>
</span></div></div>{% endif %}
<div class="menu" tabindex="0"><span class="mlabel {{'on' if page in ['sup','cus','dat'] else ''}}">Master data</span><div class="mdrop"><span>
  <a href="/suppliers" class="{{'on' if page=='sup'}}">Suppliers</a>
  {% if is_admin %}<a href="/customers" class="{{'on' if page=='cus'}}">Customers (CRM)</a>{% endif %}
  {% if 'data_import' in perms %}<a href="/data" class="{{'on' if page=='dat'}}">Data manager</a>{% endif %}
</span></div></div>
<a href="/history" class="{{'on' if page=='his'}}">History</a>
<span class="rightnav">
{% if 'exports' in perms %}<div class="menu" tabindex="0"><span class="mlabel">⬇ Export</span><div class="mdrop"><span>
  <a href="/export/summary">Summary report</a><a href="/export/master">Master workbook</a><a href="/export/history">History report</a>
</span></div></div>{% endif %}
{% if role == 'admin' %}<a href="/close" class="{{'on' if page=='close'}}">Monthly close</a>
<a href="/admin" class="{{'on' if page=='adm'}}">Admin</a>{% endif %}
<span class="note" style="color:#9fb3c4">{{ user }} ({{ role }})</span>
<a href="/logout">Sign out</a></span>
</header><main>{{ body|safe }}</main><script src="/app.js" defer></script></body></html>"""

_BASE_TMPL = None   # compiled once; render_template_string would recompile per call
def page(body, p):
    global _BASE_TMPL
    if _BASE_TMPL is None:
        _BASE_TMPL = app.jinja_env.from_string(BASE)
    role = session.get("role", "processor")
    return _BASE_TMPL.render(body=body, page=p,
                             user=session.get("user", ""), role=role, is_admin=(role == "admin"),
                             modules=enabled_modules() if session.get("user") else set(),
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
    bm = q_benchmark(con, period); tr = q_trend(con)
    # Prefer the SETTLED per-period aggregates (materialized at the monthly close) for
    # the KPI cards + the avoidable-overpay figure — they save the expensive live scans
    # (the overpay loop in particular). Any failure / un-rebuilt period silently falls
    # back to the live queries.py path, which stays the source of truth for an open month.
    litres = eurl = net = vat = gross = overpay = None
    try:
        settled = metrics.read(period, con)
    except Exception as e:
        _log_exc("dashboard settled metrics", e); settled = {}
    if settled.get(metrics.M_NET) and settled.get(metrics.M_OVERPAY):
        litres = settled[metrics.M_LITRES]["value"]
        eurl = settled[metrics.M_EURL]["value"]
        net = settled[metrics.M_NET]["value"]
        vat = settled[metrics.M_VAT]["value"]
        gross = money.f2((net or 0) + (vat or 0))   # gross is not stored: net + VAT
        overpay = settled[metrics.M_OVERPAY]["value"]
    else:                                           # un-rebuilt period -> live fallback
        k = q_kpis(con, period); sv = q_savings(con, period)
        litres, eurl, net, vat, gross = k["litres"], k["eurl"], k["net"], k["vat"], k["gross"]
        overpay = sv["total"]
    _litres = f"{litres:,.0f}" if litres is not None else "—"
    _eurl = f"€{eurl:.4f}" if eurl is not None else "—"
    _net = f"€{net:,.0f}" if net is not None else "€0"
    _vat = f"€{vat:,.0f}" if vat is not None else "€0"
    _gross = f"€{gross:,.0f}" if gross is not None else "€0"
    kpis = f"""<div class="dashlabel">This period · {esc(period)}</div><div class="kpis metrics">
      <div class="kpi"><div class="v">{_litres} L</div><div class="l">Diesel litres · {esc(period)}</div></div>
      <div class="kpi"><div class="v">{_eurl}</div><div class="l">Fleet eff. net €/L</div></div>
      <div class="kpi"><div class="v">{_net}</div><div class="l">Net spend</div></div>
      <div class="kpi"><div class="v">{_vat}</div><div class="l">Reclaimable VAT</div></div>
      <a class="kpi link" href="/savings" style="text-decoration:none;color:inherit">
        <div class="v bad">€{(overpay or 0):,.0f}</div><div class="l">Avoidable overpay &rarr;</div></a></div>"""
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
    if session.get("role") == "admin":          # VAT-refund worklist is admin-only
        worklist = _worklist_card(int(period[:4]) if period[:4].isdigit() else 2026)
    body = (f'<form class="f" method="get"><label>Period<select name="period" onchange="this.form.submit()">{psw}</select></label></form>'
            + kpis + close + worklist
            + f'<div class="card"><h2>Diesel benchmark — effective net €/L (cheapest first)</h2>{bench}'
            f'<div class="note">Effective includes rebate layers (Q8/Port One).</div></div>'
            f'<div class="card"><h2>Monthly trend</h2>{trend}<div class="note">Populates as periods are loaded via history.py.</div></div>')
    con.close(); return page(body, "dash")

_close_cache = {}   # period -> (expires_epoch, html); the controls below are heavy
_CLOSE_TTL = 30     # seconds

def _close_status(period):
    """Month-close checklist: one glance at where the period stands. Cached for a
    few seconds so repeated dashboard loads don't re-run the full receipt/anomaly
    controls every time."""
    import os as _os
    _hit = _close_cache.get(period)
    if _hit and _hit[0] > time.time():
        return _hit[1]
    if len(_close_cache) > 24:      # bound the cache (one entry per period)
        _close_cache.clear()
    items = []
    fc = dataproduct.connect("fuel_history")   # read-only: engine-owned product DB
    n = fc.execute("SELECT COUNT(*) c FROM transactions WHERE period=?", (period,)).fetchone()["c"]
    items.append(("Data loaded", n > 0, f"{n} transactions"))
    try:
        import invoice_control as IC
        rows, orphans = IC.control_summary(period)   # render path: read-only, no write/audit churn
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
    except Exception as e:
        _log_exc("dashboard anomaly scan", e)
        items.append(("Anomaly scan", False, str(e)[:40]))
    bdir = f"{WORKDIR}/backups"
    has_b = _os.path.isdir(bdir) and any(f.startswith("ffs_") for f in _os.listdir(bdir))
    items.append(("Backup taken", has_b, "yes" if has_b else "run backup.py"))
    cells = ""
    for label, ok_, detail in items:
        ic = "ok" if ok_ else "bad"
        mark = "\u2713" if ok_ else "\u2717"
        cells += (f'<div class="kpi"><div class="v {ic}">{mark}</div>'
                  f'<div class="l">{esc(label)}<br><span class="note">{esc(detail)}</span></div></div>')
    html = f'<div class="card"><h2>Month-close status — {esc(period)}</h2><div class="kpis status">{cells}</div></div>'
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
                      f"{esc(c['period'])} — €{c['vat_eur']:,.0f} VAT ready (1E)", "/readiness"))
    for c in blocked:
        why = ", ".join(c.get("issues") or []) or "not ready"
        sev = "bad" if c.get("code") != "1B" else ""     # 1B is just waiting for period end
        items.append((sev, f"Unblock {esc(c['entity'])} · {esc(c['country'])} "
                      f"{esc(c['period'])} — {esc(why)}", "/readiness"))
    # filing/response deadlines at risk — from the single source of truth, so PRIOR-YEAR
    # periods (whose 30-Sep deadline lands THIS year) surface regardless of which year's
    # period the dashboard is currently displaying. Distinct from the Submit/Unblock
    # readiness lines above (those describe readiness, not the statutory clock).
    for d in VR.approaching_deadlines(within_days=90):
        when = (f"OVERDUE by {-d['days_left']}d" if d.get("overdue")
                else f"{d['days_left']}d left")
        if d.get("kind") == "filing":
            items.append(("bad", f"Deadline {esc(d.get('deadline', ''))} ({esc(when)}) for "
                          f"{esc(d['entity'])} · {esc(d['country'])} {esc(d['period'])} — "
                          "not submitted yet", "/readiness"))
        else:
            what = "document request" if d.get("code") == "2B" else "appeal"
            items.append(("bad", f"Answer {esc(what)} for {esc(d['entity'])} · "
                          f"{esc(d['country'])} {esc(d['period'])} — deadline "
                          f"{esc(d.get('deadline', ''))} ({esc(when)})", "/vat"))
    for r in recs:
        if r["status"] in ("submitted", "approved") and isinstance(r["age_days"], int) \
           and r["age_days"] >= _AGING_DAYS:
            items.append(("bad", f"Chase {esc(r['entity'])} · {esc(r['country'])} "
                          f"{esc(r['period'])} — submitted {r['age_days']}d ago, unpaid",
                          "/recovery"))
        # money received -> advance to 4 (invoice fee) / 4A (credit), then close
        if r.get("status_code") == "3A" and r.get("next_code"):
            items.append(("", f"Advance {esc(r['entity'])} · {esc(r['country'])} "
                          f"{esc(r['period'])} — money received, → {esc(r['next_code'])} "
                          f"{'invoice the fee' if r['next_code'] == '4' else 'credit the customer'}",
                          "/recovery"))
        if r["fee_billed_date"] and (r["payout_to"] or "customer") == "customer" \
           and not r["fee_invoice_no"]:
            items.append(("", f"Invoice fee for {esc(r['entity'])} · {esc(r['country'])} "
                          f"{esc(r['period'])} — €{(r['fee_eur'] or 0):,.0f}", "/recovery"))
    # customer documents (POA, certificates) that expired or expire soon — an expired
    # document silently turns its claims back to 1A, so flag it early
    try:
        import customer_master as _cm
        ccon = _cm.connect()
        for d in _cm.expiring_documents(ccon, within_days=60)[:6]:
            left = d.get("days_left")
            when = ("EXPIRED" if isinstance(left, int) and left < 0
                    else f"expires in {left}d" if isinstance(left, int) else "expiring")
            where = f" ({d['country']})" if d.get("country") else ""
            items.append(("bad", f"Renew {esc(d['kind'])} for {esc(d['customer'])}{esc(where)} "
                          f"— {when} ({esc(d['valid_until'])})", "/customers"))
        ccon.close()
    except Exception as e:
        _log_exc("worklist expiring docs", e)
    # open document REQUESTS (e.g. a power of attorney sent out for signature) — surface
    # the ones we're still waiting on, flagging overdue ones; this does NOT gate anything,
    # it just chases the signed original needed to activate a refund country.
    try:
        import customer_master as _cm
        ccon = _cm.connect()
        for d in _cm.pending_document_requests(ccon)[:6]:
            ctry = f" ({d['refund_country']})" if d.get("refund_country") else ""
            items.append(("bad" if d.get("overdue") else "",
                          f"Awaiting signed {esc((d.get('kind') or '').replace('_', ' '))} "
                          f"— {esc(d['customer'])}{esc(ctry)}, sent {d.get('age_days', 0)}d ago",
                          "/customers"))
        ccon.close()
    except Exception as e:
        _log_exc("worklist document requests", e)
    # claims blocked specifically on UNMATCHED/unresolved invoice refs — these are
    # silent stalls (the transaction never matched a registered invoice), so surface
    # them with a direct link to where they're resolved.
    try:
        import re as _re
        n_unres = 0
        for c in blocked:
            for iss in (c.get("issues") or []):
                if "unresolved invoice ref" in iss:
                    m = _re.match(r"\s*(\d+)", iss)
                    n_unres += int(m.group(1)) if m else 1
        if n_unres:
            items.append(("bad", f"Resolve UNMATCHED — {n_unres} unresolved "
                          f"invoice ref(s)", "/vat/unmatched"))
    except Exception as e:
        _log_exc("worklist unmatched refs", e)
    # documents stuck in the intake queue (failed extraction or held for manual retry)
    # — otherwise they sit invisibly in the waiting room and nobody acts on them.
    try:
        import waiting_room as _wr
        cnt = _wr.counts()
        stuck = (cnt.get("failed") or 0) + (cnt.get("held") or 0)
        if stuck:
            items.append(("bad", f"{stuck} document(s) stuck in intake "
                          f"(failed/held) — review", "/queue"))
        # the intake worker may be stalled/starved: the oldest still-flowing job is
        # older than the SLO, so nothing is draining even though it's not "failed".
        h = _wr.queue_health()
        if h.get("age_breach"):
            hrs = (h.get("oldest_pending_age_s") or 0) // 3600
            items.append(("bad", f"Intake worker may be stalled — oldest pending "
                          f"document is {hrs}h old (SLO {_wr.OLDEST_PENDING_SLO_HOURS}h)",
                          "/queue"))
    except Exception as e:
        _log_exc("worklist intake stuck", e)
    # register-failure split-brain (D4): a statement's source PDFs are vaulted IN-REQUEST
    # but the registry write is enqueued (kind='register'); if that job failed/was lost the
    # documents sit vaulted with no registered invoice and nothing flags them. Surface both
    # the doc-orphan reconcile AND the failed/held register JOB as distinct signals.
    try:
        import invoice_control as _ic
        orphans = _ic.unregistered_vaulted_documents()
        if orphans:
            items.append(("bad", f"{len(orphans)} vaulted document(s) have no registered "
                          f"invoice — registration may have failed", "/imports"))
    except Exception as e:
        _log_exc("worklist unregistered docs", e)
    try:
        import waiting_room as _wr
        bad = {"failed", "held"}
        reg_failed = sum(1 for j in _wr.jobs()
                         if (j.get("kind") == _wr.KIND_REGISTER) and j.get("status") in bad)
        if reg_failed:
            items.append(("bad", f"{reg_failed} statement registration job(s) failed/held "
                          f"— invoices not registered", "/queue"))
    except Exception as e:
        _log_exc("worklist register jobs", e)
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

# the five stages engine_close.close runs, in order — the progress panel shows one
# line per stage (the import_log "close" channel records each under these names).
_CLOSE_STAGES = ("consolidate", "build_master", "history", "invoice_control", "backup")

def _close_progress(period):
    """READ-ONLY per-stage progress panel for the monthly close, built from the
    import_log 'close' channel for `period`. One line per stage showing its latest
    status (received/success/failed), message and time, plus an overall state badge:
    IN PROGRESS while the engine 'close-run' lock is held (or a stage is 'received'
    with no terminal event yet), COMPLETE once backup succeeded, FAILED if any stage
    failed. Degrades to an empty panel on a read error (logged, never a 500) — the app
    is READ-ONLY here; the close itself runs on the worker."""
    import import_log
    latest = {}            # stage -> the newest import_log row for it
    try:
        # newest-first; keep the FIRST (latest) row seen for each stage.
        for r in import_log.recent(channel="close", limit=300):
            if r.get("period") != period:
                continue
            stage = r.get("source_name")
            if stage in _CLOSE_STAGES and stage not in latest:
                latest[stage] = r
    except Exception as e:
        _log_exc("close progress read", e)
        latest = {}
    in_progress = False
    try:
        import process_lock
        in_progress = process_lock.held_by(_CLOSE_LOCK) is not None
    except Exception as e:
        _log_exc("close progress lock", e)
    any_failed = any((latest.get(s) or {}).get("status") == "failed" for s in _CLOSE_STAGES)
    any_received = any((latest.get(s) or {}).get("status") == "received" for s in _CLOSE_STAGES)
    backup_ok = (latest.get("backup") or {}).get("status") == "success"
    if any_failed:
        badge = '<span class="bad">FAILED</span>'
    elif in_progress or (any_received and not backup_ok):
        badge = '<span>IN PROGRESS</span>'
    elif backup_ok:
        badge = '<span class="ok">COMPLETE</span>'
    elif latest:
        badge = '<span>IN PROGRESS</span>'
    else:
        badge = '<span class="note">not started</span>'
    _STCLS = {"success": "ok", "failed": "bad", "received": ""}
    rows = []
    for s in _CLOSE_STAGES:
        r = latest.get(s)
        if r:
            st = r.get("status") or ""
            cell = f'<td class="{_STCLS.get(st, "")}">{esc(st)}</td>'
            msg = esc((r.get("message") or "")[:120])
            ts = esc(r.get("ts") or "")
        else:
            cell = '<td class="note">—</td>'
            msg = '<span class="note">no event yet</span>'
            ts = ""
        rows.append([f'<td>{esc(s)}</td>', cell, f'<td>{msg}</td>',
                     f'<td class="note">{ts}</td>'])
    table = tbl(["Stage", "Status", "Message", "Time (UTC)"], rows)
    return (f'<div class="card"><h2>Close progress — {esc(period)} &nbsp;{badge}</h2>'
            f'{table}<div class="note">Read-only view of the worker\'s progress '
            '(import log, channel “close”). Reload to refresh.</div></div>')

@app.route("/close", methods=["GET", "POST"])
def monthly_close():
    """One-click MONTHLY CLOSE (admin-only; enforced via ADMIN_ONLY in _guard). The
    web request NEVER runs the close inline and holds NO writable engine-owned
    product-DB handle — it only ENQUEUES a fileless job (waiting_room.enqueue_close);
    the worker tier dispatches kind='close' and calls engine_close.close OFF the
    request. The GET view shows a period selector, the guarded run button, and a
    READ-ONLY per-stage progress panel from the import_log 'close' channel."""
    import waiting_room as IQ
    con = DB(); periods = q_periods(con); con.close()
    banner = ""
    if request.method == "POST":
        # default to the latest loaded period (same source the dashboard uses).
        period = (request.form.get("period") or (periods[0] if periods else "")).strip()
        if not period:
            banner = ('<div class="card"><b class="bad">No period to close — load a '
                      'period first (import a batch or run history).</b></div>')
        else:
            try:
                # ENQUEUE ONLY — the close runs on the worker; the request returns now.
                IQ.enqueue_close(period, session["user"])
                banner = (f'<div class="card"><b class="ok">Monthly close queued for '
                          f'{esc(period)} — it runs on the worker; progress below.</b></div>')
            except Exception as e:
                _log_exc("enqueue monthly close", e)
                banner = (f'<div class="card"><b class="bad">Could not queue the close: '
                          f'{esc(str(e))}</b></div>')
    else:
        period = (request.args.get("period") or (periods[0] if periods else "")).strip()
    if not period:
        return page(banner + '<div class="card"><h2>Monthly close</h2><p>No data '
                    'loaded yet — import an invoice batch first.</p></div>', "close")
    psw = "".join(f'<option {"selected" if p == period else ""}>{esc(p)}</option>'
                  for p in periods)
    run_form = (
        '<form method="post" class="f" data-confirm="Run the monthly close for this '
        'period? It runs on the worker (consolidate → build_master → history → '
        'invoice_control → backup).">' + _csrf_input()
        + f'<label>Period<select name="period">{psw}</select></label>'
        + '<button name="__act" value="run">Run monthly close</button></form>')
    card = ('<div class="card"><h2>Monthly close</h2>'
            '<p class="note">Runs the full engine close as ONE guarded, restartable '
            'unit on the worker tier — consolidate → build_master → history → '
            'invoice_control → backup. The web request only queues it; it never runs '
            'inline.</p>' + run_form + '</div>')
    return page(banner + card + _close_progress(period), "close")

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

# Routing flags are LEARNED from the data, not fixed numbers. For each country/period
# we learn both the price LEVEL and the price SPREAD from the actual transactions, and
# flag a station only when it falls outside that market's own normal spread (± one
# standard deviation). So the trigger price is never hardcoded: in a volatile market a
# station must be much cheaper to be PREFER; in a tight market a small edge qualifies.
# ROUTE_SIGMAS is the only knob (statistical sensitivity, default 1.0), env-overridable.
import statistics as _stats
ROUTE_SIGMAS = float(os.environ.get("ROUTE_SIGMAS", "1.0"))

def _country_benchmarks(rows):
    """Learn each country's (volume-weighted mean, std-dev) of effective NET EUR/L for
    the displayed period — both numbers come from the data, nothing is fixed."""
    by = {}
    for r in rows:
        if r["eurl"] is None:
            continue
        by.setdefault(r["country"], []).append((r["eurl"], r["litres"] or 0))
    out = {}
    for c, vals in by.items():
        prices = [p for p, _ in vals]
        lsum = sum(l for _, l in vals)
        mean = (sum(p * l for p, l in vals) / lsum) if lsum else _stats.fmean(prices)
        sd = _stats.pstdev(prices) if len(prices) > 1 else 0.0
        out[c] = (mean, sd)
    return out

def _route_flag(eurl, country, bench):
    b = bench.get(country)
    if eurl is None or not b:
        return ""
    mean, sd = b
    if sd <= 0:                              # no spread in this market -> nothing stands out
        return ""
    if eurl <= mean - ROUTE_SIGMAS * sd:
        return "PREFER"
    if eurl >= mean + ROUTE_SIGMAS * sd:
        return "AVOID"
    return ""

@app.route("/stations")
def stations():
    con = DB(); periods = q_periods(con)
    period = request.args.get("period", periods[0] if periods else None)
    rows = q_stations(con, period) if period else []
    bench = _country_benchmarks(rows)
    psw = "".join(f'<option {"selected" if p==period else ""}>{esc(p)}</option>' for p in periods)
    trs = []
    for r in rows:
        eurl = r["eurl"]
        rf = _route_flag(eurl, r["country"], bench)
        flag = ('<td class="ok">PREFER</td>' if rf == "PREFER"
                else '<td class="bad">AVOID</td>' if rf == "AVOID" else "<td></td>")
        trs.append([f"<td>{esc(r['supplier'])}</td><td>{esc(r['country'])}</td><td>{esc(r['station'])}</td>",
                    f"<td class=r>{r['litres']:,.0f}</td><td class=r><b>{(eurl or 0):.4f}</b></td>{flag}"])
    body = (f'<form class="f" method="get"><label>Period<select name="period" onchange="this.form.submit()">{psw}</select></label>'
            f'<a href="/export/stations?period={esc(period or "")}" style="align-self:end;padding:8px 12px;font-size:13px">⬇ Routing export (Excel)</a></form>'
            f'<div class="card"><h2>Diesel station scorecard ≥300 L — cheapest first ({esc(period) if period else "no data"})</h2>'
            + tbl(["Supplier","Country","Station","Litres","Eff. €/L","Routing"], trs)
            + '<div class="note">PREFER / AVOID are <b>learned from the data</b>: a station is flagged '
              'only when it falls outside its own country\'s normal price spread this period '
              '(±1 std-dev of the volume-weighted average). No fixed price band — the trigger adapts to '
              'how volatile each market actually is. Export gives drivers the routing list per station.</div></div>')
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
    bench = _country_benchmarks(rows)
    import reports as R
    from openpyxl.styles import Font, PatternFill
    from openpyxl.formatting.rule import ColorScaleRule
    wb = Workbook(); ws = wb.active; ws.title = "Routing"
    ws.append(["Supplier", "Country", "Station", "Litres", "Eff EUR/L", "Routing", "Period"])
    R.style_header(ws, 1, 7)
    for r in rows:
        e = r["eurl"]
        flag = _route_flag(e, r["country"], bench)
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
    # Per-supplier price-review rollup from the detail lines — WHICH supplier to review,
    # most-overpaid first. Wrapped so a failure here can't break the /savings page.
    review = []
    if period:
        try:
            roll = {}
            for ln in q_savings_lines(con, period):
                a = roll.setdefault(ln["supplier"], {"fuellings": 0, "litres": 0.0, "overpay": 0.0})
                a["fuellings"] += 1
                a["litres"] += ln["litres"] or 0
                a["overpay"] += ln["overpay_eur"] or 0
            review = sorted(roll.items(), key=lambda x: -x[1]["overpay"])
        except Exception as e:
            _log_exc("savings/per-supplier review", e)
            review = []
    con.close()
    psw = "".join(f'<option {"selected" if p==period else ""}>{esc(p)}</option>' for p in periods)
    review_rows = [[f'<td>{esc(sup)}</td>',
                    f'<td class=r>{a["fuellings"]:,d}</td>',
                    f'<td class=r>{a["litres"]:,.0f}</td>',
                    f'<td class="r bad">€{a["overpay"]:,.2f}</td>']
                   for sup, a in review]
    if review_rows:
        dl = (f'<a class="btn" href="/export/overpay?period={esc(period)}">Download price-review packet (Excel)</a>'
              if period else "")
        review_card = ('<div class="card"><h2>Supplier price-review — who to renegotiate '
                       f'<span class="note">(most-overpaid first)</span></h2>{dl}'
                       + tbl(["Supplier", "Fuellings", "Litres", "Total overpay €"], review_rows)
                       + '<div class="note"><b>Competitiveness review, not a contractual claim.</b> '
                         'Each figure is how much more a supplier charged than the cheapest same-day, '
                         'same-country diesel rival — evidence for renegotiation or steering volume, '
                         'not money the supplier owes. NET EUR/L, final. Download the per-fuelling-day '
                         'detail packet above.</div></div>')
    else:
        review_card = ''
    body = (f'<form class="f" method="get"><label>Period<select name="period" onchange="this.form.submit()">{psw}</select></label></form>'
            f'<div class="kpis"><div class="kpi"><div class="v bad">€{s["total"]:,.0f}</div>'
            f'<div class="l">avoidable overpay vs cheapest same-day rival · {esc(period) if period else "no data"}</div></div></div>'
            + review_card
            + '<div class="card"><h2>Overpay by country</h2>'
            + svg_hbars(s["by_country"], unit=" €") + '</div>'
            + '<div class="card"><h2>Overpay by supplier</h2>'
            + svg_hbars(s["by_supplier"], unit=" €", color="#c8102e") + '</div>'
            + '<div class="note"><b>Diesel-focused</b> — apples-to-apples: only days where 2+ suppliers '
              'fueled diesel in the same country count (other product groups are not included). The premium '
              'is attributed to the dearer supplier. Drill into any slice on the '
              '<a href="/transactions">Transactions</a> page. Prices NET EUR/L, final.</div>')
    return page(body, "sav")

@app.route("/export/overpay")
def export_overpay():
    """Supplier price-competitiveness review packet (Excel) for a period — the
    per-fuelling-day overpay detail behind /savings, to act on (renegotiate / steer
    volume). Competitiveness review, NOT a contractual claim."""
    import reports
    period = request.args.get("period") or None
    supplier = request.args.get("supplier") or None
    path = reports.overpay_review_workbook(period, supplier)
    return send_file(path, as_attachment=True, download_name=os.path.basename(path),
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

@app.route("/expenses")
def expenses():
    """Finance-facing company expense / cost-allocation report — per-entity (cost
    centre) and per-vehicle NET / VAT / gross spend over the validated transactions.
    NET EUR basis, final (rebates applied); gross = net + VAT. Read-only."""
    con = DB(); periods = q_periods(con)
    period = request.args.get("period", periods[0] if periods else None)
    entity = request.args.get("entity") or None
    # Wrap the data fetch so a failure here can't 500 the page.
    try:
        data = q_expense(con, period, entity) if period else None
    except Exception as e:
        _log_exc("expenses/q_expense", e)
        data = None
    con.close()
    if data is None:
        data = {"by_entity": [], "by_vehicle": [], "by_product": [],
                "totals": {"net_eur": 0.0, "vat_eur": 0.0, "gross_eur": 0.0,
                           "litres": 0.0, "fuellings": 0}}
    psw = "".join(f'<option {"selected" if p==period else ""}>{esc(p)}</option>' for p in periods)
    t = data["totals"]

    ent_rows = [[f'<td>{esc(e["entity"])}</td>',
                 f'<td class=r>{e["fuellings"]:,d}</td>',
                 f'<td class=r>{e["n_vehicles"]:,d}</td>',
                 f'<td class=r>{e["litres"]:,.0f}</td>',
                 f'<td class=r>€{e["net_eur"]:,.2f}</td>',
                 f'<td class=r>€{e["vat_eur"]:,.2f}</td>',
                 f'<td class=r>€{e["gross_eur"]:,.2f}</td>']
                for e in data["by_entity"]]
    prod_rows = [[f'<td>{esc(g["product_group"])}</td>',
                  f'<td class=r>{g["litres"]:,.0f}</td>',
                  f'<td class=r>€{g["net_eur"]:,.2f}</td>',
                  f'<td class=r>€{g["vat_eur"]:,.2f}</td>',
                  f'<td class=r>€{g["gross_eur"]:,.2f}</td>']
                 for g in data["by_product"]]
    veh_rows = [[f'<td>{esc(v["entity"])}</td>',
                 f'<td>{esc(v["vehicle"])}</td>',
                 f'<td class=r>{v["fuellings"]:,d}</td>',
                 f'<td class=r>{v["litres"]:,.0f}</td>',
                 f'<td class=r>€{v["net_eur"]:,.2f}</td>',
                 f'<td class=r>€{v["vat_eur"]:,.2f}</td>',
                 f'<td class=r>€{v["gross_eur"]:,.2f}</td>',
                 f'<td class=r>{v["net_eur_l"]:,.4f}</td>',
                 f'<td class=r>{v["n_countries"]:,d}</td>']
                for v in data["by_vehicle"]]

    ent_qs = f"&entity={esc(entity)}" if entity else ""
    dl = (f'<a class="btn" href="/export/expenses?period={esc(period)}{ent_qs}">Download expense report (Excel)</a>'
          f' <a class="btn" href="/export/accounting?period={esc(period)}{ent_qs}">Download accounting ledger (CSV)</a>'
          f' <a class="btn" href="/export/saft?period={esc(period)}{ent_qs}">Download SAF-T (XML, core structure)</a>'
          f'<div class="note">Transaction-level ledger for import into your accounting/ERP system. '
          f'NET EUR, final; VAT shown separately; gross = net + VAT. '
          f'The SAF-T export is the OECD core structure — specialize per jurisdiction '
          f'(namespace/version/required fields) before any real tax-authority submission.</div>'
          if period else "")
    body = (f'<form class="f" method="get"><label>Period<select name="period" onchange="this.form.submit()">{psw}</select></label></form>'
            f'<div class="kpis">'
            f'<div class="kpi"><div class="v">€{t["net_eur"]:,.0f}</div><div class="l">Net spend (EUR)</div></div>'
            f'<div class="kpi"><div class="v">€{t["vat_eur"]:,.0f}</div><div class="l">VAT (EUR)</div></div>'
            f'<div class="kpi"><div class="v">€{t["gross_eur"]:,.0f}</div><div class="l">Gross (EUR)</div></div>'
            f'<div class="kpi"><div class="v">{t["litres"]:,.0f}</div><div class="l">Litres</div></div></div>'
            f'<div class="card"><h2>Expense by entity (cost centre)</h2>{dl}'
            + tbl(["Entity", "Fuellings", "Vehicles", "Litres", "Net €", "VAT €", "Gross €"], ent_rows)
            + '</div>'
            + '<div class="card"><h2>Expense by product group</h2>'
            + tbl(["Product group", "Litres", "Net €", "VAT €", "Gross €"], prod_rows) + '</div>'
            + '<div class="card"><h2>Expense by vehicle</h2>'
            + tbl(["Entity", "Vehicle", "Fuellings", "Litres", "Net €", "VAT €", "Gross €", "NET €/L", "Countries"], veh_rows)
            + '</div>'
            + '<div class="note">Company fuel &amp; toll spend allocated per entity (cost centre) '
              'and per vehicle. Prices NET EUR, final (rebates applied); VAT shown separately; '
              'gross = net + VAT. NET €/L is the rebate-effective net price per litre.</div>')
    return page(body, "exp")

@app.route("/export/expenses")
def export_expenses():
    """Company expense / cost-allocation report (Excel) for a period — per-entity and
    per-vehicle NET / VAT / gross spend. NET EUR basis, final (rebates applied)."""
    import reports
    period = request.args.get("period") or None
    entity = request.args.get("entity") or None
    path = reports.expense_report_workbook(period, entity)
    return send_file(path, as_attachment=True, download_name=os.path.basename(path),
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

@app.route("/export/accounting")
def export_accounting():
    """Accounting / ERP ledger export (CSV) for a period — one row per transaction
    (decision-free; no chart-of-accounts, no country-specific SAF-T XML). NET EUR basis,
    final (rebates applied); VAT shown separately; gross = net + VAT. Read-only."""
    import io, reports
    period = request.args.get("period") or None
    entity = request.args.get("entity") or None
    name, data = reports.accounting_ledger_csv(period, entity)
    return send_file(io.BytesIO(data), as_attachment=True, download_name=name,
                     mimetype="text/csv")

@app.route("/export/saft")
def export_saft():
    """SAF-T (Standard Audit File for Tax) export — OECD-SAF-T CORE STRUCTURE for a
    period. Parameterised by a CountryProfile (namespace/version/sections) so a real
    jurisdiction can be specialised later; the generic profile is NOT a validated
    submission for any tax authority. NET EUR basis; gross = net + VAT. Read-only."""
    import io, saft
    period = request.args.get("period") or None
    entity = request.args.get("entity") or None
    profile = saft.get_profile(request.args.get("profile", "OECD"))
    try:
        name, data = saft.build_saft(period, entity, profile)
    except ValueError:
        return page('<div class="card"><h2>Nothing to export</h2>'
                    '<p class="note">No ledger data for this period/entity. '
                    'Load a period or pick another.</p></div>', "exp")
    return send_file(io.BytesIO(data), as_attachment=True, download_name=name,
                     mimetype="application/xml")

@app.route("/transactions")
def transactions():
    con = DB(); f = q_filters(con)
    period = request.args.get("period")
    if period is None:
        period = f["periods"][0] if f["periods"] else "ALL"
    ent = [x for x in request.args.getlist("entity") if x and x != "ALL"]
    sup = [x for x in request.args.getlist("supplier") if x and x != "ALL"]
    ctry = [x for x in request.args.getlist("country") if x and x != "ALL"]
    stn = [x for x in request.args.getlist("station") if x and x != "ALL"]
    df = request.args.get("date_from", ""); dt = request.args.get("date_to", "")
    w, p = where(request.args, period)
    # full precision from the DB — never pre-round currency for analytics; format at display
    rows = [dict(r) for r in con.execute(f"""SELECT period, date, supplier, country, station,
        vehicle, product, product_group, qty, net_eur, net_eur_eff,
        net_eur_eff/NULLIF(qty,0) eurl
        FROM transactions WHERE {w} ORDER BY date, supplier, station LIMIT 2000""", p).fetchall()]
    import anomaly as AN
    hist_rebates = AN.expected_rebates(con)          # learn typical Port One-style rebates
    ann = AN.annotate(rows, hist_rebates)            # in-place per-line flags & discounts
    con.close()
    pcur = period if period in f["periods"] else "ALL"
    form = ('<form class="f" method="get">'
            + f'<label>period<select name="period"><option {"selected" if pcur=="ALL" else ""}>ALL</option>'
            + "".join(f'<option {"selected" if pp==pcur else ""}>{esc(pp)}</option>' for pp in f["periods"])
            + '</select></label>'
            + multisel("entity", "client", f["entities"], ent, size=6)
            + multisel("supplier", "suppliers", f["suppliers"], sup, size=6)
            + multisel("country", "countries", f["countries"], ctry, size=6)
            + multisel("station", "location (station)", f["stations"], stn, size=6)
            + f'<label>date from<input type="date" name="date_from" value="{esc(df)}"></label>'
            + f'<label>date to<input type="date" name="date_to" value="{esc(dt)}"></label>'
            + '<button>Apply filters</button>'
            + '<a href="/transactions" style="align-self:end;padding:8px 12px;font-size:13px">Reset</a></form>')
    head_cells = ["Date","Supplier","Country","Station","Vehicle","Product","Qty","Net €",
                  "Rebate/Discount €","€/L eff","Flags"]
    n_anom = sum(1 for a in ann if a["anomaly"])
    n_disc = sum(1 for a in ann if a["is_discount"])
    n_exp = sum(1 for a in ann if a["expected_rebate"])
    body_rows = ""
    for r, a in zip(rows, ann):
        cls = "anom" if a["anomaly"] else ("disc" if a["is_discount"] else "")
        # rebate / discount cell: applied rebate (e.g. Port One), a discount line's value,
        # or an expected-but-missing rebate learned from history
        if a["is_discount"]:
            reb = f'<span class="bad">discount {(r["net_eur"] or 0):,.2f}</span>'
        elif a["rebate"] and a["rebate"] > 0.005:
            reb = f'<span class="ok">−{a["rebate"]:,.2f}</span>'
        elif a["expected_rebate"]:
            reb = f'<span class="note">exp ~−{a["expected_rebate"]:,.2f}</span>'
        else:
            reb = ""
        flags = []
        if a["anomaly"]:
            flags.append(f'<span class="bad">⚠ {esc(a["anomaly"])}</span>')
        if a["is_discount"] and a["relates_to"]:
            flags.append(f'<span class="note">discount → relates to {esc(a["relates_to"])}</span>')
        if a["expected_rebate"]:
            flags.append('<span class="note">Port One-style rebate expected (not on invoice) — '
                         'estimated from history</span>')
        eurl_disp = f'{r["eurl"]:.4f}' if r["eurl"] is not None else ""
        body_rows += (f'<tr class="{cls}"><td>{esc(r["date"])}</td><td>{esc(r["supplier"])}</td>'
                      f'<td>{esc(r["country"])}</td><td>{esc(r["station"])}</td>'
                      f'<td>{esc(r["vehicle"])}</td><td>{esc(r["product"])}</td>'
                      f'<td class=r>{(r["qty"] or 0):,.2f}</td><td class=r>{(r["net_eur"] or 0):,.2f}</td>'
                      f'<td class=r>{reb}</td><td class=r>{eurl_disp}</td>'
                      f'<td class=note>{" · ".join(flags)}</td></tr>')
    table = ('<table class="sticky"><thead><tr>' + "".join(f"<th>{h}</th>" for h in head_cells)
             + f'</tr></thead><tbody>{body_rows}</tbody></table>')
    body = (form
            + '<div class="kpis">'
            + f'<div class="kpi"><div class="v">{len(rows)}</div><div class="l">lines</div></div>'
            + f'<div class="kpi"><div class="v {"bad" if n_anom else ""}">{n_anom}</div><div class="l">price anomalies</div></div>'
            + f'<div class="kpi"><div class="v">{n_disc}</div><div class="l">discount/adj lines</div></div>'
            + f'<div class="kpi"><div class="v">{n_exp}</div><div class="l">expected rebates (off-invoice)</div></div></div>'
            + f'<div class="card"><h2>Transactions — {len(rows)} line(s)'
            + (' <span class="note">(capped at 2,000 — narrow the filters)</span>' if len(rows) == 2000 else '')
            + '</h2>'
            + table
            + '<div class="note">Line-level detail. <b>Anomalies are highlighted in place</b> (amber) — the '
              'flag sits on the exact transaction, learned from each country/period\'s own price spread. '
              '<b>Discount/adjustment lines</b> (blue) are marked and related to the supplier/country/period '
              'they apply to. <b>Rebate/Discount €</b> shows the applied rebate (e.g. Q8 Port One), a '
              'discount line\'s value, or an <i>expected</i> rebate estimated from history when the separate '
              'rebate invoice isn\'t present. Filter by client, supplier, country, location, date.</div></div>')
    return page(body, "txn")

# Established FX deviation threshold: an applied/implied rate ≥2% off the official ECB
# reference is flagged for review (the period-aggregate trend and the per-invoice
# verification both use this one convention).
FX_DEVIATION_PCT = 2.0

@app.route("/fx", methods=["GET", "POST"])
def fx():
    """Compare each invoice's effective exchange rate (net_local / net_eur) against
    the official ECB euro reference rate for the period, fetched on demand."""
    import ecb_rates as ECB, money
    banner = ""
    is_admin = session.get("role") == "admin"
    import import_log as _IL
    if request.method == "POST" and request.form.get("__act") in ("refresh", "backfill"):
        full = request.form.get("__act") == "backfill"
        try:
            info = ECB.backfill_history() if full else ECB.fetch_and_store()
            banner = (f'<div class="card"><b class="ok">ECB rates '
                      f'{"backfilled (full history)" if full else "scraped"} via {esc(info["source"])} '
                      f'— as of {esc(info["asof"])}: {info["days"]} day(s), '
                      f'{len(info["currencies"])} currencies stored in the database.</b></div>')
            _IL.log("fx", "ECB " + ("full history" if full else "daily"), "success",
                    actor=session.get("user", "system"), records=info["rows"],
                    message=f"{info['source']} · {len(info['currencies'])} currencies · as of {info['asof']}")
        except Exception as e:
            _log_exc("ECB scrape", e)
            _IL.log("fx", "ECB scrape", "failed", actor=session.get("user", "system"),
                    message=str(e)[:200])
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
    # filters so the competitor exchange can be filtered & compared
    fsup = request.args.get("supplier") or None
    fccy = request.args.get("currency") or None
    fper = request.args.get("period") or None
    con = DB()
    sup_opts = [r[0] for r in con.execute("SELECT DISTINCT supplier FROM transactions WHERE currency<>'EUR' ORDER BY 1")]
    ccy_opts = [r[0] for r in con.execute("SELECT DISTINCT currency FROM transactions WHERE currency<>'EUR' ORDER BY 1")]
    per_opts = [r[0] for r in con.execute("SELECT DISTINCT period FROM transactions WHERE currency<>'EUR' ORDER BY 1 DESC")]
    fw, fp = ["currency<>'EUR'"], []
    if fsup: fw.append("supplier=?"); fp.append(fsup)
    if fccy: fw.append("currency=?"); fp.append(fccy)
    if fper: fw.append("period=?");   fp.append(fper)
    rows = con.execute(f"""
        SELECT supplier, currency, period,
               SUM(net_local) net_local, SUM(net_eur) net_eur,
               SUM(net_local)/NULLIF(SUM(net_eur),0) implied, MAX(date) last_date
        FROM transactions WHERE {' AND '.join(fw)}
        GROUP BY supplier, currency, period ORDER BY period DESC, supplier""", fp).fetchall()
    con.close()
    # persist the competitor FX as a historic pattern, then read the control/trend
    import supplier_fx as SFX
    try:
        SFX.snapshot()
    except Exception as e:
        _log_exc("supplier FX snapshot", e)
    trend = [t for t in SFX.trend()
             if (not fsup or t["supplier"] == fsup) and (not fccy or t["currency"] == fccy)]
    asof, asof_src = ECB.latest_asof()
    trs, total_diff, flagged = [], 0.0, 0
    for r in rows:
        ecb_rate, ecb_date = ECB.rate_for(r["currency"], r["last_date"])
        if ecb_rate:
            dev = (r["implied"] - ecb_rate) / ecb_rate * 100 if ecb_rate else None
            eur_at_ecb = money.f2((r["net_local"] or 0) / ecb_rate)
            eur_diff = money.f2((r["net_eur"] or 0) - eur_at_ecb)
            total_diff += eur_diff
            bad = abs(dev) >= FX_DEVIATION_PCT
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
    backfill = (('<form method="post" style="display:inline">' + _csrf_input()
                 + '<button name="__act" value="backfill" style="background:var(--mut)">⤓ Backfill full history</button></form>')
                if is_admin else "")
    upload = ('<form method="post" enctype="multipart/form-data" style="display:inline-flex;gap:8px;align-items:center">'
              + _csrf_input()
              + '<input type="file" name="file" accept=".csv,.xml" required>'
              + '<button name="__act" value="upload">⬆ Upload rates</button></form>') if is_admin else ""
    # reference card: every European currency and its latest rate stored in the database
    cov = ECB.coverage()
    latest = ECB.latest_rates(list(ECB.EUROPEAN) + ["USD"])
    ccy_rows = []
    for code, name in {**ECB.EUROPEAN, "USD": "US dollar"}.items():
        lr = latest.get(code)
        rate = "1.00000 (base)" if code == "EUR" else (f"{lr[0]:.5f}" if lr else "—")
        ason = (lr[1] if lr else "") if code != "EUR" else ""
        ccy_rows.append([f"<td>{esc(code)}</td><td>{esc(name)}</td>",
                         f"<td class=r>{rate}</td><td class=note>{esc(ason)}</td>"])
    ccy_card = ('<div class="card"><h2>European currencies — latest ECB rate in the database</h2>'
                + tbl(["Code", "Currency", "Rate (per €1)", "As of"], ccy_rows)
                + f'<div class="note">All ECB euro reference currencies are stored in <code>ecb_rates.db</code>'
                + (f' — {cov[2]:,} daily rates across {cov[3]} currencies, {esc(cov[0] or "")} to {esc(cov[1] or "")}.'
                   if cov[2] else ' (none cached yet — scrape or upload).')
                + ' Use <b>Backfill full history</b> so a relevant rate exists for any transaction date.</div></div>')
    head = (f'<div class="kpis">'
            f'<div class="kpi"><div class="v">{esc(asof) if asof else "—"}</div>'
            f'<div class="l">rates as of{(" · " + esc(asof_src)) if asof_src else ""}</div></div>'
            f'<div class="kpi"><div class="v {"bad" if abs(total_diff)>=1 else ""}">'
            f'€{total_diff:+,.0f}</div><div class="l">invoiced EUR vs EUR at ECB</div></div>'
            f'<div class="kpi"><div class="v {"bad" if flagged else "ok"}">{flagged}</div>'
            f'<div class="l">streams ≥2% off ECB</div></div></div>')
    # filter form (compare competitor FX by supplier / currency / period)
    def _fsel(name, opts, cur):
        o = '<option value="">all</option>' + "".join(
            f'<option {"selected" if v == cur else ""}>{esc(v)}</option>' for v in opts)
        return f'<label>{esc(name)}<select name="{esc(name)}" onchange="this.form.submit()">{o}</select></label>'
    filt = ('<form class="f" method="get" style="margin-bottom:12px">'
            + _fsel("supplier", sup_opts, fsup or "") + _fsel("currency", ccy_opts, fccy or "")
            + _fsel("period", per_opts, fper or "")
            + '<a href="/fx" style="align-self:end;padding:8px 12px;font-size:13px">Reset</a></form>')
    # competitor FX markup trend — control vs the market, historic pattern
    arrow = {"increasing": "↑", "decreasing": "↓", "stable": "→", "new": "•"}
    tcls = {"increasing": "bad", "decreasing": "ok", "stable": "", "new": "note"}
    ttrs = []
    for t in trend:
        d = "" if t["delta"] is None else f'{t["delta"]:+.2f}pp'
        ttrs.append([f"<td>{esc(t['supplier'])}</td><td>{esc(t['currency'])}</td><td>{esc(t['period'])}</td>",
                     f"<td class=r><b>{t['markup']:+.2f}%</b></td>",
                     f"<td class='{tcls[t['direction']]}'>{arrow[t['direction']]} {esc(t['direction'])}</td>",
                     f"<td class=r>{d}</td><td class=note>{t['points']} period(s)</td>"])
    n_up = sum(1 for t in trend if t["increasing"])
    trend_card = ('<div class="card"><h2>Competitor FX markup — control vs market (ECB)</h2>'
                  + f'<div class="kpis"><div class="kpi"><div class="v {"bad" if n_up else "ok"}">{n_up}</div>'
                    '<div class="l">markups increasing vs market</div></div></div>'
                  + (tbl(["Supplier", "Ccy", "Period", "Markup vs ECB", "Trend", "Δ vs prev", "History"], ttrs)
                     if ttrs else '<p class="note">FX history builds as more periods load — the trend needs '
                                   '2+ periods to compare.</p>')
                  + '<div class="note">Each supplier\'s implied FX rate vs the ECB market rate is stored as a '
                    'historic pattern (<code>supplier_fx_history</code>). <b>Trend</b> shows whether the markup '
                    'over the market is <span class="bad">increasing</span> (worse) or <span class="ok">'
                    'decreasing</span> (better) vs the previous period — an FX cost control. Filter above to '
                    'compare suppliers/currencies.</div></div>')
    # ---- Per-invoice ECB verification (owner-directed: rates verified INDEPENDENTLY
    # per invoice). Reads the OFFICIAL ECB reference frozen on each line at consolidation
    # (history.ecb_reference -> fx_ecb_rate/date/source) and compares it to the APPLIED
    # rate at the (supplier, currency, period, date) — invoice/fuelling-day — grain, NOT
    # the period aggregate above. The 2% threshold is the same FX_DEVIATION_PCT convention.
    try:
        vrows = SFX.verify_invoices_fx(period=fper, tolerance=FX_DEVIATION_PCT / 100.0)
    except Exception as e:
        # never let the verification read take down /fx — degrade to the empty state
        _log_exc("fx per-invoice verification", e)
        vrows = []
    if fsup: vrows = [v for v in vrows if v["supplier"] == fsup]
    if fccy: vrows = [v for v in vrows if v["currency"] == fccy]
    v_flagged = sum(1 for v in vrows if v["flagged"])
    v_noref = sum(1 for v in vrows if v["no_ref"])
    v_verified = len(vrows) - v_noref
    vtrs = []
    for v in vrows:
        if v["no_ref"]:
            ecb_cell = '<td class=r>—</td><td class=note>—</td>'
            dev_cell = '<td class="note">no ECB reference — load ECB rates</td>'
            diff_cell = '<td class=r>—</td>'
        else:
            cls = "bad" if v["flagged"] else "ok"
            ecb_cell = (f'<td class=r>{v["ecb_rate"]:.5f}</td>'
                        f'<td class=note>{esc(v["ecb_date"] or "")}</td>')
            dev_cell = f'<td class="r {cls}">{v["deviation_pct"]:+.2f}%</td>'
            diff_cell = f'<td class="r {cls}">{money.f2(v["eur_diff"]):+,.2f}</td>'
        vtrs.append([
            f"<td>{esc(v['supplier'])}</td><td>{esc(v['currency'])}</td>"
            f"<td>{esc(v['period'])}</td><td class=note>{esc(v['date'] or '')}</td>"
            f"<td class=r>{v['lines']}</td>",
            f"<td class=r>{(v['net_local'] or 0):,.2f}</td>"
            f"<td class=r>{(v['net_eur'] or 0):,.2f}</td>",
            f"<td class=r><b>{v['applied_rate']:.5f}</b></td>" + ecb_cell + dev_cell + diff_cell])
    verify_card = (
        '<div class="card"><h2>Per-invoice ECB verification</h2>'
        + f'<div class="kpis"><div class="kpi"><div class="v">{v_verified}</div>'
          '<div class="l">invoices verified vs ECB</div></div>'
        + f'<div class="kpi"><div class="v {"bad" if v_flagged else "ok"}">{v_flagged}</div>'
          f'<div class="l">flagged ≥{FX_DEVIATION_PCT:g}% off ECB</div></div>'
        + f'<div class="kpi"><div class="v {"bad" if v_noref else ""}">{v_noref}</div>'
          '<div class="l">without an ECB reference</div></div></div>'
        + (tbl(["Supplier", "Ccy", "Period", "Date", "Lines", "Net local", "Net EUR",
                "Applied rate", "ECB rate", "ECB date", "Deviation", "EUR diff"], vtrs)
           if vtrs else '<p class="note">No non-EUR invoices to verify for this selection.</p>')
        + '<div class="note">INDEPENDENT verification: each invoice/fuelling-day is checked '
          'against the <b>official ECB reference rate frozen on its own lines</b> at '
          'consolidation (<code>fx_ecb_rate</code>/<code>fx_ecb_date</code>), not a single '
          'period rate. <b>Applied rate</b> = net_local ÷ net_eur. Deviation ≥ '
          f'{FX_DEVIATION_PCT:g}% is flagged. Lines with <b>no ECB reference</b> show '
          '"load ECB rates" rather than a false pass — backfill ECB history above so a rate '
          'exists for every transaction date. Amounts NET EUR, final.</div></div>')
    body = (banner
            + f'<div class="f" style="margin-bottom:12px;align-items:center;gap:8px">{refresh}{backfill}{upload}'
            + (('<span class="note" style="align-self:center">No rates cached yet — '
                + ('scrape live or upload a CSV/ECB-XML.' if is_admin
                   else 'ask an admin to scrape or upload rates.') + '</span>') if not asof else '')
            + '</div>'
            + head
            + filt
            + trend_card
            + verify_card
            + ccy_card
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
    fs = sorted(glob.glob(os.path.join(WORKDIR, "Fleet_Fuel_Master_*.xlsx")))
    if not fs:
        return page('<div class="card"><b class="bad">No master workbook yet.</b>'
                    '<p>Run the monthly pipeline to generate it: '
                    '<kbd>python consolidate.py</kbd> then <kbd>python build_master.py</kbd>.</p></div>',
                    "dash"), 404
    return send_file(fs[-1], as_attachment=True)

@app.route("/export/history")
def export_history():
    path = os.path.join(WORKDIR, "Fleet_Fuel_History_Report.xlsx")
    if not os.path.exists(path):
        # degrade gracefully (200) when the deliverable hasn't been generated yet —
        # e.g. on a fresh checkout, before the monthly close has run history.py.
        return page('<div class="card"><b class="bad">No history report yet.</b>'
                    '<p>It is produced by the monthly close: run '
                    '<kbd>python history.py</kbd> (after consolidate / build_master).</p></div>',
                    "dash")
    return send_file(path, as_attachment=True)

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

@app.route("/export/evidence", methods=["POST"])
def export_evidence():
    """Audit-ready EVIDENCE-EXPORT pack (M6): bundle a VAT claim's SHA-256-verified
    original documents into one ZIP with an integrity MANIFEST + a cover summary, so
    a claim's supporting evidence can be handed over provably-intact. Admin-only (VAT
    surface). Re-verifies each document at export time; a MISSING/MISMATCH is flagged
    in the cover/manifest, never hidden, and the download still works. Audited."""
    import vat_refund as VR, io
    ent = request.form.get("entity", ""); ctry = request.form.get("country", "")
    per = request.form.get("period", "")
    con = VR.connect()
    try:
        if not con.execute("""SELECT 1 FROM vat_applications WHERE entity=? AND
                              refund_country=? AND ref_period=?""",
                           (ent, ctry, per)).fetchone():
            con.close()
            return page('<div class="card"><b class="bad">No such claim.</b></div>', "rec"), 404
        zip_bytes, summary = VR.evidence_pack(ent, ctry, per, con=con)
        # audit the export action (no row mutation for a trigger to catch)
        _audit_mod.record_event(con, "vat_applications",
                                f"{ent}|{ctry}|{per}", "EVIDENCE_EXPORT",
                                {"documents": summary["documents"], "ok": summary["ok"],
                                 "mismatch": summary["mismatch"], "missing": summary["missing"],
                                 "intact": summary["intact"]})
    except Exception as e:
        _log_exc("export/evidence", e)
        con.close()
        return page('<div class="card"><b class="bad">Could not build evidence pack.</b></div>',
                    "rec"), 500
    con.close()
    fname = f"Evidence_{VR._safe_name(ent)}_{VR._safe_name(ctry)}_{VR._safe_name(per)}.zip"
    return send_file(io.BytesIO(zip_bytes), as_attachment=True, download_name=fname,
                     mimetype="application/zip")

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


def _human_bytes(n):
    n = int(n or 0)
    if n < 1024:
        return f"{n} B"
    if n < 1048576:
        return f"{n/1024:.0f} KB"
    return f"{n/1048576:.1f} MB"


def _upload_receipt(filename, nbytes, sha, ok, why=""):
    """An explicit, human-readable confirmation that an uploaded file was (OK) or was
    not (Bad) safely stored. 'Upload OK' means the bytes are durably archived in the
    data lake AND verified by reading them back and re-hashing — the file can no longer
    be lost. Processing/using the file's DATA is a separate step shown below."""
    fn = esc(filename or "file")
    if ok:
        return ('<div class="card" style="border-left:4px solid var(--ok)">'
                f'<b class="ok">&#10003; Upload OK</b> — <b>{fn}</b> '
                f'({_human_bytes(nbytes)}) was received and safely archived.'
                f'<div class="note">Fingerprint <code>{esc(sha[:12])}</code>. '
                'Stored in the data lake — it can’t be lost, only deleted by a user. '
                'Processing the data is the next step.</div></div>')
    return ('<div class="card" style="border-left:4px solid var(--bad)">'
            f'<b class="bad">&#10007; Upload failed — batch rejected</b> — <b>{fn}</b> '
            'could not be stored' + (f': {esc(why)}.' if why else '.') +
            '<div class="note">The bad data was discarded — nothing was kept and nothing '
            'was processed. Please <b>re-upload the entire batch</b>. The problem is '
            'recorded in the admin error log.</div></div>')


@app.route("/extract", methods=["GET", "POST"])
def extract_batch():
    import extract as EX
    # access is enforced centrally in _guard (capability: data_import)
    backend_env = EX.EXTRACT_BACKEND
    body = ""
    if request.method == "POST" and "file" in request.files:
        f = request.files["file"]
        data = f.read()
        # DURABILITY: archive every uploaded file permanently in the data lake on
        # arrival (SHA-256, same storage backends as the PDF vault) — it can only be
        # removed later by an explicit user delete or a corruption flag, never lost.
        # Whether its DATA is processed/used is a separate concern.
        import data_lake as _DL, import_log as _IL, hashlib as _hl
        _loc, _sha = None, _hl.sha256(data).hexdigest()
        _ok, _why = False, ""
        if not data:
            _why = "the file was empty (0 bytes)"
        else:
            try:
                _loc = _DL.put(data, f.filename, kind="raw_upload",
                               supplier=request.form.get("backend") or None,
                               source_name=f.filename, meta={"by": session.get("user")})
                # CONFIRM the stored copy: read it back and re-hash so 'OK' means the
                # file truly landed intact, not merely that put() returned.
                _back = _DL.get(_loc)
                _ok = bool(_back) and _hl.sha256(_back).hexdigest() == _sha
                if not _ok:
                    _why = "the archived copy did not match the uploaded file"
            except Exception as e:
                _log_exc("data lake archive", e)
                _why = str(e)
        _IL.log("upload", f.filename, "received" if _ok else "failed",
                actor=session.get("user", "system"),
                supplier=request.form.get("backend") or None, sha256=_sha,
                file_locator=_loc, bytes=len(data),
                message=(f"archived to data lake ({request.form.get('__mode','now')})"
                         if _ok else f"archive failed: {_why}"))
        if not _ok:
            # Reject the whole batch: a file we couldn't store safely is never processed.
            # PURGE any bad/partial copy so corrupt data doesn't linger, then send the
            # user back to re-upload the entire batch.
            if _loc:
                try:
                    _DL.delete_locator(_loc)
                except Exception as e:
                    _log_exc("purge bad upload", e)
            return page(_upload_receipt(f.filename, len(data), _sha, False, _why)
                        + _upload_form(backend_env), "ext")
        receipt = _upload_receipt(f.filename, len(data), _sha, True)
        if request.form.get("__mode") == "queue":
            # waiting room: store durably now, extract later in the background worker
            import waiting_room as IQ
            blocked, pend = _intake_uploads_blocked()
            if blocked:
                hint = ('An administrator can grant a temporary override on the '
                        'waiting-room page.' if session.get("role") != "admin"
                        else 'Use “Allow uploads (override)” on the waiting-room page '
                             'to add anyway.')
                return page(receipt + '<div class="card"><b class="bad">New uploads are '
                            'paused.</b>'
                            f'<p>Your file is safely archived, but {pend} document(s) in the '
                            'waiting room still need to be processed first. Clear them — open '
                            'the waiting room and press <b>Send / restart all</b>. ' + esc(hint)
                            + '</p><p><a href="/queue">→ Go to the waiting room</a></p></div>'
                            + _upload_form(backend_env), "ext")
            try:
                jid, st = IQ.enqueue(data, f.filename, backend=request.form.get("backend") or None,
                                     period=request.form.get("period") or None,
                                     user=session.get("user", "system"))
            except Exception as e:
                _log_exc("intake enqueue", e)
                return page(receipt + '<div class="card"><b class="bad">Could not queue '
                            f'file: {esc(str(e))}</b><div class="note">The upload itself is '
                            'safe in the data lake; only the background job could not be '
                            'created.</div></div>' + _upload_form(backend_env), "ext")
            # Upload OK -> sent for processing (background). Land on the waiting room with
            # an explicit confirmation.
            return redirect(f"/queue?msg=Upload+OK:+{esc(f.filename)}+archived+%26+sent+for+"
                            f"processing+(job+{jid},+{st})")
        try:
            draft = EX.extract(data, f.filename, backend=request.form.get("backend") or None)
        except Exception as e:
            _log_exc("import batch / extraction", e)
            return page(receipt + '<div class="card"><b class="bad">Extraction error: '
                        f'{esc(str(e))}</b><div class="note">The file is safely archived; '
                        'only reading its data failed. You can retry or pick another '
                        'extractor.</div></div>' + _upload_form(backend_env), "ext")
        if draft.get("error"):
            return page(receipt + f'<div class="card"><b class="bad">{esc(draft["error"])}</b>'
                        '<div class="note">The file is safely archived; review the message '
                        'above and retry.</div></div>' + _upload_form(backend_env), "ext")
        # stash pdf bytes in a temp dir keyed by token; never put bytes in the form
        import tempfile, pickle, os as _os
        token = _os.urandom(8).hex()
        tmp = _os.path.join(WORKDIR, ".extract_tmp"); _os.makedirs(tmp, exist_ok=True); _os.chmod(tmp, 0o700)
        with open(_os.path.join(tmp, token + ".pkl"), "wb") as pf:
            pickle.dump(draft.get("_pdf_bytes", []), pf)
        _stash_draft(token, draft)
        return page(receipt + _review_form(draft, token), "ext")
    return page(_upload_form(backend_env), "ext")


def _default_period():
    """The current/active close period, sourced from month_config (the one file edited
    each month) — the SAME PERIOD consolidate.py/build_master.py/history.py key off.
    Best-effort: if the import ever fails, fall back to the empty string (the form field
    is required, so the user just fills it in)."""
    try:
        from month_config import PERIOD
        return PERIOD
    except Exception as e:
        _log_exc("default period from month_config", e)
        return ""


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
            f'<label>period (YYYY-MM)<input name="period" value="{esc(request.values.get("period", _default_period()))}" style="width:100px"></label>'
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


def _stash_draft(token, draft):
    """Persist the cleaned review draft (no PDF bytes, no '_'-prefixed keys) next to the
    token's PDF stash, so the advisory /extract/ai-review route can rebuild context from
    the SAME token. Best-effort: a failure here never blocks the review screen."""
    import os as _os, json as _json
    clean = {k: v for k, v in (draft or {}).items() if not str(k).startswith("_")}
    try:
        tmp = _os.path.join(WORKDIR, ".extract_tmp")
        _os.makedirs(tmp, exist_ok=True); _os.chmod(tmp, 0o700)
        with open(_os.path.join(tmp, token + ".draft.json"), "w", encoding="utf-8") as f:
            _json.dump(clean, f, ensure_ascii=False, default=str)
    except Exception as e:
        _log_exc("stash review draft", e)


def _load_draft(token):
    """Read back the cleaned draft for a token, or None."""
    import os as _os, json as _json
    p = _os.path.join(WORKDIR, ".extract_tmp", token + ".draft.json")
    if not _os.path.exists(p):
        return None
    try:
        with open(p, encoding="utf-8") as f:
            return _json.load(f)
    except Exception as e:
        _log_exc("load review draft", e)
        return None


def _review_form(draft, token, intake_job=None, period=None, ai_panel=""):
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
            f'<label>period (YYYY-MM)<input name="period" value="{esc(period or request.values.get("period", _default_period()))}" required></label>'
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
            'normal triage. You can still edit any field above first.</div>'
            + _ai_review_button(token, intake_job, period)
            + '</div>'
            + ai_panel)


def _ai_review_button(token, intake_job=None, period=None):
    """The 'AI review (advisory)' control under the draft. Shown only when a backend is
    configured; otherwise a muted off-note. Advisory only — it never gates commit."""
    import ai_review
    if ai_review.resolve_backend() == "none":
        return ('<div class="note" style="margin-top:10px">AI review is off — '
                'enable it in <a href="/admin">Admin</a> (advisory only; it never '
                'changes a figure or gates the commit).</div>')
    return ('<form method="post" action="/extract/ai-review" style="margin-top:10px">'
            + _csrf_input()
            + f'<input type="hidden" name="token" value="{esc(token)}">'
            + (f'<input type="hidden" name="intake_job" value="{esc(str(intake_job))}">'
               if intake_job else "")
            + f'<input type="hidden" name="period" value="{esc(period or request.values.get("period", _default_period()))}">'
            + '<button>AI review (advisory)</button>'
            + '<span class="note" style="margin-left:8px">A second opinion over the '
              'already-extracted data — never changes anything, never gates commit.</span>'
            + '</form>')


@app.route("/extract/confirm", methods=["POST"])
def extract_confirm():
    import extract as EX, vat_refund as VR
    import os as _os, pickle
    # access is enforced centrally in _guard (capability: data_import)
    token = request.form["token"]
    tmpf = _os.path.join(WORKDIR, ".extract_tmp", token + ".pkl")
    draftf = _os.path.join(WORKDIR, ".extract_tmp", token + ".draft.json")
    def _drop_draft():
        try:
            if _os.path.exists(draftf): _os.unlink(draftf)
        except OSError as e:
            _log_exc("drop review draft", e)
    if request.form.get("__do") == "cancel":
        if _os.path.exists(tmpf): _os.unlink(tmpf)
        _drop_draft()
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
        try:
            import import_log as _IL
            _IL.log("statement", request.form.get("stmt_ref", "").strip(), "failed",
                    actor=session.get("user", "system"), supplier=supplier, period=period,
                    message=f"commit blocked: {vr['errors']} error(s), {vr['warnings']} warning(s)")
        except Exception as e:
            _log.debug("statement-commit blocked: import_log write failed: %s", e)
        thead = "".join(f"<th>{h}</th>" for h in ["Invoice","Country","Net","VAT","Check","Issue"])
        return page('<div class="card"><b class="bad">Commit blocked - fix the errors '
                    f'({vr["errors"]} error, {vr["warnings"]} warning) and re-import:</b>'
                    f'<table><thead><tr>{thead}</tr></thead><tbody>{rows_html}</tbody></table>'
                    + "</div>", "ext")
    stmt_ref = request.form["stmt_ref"].strip()
    # D4: the suppliers.db WRITE is the last in-request product-DB write — move it
    # behind the engine. Validation (above) and PDF vaulting (below, vat_claims.db is
    # app-owned) stay synchronous so the operator still sees errors immediately, but the
    # register_statement write is ENQUEUED and performed by the engine worker. The
    # confirming user is carried so the worker propagates it as the audit actor. The web
    # request therefore holds NO writable suppliers.db handle.
    import waiting_room as IQ
    reg_payload = {
        "supplier": supplier, "statement_ref": stmt_ref, "period": period,
        "statement_date": request.form.get("stmt_date", "").strip(),
        "lines": lines, "customer": customer,
        "notes": "imported via batch extraction", "draft": token,
    }
    job_id, _job_st = IQ.enqueue_registration(reg_payload, user=session.get("user", "system"))
    VAL.save_baseline(supplier, stmt_ref, vlines)
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
    _drop_draft()
    # if this draft came from the waiting room, mark the job done (frees its bytes)
    if request.form.get("intake_job"):
        try:
            IQ.complete(int(request.form["intake_job"]))
        except Exception as e:
            _log_exc("intake complete", e)
    try:
        import import_log as _IL
        _IL.log("statement", stmt_ref, "success",
                actor=session.get("user", "system"), client=customer, supplier=supplier,
                period=period, records=len(lines),
                message=f"{len(lines)} invoices validated, {attached} PDFs vaulted; "
                        f"registration queued (job {job_id})")
    except Exception as e:
        _log_exc("import log statement", e)
    banner = (f'<div class="card"><b class="ok">Statement {esc(stmt_ref)} '
              f'queued for registration: {len(lines)} invoices validated, '
              f'{attached} PDFs vaulted. The suppliers.db write is completing in the '
              f'background — see the intake monitor below for the outcome.</b></div>')
    return page(banner + '<p><a href="/extract">→ Intake monitor</a> &nbsp; '
                f'<a href="/invoices?period={esc(period)}">→ Invoice control</a></p>', "ext")


def _contract_price_terms(SM, supplier, country, product_group="Diesel"):
    """The contracted PURCHASE-price terms for (supplier, country) from
    supplier_master.supplier_discounts — the SAME source/keying contract_audit.py uses.
    Returns (expected_discount_eur_l, max_net_eur_l) on a NET EUR/L basis; either may be
    None. Matches like contract_audit: supplier uppercased, country/station SQL-LIKE
    ('%' = any), product_group exact (blank = any). MERGES across all matching active
    rules: takes the first non-None rebate AND the first non-None ceiling, so a supplier
    with a rebate-only rule and a separate ceiling-only rule surfaces BOTH. Full float
    precision (no cent-quantization)."""
    import contract_audit
    want_sup = (supplier or "").upper()
    exp_disc = ceiling = None
    for r in SM.discount_rules():
        if r.get("supplier") != want_sup:
            continue
        pg = r.get("product_group")
        if pg and product_group and pg != product_group:
            continue
        if not contract_audit._like(country, r.get("country")):
            continue
        if exp_disc is None:
            exp_disc = r.get("expected_discount_eur_l")
        if ceiling is None:
            ceiling = r.get("max_net_eur_l")
        if exp_disc is not None and ceiling is not None:
            break
    return exp_disc, ceiling


def _ai_review_context(draft):
    """Assemble the (non-secret) context for ai_review.review from master data:
    supplier expected name/VAT (supplier_master), customer contract terms
    (customer_master, bank/contact fields stripped by ai_review), and a NET-EUR/L price
    range learned from history. Best-effort — any lookup failure just omits that slice."""
    import supplier_master as SM, customer_master as CM
    ctx = {}
    supplier = (draft.get("supplier") or "").strip()
    customer = (draft.get("customer") or "").strip()
    countries = [ (ln.get("country") or "").strip() for ln in draft.get("lines", []) ]
    countries = [c for c in countries if c]
    first_ctry = countries[0] if countries else None
    if supplier:
        try:
            name, vat, _src = SM.get_issuer(supplier, first_ctry)
            sc = {"expected_name": name, "expected_vat": vat, "aliases": []}
            # Contract PRICE terms for the AI's price-vs-contract reasoning come from the
            # SAME source contract_audit.py uses (supplier_master.supplier_discounts):
            # the contracted rebate (EUR/L) and the NET price ceiling (EUR/L). These are
            # the PURCHASE-price contract, NOT the agency's service fee (fee_pct/fee_min).
            # Match the rule the way contract_audit does: uppercased supplier, country
            # LIKE-pattern, Diesel product group ('%' = any). NET EUR/L basis.
            try:
                exp_disc, ceiling = _contract_price_terms(SM, supplier, first_ctry)
                if exp_disc is not None:
                    sc["expected_discount_eur_l"] = exp_disc
                if ceiling is not None:
                    sc["price_ceiling_eur_l"] = ceiling
            except Exception as e:
                _log_exc("ai-review contract terms", e)
            ctx["supplier"] = sc
        except Exception as e:
            _log_exc("ai-review supplier context", e)
    if customer:
        try:
            ccon = CM.connect()
            f = CM.merge_fields(ccon, customer, first_ctry); ccon.close()
            # Name only — the agency service-fee terms (fee_pct/fee_min) are domain #7
            # invoicing and are irrelevant to invoice validation; never sent to the AI.
            ctx["customer"] = {"name": f.get("company_name") or f.get("name") or customer}
        except Exception as e:
            _log_exc("ai-review customer context", e)
    # NET-EUR/L price samples from history for the supplier's countries (display basis)
    if supplier:
        try:
            import sqlite3 as _sq
            hp = os.path.join(WORKDIR, "fuel_history.db")
            if os.path.exists(hp) and countries:
                hc = _sq.connect(hp)
                qmarks = ",".join("?" for _ in set(countries))
                rows = hc.execute(
                    "SELECT net_eur_eff/NULLIF(qty,0) p FROM transactions "
                    "WHERE supplier=? AND product_group='Diesel' AND qty>0 "
                    f"AND country IN ({qmarks})",
                    [supplier] + list(set(countries))).fetchall()
                hc.close()
                samples = [r[0] for r in rows if r[0] is not None]
                if samples:
                    ctx["price_samples"] = samples
        except Exception as e:
            _log_exc("ai-review price range", e)
    return ctx


@app.route("/extract/ai-review", methods=["POST"])
def extract_ai_review():
    """ADVISORY AI review of an already-extracted draft. Re-renders the same review/confirm
    screen with an appended advisory panel (flags + analytics note + deterministic block).
    Access: data_import (enforced in _guard). NEVER mutates the draft, NEVER gates commit —
    the /extract/confirm deterministic gate is untouched."""
    import ai_review
    token = request.form.get("token", "")
    intake_job = request.form.get("intake_job") or None
    period = request.form.get("period") or None
    draft = _load_draft(token)
    if draft is None:
        return page('<div class="card"><b class="bad">This draft is no longer available '
                    'for review (the session expired). Re-extract the batch.</b></div>'
                    '<p><a href="/extract">← back to import</a></p>', "ext")
    backend = ai_review.resolve_backend()
    if backend == "none":
        # belt-and-braces: never call out when off
        return page(_review_form(draft, token, intake_job=intake_job, period=period), "ext")
    # CONFIDENCE-LEARNING (advisory-only consumer). If this (supplier, country) pair has
    # earned enough trust, SKIP computing the AI panel entirely — a cost saving. This is
    # the ONLY place trust is consulted; it never touches a deterministic gate (the
    # /extract/confirm commit path, checklist, thresholds, locks, period-end are all
    # unchanged) and the human still confirms the draft.
    import confidence
    c_sup, c_ctry = _confidence_key(draft)
    try:
        skip = confidence.should_skip_ai(c_sup, c_ctry)
    except Exception as e:                          # should_skip_ai already fails safe
        _log_exc("confidence skip check", e)
        skip = False
    if skip:
        sc = confidence.trust(c_sup, c_ctry)
        n_clean = 0
        try:
            n_clean = next((r["n_clean"] for r in confidence.scoreboard()
                            if r["supplier"] == (c_sup or "").strip()
                            and r["country"] == (c_ctry or "").strip()), 0)
        except Exception as e:
            _log_exc("confidence skip counts", e)
        panel = ('<div class="card"><h2>AI review (advisory)</h2>'
                 '<div class="note" style="margin-top:0">AI review skipped — '
                 f'<b>{esc(c_sup or "?")}</b>/<b>{esc(c_ctry or "?")}</b> is trusted '
                 f'(score {esc(f"{sc:.2f}")} after {esc(str(n_clean))} clean validations). '
                 'Advisory only; the deterministic checks and your confirmation are '
                 'unchanged.</div></div>')
        # NB: no validation event recorded on a skip — we didn't validate anything here.
        return page(_review_form(draft, token, intake_job=intake_job, period=period,
                                 ai_panel=panel), "ext")
    try:
        ctx = _ai_review_context(draft)
        result = ai_review.review(draft, ctx)
    except Exception as e:
        _log_exc("ai review", e)
        panel = ('<div class="card"><b class="bad">AI review unavailable right now '
                 f'({esc(str(e))}). It is advisory only — nothing was changed; you can '
                 'still confirm the draft.</b></div>')
        return page(_review_form(draft, token, intake_job=intake_job, period=period,
                                 ai_panel=panel), "ext")
    # Best-effort confidence telemetry: a no-flag review is a clean validation (trust
    # rises); a review that surfaced flags is a discrepancy (trust falls). A failure
    # here must never break the advisory review.
    try:
        clean = not (result.get("flags") or [])
        confidence.record_validation(c_sup, c_ctry, clean=clean, source="ai_review")
    except Exception as e:
        _log_exc("confidence record_validation", e)
    panel = _ai_review_panel(result)
    return page(_review_form(draft, token, intake_job=intake_job, period=period,
                             ai_panel=panel), "ext")


def _confidence_key(draft):
    """Resolve the (supplier, country) key the confidence model attributes trust to.
    Supplier is the draft's single supplier (`draft['supplier']`). Country is the
    PREDOMINANT line country (the most common non-empty `country` across the draft
    lines) — a draft is one supplier statement, so attributing to its dominant country
    avoids mis-crediting trust to an incidental line. Empty -> '' (a stable bucket)."""
    supplier = (draft.get("supplier") or "").strip()
    counts = {}
    for ln in draft.get("lines", []) or []:
        c = (ln.get("country") or "").strip()
        if c:
            counts[c] = counts.get(c, 0) + 1
    # most frequent country; ties broken alphabetically for determinism
    country = ""
    if counts:
        country = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
    return supplier, country


def _ai_review_panel(result):
    """Render the advisory panel: flags table + analytics note + deterministic block +
    provenance. EVERY cell escaped. Advisory only — nothing here changes a figure."""
    sev_cls = {"info": "", "warn": "warn", "error": "bad"}
    rows = ""
    for fl in result.get("flags", []):
        cls = sev_cls.get(fl.get("severity"), "")
        rows += (f'<tr><td class="{cls}">{esc(fl.get("severity",""))}</td>'
                 f'<td>{esc(fl.get("field",""))}</td>'
                 f'<td>{esc(fl.get("message",""))}</td>'
                 f'<td class="note">{esc(fl.get("suggestion") or "")}</td></tr>')
    if not rows:
        rows = '<tr><td colspan="4" class="note">No advisory flags raised.</td></tr>'
    thead = "".join(f"<th>{h}</th>" for h in ["Severity", "Field", "Message", "Suggestion"])
    flags_tbl = (f'<table style="margin-top:8px"><thead><tr>{thead}</tr></thead>'
                 f'<tbody>{rows}</tbody></table>')
    note = result.get("note")
    note_html = (f'<div class="note" style="margin-top:8px"><b>Analytics note:</b> '
                 f'{esc(note)} <i>(prices in NET EUR/L, VAT-excluded.)</i></div>'
                 if note else "")
    det = result.get("deterministic") or {}
    det_html = (f'<div class="note" style="margin-top:8px"><b>Deterministic findings:</b> '
                f'{esc(str(det.get("errors", 0)))} error(s), '
                f'{esc(str(det.get("warnings", 0)))} warning(s); '
                f'can commit: <b>{esc(str(det.get("can_commit")))}</b>. '
                'These — not the AI — decide whether commit is allowed.</div>')
    prov = (f'<div class="note" style="margin-top:8px">'
            f'{esc(result.get("backend",""))}'
            + (f' · {esc(result.get("model",""))}' if result.get("model") else "")
            + ' · advisory only — accept/reject is your decision; nothing was changed.</div>')
    return ('<div class="card"><h2>AI review (advisory)</h2>'
            '<div class="note" style="margin-top:0">A second opinion over the '
            'already-extracted data. It never changes a figure or status and never gates '
            'the commit.</div>'
            + flags_tbl + note_html + det_html + prov + '</div>')


def _intake_extract_outcomes(limit=20):
    """Recent-uploads card for the monitoring panel: the durable extraction outcomes
    from import_log (channel='extract' — written by the queue worker on each job),
    showing the source/backend the extractor used and success/failure. The waiting_room
    only records the supplier on the live draft; the lasting per-upload outcome trail
    lives here, so we surface it rather than inventing data."""
    try:
        import import_log
        events = import_log.recent(channel="extract", limit=limit)
    except Exception as e:
        _log_exc("intake extract outcomes", e)
        return ('<div class="card"><h2>Recent uploads &amp; extraction outcome</h2>'
                '<p class="bad">Could not read the import log.</p></div>')
    if not events:
        return ('<div class="card"><h2>Recent uploads &amp; extraction outcome</h2>'
                '<p class="note">No extraction events logged yet. Outcomes appear here once '
                'the worker processes a queued upload.</p></div>')
    rows = []
    for ev in events:
        stcls = {"success": "ok", "failed": "bad", "partial": "warn"}.get(ev.get("status"), "")
        rows.append([
            f'<td class="note">{esc(ev.get("ts") or "")}</td>',
            f'<td>{esc(ev.get("source_name") or "")}</td>',
            f'<td>{esc(ev.get("supplier") or "")}</td>',
            f'<td class="{stcls}">{esc(ev.get("status") or "")}</td>',
            f'<td class="r">{esc(str(ev.get("records") or 0))}</td>',
            f'<td class="note">{esc((ev.get("message") or "")[:90])}</td>'])
    return ('<div class="card"><h2>Recent uploads &amp; extraction outcome</h2>'
            '<div class="note">Per-upload extraction results (source/backend &amp; '
            'success/failure) from the durable import log. NET EUR/L data basis is '
            'unchanged; this card is operational telemetry only.</div>'
            + tbl(["When (UTC)", "File", "Supplier", "Outcome", "Records", "Detail"], rows))


def _humanize_age(secs):
    """Render a duration in seconds as a compact 'Xh Ym' / 'Ym' / 'Xs' string."""
    secs = int(secs)
    if secs >= 3600:
        return f"{secs // 3600}h {(secs % 3600) // 60}m"
    if secs >= 60:
        return f"{secs // 60}m"
    return f"{secs}s"

def _intake_queue_health_card():
    """Reliability telemetry card for the waiting room: oldest-pending-job age SLO and
    DLQ (failed/held) size with 24h growth. A failure here must never break the queue
    page — log and skip the card."""
    import waiting_room as IQ
    try:
        h = IQ.queue_health()
    except Exception as e:
        _log_exc("queue health", e)
        return ""
    slo = IQ.OLDEST_PENDING_SLO_HOURS
    age_s = h.get("oldest_pending_age_s")
    if age_s is None:
        age_html = '<span class="ok">no jobs in flight</span>'
    else:
        cls = "bad" if h.get("age_breach") else ""
        age_html = (f'<span class="{cls}">{esc(_humanize_age(age_s))}</span>'
                    if cls else esc(_humanize_age(age_s)))
    age_line = (f'<div>Oldest still-flowing job: {age_html} '
                f'<span class="note">(SLO {esc(str(slo))}h — a higher value means the '
                f'worker is stalled or starved)</span></div>')
    dlq = h.get("dlq", 0)
    dlq_cls = "bad" if h.get("dlq_breach") else "ok"
    growth = h.get("dlq_growth_24h", 0)
    growth_note = (f' <span class="bad">(+{esc(str(growth))} in 24h)</span>'
                   if growth and growth > 0 else "")
    dlq_line = (f'<div>Dead-letter (needs a human): '
                f'<span class="{dlq_cls}">{esc(str(dlq))}</span> '
                f'<span class="note">(failed {esc(str(h.get("failed", 0)))} / '
                f'held {esc(str(h.get("held", 0)))})</span>{growth_note}</div>')
    redrive = ('<div class="note">Redrive the dead-letter pile with the '
               '<b>Send / restart all</b> button above.</div>')
    return ('<div class="card"><h2>Queue health</h2>'
            + age_line + dlq_line + redrive + '</div>')


def _fmt_pct(x):
    """Render a 0..1 fraction as a whole-percent string; '—' when None (no data)."""
    if x is None:
        return "—"
    return f"{round(x * 100)}%"

def _fmt_dur(secs):
    """Humanise a duration in seconds (reusing _humanize_age); '—' when None."""
    if secs is None:
        return "—"
    return _humanize_age(secs)

def _intake_reliability_card():
    """Per-channel processing-reliability scorecard for the waiting room: success rate,
    retry rate, median duration and the top failure reason per channel, plus an overall
    failure-reason histogram. Read-only analytics over intake_jobs. A failure here must
    never break the queue page — log and skip the card."""
    import waiting_room as IQ
    try:
        sc = IQ.reliability_scorecard()
    except Exception as e:
        _log_exc("reliability scorecard", e)
        return ""
    channels = sc.get("channels") or []
    if not channels:
        return ('<div class="card"><h2>Processing reliability</h2>'
                '<p class="note">No jobs have run through the waiting room yet.</p></div>')
    rows = []
    for ch in channels:
        sr = ch.get("success_rate")
        sr_cls = "ok" if (sr is not None and sr >= 0.9) else ("bad" if sr is not None and sr < 0.5 else "")
        rr = ch.get("retry_rate")
        rr_cls = "bad" if (rr is not None and rr > 0.25) else ""
        terminal = ch.get("done", 0) + ch.get("failed", 0) + ch.get("held", 0)
        jobs_note = (f'<br><span class="note">{esc(str(ch.get("pending", 0)))} in flight</span>'
                     if ch.get("pending") else "")
        top_err = ch.get("top_error") or ""
        rows.append([
            f'<td>{esc(str(ch.get("channel", "")))}</td>',
            f'<td class="r">{esc(str(ch.get("total", 0)))}{jobs_note}</td>',
            (f'<td class="r {sr_cls}">{esc(_fmt_pct(sr))}</td>'
             f'<td class="note r">({esc(str(ch.get("done", 0)))}/{esc(str(terminal))})</td>'),
            f'<td class="r {rr_cls}">{esc(_fmt_pct(rr))}</td>',
            (f'<td class="r">{esc(_fmt_dur(ch.get("median_duration_s")))}</td>'
             f'<td class="note r">n={esc(str(ch.get("duration_n", 0)))}</td>'),
            f'<td class="note">{esc(top_err[:60])}</td>'])
    table = tbl(["Channel", "Jobs", "Success", "", "Retry", "Median dur", "", "Top error"], rows)
    # compact overall failure-reason histogram (across all failed + held jobs)
    fr = sc.get("failure_reasons") or []
    if fr:
        items = "".join(
            f'<li><span class="bad">{esc(str(cnt))}×</span> {esc(str(reason)[:80])}</li>'
            for reason, cnt in fr[:8])
        hist = (f'<h3>Failure reasons</h3><ul class="note" style="margin:4px 0">{items}</ul>')
    else:
        hist = '<p class="note">No failures recorded — every job has succeeded or is still in flight.</p>'
    return ('<div class="card"><h2>Processing reliability</h2>'
            '<div class="note">Per-channel (extractor) processing outcomes. Success rate is '
            'over terminal jobs only (done vs failed/held); retry rate is jobs that needed '
            'more than one attempt; median duration is finished − started.</div>'
            + table + hist + '</div>')

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
    # monitor_rows() floats stuck jobs (failed/held/waiting) to the top and attaches
    # the supplier/confidence the extractor resolved into the draft, so this one table
    # doubles as the upload-monitoring panel.
    for j in IQ.monitor_rows(limit=100):
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
        # supplier as resolved by extraction (only known once ready); show the draft
        # confidence alongside it as the extraction-quality signal.
        supplier = j.get("draft_supplier")
        if supplier:
            conf = j.get("draft_confidence")
            sup_cell = esc(supplier) + (
                f'<br><span class="note">conf: {esc(conf)}</span>' if conf else "")
        else:
            sup_cell = '<span class="note">—</span>'
        rows.append([
            f'<td>{j["id"]}</td><td>{esc(j["filename"] or "")}</td>',
            f'<td>{sup_cell}</td>',
            f'<td>{esc(j["backend"] or "auto")}</td><td>{esc(j["period"] or "")}</td>',
            f'<td>{esc(j["uploaded_by"] or "")}</td><td class="note">{esc(j["uploaded_at"] or "")}</td>',
            f'<td class="{stcls}">{statetxt}</td><td class="r">{j["attempts"]}</td>',
            f'<td class="note">{esc((j["error"] or "")[:80])}</td>',
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
            + _intake_queue_health_card()
            + _intake_reliability_card()
            + '<div class="card"><h2>Jobs — live monitor</h2>'
            + '<div class="note">Stuck jobs (failed / held / waiting) are listed first, '
              'then newest. <b>Supplier</b> and its confidence appear once extraction has '
              'produced a draft.</div>'
            + (tbl(["#", "File", "Supplier", "Extractor", "Period", "By", "Uploaded",
                    "Status", "Tries", "Last error", "Action"], rows) if rows
               else '<p class="note">The waiting room is empty.</p>')
            + '</div>'
            + _intake_extract_outcomes())
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
    _stash_draft(token, draft)
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

@app.route("/imports")
def imports():
    """Data-import report: every upload / extraction / statement registration with its
    outcome (received / success / partial / failed). Filter by channel, status, client,
    supplier and date. Capability: data_import."""
    import import_log as IL
    fl = IL.filters()
    chan = request.args.get("channel") or None
    status = request.args.get("status") or None
    client = request.args.get("client") or None
    supplier = request.args.get("supplier") or None
    df = request.args.get("date_from", ""); dt = request.args.get("date_to", "")
    rows = IL.recent(channel=chan, status=status, client=client, supplier=supplier,
                     date_from=df or None, date_to=dt or None, limit=1000)
    summ = IL.summary(30)
    def sel(name, opts, cur):
        o = '<option value="">all</option>' + "".join(
            f'<option {"selected" if v==cur else ""}>{esc(v)}</option>' for v in opts)
        return f'<label>{esc(name)}<select name="{esc(name)}">{o}</select></label>'
    form = ('<form class="f" method="get">'
            + sel("channel", fl["channels"], chan or "")
            + sel("status", ["received", "success", "partial", "failed"], status or "")
            + sel("client", fl["clients"], client or "")
            + sel("supplier", fl["suppliers"], supplier or "")
            + f'<label>from<input type="date" name="date_from" value="{esc(df)}"></label>'
            + f'<label>to<input type="date" name="date_to" value="{esc(dt)}"></label>'
            + '<button>Filter</button><a href="/imports" style="align-self:end;padding:8px 12px;font-size:13px">Reset</a></form>')
    scls = {"success": "ok", "received": "", "partial": "", "failed": "bad"}
    trs = [[f"<td class=note>{esc(r['ts'])}</td><td>{esc(r['actor'] or '')}</td>",
            f"<td>{esc(r['channel'])}</td><td class='{scls.get(r['status'],'')}'>{esc(r['status'])}</td>",
            f"<td>{esc(r['client'] or '')}</td><td>{esc(r['supplier'] or '')}</td>",
            f"<td>{esc(r['source_name'] or '')}</td><td class=r>{r['records']}</td>",
            f"<td class=note>{esc((r['message'] or '')[:80])}</td>"] for r in rows]
    body = (form
            + '<div class="kpis">'
            + f'<div class="kpi"><div class="v">{summ["received"]}</div><div class="l">received (30d)</div></div>'
            + f'<div class="kpi"><div class="v ok">{summ["success"]}</div><div class="l">success</div></div>'
            + f'<div class="kpi"><div class="v">{summ["partial"]}</div><div class="l">partial</div></div>'
            + f'<div class="kpi"><div class="v {"bad" if summ["failed"] else ""}">{summ["failed"]}</div><div class="l">failed</div></div></div>'
            + f'<div class="card"><h2>Data imports — {len(rows)} event(s)</h2>'
            + tbl(["Time (UTC)", "User", "Channel", "Status", "Client", "Supplier",
                   "Source file", "Records", "Message"], trs)
            + '<div class="note">Every import is logged: <b>received</b> (file archived in the '
              'data lake on arrival), <b>success</b>/<b>partial</b>/<b>failed</b> extraction and '
              'statement registration. Append-only audit trail.</div></div>')
    try:
        rel = IL.reliability(30)
        def _rate(v):
            return f"{v*100:.0f}%" if v is not None else "—"
        crows = [[f"<td>{esc(c['channel'])}</td><td class=r>{c['total']}</td>",
                  f"<td class=r ok>{c['success']}</td><td class=r>{c['partial']}</td>",
                  f"<td class=r {'bad' if c['failed'] else ''}>{c['failed']}</td>",
                  f"<td class=r>{esc(_rate(c['success_rate']))}</td>"]
                 for c in rel["by_channel"]]
        srows = [[f"<td>{esc(s['supplier'])}</td><td class=r>{s['total']}</td>",
                  f"<td class=r>{esc(_rate(s['success_rate']))}</td>"]
                 for s in rel["by_supplier"]]
        body += ('<div class="card"><h2>Reliability by channel &amp; supplier (last 30d)</h2>'
                 + '<div style="display:flex;gap:24px;flex-wrap:wrap;align-items:flex-start">'
                 + '<div>' + tbl(["Channel", "Total", "Success", "Partial", "Failed", "Success rate"], crows) + '</div>'
                 + '<div>' + tbl(["Supplier (top 20)", "Total", "Success rate"], srows) + '</div></div>'
                 + '<div class="note">Success rate = success / (success + partial + failed) — '
                   'extraction outcomes only; <b>received</b> (the arrival marker) is excluded from '
                   'the denominator. Suppliers capped to the top 20 by volume.</div></div>')
    except Exception as e:
        _log_exc("imports reliability card", e)
    return page(body, "imp")

@app.route("/files", methods=["GET", "POST"])
def files_archive():
    """The permanent file archive (data lake): uploaded files and AI-processed outputs.
    Files are kept forever — removed only by an explicit delete here or when integrity
    verification flags them corrupt/missing. Capability: data_import."""
    import data_lake as DL
    banner = ""
    if request.method == "POST":
        if request.form.get("__act") == "delete":
            if DL.delete(int(request.form.get("file_id", "0"))):
                banner = '<div class="card"><b class="ok">File deleted (explicit user action).</b></div>'
        elif request.form.get("__act") == "verify":
            _rows, vs = DL.verify()
            cls = "bad" if (vs["corrupt"] or vs["missing"]) else "ok"
            banner = (f'<div class="card"><b class="{cls}">Integrity: {vs["ok"]}/{vs["total"]} OK, '
                      f'{vs["corrupt"]} corrupt, {vs["missing"]} missing.</b> '
                      + ('Corrupt/missing files can be deleted and re-uploaded.' if cls == "bad"
                         else 'All archived files verified intact.') + '</div>')
    kind = request.args.get("kind") or None
    rows = DL.query(kind=kind, limit=500)
    c = DL.counts()
    kinds = sorted(c)
    ksel = ('<form class="f" method="get"><label>kind<select name="kind" onchange="this.form.submit()">'
            '<option value="">all</option>'
            + "".join(f'<option {"selected" if k==(kind or "") else ""}>{esc(k)}</option>' for k in kinds)
            + '</select></label></form>')
    trs = []
    for r in rows:
        delf = ('<form method="post" style="display:inline" onsubmit="return confirm(\'Delete this file permanently?\')">'
                + _csrf_input() + f'<input type="hidden" name="file_id" value="{r["id"]}">'
                + '<button name="__act" value="delete" style="background:var(--bad)">Delete</button></form>')
        trs.append([f"<td>{r['id']}</td><td>{esc(r['kind'])}</td><td>{esc(r['supplier'] or '')}</td>",
                    f"<td>{esc(r['period'] or '')}</td><td>{esc(r['source_name'] or r['filename'] or '')}</td>",
                    f"<td class=r>{(r['size'] or 0):,}</td><td class=note>{esc(r['created_at'])}</td>",
                    f"<td>{delf}</td>"])
    verifyf = ('<form method="post" style="display:inline">' + _csrf_input()
               + '<button name="__act" value="verify">✓ Verify integrity</button></form>')
    body = (banner
            + '<div class="card"><h2>File archive (data lake)</h2>'
            + '<div class="kpis">'
            + "".join(f'<div class="kpi"><div class="v">{v["files"]}</div>'
                      f'<div class="l">{esc(k)} ({v["bytes"]:,} B)</div></div>' for k, v in c.items())
            + '</div>'
            + '<div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin:8px 0">'
            + ksel + verifyf + '</div>'
            + (tbl(["#", "Kind", "Supplier", "Period", "Source file", "Size", "Archived", ""], trs)
               if trs else '<p class="note">No files archived yet.</p>')
            + '<div class="note"><b>Every uploaded file is archived here permanently on arrival</b> '
              '(SHA-256, same storage as the PDF vault). A file is only ever removed by an explicit '
              '<b>Delete</b> above, or after <b>Verify integrity</b> flags it corrupt/missing — never '
              'lost automatically. Whether a file\'s data is processed/used is a separate concern.</div></div>')
    try:
        pri = DL.parser_priority()
        if pri:
            prows = [[f"<td>{esc(p['supplier'])}</td><td class=r>{p['ai_count']}</td>",
                      f"<td class=r>{p['high']}</td><td class=r>{p['medium']}</td>"
                      f"<td class=r>{p['low']}</td><td class=r>{p['unknown']}</td>",
                      f"<td class=note>{esc(', '.join(p['backends']))}</td>"
                      f"<td class=r><b>{p['weighted_score']}</b></td>"]
                     for p in pri]
            body += ('<div class="card"><h2>Parser-build priorities (AI-extracted volume by supplier)</h2>'
                     + tbl(["Supplier", "AI extractions", "High", "Medium", "Low",
                            "Unknown", "Backend(s)", "Priority"], prows)
                     + '<div class="note">Suppliers with many low-confidence AI extractions are the '
                       'best candidates for a deterministic <code>parse_&lt;x&gt;()</code> in '
                       'extract.py — each one removes those PDFs from the AI path. Priority = '
                       '<code>low*3 + unknown*2 + medium*2 + high*1</code>.</div></div>')
    except Exception as e:
        _log_exc("files_archive/parser_priority", e)
    return page(body, "fil")

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
    rows, orphans = invoice_control.control_summary(period)   # render path: read-only, no write/audit churn
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
    # register-failure reconcile (D4): documents vaulted in-request whose registry write
    # (enqueued kind='register') never landed — surfaced here read-only so they can be
    # re-registered. Basis = statement_invoices (the complete registry, incl. vat=0).
    orphan_docs = invoice_control.unregistered_vaulted_documents()
    od_trs = [[f"<td>{esc(o['supplier'])}</td><td>{esc(o['invoice_ref'])}</td>",
               f"<td>{esc(o.get('entity') or '—')}</td><td class=r>{o['n_docs']}</td>"]
              for o in orphan_docs]
    orphan_html = (('<div class="card"><h2><span class="bad">Vaulted documents with no '
                    'registered invoice</span></h2>'
                    + tbl(["Supplier", "Invoice", "Entity", "#docs"], od_trs)
                    + '<div class="note">These source PDFs are in the document vault but their '
                      'invoices are NOT in the statement registry — the registration job likely '
                      'failed or was lost (check the intake queue). Re-register the statement to '
                      'link them. Read-only reconcile against statement_invoices.</div></div>')
                   if orphan_docs else "")
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
    body = (banner + stmt_html + orphan_html + reg_form + f'<form class="f" method="get"><label>Period (YYYY-MM)'
            f'<input name="period" value="{esc(period)}"></label><button>Run control</button></form>'
            f'<div class="card"><h2>Invoice receipt control — {esc(period)}: '
            + (f'<span class="bad">{miss} missing to chase</span>' if miss else '<span class="ok">complete</span>')
            + '</h2>'
            + tbl(["Supplier","Country","Slot","Expected cadence","Invoice received","Status","Note"], trs)
            + (f'<h2 style="margin-top:12px">Orphan transactions (not covered by any invoice)</h2><ul>{orph}</ul>' if orphans else '')
            + '<div class="note">Expectation = supplier cadence (every 14 / 30 days, from suppliers.db) '
              'x activity from transactions. NO ACTIVITY = no transactions, no invoice expected (OK). '
              'This view is read-only; results persist (audited) in invoice_receipt_control only on the '
              'monthly-close run. Waive a slot via Data manager (set waived=1).</div></div>')
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
        + _peer_card(grain)
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

def _peer_card(grain):
    """Per-entity vs peer (M1): each entity's effective NET €/L against the equal-weight
    MEDIAN of the OTHER entities in the same country/period, and the addressable € where
    it pays above peer. Fills the "no-benchmark" gap from the pooled invoice data itself.
    Min-cohort suppression labels cells with too few other entities."""
    import pricing_intelligence as PI
    try:
        rows, summ = PI.peer_benchmark(None, grain)
    except Exception as e:
        _log_exc("peer benchmark", e)
        return ""
    # addressable € by country (reuse svg_hbars) — only countries with addressable spend
    by_ctry = {}
    for r in rows:
        if r.get("addressable_eur"):
            by_ctry[r["country"]] = by_ctry.get(r["country"], 0.0) + r["addressable_eur"]
    bars = svg_hbars([(c, v) for c, v in sorted(by_ctry.items(), key=lambda x: -x[1]) if v > 0],
                     unit=" €", fmt=",.0f", color="#c8102e")
    trs = []
    for r in rows[:80]:
        if r["suppressed"]:
            trs.append([f"<td>{esc(r['entity'])}</td><td>{esc(r['country'])}</td>",
                        f"<td>{esc(r['bucket'])}</td><td class=r>{r['eff_price']:.3f}</td>",
                        "<td class='r note' colspan=4>cohort too small "
                        f"({r['peers']} other entit{'y' if r['peers']==1 else 'ies'}) — suppressed</td>"])
        else:
            cls = "bad" if r["gap"] and r["gap"] > 0 else ("ok" if r["gap"] and r["gap"] < 0 else "")
            trs.append([f"<td>{esc(r['entity'])}</td><td>{esc(r['country'])}</td>",
                        f"<td>{esc(r['bucket'])}</td><td class=r>{r['eff_price']:.3f}</td>",
                        f"<td class=r>{r['peer_median']:.3f}</td><td class='r {cls}'>{r['gap']:+.3f}</td>",
                        f"<td class=r>{r['qty']:,.0f}</td>",
                        f"<td class='r {'bad' if r['addressable_eur'] else ''}'>{r['addressable_eur']:,.0f}</td>"])
    return ('<div class="card"><h2>Per-entity vs peer (your entities, pooled)</h2>'
            '<div class="kpis">'
            f'<div class="kpi"><div class="v bad">EUR {summ["total_addressable_eur"]:,.0f}</div>'
            '<div class="l">addressable vs peer median</div></div>'
            f'<div class="kpi"><div class="v">{summ["cells"]}</div>'
            '<div class="l">country/period cells</div></div>'
            f'<div class="kpi"><div class="v">{summ["suppressed_cells"]}</div>'
            '<div class="l">suppressed (cohort too small)</div></div></div>'
            + ('<h3 style="margin:6px 0">Addressable by country (EUR)</h3>' + bars if by_ctry else '')
            + (tbl(["Entity", "Country", "Period", "Eff €/L", "Peer median",
                    "Gap", "Litres", "Addressable €"], trs)
               if trs else '<p class="note">Need two or more of your entities in the same '
               'country/period to compare.</p>')
            + f'<p style="margin-top:10px"><a href="/export/peer?grain={esc(grain)}">'
            '⬇ Export per-entity vs peer (Excel)</a></p>'
            '<div class="note">Peer = the equal-weight (per-entity) <b>median</b> of the OTHER '
            'entities\' effective NET €/L in the same country/period — the entity itself is '
            'excluded. Where an entity pays above that median the gap × litres is addressable '
            'spend. Cells with fewer than two other entities are <b>suppressed</b> so no single '
            'entity is singled out. All prices NET EUR/L, final (VAT excluded, rebates applied).'
            '</div></div>')

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
        try:
            iv = float(p.get("interval_hours") or 0)
        except (TypeError, ValueError):
            iv = 0.0
        sched = (f'every {esc(("%g" % iv))} h' if iv > 0
                 else '<span class="note">manual</span>')
        rows.append([f"<td>{esc(p['supplier'])}</td><td>{esc(p['entity'] or '—')}</td>",
                     f"<td>{esc(p['kind'])}</td><td>{'on' if p['enabled'] else 'off'}</td>",
                     f"<td>{sched}</td>",
                     f"<td>{creds}</td><td class=note>{last}</td><td>{scrape_btn}</td>"])
    table = (tbl(["Supplier", "Entity", "Kind", "Enabled", "Schedule", "Credentials", "Last run", ""], rows)
             if rows else '<p class="note">No portals configured yet.</p>')
    sched_on = _auth.get_setting("scrape_scheduler_enabled", "0") == "1"
    sched_state = ('<div class="note" style="margin-top:8px">Scheduled pulls (global): '
                   + ('<b class="ok">ON</b>' if sched_on else '<b class="bad">OFF</b>')
                   + ' — scheduled portal pulls run ONLY for enabled portals that have a '
                     'non-zero schedule interval AND stored credentials, and ONLY for '
                     'portals you are authorized to access.</div>')
    admin_forms = ""
    if is_admin:
        admin_forms = (
            sched_state +
            '<form method="post" action="/pricing/portal" class="f" style="margin-top:6px">' + _csrf_input()
            + '<label><input type="checkbox" name="on"'
            + (' checked' if sched_on else '') + '> enable scheduled pulls (global)</label>'
            + '<button name="__act" value="set_scrape_scheduler">Save scheduler state</button></form>'
            '<details style="margin-top:10px"><summary><b>Add / update a portal (admin)</b></summary>'
            '<form method="post" action="/pricing/portal" class="f" style="margin-top:8px">' + _csrf_input()
            + '<label>supplier code<input name="supplier" required style="width:110px"></label>'
            + '<label>kind<select name="kind">'
            + ''.join(f'<option>{k}</option>' for k in ("demo", "http_json", "csv", "custom"))
            + '</select></label>'
            + '<label>base URL<input name="base_url" style="width:220px" placeholder="https://portal.supplier.com"></label>'
            + '<label><input type="checkbox" name="enabled" checked> enabled</label>'
            + '<label>schedule (hours, 0 = manual)<input name="interval_hours" type="number" '
              'min="0" step="0.5" value="0" style="width:90px"></label>'
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
            'benchmark stays current without manual CSV uploads. <b>Scrape now</b> does not run in '
            'the request — it <b>queues a fetch on the worker tier</b> (per-supplier rate-limited '
            'with a circuit-breaker); watch the <b>Last run</b> column / the intake queue for the '
            'result. Credentials are encrypted at rest; use only portals you are authorized to '
            'access. Live portals need outbound network — on a locked-down box, run the worker on a '
            'connected machine or keep using CSV upload.</div>')
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
            # NEVER fetch inline in the request — park a fileless FETCH job on the worker
            # tier, where the per-supplier rate-limiter/breaker governs it. The request
            # MUST NOT block on the fetch or call PS.scrape itself.
            import waiting_room as IQ
            supplier = request.form["supplier"].strip()
            entity = request.form.get("entity", "").strip()
            date_from = request.form.get("date_from") or None
            date_to = request.form.get("date_to") or None
            jid, st = IQ.enqueue_fetch(supplier, entity, date_from, date_to,
                                       session.get("user", "system"))
            banner = (f'<div class="card"><b class="ok">Fetch queued for {esc(supplier)} / '
                      f'{esc(entity or "—")} — it runs on the worker tier (rate-limited); '
                      f'watch the portal Last-run column / the intake queue.</b></div>')
        elif act == "set_scrape_scheduler" and is_admin:
            on = bool(request.form.get("on"))
            _auth.set_setting("scrape_scheduler_enabled", "1" if on else "0")
            state = "ON" if on else "OFF"
            banner = (f'<div class="card"><b class="ok">Scheduled portal pulls are now '
                      f'{state}.</b> Scheduled pulls only run for <b>enabled</b> portals '
                      f'with a <b>non-zero interval</b> and <b>stored credentials</b>, and '
                      f'only for portals you are authorized to access.</div>')
        elif act == "save_config" and is_admin:
            cfg_raw = request.form.get("config", "").strip() or "{}"
            import json as _json
            try:
                iv = float(request.form.get("interval_hours") or 0)
            except (TypeError, ValueError):
                iv = 0.0
            PS.set_config(request.form["supplier"].strip(), request.form.get("kind", "demo"),
                          base_url=request.form.get("base_url", "").strip(),
                          config=_json.loads(cfg_raw),
                          enabled=bool(request.form.get("enabled")),
                          interval_hours=iv)
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

@app.route("/export/peer")
def export_peer():
    import pricing_intelligence as PI
    path = PI.peer_benchmark_workbook(None, request.args.get("grain", "month"))
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


@app.route("/intel")
def intel():
    """Consolidated Savings & Intelligence surface — avoidable overpay + recoverable
    contract breaches + anomalies in ONE place (the indirect-monetization hook).
    Fuel-cost intelligence ONLY: NO VAT/claim/recovery figures (those live on the
    admin-only recovery surface). All prices NET EUR/L, final, VAT excluded."""
    import savings_intel
    period = request.args.get("period") or None
    try:
        s = savings_intel.summary(period)
    except Exception as e:
        _log_exc("savings intel summary", e)
        return page('<div class="card"><h2>Savings &amp; Intelligence</h2>'
                    '<p class="bad">Could not build the intelligence summary — see the '
                    'admin error log.</p></div>', "int")
    # by-country overpay bar (reuse svg_hbars) — only countries with avoidable overpay
    bars = svg_hbars([(c["country"], c["overpay_eur"]) for c in s["by_country"]
                      if c["overpay_eur"] > 0], unit=" €", fmt=",.0f", color="#c8102e")
    crows = []
    for c in s["by_country"]:
        crows.append([
            f"<td>{esc(c['country'])}</td>",
            f"<td class='r bad'>{c['overpay_eur']:,.0f}</td>",
            f"<td class='r ok'>{c['recover_eur']:,.0f}</td>",
            f"<td class=r><b>{c['addressable_eur']:,.0f}</b></td>"])
    arows = []
    for a in s["top_actions"][:30]:
        arows.append([
            f"<td>{esc(a['kind'])}</td>",
            f"<td>{esc(a['country'])}</td>",
            f"<td>{esc(a['detail'])}</td>",
            f"<td class=r><b>{a['eur']:,.0f}</b></td>"])
    body = (
        '<div class="card"><h2>Savings &amp; Intelligence</h2>'
        '<div class="note">Fuel-cost intelligence: avoidable overpay (route volume to the '
        'cheaper supplier you already use) + recoverable contract breaches + anomalies, in '
        'one place. The overpay/peer figures are <b>diesel-focused</b> (the detector default '
        'product group) — other product groups are not included. All prices NET EUR/L, final '
        '(VAT excluded, rebates applied). VAT/claim figures are kept on the admin-only '
        'Recovery page.</div>'
        f'<div class="note">Period: {esc(s["period"] or "all loaded")}</div>'
        '<div class="kpis">'
        f'<div class="kpi"><div class="v bad">EUR {s["avoidable_overpay_eur"]:,.0f}</div>'
        '<div class="l">avoidable overpay</div></div>'
        f'<div class="kpi"><div class="v ok">EUR {s["recoverable_contract_eur"]:,.0f}</div>'
        '<div class="l">recoverable contract breaches</div></div>'
        f'<div class="kpi"><div class="v">{s["anomaly_count"]}</div>'
        '<div class="l">anomalies flagged</div></div>'
        f'<div class="kpi"><div class="v">EUR {s["total_addressable_eur"]:,.0f}</div>'
        '<div class="l">total addressable</div></div></div>'
        f'<p style="margin-top:6px"><a href="/export/intel'
        + (f'?period={esc(s["period"])}' if s["period"] else '') +
        '">⬇ Export Savings &amp; Intelligence (Excel)</a></p></div>'
        '<div class="card"><h2>Avoidable overpay by country (EUR)</h2>'
        + bars + '</div>'
        '<div class="card"><h2>Addressable by country</h2>'
        + tbl(["Country", "Avoidable overpay", "Recoverable contract", "Addressable EUR"], crows)
        + '</div>'
        '<div class="card"><h2>Top actions (biggest € first)</h2>'
        + tbl(["Opportunity", "Country", "Detail", "EUR"], arows)
        + '<div class="note">Anomalies carry no € — they are surfaced as the count above and '
        'on the Anomalies page.</div></div>')
    return page(body, "int")


@app.route("/export/intel")
def export_intel():
    import savings_intel, reports
    s = savings_intel.summary(request.args.get("period") or None)
    path = reports.savings_intel_workbook(s)
    return send_file(path, as_attachment=True, download_name=os.path.basename(path),
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

@app.route("/recovery", methods=["GET", "POST"])
def recovery():
    import vat_refund as VR, customer_master as CD, money
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
    elif request.method == "POST" and request.form.get("__act") == "advance":
        # advance the claim's workflow code (4 invoice fee / 4A credit / 5 closed, …)
        con = VR.connect()
        ok, res = VR.set_status_code(con, request.form.get("entity", ""),
                                     request.form.get("country", ""),
                                     request.form.get("period", ""),
                                     request.form.get("to", ""))
        con.close()
        banner = f'<div class="card"><b class="{"ok" if ok else "bad"}">{esc(res)}</b></div>'
    elif request.method == "POST" and request.form.get("__act") == "record_payment":
        # record the ACTUALLY-refunded amount; the fee re-bills on the paid amount
        con = VR.connect()
        ok = False; res = "could not record payment"
        try:
            ok, res = VR.record_payment(con, request.form.get("entity", ""),
                                        request.form.get("country", ""),
                                        request.form.get("period", ""),
                                        request.form.get("amount", ""),
                                        request.form.get("date", "") or None)
        except Exception as e:
            _log_exc("recovery/record_payment", e)
            res = "could not record payment"
        finally:
            con.close()
        banner = f'<div class="card"><b class="{"ok" if ok else "bad"}">{esc(res)}</b></div>'
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
            # EUR-threshold decision: quantize HALF_UP via money (mirrors
            # customer_master.compute_fee), not bare round().
            pct_fee = money.f2((r.get("fee_pct") or 0) / 100 * vat)
            basis = "percent" if pct_fee >= money.f2(r.get("fee_min") or 0) else "minimum"
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
        # audit-ready evidence pack: the claim's SHA-256-verified original documents,
        # bundled with an integrity MANIFEST + cover summary (POST + CSRF, audited).
        inv_cell += ('<form method="post" action="/export/evidence" style="margin:2px 0 0">'
                     + _csrf_input() + hid
                     + '<button style="font-size:11px;padding:3px 8px">⬇ Evidence pack</button></form>')
        # workflow cell: the claim's status code + suggested next step (after 3A the
        # payout route decides: 4 invoice the fee / 4A credit; then 5 closed)
        code = r.get("status_code") or {"submitted": "2", "approved": "3", "paid": "3A"}.get(r["status"], "")
        nxt = r.get("next_code")
        wf = f'<b>{esc(code)}</b> {esc(VR.STATUS_LABELS.get(code, ""))}'
        bits = []
        if r.get("decision_date"):
            bits.append(f"decision {esc(r['decision_date'])}")
        if r.get("action_deadline"):
            bits.append(f'<b class="bad">deadline {esc(r["action_deadline"])}</b>')
        if r.get("status_note"):
            bits.append(esc(r["status_note"]))
        if bits:
            wf += f'<div class="note">{" · ".join(bits)}</div>'
        if nxt:
            wf += ('<form method="post" style="margin:2px 0 0">' + _csrf_input() + hid
                   + f'<input type="hidden" name="to" value="{nxt}">'
                   + f'<button name="__act" value="advance" style="font-size:11px;padding:3px 8px">'
                     f'→ {nxt} {esc(VR.STATUS_LABELS[nxt])}</button></form>')
        # Record the actually-refunded amount on an open (not yet paid) claim; the fee
        # re-bills on the paid amount via vat_refund.record_payment.
        if r["status"] in ("submitted", "approved"):
            wf += ('<form method="post" style="margin:2px 0 0">' + _csrf_input() + hid
                   + '<input name="amount" type="number" step="0.01" min="0" required '
                     'placeholder="paid EUR" style="width:90px;font-size:11px">'
                   + '<input name="date" type="date" style="font-size:11px">'
                   + '<button name="__act" value="record_payment" '
                     'style="font-size:11px;padding:3px 8px">Record payment</button></form>')
        trs.append([f"<td>{esc(r['entity'])}</td><td>{esc(r['country'])}</td><td>{esc(r['period'])}</td>",
                    f"<td class=r>{vat:,.2f}</td>",
                    f"<td class=r>{fee:,.2f}</td><td class='{'ok' if billed else 'note'}'>{esc(basis)} · {'charged' if billed else 'pending'}</td>",
                    f"<td class=note>{settle}</td>",
                    f"<td>{wf}</td>",
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
            + tbl(["Entity","Country","Period","VAT EUR","Our fee","Settlement","Workflow (2→5)",
                   "Age (days)","Paid","Fee invoice / report"], trs)
            + '<div class="note">Fee rate is frozen at submission and <b>charged when the refund is '
              'paid</b>. Settlement depends on where the refund lands (set per customer): to the '
              '<b>customer</b> → <b>4 invoice the fee</b>; to <b>us</b> → <b>4A credit</b> (deduct '
              'the fee, remit the net). The workflow column suggests the next step — after the money '
              'arrives (3A) advance to 4/4A, issue the fee invoice, then close (5). Age over 120 '
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
        code = c.get("code", "")
        verdict = (f'<span class="ok">{esc(code)} READY ✓</span>' if c["ready"]
                   else f'<span class="bad">{esc(code)} {esc(c.get("code_label", "BLOCKED"))} ✗</span>')
        dd = c.get("deadline_days")
        dcls = "bad" if isinstance(dd, int) and dd <= 90 else "note"
        dl = (f'<span class="{dcls}">{esc(c.get("deadline", ""))}'
              + (f" ({dd}d)" if isinstance(dd, int) else "") + "</span>")
        trs1.append([f"<td>{esc(c['entity'])}</td><td>{esc(c['country'])}</td><td>{esc(c['period'])}</td>",
                     f"<td class=r>{(c['vat_eur'] or 0):,.2f}</td>",
                     f"<td>{verdict}</td><td class='note'>{esc('; '.join(c['issues']))}</td>",
                     f"<td>{dl}</td>"])
    trs2 = []
    open_vat = 0.0
    for c in op:
        open_vat += c["vat_eur"] or 0
        agecls = "bad" if isinstance(c["age_days"], int) and c["age_days"] > 120 else ""
        st = f"<b>{esc(c.get('code', ''))}</b> {esc(c.get('code_label', c['status']))}"
        if c.get("action_deadline"):
            st += f' <span class="bad">deadline {esc(c["action_deadline"])}</span>'
        if c.get("note"):
            st += f' <span class="note">{esc(c["note"])}</span>'
        trs2.append([f"<td>{esc(c['entity'])}</td><td>{esc(c['country'])}</td><td>{esc(c['period'])}</td>",
                     f"<td class=r>{(c['vat_eur'] or 0):,.2f}</td>",
                     f"<td>{st}</td><td>{esc(c['submitted'] or '')}</td>",
                     f"<td class='{agecls}'>{c['age_days'] if c['age_days'] != '' else ''}</td>"])
    body = (f'<form class="f" method="get"><label>Year<input name="year" value="{esc(year)}" style="width:80px"></label>'
            f'<a href="/export/readiness?year={esc(year)}" style="align-self:end;padding:8px 12px;font-size:13px">⬇ Export (Excel)</a></form>'
            + '<div class="kpis">'
            + f'<div class="kpi"><div class="v ok">{nready}</div><div class="l">ready to submit</div></div>'
            + f'<div class="kpi"><div class="v {"bad" if len(ts)-nready else ""}">{len(ts)-nready}</div><div class="l">blocked</div></div>'
            + f'<div class="kpi"><div class="v">{len(op)}</div><div class="l">open claims</div></div>'
            + f'<div class="kpi"><div class="v">EUR {open_vat:,.0f}</div><div class="l">open VAT</div></div></div>'
            + '<div class="card"><h2>Ready to submit — can we file this claim?</h2>'
            + tbl(["Entity", "Country", "Period", "VAT EUR", "Stage (1A→1E)", "Blocking reasons",
                   "Filing deadline"], trs1)
            + '<div class="note">A claim is <b>1E ready</b> when the system checklist passes — '
              'submission checklist complete (contract, customer data, bank account, NACE, trade '
              'register, power of attorney), all invoice refs resolved, all documents attached, '
              'no duplicate locks, threshold met, and the <b>period has ended</b>. Then submit on '
              'the VAT refunds page. Filing deadline (30 Sep of the following year) turns red '
              'inside 90 days.</div></div>'
            + '<div class="card"><h2>Open claims — submitted, awaiting refund</h2>'
            + tbl(["Entity", "Country", "Period", "VAT EUR", "Workflow status", "Submitted",
                   "Age (days)"], trs2)
            + '<div class="note">Open = submitted/awaiting decision. A red deadline is an open '
              'document request (2B) or appeal (3D). Age over 120 days flagged red — chase the '
              'tax authority.</div></div>')
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

@app.route("/receivables", methods=["GET", "POST"])
def receivables():
    """ADMIN-ONLY VAT receivables & payout forecast — an INTERNAL, data-only view
    (no lending, no outward send). Surfaces the under-used VAT-lifecycle data
    (DATA_ARCHITECTURE.md #2 cycle-time/forecast, #9 realization): per-claim
    route-aware figures via settlement() — refund receivable (VAT owed by the state,
    what's aged), frozen agency fee, and customer net (0 on the customer-payout route,
    VAT−fee on the deduct route) — open-receivable aging, median submitted→paid days per
    refund country, the realization rate (paid/claimed) per jurisdiction, and a two-flow
    open-receivable cash forecast (refund vs fee, never summed). NET (VAT-excluded) EUR.

    Also hosts the ADDITIVE embedded-finance section (finance.py): the financeable
    receivable base (= the same submitted/approved outstanding total) and the advance
    economics at the configured terms. The default provider is NULL — no money moves,
    figures are informational, and NOTHING here touches a VAT figure/gate/lock/lifecycle."""
    import vat_refund as VR
    import finance
    year = request.args.get("year", "2026")
    fin_banner = ""
    if request.method == "POST" and request.form.get("__act") == "set_finance_terms":
        try:
            _auth.set_setting("finance_advance_pct", request.form.get("advance_pct", "").strip())
            _auth.set_setting("finance_fee_pct", request.form.get("fee_pct", "").strip())
            _auth.set_setting("finance_provider", (request.form.get("provider", "none") or "none").strip())
            fin_banner = '<div class="card"><b class="ok">Financing terms saved.</b></div>'
        except Exception as e:
            _log_exc("receivables/set_finance_terms", e)
            fin_banner = '<div class="card"><b class="bad">Could not save financing terms.</b></div>'
    elif request.method == "POST" and request.form.get("__act") == "record_advance":
        try:
            ent = request.form.get("entity", ""); cty = request.form.get("country", "")
            per = request.form.get("period", ""); vat = float(request.form.get("vat_eur", "0") or 0)
            t = finance.terms()
            q = finance.quote(vat, t)
            res = finance.request_advance(f"{ent}|{cty}|{per}", q["advance_eur"], q["fee_eur"],
                                          actor=session.get("user", "admin"))
            fin_banner = (f'<div class="card"><b class="{"ok" if res.get("ok") else "bad"}">'
                          + esc(res.get("message") or "") + '</b></div>')
        except Exception as e:
            _log_exc("receivables/record_advance", e)
            fin_banner = '<div class="card"><b class="bad">Could not record advance intent.</b></div>'
    fc = VR.receivables_forecast(year)
    rows = fc["rows"]; forecast = fc["forecast"]; aging = fc["aging"]
    ct = fc["cycle_time"]; realization = fc["realization"]
    _band_cls = {"0-30": "ok", "30-60": "", "60-90": "bad", "90+": "bad"}
    trs = []
    for r in rows:
        band = r.get("aging_band") or ""
        agecls = _band_cls.get(band, "")
        refund = r.get("refund_receivable_eur") or 0
        fee = r.get("fee_eur") or 0
        net = r.get("net_to_customer_eur") or 0
        route = r.get("route") or "customer"
        route_lbl = "deduct (to us)" if route == "us" else "direct (to customer)"
        paid_amt = r.get("paid_amount")
        trs.append([
            f"<td>{esc(r['entity'])}</td><td>{esc(r['country'])}</td><td>{esc(r['period'])}</td>",
            f"<td><b>{esc(r.get('status_code') or '')}</b> {esc(r.get('status_label') or '')}</td>",
            f"<td>{esc(route_lbl)}</td>",
            f"<td class=r><b>{refund:,.2f}</b></td>",
            f"<td class=r>{fee:,.2f}</td>",
            f"<td class=r>{net:,.2f}</td>",
            f"<td class=r>{(paid_amt or 0):,.2f}</td>" if paid_amt is not None else "<td class='r note'>—</td>",
            f"<td>{esc(r.get('submitted') or '')}</td><td>{esc(r.get('paid') or '')}</td>",
            f"<td class='{agecls}'>{r['age_days'] if isinstance(r.get('age_days'), int) else ''}"
            + (f" <span class='note'>{esc(band)}</span>" if band else "") + "</td>"])
    # aging-by-EUR bar (open expected payout per band)
    by_band = aging["by_band"]
    bars = svg_hbars([(b, by_band[b]["eur"]) for b in ("0-30", "30-60", "60-90", "90+")],
                     unit=" EUR", fmt=",.0f", color="#0e5fa8")
    # cycle-time + realization per country
    ct_rows = []
    by_ctry = ct["by_country"]
    countries = sorted(set(by_ctry) | {c for c in realization if c != "overall"})
    for c in countries:
        rz = realization.get(c, {})
        med = by_ctry.get(c)
        rate = rz.get("rate")
        ratecls = "bad" if (rate is not None and rate < 0.95) else "ok" if rate is not None else ""
        ct_rows.append([
            f"<td>{esc(c)}</td>",
            f"<td class=r>{med:.0f}</td>" if med is not None else "<td class='r note'>—</td>",
            f"<td class=r>{(rz.get('claimed') or 0):,.2f}</td>",
            f"<td class=r>{(rz.get('paid') or 0):,.2f}</td>",
            f"<td class='r {ratecls}'>{rate*100:.1f}%</td>" if rate is not None else "<td class='r note'>—</td>"])
    ovr = realization.get("overall", {})
    ovr_rate = ovr.get("rate")
    ct_rows.append([
        "<td><b>Overall</b></td>",
        f"<td class=r><b>{ct['overall']:.0f}</b></td>" if ct["overall"] is not None else "<td class='r note'>—</td>",
        f"<td class=r>{(ovr.get('claimed') or 0):,.2f}</td>",
        f"<td class=r>{(ovr.get('paid') or 0):,.2f}</td>",
        f"<td class=r><b>{ovr_rate*100:.1f}%</b></td>" if ovr_rate is not None else "<td class='r note'>—</td>"])
    body = (
        f'<form class="f" method="get"><label>Year<input name="year" value="{esc(year)}" style="width:80px"></label>'
        f'<a href="/export/receivables?year={esc(year)}" style="align-self:end;padding:8px 12px;font-size:13px">⬇ Receivables &amp; forecast (Excel)</a></form>'
        + '<div class="card"><h2>Open-receivable cash forecast</h2>'
        + '<div class="kpis">'
        + f'<div class="kpi"><div class="v">{forecast["open_count"]}</div><div class="l">open claims</div></div>'
        + f'<div class="kpi"><div class="v">EUR {forecast["open_refund_receivable_eur"]:,.0f}</div>'
          '<div class="l">refund receivable (from state)</div></div>'
        + f'<div class="kpi"><div class="v">EUR {forecast["open_weighted_refund_eur"]:,.0f}</div>'
          '<div class="l">realization-weighted refund</div></div>'
        + f'<div class="kpi"><div class="v">EUR {forecast["open_fee_receivable_eur"]:,.0f}</div>'
          '<div class="l">agency fee receivable</div></div></div>'
        + '<h3>Open refund receivable by aging band</h3>' + bars
        + '<div class="note">Open = submitted/approved, not yet paid. Two SEPARATE cash flows, '
          'never summed across routes: the <b>refund receivable</b> is the VAT owed by the state '
          '(route-independent — aged below until the state pays), and the <b>agency fee receivable</b> '
          'is the frozen service fee (invoiced separately on the direct-to-customer route, deducted '
          'on the to-us route). The realization-weighted figure scales each open claim\'s refund by '
          'its refund country\'s historical paid/claimed rate (1.0 where no history yet) — a prudent '
          'expected-cash view. Internal, data-only.</div></div>'
        + '<div class="card"><h2>Cycle time &amp; realization by refund country</h2>'
        + tbl(["Country", "Median submitted→paid (days)", "Claimed EUR", "Paid EUR", "Realization %"], ct_rows)
        + '<div class="note">Median days and realization are computed on <b>paid</b> claims only. '
          'Realization = paid / claimed (which jurisdictions haircut a claim); under 95% flagged '
          'red. NET basis, VAT-excluded.</div></div>'
        + '<div class="card"><h2>VAT receivables ' + esc(year) + '</h2>'
        + tbl(["Entity", "Country", "Period", "Status", "Payout route",
               "Refund receivable (from state)", "Agency fee", "Customer net",
               "Paid amount", "Submitted", "Paid", "Age / band"], trs)
        + '<div class="note">All EUR figures are VAT amounts/refunds (NET-basis prices, VAT '
          'excluded). <b>Refund receivable</b> = VAT owed by the state (route-independent); '
          '<b>agency fee</b> = frozen service fee; <b>customer net</b> = what the customer receives '
          '(0 on the direct route where they collect the full refund and we invoice the fee; '
          'VAT − fee on the deduct route where we remit the net). Aging band colours an open claim '
          'by days since submission: 0-30 green, 30-60 neutral, 60-90/90+ red. This is an internal '
          'financing-ready view — no external send.</div></div>')

    # ----- Embedded-finance section (additive, finance.py) -----------------
    # The financeable base reuses finance.financeable() which itself reuses
    # vat_refund.recovery_report() — so this total reconciles exactly with the
    # submitted/approved outstanding figure shown above. NULL provider by default.
    fin = finance.financeable(year)
    fterms = finance.terms()
    fq = finance.quote(fin["total"], fterms)
    prov = finance.provider()
    fin_rows = []
    for fr in fin["rows"]:
        age = fr.get("age_days")
        fin_rows.append([
            f"<td>{esc(fr.get('entity') or '')}</td><td>{esc(fr.get('country') or '')}</td>"
            f"<td>{esc(fr.get('period') or '')}</td>",
            f"<td>{esc(fr.get('status') or '')}</td>",
            f"<td class=r>{money.f2(fr.get('vat_eur') or 0):,.2f}</td>",
            f"<td class=r>{age if isinstance(age, int) else ''}</td>"])
    fin_section = (
        '<div class="card"><h2>Financing (embedded — origination only)</h2>'
        + '<div class="note" style="border-left:3px solid #b06b00;padding-left:8px">'
          'Financing originates via a <b>LICENSED factoring partner</b>; the platform '
          'never lends. <b>' + esc("Provider: " + prov.name)
        + ('</b> — No provider configured — figures below are <b>informational only</b>.'
           if prov.name == "none" else '</b>')
        + '</div>'
        + '<div class="kpis">'
        + f'<div class="kpi"><div class="v">EUR {fq["receivable_eur"]:,.0f}</div>'
          '<div class="l">financeable (submitted/approved, unpaid)</div></div>'
        + f'<div class="kpi"><div class="v">EUR {fq["advance_eur"]:,.0f}</div>'
          f'<div class="l">advance ({fterms["advance_pct"]*100:.0f}%)</div></div>'
        + f'<div class="kpi"><div class="v">EUR {fq["fee_eur"]:,.0f}</div>'
          f'<div class="l">factoring fee ({fterms["fee_pct"]*100:.2f}%)</div></div>'
        + f'<div class="kpi"><div class="v">EUR {fq["net_now_eur"]:,.0f}</div>'
          '<div class="l">net now (advance − fee)</div></div>'
        + f'<div class="kpi"><div class="v">EUR {fq["remainder_on_settlement_eur"]:,.0f}</div>'
          '<div class="l">remainder on settlement</div></div></div>'
        + '<div class="note">The financeable base is the SAME submitted/approved '
          'outstanding receivable shown above — a tax-authority refund is high-certainty, '
          'which is what makes it financeable. The advance economics are computed at the '
          'configured terms; no VAT figure, gate, lock, or claim is touched.</div>'
        + '<h3>Financeable claims ' + esc(year) + '</h3>'
        + tbl(["Entity", "Country", "Period", "Status", "VAT receivable EUR", "Age (days)"],
              fin_rows)
        + '<form method="post" class="f" style="margin-top:10px">' + _csrf_input()
        + '<input type="hidden" name="__act" value="set_finance_terms">'
        + f'<label>Advance %<input name="advance_pct" value="{esc(str(fterms["advance_pct"]))}" '
          'style="width:80px"></label>'
        + f'<label>Fee %<input name="fee_pct" value="{esc(str(fterms["fee_pct"]))}" '
          'style="width:80px"></label>'
        + '<label>Provider<select name="provider"><option value="none"'
        + (' selected' if prov.name == "none" else '')
        + '>none (informational)</option></select></label>'
        + '<button>Save terms</button>'
        + '<span class="note">Advance fraction (0&lt;x&le;1) and fee fraction (0&le;x&lt;0.5).</span>'
        + '</form></div>')

    # ----- Recorded advance requests (read-only ledger) --------------------
    # finance.list_advances() never raises (→ [] on a broken/missing DB), but guard the
    # render so a malformed row can't break the page.
    try:
        adv_rows = []
        for a in finance.list_advances():
            adv_rows.append([
                f"<td>{esc(a.get('created_at') or '')}</td>",
                f"<td>{esc(a.get('claim_key') or '')}</td>",
                f"<td class=r>{money.f2(a.get('amount_eur') or 0):,.2f}</td>",
                f"<td class=r>{money.f2(a.get('fee_eur') or 0):,.2f}</td>",
                f"<td>{esc(a.get('provider') or '')}</td>",
                f"<td>{esc(a.get('status') or '')}</td>"])
        adv_body = (tbl(["Recorded", "Claim", "Advance EUR", "Fee EUR", "Provider", "Status"],
                        adv_rows) if adv_rows
                    else '<p class="note">No advance requests recorded yet.</p>')
    except Exception as e:
        _log_exc("receivables/list_advances", e)
        adv_body = '<p class="note">Advance ledger temporarily unavailable.</p>'
    fin_section += (
        '<div class="card"><h2>Recorded advance requests</h2>'
        + adv_body
        + '<div class="note">Read-only intent ledger (finance.db). With the NULL provider '
          'these rows record advance INTENT only — no money moves. EUR figures are advance '
          'amount / factoring fee at the recorded terms.</div></div>')
    body = fin_banner + body + fin_section
    return page(body, "rcv")

@app.route("/export/receivables")
def export_receivables():
    import vat_refund as VR, reports
    year = request.args.get("year", "2026")
    path = reports.receivables_forecast_workbook(VR.receivables_forecast(year), year)
    return send_file(path, as_attachment=True, download_name=os.path.basename(path),
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

@app.route("/recon", methods=["GET", "POST"])
def recon():
    """ADMIN-ONLY open-banking reconciliation — ADVISORY ONLY. Reconciles bank
    transactions against EXPECTED incoming VAT refunds (claims FILED but not yet PAID:
    bank_recon.expected_refunds() reuses vat_refund.recovery_report()) by amount + date,
    so you can see which refunds have landed and which are still outstanding.

    Bank lines arrive TODAY via a bank-statement CSV upload (parse_bank_csv); an AISP
    (account-information) provider seam allows an automated, read-only bank feed later via
    a licensed-aggregator AGENT (none configured by default). This surface NEVER marks a
    claim paid or changes any VAT figure/gate/lock/lifecycle — it shows suggestions only.
    """
    import bank_recon as BR
    year = request.args.get("year", "2026")
    banner = ""
    bank_lines = []
    did_recon = False
    if request.method == "POST" and request.form.get("__act") == "upload":
        f = request.files.get("file")
        try:
            if not f or not f.filename:
                raise ValueError("no file selected")
            data = f.read()
            bank_lines = BR.parse_bank_csv(data)
            did_recon = True
            if not bank_lines:
                banner = (f'<div class="card"><b class="bad">No usable rows parsed from '
                          f'{esc(f.filename)}.</b><div class="note">CSV columns: '
                          '<b>date</b> (date | booking date | value date), <b>amount</b> '
                          '(signed; + credit, − debit — or a separate credit/debit pair), '
                          'and optional <b>description</b> / <b>counterparty</b>.</div></div>')
            else:
                banner = (f'<div class="card"><b class="ok">Parsed {len(bank_lines)} '
                          f'bank line(s) from {esc(f.filename)} — advisory reconciliation '
                          f'below (nothing was changed).</b></div>')
        except Exception as e:
            _log_exc("recon/upload", e)
            banner = f'<div class="card"><b class="bad">Upload failed: {esc(str(e))}</b></div>'

    prov = BR.provider()
    # With a configured (non-null) provider the same reconcile would run over a fetched
    # feed; with NullProvider (the default) it returns [] and we use the CSV-upload path.
    if not bank_lines and prov.name != "none":
        try:
            bank_lines = prov.fetch_transactions(_auth.get_setting("bank_account", ""),
                                                 since=None) or []
            did_recon = bool(bank_lines)
        except Exception as e:
            _log_exc("recon/fetch", e)

    expected = BR.expected_refunds(year)
    result = BR.reconcile(bank_lines, expected) if did_recon else None

    advisory = ('<div class="note" style="border-left:3px solid #b06b00;padding-left:8px">'
                '<b>Advisory reconciliation — suggestions only; nothing is marked paid or '
                'changed.</b> Automated bank feeds connect via a licensed AISP partner '
                '(read-only account information, no payment initiation) — '
                f'<b>Provider: {esc(prov.name)}</b>'
                + (' (none configured).' if prov.name == "none" else '.') + '</div>')

    upload_form = (
        '<div class="card"><h2>Bank statement</h2>' + advisory
        + '<form method="post" enctype="multipart/form-data" class="f" style="margin-top:10px">'
        + _csrf_input()
        + '<input type="hidden" name="__act" value="upload">'
        + '<label>CSV file<input type="file" name="file" accept=".csv,text/csv" required></label>'
        + '<button>Upload &amp; reconcile</button></form>'
        + '<div class="note">Columns (header matched case-insensitively, BOM tolerated): '
          '<b>date</b> (date | booking date | value date), <b>amount</b> (signed; + credit, '
          '− debit — or a separate credit/debit pair), optional <b>description</b> '
          '(reference | details) and <b>counterparty</b> (name). Expected refunds = '
          'submitted/approved claims, NET (VAT-excluded) EUR.</div></div>')

    body = upload_form
    if result is not None:
        m_rows = []
        for m in result["matched"]:
            e = m["expected"]; b = m["bank"]
            m_rows.append([
                f"<td>{esc(e.get('entity') or '')}</td><td>{esc(e.get('country') or '')}</td>"
                f"<td>{esc(e.get('period') or '')}</td>",
                f"<td class=r>{money.f2(e.get('expected_eur') or 0):,.2f}</td>",
                f"<td>{esc(b.get('date') or '')}</td>",
                f"<td class=r>{money.f2(b.get('amount') or 0):,.2f}</td>",
                f"<td>{esc(b.get('counterparty') or '')}</td>",
                f"<td>{esc((b.get('description') or '')[:60])}</td>",
                f"<td class=r>{money.f2(m['amount_delta']):,.2f}</td>",
                f"<td class=r>{esc(str(m['day_gap']))}</td>"])
        ue_rows = []
        for e in result["unmatched_expected"]:
            ue_rows.append([
                f"<td>{esc(e.get('entity') or '')}</td><td>{esc(e.get('country') or '')}</td>"
                f"<td>{esc(e.get('period') or '')}</td>",
                f"<td class=r>{money.f2(e.get('expected_eur') or 0):,.2f}</td>",
                f"<td>{esc(e.get('since') or '')}</td>"])
        ub_rows = []
        for b in result["unmatched_bank"]:
            ub_rows.append([
                f"<td>{esc(b.get('date') or '')}</td>",
                f"<td class=r>{money.f2(b.get('amount') or 0):,.2f}</td>",
                f"<td>{esc(b.get('counterparty') or '')}</td>",
                f"<td>{esc((b.get('description') or '')[:80])}</td>"])
        body += (
            '<div class="card"><h2>Matched refunds &harr; bank credits</h2>'
            + tbl(["Entity", "Country", "Period", "Expected EUR", "Bank date",
                   "Bank amount", "Counterparty", "Description", "Δ amount", "Day gap"],
                  m_rows)
            + '<div class="note">A match means a bank credit landed within EUR and date '
              'tolerance of an expected refund. This is a SUGGESTION — the claim is not '
              'marked paid.</div></div>'
            + '<div class="card"><h2>Outstanding — expected refunds not yet seen</h2>'
            + tbl(["Entity", "Country", "Period", "Expected EUR", "Submitted"], ue_rows)
            + '<div class="note">Submitted/approved claims with no matching bank credit '
              'yet — still outstanding from the tax authority.</div></div>'
            + '<div class="card"><h2>Unmatched incoming credits</h2>'
            + tbl(["Bank date", "Amount EUR", "Counterparty", "Description"], ub_rows)
            + '<div class="note">Incoming bank credits not tied to any known expected '
              'refund — review whether they relate to something else.</div></div>')
    return page(banner + body, "rcn")

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
              'price jumps, vehicle volume spikes, per-vehicle price outliers, off-period dates, '
              'off-hours fuelings. Tune thresholds in anomaly.py.</div></div>')
    # time-of-day distribution (the under-used transactions.time dimension). Wrapped so a
    # failure here cannot break the anomaly page — log and skip the table.
    try:
        tod = anomaly.time_of_day_summary(period)
        if tod:
            ttrs = []
            for b in tod:
                litres = format(b["litres"], ",.0f")
                price = format(b["eur_l"], ".3f") if b["eur_l"] is not None else "—"
                ttrs.append([f"<td>{esc(b['hour'])}</td>", f"<td>{esc(b['count'])}</td>",
                             f"<td>{esc(litres)}</td>", f"<td>{esc(price)}</td>"])
            body += ('<div class="card"><h2>Fuelling by time of day — '
                     f'{esc(period)} (diesel)</h2>'
                     + tbl(["Hour", "Fuellings", "Litres", "NET €/L"], ttrs)
                     + '<div class="note">NET EUR/L, VAT-excluded. Hour parsed from the '
                       'station-local fuelling time; rows with no time roll up under '
                       '"unknown".</div></div>')
    except Exception as e:
        _log_exc("anomalies_page time_of_day", e)
    # per-vehicle fuel cost (the under-used transactions.vehicle dimension). Wrapped so a
    # failure here cannot break the anomaly page — log and skip the table.
    try:
        vcost = anomaly.vehicle_cost_summary(period)
        if vcost:
            vtrs = []
            for v in vcost:
                litres = format(v["litres"], ",.0f")
                spend = format(v["spend"], ",.2f")
                price = format(v["eur_l"], ".3f") if v["eur_l"] is not None else "—"
                vtrs.append([f"<td>{esc(v['vehicle'])}</td>", f"<td>{esc(v['n_fuellings'])}</td>",
                             f"<td>{esc(litres)}</td>", f"<td>{esc(spend)}</td>",
                             f"<td>{esc(price)}</td>"])
            body += ('<div class="card"><h2>Vehicle fuel cost (NET €/L) — '
                     f'{esc(period)} (diesel)</h2>'
                     + tbl(["Vehicle", "Fuellings", "Litres", "Spend €", "NET €/L"], vtrs)
                     + '<div class="note">NET EUR/L, VAT-excluded. Volume-weighted per '
                       'vehicle; costliest first. A vehicle systematically high here may '
                       'point at station choice, card misuse, or the wrong supplier.</div></div>')
    except Exception as e:
        _log_exc("anomalies_page vehicle_cost", e)
    return page(body, "ano")

@app.route("/api/recovery")
def api_recovery():
    import vat_refund as VR
    rows, summ = VR.recovery_report(request.args.get("year", "2026"))
    return jsonify({"summary": summ, "claims": rows})

def _vat_status_cell(con, VR, m, cache):
    """Status cell: the system-derived/stored workflow CODE, the live checklist (so 1A
    explains what's missing), and a dropdown to advance the manual lifecycle. A
    withdraw action (admin) releases the invoice locks."""
    ent, ctry, period = m["entity"], m["country"], m["period"]
    is_year = period.endswith("YEAR")
    code = VR.current_code(con, ent, ctry, period, m["verdict"], cache)
    label = VR.STATUS_LABELS.get(code, code)
    cls = ("ok" if code in ("1E", "2A", "3A", "4", "4A")
           else "bad" if code in ("1A", "3B", "3C") else "")
    badge = f'<b class="{cls}">{esc(code)} — {esc(label)}</b>'
    chk = ""
    if not is_year:
        items = VR.submission_checklist(con, ent, ctry, period, cache)
        lis = "".join(f'<div class="{"ok" if ok else "bad"}">{"✓" if ok else "✗"} {esc(l)}</div>'
                      for l, ok in items)
        chk = ('<details><summary class="note" style="cursor:pointer">checklist</summary>'
               f'<div style="font-size:12px;margin:3px 0 0">{lis}</div></details>')
    cur_manual = code if code in VR.MANUAL_CODES else ""
    opts = [f'<option value="" disabled {"selected" if not cur_manual else ""}>advance…</option>']
    for c in VR.MANUAL_CODES:
        opts.append(f'<option value="{c}" {"selected" if c==cur_manual else ""}>'
                    f'{c} — {esc(VR.STATUS_LABELS[c])}</option>')
    # one read of this PK row serves both the meta line below and the lock/withdraw gate
    row = con.execute("""SELECT decision_date, status_note, action_deadline, payout_to, status
                         FROM vat_applications WHERE entity=? AND refund_country=?
                         AND ref_period=?""", (ent, ctry, period)).fetchone()
    meta = ""
    if row:
        bits = []
        if row["decision_date"]:
            bits.append(f"decision {esc(row['decision_date'])}")
        if row["action_deadline"]:
            bits.append(f'<b class="bad">deadline {esc(row["action_deadline"])}</b>')
        if row["status_note"]:
            bits.append(f'note: {esc(row["status_note"])}')
        if bits:
            meta = f'<div class="note" style="margin:2px 0 0">{" · ".join(bits)}</div>'
    nxt = VR.suggested_next(cur_manual, row["payout_to"] if row else None) if cur_manual else None
    hint = (f'<div class="note" style="margin:2px 0 0">next: {nxt} — '
            f'{esc(VR.STATUS_LABELS[nxt])}</div>' if nxt else "")
    # Admin-only override of the refund-country minimum gate (Dir. 2008/9/EC Art. 17):
    # a below-minimum quarter is normally BLOCKED at submit; an admin may submit anyway
    # (recorded in status_note). Hidden for processors and for the YEAR roll-up row.
    ovr = ""
    if session.get("role") == "admin" and not is_year:
        ovr = ('<label style="font-size:11px;margin:0 4px" '
               'title="Submit a below-minimum claim anyway (Art. 17). Recorded in the note.">'
               '<input type="checkbox" name="override_threshold" value="1"> override min</label>')
    frm = ('<form method="post" style="margin:4px 0 0">' + _csrf_input() +
           f'<input type="hidden" name="entity" value="{esc(ent)}">'
           f'<input type="hidden" name="country" value="{esc(ctry)}">'
           f'<input type="hidden" name="ref_period" value="{esc(period)}">'
           f'<select name="status">{"".join(opts)}</select> '
           '<input name="note" placeholder="note / reason" style="width:110px;font-size:12px"> '
           '<input name="deadline" type="date" title="deadline (for 2B document request / 3D appeal)" '
           'style="font-size:12px"> '
           + ovr +
           '<button style="font-size:12px;padding:4px 10px">Set</button></form>')
    wd = ""
    if row and row["status"] in VR.LOCKING and session.get("role") == "admin":
        wd = ('<form method="post" style="margin:2px 0 0">' + _csrf_input() +
              f'<input type="hidden" name="entity" value="{esc(ent)}">'
              f'<input type="hidden" name="country" value="{esc(ctry)}">'
              f'<input type="hidden" name="ref_period" value="{esc(period)}">'
              '<button name="__act" value="withdraw" style="background:var(--mut);'
              'font-size:11px;padding:3px 8px">Withdraw (release locks)</button></form>')
    # Receipt-control WAIVE (admin-only, not on the YEAR roll-up): a supplier whose ref
    # is SYNTHETIC *and* has NO registered invoice for this country (R5 case (a): the
    # invoice isn't coming, so its ref is the INPUT stub) can be waived so it neither
    # blocks submission nor enters the claim. Existing waivers get an "undo". ALL:/vat-id
    # placeholders and case-(b) UNMATCHED (invoices on file, no note match) are never
    # waivable. The predicate MUST mirror submission_checklist's `missing_sup` exactly.
    wv = ""
    if session.get("role") == "admin" and not is_year:
        import supplier_master
        scon = cache.get("_scon")
        if scon is None:
            scon = cache["_scon"] = supplier_master.connect()
        nri = cache.setdefault("_waivable", {})
        def waivable(s, r):
            if (s, r) not in nri:
                nri[(s, r)] = VR._waivable_missing(r, s, ctry, scon)
            return nri[(s, r)]
        invs = VR.stream_invoices(con, ent, ctry, period, cache)
        waived = VR.list_waivers(con, ent, ctry, period)
        missing_sup = sorted({s for s, r in invs
                              if waivable(s, r) and s not in waived})
        rows_wv = []
        for s in missing_sup:
            rows_wv.append(
                '<form method="post" style="margin:2px 0 0">' + _csrf_input() +
                f'<input type="hidden" name="entity" value="{esc(ent)}">'
                f'<input type="hidden" name="country" value="{esc(ctry)}">'
                f'<input type="hidden" name="ref_period" value="{esc(period)}">'
                f'<input type="hidden" name="supplier" value="{esc(s)}">'
                '<input name="reason" placeholder="reason" style="width:90px;font-size:11px"> '
                f'<button name="__act" value="waive" style="background:var(--mut);font-size:11px;'
                f'padding:3px 8px">Waive {esc(s)} (invoice not coming)</button></form>')
        for s in sorted(waived):
            rows_wv.append(
                '<form method="post" style="margin:2px 0 0">' + _csrf_input() +
                f'<input type="hidden" name="entity" value="{esc(ent)}">'
                f'<input type="hidden" name="country" value="{esc(ctry)}">'
                f'<input type="hidden" name="ref_period" value="{esc(period)}">'
                f'<input type="hidden" name="supplier" value="{esc(s)}">'
                f'<span class="note">waived: {esc(s)} </span>'
                '<button name="__act" value="unwaive" style="font-size:11px;'
                'padding:3px 8px">undo</button></form>')
        wv = "".join(rows_wv)
    return badge + meta + hint + chk + frm + wd + wv

@app.route("/vat", methods=["GET", "POST"])
def vat():
    import vat_refund as VR
    con = VR.connect()
    banner = ""
    if request.method == "POST":
        ent, ctry, per = request.form["entity"], request.form["country"], request.form["ref_period"]
        __act = request.form.get("__act")
        if __act == "withdraw":
            ok, msg = VR.withdraw_claim(con, ent, ctry, per)
        elif __act in ("waive", "unwaive"):
            # Receipt-control WAIVE / UNDO is admin-only (the /vat route is already
            # ADMIN_ONLY; this belt-and-suspenders refuses a non-admin defensively).
            if session.get("role") != "admin":
                ok, msg = False, "admin only"
            elif __act == "waive":
                ok, msg = VR.add_waiver(con, ent, ctry, per, request.form["supplier"],
                                        reason=request.form.get("reason", "").strip() or None)
            else:
                ok, msg = VR.remove_waiver(con, ent, ctry, per, request.form["supplier"])
        else:
            # The minimum-threshold override (Dir. 2008/9/EC Art. 17 gate) is ADMIN-ONLY:
            # a processor's checkbox is ignored, so it can never trigger the override.
            override = (request.form.get("override_threshold") == "1"
                        and session.get("role") == "admin")
            ok, msg = VR.set_status_code(con, ent, ctry, per, request.form["status"],
                                         note=request.form.get("note", "").strip() or None,
                                         deadline=request.form.get("deadline", "").strip() or None,
                                         override_threshold=override)
        cls = "ok" if ok else "bad"
        # A doc-missing block is actionable: link straight to the attach UI, prefilled
        # with this claim's entity (the /documents table then lists its invoices).
        resolve = ""
        if msg.startswith("BLOCKED - physical document missing"):
            from urllib.parse import quote
            href = "/documents?entity=" + quote(ent)
            resolve = (f' &nbsp; <a href="{esc(href)}">Resolve: attach a document &rarr;</a>')
        banner = (f'<div class="card"><b class="{cls}">{esc(msg)}</b>{resolve}</div>')
    year = request.args.get("year", "2026")
    matrix = VR.claim_matrix(con, year)
    docidx = VR.docs_index(con)        # one query instead of docs_for() per invoice
    inv_cache = {"_docidx": docidx}    # shared supplier/customer conns + memo, reused below
    rows = []
    for m in matrix:
        v = m["verdict"]
        vcls = "ok" if v.startswith("READY") else ("bad" if "BELOW" in v else "")
        invs = VR.stream_invoices(con, m["entity"], m["country"], m["period"], inv_cache) if not m["period"].endswith("YEAR") else []
        missing_inv = [(s, ref) for s, ref in invs if (m["entity"], s, ref) not in docidx]
        nd = len(missing_inv)
        if m["period"].endswith("YEAR"):
            doccov = ""
        elif invs and nd == 0:
            doccov = f'<span class="ok">{len(invs)}/{len(invs)} docs</span>'
        else:
            # red coverage links straight to the attach UI, prefilled with the first
            # doc-missing invoice (entity/supplier/ref) so it can be resolved in one click.
            from urllib.parse import quote
            label = f'<span class="bad">{len(invs)-nd}/{len(invs)} docs</span>'
            if missing_inv:
                ms, mref = missing_inv[0]
                href = ("/documents?entity=" + quote(m["entity"]) + "&supplier=" + quote(ms)
                        + "&ref=" + quote(mref))
                doccov = f'<a href="{esc(href)}">{label}</a>'
            else:
                doccov = label
        rows.append([f"<td>{esc(m['entity'])}</td><td>{esc(m['country'])}</td><td>{esc(m['period'])}</td>",
                     f"<td class=r>{m['vat_eur']:,.2f}</td><td class=r>{m['vat_local']:,.2f} {esc(m['currency'])}</td>",
                     f"<td class='{vcls}'>{esc(v)}</td><td>{esc(', '.join(m['missing']))}</td>",
                     f"<td>{doccov}</td><td>{esc(m['home'])}</td><td>{esc(m['deadline'])}</td>"
                     f"<td>{_vat_status_cell(con, VR, m, inv_cache)}</td>"])
    total_ready = sum(m["vat_eur"] for m in matrix
                      if m["verdict"].startswith("READY") and not m["period"].endswith("YEAR"))
    body = (banner + f'<div class="card"><h2>VAT refund applications {esc(year)} (2008/9/EC) — '
            f'quarterly READY total: <span class="ok">€{total_ready:,.0f}</span> &nbsp; '
            f'<a href="/export/vat?year={esc(year)}">⬇ Generate claim workbook</a></h2>'
            + tbl(["Entity","Refund country","Period","VAT EUR","VAT local","Threshold verdict",
                   "Months missing","Documents","Home portal","Deadline","Status (1A→5)"], rows)
            + '<div class="note">Status follows a system-controlled checklist: a claim climbs '
              '<b>1A→1E</b> automatically as documents/data are completed and the period ends, '
              'then you advance it manually (2 Submitted → 3A Money received → 5 Closed). '
              'Submission is blocked until the checklist is complete, the period has ended, and '
              'the claim reaches the refund-country minimum (Art. 17: €400/€50 base, or the fixed '
              'national-currency amount — e.g. SEK 4 000/500, DKK 3 000/400). An admin can submit a '
              'below-minimum claim anyway via the per-claim "override min" box. '
              'Edit the checklist rules on the Customers page.'
              '</div></div>')
    for k in ("_scon", "_acon", "_cmcon"):
        if inv_cache.get(k) is not None:
            try: inv_cache[k].close()
            except Exception as e:
                _log.debug("inv_cache close failed for %s: %s", k, e)
    con.close(); return page(body, "vat")


@app.route("/vat/unmatched", methods=["GET", "POST"])
def vat_unmatched():
    """ADMIN-ONLY UNMATCHED-resolution surface. A claim line tags UNMATCHED when a
    transaction's note matches no registered invoice and there isn't exactly one
    registered invoice for that supplier/country — a HARD block on filing. Here an
    admin maps that note to an EXISTING registered invoice (an ASSOCIATION only — it
    never changes a net/VAT amount; the target is re-validated as still-registered &
    non-synthetic at READ time). Set/Clear are CSRF-protected (the /vat module is
    ADMIN_ONLY) and audited with the actor."""
    import vat_refund as VR, supplier_master
    banner = ""
    if request.method == "POST":
        # belt-and-suspenders: ADMIN_ONLY already gates this endpoint in _guard().
        if session.get("role") != "admin":
            return page(FORBIDDEN, ""), 403
        sup = (request.form.get("supplier") or "").strip()
        ctry = (request.form.get("country") or "").strip()
        note = (request.form.get("note") or "").strip()
        act = request.form.get("__act", "set")
        actor = session.get("user", "admin")
        try:
            if act == "clear":
                ok, msg = VR.clear_note_override(sup, ctry, note, actor)
            else:
                ref = (request.form.get("invoice_ref") or "").strip()
                VR.set_note_override(sup, ctry, note, ref, actor)
                ok, msg = True, (f"resolved note '{note}' → invoice {ref} "
                                 f"({sup} / {ctry})")
        except ValueError as e:
            # a stale/unregistered/synthetic ref is a user error, not a 500.
            ok, msg = False, str(e)
        except Exception as e:
            _log_exc("set note override", e)
            ok, msg = False, f"could not save override: {e}"
        banner = (f'<div class="card"><b class="{"ok" if ok else "bad"}">'
                  f'{esc(msg)}</b></div>')
    year = request.args.get("year", "2026")
    con = VR.connect()
    matrix = VR.claim_matrix(con, year, with_portal=False)
    con.close()
    scon = supplier_master.connect()
    cache = {}
    rows = []
    n_unres = 0
    try:
        for m in matrix:
            if m["period"].endswith("YEAR"):
                continue
            uls = VR.unmatched_lines(m["entity"], m["country"], m["period"])
            if not uls:
                continue
            # one resolution form per distinct (supplier, country, note) for this claim;
            # collapse the per-product rows (amounts shown summed for context only).
            by_note = {}
            for u in uls:
                k = (u["supplier"], u["note"])
                agg = by_note.setdefault(k, {"net": 0.0, "vat": 0.0})
                agg["net"] += u["net_eur"]; agg["vat"] += u["vat_eur"]
            for (sup, note), agg in sorted(by_note.items()):
                n_unres += 1
                regs = supplier_master.get_invoices(sup, m["country"], con=scon)
                # only OFFER a registered, non-synthetic ref as a target (matches what
                # set_note_override will accept).
                opts = [no for no, _d in regs if not VR._synthetic(no)]
                if opts:
                    osel = "".join(f'<option>{esc(o)}</option>' for o in opts)
                    setf = (
                        '<form method="post" style="margin:0">' + _csrf_input()
                        + f'<input type="hidden" name="entity" value="{esc(m["entity"])}">'
                        + f'<input type="hidden" name="country" value="{esc(m["country"])}">'
                        + f'<input type="hidden" name="supplier" value="{esc(sup)}">'
                        + f'<input type="hidden" name="note" value="{esc(note)}">'
                        + f'<select name="invoice_ref" style="font-size:12px">{osel}</select> '
                        + '<button name="__act" value="set" style="font-size:12px;'
                          'padding:4px 10px">Resolve</button></form>')
                else:
                    setf = ('<span class="note">no registered invoice for this '
                            'supplier/country — register one first</span>')
                clearf = (
                    '<form method="post" style="margin:2px 0 0">' + _csrf_input()
                    + f'<input type="hidden" name="country" value="{esc(m["country"])}">'
                    + f'<input type="hidden" name="supplier" value="{esc(sup)}">'
                    + f'<input type="hidden" name="note" value="{esc(note)}">'
                    + '<button name="__act" value="clear" style="background:var(--mut);'
                      'font-size:11px;padding:3px 8px">Clear any override</button></form>')
                rows.append([
                    f"<td>{esc(m['entity'])}</td><td>{esc(m['country'])}</td>"
                    f"<td>{esc(m['period'])}</td>",
                    f"<td>{esc(sup)}</td>",
                    f"<td><code>{esc(note) or '<i>(blank)</i>'}</code></td>",
                    f"<td class=r>{money.f2(agg['net']):,.2f}</td>"
                    f"<td class=r>{money.f2(agg['vat']):,.2f}</td>",
                    f"<td>{setf}{clearf}</td>"])
    except Exception as e:
        _log_exc("vat unmatched listing", e)
        banner += ('<div class="card"><b class="bad">could not build the unmatched '
                   f'list: {esc(str(e))}</b></div>')
    finally:
        scon.close()
        for k in ("_scon", "_acon", "_cmcon"):
            if cache.get(k) is not None:
                try: cache[k].close()
                except Exception as e:
                    _log.debug("unmatched cache close failed for %s: %s", k, e)
    body = (banner
            + f'<form class="f" method="get"><label>Year<input name="year" '
              f'value="{esc(year)}" style="width:80px"></label></form>'
            + '<div class="card"><h2>Resolve UNMATCHED invoice references</h2>'
            + (f'<div class="note">{n_unres} unmatched note(s). Each maps a '
               'transaction <b>note</b> to an existing registered invoice for that '
               'supplier/country. This is an <b>association only</b> — it never changes '
               'a net/VAT amount; the claim total is the sum of transaction VAT '
               'regardless of bucketing. A target ref must be currently registered and '
               'non-synthetic, re-validated at read time (a stale override is ignored '
               'and the line falls back to UNMATCHED).</div>'
               + tbl(["Entity", "Country", "Period", "Supplier", "Transaction note",
                      "Net EUR", "VAT EUR", "Resolve"], rows)
               if rows else
               '<div class="note ok">No UNMATCHED lines — every claim line resolves '
               'to a registered invoice.</div>')
            + '</div>')
    return page(body, "vat")


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
    find_block = ""
    if request.method == "POST":
        import import_log as _IL, hashlib as _hl
        act = request.form.get("__act", "upload")
        if act == "find":
            # Search files ALREADY stored (data lake + this customer's vault tree) so an
            # operator can resolve a doc-missing invoice without re-uploading.
            find_block = _documents_find_block(VR, request.form)
        elif act == "attach_existing":
            ent = request.form.get("entity", ""); sup = request.form.get("supplier", "")
            ref = request.form.get("invoice_ref", "")
            try:
                ok, msg = VR.attach_existing(con, ent, sup, ref,
                                             source=request.form.get("source", ""),
                                             source_id=request.form.get("source_id", ""),
                                             kind=request.form.get("kind", "scan"))
            except Exception as e:
                _log_exc("attach existing document", e)
                ok, msg = False, f"could not attach stored file: {e}"
            _IL.log("attach_existing", request.form.get("source_id"), "received" if ok else "failed",
                    actor=session.get("user", "system"), supplier=sup,
                    message=("document vault attach (existing file)" if ok else f"attach failed: {msg}"))
            border = "var(--ok)" if ok else "var(--bad)"
            head = ("&#10003; Stored file attached OK" if ok else "&#10007; Attach failed")
            banner = (f'<div class="card" style="border-left:4px solid {border}">'
                      f'<b class="{"ok" if ok else "bad"}">{head}</b> — {esc(msg)}'
                      + ('<div class="note">Re-used an already-stored file — same SHA-256 dedup '
                         'and cross-invoice warning as a fresh upload.</div>' if ok else
                         '<div class="note">Nothing was attached. Check the source and try again.</div>')
                      + '</div>')
        else:
            f = request.files["doc"]
            _bytes = f.read()
            _sha = _hl.sha256(_bytes).hexdigest() if _bytes else ""
            if not _bytes:
                ok, msg = False, f"{f.filename or 'file'} was empty (0 bytes) — nothing attached"
            else:
                try:
                    ok, msg = VR.attach_document(con, request.form["entity"], request.form["supplier"],
                                                 request.form["invoice_ref"], file_bytes=_bytes,
                                                 filename=f.filename, kind=request.form.get("kind", "scan"))
                except Exception as e:
                    _log_exc("attach document", e)
                    ok, msg = False, f"could not store {f.filename or 'file'}: {e}"
            _IL.log("upload", f.filename, "received" if ok else "failed",
                    actor=session.get("user", "system"), supplier=request.form.get("supplier"),
                    sha256=_sha, bytes=len(_bytes),
                    message=("document vault attach" if ok else f"attach failed: {msg}"))
            border = "var(--ok)" if ok else "var(--bad)"
            head = ("&#10003; Document attached OK" if ok else "&#10007; Attach failed")
            banner = (f'<div class="card" style="border-left:4px solid {border}">'
                      f'<b class="{"ok" if ok else "bad"}">{head}</b> — {esc(msg)}'
                      + ('<div class="note">Stored in the document vault (SHA-256 verified); '
                         'it can’t be lost, only deleted by a user.</div>' if ok else
                         '<div class="note">Nothing was stored. Check the file and try again.</div>')
                      + '</div>')
    focus_ref = request.args.get("ref", "")
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
                  f'<button>Attach</button></form>'
                  # Resolve from an ALREADY-stored file (data lake + this customer's vault).
                  f'<form method="post" style="margin:4px 0 0;display:flex;gap:6px">'
                  + _csrf_input() +
                  f'<input type="hidden" name="entity" value="{esc(ent)}">'
                  f'<input type="hidden" name="supplier" value="{esc(sup)}">'
                  f'<input type="hidden" name="invoice_ref" value="{esc(ref)}">'
                  f'<input type="text" name="q" placeholder="filename contains…" style="font-size:12px">'
                  f'<button name="__act" value="find" style="font-size:12px;padding:3px 8px">Find stored</button></form>')
            data_ref = f' data-doc-ref="{esc(ref)}"' if focus_ref else ""
            rows.append([f"<td{data_ref}>{esc(ent)}</td><td>{esc(sup)}</td><td>{esc(ctry)}</td><td>{esc(ref)}</td><td>{esc(dt)}</td>",
                         f"<td>{dl}</td><td>{up}</td>"])
    auto = (f'<div data-doc-focus="{esc(focus_ref)}"></div>' if focus_ref else "")
    body = banner + find_block + auto + ('<div class="card"><h2>Invoice document vault — every invoice needs its '
                     'original PDF or scan before submission</h2>'
                     + tbl(["Entity","Supplier","Country","Invoice ref","Date","Attached document(s)","Upload"], rows)
                     + '<div class="note">Files are SHA-256 hashed; identical files on different '
                       'invoices trigger a wrong-attachment warning; submission is blocked while '
                       'any invoice in the application has no document. Use <b>Find stored</b> to '
                       'attach a file already in the data lake or this customer’s vault.</div></div>')
    con.close(); return page(body, "doc")

def _documents_find_block(VR, form):
    """Render the search-results card for the 'find a stored file' action: data-lake
    artifacts plus THIS customer's existing invoice_documents (same-customer tree only).
    Each result carries an Attach button that routes through VR.attach_existing ->
    VR.attach_document, so dedup + the cross-invoice warning are preserved."""
    import data_lake
    ent = form.get("entity", ""); sup = form.get("supplier", ""); ref = form.get("invoice_ref", "")
    q = (form.get("q", "") or "").strip().lower()
    results = []   # (source, source_id, filename, supplier, sha, where)
    try:
        for r in data_lake.query(supplier=sup or None, limit=500):
            fn = r.get("filename") or ""
            if q and q not in fn.lower():
                continue
            results.append(("lake", str(r["id"]), fn, r.get("supplier") or "",
                            (r.get("sha256") or "")[:8], f"data lake / {r.get('kind') or ''}"))
    except Exception as e:
        _log_exc("documents find: lake query", e)
    # this customer's already-attached files (same-customer tree only)
    con = VR.connect()
    try:
        for r in con.execute("""SELECT filename, stored_path, sha256, supplier, invoice_ref
                                FROM invoice_documents WHERE entity=? ORDER BY id DESC""", (ent,)):
            fn = r["filename"] or ""
            if q and q not in fn.lower():
                continue
            results.append(("vault", r["stored_path"], fn, r["supplier"] or "",
                            (r["sha256"] or "")[:8], f"vault / invoice {r['invoice_ref']}"))
    except Exception as e:
        _log_exc("documents find: vault query", e)
    finally:
        con.close()
    if not results:
        return ('<div class="card"><b>No stored files match</b> — '
                f'searched the data lake and {esc(ent)}\'s vault'
                + (f' for filenames containing “{esc(q)}”' if q else "") + '.</div>')
    body_rows = []
    for source, sid, fn, rsup, sha, where in results[:200]:
        attach = ('<form method="post" style="margin:0">' + _csrf_input() +
                  f'<input type="hidden" name="entity" value="{esc(ent)}">'
                  f'<input type="hidden" name="supplier" value="{esc(sup)}">'
                  f'<input type="hidden" name="invoice_ref" value="{esc(ref)}">'
                  f'<input type="hidden" name="source" value="{esc(source)}">'
                  f'<input type="hidden" name="source_id" value="{esc(sid)}">'
                  '<button name="__act" value="attach_existing" '
                  'style="font-size:12px;padding:3px 8px">Attach</button></form>')
        body_rows.append(f"<tr><td>{esc(fn)}</td><td>{esc(rsup)}</td><td>{esc(sha)}</td>"
                         f"<td>{esc(where)}</td><td>{attach}</td></tr>")
    return ('<div class="card"><h2>Stored files for '
            f'{esc(sup)} invoice {esc(ref)} ({esc(ent)})</h2>'
            '<table><thead><tr><th>Filename</th><th>Supplier</th><th>SHA</th>'
            '<th>Where</th><th>Attach</th></tr></thead><tbody>'
            + "".join(body_rows) + '</tbody></table>'
            '<div class="note">Attaching re-uses the stored bytes — same SHA-256 dedup and '
            'cross-invoice warning as a fresh upload.</div></div>')

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

class _GenWarn(Exception):
    """Carries a pre-rendered warning banner out of the gen_doc action (unfilled fields)."""

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
                CD.add_document(con, code, request.form.get("kind", "other"), f.filename, f.read(),
                                valid_until=request.form.get("valid_until", "").strip() or None)
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
                CD.add_document(con, code, request.form.get("kind", "power_of_attorney"),
                                f.filename, f.read(), country=country,
                                valid_until=request.form.get("valid_until", "").strip() or None)
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
            elif act == "set_nace":
                con = CD.connect()
                con.execute("UPDATE customers SET nace_code=? WHERE code=?",
                            (request.form.get("nace_code", "").strip(), code)); con.commit(); con.close()
                msg = f"NACE business activity code saved for {esc(code)}."
            elif act == "set_signatory":
                # authorised signatory for generated contracts / POAs — written via the
                # shared update_customer allowlist (rejects empties), so only pass fields
                # the admin actually filled in.
                sig = {k: request.form.get(k, "").strip()
                       for k in ("signatory_name", "signatory_title")
                       if request.form.get(k, "").strip()}
                if not sig:
                    raise ValueError("enter a signatory name and/or title")
                ok, m = CD.update_customer(code, **sig)
                if not ok:
                    raise ValueError(m)
                msg = f"Authorised signatory saved for {esc(code)}."
            elif act == "add_template":
                f = request.files.get("file")
                if not f or not f.filename:
                    raise ValueError("choose a template file")
                con = CD.connect()
                CD.add_template(con, request.form.get("tname", ""), request.form.get("tkind", "other"),
                                f.filename, f.read()); con.close()
                msg = f"Template '{esc(request.form.get('tname',''))}' uploaded."
            elif act == "del_template":
                con = CD.connect(); CD.delete_template(con, int(request.form.get("tid", "0"))); con.close()
                msg = "Template removed."
            elif act == "gen_doc":
                con = CD.connect()
                data, outname, ext, leftover = CD.generate_document(
                    con, int(request.form.get("tid", "0")), code,
                    request.form.get("gen_country", "").strip() or None,
                    as_pdf=request.form.get("as_pdf") == "on")
                if data is None:
                    con.close(); raise ValueError("template not found")
                if leftover and not request.form.get("force"):
                    # UNFILLED FIELDS: warn on screen instead of silently downloading a
                    # document with {{placeholders}} left in. Offer to fix the data or
                    # download anyway.
                    con.close()
                    keep = "".join(f'<input type="hidden" name="{esc(k)}" value="{esc(v)}">'
                                   for k, v in request.form.items() if k != "_csrf")
                    banner = ('<div class="card" style="border-left:4px solid var(--bad)">'
                              f'<b class="bad">&#9888; {len(leftover)} field(s) have no data:</b> '
                              + ", ".join(f"<code>{{{{{esc(f)}}}}}</code>" for f in leftover)
                              + '<div class="note">Fill the missing customer data below, or '
                                'download the draft with the placeholders left in.</div>'
                              + '<form method="post" style="margin-top:6px">' + _csrf_input() + keep
                              + '<input type="hidden" name="force" value="1">'
                              + '<button style="background:var(--mut)">Download anyway</button>'
                              '</form></div>')
                    raise _GenWarn(banner)
                if request.form.get("file_as"):
                    # also file the generated draft as a customer document of the kind it
                    # fulfils (so it can satisfy the checklist once signed/uploaded later)
                    CD.add_document(con, code, request.form.get("file_as"), outname, data,
                                    country=request.form.get("gen_country", "").strip() or None)
                con.close()
                from flask import Response
                mt = {"docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                      "html": "text/html", "md": "text/markdown",
                      "pdf": "application/pdf"}.get(ext, "text/plain")
                resp = Response(data, mimetype=mt)
                resp.headers["Content-Disposition"] = f'attachment; filename="{outname}"'
                if request.form.get("as_pdf") == "on" and ext == "docx":
                    # PDF was requested but the .docx->PDF (LibreOffice) path was
                    # unavailable; we deliver the prefilled .docx as a graceful fallback.
                    resp.headers["X-FFS-Notice"] = "PDF conversion unavailable - delivered .docx"
                return resp
            elif act == "new_doc_request":
                # open a new document request (contract / power of attorney) bound to a
                # prepared form (template) — the lifecycle starts in 'requested'.
                kind = request.form.get("kind", "").strip()
                tid = request.form.get("template_id", "").strip()
                if not tid:
                    raise ValueError("choose a prepared form (template)")
                if kind not in CD.DOC_REQUEST_KINDS:
                    raise ValueError("choose a valid request kind")
                con = CD.connect()
                rid = CD.create_document_request(
                    con, code, kind, int(tid),
                    country=request.form.get("dr_country", "").strip() or None,
                    requested_by=session.get("user"))
                con.close()
                msg = f"Document request #{esc(str(rid))} ({esc(kind)}) opened for {esc(code)}."
            elif act == "gen_doc_request":
                # generate (or re-generate) the draft for a request; this advances it to
                # 'generated' and auto-vaults the bytes — we also offer them as a download.
                con = CD.connect()
                data, outname, ext = CD.generate_request_document(
                    con, int(request.form.get("req_id", "0")))
                con.close()
                from flask import Response
                mt = {"docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                      "html": "text/html", "md": "text/markdown",
                      "pdf": "application/pdf"}.get(ext, "text/plain")
                resp = Response(data, mimetype=mt)
                resp.headers["Content-Disposition"] = f'attachment; filename="{outname}"'
                if ext == "docx":
                    # a PDF was requested but the .docx->PDF (LibreOffice) path was
                    # unavailable; the prefilled .docx is the graceful fallback.
                    resp.headers["X-FFS-Notice"] = "PDF conversion unavailable - delivered .docx"
                return resp
            elif act == "advance_doc_request":
                # state-machine advance. advance_document_request enforces validity server
                # side (returns (False, illegal transition) for an out-of-state POST), so a
                # forged/stale button cannot crash or skip a step.
                new_status = request.form.get("new_status", "").strip()
                f = request.files.get("signed_file")
                signed_bytes = f.read() if (f and f.filename) else None
                signed_name = f.filename if (f and f.filename) else None
                con = CD.connect()
                ok, m = CD.advance_document_request(
                    con, int(request.form.get("req_id", "0")), new_status,
                    signed_file=signed_bytes, signed_filename=signed_name,
                    by=session.get("user"))
                con.close()
                if not ok:
                    raise ValueError(m)
                msg = f"Document request #{esc(request.form.get('req_id',''))}: {esc(m)}."
            elif act in ("add_checklist_rule", "toggle_checklist_rule", "del_checklist_rule"):
                if session.get("role") != "admin":
                    raise ValueError("only an admin can change the checklist rules")
                con = CD.connect()
                if act == "add_checklist_rule":
                    ok2, m2 = CD.set_checklist_rule(
                        con, request.form.get("rkey", ""), request.form.get("rlabel", ""),
                        request.form.get("rscope", "customer"), request.form.get("rcheck", "document"),
                        request.form.get("rref", ""), active=1)
                    if not ok2:
                        con.close(); raise ValueError(m2)
                    msg = m2
                elif act == "toggle_checklist_rule":
                    CD.toggle_checklist_rule(con, request.form.get("rkey"),
                                             request.form.get("active") == "1")
                    msg = "Checklist rule updated."
                else:
                    CD.delete_checklist_rule(con, request.form.get("rkey"))
                    msg = "Checklist rule removed."
                con.close()
            else:
                raise ValueError("unknown action")
            banner = f'<div class="card"><b class="ok">{msg}</b></div>'
        except _GenWarn as w:
            banner = w.args[0]                     # unfilled-fields warning, pre-rendered
        except Exception as e:
            _log_exc("customer management", e)
            banner = f'<div class="card"><b class="bad">Error: {esc(str(e))}</b></div>'

    con = CD.connect()
    # document kinds offered for a customer-level upload: the legacy required docs plus
    # any customer-scoped 'document' checklist rule's kind (so a new rule is uploadable).
    cust_doc_kinds = dict(CD.REQUIRED_DOCS)
    for r in CD.list_checklist_rules(con, active_only=True):
        if r["check_type"] == "document" and r["scope"] == "customer":
            cust_doc_kinds.setdefault(r["ref"], CD.DOC_KINDS.get(r["ref"])
                                      or r["ref"].replace("_", " ").capitalize())
    templates = CD.list_templates(con)
    tmpl_opts = "".join(f'<option value="{t["id"]}">{esc(t["name"])} ({esc(t["ext"])})</option>'
                        for t in templates)
    cards = []
    for c in con.execute("SELECT * FROM customers ORDER BY status DESC, code"):
        code = c["code"]
        active = c["status"] == "active"
        items, ready = CD.activation_checklist(con, code)
        sub_items = CD.evaluate_checklist(con, code, None)   # customer-scoped checklist
        status_badge = (f'<span class="ok">● ACTIVE</span>' if active
                        else f'<span class="bad">● PENDING ACTIVATION</span>')
        chk = "".join(f'<div class="row"><span class="{"ok" if ok else "bad"}">'
                      f'{"✓" if ok else "✗"}</span> {esc(lbl)}</div>' for lbl, ok in items)
        subchk = "".join(f'<div class="row"><span class="{"ok" if ok else "bad"}">'
                         f'{"✓" if ok else "✗"}</span> {esc(lbl)}</div>' for _k, lbl, _sc, ok in sub_items)
        nace_val = (c["nace_code"] if "nace_code" in c.keys() else "") or ""
        sig_name = (c["signatory_name"] if "signatory_name" in c.keys() else "") or ""
        sig_title = (c["signatory_title"] if "signatory_title" in c.keys() else "") or ""
        def fld(v):
            v2 = esc(str(v)) if v is not None else ""
            return f'<span class="bad">{v2}</span>' if v and "INPUT" in str(v) else v2
        meta = "".join(f"<tr><td style='color:var(--mut);width:160px'>{lbl}</td><td>{fld(c[k])}</td></tr>"
                       for lbl, k in (("Company name","company_name"),("Registration number","reg_number"),
                                      ("VAT number","vat_number"),("Country","country"),
                                      ("Home tax portal","home_portal"),("Notes","notes")) if c[k])
        banks = "".join(f"<tr><td>{fld(r['iban'])}</td><td>{esc(r['bank'])}</td><td>{esc(r['currency'])}</td></tr>"
                        for r in con.execute("SELECT * FROM customer_bank_accounts WHERE customer=?", (code,)))
        import datetime as _dt
        _today = _dt.date.today().isoformat()
        def _validity(d):
            vu = d["valid_until"] if "valid_until" in d.keys() else None
            if not vu:
                return ""
            return (f'<span class="bad">EXPIRED {esc(vu)}</span>' if vu < _today
                    else f'<span class="note">valid until {esc(vu)}</span>')
        docs = "".join(f"<tr><td>{esc(d['kind'])}</td><td>{esc(d['filename'])}</td>"
                       f"<td class='note'>sha {esc((d['sha256'] or '')[:8])}</td>"
                       f"<td>{_validity(d)}</td></tr>"
                       for d in CD.documents(con, code))
        fee_pct = c["fee_pct"] or 0; fee_min = c["fee_min"] or 0
        # management forms
        opt = lambda v: "".join(f'<option value="{k}" {"selected" if k==v else ""}>{esc(lbl)}</option>'
                                for k, lbl in {**cust_doc_kinds, "other": "Other"}.items())
        hid = f'<input type="hidden" name="code" value="{esc(code)}">'
        nace_f = ('<form method="post" class="f" style="margin-top:6px">' + _csrf_input() + hid
                  + '<input type="hidden" name="__act" value="set_nace">'
                  + f'<label>NACE business activity<input name="nace_code" value="{esc(nace_val)}" '
                    'style="width:140px" placeholder="e.g. 49.41"></label>'
                  + '<button>Save NACE</button></form>')
        # authorised signatory for generated contracts / powers of attorney (merge-only
        # CRM data; written through the shared update_customer allowlist)
        sig_f = ('<form method="post" class="f" style="margin-top:6px">' + _csrf_input() + hid
                 + '<input type="hidden" name="__act" value="set_signatory">'
                 + f'<label>signatory name<input name="signatory_name" value="{esc(sig_name)}" '
                   'style="width:180px" placeholder="e.g. Jonas Kazlauskas"></label>'
                 + f'<label>signatory title<input name="signatory_title" value="{esc(sig_title)}" '
                   'style="width:160px" placeholder="e.g. Managing Director"></label>'
                 + '<button>Save signatory</button></form>')
        upload_f = ('<form method="post" enctype="multipart/form-data" class="f" style="margin-top:8px">'
                    + _csrf_input() + hid + '<input type="hidden" name="__act" value="upload_doc">'
                    + f'<label>document<select name="kind">{opt("trade_registry")}</select></label>'
                    + '<label>file<input type="file" name="file" required></label>'
                    + '<label>valid until<input type="date" name="valid_until" title="optional — '
                      'an expired document stops satisfying the checklist"></label>'
                    + '<button>Upload</button></form>')
        # generate a document (contract / POA / …) by filling a template with this
        # customer's data — the mini-CRM mail-merge.
        gen_f = ""
        if templates:
            file_as_opts = '<option value="">(download only)</option>' + "".join(
                f'<option value="{k}">also file as {esc(lbl)}</option>'
                for k, lbl in cust_doc_kinds.items())
            gen_f = ('<form method="post" class="f" style="margin-top:8px">' + _csrf_input() + hid
                     + '<input type="hidden" name="__act" value="gen_doc">'
                     + f'<label>template<select name="tid">{tmpl_opts}</select></label>'
                     + '<label>refund country (for POA)<input name="gen_country" '
                       'style="width:120px" placeholder="optional"></label>'
                     + f'<label>filing<select name="file_as">{file_as_opts}</select></label>'
                     + '<label style="flex-direction:row;align-items:center;gap:5px">'
                       '<input type="checkbox" name="as_pdf" style="width:auto"> as PDF</label>'
                     + '<button>Generate document</button></form>')
        # ---- document requests: the generate -> sign -> receive lifecycle register ----
        # One row per request; each row shows ONLY the lifecycle actions valid from its
        # current status (per CD.DOC_REQUEST_TRANSITIONS). advance_document_request enforces
        # the same validity server-side, so a forged/stale button can't skip a step.
        _DR_BADGE = {"requested": "var(--mut)", "generated": "var(--mut)",
                     "sent_for_signature": "var(--mut)", "signed": "var(--mut)",
                     "received": "var(--ok)", "cancelled": "var(--bad)"}

        def _dr_badge(st):
            return (f'<span style="display:inline-block;padding:1px 7px;border-radius:9px;'
                    f'font-size:11px;background:{_DR_BADGE.get(st, "var(--mut)")};'
                    f'color:#fff">{esc((st or "").replace("_", " "))}</span>')

        def _dr_doc_link(doc_id, label):
            if not doc_id:
                return ""
            return f'<a href="/customer-doc/{int(doc_id)}">{esc(label)}</a>'

        def _dr_advance_form(rid, new_status, label, *, bg="var(--mut)", file_field=False):
            enc = ' enctype="multipart/form-data"' if file_field else ""
            file_in = ('<input type="file" name="signed_file" required style="width:150px">'
                       if file_field else "")
            return ('<form method="post"' + enc + ' style="display:inline">' + _csrf_input()
                    + f'<input type="hidden" name="__act" value="advance_doc_request">'
                    + f'<input type="hidden" name="req_id" value="{int(rid)}">'
                    + f'<input type="hidden" name="new_status" value="{esc(new_status)}">'
                    + file_in
                    + f'<button style="background:{bg};font-size:11px;padding:3px 8px">'
                    + f'{esc(label)}</button></form> ')

        dr_rows = []
        for dr in CD.list_document_requests(con, code):
            st = dr["status"]
            rid = dr["id"]
            valid = CD.DOC_REQUEST_TRANSITIONS.get(st, set())
            actions = ""
            if st == "requested":
                actions += ('<form method="post" style="display:inline">' + _csrf_input()
                            + '<input type="hidden" name="__act" value="gen_doc_request">'
                            + f'<input type="hidden" name="req_id" value="{int(rid)}">'
                            + '<button style="background:var(--ok);font-size:11px;padding:3px 8px">'
                              'Generate</button></form> ')
            if st == "generated":
                actions += ('<form method="post" style="display:inline">' + _csrf_input()
                            + '<input type="hidden" name="__act" value="gen_doc_request">'
                            + f'<input type="hidden" name="req_id" value="{int(rid)}">'
                            + '<button style="background:var(--mut);font-size:11px;padding:3px 8px">'
                              'Re-generate</button></form> ')
                if "sent_for_signature" in valid:
                    actions += _dr_advance_form(rid, "sent_for_signature",
                                                "Mark sent for signature", bg="var(--ok)")
            if st == "sent_for_signature" and "signed" in valid:
                actions += _dr_advance_form(rid, "signed", "Mark signed", bg="var(--ok)")
            if st == "signed" and "received" in valid:
                actions += _dr_advance_form(rid, "received", "Upload signed original",
                                            bg="var(--ok)", file_field=True)
            if "cancelled" in valid:
                actions += _dr_advance_form(rid, "cancelled", "Cancel", bg="var(--bad)")
            # ready-to-activate hint: a received PoA whose country now has all docs on file.
            ready_hint = ""
            drc = dr["refund_country"]
            if (st == "received" and dr["kind"] == "power_of_attorney" and drc
                    and CD.country_ready_to_activate(con, code, drc)):
                ready_hint = (f'<div class="note ok" style="font-size:11px">&#10003; {esc(drc)} '
                              'now ready to activate (see Refund countries above)</div>')
            links = " ".join(filter(None, [
                _dr_doc_link(dr["generated_doc_id"], "generated"),
                _dr_doc_link(dr["signed_doc_id"], "signed original")])) or '<span class="note">—</span>'
            dr_rows.append([
                f'<td>{esc(dr["kind"].replace("_", " "))}</td>',
                f'<td>{esc(drc or "—")}</td>',
                f'<td>{_dr_badge(st)}</td>',
                f'<td class="note">{esc(dr["requested_at"] or "")}</td>',
                f'<td>{links}{ready_hint}</td>',
                f'<td>{actions or "<span class=note>—</span>"}</td>',
            ])
        dr_table = (tbl(["Kind", "Country", "Status", "Requested", "Documents", "Actions"], dr_rows)
                    if dr_rows else '<p class="note">no document requests yet</p>')
        # new-request form (prepared-form template + kind + optional refund country)
        new_dr_f = ""
        if templates:
            kind_opts = "".join(f'<option value="{esc(k)}">{esc(k.replace("_", " "))}</option>'
                                for k in CD.DOC_REQUEST_KINDS)
            ctry_opts = '<option value="">(none — global)</option>' + "".join(
                f'<option value="{esc(r["country"])}">{esc(r["country"])}</option>'
                for r in CD.country_rows(con, code))
            new_dr_f = ('<form method="post" class="f" style="margin-top:8px">' + _csrf_input() + hid
                        + '<input type="hidden" name="__act" value="new_doc_request">'
                        + f'<label>prepared form<select name="template_id" required>{tmpl_opts}</select></label>'
                        + f'<label>kind<select name="kind">{kind_opts}</select></label>'
                        + f'<label>refund country<select name="dr_country">{ctry_opts}</select></label>'
                        + '<button>Open request</button></form>')
        docreq_section = (
            '<h2 style="margin-top:12px">Document requests</h2>'
            '<div class="note" style="margin-top:0">A request tracks one contract / power-of-attorney '
            'lifecycle: generate the draft from a prepared form, mark it sent for signature, mark it '
            'signed, then upload the signed original. Generated drafts and signed originals are vaulted.</div>'
            + dr_table
            + (new_dr_f if new_dr_f else '<p class="note">upload a template first to open a request</p>'))
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
                   + '<input type="file" name="file" required style="width:140px">'
                   + '<input type="date" name="valid_until" title="valid until (optional)" '
                     'style="font-size:12px"><button>Receive doc</button></form> ')
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
            + '<h2 style="margin-top:12px">Submission checklist (system-controlled)</h2>'
            + '<div class="note" style="margin-top:0">Customer-level requirements the system '
              'verifies before any claim can be submitted (power of attorney is checked per refund '
              'country below). Edit the rule set in the card at the top of this page.</div>'
            + (subchk or '<p class="note">no active customer-level rules</p>') + nace_f + sig_f
            + '<h2 style="margin-top:12px">Refund countries (activate separately)</h2>'
            + '<div class="note" style="margin-top:0">Request the country documents (power of '
              'attorney), receive them, then activate that country. Claims can only be submitted '
              'for activated countries.</div>'
            + (ctry_html or '<p class="note">no countries requested yet</p>') + addc
            + '<h2 style="margin-top:12px">Documents</h2>'
            + (f'<table><tbody>{docs}</tbody></table>' if docs else '<p class="note">none yet</p>')
            + upload_f
            + (('<h2 style="margin-top:12px">Generate document from a template</h2>'
                '<div class="note" style="margin-top:0">Fills the template with this '
                'customer\'s data (mail-merge). Optionally file the draft as a document.</div>'
                + gen_f) if gen_f else "")
            + docreq_section
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
    # adjustable submission checklist rules (admin)
    is_admin = session.get("role") == "admin"
    rrows = ""
    for r in CD.list_checklist_rules(con):
        verify = (f"data · {esc(r['ref'])}" if r["check_type"] == "data"
                  else f"document · {esc(CD.DOC_KINDS.get(r['ref']) or CD.REQUIRED_DOCS.get(r['ref']) or r['ref'])}")
        st = '<span class="ok">on</span>' if r["active"] else '<span class="note">off</span>'
        actions = ""
        if is_admin:
            rh = (_csrf_input() + f'<input type="hidden" name="rkey" value="{esc(r["key"])}">')
            actions = (
                '<form method="post" style="display:inline">' + rh
                + f'<input type="hidden" name="active" value="{0 if r["active"] else 1}">'
                + '<button name="__act" value="toggle_checklist_rule" style="background:var(--mut);'
                  f'font-size:11px;padding:3px 8px">{"disable" if r["active"] else "enable"}</button></form> '
                + '<form method="post" style="display:inline">' + rh
                + '<button name="__act" value="del_checklist_rule" style="background:var(--bad);'
                  'font-size:11px;padding:3px 8px">×</button></form>')
        rrows += (f"<tr><td>{esc(r['key'])}</td><td>{esc(r['label'])}</td><td>{esc(r['scope'])}</td>"
                  f"<td>{verify}</td><td>{st}</td><td>{actions}</td></tr>")
    con.close()
    add_rule_f = ""
    if is_admin:
        add_rule_f = (
            '<form method="post" class="f" style="margin-top:8px">' + _csrf_input()
            + '<input type="hidden" name="__act" value="add_checklist_rule">'
            + '<label>key<input name="rkey" required style="width:110px" placeholder="vat_cert"></label>'
            + '<label>label<input name="rlabel" required style="width:200px"></label>'
            + '<label>scope<select name="rscope"><option value="customer">customer</option>'
              '<option value="country">country</option></select></label>'
            + '<label>check<select name="rcheck"><option value="document">document</option>'
              '<option value="data">data</option></select></label>'
            + '<label>document kind / data verifier<input name="rref" required style="width:160px" '
              'placeholder="signed_contract"></label>'
            + '<button>Add / update rule</button></form>'
            + f'<div class="note">Data verifiers available: {esc(", ".join(CD.DATA_VERIFIERS))}. '
              'Document rules accept any kind (e.g. signed_contract, trade_registry, '
              'power_of_attorney, or a new one you define).</div>')
    # document templates (mini-CRM mail-merge): upload once, generate per customer
    trows = "".join(
        f'<tr><td>{esc(t["name"])}</td><td>{esc(t["kind"])}</td><td>{esc(t["ext"])}</td>'
        f'<td>{esc(t["filename"])}</td><td>'
        + (('<form method="post" style="display:inline">' + _csrf_input()
            + f'<input type="hidden" name="tid" value="{t["id"]}">'
            + '<button name="__act" value="del_template" style="background:var(--bad);'
              'font-size:11px;padding:3px 8px">×</button></form>') if is_admin else "")
        + '</td></tr>' for t in templates)
    tkind_opts = "".join(f'<option value="{esc(k)}">{esc(l)}</option>'
                         for k, l in {**cust_doc_kinds, "power_of_attorney": "Power of attorney",
                                      "other": "Other"}.items())
    tmpl_add_f = ('<form method="post" enctype="multipart/form-data" class="f" style="margin-top:8px">'
                  + _csrf_input() + '<input type="hidden" name="__act" value="add_template">'
                  + '<label>name<input name="tname" required style="width:150px" placeholder="Contract"></label>'
                  + f'<label>fulfils kind<select name="tkind">{tkind_opts}</select></label>'
                  + '<label>file<input type="file" name="file" required></label>'
                  + '<button>Upload template</button></form>') if is_admin else ""
    tmpl_card = ('<div class="card"><h2>Document templates (contract, power of attorney…)</h2>'
                 '<div class="note" style="margin-top:0">Upload your own template once; the system '
                 'fills the <code>{{placeholders}}</code> with each customer\'s data to generate the '
                 'document (per customer, below). Supported: <b>.txt / .html / .md</b> (full) and '
                 '<b>.docx</b> (best-effort). Available fields: '
                 + str(esc(", ".join("{{" + f + "}}" for f in CD.template_fields()))) + '.</div>'
                 + (f'<table style="margin-top:6px"><thead><tr><th>name</th><th>fulfils kind</th>'
                    f'<th>type</th><th>file</th><th></th></tr></thead><tbody>{trows}</tbody></table>'
                    if templates else '<p class="note">no templates yet</p>')
                 + tmpl_add_f + '</div>')
    rules_card = ('<div class="card"><h2>Submission checklist rules (adjustable)</h2>'
                  '<div class="note" style="margin-top:0">The <b>system</b> verifies these before a '
                  'claim can be submitted — a claim climbs <b>1A→1E</b> automatically as each passes; '
                  'no one can tick them by hand. Rules change over time, so '
                  + ('admins edit them here.' if is_admin else 'an admin can edit them here.')
                  + ' <b>document</b> rules need a customer/country document of that kind; '
                    '<b>data</b> rules check stored customer data.</div>'
                  + f'<table style="margin-top:6px"><thead><tr><th>key</th><th>requirement</th>'
                    f'<th>scope</th><th>verified by</th><th>active</th><th></th></tr></thead>'
                    f'<tbody>{rrows}</tbody></table>' + add_rule_f + '</div>')
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
    body = (banner + rules_card + tmpl_card + new_f + req_card
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
    # rows with inline edit. For the claims DB only, guard against an accidental
    # one-slip change: rows render read-only with an explicit Edit toggle (reveals
    # Save + clears readonly via /app.js) and a confirm-before-save. The server-side
    # save handler is unchanged — the guard is purely client-side.
    guard = (dbk == "claims")
    ro = " readonly" if guard else ""
    rows_html = ""
    for r in con.execute(f"SELECT {','.join(cols)} FROM {table} LIMIT 200"):
        inputs = "".join(f'<td><input name="c_{c}" value="{esc("" if v is None else str(v))}"{ro} '
                         f'style="width:{max(70,min(200,len(str(v or ""))*8))}px"></td>' for c, v in zip(cols, r))
        hpk = "".join(f'<input type="hidden" name="__pk_{k}" value="{esc(r[cols.index(k)])}">' for k in pks)
        form_attr = (' data-confirm="Save changes to this row? Amounts are stored as entered."'
                     if guard else "")
        edit_btn = ('<button type="button" data-edit-row>Edit</button> ' if guard else "")
        save_style = ' style="display:none"' if guard else ""
        rows_html += (f'<tr><form method="post" action="/data?db={esc(dbk)}&table={esc(table)}"{form_attr}>'
                      + _csrf_input() + inputs +
                      f'<td style="white-space:nowrap">{hpk}'
                      f'{edit_btn}'
                      f'<button name="__action" value="save" data-save-row{save_style}>Save</button> '
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
    try:
        act = _audit.activity_summary(con, 30)
        urows = [[f"<td>{esc(u['changed_by'])}</td><td class=r ok>{u['inserts']}</td>",
                  f"<td class=r>{u['updates']}</td><td class=r bad>{u['deletes']}</td>",
                  f"<td class=r>{u['total']}</td>"] for u in act["by_user"]]
        trows = [[f"<td>{esc(t['tbl'])}</td><td class=r ok>{t['inserts']}</td>",
                  f"<td class=r>{t['updates']}</td><td class=r bad>{t['deletes']}</td>",
                  f"<td class=r>{t['total']}</td>"] for t in act["by_table"]]
        hrows = [[f"<td>{esc(h['tbl'])}</td><td>{esc(h['rowkey'])}</td>",
                  f"<td class=r>{h['updates']}</td>"] for h in act["churn"]]
        body += (f'<div class="card"><h2>Activity summary — {esc(dbk)}.db (last 30d)</h2>'
                 + '<div style="display:flex;gap:24px;flex-wrap:wrap;align-items:flex-start">'
                 + '<div>' + tbl(["User", "Ins", "Upd", "Del", "Total"], urows) + '</div>'
                 + '<div>' + tbl(["Table", "Ins", "Upd", "Del", "Total"], trows) + '</div>'
                 + '<div>' + tbl(["Most-revised record", "Key", "Updates"], hrows) + '</div></div>'
                 + '<div class="note">BASELINE install-day snapshots are excluded. The '
                   '"most-revised records" are the rows with the most UPDATEs in the window — '
                   'the per-record rework hotspots.</div></div>')
    except Exception as e:
        _log_exc("history activity summary card", e)
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
            elif act == "set_modules":
                # admin turns whole parts of the app on/off
                for k in MODULES:
                    _auth.set_setting(f"module_{k}", "on" if request.form.get(f"mod_{k}") == "on" else "off")
                banner = "Modules updated — the menu reflects what's turned on."
            elif act == "set_ai_review":
                # advisory AI review backend (default 'none' = OFF). Reuses the same
                # API keys as the extractor backends; no new env vars.
                be = request.form.get("ai_review_backend", "none")
                if be not in ("none", "claude", "openai", "azure"):
                    be = "none"
                _auth.set_setting("ai_review_backend", be)
                banner = ("AI review assistant turned OFF." if be == "none"
                          else f"AI review assistant set to <b>{esc(be)}</b> (advisory only).")
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
            elif act == "set_backup_sync":
                _dir = request.form.get("backup_sync_dir", "").strip()
                _auth.set_setting("backup_sync_dir", _dir)
                banner = (f"Off-site backup folder set to <b>{esc(_dir)}</b>." if _dir
                          else "Off-site backup sync turned OFF.")
            elif act == "sync_backup":
                import backup as _bk
                _dir = _bk.sync_dir()
                if not _dir:
                    banner = "Configure an off-site backup folder first."
                else:
                    dest = _bk.sync_snapshot()
                    if dest is None:
                        _auth.log_error("backup sync", "SyncFailed",
                                        f"could not copy the latest snapshot to {esc(_dir)}",
                                        "", session["user"])
                        raise ValueError("off-site backup sync FAILED — see error log")
                    banner = (f"Latest snapshot copied off-site to "
                              f"<b>{esc(os.path.basename(dest))}</b>.")
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
            elif act == "verify_metrics":
                # DRIFT CHECK: recompute the settled per-period aggregates LIVE via the
                # canonical queries and compare to the materialized settled_metrics. Runs
                # on the latest loaded period (or an explicit ?period). Any drift is an
                # error-log entry + a red banner; a clean match is a green banner.
                _con = DB()
                try:
                    _pers = q_periods(_con)
                finally:
                    _con.close()
                _period = request.form.get("period") or (_pers[0] if _pers else None)
                if not _period:
                    raise ValueError("no loaded period to check — run the monthly close first")
                drifts = metrics.verify(_period)
                if drifts:
                    detail = "; ".join(
                        "{m}: stored {s} vs live {l} (delta {d:+.4f})".format(
                            m=d["metric"], s=d["stored"], l=d["live"], d=d["delta"])
                        for d in drifts)
                    _auth.log_error("metrics drift", "DriftDetected",
                                    f"{len(drifts)} settled metric(s) drifted from a live "
                                    f"recompute for {_period}", detail, session["user"])
                    raise ValueError(
                        f"settled metrics DRIFTED for {_period} ({len(drifts)} metric(s): "
                        f"{', '.join(d['metric'] for d in drifts)}) — see error log; "
                        f"re-run the monthly close to re-settle")
                banner = (f"Settled metrics for <b>{esc(_period)}</b> match a live "
                          f"recompute — no drift.")
            elif act == "set_smtp":
                # SMTP relay config for the digest + per-event alerts. The password is
                # WRITE-ONLY: a blank field leaves the stored secret untouched, so
                # re-saving the form does not wipe it (it is never echoed back either).
                _auth.set_setting("smtp_host", request.form.get("smtp_host", "").strip())
                try:
                    _port = int(float(request.form.get("smtp_port", "0") or 0))
                except ValueError:
                    _port = 0
                _auth.set_setting("smtp_port", str(_port))
                _auth.set_setting("smtp_user", request.form.get("smtp_user", "").strip())
                _auth.set_setting("smtp_from", request.form.get("smtp_from", "").strip())
                _auth.set_setting("notify_recipients", request.form.get("notify_recipients", "").strip())
                try:
                    _iv = int(float(request.form.get("notify_interval_hours", "0") or 0))
                except ValueError:
                    _iv = 0
                _auth.set_setting("notify_interval_hours", str(_iv))
                _pw = request.form.get("smtp_pass", "")
                if _pw:
                    _auth.set_setting("smtp_pass", _pw)
                banner = "Email settings saved."
            elif act == "send_test_email":
                import notify as _notify
                res = _notify.send_test()
                if res == _notify.SENT:
                    banner = f"Test email sent to {len(_notify._recipients())} recipient(s)."
                elif res == _notify.NOOP:
                    banner = ("Nothing sent — configure an SMTP host and at least one "
                              "recipient first.")
                else:
                    _auth.log_error("smtp test", "SendFailed",
                                    "send_test reported a transport failure — check the "
                                    "SMTP host/port/credentials", "", session["user"])
                    raise ValueError("test email FAILED to send — see error log")
            elif act == "issue_api_key":
                import api_keys
                label = request.form.get("api_label", "").strip()
                scopes = [s for s in api_keys.SCOPES if request.form.get(f"scope_{s}") == "on"]
                kid, token = api_keys.issue(label, scopes, session["user"])
                # The plaintext token is shown ONCE here and never stored (only its
                # SHA-256 hash is persisted). esc() the token + label.
                banner = (
                    f'API key <b>#{kid}</b> issued ({esc(label) or "no label"}). '
                    f'<b>Copy it now — it is shown only once and cannot be recovered:</b>'
                    f'<br><code style="user-select:all;word-break:break-all;'
                    f'background:#0001;padding:3px 6px;border-radius:4px">{esc(token)}</code>')
            elif act == "revoke_api_key":
                import api_keys
                api_keys.revoke(int(request.form.get("key_id", "0")))
                banner = f'API key <b>#{esc(request.form.get("key_id",""))}</b> revoked — it now fails on its next call.'
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
    for _db in ("customers.db", "suppliers.db", "fuel_history.db", "vat_claims.db", "security.db"):
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
    # off-site backup sync (mounted OneDrive/SharePoint/NAS folder)
    import backup as _bksync
    _sync_dir = _auth.get_setting("backup_sync_dir", "") or ""
    _sync_form = ('<form method="post" class="f" style="margin:6px 0">' + _csrf_input()
                  + f'<label>Off-site folder<input name="backup_sync_dir" value="{esc(_sync_dir)}" '
                  'placeholder="e.g. /mnt/onedrive/ffs-backups" style="min-width:22em"></label>'
                  + '<button name="__act" value="set_backup_sync">Save off-site folder</button></form>')
    if _sync_dir.strip():
        _ls_path, _ls_mtime = _bksync.last_synced()
        if _ls_path:
            _ls_when = _dt.datetime.fromtimestamp(_ls_mtime).strftime("%Y-%m-%d %H:%M")
            _sync_state = (f'<span class="ok">{esc(_os.path.basename(_ls_path))}</span> ({esc(_ls_when)})')
        else:
            _sync_state = '<span class="bad">nothing copied off-site yet</span>'
        # warn if the latest LOCAL snapshot is not (yet) off-site
        _local_path, _local_mtime = _bksync.last_snapshot()
        _stale = bool(_local_path) and (_ls_mtime is None or
                                        (_local_mtime is not None and _ls_mtime < _local_mtime))
        _sync_warn = ('<br><span class="bad">⚠ latest snapshot not yet copied off-site</span>'
                      if _stale else "")
        _sync_line = (f'<br>Off-site sync: <b>{esc(_sync_dir.strip())}</b> &nbsp;·&nbsp; '
                      f'last copied: {_sync_state}{_sync_warn}')
    else:
        _sync_line = '<br>Off-site sync: <span class="bad">not configured</span>'
    # Encryption-at-rest status (read-only; the key is env/secret-manager only, like
    # FFS_KEK_KEY — no UI to set it). A malformed key must surface, not crash the page.
    try:
        _bk_enc_on = _bksync.backup_key() is not None
        _enc_state = ('<span class="ok">ON (AES-256-GCM)</span>' if _bk_enc_on
                      else '<span>off — snapshots stored in cleartext</span>')
    except Exception as _enc_e:
        _enc_state = ('<span class="bad">misconfigured backup key — '
                      f'{esc(str(_enc_e))}</span>')
    _enc_line = f'<br>Encryption at rest: {_enc_state}'
    backupcard = ('<div class="card"><h2>Backups &amp; data integrity</h2>'
                  f'<p>Last snapshot: {last_bk} &nbsp;·&nbsp; Schedule: <b>{esc(_sched_txt)}</b>'
                  f'<br>DB integrity (quick_check): {" &nbsp; ".join(dbstat) or "—"}'
                  f'{_sync_line}'
                  f'{_enc_line}'
                  f'<br>{ndocs} document file(s) tracked.</p>'
                  + schedule_form
                  + _sync_form
                  + f'<div style="margin:8px 0">{_bkbtn("run_backup","↓ Run backup now")}'
                  f'{_bkbtn("verify_backup","✓ Verify last backup")}'
                  f'{_bkbtn("sync_backup","☁ Sync latest off-site now")}'
                  f'{_bkbtn("verify_docs","✓ Check document integrity")}'
                  f'{_bkbtn("verify_metrics","✓ Drift-check settled metrics")}</div>'
                  '<div class="note">Backups run <b>automatically</b> on the schedule above (a '
                  'background task snapshots when one is due) and can also be taken on demand. Each '
                  'snapshot bundles the databases (crash-consistent copies), the physical '
                  '<b>documents/</b> store (PDF/ZIP originals) and write-once audit CSVs, every file '
                  'carrying a SHA-256 hash so corruption is detectable; the last 14 are kept. Sync the '
                  '<b>backups/</b> folder to off-machine storage (OneDrive/SharePoint) so files survive '
                  'disk loss. "Check document integrity" re-hashes every stored PDF/ZIP against the hash '
                  'recorded at upload — any mismatch or missing file is written to the error log '
                  'above.</div></div>')
    # Email notifications (SMTP relay) — the digest cadence + per-event critical alerts
    # both send through these settings. The stored password is NEVER rendered (write-only).
    _smtp_host = _auth.get_setting("smtp_host", "") or ""
    _smtp_port = _auth.get_setting("smtp_port", "") or ""
    _smtp_user = _auth.get_setting("smtp_user", "") or ""
    _smtp_from = _auth.get_setting("smtp_from", "") or ""
    _smtp_rcpt = _auth.get_setting("notify_recipients", "") or ""
    _smtp_iv = _auth.get_setting("notify_interval_hours", "0") or "0"
    _smtp_has_pw = bool(_auth.get_setting("smtp_pass", ""))
    _n_rcpt = len([a for a in _smtp_rcpt.replace(";", ",").split(",") if a.strip()])
    _smtp_state = (f'<span class="ok">configured</span> (host <b>{esc(_smtp_host)}</b>, '
                   f'{_n_rcpt} recipient(s){", password set" if _smtp_has_pw else ""})'
                   if _smtp_host.strip()
                   else '<span class="bad">not configured — no email is sent</span>')
    smtpcard = ('<div class="card"><h2>Email notifications (SMTP)</h2>'
                f'<p>Status: {_smtp_state}</p>'
                '<form method="post" class="f" style="margin:6px 0">' + _csrf_input()
                + f'<label>SMTP host<input name="smtp_host" value="{esc(_smtp_host)}" '
                  'placeholder="smtp.example.com"></label>'
                + f'<label>Port<input name="smtp_port" value="{esc(_smtp_port)}" '
                  'placeholder="587" style="width:80px"></label>'
                + f'<label>Username<input name="smtp_user" value="{esc(_smtp_user)}" '
                  'placeholder="optional"></label>'
                + '<label>Password<input type="password" name="smtp_pass" '
                  'placeholder="leave blank to keep current" autocomplete="new-password"></label>'
                + f'<label>From<input name="smtp_from" value="{esc(_smtp_from)}" '
                  'placeholder="noreply@example.com"></label>'
                + f'<label>Recipients<input name="notify_recipients" value="{esc(_smtp_rcpt)}" '
                  'placeholder="a@x.com, b@y.com"></label>'
                + f'<label>Digest every (hours)<input name="notify_interval_hours" '
                  f'value="{esc(_smtp_iv)}" placeholder="0 = off" style="width:90px"></label>'
                + '<button name="__act" value="set_smtp">Save email settings</button></form>'
                + '<form method="post" style="display:inline">' + _csrf_input()
                + '<button name="__act" value="send_test_email">Send test email</button></form>'
                + '<div class="note">The action <b>digest</b> e-mails on the cadence above '
                  '(0 = off); <b>critical alerts</b> (dead-letter queue, a stalled intake '
                  'worker, an overdue VAT filing deadline) e-mail immediately when they first '
                  'appear, independent of the digest cadence. The password is stored '
                  'write-only and never shown here — leave it blank to keep the current '
                  'one. Recipients are comma/semicolon-separated.</div></div>')
    modchecks = "".join(
        f'<label class="chk" style="display:flex;gap:7px;align-items:center;font-size:13px;'
        f'flex-direction:row;color:var(--ink);margin:3px 0">'
        f'<input type="checkbox" name="mod_{esc(k)}" {"checked" if module_enabled(k) else ""}> '
        f'<b>{esc(k)}</b> — {esc(lbl)}</label>'
        for k, (lbl, _eps) in MODULES.items())
    modf = ('<div class="card"><h2>Modules — turn parts of the app on / off</h2>'
            '<div class="note" style="margin-top:0">Switch whole parts of the system on or off. '
            'A part that is off disappears from the menu and its pages are unavailable to everyone '
            '(you can turn it back on here at any time). Core pages — dashboard, entities, '
            'customers, suppliers and this panel — are always on.</div>'
            '<form method="post" style="margin-top:8px">'
            + _csrf_input() + modchecks
            + '<div style="margin-top:10px"><button name="__act" value="set_modules">'
              'Save modules</button></div></form></div>')
    # AI review assistant (advisory) — default OFF; reuses the extractor backend keys.
    _air_cur = _auth.get_setting("ai_review_backend", "none") or "none"
    _air_opts = "".join(f'<option value="{b}" {"selected" if b==_air_cur else ""}>{b}</option>'
                        for b in ("none", "claude", "openai", "azure"))
    aireviewf = ('<div class="card"><h2>AI review assistant (advisory)</h2>'
                 '<div class="note" style="margin-top:0">An <b>advisory</b> second opinion '
                 'over data that has ALREADY been extracted and checked deterministically. '
                 'It flags only fuzzy concerns (supplier alias/VAT plausibility, '
                 'price-vs-history); it <b>never changes a figure or status and never gates '
                 'a commit</b>. <b>Default OFF.</b> It sends MINIMIZED DERIVED DATA only — '
                 'never the PDF, never bank/secret fields. The chosen backend reuses the '
                 'same API keys as the extractor (Claude/OpenAI/Azure) and is permitted '
                 'under your DPA; <b>Azure</b> keeps the call inside your own tenant.</div>'
                 '<form method="post" class="f" style="margin-top:8px">'
                 + _csrf_input()
                 + f'<label>Review backend<select name="ai_review_backend">{_air_opts}</select></label>'
                 + '<button name="__act" value="set_ai_review">Save AI review setting</button>'
                 + '</form>'
                 '<div class="note" style="margin-top:8px">A per-(supplier &times; country) '
                 '<b>trust</b> score lets the advisory AI review be skipped for pairs it has '
                 'learned to trust — a cost saving that <b>never</b> touches a legal gate. '
                 '<a href="/admin/confidence">View the confidence scoreboard &rarr;</a></div>'
                 + '</div>')
    # API keys (machine access to the versioned /api/v1 contract). Default-OFF: no keys
    # exist until issued here. Tokens are SHA-256 hashed at rest and shown once at issue.
    import api_keys
    _akeys = api_keys.list_keys()
    _ausage = api_keys.usage_summary()
    aktr = []
    for k in _akeys:
        u = _ausage.get(k["id"], {})
        revform = ("" if k["revoked"] else
                   '<form method="post" style="display:inline">' + _csrf_input()
                   + f'<input type="hidden" name="key_id" value="{k["id"]}">'
                   + '<button name="__act" value="revoke_api_key" '
                     'style="background:var(--bad)">Revoke</button></form>')
        aktr.append([f'<td>#{k["id"]}</td><td>{esc(k["label"] or "")}</td>',
                     f'<td class="note">{esc(k["scopes"] or "")}</td>',
                     f'<td>{esc(k["owner"] or "")}</td>',
                     f'<td class="{"bad" if k["revoked"] else "ok"}">'
                     f'{"REVOKED" if k["revoked"] else "active"}</td>',
                     f'<td class="note">{esc(k["last_used"] or "never")}</td>',
                     f'<td>{u.get("calls", 0)}</td>',
                     f'<td>{revform}</td>'])
    scope_checks = "".join(
        '<label class="chk" style="display:flex;gap:7px;align-items:center;font-size:13px;'
        'flex-direction:row;color:var(--ink);margin:3px 0">'
        f'<input type="checkbox" name="scope_{esc(s)}"> <b>{esc(s)}</b> — {esc(desc)}</label>'
        for s, desc in api_keys.SCOPES.items())
    apikeyf = ('<div class="card"><h2>API keys (machine access — /api/v1)</h2>'
               '<div class="note" style="margin-top:0">Issue a bearer token for the '
               'versioned API (<code>/api/v1</code>: read analytics + the CRM read/write '
               'surface). Tokens are stored only as a '
               'SHA-256 hash (never in plain text, same as passwords) and the plaintext is '
               'shown <b>once</b> at issue — copy it then. Each key carries the scopes you tick; '
               'a call is allowed only for an endpoint whose scope the key holds. Revoke to cut '
               'access immediately. <b>Default off:</b> with no keys, the API returns 401. Send '
               'the token as <code>Authorization: Bearer &lt;token&gt;</code> or '
               '<code>X-API-Key</code>. See docs/API.md.</div>'
               + (tbl(["ID", "Label", "Scopes", "Owner", "Status", "Last used", "Calls", ""], aktr)
                  if _akeys else '<p class="note">No API keys issued — the /api/v1 API is inert.</p>')
               + '<form method="post" style="margin-top:10px">' + _csrf_input()
               + '<label class="f" style="margin:0">label'
               + '<input name="api_label" placeholder="e.g. PowerBI read"></label>'
               + '<div style="margin:6px 0">' + scope_checks + '</div>'
               + '<button name="__act" value="issue_api_key">+ Issue API key</button>'
               + '<span class="note" style="margin-left:8px">tick at least one scope</span>'
               + '</form></div>')
    users_card = ('<div class="card"><h2>Users &amp; permissions</h2>'
                  + tbl(["Username", "Role", "Status", "Last login", "Actions"], utr)
                  + addf + "</div>")
    security_card = (f'<div class="card"><h2>Security status</h2>'
                     f'<p>TLS certificate: '
                     f'{"<span class=ok>cert.pem present - app serves HTTPS</span>" if tls else "<span class=bad>none - run python3 make_cert.py (self-signed) or install a CA cert</span>"}'
                     f' &nbsp;|&nbsp; Password storage: <span class="ok">scrypt (salted)</span>'
                     f' &nbsp;|&nbsp; Session cookies: HttpOnly, SameSite'
                     f'{", Secure (HTTPS)" if tls else ""}</p></div>')
    logins_card = ('<div class="card"><h2>Recent logins</h2>'
                   + tbl(["Timestamp (UTC)", "Username", "Result", "From"], ltr) + "</div>")
    # Platform surfaces live on their own read-only admin routes; link to them so the
    # panel is a single jumping-off point (pure navigation — no behaviour here).
    platform_card = ('<div class="card"><h2>Platform surfaces</h2>'
                     '<div class="note" style="margin-top:0">Read-only platform views, each on '
                     'its own page.</div>'
                     '<p style="margin:10px 0 0"><a href="/admin/tenants">Multi-tenancy registry &rarr;</a>'
                     ' &nbsp;·&nbsp; <a href="/admin/confidence">Confidence-learning scoreboard '
                     '&amp; recent validation events &rarr;</a></p></div>')
    # Section jump-nav (works without JS; anchors below). Keeps every existing card,
    # only regrouped under labelled section headers.
    subnav = ('<div class="subnav">'
              '<a href="#access">Access &amp; Security</a>'
              '<a href="#data">Data &amp; Backups</a>'
              '<a href="#notifications">Notifications</a>'
              '<a href="#modules">Modules &amp; AI</a>'
              '<a href="#platform">Platform</a></div>')
    body = (banner
            + subnav
            + '<h2 class="section" id="access">Access &amp; Security</h2>'
            + users_card
            + permf
            + security_card
            + logins_card
            + errcard
            + apikeyf
            + '<h2 class="section" id="data">Data &amp; Backups</h2>'
            + backupcard
            + '<h2 class="section" id="notifications">Notifications</h2>'
            + smtpcard
            + '<h2 class="section" id="modules">Modules &amp; AI</h2>'
            + modf
            + aireviewf
            + '<h2 class="section" id="platform">Platform</h2>'
            + platform_card)
    return page(body, "adm")

@app.route("/admin/confidence")
def admin_confidence():
    """READ-ONLY confidence-learning scoreboard (admin-only; ADMIN_ONLY + user_admin).
    Shows the per-(supplier x country) TRUST score, clean/flagged counts, and the recent
    append-only validation-event ledger. EXPLAINER (hard invariant): trust only governs
    whether the ADVISORY AI review runs — it NEVER skips or alters a legal gate (checklist,
    thresholds, locks, period-end, document presence, synthetic-line refusal). Every value
    escaped via esc()."""
    import confidence
    board = confidence.scoreboard()
    events = confidence.recent_events(100)
    btr = []
    for r in board:
        sc = r["trust"]
        tcls = "ok" if sc >= confidence.SKIP_AI_TRUST else ("bad" if sc < confidence.HUMAN_REVIEW_TRUST else "")
        btr.append([f'<td>{esc(r["supplier"] or "—")}</td>',
                    f'<td>{esc(r["country"] or "—")}</td>',
                    f'<td class="{tcls}">{esc(f"{sc:.2f}")}</td>',
                    f'<td>{esc(str(r["n_clean"]))}</td>',
                    f'<td>{esc(str(r["n_flagged"]))}</td>',
                    f'<td class="note">{esc(r["updated_at"] or "")}</td>'])
    etr = []
    for e in events:
        etr.append([f'<td class="note">{esc(e["created_at"] or "")}</td>',
                    f'<td>{esc(e["supplier"] or "—")}</td>',
                    f'<td>{esc(e["country"] or "—")}</td>',
                    f'<td class="{"ok" if e["clean"] else "bad"}">'
                    f'{"clean" if e["clean"] else "flagged"}</td>',
                    f'<td class="note">{esc(e["source"] or "")}</td>',
                    f'<td class="note">{esc(e["detail"] or "")}</td>'])
    body = (
        '<div class="card"><h2>Confidence-learning scoreboard</h2>'
        '<div class="note" style="margin-top:0">A per-(supplier &times; country) <b>trust</b> '
        'score that grows with each clean validation and decays on a discrepancy. '
        f'Trust starts at {confidence.INIT:.2f}; a pair at or above '
        f'<b>{confidence.SKIP_AI_TRUST:.2f}</b> lets the advisory AI review be SKIPPED (a '
        f'cost saving), and below <b>{confidence.HUMAN_REVIEW_TRUST:.2f}</b> a human look is '
        'recommended. <b>Trust governs ONLY whether the advisory AI review runs — it never '
        'skips or alters any legal gate</b> (checklist, thresholds, locks, period-end, '
        'document presence, synthetic-line refusal). Prices/figures are unaffected.</div>'
        + (tbl(["Supplier", "Country", "Trust", "Clean", "Flagged", "Updated"], btr)
           if btr else '<p class="note">No validations recorded yet — every (supplier, '
                       f'country) pair defaults to {confidence.INIT:.2f}.</p>')
        + '</div>'
        + '<div class="card"><h2>Recent validation events (append-only ledger)</h2>'
        + (tbl(["When", "Supplier", "Country", "Outcome", "Source", "Detail"], etr)
           if etr else '<p class="note">No validation events recorded yet.</p>')
        + '</div>')
    return page(body, "adm")

@app.route("/admin/tenants")
def admin_tenants():
    """READ-ONLY multi-tenancy registry (admin-only; ADMIN_ONLY + user_admin).

    This is the P0 foundation surface only: it shows the master switch state and
    the registered tenants. When multitenant is OFF (the default single-tenant
    install) it makes that explicit and lists no tenant scoping — NOTHING here
    gates or alters any existing query/figure. Tenant onboarding/admin (create,
    activate, subdomain mapping) is the P4 surface, deliberately deferred. See
    docs/MULTI_TENANCY.md. Every value escaped via esc()."""
    on = _tenancy.multitenant_enabled()
    rows = []
    for t in _tenancy.list_tenants():
        rows.append([f'<td>{esc(t["tenant_id"])}</td>',
                     f'<td>{esc(t.get("name") or "—")}</td>',
                     f'<td class="{"ok" if t["active"] else "bad"}">'
                     f'{"active" if t["active"] else "inactive"}</td>',
                     f'<td class="note">{esc(t.get("created_at") or "")}</td>'])
    mode = ('<b class="ok">ON</b>' if on
            else '<b>OFF</b> — single-tenant mode (default)')
    body = (
        '<div class="card"><h2>Multi-tenancy registry</h2>'
        f'<div class="note" style="margin-top:0">Master switch (<code>multitenant</code>): '
        f'{mode}. This is the <b>P0 foundation</b>: while OFF, the app behaves exactly as a '
        'single-tenant install — the tenant context is never bound and every scoping helper '
        'is a no-op, so no existing query or figure is changed. Per-table query scoping '
        '(P1/P2) and tenant onboarding (P4) are deferred; see '
        '<code>docs/MULTI_TENANCY.md</code>.</div>'
        + (tbl(["Tenant ID", "Name", "Status", "Created"], rows)
           if rows else '<p class="note">No tenants registered. The registry exists but is '
                        'empty; a default single-tenant install needs none.</p>')
        + '</div>')
    return page(body, "adm")

@app.route("/doc/<int:doc_id>")
def doc_download(doc_id):
    import vat_refund as VR, document_vault, io
    con = VR.connect()
    d = con.execute("SELECT * FROM invoice_documents WHERE id=?", (doc_id,)).fetchone()
    con.close()
    if d is None:
        return page('<div class="card"><b class="bad">No such document.</b></div>', ""), 404
    data = document_vault.get_bytes(d["stored_path"], VR.DOCDIR)
    return send_file(io.BytesIO(data), as_attachment=True, download_name=d["filename"])

@app.route("/customer-doc/<int:doc_id>")
def cust_doc_download(doc_id):
    """Download a vaulted CUSTOMER document (customer_documents row) — used by the
    document-request register to fetch a generated draft or a signed original. Admin
    only (same gate as /customers); routes the stored locator through document_vault."""
    import customer_master as CD, document_vault, io
    con = CD.connect()
    d = con.execute("SELECT * FROM customer_documents WHERE id=?", (doc_id,)).fetchone()
    con.close()
    if d is None:
        return page('<div class="card"><b class="bad">No such document.</b></div>', ""), 404
    data = document_vault.get_bytes(d["stored_path"], CD.DOCDIR)
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

# ---------------------------------------------------------------- /api/v1 (token API)
# The versioned, TOKEN-ONLY external contract (see docs/API.md). Auth + scope are
# enforced upstream by _api_v1_guard before any of these views run; a view that runs
# has already proved its key carries the endpoint's scope. v1 is mostly READ-ONLY with a
# SCOPED WRITE surface: CRM create/update (POST/PATCH /customers) under the `api:crm.write`
# scope; there is no extract surface. Each producer is REUSED from the internal app, but
# every payload is whitelisted to NON-SENSITIVE fields — never IBAN/payout/fee/secret/PII.

@app.route("/api/v1/benchmark")
def api_v1_benchmark():
    """Internal price benchmark summary (NET EUR/L, VAT-excluded), per supplier/country
    for a period. Reuses q_benchmark over the read-only product DB."""
    con = DB()
    ps = q_periods(con)
    period = request.args.get("period") or (ps[0] if ps else None)
    rows = [dict(r) for r in q_benchmark(con, period)] if period else []
    con.close()
    return jsonify({"period": period, "basis": "NET EUR/L (VAT excluded)", "rows": rows})

# Claim-status fields safe to expose externally: the workflow stream + readiness only.
# Deliberately EXCLUDES `home` (portal URL) and never carries any payout/fee/bank/PII
# field (claim_matrix itself returns none, but we whitelist to be future-proof).
_V1_CLAIM_FIELDS = ("entity", "country", "period", "vat_eur", "vat_local", "currency",
                    "lines", "verdict", "missing", "deadline")

@app.route("/api/v1/claim-status")
def api_v1_claim_status():
    """VAT claim status / readiness per (entity, country, period). Non-sensitive fields
    only — workflow verdict + EUR/local VAT totals + line count; no payout/fee/bank/PII."""
    import vat_refund as VR
    con = VR.connect()
    matrix = VR.claim_matrix(con, request.args.get("year", "2026"), with_portal=False)
    con.close()
    rows = [{k: r.get(k) for k in _V1_CLAIM_FIELDS} for r in matrix]
    return jsonify({"claims": rows})

@app.route("/api/v1/savings")
def api_v1_savings():
    """Savings-intelligence summary (avoidable overpay + recoverable contract €,
    addressable total, per-country breakdown, top actions). All € are money.f2."""
    import savings_intel
    s = savings_intel.summary(request.args.get("period") or None)
    return jsonify(s)

# ---------------------------------------------------------------- /api/v1 CRM sync
# A BASIC, token-scoped CRM-integration surface so an outsourced/external CRM (a
# top-10 vendor's connector) can read and maintain the in-app customer master that
# feeds VAT claims. Deliberately minimal — richer CRM duties are intended to be
# delegated to that external CRM via this seam, NOT rebuilt in the app. WRITE is
# audit-attributed to the calling API key (see _api_v1_actor below).

# Core, non-secret customer fields exposed to the CRM. NO bank/payout/fee/PII.
_V1_CUSTOMER_FIELDS = ("code", "company_name", "country", "status", "reg_number",
                       "vat_number", "legal_address", "home_portal", "phone",
                       "email", "nace_code")

def _api_v1_actor_name():
    """Resolve the authorized API key (stashed by the guard) to a stable actor string
    'api:<label-or-id>' for audit attribution. Never raises (falls back to id/system)."""
    kid = request.environ.get("ffs_api_key_id")
    if kid is None:
        return "api:unknown"
    try:
        import api_keys
        row = next((k for k in api_keys.list_keys() if k["id"] == kid), None)
        label = (row.get("label") if row else "") or ""
        return f"api:{label.strip() or kid}"
    except Exception as e:
        _log_exc("api_v1 actor resolve", e)
        return f"api:{kid}"

def _v1_customer_detail(code):
    """The GET-detail shape for one customer, or None if absent. Core fields +
    activation summary (checklist + is_active) + per-country status."""
    import customer_master as CD
    con = CD.connect()
    try:
        c = con.execute("SELECT * FROM customers WHERE code=?", (code,)).fetchone()
        if not c:
            return None
        out = {k: c[k] for k in _V1_CUSTOMER_FIELDS if k in c.keys()}
        out["active"] = (c["status"] == "active")
        items, ready = CD.activation_checklist(con, code)
        out["activation_checklist"] = [{"label": lbl, "ok": bool(ok)} for lbl, ok in items]
        out["is_active"] = out["active"]
        out["countries"] = [{"country": r["country"], "status": r["status"]}
                            for r in CD.country_rows(con, code)]
        return out
    finally:
        con.close()

@app.route("/api/v1/customers")
def api_v1_customers_list():
    """List customers (CRM read). Core, non-secret fields only. Scope api:crm."""
    import customer_master as CD
    con = CD.connect()
    try:
        rows = con.execute("""SELECT code, company_name, country, status
                              FROM customers ORDER BY code""").fetchall()
        out = []
        for r in rows:
            countries = [cr["country"] for cr in CD.country_rows(con, r["code"])
                         if cr["status"] == "active"]
            out.append({"code": r["code"], "company_name": r["company_name"],
                        "country": r["country"], "status": r["status"],
                        "active": (r["status"] == "active"),
                        "countries_active": countries})
    finally:
        con.close()
    return jsonify({"customers": out})

@app.route("/api/v1/customers/<code>")
def api_v1_customer_get(code):
    """One customer's detail (CRM read). 404 if absent. Scope api:crm."""
    detail = _v1_customer_detail((code or "").strip().upper())
    if detail is None:
        return _api_err(404, "customer not found")
    return jsonify(detail)

@app.route("/api/v1/customers", methods=["POST"])
def api_v1_customer_create():
    """Create a customer from an external CRM (write). Required: code, company_name,
    country; optional real fields are written immediately so an API-onboarded customer
    carries real values (not INPUT placeholders). Scope api:crm.write.

    409 on duplicate code; 400 on missing required. The write is audit-attributed to
    the calling API key; the actor is always reset (try/finally)."""
    import customer_master as CD
    body = request.get_json(silent=True) or {}
    code = (body.get("code") or "").strip().upper()
    company_name = (body.get("company_name") or "").strip()
    country = (body.get("country") or "").strip()
    if not code or not company_name or not country:
        return _api_err(400, "code, company_name and country are required")
    actor = _api_v1_actor_name()
    try:
        _audit_mod.set_actor(None, actor)
        con = CD.connect()
        exists = con.execute("SELECT 1 FROM customers WHERE code=?", (code,)).fetchone()
        con.close()
        if exists:
            return _api_err(409, f"customer {code} already exists")
        CD.add_customer(code, company_name, country)
        # Replace the INPUT placeholders with the real optional fields the CRM supplied.
        opt = {k: body[k] for k in ("reg_number", "vat_number", "legal_address",
                                    "home_portal", "phone", "email")
               if body.get(k) and str(body.get(k)).strip()}
        if opt:
            ok, msg = CD.update_customer(code, **opt)
            if not ok:
                return _api_err(400, msg)
    except Exception as e:
        _log_exc("api_v1 customer create", e)
        return _api_err(500, "internal error")
    finally:
        _audit_mod.reset_actor()
    return jsonify(_v1_customer_detail(code)), 201

@app.route("/api/v1/customers/<code>", methods=["PATCH"])
def api_v1_customer_update(code):
    """Update a customer's editable fields from an external CRM (write). Body is any
    subset of the editable allowlist. 404 if absent, 400 on validation. Scope
    api:crm.write. Audit-attributed to the calling API key; actor always reset."""
    import customer_master as CD
    code = (code or "").strip().upper()
    body = request.get_json(silent=True) or {}
    fields = {k: v for k, v in body.items() if k in CD.EDITABLE_FIELDS}
    if not fields:
        return _api_err(400, "no editable fields supplied")
    actor = _api_v1_actor_name()
    try:
        _audit_mod.set_actor(None, actor)
        ok, msg = CD.update_customer(code, **fields)
    except Exception as e:
        _log_exc("api_v1 customer update", e)
        return _api_err(500, "internal error")
    finally:
        _audit_mod.reset_actor()
    if not ok:
        return _api_err(404 if "not found" in msg else 400, msg)
    return jsonify(_v1_customer_detail(code)), 200

if __name__ == "__main__":
    import tls
    start_backup_scheduler()
    start_notify_scheduler()   # e-mail the action digest on the admin-set cadence
    start_intake_worker()      # drain the document waiting room in the background
    _ctx, _desc = tls.build_context()
    if _ctx:
        app.config.update(SESSION_COOKIE_SECURE=True)
        print(f" * TLS enabled -> https://127.0.0.1:8050\n * Certificate: {_desc}")
        app.run(host="127.0.0.1", port=8050, debug=False, ssl_context=_ctx)
    else:
        print(f" * HTTP on localhost only ({_desc})")
        app.run(host="127.0.0.1", port=8050, debug=False)
