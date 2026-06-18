"""
FLEET FUEL ANALYTICS - WEB UI
Basic but scalable: server-rendered pages over fuel_history.db (SQLite), with a
parallel JSON API (/api/...) so any future frontend (React, Power BI, mobile)
consumes the same backend without changes.

Run locally:        python3 app.py            -> http://localhost:8050
Team deployment:    gunicorn -w 2 app:app     behind nginx; add auth at the proxy
Scale-up path:      swap DB() for PostgreSQL (one function), keep everything else.

Pages:  /            welcome landing (intro + section cards)
        /analytics   dashboard (KPIs, diesel benchmark, monthly trend)
        /compare     filterable comparison (period, supplier, country, product, dates)
        /headtohead  same-day same-country supplier overlaps + overpay
        /entities    per-entity totals & reclaimable VAT
        /stations    station scorecard with price drift
Exports: /export/master  /export/history   (download the Excel deliverables)
API:    /api/benchmark /api/compare /api/headtohead /api/entities /api/periods
"""
import sqlite3, os, re, secrets, threading, time
from flask import Flask, request, jsonify, render_template_string, send_file, session, redirect, Response, url_for
from markupsafe import escape as esc
from werkzeug.middleware.proxy_fix import ProxyFix
import auth as _auth
import audit as _audit_mod
import tenancy as _tenancy
import dataproduct
import applog
_log = applog.get("app")

# Load any admin-managed provider API keys (set from the Admin panel, sealed at rest)
# into the process environment so the existing os.environ[...] reads keep working. A
# real env var (systemd/shell) always wins; never raises.
try:
    import appsecrets as _appsecrets
    _appsecrets.load_into_environ()
except Exception as _e:   # pragma: no cover - never block startup on secret load
    _log.warning("appsecrets.load_into_environ failed: %s", _e)

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

  // SSO admin card: choosing a provider preset prefills the issuer hint (Google /
  // Microsoft); 'Custom' leaves it alone. Progressive enhancement — no-op if absent.
  (function(){
    var sel=document.querySelector('select[data-sso-preset]'); if(!sel) return;
    var iss=document.querySelector('input[name="sso_issuer"]'); if(!iss) return;
    var hints={google:'https://accounts.google.com',
               microsoft:'https://login.microsoftonline.com/<tenant>/v2.0'};
    sel.addEventListener('change',function(){
      var h=hints[sel.value];
      if(h && !iss.value.trim()) iss.value=h;
    });
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
.pwtoggle{position:absolute;right:11px;top:15px;font-size:12px;color:#0e5fa8;cursor:pointer;user-select:none;font-weight:500}
.ssobtn{display:block;text-align:center;text-decoration:none;background:#fff;color:#1a2733;border:1px solid #dde4ea;border-radius:7px;padding:10px;font-size:14px;font-weight:600;margin-bottom:14px}
.ssobtn:hover{background:#f4f7f9}
.ssosep{text-align:center;color:#9fb3c4;font-size:12px;margin:0 0 12px}</style></head><body>
<div class="box"><h1>Fleet Fuel Analytics</h1>{ERR}{SSO}
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
                         "self-signed, ready" if cert_made else "add later (see docs/MANUAL.md#install-setup-installation)"))
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
                     'docs/MANUAL.md#user-manual-fleet-fuel-vat-refund-system</p></div>')
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
    # Optional "Sign in with SSO" button — shown ONLY when SSO is enabled+configured.
    # Local username/password ALWAYS stays available below it (fallback so the admin
    # can never be locked out).
    sso_html = ""
    try:
        import sso as _sso
        if _sso.enabled():
            cfg = _sso.config()
            label = esc(cfg.get("provider") or "single sign-on")
            sso_html = (f'<a class="ssobtn" href="/sso/login">Sign in with {label}</a>'
                        '<p class="ssosep">— or sign in with a username —</p>')
    except Exception as e:
        _log_exc("login: SSO button render", e)
    return LOGIN_HTML.replace("{ERR}", err).replace("{SSO}", sso_html)

@app.route("/logout")
def logout():
    session.clear()
    return redirect("/login")

@app.route("/sso/login")
def sso_login():
    """Start the OIDC Authorization-Code flow: generate a random state+nonce, stash them
    in the (signed) Flask session for CSRF/replay defence, and redirect to the provider's
    authorize endpoint. A no-op (-> /login) when SSO is disabled. Never raises into the
    request — any failure maps to /login with an error banner."""
    try:
        import sso as _sso
        if not _sso.enabled():
            return redirect("/login")
        state = secrets.token_urlsafe(32)
        nonce = secrets.token_urlsafe(32)
        session["sso_state"] = state
        session["sso_nonce"] = nonce
        redirect_uri = url_for("sso_callback", _external=True)
        url = _sso.login_url(redirect_uri, state, nonce)
        if not url:
            return _login_with_error("Single sign-on is temporarily unavailable "
                                     "(could not reach the identity provider).")
        return redirect(url)
    except Exception as e:
        _log_exc("sso_login", e)
        return _login_with_error("Could not start single sign-on. Please try again.")

@app.route("/sso/callback")
def sso_callback():
    """OIDC redirect target. Validate the returned `state` against the session (reject
    mismatch), exchange the code for tokens, read identity, enforce the domain allowlist,
    map/auto-provision the user, then log in exactly like the local-login success path.
    Never raises into the request."""
    try:
        import sso as _sso
        if not _sso.enabled():
            return redirect("/login")
        # CSRF/replay: the returned state MUST match what we stashed before redirecting.
        sess_state = session.pop("sso_state", "") or ""
        session.pop("sso_nonce", None)
        ret_state = request.args.get("state", "") or ""
        if not sess_state or not secrets.compare_digest(ret_state, sess_state):
            return _login_with_error("Single sign-on could not be verified "
                                     "(state mismatch). Please try again.")
        # An IdP-side error (user denied, etc.) comes back as ?error=...
        if request.args.get("error"):
            return _login_with_error("Single sign-on was cancelled or failed at the "
                                     "identity provider.")
        code = request.args.get("code", "") or ""
        if not code:
            return _login_with_error("Single sign-on did not return an authorization code.")
        redirect_uri = url_for("sso_callback", _external=True)
        ident = _sso.resolve_identity(code, redirect_uri)
        if not ident.get("ok"):
            return _login_with_error("Single sign-on failed: "
                                     + (ident.get("reason") or "unknown error") + ".")
        email = ident["email"]
        if not _sso.domain_allowed(email):
            return _login_with_error(f"Single sign-on refused for {email} — your "
                                     "email domain is not permitted. Ask an admin.")
        u = _auth.get_user_by_email(email)
        if not u:
            if _sso.auto_provision_allowed(email):
                _auth.add_sso_user(email, email)   # username == email; role processor
                u = _auth.get_user_by_email(email)
            else:
                return _login_with_error(f"No account for {email} — ask an admin "
                                         "to create one. (Auto-creation requires an "
                                         "allowed-domain list.)")
        if not u or not u.get("active", 1):
            return _login_with_error("Your account is disabled. Ask an admin.")
        # Log in the SAME way the local-login success path does.
        session.clear()                              # session fixation: start fresh
        session["user"] = u["username"]
        session["role"] = u.get("role") or "processor"
        session.permanent = True
        # Audit the successful SSO login (actor = the username).
        try:
            scon = _auth.connect()
            _audit_mod.set_actor(scon, u["username"])
            scon.execute("INSERT INTO login_log (username, success, remote) VALUES (?,1,?)",
                         (u["username"], request.remote_addr or "sso"))
            scon.commit(); scon.close()
        except Exception as e:
            _log_exc("sso_callback: audit login", e)
        return redirect("/")
    except Exception as e:
        _log_exc("sso_callback", e)
        return _login_with_error("Single sign-on failed unexpectedly. Please try again.")

def _login_with_error(msg):
    """Render the login page with an error banner (used by the SSO flow)."""
    err = f'<div class="err">{esc(msg)}</div>'
    sso_html = ""
    try:
        import sso as _sso
        if _sso.enabled():
            cfg = _sso.config()
            label = esc(cfg.get("provider") or "single sign-on")
            sso_html = (f'<a class="ssobtn" href="/sso/login">Sign in with {label}</a>'
                        '<p class="ssosep">— or sign in with a username —</p>')
    except Exception:
        pass
    return LOGIN_HTML.replace("{ERR}", err).replace("{SSO}", sso_html)

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
    "extract_ai_review": "data_import", "extract_ai_verify": "data_import",
    "extract_ai_correct": "data_import",
    "extract_capture_download": "data_import",
    "extract_capture_file_download": "data_import",
    "extract_folder": "data_import", "extract_folder_file": "data_import",
    "extract_capture_excel": "data_import",
    "capture_file_download": "documents",
    "data_manager":    "data_import",
    "intake_queue_page": "data_import", "intake_review": "data_import",
    "doc_mining_page": "data_import", "imports": "data_import", "files_archive": "data_import",
    "invoice_ctrl":    "invoice_control", "contracts": "invoice_control",
    "vat":             "vat_claims", "api_vat": "vat_claims", "readiness": "vat_claims",
    "receivables":     "vat_claims", "export_receivables": "exports",
    "financing":       "vat_claims",   # embedded-finance origination (advisory, NullProvider)
    "recon":           "vat_claims",
    "customers":       "customers", "cust_doc_download": "customers",
    "doc_requests":    "customers",   # the cross-customer document-requests control board
    "pricing":         "pricing", "pricing_upload": "pricing", "api_pricing": "pricing",
    "pricing_market":  "pricing", "pricing_portal": "pricing",
    "pricing_adopt_benchmark": "pricing", "export_benchmark": "exports",
    "export_peer":     "exports",
    "reliability_page": "pricing",
    "intel":           "pricing", "export_intel": "exports",
    "export_overpay":  "exports",
    "export_expenses": "exports",
    "export_accounting": "exports",
    "export_saft": "exports",
    "export_einvoice": "exports", "export_einvoice_batch": "exports",
    "exports_hub": "exports",
    "documents":       "documents", "doc_download": "documents",
    "doc_assistant":   "documents",
    # A3 — typed custom fields + hierarchical tags over vaulted documents.
    "doc_meta":        "documents",   # the per-document metadata panel (tags + fields)
    "metadata_admin":  "documents",   # the "manage fields & tags" settings page
    "search_page":     "documents",   # full-text search over the document/invoice corpus
    # A4 — the ordered VERSION chain of a vaulted document (upload / revert / view).
    "doc_versions":        "documents",  # the per-document versions panel
    "doc_version_download": "documents", # download/view a specific version's bytes
    # A5 — document RETENTION policies + LEGAL HOLD (advisory records-management). The
    # legal-hold place/release control lives on the doc_meta page (same `documents` cap).
    "retention_admin":   "documents",   # manage retention policies (records schedule)
    "retention_review":  "documents",   # the advisory disposition-review worklist
    "share_links_page": "share", "share_create": "share", "share_views_page": "share",
    "share_revoke":    "share",
    # ② E-SIGNATURE (SES) — the authed dashboard / send-for-signature / verify surface lives
    # alongside the document module (capability `documents`).
    "esign_page": "documents", "esign_send": "documents", "esign_verify_page": "documents",
    "esign_void": "documents", "esign_signed_download": "documents",
    # Data rooms (B4) — same capability as the share-link management surface.
    "rooms_page": "share", "room_page": "share", "room_qa_page": "share",
    "room_engagement_page": "share",
    # WORKFLOW engine (Box Relay-style, advisory) — the Tasks inbox is open to any
    # logged-in user (no capability), so only the admin define/manage pages are gated
    # here (the whole `workflow_admin*` surface needs the `documents` capability, plus
    # an admin-only check below). The inbox + act-on-task + start-run are intentionally
    # NOT listed -> any logged-in user may reach them (subject to login + CSRF).
    "workflow_admin": "documents", "workflow_define": "documents",
    "workflow_update": "documents", "workflow_deactivate": "documents",
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
              "financing", "recon",
              "export_vat", "export_readiness", "export_fees", "export_fee",
              "export_receivables", "export_evidence",
              # the one-click monthly close is an engine-orchestration action (it
              # ENQUEUES engine_close.close onto the worker), so it is admin-only.
              "monthly_close",
              # customer/CRM data (checklist, templates, document generation) is part of
              # the VAT-refund module, so the same admin-only access applies.
              "customers", "cust_doc_download", "doc_requests",
              # the confidence-learning scoreboard is a read-only admin surface.
              "admin_confidence",
              # the multi-tenancy registry is a read-only admin surface (P0).
              "admin_tenants",
              # the workflow DEFINE/MANAGE surface is admin-only (an admin builds the
              # routing); the Tasks inbox / act / start-run are NOT here (any login).
              "workflow_admin", "workflow_define", "workflow_update",
              "workflow_deactivate"}

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
                    "export_saft", "export_einvoice", "export_einvoice_batch",
                    "exports_hub", "reports_page", "reliability_page", "analytics"}),
    "intake":     ("Intake — import, waiting room, files, document mining",
                   {"extract_batch", "extract_confirm", "extract_ai_review",
                    "extract_ai_verify", "extract_ai_correct", "extract_capture_download",
                    "extract_capture_file_download", "extract_folder", "extract_folder_file",
                    "extract_capture_excel",
                    "intake_queue_page", "intake_review",
                    "imports", "files_archive", "doc_mining_page", "data_manager"}),
    "compliance": ("Compliance — invoice control, contract audit, documents",
                   {"invoice_ctrl", "contracts", "documents", "doc_download", "doc_assistant",
                    "capture_file_download",
                    "doc_meta", "metadata_admin", "search_page",
                    "doc_versions", "doc_version_download",
                    "retention_admin", "retention_review",
                    "esign_page", "esign_send", "esign_verify_page", "esign_void",
                    "esign_signed_download"}),
    "sharing":    ("Secure share links — trackable public links to vaulted documents",
                   {"share_links_page", "share_create", "share_views_page", "share_revoke",
                    "rooms_page", "room_page", "room_qa_page", "room_engagement_page"}),
    "vat":        ("VAT refunds — claims, readiness, recovery & fees (admin only)",
                   {"vat", "api_vat", "readiness", "recovery", "receivables", "financing",
                    "recon",
                    "export_vat", "export_readiness", "export_fees", "export_fee",
                    "export_receivables", "export_evidence"}),
    "fx":         ("FX vs ECB exchange rates", {"fx"}),
    "workflow":   ("Workflow — configurable approval/routing + a Tasks inbox (advisory)",
                   {"tasks_page", "task_act", "workflow_start",
                    "workflow_admin", "workflow_define", "workflow_update",
                    "workflow_deactivate"}),
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
    # Secure share links: the public viewer + file stream + the per-page engagement
    # beacon (B3) are PUBLIC by design (no session). They run their OWN per-token gate
    # (revoked/expired/password/email) in the view; do NOT require login here. The beacon
    # (share_event) is exempt from the session-CSRF check by the same token — it is a
    # cross-origin-safe, no-cookie-authority POST that records NOTHING unless the SAME
    # gates pass. The authenticated management pages (share_links_page / share_create /
    # share_views_page / share_revoke) are NOT exempted and fall through to the normal
    # session + capability + CSRF checks below.
    # share_sign is the ② SES public signing POST: like share_event it is a no-cookie-
    # authority public POST that records NOTHING unless the SAME per-token gates pass, so it
    # is exempt from login + the session-CSRF check (re-run by the view itself).
    if request.endpoint in ("share_public", "share_file", "share_event", "share_sign"):
        return
    # SSO (OIDC) login start + provider callback are PUBLIC by design (the user is not
    # logged in yet). They are GET-only and run their OWN CSRF/replay defence via the
    # signed-session `state`/`nonce`, so they are login-exempt here (like the share
    # surface). When SSO is OFF the views themselves redirect to /login (no-op).
    if request.endpoint in ("sso_login", "sso_callback"):
        return
    # Data rooms (B4): the PUBLIC room index, in-room pdf.js viewer, file stream, the
    # per-page engagement beacon and the Q&A 'ask' POST are PUBLIC by design (no session)
    # and run their OWN per-token room-link gate in-view. Like the B1-B3 public surface
    # they fall through here (no login / no session-CSRF); the authed management pages
    # (rooms_page / room_page / room_qa_page / room_engagement_page) are NOT exempt and
    # go through the normal session + capability + CSRF checks below.
    if request.endpoint in ("room_public", "room_doc_viewer", "room_doc_file",
                            "room_doc_event", "room_ask"):
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
    # to any existing query/route/figure. See docs/STRATEGY.md#multi-tenancy-program-plan.
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

def _bind_link_tenant(link):
    """Bind this request's tenant context FROM a public share/room link's stored
    tenant_id (multi-tenancy). The PUBLIC share/room routes (/s/<token>, /r/<token>...)
    are login-exempt, so the before-request hook bound NO tenant; the unguessable token
    is the principal and the link carries the owning tenant. We bind it so every
    downstream tenant-scoped read (record_view, get_by_id, room documents, page beacons,
    agreements, Q&A) sees EXACTLY that link's tenant and nothing else — a public viewer
    can never reach across tenants.

    STRICT/SAFE reading: a link only ever exposes its OWN tenant's data. Inert when
    multitenant is OFF (the default) and a no-op for a missing/foreign link (the route
    then denies anyway). The after-request hook resets the context. Never raises."""
    try:
        if not _tenancy.multitenant_enabled():
            return
        t = (link or {}).get("tenant_id")
        if t:
            _tenancy.set_tenant(t)
    except Exception as e:
        _log.debug("_bind_link_tenant failed (treating as unbound): %s", e)

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
                     q_stations, q_savings, q_savings_lines, q_expense, q_ledger,
                     q_spend_trend, q_price_trend_by_country)
import metrics
import money
import search as _search

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

# Distinct, color-blind-aware palette for multi-series line charts (reused in order).
SVG_PALETTE = ("#0e5fa8", "#c8102e", "#1b7340", "#b8860b", "#6a3d9a", "#0e8a8a",
               "#d2691e", "#5b6b7a")

def svg_line(series, labels, unit="", width=720, height=260, fmt=",.0f", title="line chart"):
    """Dependency-free inline-SVG multi-series LINE/trend chart, same house style as
    svg_hbars. `labels` is the shared, ordered x-axis (periods/dates). `series` is a
    list of (name, values) where values is a list aligned to `labels` (None = gap).
    Draws axes, horizontal gridlines + y-tick labels, one polyline per series in a
    distinct color, a small legend and thinned x-axis labels. Numeric only; currency
    callers format via `fmt`/`unit` (no bare round on currency — values come pre-
    quantized from the query layer). Returns an `<svg ...>...</svg>` string."""
    labels = [str(x) for x in (labels or [])]
    series = [(str(n), [None if v is None else float(v) for v in vals]) for n, vals in (series or [])]
    n = len(labels)
    # Collect every finite y across all series to scale the axis.
    ys = [v for _, vals in series for v in vals if v is not None]
    if n == 0 or not ys:
        return '<p class="note">No data for this selection.</p>'
    pad_l, pad_r, pad_t, pad_b = 64, 14, 14, 34
    legend_h = 22 if len(series) > 1 or (series and series[0][0]) else 0
    plot_w = max(1, width - pad_l - pad_r)
    plot_h = max(1, height - pad_t - pad_b - legend_h)
    ymin = min(0.0, min(ys)); ymax = max(ys)
    if ymax == ymin: ymax = ymin + 1.0          # flat series -> avoid /0
    def xpos(i):
        return pad_l + (plot_w * i / (n - 1) if n > 1 else plot_w / 2)
    def ypos(v):
        return pad_t + plot_h - plot_h * (v - ymin) / (ymax - ymin)
    parts = []
    # horizontal gridlines + y-axis tick labels (5 bands)
    ticks = 4
    for t in range(ticks + 1):
        v = ymin + (ymax - ymin) * t / ticks
        y = ypos(v)
        parts.append(f'<line x1="{pad_l}" y1="{y:.1f}" x2="{pad_l+plot_w}" y2="{y:.1f}" '
                     f'stroke="#e7ecf1" stroke-width="1"/>')
        parts.append(f'<text x="{pad_l-6}" y="{y+4:.1f}" font-size="11" fill="#5b6b7a" '
                     f'text-anchor="end">{format(v, fmt)}</text>')
    # axes
    parts.append(f'<line x1="{pad_l}" y1="{pad_t}" x2="{pad_l}" y2="{pad_t+plot_h}" stroke="#aab6c2"/>')
    parts.append(f'<line x1="{pad_l}" y1="{pad_t+plot_h}" x2="{pad_l+plot_w}" y2="{pad_t+plot_h}" stroke="#aab6c2"/>')
    # x-axis labels, thinned so they don't collide
    step = max(1, (n + 9) // 10)
    for i, lab in enumerate(labels):
        if i % step and i != n - 1:
            continue
        parts.append(f'<text x="{xpos(i):.1f}" y="{pad_t+plot_h+16:.1f}" font-size="11" '
                     f'fill="#5b6b7a" text-anchor="middle">{esc(lab[:10])}</text>')
    # one polyline per series (plus a dot at single points so they're visible)
    for si, (name, vals) in enumerate(series):
        color = SVG_PALETTE[si % len(SVG_PALETTE)]
        pts = [(xpos(i), ypos(v)) for i, v in enumerate(vals) if v is not None]
        if len(pts) >= 2:
            poly = " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
            parts.append(f'<polyline fill="none" stroke="{color}" stroke-width="2" '
                         f'stroke-linejoin="round" stroke-linecap="round" points="{poly}"/>')
        for x, y in pts:
            parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="2.2" fill="{color}"/>')
    # legend (only when it carries information)
    if legend_h:
        lx, ly = pad_l, height - 6
        for si, (name, _vals) in enumerate(series):
            color = SVG_PALETTE[si % len(SVG_PALETTE)]
            parts.append(f'<rect x="{lx}" y="{ly-9}" width="11" height="11" rx="2" fill="{color}"/>')
            parts.append(f'<text x="{lx+15}" y="{ly}" font-size="11.5" fill="#1a2733">{esc(name[:18])}</text>')
            lx += 26 + min(len(name[:18]), 18) * 7
    return (f'<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" '
            f'role="img" aria-label="{esc(title)}">{"".join(parts)}</svg>')

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
<a href="/" class="{{'on' if page=='home'}}">Home</a>
{% if 'intake' in modules and 'data_import' in perms %}<div class="menu" tabindex="0"><span class="mlabel {{'on' if page in ['ext','queue','imp','fil','min'] else ''}}">Intake</span><div class="mdrop"><span>
  <a href="/extract" class="{{'on' if page=='ext'}}">Import batch</a>
  <a href="/queue" class="{{'on' if page=='queue'}}">Waiting room</a>
  <a href="/imports" class="{{'on' if page=='imp'}}">Import log</a>
  <a href="/files" class="{{'on' if page=='fil'}}">File archive</a>
  <a href="/mining" class="{{'on' if page=='min'}}">Doc mining</a>
</span></div></div>{% endif %}
{% if 'compliance' in modules and ('invoice_control' in perms or 'documents' in perms) %}<div class="menu" tabindex="0"><span class="mlabel {{'on' if page in ['doc','srch','esign','inv','con'] else ''}}">Documents</span><div class="mdrop"><span>
  {% if 'documents' in perms %}<a href="/documents" class="{{'on' if page=='doc'}}">Documents</a>
  <a href="/search" class="{{'on' if page=='srch'}}">Search</a>
  <a href="/esign" class="{{'on' if page=='esign'}}">E-signatures</a>{% endif %}
  {% if 'invoice_control' in perms %}<a href="/invoices" class="{{'on' if page=='inv'}}">Invoice control</a>
  <a href="/contracts" class="{{'on' if page=='con'}}">Contract audit</a>{% endif %}
</span></div></div>{% endif %}
{% if 'sharing' in modules and 'share' in perms %}<div class="menu" tabindex="0"><span class="mlabel {{'on' if page in ['shr','rooms'] else ''}}">Sharing</span><div class="mdrop"><span>
  <a href="/share" class="{{'on' if page=='shr'}}">Share links</a>
  <a href="/rooms" class="{{'on' if page=='rooms'}}">Data rooms</a>
</span></div></div>{% endif %}
{% if 'analytics' in modules %}<div class="menu" tabindex="0"><span class="mlabel {{'on' if page in ['ana','rep','sav','exp','int','cmp','txn','h2h','stn','ano','pri','rel'] else ''}}">Analytics</span><div class="mdrop"><span>
  <a href="/analytics" class="{{'on' if page=='ana'}}">Dashboard</a>
  <a href="/reports" class="{{'on' if page=='rep'}}">Reports (charts)</a>
  <a href="/savings" class="{{'on' if page=='sav'}}">Savings</a>
  <a href="/expenses" class="{{'on' if page=='exp'}}">Expenses</a>
  {% if 'pricing' in perms %}<a href="/intel" class="{{'on' if page=='int'}}">Savings &amp; intel</a>{% endif %}
  <a href="/compare" class="{{'on' if page=='cmp'}}">Compare</a>
  <a href="/transactions" class="{{'on' if page=='txn'}}">Transactions</a>
  <a href="/headtohead" class="{{'on' if page=='h2h'}}">Head-to-head</a>
  <a href="/stations" class="{{'on' if page=='stn'}}">Stations</a>
  <a href="/anomalies" class="{{'on' if page=='ano'}}">Anomalies</a>
  {% if 'pricing' in perms %}<a href="/pricing" class="{{'on' if page=='pri'}}">Pricing intel</a>
  <a href="/reliability" class="{{'on' if page=='rel'}}">Reliability</a>{% endif %}
</span></div></div>{% endif %}
<div class="menu" tabindex="0"><span class="mlabel {{'on' if page in ['ent','vat','rdy','rec','rcv','fin','rcn','fx'] else ''}}">VAT &amp; Recovery</span><div class="mdrop"><span>
  <a href="/entities" class="{{'on' if page=='ent'}}">Entities &amp; VAT</a>
  {% if is_admin and 'vat' in modules %}<a href="/vat" class="{{'on' if page=='vat'}}">VAT refunds</a>
  <a href="/readiness" class="{{'on' if page=='rdy'}}">Claims readiness</a>
  <a href="/recovery" class="{{'on' if page=='rec'}}">Recovery &amp; fees</a>
  <a href="/receivables" class="{{'on' if page=='rcv'}}">Receivables &amp; forecast</a>
  <a href="/financing" class="{{'on' if page=='fin'}}">Embedded finance</a>
  <a href="/recon" class="{{'on' if page=='rcn'}}">Bank reconciliation</a>{% endif %}
  {% if 'fx' in modules %}<a href="/fx" class="{{'on' if page=='fx'}}">FX vs ECB</a>{% endif %}
</span></div></div>
<div class="menu" tabindex="0"><span class="mlabel {{'on' if page in ['sup','cus','dreq','dat'] else ''}}">Master data</span><div class="mdrop"><span>
  <a href="/suppliers" class="{{'on' if page=='sup'}}">Suppliers</a>
  {% if is_admin %}<a href="/customers" class="{{'on' if page=='cus'}}">Customers (CRM)</a>{% endif %}
  {% if is_admin %}<a href="/doc-requests" class="{{'on' if page=='dreq'}}">Document requests</a>{% endif %}
  {% if 'data_import' in perms %}<a href="/data" class="{{'on' if page=='dat'}}">Data manager</a>{% endif %}
</span></div></div>
<a href="/history" class="{{'on' if page=='his'}}">History</a>
{% if 'workflow' in modules %}<div class="menu" tabindex="0"><span class="mlabel {{'on' if page in ['tasks','wfadm'] else ''}}">Tasks</span><div class="mdrop"><span>
  <a href="/tasks" class="{{'on' if page=='tasks'}}">My tasks &amp; approvals</a>
  {% if is_admin %}<a href="/workflows" class="{{'on' if page=='wfadm'}}">Manage workflows</a>{% endif %}
</span></div></div>{% endif %}
<span class="rightnav">
{% if 'exports' in perms %}<div class="menu" tabindex="0"><span class="mlabel">⬇ Export</span><div class="mdrop"><span>
  <a href="/export/summary">Summary report</a><a href="/export/master">Master workbook</a><a href="/export/history">History report</a>
  {% if 'analytics' in modules %}<a href="/exports">Accounting &amp; ERP exports</a>{% endif %}
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
def home():
    """Clean WELCOME landing page: a short intro to what the system does plus a row
    of cards linking to the main sections the user has permission for. No data tables
    or VAT worklist — those live on /analytics and /vat respectively."""
    role = session.get("role", "processor")
    is_admin = (role == "admin")
    mods = enabled_modules()
    perms = _auth.permissions_for(role)
    # (title, blurb, href, show?) — only sections the user can actually reach are shown.
    cards = [
        ("Analytics", "Diesel benchmark, savings and price intelligence across every fuel card.",
         "/analytics", "analytics" in mods),
        ("Intake", "Import invoice batches, run the waiting room and mine documents.",
         "/extract", "intake" in mods and "data_import" in perms),
        ("Documents", "Vaulted invoices and supporting evidence, deduplicated and hash-verified.",
         "/documents", "compliance" in mods and "documents" in perms),
        ("Invoice control", "Receipt control, statement reconciliation and contract audit.",
         "/invoices", "compliance" in mods and "invoice_control" in perms),
        ("Customers", "The light CRM: entities, activation, checklist rules, fees and expiry.",
         "/customers", is_admin),
        ("Suppliers", "Supplier master, cadences and registered statements.",
         "/suppliers", True),
        ("VAT &amp; recovery", "EU VAT refund claims (2008/9/EC), readiness, recovery and fees.",
         "/vat", is_admin and "vat" in mods),
        ("History", "The validated, reconciled transaction record across periods.",
         "/history", True),
        ("Admin", "Users, capabilities, modules, backups and server setup.",
         "/admin", is_admin),
    ]
    tiles = "".join(
        f'<a class="kpi link" href="{esc(href)}" style="text-decoration:none;color:inherit">'
        f'<div class="v" style="font-size:16px">{title} &rarr;</div>'
        f'<div class="l" style="margin-top:6px">{blurb}</div></a>'
        for title, blurb, href, show in cards if show)
    body = (
        '<div class="card"><h2>Welcome to Fleet Fuel</h2>'
        '<p class="note" style="font-size:13.5px;color:var(--ink)">This system turns your '
        'multi-supplier fuel and toll spend into recovered cash and an audit-ready financial '
        'record. It processes fuel invoices from every card, recovers EU VAT under Directive '
        '2008/9/EC, and benchmarks prices so you can see where you are overpaying.</p>'
        '<p class="note">Pick a section below to get started — only the areas you have access '
        'to are shown.</p></div>'
        f'<div class="kpis metrics">{tiles}</div>')
    return page(body, "home")

@app.route("/analytics")
def dash():
    con = DB(); periods = q_periods(con)
    period = request.args.get("period", periods[0] if periods else None)
    if not period:
        con.close()
        return page('<div class="card"><h2>No data loaded yet</h2><p>Import an invoice batch '
                    'or run the monthly close to populate transactions.</p></div>', "ana")
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
    body = (f'<form class="f" method="get"><label>Period<select name="period" onchange="this.form.submit()">{psw}</select></label></form>'
            + kpis + close
            + f'<div class="card"><h2>Diesel benchmark — effective net €/L (cheapest first)</h2>{bench}'
            f'<div class="note">Effective includes rebate layers (Q8/Port One).</div></div>'
            f'<div class="card"><h2>Monthly trend</h2>{trend}<div class="note">Populates as periods are loaded via history.py.</div></div>')
    con.close(); return page(body, "ana")

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

@app.route("/reports")
def reports_page():
    """Visual analytics — the charts companion to the table-heavy analytics pages.
    Read-only over the engine-owned product DB (dataproduct.connect via DB()); every
    aggregate comes from the canonical queries.py layer. All prices NET EUR/L, final
    (VAT excluded, rebates applied)."""
    con = DB(); periods = q_periods(con)
    period = request.args.get("period", periods[0] if periods else None)
    NET_NOTE = ('<div class="note">Prices/amounts are <b>NET EUR</b>, final '
                '(VAT excluded, rebates applied). Effective NET €/L = net_eur_eff / litres.</div>')

    # --- per-period trends (canonical: q_spend_trend / q_price_trend_by_country) ---
    spend = q_spend_trend(con)                 # one row per period, all products
    p_labels = [r["period"] for r in spend]
    net_series = [r["net_eur"] for r in spend]
    litre_series = [r["litres"] for r in spend]

    price = q_price_trend_by_country(con)      # (period, country) diesel eff €/L
    pc_periods = sorted({r["period"] for r in price})
    by_country = {}
    for r in price:
        by_country.setdefault(r["country"], {})[r["period"]] = r["eff"]
    price_series = [(c, [by_country[c].get(p) for p in pc_periods])
                    for c in sorted(by_country)]

    # --- selected-period spend splits (canonical: q_compare, rolled up) ---
    comp = q_compare(con, request.args, period) if period else []
    sup_spend, ctry_spend = {}, {}
    for r in comp:
        sup_spend[r["supplier"]] = sup_spend.get(r["supplier"], 0.0) + (r["net_eur"] or 0.0)
        ctry_spend[r["country"]] = ctry_spend.get(r["country"], 0.0) + (r["net_eur"] or 0.0)
    sup_pairs = sorted(sup_spend.items(), key=lambda x: -x[1])
    ctry_pairs = sorted(ctry_spend.items(), key=lambda x: -x[1])

    # --- avoidable-overpay-by-month trend (canonical q_savings per period) ---
    # q_savings is per-period; iterate the (typically few) periods. Guarded so a slow
    # / failing scan can't break the page — the chart is simply omitted on failure.
    overpay_card = ""
    try:
        ov = [(p, q_savings(con, p)["total"]) for p in p_labels]
        if any(v for _, v in ov):
            overpay_chart = svg_line([("Avoidable overpay", [v for _, v in ov])],
                                     [p for p, _ in ov], unit=" €", fmt=",.0f",
                                     title="avoidable overpay by month")
            overpay_card = (
                '<div class="card"><h2>Avoidable overpay by month (EUR)</h2>'
                + overpay_chart
                + '<div class="note"><b>Diesel-focused competitiveness review</b> — litres × '
                  '(this supplier\'s eff. €/L − the cheapest same-day, same-country rival\'s). '
                  'Not a contractual claim. Prices NET EUR/L, final.</div></div>')
    except Exception as e:
        _log_exc("reports avoidable-overpay trend", e)
        overpay_card = ""
    con.close()

    psw = "".join(f'<option {"selected" if p==period else ""}>{esc(p)}</option>' for p in periods)
    pform = ('<form class="f" method="get"><label>Period (spend splits)'
             f'<select name="period" onchange="this.form.submit()">{psw}</select></label></form>')

    net_chart = svg_line([("Net spend", net_series)], p_labels, unit=" €", fmt=",.0f",
                         title="monthly net spend")
    litre_chart = svg_line([("Litres", litre_series)], p_labels, unit=" L", fmt=",.0f",
                           title="monthly volume")
    price_chart = svg_line(price_series, pc_periods, unit=" €/L", fmt=".3f",
                           title="effective net price per country")
    sup_bars = svg_hbars(sup_pairs, unit=" €", fmt=",.0f")
    ctry_bars = svg_hbars(ctry_pairs, unit=" €", fmt=",.0f", color="#1b7340")
    psel_lbl = esc(period) if period else "no data"

    body = (
        '<div class="card"><h2>Visual analytics</h2>'
        '<div class="note">The charts companion to the analytics tables — fleet spend, '
        'volume and price trends over loaded periods, plus the spend split for one period. '
        'All figures come from the same canonical queries the dashboard and savings pages use. '
        'Prices/amounts are NET EUR, final (VAT excluded, rebates applied).</div></div>'
        + f'<div class="card"><h2>Monthly net spend (EUR)</h2>{net_chart}{NET_NOTE}</div>'
        + f'<div class="card"><h2>Monthly volume (litres)</h2>{litre_chart}'
          '<div class="note">All product groups. Litres = SUM(qty) per period.</div></div>'
        + f'<div class="card"><h2>Effective net price by country (diesel, EUR/L)</h2>{price_chart}{NET_NOTE}</div>'
        + overpay_card
        + pform
        + f'<div class="card"><h2>Spend by supplier &middot; {psel_lbl}</h2>{sup_bars}{NET_NOTE}</div>'
        + f'<div class="card"><h2>Spend by country &middot; {psel_lbl}</h2>{ctry_bars}{NET_NOTE}</div>')
    return page(body, "rep")

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
          f' <a class="btn" href="/export/einvoice/batch?period={esc(period)}">Download e-invoices (UBL, ZIP)</a>'
          f' <a class="btn" href="/exports?period={esc(period)}">All accounting &amp; ERP exports</a>'
          f'<div class="note">Transaction-level ledger for import into your accounting/ERP system. '
          f'NET EUR, final; VAT shown separately; gross = net + VAT. '
          f'The SAF-T export is the OECD core structure — specialize per jurisdiction '
          f'(namespace/version/required fields) before any real tax-authority submission. '
          f'The e-invoice batch is EN-16931 / UBL 2.1 Invoice XML (one file per registered invoice).</div>'
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

@app.route("/export/einvoice")
def export_einvoice():
    """EN-16931 / UBL 2.1 Invoice XML export of a SINGLE registered invoice
    (?ref=<invoice>). Read-only over the product data; NET EUR basis, final (rebates
    applied). Amounts are the registered capture (no figure invented)."""
    import io, einvoice_export
    ref = request.args.get("ref") or ""
    # Locate the (entity, country, claim-period) the registered invoice belongs to,
    # then build its UBL document. Read-only enumeration; degrade to a friendly page.
    invoice = None
    try:
        for ent, ctry, qtr, r in einvoice_export._enumerate_invoices():
            if str(r) == str(ref):
                invoice = einvoice_export.build_invoice_for(ent, ctry, qtr, r)
                break
    except Exception as e:
        _log_exc("export_einvoice", e)
    if not invoice:
        return page('<div class="card"><h2>Nothing to export</h2>'
                    '<p class="note">No registered invoice matches that reference. '
                    'Pick another, or export a whole supplier/period as a ZIP.</p></div>', "exp")
    name, data = einvoice_export.ubl_invoice_xml(invoice)
    return send_file(io.BytesIO(data), as_attachment=True, download_name=name,
                     mimetype="application/xml")

@app.route("/export/einvoice/batch")
def export_einvoice_batch():
    """EN-16931 / UBL 2.1 Invoice XML export of a SET of registered invoices
    (?supplier=&period=) as a ZIP of XML files. Read-only; NET EUR basis. A single
    bad invoice is skipped, not fatal."""
    import io, einvoice_export
    supplier = request.args.get("supplier") or None
    period = request.args.get("period") or None
    try:
        name, data = einvoice_export.ubl_invoices_zip(
            {"supplier": supplier, "period": period})
    except ValueError:
        return page('<div class="card"><h2>Nothing to export</h2>'
                    '<p class="note">No registered invoices match that supplier/period. '
                    'Pick another filter.</p></div>', "exp")
    return send_file(io.BytesIO(data), as_attachment=True, download_name=name,
                     mimetype="application/zip")

@app.route("/exports")
def exports_hub():
    """Accounting & ERP exports HUB — one place gathering the structured exports an
    accounting/ERP team consumes: the SAF-T (OECD core) XML, the accounting-ledger
    CSV, and the EN-16931 / UBL 2.1 e-invoice XML (single + batch). Read-only over the
    product data; NET EUR basis, final (rebates applied); VAT shown separately."""
    import reports
    con = reports.connect()
    try:
        periods = reports._periods(con)
    except Exception as e:
        _log_exc("exports_hub", e); periods = []
    finally:
        con.close()
    period = request.args.get("period") or (periods[0] if periods else "")
    psw = "".join(f'<option {"selected" if p==period else ""}>{esc(p)}</option>'
                  for p in periods) or '<option></option>'
    pq = f"?period={esc(period)}" if period else ""

    cards = [
        ("SAF-T (XML, OECD core structure)",
         "Standard Audit File for Tax — the OECD core AuditFile for the period "
         "(Header, MasterFiles, GeneralLedgerEntries). Specialize per jurisdiction "
         "(namespace/version/required fields) before any real tax-authority submission.",
         f"/export/saft{pq}", "Download SAF-T (XML)"),
        ("Accounting ledger (CSV)",
         "One clean row per transaction — decision-free, no chart-of-accounts, no "
         "country-specific XML. Import into Xero/QuickBooks/DATEV/a spreadsheet, or "
         "derive any journal from it.",
         f"/export/accounting{pq}", "Download ledger (CSV)"),
        ("E-invoice batch (EN-16931 / UBL 2.1, ZIP)",
         "Every registered invoice for a supplier/period as well-formed UBL 2.1 "
         "Invoice XML (one file per invoice), the outbound counterpart to the "
         "structured e-invoices the system already ingests.",
         f"/export/einvoice/batch{pq}", "Download e-invoices (ZIP)"),
    ]
    card_html = "".join(
        f'<div class="card"><h2>{esc(title)}</h2>'
        f'<p class="note">{esc(blurb)}</p>'
        f'<p style="margin-top:8px"><a class="btn" href="{href}">{esc(label)}</a></p></div>'
        for title, blurb, href, label in cards)

    body = (
        '<div class="card"><h2>Accounting &amp; ERP exports</h2>'
        '<p class="note">Structured, read-only exports of the validated transaction '
        'record for your accounting / ERP / tax tooling. Basis: <b>NET EUR</b>, final '
        '(rebates applied); VAT shown separately; gross = net + VAT. These are clean '
        'structured files, not country-validated e-reporting submissions — validate '
        'against the destination schema (SAF-T / EN-16931 BIS / national CIUS) before '
        'any legal filing.</p>'
        '<form class="f" method="get" style="margin-top:8px">'
        f'<label>Period<select name="period" onchange="this.form.submit()">{psw}</select></label>'
        '</form></div>'
        + card_html
        + '<div class="note">Single e-invoice export: from a claim, use the per-invoice '
          'UBL link, or call <code>/export/einvoice?ref=&lt;invoice&gt;</code>. The '
          'amounts are the registered capture — no VAT figure is invented.</div>')
    return page(body, "exp")

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
                    ""), 404
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
                    "")
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
                            f'the waiting room and press <b>Send / restart all</b>. {esc(hint)}'
                            '</p><p><a href="/queue">→ Go to the waiting room</a></p></div>'
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
        # Persist the AI-vision CAPTURE DOCUMENT as a durable second file (JSON + text),
        # LINKED to this upload's sha256 (the raw_upload archived above) so the original
        # PDF and its captured-data file are a pair. Best-effort; only a vision draft.
        if draft.get("backend") == "vision":
            try:
                import capture_file
                capture_file.persist(draft, _sha, source_name=f.filename, backend="vision",
                                     actor=session.get("user", "system"))
            except Exception as e:
                _log_exc("persist capture file", e)
        # stash the upload sha on the (already-stashed) draft so the review screen can
        # surface the saved-file links (a `_`-prefixed key, never registered).
        draft["_upload_sha256"] = _sha
        _stash_draft(token, draft)
        # CO-LOCATE the original document(s) AND the captured text in ONE folder
        # (captures/<sha>/) so an operator can open one place and see both the source PDF
        # and exactly what was read. Best-effort; never blocks the review.
        try:
            import capture_folder
            _cap = draft.get("capture") if isinstance(draft, dict) else None
            _cap_txt = None
            if _cap:
                try:
                    _cap_txt = _capture_text(_cap)
                except Exception:
                    _cap_txt = None
            capture_folder.save(_sha, draft.get("_pdf_bytes") or [],
                                draft.get("_source_text") or "",
                                source_name=f.filename, capture_json=_cap,
                                capture_text=_cap_txt)
        except Exception as e:
            _log_exc("capture folder save", e)
        # READ-FIRST: derive the period from the invoice/statement date (the manual field
        # is an optional override) and surface what was auto-detected — auto-onboarding an
        # unknown-but-VAT-identified supplier as provisional, or flagging it UNMATCHED.
        period = _derive_period(draft, request.form.get("period") or None)
        notice = _read_first_notice(draft, period)
        return page(receipt + notice + _review_form(draft, token, period=period), "ext")
    return page(_upload_form(backend_env), "ext")


def _period_from_date(d):
    """Derive a YYYY-MM period from a 'YYYY-MM-DD' (or 'YYYY-MM…') date string, or None.
    Read-first intake keys the period off the invoice/statement date so the operator
    needn't pre-pick it."""
    s = (d or "").strip()
    if len(s) >= 7 and s[4] == "-" and s[:4].isdigit() and s[5:7].isdigit():
        return s[:7]
    return None


def _derive_period(draft, override=None):
    """The period for a read-first draft: an explicit override wins; else the invoice/
    statement date drives it (first the statement_date, then the earliest line date);
    else the active close period from month_config."""
    if override:
        return override
    p = _period_from_date(draft.get("statement_date"))
    if p:
        return p
    for ln in draft.get("lines", []):
        p = _period_from_date(ln.get("date"))
        if p:
            return p
    return _default_period()


def _supplier_known(code):
    """READ-ONLY existence check for a supplier code against the ENGINE-owned suppliers.db
    via dataproduct (mode=ro) — the web request never opens a writable handle to it. A
    missing DB file (fresh install) or any read error degrades to 'unknown' so onboarding
    can proceed. Returns True only when a row is positively found."""
    code = (code or "").strip().upper()
    if not code:
        return False
    try:
        import dataproduct
        con = dataproduct.connect("suppliers")
        try:
            r = con.execute("SELECT 1 FROM suppliers WHERE code=?", (code,)).fetchone()
        finally:
            con.close()
        return r is not None
    except Exception as e:
        _log_exc("supplier existence check", e)
        return False


def _read_first_notice(draft, period):
    """Surface what read-first extraction AUTO-DETECTED (supplier, statement ref/date,
    derived period) and, for an UNKNOWN supplier, either enqueue an auto-onboard job (when
    a VAT id resolves to a country) or flag it UNMATCHED for manual handling (never invent a
    supplier from a low-confidence / VAT-less draft). Returns an HTML banner string. Best-
    effort: any failure here never blocks the review screen."""
    try:
        import supplier_master as SM
        supplier = (draft.get("supplier") or "").strip()
        vat = (draft.get("supplier_vat") or "").strip()
        detected = ('<div class="card"><b class="ok">Auto-detected from the document</b>'
                    '<div class="note">These were read for you; the fields below are '
                    'editable overrides.</div><ul style="margin:6px 0 0 18px">'
                    f'<li>supplier: <b>{esc(supplier or "—")}</b>'
                    + (f' (VAT {esc(vat)})' if vat else "") + '</li>'
                    f'<li>statement ref: <b>{esc(draft.get("statement_ref") or "—")}</b></li>'
                    f'<li>statement date: <b>{esc(draft.get("statement_date") or "—")}</b></li>'
                    f'<li>period (derived): <b>{esc(period or "—")}</b></li></ul></div>')
        if not supplier or _supplier_known(supplier):
            return detected
        # UNKNOWN supplier — onboard only when a VAT id yields a country (never guess).
        country = SM.country_from_vat(vat) if vat else None
        if not country:
            return (detected + '<div class="card"><b class="bad">Unknown supplier — left '
                    'UNMATCHED</b><div class="note">No usable VAT number was read, so the '
                    'supplier was NOT auto-created (a mis-read must never invent master '
                    'data). Set the correct supplier code below, or create the supplier on '
                    'the <a href="/suppliers">Suppliers</a> page, then confirm.</div></div>')
        try:
            import waiting_room as IQ
            jid, _st = IQ.enqueue_onboard(
                supplier, country, legal_name=supplier, vat_number=vat,
                invoice_ref=draft.get("statement_ref"),
                user=session.get("user", "system"))
            return (detected + '<div class="card"><b class="ok">New supplier auto-onboarded '
                    f'(provisional)</b><div class="note">Supplier <b>{esc(supplier)}</b> '
                    f'was not in the master, so a <b>provisional</b> {esc(country)} supplier '
                    f'is being created from VAT <b>{esc(vat)}</b> (job {jid}). It needs admin '
                    'confirmation before it goes active — see <a href="/suppliers">Suppliers '
                    '→ provisional</a>. You can confirm this statement against it now.</div>'
                    '</div>')
        except Exception as e:
            _log_exc("auto-onboard enqueue", e)
            return (detected + '<div class="card"><b class="bad">Could not queue supplier '
                    f'onboarding: {esc(str(e))}</b></div>')
    except Exception as e:
        _log_exc("read-first notice", e)
        return ""


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
            f'<label>extractor (optional)<select name="backend">{opts}</select></label>'
            f'<label>period override (optional)<input name="period" value="{esc(request.values.get("period", ""))}" placeholder="auto (YYYY-MM)" style="width:120px"></label>'
            '<button name="__mode" value="now">Read &amp; review now</button>'
            '<button name="__mode" value="queue" style="background:var(--mut)">Queue for later</button>'
            '</form>'
            '<div class="note"><b>Read-first:</b> just upload — the supplier, statement '
            'reference/date and period are read off the document for you; the fields above '
            'are optional overrides. An unknown supplier identified by its VAT number is '
            'auto-onboarded as a <b>provisional</b> supplier (admin-confirmed before it goes '
            'live); a supplier that can\'t be identified is left UNMATCHED for manual '
            'handling. Nothing is saved until you review and confirm.</div>'
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


def _capture_findings_html(draft):
    """Render the advisory capture checks (VAT-ID structure, in-batch duplicates) on the
    review screen from the draft alone, so a reviewer sees data-quality problems BEFORE
    confirming. IBAN and cross-entity duplicate scope need data not present in the redacted
    draft (and run at confirm time); these never block — the checklist remains the gate."""
    try:
        import capture_checks
        findings = capture_checks.run(draft.get("lines", []),
                                      supplier=draft.get("supplier"),
                                      supplier_vat=draft.get("supplier_vat"))
    except Exception as e:
        _log_exc("capture findings", e)
        return ""
    if not findings:
        return ""
    items = "".join(
        f'<li class="{"bad" if f.get("severity") == "error" else ""}">'
        f'{esc(f.get("severity", "").upper())}: {esc(f.get("message", ""))}</li>'
        for f in findings)
    return ('<div class="card"><b>Capture checks</b> '
            '<span class="note">(advisory — does not block; verify before confirming)</span>'
            f'<ul style="margin:6px 0 0 18px">{items}</ul></div>')


def _capture_upload_sha(draft, intake_job=None):
    """Resolve the ORIGINAL upload's sha256 for a vision draft — the key the persisted
    captured-data file is linked by. Prefers the sha stashed on the draft (sync path),
    else the intake job's sha256 (queue path). Returns '' when unknown. NEVER raises."""
    try:
        s = (draft or {}).get("_upload_sha256") if isinstance(draft, dict) else None
        if s:
            return str(s)
        if intake_job:
            import waiting_room as IQ
            job = IQ.get_job(int(intake_job))
            if job and job.get("sha256"):
                return str(job["sha256"])
    except Exception as e:
        _log_exc("resolve capture upload sha", e)
    return ""


def _capture_document_html(draft, token=None, intake_job=None, upload_sha=None):
    """Render the FULL AI-vision capture document (header + per-transaction table with all
    the richer columns + totals) on the review screen, with Download (JSON + readable text)
    links. EVERY model-supplied value is ESCAPED — the capture document is UNTRUSTED model
    output. Returns '' when the draft carries no `capture` (i.e. not a vision capture)."""
    cap = draft.get("capture")
    if not isinstance(cap, dict) or not cap:
        return ""
    hdr = cap.get("header") or {}
    sup = hdr.get("supplier") or {}
    cust = hdr.get("customer") or {}
    inv = hdr.get("invoice") or {}
    tot = cap.get("totals") or {}

    def _v(x):
        return esc("" if x is None else str(x))

    head_rows = "".join(
        f'<tr><td style="color:var(--mut);width:170px">{esc(label)}</td><td>{_v(val)}</td></tr>'
        for label, val in [
            ("supplier name", sup.get("name")), ("supplier VAT", sup.get("vat_number")),
            ("supplier address", sup.get("address")), ("supplier country", sup.get("country")),
            ("customer name", cust.get("name")), ("customer VAT", cust.get("vat_number")),
            ("customer account/card", cust.get("account_or_card_no")),
            ("invoice number", inv.get("number")), ("issue date", inv.get("issue_date")),
            ("due date", inv.get("due_date")), ("currency", inv.get("currency")),
            ("exchange rate", inv.get("exchange_rate")),
        ])

    cols = ["Date", "Time", "Station", "City", "Country", "Product", "Qty", "Unit",
            "Unit price", "Discount", "Net", "VAT %", "VAT", "Gross", "Card", "Receipt"]
    keys = ["date", "time", "station_name", "city", "country", "product", "quantity",
            "unit", "unit_price", "discount", "net", "vat_rate", "vat", "gross",
            "card_no", "receipt_no"]
    line_rows = ""
    for ln in (cap.get("lines") or []):
        line_rows += "<tr>" + "".join(f"<td>{_v(ln.get(k))}</td>" for k in keys) + "</tr>"
    if not line_rows:
        line_rows = f'<tr><td colspan="{len(cols)}" class="note">No transaction lines captured.</td></tr>'

    tot_html = (
        '<div class="note" style="margin-top:8px">Totals (as captured): '
        f'net <b>{_v(tot.get("net_total"))}</b> · discount <b>{_v(tot.get("discount_total"))}</b> · '
        f'VAT <b>{_v(tot.get("vat_total"))}</b> · gross <b>{_v(tot.get("gross_total"))}</b>.</div>')

    dl = ""
    if token:
        jq = f"?intake_job={esc(str(intake_job))}" if intake_job else ""
        dl = ('<div class="note" style="margin-top:8px">Download the full capture document: '
              f'<a href="/extract/capture/{esc(token)}.json{jq}">JSON</a> · '
              f'<a href="/extract/capture/{esc(token)}.txt{jq}">readable text</a></div>')

    # The PERSISTED second file: when the captured data has been saved as a durable file
    # in the data lake (linked to the original PDF by its upload sha256), surface it as
    # "saved as a file" with the SAME view/download next to the original PDF in the vault.
    saved = ""
    sha = (upload_sha or _capture_upload_sha(draft, intake_job) or "").strip()
    if sha:
        try:
            import capture_file
            if capture_file.has_capture(sha):
                s = esc(sha)
                saved = ('<div class="note" style="margin-top:6px">'
                         '<b class="ok">Saved as a file</b> — this captured data is stored '
                         'permanently as a second file next to the original PDF (in the '
                         'document vault): '
                         f'<a href="/extract/capture-file/{s}.json?view=1">view</a> · '
                         f'<a href="/extract/capture-file/{s}.json">JSON</a> · '
                         f'<a href="/extract/capture-file/{s}.txt">text</a>.</div>')
        except Exception as e:
            _log_exc("review saved-capture link", e)

    return ('<div class="card"><h2>Capture document (AI vision — advisory)</h2>'
            '<div class="note" style="margin-top:0">The comprehensive structured data a '
            'vision model read from the page images. <b>Untrusted model output</b> — verify '
            'every figure against the PDF before confirming; nothing here is authoritative.</div>'
            '<table style="margin-top:8px"><tbody>' + head_rows + '</tbody></table>'
            '<table style="margin-top:10px"><thead><tr>'
            + "".join(f"<th>{h}</th>" for h in cols)
            + f'</tr></thead><tbody>{line_rows}</tbody></table>'
            + tot_html + dl + saved + '</div>')


def _provenance_badge(src):
    """Render a line's data provenance (`_source`) as a labelled badge so a reviewer can
    SEE which lines came from a hallucination-prone AI extraction versus a deterministic
    structured/parser path. AI lines are flagged for extra scrutiny."""
    s = (src or "").strip()
    low = s.lower()
    if low == "ai":
        return '<span class="bad" title="AI-extracted — verify every figure">AI · verify</span>'
    if low == "e-invoice":
        return '<span class="ok" title="structured EN-16931 e-invoice">structured</span>'
    if low.startswith("parse error"):
        return f'<span class="bad" title="{esc(s)}">parse error</span>'
    if not s:
        return '<span class="note">—</span>'
    return f'<span class="note" title="deterministic parser / source PDF">{esc(s)}</span>'


def _persisted_corrections_html(draft):
    """Render the AI corrections log + status PERSISTED on the draft (so the review screen,
    a reload, and the read-only view all surface what the AI corrected). EVERY value is
    ESCAPED (untrusted model output). Returns '' when no corrections were applied. Advisory:
    the corrections are on the PRE-commit draft only; the human Confirm gate still stands."""
    corrections = (draft or {}).get("corrections") or []
    if not corrections:
        return ""
    status = (draft or {}).get("correction_status") or "corrected"
    badge = ('<span class="ok"><b>AI-corrected &amp; verified ✅</b></span>'
             if status == "verified_after_correction"
             else '<span class="warn"><b>AI-corrected (re-verify pending/incomplete)</b></span>')
    items = ""
    for c in corrections:
        items += ('<li>'
                  f'<b>{esc(c.get("field",""))}</b>: captured "<span class="bad">'
                  f'{esc("" if c.get("was") is None else str(c.get("was")))}</span>" '
                  f'→ AI-corrected to "<span class="ok">'
                  f'{esc("" if c.get("now") is None else str(c.get("now")))}</span>" '
                  f'<span class="note">({esc(c.get("source","ai-verify(PDF)"))})</span></li>')
    return ('<div class="card"><h2>AI corrections applied (per PDF)</h2>'
            f'<div style="margin-top:0">{badge}</div>'
            f'<ul style="margin:8px 0 0 18px">{items}</ul>'
            '<div class="note" style="margin-top:8px">These values were written into the '
            'pre-commit draft to match the PDF and are audited. Nothing is registered until '
            'you Confirm below.</div></div>')


def _capture_accuracy_hints(draft):
    """Advisory capture-accuracy context for the draft's supplier from the capture-confidence
    learning loop: an overall 'capture accuracy: X% (n=…)' line and the set of WEAK field
    names to flag with a ⚠️ hint. Returns (accuracy_line_html, {weak_field_name: hint_html}).
    ADVISORY ONLY — purely a hint; it never blocks/gates anything. Best-effort -> ("", {})."""
    try:
        supplier = (draft.get("supplier") or "").strip()
        if not supplier:
            return "", {}
        import capture_confidence as CC
        acc = CC.supplier_accuracy(supplier)
        weak = {}
        for w in CC.weak_fields(supplier):
            pct = f"{w['rate'] * 100:.0f}"
            weak[w["field"]] = (
                f'<span class="bad" title="this supplier&#39;s {esc(w["field"])} is often '
                f'mis-read — double-check">⚠️ often mis-read ({esc(pct)}% ok, n={esc(str(w["n"]))})</span>')
        line = ""
        if acc.get("rate") is not None:
            pct = f"{acc['rate'] * 100:.0f}"
            cls = "ok" if acc["rate"] >= 0.9 else ("bad" if acc["rate"] < 0.8 else "")
            line = (f'<div class="note" style="margin-top:4px">Capture accuracy for '
                    f'<b>{esc(supplier)}</b>: <b class="{cls}">{esc(pct)}%</b> '
                    f'(n={esc(str(acc["n"]))}). '
                    'Advisory only — fields this supplier tends to get mis-read are flagged '
                    '⚠️ below; nothing is changed or gated.</div>')
        return line, weak
    except Exception as e:
        _log_exc("capture-confidence review hints", e)
        return "", {}


def _source_text_html(draft, upload_sha=None):
    """A collapsible 'Full text read from this PDF' panel: shows, line by line, EXACTLY
    what the system read out of the document so the operator can verify nothing was missed.
    Plus links to the co-located folder (original PDF + captured text together). Read-only;
    the text is HTML-escaped (served as content, never executed)."""
    txt = (draft.get("_source_text") if isinstance(draft, dict) else "") or ""
    sha = upload_sha or (draft.get("_upload_sha256") if isinstance(draft, dict) else "") or ""
    if not txt and not sha:
        return ""
    nlines = txt.count("\n") + 1 if txt else 0
    links = ""
    if sha:
        links = (f'<div class="note" style="margin:6px 0">'
                 f'<a href="/extract/folder/{esc(sha)}">📁 Open the saved folder</a> '
                 f'(original document + captured text together) · '
                 f'<a href="/extract/folder/{esc(sha)}/captured.txt">⬇ Download captured text</a></div>')
    body = (f'<pre style="white-space:pre-wrap;max-height:340px;overflow:auto;'
            f'background:#fafbfc;border:1px solid #e2e8f0;border-radius:6px;padding:10px;'
            f'font-size:12px;margin:6px 0">{esc(txt) if txt else "(no text could be read from this document)"}</pre>')
    return ('<div class="card"><h2>Full text read from this PDF '
            f'<span class="note" style="font-weight:400">({nlines} line(s))</span></h2>'
            '<div class="note" style="margin-top:0">This is EXACTLY what the system read '
            'from the document, verbatim. Check every figure here matches the original '
            'before confirming — the structured fields below are derived from this text.</div>'
            + links + body + '</div>')


def _country_supply_summary_html(draft, token=None):
    """A read-only per-country roll-up of the draft lines: net, VAT, gross and the entity
    of supply per country. Surfaces the cross-border breakdown a fuel-card statement
    implies (each country = a separate refund jurisdiction with its own supplying entity).
    Derived purely from the draft lines — invents no figure, changes nothing. Renders only
    when at least one line carries a country. `token` enables the analytics-Excel link."""
    _xl_token = token
    lines = [l for l in (draft.get("lines") or []) if isinstance(l, dict)]
    by_country = {}
    for l in lines:
        ctry = (l.get("country") or "").strip() or "—"
        agg = by_country.setdefault(ctry, {"net": [], "vat": [], "n": 0, "entities": {}})
        agg["net"].append(l.get("net") or 0)
        agg["vat"].append(l.get("vat") or 0)
        agg["n"] += 1
        ent = (l.get("supplier_name") or "").strip()
        if ent:
            agg["entities"][ent] = (l.get("supplier_vat") or "").strip()
    # only show it when there is real country structure (>1 country, or a single named one)
    real = [c for c in by_country if c != "—"]
    if not real:
        return ""
    rows = ""
    tnet = tvat = 0.0
    for ctry in sorted(by_country):
        a = by_country[ctry]
        net = money.fsum(a["net"]); vat = money.fsum(a["vat"])
        tnet += float(net); tvat += float(vat)
        ents = a["entities"]
        ent_html = "<br>".join(
            f'{esc(name)}' + (f' <span class="note">{esc(vatno)}</span>' if vatno else "")
            for name, vatno in ents.items()) or '<span class="note">(header supplier)</span>'
        rows += (f'<tr><td><b>{esc(ctry)}</b></td><td class="r">{a["n"]}</td>'
                 f'<td class="r">{money.f2(net):,.2f}</td>'
                 f'<td class="r">{money.f2(vat):,.2f}</td>'
                 f'<td class="r">{money.f2(float(net)+float(vat)):,.2f}</td>'
                 f'<td>{ent_html}</td></tr>')
    rows += (f'<tr><td><b>All countries</b></td>'
             f'<td class="r"><b>{len(lines)}</b></td>'
             f'<td class="r"><b>{money.f2(tnet):,.2f}</b></td>'
             f'<td class="r"><b>{money.f2(tvat):,.2f}</b></td>'
             f'<td class="r"><b>{money.f2(tnet+tvat):,.2f}</b></td><td></td></tr>')
    return ('<div class="card"><h2>Per-country VAT &amp; entity of supply</h2>'
            '<div class="note" style="margin-top:0">Each country is a separate refund '
            'jurisdiction. NET basis (VAT excluded). The supply entity is the supplier for '
            'that country when the document names one, else the header supplier. '
            + (f'<a href="/extract/capture.xlsx?token={esc(_xl_token)}">⬇ Download '
               'analytics Excel</a> (Transactions · Per-country · Invoice).'
               if _xl_token else "") + '</div>'
            '<table style="margin-top:8px"><thead><tr>'
            + "".join(f"<th>{h}</th>" for h in
                      ["Country", "Lines", "Net EUR", "VAT EUR", "Gross EUR", "Entity of supply"])
            + f'</tr></thead><tbody>{rows}</tbody></table></div>')


def _review_form(draft, token, intake_job=None, period=None, ai_panel="", upload_sha=None):
    acc_line, weak = _capture_accuracy_hints(draft)
    def _wh(field):  # a weak-field hint cell fragment, or empty
        return (" " + weak[field]) if field in weak else ""
    rows = ""
    for i, ln in enumerate(draft.get("lines", [])):
        # Entity of supply for this line — can differ per country on cross-border
        # statements. Shown read-only from the captured draft: line-specific values are
        # highlighted; a line with none inherits the header supplier.
        _sup = ln.get("supplier_name")
        _supvat = ln.get("supplier_vat")
        if _sup:
            _origin = "per-country" if ln.get("supplier_is_line_specific") else "from header"
            _supply_cell = (f'<td class="note">{esc(_sup)}'
                            + (f'<br>{esc(_supvat)}' if _supvat else "")
                            + f'<br><span style="font-size:11px">({_origin})</span></td>')
        else:
            _supply_cell = '<td class="note">—</td>'
        rows += ('<tr>'
                 f'<td><input name="inv_{i}" value="{esc(ln.get("invoice_no") or "")}" style="width:160px">{_wh("line.invoice_no")}</td>'
                 f'<td><input name="date_{i}" value="{esc(ln.get("date") or draft.get("statement_date") or "")}" style="width:100px" placeholder="YYYY-MM-DD">{_wh("line.date")}</td>'
                 f'<td><input name="ctry_{i}" value="{esc(ln.get("country") or "")}" style="width:100px">{_wh("line.country")}</td>'
                 f'<td><input name="ccy_{i}" value="{esc(ln.get("currency") or "EUR")}" style="width:55px">{_wh("line.currency")}</td>'
                 f'<td><input name="net_{i}" value="{ln.get("net",0)}" style="width:90px" class="r">{_wh("line.net")}</td>'
                 f'<td><input name="vat_{i}" value="{ln.get("vat",0)}" style="width:90px" class="r">{_wh("line.vat")}</td>'
                 f'{_supply_cell}'
                 f'<td class="note">{_provenance_badge(ln.get("_source"))}</td></tr>')
    gross = sum((ln.get("net",0) or 0) + (ln.get("vat",0) or 0) for ln in draft.get("lines", []))
    conf = draft.get("confidence","low")
    ccls = {"high":"ok","medium":"","low":"bad"}.get(conf,"")
    _tr = draft.get("_pages_truncated") if isinstance(draft, dict) else None
    trunc_banner = (
        f'<div class="card" style="border-left:4px solid #c0392b;background:#fdecea">'
        f'<b class="bad">⚠ Not all pages were read.</b> Only the first '
        f'{esc(str(_tr.get("read")))} of {esc(str(_tr.get("total")))} pages were captured — '
        f'<b>transactions on later pages are missing</b>. Raise the page limit '
        f'(VISION_CAPTURE_MAX_PAGES) and re-capture before confirming.</div>'
        if isinstance(_tr, dict) else "")
    return (trunc_banner
            + '<div class="card"><h2>Review extracted draft — confirm before anything is saved</h2>'
            f'<div class="note">Source: <b>{esc(draft.get("backend",""))}</b> · '
            f'confidence <span class="{ccls}">{esc(conf)}</span> · '
            f'{len(draft.get("files",[]))} PDF(s). {esc(draft.get("notes",""))}</div>'
            + acc_line
            + _source_text_html(draft, upload_sha=upload_sha)
            + _country_supply_summary_html(draft, token=token)
            + _capture_document_html(draft, token, intake_job, upload_sha=upload_sha)
            + _persisted_corrections_html(draft)
            + _capture_findings_html(draft) +
            '<form method="post" action="/extract/confirm" class="f" style="margin-top:10px">'
            + _csrf_input() +
            f'<input type="hidden" name="token" value="{esc(token)}">'
            + (f'<input type="hidden" name="intake_job" value="{esc(str(intake_job))}">' if intake_job else "")
            + f'<label>supplier code<input name="supplier" value="{esc(draft.get("supplier") or "")}" required>{_wh("supplier.name")}</label>'
            f'<label>statement ref<input name="stmt_ref" value="{esc(draft.get("statement_ref") or "")}" required>{_wh("invoice.statement_ref")}</label>'
            f'<label>statement date<input type="date" name="stmt_date" value="{esc(draft.get("statement_date") or "")}">{_wh("invoice.statement_date")}</label>'
            f'<label>customer<input name="customer" value="{esc((draft.get("customer") or "").strip())}">{_wh("customer.name")}</label>'
            f'<label>period (YYYY-MM)<input name="period" value="{esc(period or request.values.get("period", _default_period()))}" required></label>'
            '</label></div>'
            + '<table style="margin-top:10px"><thead><tr>'
            + "".join(f"<th>{h}</th>" for h in ["Invoice no","Date","Country","Ccy","Net","VAT","Supply entity","Provenance"])
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
            + _ai_verify_button(token, intake_job, period)
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


def _ai_status_line(label, st):
    """Render the precise live AI-pipeline status (from ai_verify.status()) for the AI
    settings cards. ACTIVE => green 'ACTIVE (Provider, model …)'; INACTIVE => red
    'INACTIVE — reason: …'. The reason text is esc()'d (it can carry a provider/setting
    name). `st` is the dict from ai_verify.status()."""
    if st.get("active"):
        return (f'<b class="ok">{esc(label)}: ACTIVE</b> — {esc(st.get("provider_label") or "")}, '
                f'model <b>{esc(st.get("model") or "")}</b>.')
    return (f'<b class="bad">{esc(label)}: INACTIVE</b> — reason: '
            f'{esc(st.get("reason") or "not configured")}.')


def _ai_test_connection_form():
    """The 'Test AI connection' control on the AI settings card. Posts back to /admin; the
    result banner is rendered server-side after a MINIMAL real call (text-only, no PDF)."""
    return ('<form method="post" class="f" style="margin-top:8px">'
            + _csrf_input()
            + '<button name="__act" value="test_ai_connection">Test AI connection</button>'
            + '<span class="note" style="margin-left:8px">Sends a tiny text-only prompt to '
              'the configured vision backend to verify the key/model — no PDF, negligible '
              'cost.</span>'
            + '</form>')


def _ai_verify_button(token, intake_job=None, period=None):
    """The 'Verify against PDF with AI' control under the draft. Shown ONLY when AI
    verification is enabled (opt-in setting ON AND a vision backend configured). This is the
    DELIBERATE exception that sends the ORIGINAL PDF to the AI provider, so it is loudly
    labelled and never shown unless explicitly enabled. Advisory only — never gates commit.
    Needs the queue job to locate the original PDF, so it appears only for queued jobs."""
    import ai_verify
    if not (intake_job and ai_verify.enabled()):
        return ""
    prov = esc(ai_verify.provider_label() or "the configured AI provider")
    return ('<form method="post" action="/extract/ai-verify" style="margin-top:10px">'
            + _csrf_input()
            + f'<input type="hidden" name="token" value="{esc(token)}">'
            + f'<input type="hidden" name="intake_job" value="{esc(str(intake_job))}">'
            + f'<input type="hidden" name="period" value="{esc(period or request.values.get("period", _default_period()))}">'
            + '<button>Verify against PDF with AI (advisory)</button>'
            + '<span class="bad" style="margin-left:8px">⚠️ Sends the ORIGINAL invoice PDF '
              f'(which may contain IBANs/bank details) to {prov} for verification. Advisory '
              'only — never changes a figure or gates the commit.</span>'
            + '</form>')


def _feed_validator_trust(supplier, vr):
    """Feed the confidence model from the DETERMINISTIC validator outcome, per country:
    a country whose lines all passed (no error verdict) is a CLEAN signal; a country with
    any error line is a FLAGGED signal. This is the ground-truth learning signal the trust
    model was missing — previously trust learned ONLY from the advisory AI review. It still
    governs nothing but whether that advisory review may be skipped; no legal gate moves.
    Best-effort: confidence.record_validation never raises."""
    try:
        import confidence
        order = {"ok": 0, "warn": 1, "error": 2}
        worst = {}
        for res in vr.get("lines", []):
            c = (res["line"].get("country") or "").strip()
            worst[c] = max(worst.get(c, 0), order.get(res["verdict"], 0))
        for c, w in worst.items():
            confidence.record_validation(supplier, c, clean=(w < 2), source="validator",
                                         detail="deterministic batch validation")
    except Exception as e:
        _log_exc("confidence validator-feed", e)


def _norm_capture_val(v):
    """Normalise a value for the captured-vs-confirmed diff: trimmed string, with numeric
    amounts compared on VALUE (so '12.5' == '12.50' == 12.5 — a reformat is NOT an edit).
    Pure; never raises."""
    s = "" if v is None else str(v).strip()
    try:
        return ("num", float(s))
    except (TypeError, ValueError):
        return ("str", s)


def _feed_capture_confidence_confirm(supplier, captured_draft):
    """HUMAN-EDIT-AT-CONFIRM training signal for the capture-confidence learning loop.

    Diff each CAPTURED field (from the draft the human reviewed) against the value the human
    just CONFIRMED in the form: a field left UNCHANGED is was_correct=True; a field the human
    EDITED is was_correct=False. Header fields map to 'invoice.statement_ref'/'statement_date'/
    'supplier.name'/'customer.name'; per-line fields map to a normalized 'line.<field>' name
    (one outcome per captured line). ADVISORY telemetry only — best-effort, NEVER raises
    (a failure must never break the confirm/registration path)."""
    try:
        import capture_confidence as CC
        supplier = (supplier or "").strip()
        if not supplier or not isinstance(captured_draft, dict):
            return

        def _emit(field, captured, confirmed):
            if _norm_capture_val(captured) == _norm_capture_val(""):
                return  # nothing was captured for this field -> not a capture signal
            ok = _norm_capture_val(captured) == _norm_capture_val(confirmed)
            CC.record_outcome(supplier, field, was_correct=ok, source="confirm-edit",
                              detail="human confirm vs captured")

        # header fields the confirm form exposes (captured value -> confirmed form value)
        _emit("supplier.name", captured_draft.get("supplier"),
              request.form.get("supplier", ""))
        _emit("invoice.statement_ref", captured_draft.get("statement_ref"),
              request.form.get("stmt_ref", ""))
        _emit("invoice.statement_date", captured_draft.get("statement_date"),
              request.form.get("stmt_date", ""))
        _emit("customer.name", captured_draft.get("customer"),
              request.form.get("customer", ""))

        # per-line fields: same positional index the review form rendered (inv_/date_/…)
        for i, ln in enumerate(captured_draft.get("lines", []) or []):
            if not isinstance(ln, dict):
                continue
            _emit("line.invoice_no", ln.get("invoice_no"), request.form.get(f"inv_{i}", ""))
            _emit("line.date", ln.get("date"), request.form.get(f"date_{i}", ""))
            _emit("line.country", ln.get("country"), request.form.get(f"ctry_{i}", ""))
            _emit("line.currency", ln.get("currency"), request.form.get(f"ccy_{i}", ""))
            _emit("line.net", ln.get("net"), request.form.get(f"net_{i}", ""))
            _emit("line.vat", ln.get("vat"), request.form.get(f"vat_{i}", ""))
    except Exception as e:
        _log_exc("capture-confidence confirm-feed", e)


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
    _feed_validator_trust(supplier, vr)        # ground-truth signal into the trust model
    # CAPTURE-CONFIDENCE (advisory learning loop): diff the CAPTURED draft against what the
    # human just confirmed — an unchanged field is a correct capture, an edited one a miss.
    # Best-effort, before the draft file is dropped below; never gates the commit.
    _feed_capture_confidence_confirm(supplier, _load_draft(token))
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


def _capture_text(cap):
    """A readable plain-text rendering of a capture document (header + per-transaction
    lines + totals) for the .txt download. Delegates to the single reusable builder in
    `capture_file` so the transient download and the persisted .txt artifact stay in sync."""
    import capture_file
    return capture_file.build_text(cap)


@app.route("/extract/capture/<token>.<fmt>")
def extract_capture_download(token, fmt):
    """Download the FULL AI-vision capture document for a review token, as JSON or readable
    text. Reads the stashed review draft (the capture doc rides under its `capture` key).
    Access: data_import (enforced in _guard). Read-only; serves the captured document only."""
    if fmt not in ("json", "txt"):
        return page('<div class="card"><b class="bad">Unknown format.</b></div>', "ext")
    draft = _load_draft(token)
    cap = (draft or {}).get("capture") if isinstance(draft, dict) else None
    if not cap:
        # fall back to the persisted queue draft when a job id is supplied
        ij = request.args.get("intake_job")
        if ij:
            try:
                import waiting_room as IQ
                stored = IQ.get_stored_draft(int(ij))
                cap = (stored or {}).get("capture")
            except Exception as e:
                _log_exc("capture download stored draft", e)
    if not cap:
        return page('<div class="card"><b class="bad">No capture document is available for '
                    'this draft.</b></div><p><a href="/extract">← back to import</a></p>', "ext")
    if fmt == "json":
        import json as _json
        body = _json.dumps(cap, ensure_ascii=False, indent=2, default=str)
        mime = "application/json"
        fname = f"capture-{token}.json"
    else:
        body = _capture_text(cap)
        mime = "text/plain; charset=utf-8"
        fname = f"capture-{token}.txt"
    from flask import Response
    return Response(body, mimetype=mime,
                    headers={"Content-Disposition": f'attachment; filename="{fname}"'})


def _serve_capture_file(sha, fmt):
    """Serve the PERSISTED captured-data file (the durable second file next to the original
    PDF) for an upload sha256, as JSON or readable text. `?view=1` renders inline (View);
    otherwise it downloads (attachment). Reads the artifact bytes via the app-owned data
    lake; reads NO product DB. Read-only; escapes nothing into HTML (it serves raw bytes
    with a non-HTML content type, so the captured fields cannot execute as markup)."""
    if fmt not in ("json", "txt"):
        return page('<div class="card"><b class="bad">Unknown format.</b></div>', "ext")
    inline = request.args.get("view") in ("1", "true", "yes")
    body, mime = None, None
    try:
        import capture_file, data_lake
        latest = capture_file.latest_for_upload(sha)
        row = latest.get("json") if fmt == "json" else latest.get("text")
        if row:
            data = data_lake.get(row["stored_path"])
            if data:
                body = data
                mime = ("application/json" if fmt == "json"
                        else "text/plain; charset=utf-8")
    except Exception as e:
        _log_exc("serve capture file", e)
    if body is None:
        return page('<div class="card"><b class="bad">No saved captured-data file is '
                    'available for this document.</b></div>'
                    '<p><a href="/documents">← back to documents</a></p>', "doc")
    from flask import Response
    fname = f"capture-{esc(str(sha)[:12])}.{ 'json' if fmt == 'json' else 'txt' }"
    disp = "inline" if inline else "attachment"
    # When rendering inline we still force a NON-HTML content type so untrusted captured
    # fields cannot run as markup in the browser (defence-in-depth alongside esc()).
    return Response(body, mimetype=mime,
                    headers={"Content-Disposition": f'{disp}; filename="{fname}"',
                             "X-Content-Type-Options": "nosniff"})


@app.route("/capture-file/<sha>.<fmt>")
def capture_file_download(sha, fmt):
    """Document-vault view/download of the PERSISTED captured-data file for an original PDF,
    keyed by the upload's sha256. Access: documents (enforced in _guard). The captured-data
    file is the SECOND file paired with the original PDF in the vault."""
    return _serve_capture_file(sha, fmt)


@app.route("/extract/capture-file/<sha>.<fmt>")
def extract_capture_file_download(sha, fmt):
    """Intake-review view/download of the PERSISTED captured-data file (the same durable
    artifact surfaced in the document vault), keyed by the upload's sha256. Access:
    data_import (enforced in _guard) so a reviewer can open the saved file from review."""
    return _serve_capture_file(sha, fmt)


@app.route("/extract/capture.xlsx")
def extract_capture_excel():
    """Download the captured invoice as a typed, analytics-ready .xlsx (Transactions /
    Per-country / Invoice sheets). Access: data_import (enforced in _guard). Built from the
    stashed review draft for the token, or the queue's stored draft via ?intake_job=."""
    token = request.args.get("token", "")
    draft = _load_draft(token)
    if not draft:
        ij = request.args.get("intake_job")
        if ij:
            try:
                import waiting_room as IQ
                draft = IQ.get_stored_draft(int(ij))
            except Exception as e:
                _log_exc("capture excel stored draft", e)
    if not draft:
        return page('<div class="card"><b class="bad">No draft is available to export.</b>'
                    '</div><p><a href="/extract">← back to import</a></p>', "ext")
    try:
        import capture_excel
        src = (draft.get("files") or [{}])[0].get("name") if draft.get("files") else None
        data = capture_excel.build(draft, source_name=src)
    except Exception as e:
        _log_exc("capture excel build", e)
        return page('<div class="card"><b class="bad">Could not build the Excel file.</b>'
                    '</div>', "ext")
    from flask import Response
    return Response(data,
                    mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": 'attachment; filename="captured_invoice.xlsx"',
                             "X-Content-Type-Options": "nosniff"})


@app.route("/extract/folder/<sha>")
def extract_folder(sha):
    """List the co-located CAPTURE FOLDER for an upload (original document(s) + the
    captured text together), with a download link per file. Access: data_import (enforced
    in _guard). Read-only; serves only the files the app itself wrote to captures/<sha>/."""
    import capture_folder
    files = capture_folder.listing(sha)
    if not files:
        return page('<div class="card"><b class="bad">No saved folder for this document.</b>'
                    '<div class="note">A folder is written when a document is uploaded and '
                    'read. Older uploads (before this feature) have none.</div></div>'
                    '<p><a href="/extract">← back to import</a></p>', "ext")
    rows = "".join(
        f'<tr><td><a href="/extract/folder/{esc(sha)}/{esc(fr["name"])}">{esc(fr["name"])}</a></td>'
        f'<td class="r note">{fr.get("size", 0):,} bytes</td></tr>' for fr in files)
    return page('<div class="card"><h2>Saved folder — original document + captured text</h2>'
                f'<div class="note">Folder key: <code>{esc(sha)}</code>. Both the source '
                'document and the text read from it are stored together here.</div>'
                f'<table style="margin-top:8px"><thead><tr><th>File</th><th>Size</th></tr>'
                f'</thead><tbody>{rows}</tbody></table></div>'
                '<p><a href="/extract">← back to import</a></p>', "ext")


@app.route("/extract/folder/<sha>/<path:name>")
def extract_folder_file(sha, name):
    """Download a single file from an upload's capture folder. Path-traversal safe (the
    name is reduced to a basename inside the folder). Access: data_import (in _guard)."""
    import capture_folder
    data = capture_folder.file_bytes(sha, name)
    if data is None:
        return page('<div class="card"><b class="bad">File not found in the saved folder.</b>'
                    '</div>', "ext")
    from flask import Response
    safe = capture_folder._safe_name(name)
    low = safe.lower()
    mime = ("application/pdf" if low.endswith(".pdf") else
            "application/json" if low.endswith(".json") else
            "application/xml" if low.endswith(".xml") else
            "text/plain; charset=utf-8")
    return Response(data, mimetype=mime,
                    headers={"Content-Disposition": f'attachment; filename="{safe}"',
                             "X-Content-Type-Options": "nosniff"})


@app.route("/extract/ai-verify", methods=["POST"])
def extract_ai_verify():
    """ADVISORY AI VERIFICATION of an extracted draft against the ORIGINAL PDF using a
    vision model. Re-renders the same review/confirm screen with an appended verdict panel.
    Access: data_import (enforced in _guard). NEVER mutates the draft, NEVER gates commit —
    the /extract/confirm deterministic gate is untouched.

    This is the deliberate, loudly-gated exception that DOES send the PDF: it only runs when
    `ai_verify.enabled()` (opt-in setting ON + vision backend), and the original PDF bytes
    are re-derived from the queue job's kept inbox file (so it is a queued-job action)."""
    import ai_verify, waiting_room as IQ, extract as EX
    token = request.form.get("token", "")
    intake_job = request.form.get("intake_job") or None
    period = request.form.get("period") or None
    draft = _load_draft(token)
    if draft is None:
        return page('<div class="card"><b class="bad">This draft is no longer available '
                    'for review (the session expired). Re-extract the batch.</b></div>'
                    '<p><a href="/extract">← back to import</a></p>', "ext")
    if not (intake_job and ai_verify.enabled()):
        # belt-and-braces: never call out / render the panel when off
        return page(_review_form(draft, token, intake_job=intake_job, period=period), "ext")
    # Re-derive the ORIGINAL PDF bytes from the queue job's kept inbox file. We verify the
    # FIRST PDF of the batch (one statement document per review). Never raises out of here.
    pdf_bytes = b""
    try:
        job = IQ.get_job(int(intake_job))
        if job:
            pairs = EX.unpack(IQ.read_bytes(job["stored_path"]), job["filename"])
            if pairs:
                pdf_bytes = pairs[0][1]
    except Exception as e:
        _log_exc("ai verify load pdf", e)
    try:
        result = ai_verify.verify(pdf_bytes, draft)
    except Exception as e:                              # verify() is best-effort; defensive
        _log_exc("ai verify", e)
        result = {"verdict": "unavailable", "fields": [],
                  "notes": "AI verification is unavailable right now. Advisory only — "
                           "nothing was changed; you can still confirm the draft.",
                  "provider": "", "model": ""}
    # Audit that a verification ran (job id + verdict + provider) — NEVER the PDF bytes,
    # image data, or any secret. Best-effort: an audit failure never breaks the review.
    try:
        scon = _auth.connect()
        _audit_mod.record_event(scon, "ai_verify", str(intake_job), "AI_VERIFY",
                                {"verdict": result.get("verdict"),
                                 "provider": result.get("provider"),
                                 "model": result.get("model"),
                                 "pages": result.get("pages"),
                                 "statement_ref": draft.get("statement_ref")})
        scon.close()
    except Exception as e:
        _log_exc("ai verify audit", e)
    panel = _ai_verify_panel(result, token=token, intake_job=intake_job, period=period)
    return page(_review_form(draft, token, intake_job=intake_job, period=period,
                             ai_panel=panel), "ext")


@app.route("/extract/ai-correct", methods=["POST"])
def extract_ai_correct():
    """AI CORRECTION + RE-VERIFY loop. Given a draft whose AI verification found
    discrepancies against the ORIGINAL PDF, apply the PDF-authoritative values back into the
    PRE-commit draft (ai_verify.apply_corrections), PERSIST the corrected draft + a visible
    corrections log + status, AUDIT the batch (field names only — never the PDF bytes), then
    automatically RE-VERIFY the corrected draft and render the corrections log + the re-verify
    verdict. Access: data_import (enforced in _guard).

    SAFETY: the corrections edit the PRE-commit draft ONLY. Nothing registers here — the
    existing /extract/confirm human gate still stands; the human reviews the corrections +
    the ✅ status and clicks Confirm. Gated exactly like verify (opt-in setting ON + a vision
    backend); OFF => this action is unavailable and falls back to the plain review form."""
    import ai_verify, waiting_room as IQ, extract as EX
    token = request.form.get("token", "")
    intake_job = request.form.get("intake_job") or None
    period = request.form.get("period") or None
    draft = _load_draft(token)
    if draft is None:
        return page('<div class="card"><b class="bad">This draft is no longer available '
                    'for review (the session expired). Re-extract the batch.</b></div>'
                    '<p><a href="/extract">← back to import</a></p>', "ext")
    if not (intake_job and ai_verify.enabled()):
        return page(_review_form(draft, token, intake_job=intake_job, period=period), "ext")
    # Re-derive the ORIGINAL PDF bytes from the queue job (same as verify). Never raises out.
    pdf_bytes = b""
    try:
        job = IQ.get_job(int(intake_job))
        if job:
            pairs = EX.unpack(IQ.read_bytes(job["stored_path"]), job["filename"])
            if pairs:
                pdf_bytes = pairs[0][1]
    except Exception as e:
        _log_exc("ai correct load pdf", e)
    # 1) Verify the CURRENT draft to obtain the discrepancy verdict to correct from.
    try:
        verdict = ai_verify.verify(pdf_bytes, draft)
    except Exception as e:
        _log_exc("ai correct verify", e)
        verdict = {"verdict": "unavailable", "fields": [], "notes": "", "provider": "",
                   "model": "", "pages": 0}
    # 2) Apply the PDF-authoritative corrections onto a COPY of the draft (never in place).
    try:
        corrected, corrections = ai_verify.apply_corrections(draft, verdict)
    except Exception as e:                              # apply_corrections is best-effort
        _log_exc("ai correct apply", e)
        corrected, corrections = draft, []
    # 3) Persist: store the corrections log + status on the draft so the review screen and
    #    downstream see it; the CORRECTED draft becomes what Confirm registers.
    corrected = dict(corrected or {})
    corrected["corrections"] = (corrected.get("corrections") or []) + corrections
    # 4) Auto re-verify the corrected draft (same one-batch capture-doc-vs-PDF check).
    try:
        reverify = ai_verify.verify(pdf_bytes, corrected)
    except Exception as e:
        _log_exc("ai correct re-verify", e)
        reverify = {"verdict": "unavailable", "fields": [], "notes": "", "provider": "",
                    "model": "", "pages": 0}
    corrected["correction_status"] = ("verified_after_correction"
                                      if reverify.get("verdict") == "confirmed" else "corrected")
    corrected["reverify"] = {"verdict": reverify.get("verdict"),
                             "fields": reverify.get("fields", []),
                             "notes": reverify.get("notes", ""),
                             "provider": reverify.get("provider", ""),
                             "model": reverify.get("model", "")}
    _stash_draft(token, corrected)
    # RE-SAVE the persisted capture file so the durable second file reflects the AI-verified /
    # CORRECTED capture (newest-wins versioning, linked to the same upload sha). Best-effort.
    try:
        import capture_file
        upload_sha = (job or {}).get("sha256")
        if upload_sha:
            capture_file.persist(corrected, upload_sha,
                                 source_name=(job or {}).get("filename"), backend="vision",
                                 actor=session.get("user", "system"))
    except Exception as e:
        _log_exc("re-persist capture file after corrections", e)
    # 5) Audit the correction batch: job/doc id + count + provider/model + the FIELD NAMES
    #    only — NEVER the PDF bytes, image data, or any secret. Best-effort.
    try:
        scon = _auth.connect()
        _audit_mod.record_event(scon, "ai_verify", str(intake_job), "AI_CORRECT",
                                {"count": len(corrections),
                                 "fields": [c.get("field") for c in corrections],
                                 "provider": reverify.get("provider"),
                                 "model": reverify.get("model"),
                                 "correction_status": corrected.get("correction_status"),
                                 "reverify": reverify.get("verdict"),
                                 "statement_ref": corrected.get("statement_ref")})
        scon.close()
    except Exception as e:
        _log_exc("ai correct audit", e)
    panel = (_ai_correction_panel(corrections, reverify)
             + _ai_verify_panel(reverify, token=token, intake_job=intake_job, period=period))
    # render the CORRECTED draft (so Confirm registers the corrected values)
    corrected = _load_draft(token) or corrected
    return page(_review_form(corrected, token, intake_job=intake_job, period=period,
                             ai_panel=panel), "ext")


def _ai_verify_field_group(name):
    """Group a verifier field name into a section for display. The PDF is authoritative and
    the capture document is rich (header / per-line / totals), so we bucket fields by their
    name prefix for a clean, scannable table. Pure; name itself is escaped at render time."""
    n = (name or "").lower()
    if n.startswith("line") or n.startswith("lines"):
        return "Lines"
    if n.startswith("total"):
        return "Totals"
    if n.startswith(("invoice", "supplier", "customer", "header", "currency", "statement")):
        return "Header"
    return "Other"


def _ai_correction_action(token, intake_job, period):
    """The 'Apply AI corrections & re-verify' control, shown under a DISCREPANCY verdict when
    AI verification is enabled. It posts to /extract/ai-correct, which writes the PDF values
    into the PRE-commit draft and re-verifies. Loudly labelled: the AI edits the draft, but
    nothing registers without the human Confirm. Returns '' unless enabled + a queued job."""
    import ai_verify
    if not (token and intake_job and ai_verify.enabled()):
        return ""
    prov = esc(ai_verify.provider_label() or "the configured AI provider")
    return ('<form method="post" action="/extract/ai-correct" style="margin-top:10px">'
            + _csrf_input()
            + f'<input type="hidden" name="token" value="{esc(token)}">'
            + f'<input type="hidden" name="intake_job" value="{esc(str(intake_job))}">'
            + f'<input type="hidden" name="period" value="{esc(period or request.values.get("period", _default_period()))}">'
            + '<button>Apply AI corrections &amp; re-verify</button>'
            + '<span class="note" style="margin-left:8px">Writes the PDF value into the '
              f'pre-commit draft for each mismatch above (via {prov}), then re-verifies. The '
              'corrections are shown and audited; <b>nothing registers</b> until you Confirm.</span>'
            + '</form>')


def _ai_correction_panel(corrections, reverify):
    """Render the CORRECTIONS LOG (each change as field: captured "X" → AI-corrected to "Y"
    (per PDF)) + the re-verify outcome banner (green 'AI-corrected & re-verified ✅' when the
    re-verify confirmed, else 'AI-corrected — discrepancies remain'). EVERY value is ESCAPED
    (untrusted model output). Advisory: the human Confirm gate still stands."""
    if not corrections:
        body = ('<div class="note">No correctable discrepancies were found — nothing in the '
                'capture document could be safely corrected from the PDF (any remaining '
                'mismatches are left flagged above for manual review).</div>')
    else:
        items = ""
        for c in corrections:
            items += ('<li>'
                      f'<b>{esc(c.get("field",""))}</b>: captured "<span class="bad">'
                      f'{esc("" if c.get("was") is None else str(c.get("was")))}</span>" '
                      f'→ AI-corrected to "<span class="ok">'
                      f'{esc("" if c.get("now") is None else str(c.get("now")))}</span>" '
                      '<span class="note">(per PDF)</span></li>')
        body = (f'<div class="note">{len(corrections)} field(s) corrected to the PDF value '
                '(applied to the pre-commit draft only):</div>'
                f'<ul style="margin:6px 0 0 18px">{items}</ul>')
    rv = (reverify or {}).get("verdict")
    if rv == "confirmed":
        banner = '<span class="ok"><b>AI-corrected &amp; re-verified ✅</b></span>'
    elif rv == "discrepancies":
        banner = ('<span class="warn"><b>AI-corrected — discrepancies remain</b></span> '
                  '<span class="note">some captured fields still disagree with the PDF '
                  '(shown below); review them before confirming.</span>')
    elif rv in ("unreadable", "unavailable"):
        banner = ('<span class="note"><b>AI-corrected — re-verification unavailable</b></span> '
                  '<span class="note">the corrections were applied; re-verification could '
                  'not run right now.</span>')
    else:
        banner = '<span class="note"><b>Corrections applied</b></span>'
    return ('<div class="card"><h2>AI corrections &amp; re-verify</h2>'
            f'<div style="margin-top:0">{banner}</div>'
            f'<div style="margin-top:8px">{body}</div>'
            '<div class="note" style="margin-top:8px">The AI edited the PRE-commit draft to '
            'match the PDF; every change above is audited. Nothing has been registered — '
            'review the corrections and click <b>Confirm</b> below to register.</div></div>')


def _ai_verify_panel(result, token=None, intake_job=None, period=None):
    """Render the verification verdict: an overall badge (confirmed=green / discrepancies=
    amber with each mismatch shown / unreadable|unavailable=muted) + the per-field rows
    GROUPED (Header / Lines / Totals / Other) + notes. The PDF is the source of truth, so a
    mismatch shows the captured value vs what the PDF actually shows. EVERY model-supplied
    value is ESCAPED (treated as untrusted). When the verdict is `discrepancies` and the
    feature is enabled, the 'Apply AI corrections & re-verify' action is offered. The
    correction edits the PRE-commit draft only — nothing registers without the human Confirm."""
    verdict = result.get("verdict") or "unavailable"
    badge = {
        "confirmed": ('ok', 'Confirmed — the capture matches the PDF'),
        "discrepancies": ('warn', 'Discrepancies found — the PDF disagrees with the capture below'),
        "unreadable": ('note', 'Unreadable — the AI could not read the page images'),
        "unavailable": ('note', 'Unavailable — no verification was performed'),
    }.get(verdict, ('note', 'Unavailable'))
    cls, label = badge
    # Bucket fields into stable sections, preserving model order within each section.
    order = ["Header", "Lines", "Totals", "Other"]
    groups = {g: [] for g in order}
    for f in result.get("fields", []) or []:
        groups[_ai_verify_field_group(f.get("name"))].append(f)
    rows = ""
    for g in order:
        items = groups[g]
        if not items:
            continue
        rows += (f'<tr><td colspan="2" class="note" style="font-weight:600">{esc(g)}</td></tr>')
        for f in items:
            match = bool(f.get("match"))
            rcls = "" if match else "warn"
            if match:
                detail = '<span class="ok">match</span>'
            else:
                # captured "X" vs PDF shows "Y" — both escaped (untrusted model output); the
                # PDF is authoritative, so it is labelled as what the document actually shows.
                detail = (f'captured "<b>{esc(f.get("extracted",""))}</b>" vs PDF shows '
                          f'"<b>{esc(f.get("document",""))}</b>"')
            rows += (f'<tr class="{rcls}"><td>{esc(f.get("name",""))}</td><td>{detail}</td></tr>')
    if not rows:
        rows = '<tr><td colspan="2" class="note">No field-level detail returned.</td></tr>'
    tbl = ('<table style="margin-top:8px"><thead><tr><th>Field</th>'
           '<th>Captured vs PDF</th></tr></thead>'
           f'<tbody>{rows}</tbody></table>')
    notes = result.get("notes")
    notes_html = (f'<div class="note" style="margin-top:8px"><b>Notes:</b> {esc(notes)}</div>'
                  if notes else "")
    prov = result.get("provider") or ""
    prov_html = ('<div class="note" style="margin-top:8px">'
                 + (f'{esc(prov)}' + (f' · {esc(result.get("model",""))}'
                                      if result.get("model") else "") + ' · ' if prov else "")
                 + 'advisory only — the original PDF was sent for verification; nothing was '
                   'changed and your confirmation is still required.</div>')
    # When discrepancies remain, offer the AI correction + re-verify action (gated/enabled).
    action = (_ai_correction_action(token, intake_job, period)
              if verdict == "discrepancies" else "")
    return ('<div class="card"><h2>AI verification against the PDF (advisory)</h2>'
            f'<div style="margin-top:0"><span class="{cls}"><b>{esc(label)}</b></span></div>'
            + tbl + notes_html + prov_html + action + '</div>')


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
        has_draft = bool(j.get("draft_supplier")) or bool(j.get("draft"))
        if st == "ready":
            act_cell = (f'<a href="/queue/review/{j["id"]}" '
                        'style="font-weight:600">Review extracted data →</a>')
        elif st in ("failed", "waiting", "held"):
            act_cell = retry_btn
        elif st == "done" and has_draft:
            # the extracted data stays discoverable after commit (read-only view)
            act_cell = f'<a href="/queue/review/{j["id"]}">View extracted data</a>'
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
        elif st == "done" and (j.get("error") or "").startswith("auto-filed"):
            # auto-pilot intake filed this WITHOUT a human reviewer — make it distinguishable
            # so the queue does not imply a person registered it.
            statetxt = f'{esc(st)}<br><span class="note">auto-filed via autopilot</span>'
        # supplier as resolved by extraction (only known once ready); show the draft
        # confidence alongside it as the extraction-quality signal.
        supplier = j.get("draft_supplier")
        if supplier:
            conf = j.get("draft_confidence")
            sup_cell = (f'{esc(supplier)}<br><span class="note">conf: {esc(conf)}</span>'
                        if conf else esc(supplier))
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

def _read_only_draft_view(job, draft):
    """A read-only 'what was read' view of a draft for a job that is no longer editable
    (e.g. already committed/done) — so the extracted data stays discoverable after the
    fact. Every DB value is escaped. Shows supplier, invoice no/statement ref, date,
    currency, line items, detected totals and confidence, plus any provisional-supplier
    notice; there is no Confirm action (the editable flow is the `ready` path)."""
    lines = draft.get("lines", []) or []
    rows = ""
    for ln in lines:
        rows += ('<tr>'
                 f'<td>{esc(ln.get("invoice_no") or "")}</td>'
                 f'<td>{esc(ln.get("date") or draft.get("statement_date") or "")}</td>'
                 f'<td>{esc(ln.get("country") or "")}</td>'
                 f'<td>{esc(ln.get("currency") or "EUR")}</td>'
                 f'<td class="r">{esc(str(ln.get("net", 0)))}</td>'
                 f'<td class="r">{esc(str(ln.get("vat", 0)))}</td>'
                 f'<td class="note">{_provenance_badge(ln.get("_source"))}</td></tr>')
    gross = sum((ln.get("net", 0) or 0) + (ln.get("vat", 0) or 0) for ln in lines)
    conf = draft.get("confidence", "low")
    ccls = {"high": "ok", "medium": "", "low": "bad"}.get(conf, "")
    vat = (draft.get("supplier_vat") or "").strip()
    head = (f'<div class="card"><h2>Extracted data — job {job["id"]} '
            f'<span class="note">({esc(job["status"])})</span></h2>'
            '<div class="note">Read-only view of what was read from the document. This job '
            'has already been processed; nothing here can be edited or re-committed.</div>'
            '<table style="margin-top:8px"><tbody>'
            f'<tr><td style="color:var(--mut);width:160px">supplier</td><td><b>{esc(draft.get("supplier") or "—")}</b>'
            + (f' (VAT {esc(vat)})' if vat else "") + '</td></tr>'
            f'<tr><td style="color:var(--mut)">statement ref</td><td>{esc(draft.get("statement_ref") or "—")}</td></tr>'
            f'<tr><td style="color:var(--mut)">statement date</td><td>{esc(draft.get("statement_date") or "—")}</td></tr>'
            f'<tr><td style="color:var(--mut)">currency</td><td>{esc(draft.get("currency") or "EUR")}</td></tr>'
            f'<tr><td style="color:var(--mut)">confidence</td><td class="{ccls}">{esc(conf)}</td></tr>'
            f'<tr><td style="color:var(--mut)">source</td><td>{esc(draft.get("backend") or "")}</td></tr>'
            '</tbody></table>'
            + _capture_document_html(draft, upload_sha=(job or {}).get("sha256"))
            + _capture_findings_html(draft)
            + '<table style="margin-top:10px"><thead><tr>'
            + "".join(f"<th>{h}</th>" for h in ["Invoice no", "Date", "Country", "Ccy", "Net", "VAT", "Provenance"])
            + f'</tr></thead><tbody>{rows}</tbody></table>'
            f'<div class="note" style="margin-top:8px">Detected gross total: <b>{gross:,.2f}</b> '
            f'over {len(lines)} line(s).</div>'
            '<p style="margin-top:10px"><a href="/queue">← back to the waiting room</a></p>'
            '</div>')
    return head


@app.route("/queue/review/<int:job_id>")
def intake_review(job_id):
    """Review a queue job's extracted data. A `ready` job opens the standard editable
    review/confirm screen (source PDF bytes re-derived from the kept inbox file and
    stashed for the existing confirm path; on commit the job is marked done). A job that
    already produced a draft but is no longer editable (e.g. `done`) renders a READ-ONLY
    'what was read' view so the extracted data stays discoverable after the fact."""
    import waiting_room as IQ, extract as EX
    import os as _os, pickle
    job = IQ.get_job(job_id)
    if not job:
        return page('<div class="card"><b class="bad">No such job.</b></div>'
                    '<p><a href="/queue">← back to the waiting room</a></p>', "queue")
    if job["status"] != "ready":
        # not editable — but if a draft was produced (ready->done committed, etc.) show it
        # read-only rather than a dead-end "not ready" message.
        stored = IQ.get_stored_draft(job_id)
        if stored is not None:
            return page(_read_only_draft_view(job, stored), "queue")
        return page('<div class="card"><b class="bad">This job has no extracted draft yet '
                    '(it may still be queued, processing, waiting, or failed).</b></div>'
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
    # READ-FIRST for the queued path too: derive the period from the document and surface
    # what was auto-detected (auto-onboarding an unknown-but-VAT-identified supplier).
    period = _derive_period(draft, job.get("period"))
    notice = _read_first_notice(draft, period)
    return page(notice + _review_form(draft, token, intake_job=job_id, period=period), "queue")

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


# ---------------------------------------------------------------- supplier reliability
def _reliability_parse_upload(f, form_supplier, form_country, form_pg):
    """Parse an uploaded advertised-price file (xlsx/csv/xml/pdf) into a list of dicts
    {supplier, country, city, date, product_group, net_price}. Rows that omit a
    supplier/country inherit the form's defaults. Returns (rows, note); `note` is a
    non-empty advisory string when a best-effort path (PDF) parsed little/nothing.
    Raises on a hard parse error (the caller logs + shows a red banner)."""
    name = (f.filename or "").lower()
    raw = f.read()
    rows, note = [], ""

    def _mk(d):
        """Build a normalized row from a case-insensitive header->value mapping,
        inheriting the form defaults. Returns None when no usable price/place."""
        g = {(k or "").strip().lower(): v for k, v in d.items()}
        def pick(*keys):
            for k in keys:
                v = g.get(k)
                if v not in (None, ""):
                    return v
            return None
        price = pick("net_price", "price", "eur_per_l", "net", "advertised")
        if price in (None, ""):
            return None
        city = pick("city", "location", "station", "town", "place") or ""
        date = pick("date", "day") or _dt.date.today().isoformat()
        return {
            "supplier": (pick("supplier") or form_supplier or "").strip(),
            "country": (pick("country") or form_country or "").strip(),
            "city": str(city).strip(),
            "date": str(date).strip()[:10],
            "product_group": (pick("product_group", "product", "fuel") or form_pg
                              or "Diesel"),
            "net_price": price}

    if name.endswith((".xlsx", ".xlsm")):
        import io
        from openpyxl import load_workbook
        wb = load_workbook(io.BytesIO(raw), data_only=True, read_only=True)
        ws = wb.active
        it = ws.iter_rows(values_only=True)
        header = None
        for r in it:
            if r is None or all(c is None for c in r):
                continue
            header = [(str(c).strip() if c is not None else "") for c in r]
            break
        for r in it:
            if r is None or all(c is None for c in r):
                continue
            d = {header[i]: (r[i] if i < len(r) else None) for i in range(len(header))}
            row = _mk(d)
            if row:
                rows.append(row)
        wb.close()
    elif name.endswith(".csv") or name.endswith(".txt"):
        import csv as _csv, io
        text = raw.decode("utf-8-sig", errors="replace")
        for d in _csv.DictReader(io.StringIO(text)):
            row = _mk(d)
            if row:
                rows.append(row)
    elif name.endswith(".xml"):
        import xml.etree.ElementTree as ET
        root = ET.fromstring(raw.decode("utf-8-sig", errors="replace"))
        # Generic element-per-row: any element carrying a price (child or attribute).
        for rec in root.iter():
            fields = dict(rec.attrib)
            for ch in list(rec):
                tag = ch.tag.split("}")[-1]
                if ch.text and ch.text.strip():
                    fields[tag] = ch.text.strip()
            row = _mk(fields)
            if row:
                rows.append(row)
    elif name.endswith(".pdf"):
        # BEST-EFFORT only — a simple line regex (location ... price [+ date]). An
        # unstructured PDF that yields nothing tells the user to use xlsx/csv/xml.
        import re as _re, extract
        try:
            text = extract.pdf_text(raw)
        except Exception as e:
            _log_exc("reliability pdf text", e)
            text = ""
        date_re = _re.compile(r"(\d{4}-\d{2}-\d{2})")
        price_re = _re.compile(r"(\d+[.,]\d{2,4})\s*$")
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            pm = price_re.search(line)
            if not pm:
                continue
            price = pm.group(1).replace(",", ".")
            try:
                if float(price) <= 0 or float(price) > 100:
                    continue       # a €/L line, not a total/quantity
            except ValueError:
                continue
            dm = date_re.search(line)
            loc = line[:pm.start()].strip()
            if dm:
                loc = loc.replace(dm.group(1), "").strip()
            rows.append(_mk({"city": loc, "date": dm.group(1) if dm else "",
                             "net_price": price}))
        rows = [r for r in rows if r]
        if not rows:
            note = ("Could not read advertised prices from that PDF — PDF capture is "
                    "best-effort. Use a structured file (xlsx/csv/xml) or the manual "
                    "form below.")
    else:
        raise ValueError("Unsupported file type — use xlsx, csv, xml or pdf.")

    # Drop rows still missing a supplier (nothing to attribute them to).
    rows = [r for r in rows if r.get("supplier")]
    return rows, note


def _reliability_supplier_options(cur=""):
    """<option> list of supplier codes from supplier_master, plus the current value as a
    free-text fallback. Never raises — a missing master just yields a bare blank."""
    codes = []
    try:
        import supplier_master
        con = supplier_master.connect()
        codes = [r[0] for r in con.execute("SELECT code FROM suppliers ORDER BY code")]
        con.close()
    except Exception as e:
        _log_exc("reliability supplier list", e)
    opts = ['<option value="">— choose / type below —</option>']
    for c in codes:
        opts.append(f'<option {"selected" if c == cur else ""}>{esc(c)}</option>')
    return "".join(opts)


@app.route("/reliability", methods=["GET", "POST"])
def reliability_page():
    """Supplier reliability — capture advertised portal prices and surface where each
    supplier INVOICED above what it ADVERTISED (NET EUR/L). Capability: pricing
    (gated centrally in _guard); writes ONLY to the app-owned benchmark.db via the R1
    pricing_intelligence functions; reads transactions READ-ONLY via the engine."""
    import pricing_intelligence as PI
    banner = ""
    if request.method == "POST":
        act = request.form.get("__act", "upload")
        try:
            if act == "manual":
                supplier = (request.form.get("supplier_pick") or "").strip() or \
                           (request.form.get("supplier") or "").strip()
                country = (request.form.get("country") or "").strip()
                city = (request.form.get("city") or "").strip()
                date = (request.form.get("date") or "").strip() or \
                    _dt.date.today().isoformat()
                pg = (request.form.get("product_group") or "Diesel").strip() or "Diesel"
                raw_price = (request.form.get("net_price") or "").strip()
                if not supplier or not country:
                    raise ValueError("Supplier and country are required.")
                price = money.q2(raw_price.replace(",", "."))
                if price <= 0:
                    raise ValueError(f"Net price must be a positive number "
                                     f"(got {raw_price!r}).")
                PI.add_advertised_price(supplier, country, city, date,
                                        float(price), product_group=pg, source="manual")
                banner = ('<div class="card"><b class="ok">Added advertised price for '
                          f'{esc(supplier.upper())} — {esc(country.upper())} '
                          f'{esc(city)} {esc(date)} at {float(price):.4f} €/L.</b></div>')
            else:  # upload
                f = request.files.get("file")
                if not f or not f.filename:
                    raise ValueError("Choose a file to upload.")
                rows, note = _reliability_parse_upload(
                    f, request.form.get("supplier", ""),
                    request.form.get("country", ""),
                    request.form.get("product_group", "Diesel"))
                if rows:
                    n = PI.load_advertised_prices(rows, source="upload")
                    banner = (f'<div class="card"><b class="ok">Loaded {n} advertised '
                              f'price(s).</b>'
                              + (f' <span class="note">{esc(note)}</span>' if note else '')
                              + '</div>')
                else:
                    msg = note or ("No advertised prices found in that file — check the "
                                   "columns (supplier, country, city, date, net_price).")
                    banner = (f'<div class="card"><b class="bad">{esc(msg)}</b></div>')
        except Exception as e:
            _log_exc("reliability capture", e)
            banner = (f'<div class="card"><b class="bad">Could not add advertised '
                      f'prices: {esc(str(e))}</b></div>')

    period = (request.args.get("period") or "").strip() or None
    try:
        rep = PI.reliability_report(period)
    except Exception as e:
        _log_exc("reliability report", e)
        rep = {"suppliers": [], "detail": [], "summary": {
            "matched_fills": 0, "overcharged_fills": 0, "unmatched_fills": 0,
            "total_overcharge_eur": 0.0}}
    suppliers, detail, summ = rep["suppliers"], rep["detail"], rep["summary"]

    # overcharge € by supplier (only suppliers actually overcharging)
    bars = svg_hbars([(s["supplier"], s["total_overcharge_eur"]) for s in suppliers
                      if s["total_overcharge_eur"] > 0], unit=" €", fmt=",.0f",
                     color="#c8102e")
    srows = []
    for s in suppliers:
        sc = s["reliability_score"]
        scls = "" if sc is None else ("ok" if sc >= 0.99 else ("bad" if sc < 0.9 else ""))
        srows.append([
            f"<td>{esc(s['supplier'])}</td>",
            (f"<td class='r {scls}'>{sc*100:.1f}%</td>" if sc is not None
             else "<td class='r'>—</td>"),
            f"<td class=r>{s['matched_fills']}</td>",
            f"<td class='r {'bad' if s['overcharged_fills'] else ''}'>{s['overcharged_fills']}</td>",
            f"<td class='r {'bad' if s['total_overcharge_eur'] else ''}'>{s['total_overcharge_eur']:,.0f}</td>",
            (f"<td class=r>{s['avg_delta_eur_per_l']:+.4f}</td>"
             if s['avg_delta_eur_per_l'] is not None else "<td class=r>—</td>")])
    drows = []
    for d in detail:
        drows.append([
            f"<td>{esc(d['supplier'])}</td>",
            f"<td>{esc(d['country'])} {esc(d['city'] or '')}</td>",
            f"<td>{esc(d['date'])}</td><td>{esc(d['product'])}</td>",
            f"<td class=r>{d['qty']:,.1f}</td>",
            f"<td class=r>{d['advertised']:.4f}</td>",
            f"<td class=r>{d['invoiced']:.4f}</td>",
            f"<td class='r bad'>{d['delta']:+.4f}</td>",
            f"<td class='r bad'>{d['overcharge_eur']:,.0f}</td>"])

    pform = (f'<form method="get" style="display:inline;margin-left:6px">'
             f'<label>Period (YYYY-MM) '
             f'<input name="period" value="{esc(period or "")}" placeholder="all history" '
             f'size="9"></label> <button>Apply</button></form>')

    rec = PI.list_advertised_prices(limit=50)
    rrows = [[f"<td>{esc(r['supplier'])}</td>",
              f"<td>{esc(r['country'])} {esc(r['city'] or '')}</td>",
              f"<td>{esc(r['date'])}</td><td>{esc(r['product_group'])}</td>",
              f"<td class=r>{(r['net_price'] or 0):.4f}</td>",
              f"<td>{esc(r['source'] or '')}</td>"] for r in rec]

    body = (
        '<div class="card"><h2>Supplier reliability — advertised vs invoiced</h2>'
        '<div class="note">Customers see a fuel price advertised on a supplier portal '
        'and believe it is final; the invoice can charge more. This compares each '
        'invoiced fill\'s effective NET price (net_eur_eff / qty) against the advertised '
        'price that applied on the fill\'s date, per supplier / country / location. All '
        'prices NET EUR/L, final (VAT excluded, rebates applied). The reliability score '
        'is the share of matched fills charged at or below the advertised price, within '
        '€0.01/L.</div>'
        f'<div style="margin:10px 0">Period: <b>{esc(period or "all history")}</b>{pform}</div>'
        '<div class="kpis">'
        f'<div class="kpi"><div class="v bad">EUR {summ["total_overcharge_eur"]:,.0f}</div>'
        '<div class="l">total overcharge</div></div>'
        f'<div class="kpi"><div class="v">{summ["overcharged_fills"]}</div>'
        '<div class="l">overcharged fills</div></div>'
        f'<div class="kpi"><div class="v">{summ["matched_fills"]}</div>'
        '<div class="l">matched fills</div></div>'
        f'<div class="kpi"><div class="v">{summ["unmatched_fills"]}</div>'
        '<div class="l">no advertised reference</div></div></div>')
    body += '<div class="card"><h2>Overcharge € by supplier</h2>' + bars + '</div>'
    body += ('<div class="card"><h2>Per-supplier reliability</h2>'
             + (tbl(["Supplier", "Reliability", "Matched", "Overcharged",
                     "Overcharge €", "Avg Δ €/L"], srows)
                if srows else '<p class="note">No invoiced fills matched to an '
                'advertised price yet — add advertised prices below.</p>')
             + '</div>')
    body += ('<div class="card"><h2>Where you were overcharged</h2>'
             + (tbl(["Supplier", "Location", "Date", "Product", "Qty",
                     "Advertised €/L", "Invoiced €/L", "Δ €/L", "Overcharge €"], drows)
                if drows else '<p class="note">No overcharges detected for this '
                'period.</p>')
             + '</div>')

    # capture — upload
    body += (
        '<div class="card"><h2>Add advertised prices — upload</h2>'
        '<form method="post" action="/reliability" enctype="multipart/form-data" class="f">'
        + _csrf_input()
        + '<input type="hidden" name="__act" value="upload">'
        '<label>File (xlsx / csv / xml / pdf)<input type="file" name="file" '
        'accept=".xlsx,.xlsm,.csv,.txt,.xml,.pdf" required></label>'
        '<label>Supplier (for files without a supplier column)'
        '<input name="supplier" placeholder="e.g. Q8"></label>'
        '<label>Default country<input name="country" placeholder="e.g. LV"></label>'
        '<label>Default product<input name="product_group" value="Diesel"></label>'
        '<button>Upload advertised prices</button></form>'
        '<div class="note">Columns are matched case-insensitively by name: '
        '<b>supplier, country, city/location/station, date, net_price/price/eur_per_l</b> '
        '(product optional, default Diesel). Rows without a supplier/country inherit the '
        'fields above. Uploading is <b>additive</b> — every dated row is kept so a past '
        'invoice can be checked against the price that applied on its fill date. PDF '
        'capture is <b>best-effort</b> (a simple location + price line scan); for reliable '
        'loading use xlsx/csv/xml or the manual form.</div></div>')

    # capture — manual
    body += (
        '<div class="card"><h2>Add advertised price — manual</h2>'
        '<form method="post" action="/reliability" class="f">'
        + _csrf_input()
        + '<input type="hidden" name="__act" value="manual">'
        f'<label>Supplier<select name="supplier_pick">'
        f'{_reliability_supplier_options()}</select></label>'
        '<label>…or type<input name="supplier" placeholder="free text"></label>'
        '<label>Country<input name="country" placeholder="e.g. LV" required></label>'
        '<label>Location<input name="city" placeholder="e.g. Riga"></label>'
        f'<label>Date<input type="date" name="date" '
        f'value="{_dt.date.today().isoformat()}"></label>'
        '<label>Product<input name="product_group" value="Diesel"></label>'
        '<label>Net price €/L<input name="net_price" placeholder="1.4200" required></label>'
        '<button>Add advertised price</button></form>'
        '<div class="note">NET EUR/L, final (VAT excluded). Pick a supplier from the '
        'master list or type a free-text code.</div></div>')

    # recent
    body += ('<div class="card"><h2>Recent advertised prices</h2>'
             + (tbl(["Supplier", "Location", "Date", "Product", "Advertised €/L", "Source"],
                    rrows)
                if rrows else '<p class="note">No advertised prices stored yet.</p>')
             + '</div>')

    return page(banner + body, "rel")

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
    (README.md#data-architecture-duplication-under-used-data #2 cycle-time/forecast, #9 realization): per-claim
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
                          f'{esc(res.get("message") or "")}</b></div>')
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
        + '<div class="dashlabel">Open refund receivable by aging band</div>' + bars
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
        + f'<div class="card"><h2>VAT receivables {esc(year)}</h2>'
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
          f'never lends. <b>{esc("Provider: " + prov.name)}'
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
        + f'<div class="dashlabel">Financeable claims {esc(year)}</div>'
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


_FIN_DISCLAIMER = (
    '<div class="card" style="border-left:4px solid #b06b00;background:#fff8ec">'
    '<b>Advisory / origination-only.</b> This page <b>models a potential advance</b> '
    'against your VAT receivable — <b>no funds move and this is not a financing offer</b>. '
    'Real financing requires a <b>licensed partner</b>; the platform never lends. Nothing '
    'here touches a VAT figure, claim status, lock, fee, or payment.</div>')


@app.route("/financing", methods=["GET", "POST"])
def financing():
    """ADMIN-ONLY embedded-finance ORIGINATION surface (finance.py) — advisory, NullProvider
    default. Models, per eligible open receivable, the advance offer (eligible / advance /
    fee / net-now vs net-later / expected payout) at the configured origination terms, lets
    an admin ORIGINATE an `offered` advance into the finance-owned ledger, and shows that
    ledger with status transitions. ORIGINATION-ONLY: no funds move, and NOTHING here writes
    a VAT figure/status/lock/fee/payment — finance only reads recovery_report() and writes
    its own finance.db."""
    import finance
    banner = ""
    if request.method == "POST":
        act = request.form.get("__act", "")
        try:
            if act == "set_terms":
                _auth.set_setting("finance_advance_rate",
                                  request.form.get("advance_rate", "").strip())
                _auth.set_setting("finance_fee_rate_annual",
                                  request.form.get("fee_rate_annual", "").strip())
                _auth.set_setting("finance_provider",
                                  (request.form.get("provider", "none") or "none").strip())
                banner = '<div class="card"><b class="ok">Origination terms saved.</b></div>'
            elif act == "offer":
                subject = request.form.get("subject", "")
                ent = request.form.get("entity", ""); cty = request.form.get("country", "")
                per = request.form.get("period", "")
                elig = float(request.form.get("eligible_eur", "0") or 0)
                adv = float(request.form.get("advance_eur", "0") or 0)
                fee = float(request.form.get("fee_eur", "0") or 0)
                row = finance.offer_advance(subject, elig, adv, fee,
                                            actor=session.get("user", "admin"),
                                            period=per, country=cty)
                banner = ('<div class="card"><b class="ok">Advance offer recorded '
                          '(origination-only — no funds move).</b></div>' if row
                          else '<div class="card"><b class="bad">Could not record offer.</b></div>')
            elif act == "set_status":
                aid = int(request.form.get("advance_id", "0") or 0)
                st = request.form.get("status", "")
                row = finance.set_status(aid, st, actor=session.get("user", "admin"))
                banner = ('<div class="card"><b class="ok">Advance status updated.</b></div>'
                          if row else
                          '<div class="card"><b class="bad">Could not update advance status.</b></div>')
        except Exception as e:
            _log_exc("financing/" + act, e)
            banner = '<div class="card"><b class="bad">Action failed.</b></div>'

    fo = finance.financeable_offers()
    offers = fo["offers"]; totals = fo["totals"]; fterms = fo["terms"]
    prov = finance.provider()
    prov_lbl = "null" if prov.name == "none" else prov.name

    # KPI strip — the aggregate economics of the financeable book.
    kpis = (
        '<div class="kpis">'
        + f'<div class="kpi"><div class="v">EUR {totals["eligible_eur"]:,.0f}</div>'
          '<div class="l">eligible receivable</div></div>'
        + f'<div class="kpi"><div class="v">EUR {totals["advance_eur"]:,.0f}</div>'
          f'<div class="l">advance ({fterms["advance_rate"]*100:.0f}%)</div></div>'
        + f'<div class="kpi"><div class="v">EUR {totals["fee_eur"]:,.0f}</div>'
          f'<div class="l">fee ({fterms["fee_rate_annual"]*100:.1f}%/yr)</div></div>'
        + f'<div class="kpi"><div class="v">EUR {totals["net_now_eur"]:,.0f}</div>'
          '<div class="l">net now (advance − fee)</div></div>'
        + f'<div class="kpi"><div class="v">EUR {totals["net_later_eur"]:,.0f}</div>'
          '<div class="l">net later (wait for the state)</div></div></div>')

    # Financeable offers table — one row per eligible open receivable, each with an
    # "Originate (offer)" action that records an `offered` ledger row.
    offer_rows = []
    for o in offers:
        age = o.get("age_days")
        form = (
            '<form method="post" style="margin:0">' + _csrf_input()
            + '<input type="hidden" name="__act" value="offer">'
            + f'<input type="hidden" name="subject" value="{esc(o["subject"])}">'
            + f'<input type="hidden" name="entity" value="{esc(o.get("entity") or "")}">'
            + f'<input type="hidden" name="country" value="{esc(o.get("country") or "")}">'
            + f'<input type="hidden" name="period" value="{esc(o.get("period") or "")}">'
            + f'<input type="hidden" name="eligible_eur" value="{esc(str(o["eligible_eur"]))}">'
            + f'<input type="hidden" name="advance_eur" value="{esc(str(o["advance_eur"]))}">'
            + f'<input type="hidden" name="fee_eur" value="{esc(str(o["fee_eur"]))}">'
            + '<button>Originate advance (offer)</button></form>')
        offer_rows.append([
            f"<td>{esc(o.get('entity') or '')}</td><td>{esc(o.get('country') or '')}</td>"
            f"<td>{esc(o.get('period') or '')}</td>",
            f"<td>{esc(o.get('status') or '')}</td>",
            f"<td class=r>{money.f2(o['eligible_eur']):,.2f}</td>",
            f"<td class=r><b>{money.f2(o['advance_eur']):,.2f}</b></td>",
            f"<td class=r>{money.f2(o['fee_eur']):,.2f}</td>",
            f"<td class=r>{money.f2(o['net_now_eur']):,.2f}</td>",
            f"<td class=r>{money.f2(o['net_later_eur']):,.2f}</td>",
            f"<td>{esc(o.get('expected_payout') or '')}"
            + (f" <span class='note'>~{o['expected_days']}d</span>"
               if isinstance(o.get('expected_days'), int) else "") + "</td>",
            f"<td>{form}</td>"])
    offers_tbl = (tbl(["Entity", "Country", "Period", "Claim status", "Eligible EUR",
                       "Advance EUR", "Fee EUR", "Net now EUR", "Net later EUR",
                       "Expected payout", "Originate"], offer_rows)
                  if offer_rows
                  else '<p class="note">No eligible (filed, unpaid) receivables to finance.</p>')

    # Origination ledger with status transitions.
    _next = {"offered": ("accepted", "declined"), "accepted": ("declined",)}
    try:
        ledger_rows = []
        for a in finance.list_advances():
            st = a.get("status") or ""
            transitions = ""
            for nxt in _next.get(st, ()):
                transitions += (
                    '<form method="post" style="display:inline;margin:0 4px 0 0">'
                    + _csrf_input()
                    + '<input type="hidden" name="__act" value="set_status">'
                    + f'<input type="hidden" name="advance_id" value="{esc(str(a.get("id")))}">'
                    + f'<input type="hidden" name="status" value="{esc(nxt)}">'
                    + f'<button>{esc(nxt)}</button></form>')
            ledger_rows.append([
                f"<td>{esc(a.get('created_at') or '')}</td>",
                f"<td>{esc(a.get('claim_key') or '')}</td>",
                f"<td class=r>{money.f2(a.get('eligible_eur') or 0):,.2f}</td>",
                f"<td class=r>{money.f2(a.get('amount_eur') or 0):,.2f}</td>",
                f"<td class=r>{money.f2(a.get('fee_eur') or 0):,.2f}</td>",
                f"<td>{esc(a.get('provider') or '')}</td>",
                f"<td><b>{esc(st)}</b></td>",
                f"<td>{transitions or '<span class=note>—</span>'}</td>"])
        ledger_body = (tbl(["Recorded", "Receivable", "Eligible EUR", "Advance EUR",
                            "Fee EUR", "Provider", "Status", "Transition"], ledger_rows)
                       if ledger_rows
                       else '<p class="note">No advances originated yet.</p>')
    except Exception as e:
        _log_exc("financing/list_advances", e)
        ledger_body = '<p class="note">Origination ledger temporarily unavailable.</p>'

    prov_note = (' — No partner configured; offers are <b>origination-only / informational</b>.'
                 if prov.name == "none" else '')
    body = (
        banner + _FIN_DISCLAIMER
        + '<div class="card"><h2>Embedded finance — origination</h2>'
        + f'<div class="note">Provider: <b>{esc(prov_lbl)}</b>{prov_note}</div>'
        + kpis
        + '<div class="note">The financeable base is the SAME submitted/approved receivable '
          'the recovery page shows (filed with the tax authority, not yet paid) — high '
          'certainty is what makes it financeable. <b>Net now</b> = advance − fee (cash today); '
          '<b>net later</b> = the full refund if you wait for the state. Fee = annual rate × '
          'advance × expected-days/365 (a transparent, time-priced discount). NET EUR, '
          'VAT-excluded.</div></div>'
        + '<div class="card"><h2>Financeable receivables</h2>' + offers_tbl
        + '<div class="note">Only eligible claims (status submitted/approved — filed but '
          'unpaid) are financeable; a draft or paid claim is excluded. "Originate advance '
          '(offer)" records an <b>offered</b> row in the finance ledger — origination-only, '
          'no funds move.</div></div>'
        + '<div class="card"><h2>Origination terms</h2>'
        + '<form method="post" class="f">' + _csrf_input()
        + '<input type="hidden" name="__act" value="set_terms">'
        + f'<label>Advance rate<input name="advance_rate" '
          f'value="{esc(str(fterms["advance_rate"]))}" style="width:80px"></label>'
        + f'<label>Annual fee rate<input name="fee_rate_annual" '
          f'value="{esc(str(fterms["fee_rate_annual"]))}" style="width:80px"></label>'
        + '<label>Provider<select name="provider"><option value="none"'
        + (' selected' if prov.name == "none" else '')
        + '>none (origination-only)</option></select></label>'
        + '<button>Save terms</button>'
        + '<span class="note">Advance fraction (0&lt;x&le;1), annual fee fraction '
          '(0&le;x&lt;1).</span></form></div>'
        + '<div class="card"><h2>Origination ledger</h2>' + ledger_body
        + '<div class="note">The finance-owned advances ledger (finance.db). Each row is a '
          'MODELLED advance, never a funded one — with the NULL provider no money moves. '
          'Lifecycle: offered → accepted / declined. EUR figures are the eligible receivable, '
          'the advance and the fee at the recorded terms.</div></div>')
    return page(body, "fin")


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
    # "What needs action" worklist (admin-only; the /vat route is already ADMIN_ONLY).
    worklist = _worklist_card(int(year[:4]) if year[:4].isdigit() else 2026)
    body = (banner + worklist + f'<div class="card"><h2>VAT refund applications {esc(year)} (2008/9/EC) — '
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


def _captured_data_links(sha256):
    """If a persisted AI-vision CAPTURE artifact is LINKED to this vaulted document's
    sha256 (the original PDF = the same bytes the upload archived as raw_upload), render a
    "Captured data" entry next to the PDF with View + Download (JSON / text) links. Returns
    '' when there is no linked capture artifact (so a non-vision document shows nothing
    extra). Best-effort: NEVER raises; the sha is escaped into every URL."""
    sha = (sha256 or "").strip()
    if not sha:
        return ""
    try:
        import capture_file
        if not capture_file.has_capture(sha):
            return ""
    except Exception as e:
        _log_exc("captured-data links lookup", e)
        return ""
    s = esc(sha)
    return (' <span class="note" title="AI-vision captured data saved alongside the original '
            'PDF (advisory — verify against the PDF)">· Captured data: '
            f'<a href="/capture-file/{s}.json?view=1">view</a> '
            f'<a href="/capture-file/{s}.json">JSON</a> '
            f'<a href="/capture-file/{s}.txt">text</a></span>')


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
    # A3 — optional filter-by-tag: /documents?tag=<id> shows only invoices that have at
    # least one document tagged with that tag (or any of its descendants). Best-effort:
    # an unknown/blank tag simply means "no filter". The metadata subject_ref is the same
    # `doc:<id>` rowkey the search index + metadata panel use.
    import metadata as _MD
    filter_tag = (request.args.get("tag") or "").strip()
    filter_subjects = None
    filter_banner = ""
    if filter_tag:
        _tag = _MD.get_tag(filter_tag)
        if _tag:
            filter_subjects = set(_MD.subjects_for_tag(_tag["id"], include_descendants=True))
            filter_banner = (
                f'<div class="card" style="border-left:4px solid var(--mut)">'
                f'Filtered to documents tagged <b>{esc(_tag["name"])}</b> '
                f'(and any sub-tags) — {len(filter_subjects)} document(s). '
                f'<a href="/documents">clear filter</a></div>')
    rows = []
    for (sup, ctry), invs in sorted(INVOICES.items()):
        ent = SPECS[sup]["entity"][0] if sup in SPECS else ENTITY_OVERRIDE.get(sup, sup)
        for ref, dt in invs:
            docs = VR.docs_for(con, ent, sup, ref)
            if filter_subjects is not None and not any(
                    f"doc:{d['id']}" in filter_subjects for d in docs):
                continue
            dl = " ".join(f'<a href="/doc/{d["id"]}">{esc(d["filename"])}</a> <span class="note">[{esc(d["sha256"][:8])}, {esc(d["kind"])}]</span> '
                          f'<a href="/doc-assistant/{d["id"]}" class="note">ask&nbsp;AI</a> '
                          f'<a href="/doc/{d["id"]}/meta" class="note">tags&nbsp;&amp;&nbsp;fields</a> '
                          f'<a href="/doc/{d["id"]}/versions" class="note">versions</a>'
                          + _captured_data_links(d["sha256"])
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
    # A3 — a small "filter by tag" picker + a link to manage the metadata schema.
    _tag_opts = "".join(
        f'<option value="{esc(str(t["id"]))}" '
        f'{"selected" if str(t["id"])==filter_tag else ""}>{esc(t["path"])}</option>'
        for t in _MD.flat_tags())
    meta_bar = (
        '<div class="card"><form class="f" method="get" style="margin:0">'
        '<label>Filter by tag '
        f'<select name="tag"><option value="">— all documents —</option>{_tag_opts}</select>'
        '</label><button>Apply</button>'
        '<a href="/documents" style="align-self:end;padding:8px 12px;font-size:13px">Reset</a>'
        '<span style="flex:1"></span>'
        '<a href="/metadata" style="align-self:end;padding:8px 12px;font-size:13px">'
        'Manage fields &amp; tags &rarr;</a></form></div>')
    body = banner + find_block + meta_bar + filter_banner + auto + ('<div class="card"><h2>Invoice document vault — every invoice needs its '
                     'original PDF or scan before submission</h2>'
                     + tbl(["Entity","Supplier","Country","Invoice ref","Date","Attached document(s)","Upload"], rows)
                     + '<div class="note">Files are SHA-256 hashed; identical files on different '
                       'invoices trigger a wrong-attachment warning; submission is blocked while '
                       'any invoice in the application has no document. Use <b>Find stored</b> to '
                       'attach a file already in the data lake or this customer’s vault. '
                       '<b>tags &amp; fields</b> opens a document’s metadata panel.</div></div>')
    con.close(); return page(body, "doc")


def _doc_meta_label(doc_id):
    """(label, exists) for a vaulted document — used by the metadata panel header. Reads
    invoice_documents READ-ONLY through vat_refund.connect(); returns ("", False) if gone."""
    import vat_refund as VR
    con = VR.connect()
    try:
        d = con.execute("SELECT entity, supplier, invoice_ref, filename FROM "
                        "invoice_documents WHERE id=?", (doc_id,)).fetchone()
    finally:
        con.close()
    if d is None:
        return "", False
    ref = d["invoice_ref"] or d["filename"]
    return f"{d['supplier']} · {ref} ({d['entity']})", True


@app.route("/doc/<int:doc_id>/meta", methods=["GET", "POST"])
def doc_meta(doc_id):
    """The A3 metadata PANEL for one vaulted document: view/edit its TAGS (add/remove from
    the tag tree) and its typed CUSTOM-FIELD values. Keyed by the stable `doc:<id>`
    reference (the same subject_ref the search index uses). Gated by the `documents`
    capability (compliance module). Every DB value is escaped; metadata.py never raises."""
    import metadata as MD
    subject_ref = f"doc:{doc_id}"
    label, exists = _doc_meta_label(doc_id)
    if not exists:
        return page('<div class="card"><b class="bad">No such document.</b></div>', "doc"), 404
    banner = ""
    if request.method == "POST":
        act = request.form.get("__act", "")
        ok, msg = True, ""
        try:
            if act == "add_tag":
                ok, msg = MD.assign_tag(request.form.get("tag_id"), subject_ref)
            elif act == "remove_tag":
                ok, msg = MD.unassign_tag(request.form.get("tag_id"), subject_ref)
            elif act == "set_field":
                fid = request.form.get("field_id")
                fld = MD.get_field(fid)
                if fld and fld["type"] == "boolean":
                    raw = "1" if request.form.get("value") else "0"
                else:
                    raw = request.form.get("value", "")
                ok, msg = MD.set_value(fid, subject_ref, raw)
            elif act == "clear_field":
                ok, msg = MD.clear_value(request.form.get("field_id"), subject_ref)
            elif act == "place_hold":
                import retention as RET
                obj, msg = RET.place_hold(subject_ref, request.form.get("reason", ""),
                                          session.get("user", ""))
                ok = obj is not None
            elif act == "release_hold":
                import retention as RET
                ok, msg = RET.release_hold(request.form.get("hold_id"),
                                           session.get("user", ""))
        except Exception as e:
            _log_exc("doc metadata edit", e)
            ok, msg = False, "could not apply that change (logged)."
        border = "var(--ok)" if ok else "var(--bad)"
        head = "&#10003; Saved" if ok else "&#10007; Not saved"
        banner = (f'<div class="card" style="border-left:4px solid {border}">'
                  f'<b class="{"ok" if ok else "bad"}">{head}</b>'
                  + (f' — {esc(msg)}' if msg else '') + '</div>')

    # ---- current tags + the tree picker
    cur_tags = MD.tags_for(subject_ref)
    cur_ids = {t["id"] for t in cur_tags}
    tag_chips = "".join(
        '<form method="post" style="display:inline-block;margin:2px 4px 2px 0">'
        + _csrf_input()
        + f'<input type="hidden" name="__act" value="remove_tag">'
        f'<input type="hidden" name="tag_id" value="{esc(str(t["id"]))}">'
        f'<button title="remove" style="font-size:12px;padding:2px 8px">'
        f'{esc(t["name"])} &times;</button></form>'
        for t in cur_tags) or '<span class="note">No tags yet.</span>'
    avail = [t for t in MD.flat_tags() if t["id"] not in cur_ids]
    tag_opts = "".join(f'<option value="{esc(str(t["id"]))}">{esc(t["path"])}</option>'
                       for t in avail)
    add_tag_form = ('<form method="post" class="f" style="margin-top:8px">'
                    + _csrf_input()
                    + '<input type="hidden" name="__act" value="add_tag">'
                    f'<label>Add tag <select name="tag_id">{tag_opts}</select></label>'
                    '<button>Add</button></form>') if tag_opts else (
        '<div class="note" style="margin-top:8px">No more tags to add — '
        '<a href="/metadata">create some</a>.</div>')

    # ---- custom-field values (typed inputs)
    values = {v["field_id"]: v for v in MD.get_values(subject_ref)}
    field_rows = []
    for f in MD.list_fields():
        cur = values.get(f["id"])
        cur_val = cur["value"] if cur else ""
        disp = cur["display"] if cur else ""
        ftype = f["type"]
        if ftype == "select":
            opts = "".join(
                f'<option {"selected" if o==cur_val else ""}>{esc(o)}</option>'
                for o in ([""] + list(f["options"])))
            inp = f'<select name="value">{opts}</select>'
        elif ftype == "boolean":
            inp = ('<input type="checkbox" name="value" value="1" '
                   + ("checked" if cur_val == "1" else "") + '>')
        elif ftype == "date":
            inp = f'<input type="date" name="value" value="{esc(cur_val)}">'
        elif ftype in ("number", "monetary"):
            inp = (f'<input type="number" step="any" name="value" '
                   f'value="{esc(cur_val)}">')
        elif ftype == "documentlink":
            inp = (f'<input type="text" name="value" value="{esc(cur_val)}" '
                   f'placeholder="doc:&lt;id&gt;">')
        else:
            inp = f'<input type="text" name="value" value="{esc(cur_val)}">'
        form = ('<form method="post" class="f" style="margin:0;gap:6px">'
                + _csrf_input()
                + '<input type="hidden" name="__act" value="set_field">'
                f'<input type="hidden" name="field_id" value="{esc(str(f["id"]))}">'
                f'{inp}<button>Save</button></form>')
        clear = ('<form method="post" style="margin:0;display:inline">'
                 + _csrf_input()
                 + '<input type="hidden" name="__act" value="clear_field">'
                 f'<input type="hidden" name="field_id" value="{esc(str(f["id"]))}">'
                 '<button class="note" style="padding:2px 8px;font-size:12px">clear</button>'
                 '</form>') if cur else ""
        field_rows.append([
            f'<td>{esc(f["name"])}<div class="note">{esc(ftype)}</div></td>',
            f'<td>{form}</td>',
            f'<td>{esc(disp)} {clear}</td>'])
    fields_block = (tbl(["Field", "Set value", "Current"], field_rows) if field_rows
                    else '<div class="note">No custom fields defined yet — '
                    '<a href="/metadata">create some</a>.</div>')

    retention_card = _retention_card(doc_id, subject_ref)
    classification_card = _classification_card(doc_id, subject_ref)
    workflow_card = _workflow_card(subject_ref)

    body = (banner
            + classification_card
            + workflow_card
            + f'<div class="card"><h2>Document metadata — {esc(label)}</h2>'
            '<div class="note">Tags and typed custom fields attached to this document '
            '(stored in the app-owned metadata DB, not the engine product DBs). Monetary '
            'values are quantized for display.</div>'
            f'<h3 style="margin-top:14px">Tags</h3><div>{tag_chips}</div>{add_tag_form}'
            f'<h3 style="margin-top:14px">Custom fields</h3>{fields_block}</div>'
            + retention_card
            + f'<p><a href="/doc/{doc_id}">&larr; download this document</a> · '
            f'<a href="/doc/{doc_id}/versions">version history</a> · '
            '<a href="/documents">back to the document vault</a> · '
            '<a href="/metadata">manage fields &amp; tags</a> · '
            '<a href="/retention">retention policies</a></p>')
    return page(body, "doc")


def _workflow_card(subject_ref):
    """The per-document WORKFLOW indicator + a "Start workflow" action, for the doc metadata
    screen. Shows existing runs over this subject (current step / approved / rejected) and,
    when the workflow module is on, a small form to start an ACTIVE workflow. ADVISORY only —
    starting/advancing a workflow never touches a VAT figure, status or lock. Best-effort: any
    failure renders nothing rather than breaking the page. Never raises."""
    if not module_enabled("workflow"):
        return ""
    try:
        import workflow
        runs = workflow.runs_for(subject_ref)
        active = workflow.list_workflows(active_only=True)
    except Exception as e:
        _log_exc("workflow: card", e)
        return ""
    run_rows = []
    for r in runs:
        st = workflow.run_status(r["id"]) or {}
        step_lbl = "—"
        if r.get("status") == "running" and st.get("current_step_name"):
            step_lbl = (f'step {int(st.get("current_step") or 0) + 1}/'
                        f'{int(st.get("n_steps") or 0)} · {st.get("current_step_name")}')
        run_rows.append([
            esc(r.get("workflow_name") or "—"),
            _wf_status_html(r.get("status")),
            esc(step_lbl),
            esc(r.get("created_at") or ""),
        ])
    runs_html = (tbl(["Workflow", "Status", "Current step", "Started"], run_rows) if run_rows
                 else '<p class="note">No workflows started on this document yet.</p>')
    start_form = ""
    if active:
        opts = "".join(f'<option value="{int(w["id"])}">{esc(w.get("name") or "")}</option>'
                       for w in active)
        start_form = (
            '<form method="post" action="/workflow/start" class="f" style="margin-top:10px">'
            + _csrf_input()
            + f'<input type="hidden" name="subject_ref" value="{esc(subject_ref)}">'
            + f'<label>Start workflow <select name="workflow_id">{opts}</select></label>'
            + '<button>Start</button></form>')
    else:
        start_form = ('<p class="note" style="margin-top:8px">No active workflows — '
                      '<a href="/workflows">define one</a>.</p>')
    return (f'<div class="card"><h2>Workflow</h2>'
            '<p class="note">Advisory approval/routing over this document. A workflow '
            'approval is process tracking only — it never overrides the VAT legal gates '
            '(checklist, locks, period-end) or changes a claim figure, status or fee.</p>'
            f'{runs_html}{start_form}</div>')


def _doc_dates(doc_id):
    """(doc_date, registered_date) for a vaulted document, READ-ONLY. doc_date is the
    invoice date (supplier_invoices.invoice_date when resolvable), registered_date is
    invoice_documents.uploaded_at. Best-effort -> (None, None) on any failure. Used by the
    per-document retention status panel."""
    orig = _doc_original(doc_id)
    if orig is None:
        return None, None
    registered = None
    doc_date = None
    try:
        import vat_refund as VR
        con = VR.connect()
        try:
            r = con.execute("SELECT uploaded_at FROM invoice_documents WHERE id=?",
                            (doc_id,)).fetchone()
            registered = r["uploaded_at"] if r else None
        finally:
            con.close()
    except Exception as e:
        _log_exc("retention doc registered-date", e)
    try:
        import supplier_master as SM
        scon = SM.connect()
        try:
            inv = scon.execute(
                "SELECT invoice_date FROM supplier_invoices WHERE supplier=? AND invoice_no=?",
                (orig.get("supplier"), orig.get("invoice_ref"))).fetchone()
            doc_date = inv["invoice_date"] if inv else None
        finally:
            scon.close()
    except Exception as e:
        _log_exc("retention doc invoice-date", e)
    return (doc_date or registered), registered


def _retention_card(doc_id, subject_ref):
    """The per-document RETENTION STATUS + LEGAL HOLD control. Advisory only: it shows the
    retention picture and lets a user PLACE/RELEASE a legal hold (audited). A held document
    is clearly badged and is excluded from the disposition-review worklist. Every value is
    escaped; retention.py never raises."""
    import retention as RET
    doc_date, registered = _doc_dates(doc_id)
    st = RET.retention_status(subject_ref, doc_date, registered)
    pol = st.get("policy")
    if st["on_hold"]:
        badge = ('<span class="bad" style="border:1px solid var(--bad);border-radius:4px;'
                 'padding:1px 7px">&#9211; LEGAL HOLD — disposition blocked</span>')
    elif st["past_due"]:
        badge = ('<span class="bad" style="border:1px solid var(--bad);border-radius:4px;'
                 'padding:1px 7px">Past retention — flagged for review</span>')
    elif pol:
        badge = ('<span class="ok" style="border:1px solid var(--ok);border-radius:4px;'
                 'padding:1px 7px">Within retention</span>')
    else:
        badge = '<span class="note">No retention policy applies</span>'
    if pol:
        dr = st.get("days_remaining")
        dr_txt = (f"{dr} day(s) remaining" if dr is not None and dr >= 0
                  else (f"{-dr} day(s) overdue" if dr is not None else "—"))
        detail = (f'<div class="note" style="margin-top:6px">Policy '
                  f'<b>{esc(pol.get("name") or "")}</b> — retain '
                  f'{esc(str(pol.get("retain_years")))} year(s) from '
                  f'{esc(pol.get("basis") or "doc_date")}. Retain until '
                  f'<b>{esc(st.get("retain_until") or "—")}</b> ({esc(dr_txt)}).</div>')
    else:
        detail = ('<div class="note" style="margin-top:6px">No retention policy matches '
                  'this document (define one under <a href="/retention">retention '
                  'policies</a>). Retention is <b>advisory</b> — nothing is ever '
                  'auto-deleted.</div>')

    # active hold (if any) + place/release control
    active = next((h for h in RET.holds_for(subject_ref) if h.get("released_at") is None),
                  None)
    if active:
        hold_block = (
            f'<div style="margin-top:8px">Held since '
            f'<b>{esc(active.get("placed_at") or "")}</b> by '
            f'{esc(active.get("placed_by") or "")}'
            + (f' — <i>{esc(active.get("reason") or "")}</i>' if active.get("reason") else "")
            + '</div>'
            '<form method="post" class="f" style="margin-top:8px" '
            'onsubmit="return confirm(\'Release the legal hold on this document?\')">'
            + _csrf_input()
            + '<input type="hidden" name="__act" value="release_hold">'
            f'<input type="hidden" name="hold_id" value="{esc(str(active.get("id")))}">'
            '<button>Release legal hold</button></form>')
    else:
        hold_block = (
            '<form method="post" class="f" style="margin-top:8px">'
            + _csrf_input()
            + '<input type="hidden" name="__act" value="place_hold">'
            '<label>Reason <input name="reason" placeholder="e.g. litigation / audit hold">'
            '</label><button>Place legal hold</button></form>')

    return (f'<div class="card"><h2>Retention &amp; legal hold</h2>'
            f'<div>{badge}</div>{detail}'
            '<div class="note" style="margin-top:6px">A legal hold <b>overrides</b> '
            'retention everywhere — a held document is never flagged for disposition, and '
            'nothing is ever auto-deleted (the review queue is a human worklist).</div>'
            f'<h3 style="margin-top:12px">Legal hold</h3>{hold_block}</div>')


# ---------------------------------------------------------------- data classification / DLP
_DLP_BADGE_STYLE = {
    "restricted":   ("&#128274; Restricted", "var(--bad)"),
    "confidential": ("&#128274; Confidential", "var(--bad)"),
    "internal":     ("&#128275; Internal", "var(--ink)"),
    "public":       ("Public", "var(--ok)"),
}


def _classification_badge(rec):
    """A small, ESCAPED sensitivity badge from a classify.classification() record (or scan
    result) — e.g. "🔒 Restricted — contains IBAN×1, email×2". Returns "" when `rec` is
    falsy. The finding TYPES + COUNTS only (never any value) are shown. Pure; never raises."""
    if not rec:
        return ""
    label = (rec.get("label") or "public").lower()
    text, color = _DLP_BADGE_STYLE.get(label, ("Public", "var(--ok)"))
    findings = rec.get("findings") or []
    if findings:
        parts = ", ".join(f'{esc(str(f.get("type")))}&times;{esc(str(f.get("count")))}'
                          for f in findings)
        detail = f' &mdash; contains {parts}'
    else:
        detail = ' &mdash; no sensitive data detected'
    return (f'<span class="badge" style="border:1px solid {color};border-radius:4px;'
            f'padding:1px 7px;color:{color}"><b>{text}</b>{detail}</span>')


def _classify_doc_text(doc_id):
    """Best-effort text of a vaulted document for classification: read the ORIGINAL vaulted
    bytes and extract text. Returns "" on any failure (classification then yields 'public').
    Never raises."""
    try:
        orig = _doc_original(doc_id)
        if not orig or not orig.get("stored_path"):
            return ""
        import document_vault as DV, extract as EX, vat_refund as VR
        raw = DV.get_bytes(orig["stored_path"], VR.DOCDIR)
        if not raw:
            return ""
        return EX.pdf_text(raw) or ""
    except Exception as e:
        _log_exc("classify doc text", e)
        return ""


def _classification_card(doc_id, subject_ref):
    """The per-document DATA-CLASSIFICATION (DLP) panel. Lazily classifies the document from
    its vaulted text when no record exists yet (so the label appears without manual action),
    then shows the ESCAPED sensitivity badge + the current external-AI gate policy. Advisory;
    classify.py is best-effort and never raises."""
    import classify
    rec = classify.classification(subject_ref)
    if rec is None:
        text = _classify_doc_text(doc_id)
        if text:
            classify.classify_document(subject_ref, text)
            rec = classify.classification(subject_ref)
    badge = _classification_badge(rec) if rec else (
        '<span class="note">Not yet classified.</span>')
    mx = classify.max_sensitivity()
    allowed, info = (True, {}) if rec is None else classify.external_ai_allowed(subject_ref)
    if rec is not None and not allowed:
        gate = (f'<div class="note bad" style="margin-top:6px">&#128683; External AI is '
                f'<b>blocked</b> for this document by the DLP policy '
                f'(<code>{esc(mx)}</code>): {esc(info.get("reason") or "")}</div>')
    else:
        gate = (f'<div class="note" style="margin-top:6px">External-AI sensitivity limit: '
                f'<b>{esc(mx)}</b>. This document is <b>allowed</b> to be sent to the '
                f'external AI under the current policy '
                f'(<a href="/admin#modules">change it</a>).</div>')
    return (f'<div class="card"><h2>Data classification (DLP)</h2>'
            f'<div>{badge}</div>{gate}'
            '<div class="note" style="margin-top:6px">The sensitivity label is derived by '
            'scanning the document text for sensitive-data <b>types</b> (IBAN, bank/card, '
            'email, phone, VAT id, names). Only the type + a count is stored — <b>never the '
            'matched value</b>. The label is <b>advisory</b>; it gates only the OPT-IN '
            'external-AI paths and never a legal check.</div></div>')


def _doc_original(doc_id):
    """The invoice_documents row for `doc_id` (entity/supplier/ref/filename/stored_path/
    sha256/size), READ-ONLY via vat_refund.connect(). Returns a dict or None. Used to
    lazily SEED version 1 of a document's chain from the original vaulted bytes — this
    module never WRITES invoice_documents."""
    import vat_refund as VR
    con = VR.connect()
    try:
        d = con.execute(
            "SELECT id, entity, supplier, invoice_ref, filename, stored_path, sha256, "
            "size FROM invoice_documents WHERE id=?", (doc_id,)).fetchone()
    finally:
        con.close()
    return dict(d) if d else None


@app.route("/doc/<int:doc_id>/versions", methods=["GET", "POST"])
def doc_versions(doc_id):
    """The A4 VERSION-HISTORY panel for one vaulted document: the ordered chain (newest
    first) with each version's number / date / who / note / size and current vs
    superseded state; download/view of ANY version; an "Upload new version" control; and
    a "Make current" (revert) action that records the chosen old version as a NEW current
    version (history is never destroyed). Keyed by the stable `doc:<id>` reference (the
    same subject_ref A2 search and A3 metadata use). Gated by the `documents` capability
    (compliance module). Every DB value is escaped; versioning.py never raises.

    BYTE INGESTION: an uploaded new version is vaulted through document_vault's
    app-callable store API (versioning.add_version -> document_vault.copy_to ->
    backend.put, SHA-256 + the vault's own dedup) — NO product DB is opened writable and
    no engine write happens in-request."""
    import versioning as VER
    subject_ref = f"doc:{doc_id}"
    orig = _doc_original(doc_id)
    if orig is None:
        return page('<div class="card"><b class="bad">No such document.</b></div>', "doc"), 404
    # Lazily SEED version 1 from the original vaulted bytes (idempotent — a no-op once a
    # chain exists). The original lives in the invoice vault; we record a POINTER to it.
    if orig.get("stored_path"):
        VER.record_initial(subject_ref, orig["stored_path"], sha256=orig.get("sha256"),
                           size=orig.get("size"), actor=session.get("user", ""),
                           note=f"original — {orig.get('filename') or ''}".strip(" —"))
    label = f"{orig['supplier']} · {orig['invoice_ref'] or orig.get('filename')} ({orig['entity']})"

    banner = ""
    if request.method == "POST":
        act = request.form.get("__act", "")
        obj, msg = None, ""
        try:
            if act == "upload_version":
                up = request.files.get("file")
                data = up.read() if up else b""
                if not data:
                    obj, msg = None, "choose a file to upload as a new version"
                else:
                    obj, msg = VER.add_version(
                        subject_ref, new_bytes=data,
                        filename=(getattr(up, "filename", "") or None),
                        note=request.form.get("note", ""),
                        actor=session.get("user", ""))
            elif act == "revert":
                obj, msg = VER.revert_to(request.form.get("version_id"),
                                         actor=session.get("user", ""))
        except Exception as e:
            _log_exc("doc version edit", e)
            obj, msg = None, "could not apply that change (logged)."
        ok = obj is not None
        border = "var(--ok)" if ok else "var(--bad)"
        head = "&#10003; Saved" if ok else "&#10007; Not saved"
        banner = (f'<div class="card" style="border-left:4px solid {border}">'
                  f'<b class="{"ok" if ok else "bad"}">{head}</b>'
                  + (f' — {esc(msg)}' if msg else '') + '</div>')

    chain = VER.versions_for(subject_ref)
    rows = []
    for v in chain:
        state = ('<b class="ok">current</b>' if v.get("is_current")
                 else '<span class="note">superseded</span>')
        size = v.get("size")
        size_txt = f"{int(size):,} B" if size not in (None, "") else "—"
        sha = (v.get("sha256") or "")[:8]
        view = (f'<a href="/doc/{doc_id}/version/{v["id"]}">view</a> · '
                f'<a href="/doc/{doc_id}/version/{v["id"]}?dl=1">download</a>')
        revert = ""
        if not v.get("is_current"):
            revert = ('<form method="post" style="display:inline;margin:0">'
                      + _csrf_input()
                      + '<input type="hidden" name="__act" value="revert">'
                      f'<input type="hidden" name="version_id" value="{esc(str(v["id"]))}">'
                      '<button style="font-size:12px;padding:2px 8px">Make current</button>'
                      '</form>')
        rows.append([
            f'<td>v{esc(str(v["version_no"]))} {state}</td>',
            f'<td>{esc(v.get("created_at") or "")}</td>',
            f'<td>{esc(v.get("created_by") or "")}</td>',
            f'<td>{esc(v.get("note") or "")}</td>',
            f'<td>{esc(size_txt)}<div class="note">{esc(sha)}</div></td>',
            f'<td>{view} {revert}</td>'])
    chain_block = (tbl(["Version", "When", "By", "Note", "Size", ""], rows) if rows
                   else '<div class="note">No versions recorded yet.</div>')

    upload_form = ('<form method="post" enctype="multipart/form-data" class="f" '
                   'style="margin-top:8px;gap:8px;flex-wrap:wrap">'
                   + _csrf_input()
                   + '<input type="hidden" name="__act" value="upload_version">'
                   '<label>New version file <input type="file" name="file" required></label>'
                   '<label>Note <input type="text" name="note" '
                   'placeholder="what changed (optional)"></label>'
                   '<button>Upload new version</button></form>')

    body = (banner
            + f'<div class="card"><h2>Document versions — {esc(label)}</h2>'
            '<div class="note">An ordered, append-only chain of this document’s versions '
            '(stored in the app-owned versions DB, not the engine product DBs). Uploading a '
            'new version supersedes the current one; <b>Make current</b> reverts to an older '
            'version by recording it as a new version — history is never deleted. New-version '
            'bytes are stored in the document vault (SHA-256, deduplicated).</div>'
            f'<h3 style="margin-top:14px">Version chain</h3>{chain_block}'
            f'<h3 style="margin-top:14px">Upload a new version</h3>{upload_form}</div>'
            f'<p><a href="/doc/{doc_id}">&larr; download current original</a> · '
            f'<a href="/doc/{doc_id}/meta">tags &amp; fields</a> · '
            '<a href="/documents">back to the document vault</a></p>')
    return page(body, "doc")


@app.route("/doc/<int:doc_id>/version/<int:version_id>")
def doc_version_download(doc_id, version_id):
    """View (inline) or download (?dl=1) a SPECIFIC version's vaulted bytes. The version
    must belong to this document's `doc:<id>` chain (so the URL can't read another
    document's version). Routes through versioning.get_version_bytes ->
    document_vault.get_bytes, so any storage backend resolves."""
    import versioning as VER, io
    subject_ref = f"doc:{doc_id}"
    v = VER.get_version(version_id)
    if v is None or v.get("subject_ref") != subject_ref:
        return page('<div class="card"><b class="bad">No such version.</b></div>', "doc"), 404
    data, err = VER.get_version_bytes(version_id)
    if data is None:
        _log_exc("doc version download", RuntimeError(err or "read failed"))
        return page('<div class="card"><b class="bad">Could not read that version '
                    '(logged).</b></div>', "doc"), 404
    name = f"v{v['version_no']}_{_doc_original(doc_id) and _doc_original(doc_id).get('filename') or 'document'}"
    as_attach = request.args.get("dl") == "1"
    return send_file(io.BytesIO(data), as_attachment=as_attach, download_name=name)


@app.route("/metadata", methods=["GET", "POST"])
def metadata_admin():
    """The A3 "Manage fields & tags" settings page: DEFINE typed custom fields
    (name+type+options) and manage the hierarchical TAG TREE (create nested, rename,
    recolor, re-parent with a cycle guard, delete). Gated by the `documents` capability
    (compliance module). metadata.py is best-effort and never raises; every value is
    escaped on output."""
    import metadata as MD
    banner = ""
    if request.method == "POST":
        act = request.form.get("__act", "")
        ok, msg = True, ""
        try:
            if act == "define_field":
                _, msg = MD.define_field(
                    request.form.get("name", ""), request.form.get("type", "text"),
                    request.form.get("options", ""))
                ok = not msg
            elif act == "delete_field":
                ok, msg = MD.delete_field(request.form.get("field_id"))
            elif act == "create_tag":
                _, msg = MD.create_tag(
                    request.form.get("name", ""),
                    request.form.get("parent_id") or None,
                    request.form.get("color", ""))
                ok = not msg
            elif act == "rename_tag":
                ok, msg = MD.rename_tag(
                    request.form.get("tag_id"),
                    name=request.form.get("name", ""),
                    color=request.form.get("color", ""),
                    parent_id=request.form.get("parent_id") or None)
            elif act == "delete_tag":
                ok, msg = MD.delete_tag(request.form.get("tag_id"))
        except Exception as e:
            _log_exc("metadata admin", e)
            ok, msg = False, "could not apply that change (logged)."
        border = "var(--ok)" if ok else "var(--bad)"
        head = "&#10003; Saved" if ok else "&#10007; Not saved"
        banner = (f'<div class="card" style="border-left:4px solid {border}">'
                  f'<b class="{"ok" if ok else "bad"}">{head}</b>'
                  + (f' — {esc(msg)}' if msg else '') + '</div>')

    # ---- custom fields
    type_opts = "".join(f'<option>{esc(t)}</option>' for t in MD.FIELD_TYPES)
    fields = MD.list_fields()
    frows = []
    for f in fields:
        opts = ", ".join(f["options"]) if f["options"] else ""
        delf = ('<form method="post" style="margin:0" '
                'onsubmit="return confirm(\'Delete this field and all its values?\')">'
                + _csrf_input()
                + '<input type="hidden" name="__act" value="delete_field">'
                f'<input type="hidden" name="field_id" value="{esc(str(f["id"]))}">'
                '<button class="bad" style="padding:2px 8px;font-size:12px">Delete</button>'
                '</form>')
        frows.append([f'<td>{esc(f["name"])}</td>', f'<td>{esc(f["type"])}</td>',
                      f'<td>{esc(opts)}</td>', f'<td>{delf}</td>'])
    define_form = (
        '<form method="post" class="f">'
        + _csrf_input()
        + '<input type="hidden" name="__act" value="define_field">'
        '<label>Name <input name="name" required></label>'
        f'<label>Type <select name="type">{type_opts}</select></label>'
        '<label>Options <input name="options" placeholder="select: a, b, c"></label>'
        '<button>Define field</button></form>')
    fields_card = (f'<div class="card"><h2>Custom fields</h2>'
                   '<div class="note">Typed fields (text, number, monetary, date, '
                   'boolean, select, documentlink). Options (comma/newline) are only used '
                   'by a <b>select</b> field.</div>'
                   + (tbl(["Name", "Type", "Options", ""], frows) if frows else
                      '<div class="note" style="margin:8px 0">No fields yet.</div>')
                   + define_form + '</div>')

    # ---- tag tree
    tree = MD.list_tags()
    def _render(nodes, depth):
        out = ""
        for n in nodes:
            pad = depth * 22
            sw = (f'<span style="display:inline-block;width:11px;height:11px;border-radius:2px;'
                  f'background:{esc(n["color"])};vertical-align:middle;margin-right:6px"></span>'
                  if n.get("color") else "")
            out += (f'<div style="padding:3px 0 3px {pad}px;border-bottom:1px solid var(--line)">'
                    f'{sw}<b>{esc(n["name"])}</b> <span class="note">#{esc(str(n["id"]))}</span>'
                    '<form method="post" style="display:inline-block;margin:0 0 0 10px">'
                    + _csrf_input()
                    + '<input type="hidden" name="__act" value="delete_tag">'
                    f'<input type="hidden" name="tag_id" value="{esc(str(n["id"]))}">'
                    '<button class="note" style="padding:1px 7px;font-size:12px">delete</button>'
                    '</form></div>')
            out += _render(n["children"], depth + 1)
        return out
    tree_html = _render(tree, 0) or '<div class="note">No tags yet.</div>'
    parent_opts = ('<option value="">— top level —</option>'
                   + "".join(f'<option value="{esc(str(t["id"]))}">{esc(t["path"])}</option>'
                             for t in MD.flat_tags()))
    create_tag_form = (
        '<form method="post" class="f" style="margin-top:10px">'
        + _csrf_input()
        + '<input type="hidden" name="__act" value="create_tag">'
        '<label>Tag name <input name="name" required></label>'
        f'<label>Parent <select name="parent_id">{parent_opts}</select></label>'
        '<label>Color <input type="color" name="color" value="#1b7340"></label>'
        '<button>Create tag</button></form>')
    rename_tag_form = (
        '<form method="post" class="f" style="margin-top:8px">'
        + _csrf_input()
        + '<input type="hidden" name="__act" value="rename_tag">'
        f'<label>Tag <select name="tag_id">'
        + "".join(f'<option value="{esc(str(t["id"]))}">{esc(t["path"])}</option>'
                  for t in MD.flat_tags())
        + '</select></label>'
        '<label>New name <input name="name"></label>'
        f'<label>New parent <select name="parent_id">{parent_opts}</select></label>'
        '<label>Color <input type="color" name="color" value="#1b7340"></label>'
        '<button>Rename / move</button></form>') if MD.flat_tags() else ""
    tags_card = (f'<div class="card"><h2>Tag tree</h2>'
                 '<div class="note">Nested tags — moving a tag under one of its own '
                 'descendants is refused (cycle guard); deleting a tag re-parents its '
                 'children up.</div>'
                 f'<div style="margin:8px 0">{tree_html}</div>'
                 + create_tag_form + rename_tag_form + '</div>')

    body = (banner + fields_card + tags_card
            + '<p><a href="/documents">&larr; back to the document vault</a> · '
            '<a href="/retention">retention policies</a></p>')
    return page(body, "doc")


@app.route("/retention", methods=["GET", "POST"])
def retention_admin():
    """The A5 RETENTION-POLICY management page (records schedule): define policies
    (name, applies-to all/tag, retain years, basis date, action) and delete them. Gated
    by the `documents` capability (compliance module). ADVISORY only — a policy never
    deletes anything; it drives the disposition-review WORKLIST. retention.py is
    best-effort and never raises; every value is escaped. Every define/delete is audited
    (changed_by) by the retention.db audit triggers."""
    import retention as RET
    import metadata as MD
    banner = ""
    if request.method == "POST":
        act = request.form.get("__act", "")
        ok, msg = True, ""
        try:
            if act == "define_policy":
                _, msg = RET.define_policy(
                    request.form.get("name", ""),
                    request.form.get("applies_to", "all"),
                    tag_id=request.form.get("tag_id") or None,
                    retain_years=request.form.get("retain_years", RET.DEFAULT_RETAIN_YEARS),
                    action=request.form.get("action", "review"),
                    basis=request.form.get("basis", "doc_date"))
                ok = not msg
            elif act == "delete_policy":
                ok, msg = RET.delete_policy(request.form.get("policy_id"))
        except Exception as e:
            _log_exc("retention admin", e)
            ok, msg = False, "could not apply that change (logged)."
        border = "var(--ok)" if ok else "var(--bad)"
        head = "&#10003; Saved" if ok else "&#10007; Not saved"
        banner = (f'<div class="card" style="border-left:4px solid {border}">'
                  f'<b class="{"ok" if ok else "bad"}">{head}</b>'
                  + (f' — {esc(msg)}' if msg else '') + '</div>')

    tags = {t["id"]: t for t in MD.flat_tags()}
    prows = []
    for p in RET.list_policies():
        if p.get("applies_to") == "tag":
            tg = tags.get(p.get("tag_id"))
            scope = f'tag: {tg["path"]}' if tg else f'tag #{p.get("tag_id")} (missing)'
        else:
            scope = "all documents"
        delp = ('<form method="post" style="margin:0" '
                'onsubmit="return confirm(\'Delete this retention policy? (deletes the '
                'schedule entry only — never a document)\')">'
                + _csrf_input()
                + '<input type="hidden" name="__act" value="delete_policy">'
                f'<input type="hidden" name="policy_id" value="{esc(str(p["id"]))}">'
                '<button class="bad" style="padding:2px 8px;font-size:12px">Delete</button>'
                '</form>')
        prows.append([
            f'<td>{esc(p["name"])}</td>', f'<td>{esc(scope)}</td>',
            f'<td>{esc(str(p["retain_years"]))} yr</td>',
            f'<td>{esc(p["basis"])}</td>', f'<td>{esc(p["action"])}</td>',
            f'<td>{delp}</td>'])
    policies_tbl = (tbl(["Name", "Applies to", "Retain", "Basis", "Action", ""], prows)
                    if prows else
                    '<div class="note" style="margin:8px 0">No retention policies yet.</div>')

    tag_opts = "".join(f'<option value="{esc(str(t["id"]))}">{esc(t["path"])}</option>'
                       for t in MD.flat_tags())
    applies_opts = "".join(f'<option>{esc(a)}</option>' for a in RET.APPLIES_TO)
    action_opts = "".join(f'<option>{esc(a)}</option>' for a in RET.ACTIONS)
    basis_opts = "".join(f'<option>{esc(b)}</option>' for b in RET.BASES)
    define_form = (
        '<form method="post" class="f" style="flex-wrap:wrap">'
        + _csrf_input()
        + '<input type="hidden" name="__act" value="define_policy">'
        '<label>Name <input name="name" required></label>'
        f'<label>Applies to <select name="applies_to">{applies_opts}</select></label>'
        f'<label>Tag (if tag-scoped) <select name="tag_id">'
        f'<option value="">— none —</option>{tag_opts}</select></label>'
        f'<label>Retain years <input type="number" name="retain_years" min="0" '
        f'value="{RET.DEFAULT_RETAIN_YEARS}"></label>'
        f'<label>Basis <select name="basis">{basis_opts}</select></label>'
        f'<label>Action <select name="action">{action_opts}</select></label>'
        '<button>Define policy</button></form>')
    policies_card = (
        '<div class="card"><h2>Retention policies</h2>'
        '<div class="note">A retention <b>schedule</b> over vaulted documents. Each policy '
        'sets how long to retain (years) over a basis date (the invoice date or the date the '
        'document was registered). A <b>tag</b>-scoped policy applies to documents carrying '
        'that A3 tag; when several policies apply the <b>longest</b> retention always wins '
        '(never under-retain), with a tag-scoped policy winning ties over an '
        '&ldquo;all&rdquo; policy. EU VAT records are typically kept '
        f'~{RET.DEFAULT_RETAIN_YEARS} years. <b>Advisory only</b> — a policy never deletes '
        'anything; it flags elapsed documents on the '
        '<a href="/retention/review">disposition-review worklist</a>. '
        '<code>dispose_review</code> still only FLAGS for a human.</div>'
        + policies_tbl + define_form + '</div>')

    body = (banner + policies_card
            + '<p><a href="/retention/review">Retention review worklist &rarr;</a> · '
            '<a href="/documents">back to the document vault</a> · '
            '<a href="/metadata">manage fields &amp; tags</a></p>')
    return page(body, "doc")


@app.route("/retention/review")
def retention_review():
    """The A5 RETENTION-REVIEW worklist: documents PAST their retention and NOT on legal
    hold — purely an advisory human worklist. NO delete/disposition action lives here:
    nothing is auto-deleted and a held document never appears (legal hold OVERRIDES
    retention). Gated by the `documents` capability. retention.py never raises; every
    value is escaped. Each row links to the document for a human to review."""
    import retention as RET
    due = RET.due_for_review()
    rows = []
    for d in due:
        pol = d.get("policy") or {}
        rows.append([
            f'<td><a href="/doc/{esc(str(d.get("doc_id")))}">'
            f'{esc(d.get("filename") or d.get("invoice_ref") or d.get("subject_ref"))}</a>'
            f'<div class="note">{esc(d.get("subject_ref") or "")}</div></td>',
            f'<td>{esc(d.get("supplier") or "")}<div class="note">'
            f'{esc(d.get("entity") or "")}</div></td>',
            f'<td>{esc(d.get("invoice_ref") or "")}</td>',
            f'<td>{esc(pol.get("name") or "")}<div class="note">'
            f'{esc(pol.get("action") or "review")}</div></td>',
            f'<td>{esc(d.get("retain_until") or "")}</td>',
            f'<td><a href="/doc/{esc(str(d.get("doc_id")))}/meta">review &amp; hold</a></td>'])
    worklist = (tbl(["Document", "Supplier", "Invoice ref", "Policy", "Retain until",
                     "Review"], rows) if rows else
                '<div class="note" style="margin:8px 0">Nothing is past retention and off '
                'hold — the review queue is empty.</div>')
    body = (f'<div class="card"><h2>Retention review — disposition worklist</h2>'
            '<div class="note">Documents whose retention period has <b>elapsed</b> and that '
            'are <b>not</b> under a legal hold. This is an <b>advisory human worklist only</b>: '
            'there is no delete/disposition action here. Nothing is ever auto-deleted, and a '
            'document under a legal hold is excluded (a legal hold overrides retention). Open a '
            'document to review it and, if needed, place or release a legal hold.</div>'
            + worklist + '</div>'
            + '<p><a href="/retention">&larr; retention policies</a> · '
            '<a href="/documents">document vault</a></p>')
    return page(body, "doc")


@app.route("/search")
def search_page():
    """Full-text search over the document/invoice corpus (FTS5 index in the app-owned
    search.db). A search box + ranked results with snippets, each deep-linking to the
    document (/doc/<id>) or the invoice drill-down (/transactions?...). Read-only on the
    product DBs (the index was built by the admin 'Rebuild search index' action / the
    close). Best-effort: search.search() never raises, so a garbage query just shows no
    results; any unexpected failure is logged and shown as an empty result set."""
    q = (request.args.get("q") or "").strip()
    results = []
    err = ""
    if q:
        try:
            results = _search.search(q, limit=50)
        except Exception as e:   # defensive — search() already never raises
            _log_exc("search page", e)
            err = "Search is temporarily unavailable — the error has been logged."
    form = ('<form class="f" method="get">'
            f'<label style="flex:1 1 320px">search<input type="text" name="q" '
            f'value="{esc(q)}" placeholder="supplier, invoice ref, VAT number, product…" '
            f'autofocus></label>'
            '<button>Search</button>'
            '<a href="/search" style="align-self:end;padding:8px 12px;font-size:13px">Reset</a>'
            '</form>')
    _kind_label = {_search.KIND_DOC: "document", _search.KIND_INVOICE: "invoice"}
    rows = []
    for r in results:
        kind = esc(_kind_label.get(r.get("kind"), r.get("kind") or ""))
        # snippet() emits our literal <mark>/</mark> delimiters around matched terms; the
        # surrounding text is FTS-stored DB content. Escape the WHOLE snippet, then
        # un-escape only our two known marker tags so the highlight renders but every DB
        # value stays escaped (a planted <script> in the data cannot break out).
        snip = esc(r.get("snip") or "")
        snip = (snip.replace("&lt;mark&gt;", "<mark>")
                    .replace("&lt;/mark&gt;", "</mark>"))
        rows.append(
            f'<td><span class="note">{kind}</span></td>'
            f'<td><a href="{esc(r.get("link") or "#")}">{esc(r.get("title") or r.get("link") or "")}</a>'
            f'<div class="note" style="margin-top:2px">{snip}</div></td>')
    if err:
        body = form + f'<div class="card"><b class="bad">{esc(err)}</b></div>'
    elif not q:
        body = (form + '<div class="card"><div class="note">Search every registered '
                'document and supplier invoice — by supplier name, invoice/statement '
                'reference, VAT number, country, product term or amount. Results link '
                'straight to the document or its transactions. The index is refreshed by '
                'the monthly close and the Admin → "Rebuild search index" action.</div></div>')
    else:
        body = (form + f'<div class="card"><h2>{len(rows)} result(s) for '
                f'"{esc(q)}"</h2>'
                + (tbl(["Type", "Result"], [[c] for c in rows]) if rows else
                   '<div class="note">No matches. Try a supplier name, an invoice '
                   'reference, a VAT number or a product term. If you just imported '
                   'data, an admin may need to rebuild the search index.</div>')
                + '</div>')
    return page(body, "srch")


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

def _provisional_suppliers_card():
    """Admin-confirmation surface for AUTO-ONBOARDED provisional suppliers: a small list
    with an 'Activate' action. Reads the list READ-ONLY (the app holds no writable
    suppliers.db handle); the Activate write is ENQUEUED to the engine worker
    (kind='activate'). Admin-only action (the button is shown only to admins). Every value
    is escaped. Returns '' when there are no provisional suppliers."""
    is_admin = session.get("role") == "admin"
    try:
        import supplier_master as SM
        provs = SM.list_provisional()
    except Exception as e:
        _log_exc("provisional suppliers list", e)
        return ""
    if not provs:
        return ""
    rows = []
    for p in provs:
        act = ""
        if is_admin:
            act = ('<form method="post" style="display:inline">' + _csrf_input()
                   + f'<input type="hidden" name="__act" value="activate">'
                   + f'<input type="hidden" name="code" value="{esc(p["code"])}">'
                   + '<button>Activate</button></form>')
        else:
            act = '<span class="note">admin only</span>'
        rows.append([f'<td><b>{esc(p["code"])}</b></td><td>{esc(p.get("legal_name") or "")}</td>',
                     f'<td>{esc(p.get("home_country") or "")}</td>',
                     f'<td class="note">{esc(p.get("notes") or "")}</td><td>{act}</td>'])
    return ('<div class="card" style="border-left:4px solid var(--bad)">'
            '<h2>Provisional suppliers — awaiting confirmation</h2>'
            '<div class="note">Auto-onboarded from an invoice (identified by VAT number). '
            'Confirm the details, then <b>Activate</b> to make the supplier live. The '
            'activation runs through the engine worker (the app never writes suppliers.db '
            'in-request).</div>'
            + tbl(["Code", "Legal name", "Country", "Note", ""], rows) + '</div>')


@app.route("/suppliers", methods=["GET", "POST"])
def suppliers():
    import supplier_master
    banner = ""
    if request.method == "POST" and request.form.get("__act") == "activate":
        if session.get("role") != "admin":
            banner = '<div class="card"><b class="bad">Activating a supplier is admin-only.</b></div>'
        else:
            try:
                import waiting_room as IQ
                code = (request.form.get("code") or "").strip().upper()
                jid, _st = IQ.enqueue_activate(code, user=session.get("user", "system"))
                banner = (f'<div class="card"><b class="ok">Activation queued for {esc(code)} '
                          f'(job {jid}) — the engine worker will flip it to active.</b></div>')
            except Exception as e:
                _log_exc("supplier activate enqueue", e)
                banner = f'<div class="card"><b class="bad">Could not queue activation: {esc(str(e))}</b></div>'
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
    body = (banner
            + '<div class="note" style="margin-bottom:10px">Supplier master data lives in '
            '<b>suppliers.db</b> — a separate database from the VAT refund claim database '
            '(fuel_history.db). Transactions and claims reference suppliers by code only.</div>'
            + _provisional_suppliers_card()
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
            elif act == "vault_doc_request":
                # promote a generated draft to a first-class PLATFORM document (record the
                # subject↔vault-locator link + seed its version chain) so it can be tagged,
                # versioned, shared and later e-signed. Idempotent / best-effort.
                con = CD.connect()
                ref, m = CD.vault_generated_document(
                    con, int(request.form.get("req_id", "0")), by=session.get("user"))
                con.close()
                if ref is None:
                    raise ValueError(m or "could not save to vault")
                msg = (f"Document request #{esc(request.form.get('req_id',''))} saved to the "
                       "vault as a platform document (taggable / versionable / shareable).")
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
            # Save-to-vault: promote a generated draft to a first-class platform document
            # (taggable / versionable / shareable). Offered whenever a draft exists and is
            # not yet saved; idempotent server-side so a double-click is harmless.
            if dr["generated_doc_id"] and not CD.generated_vault_ref(con, rid):
                actions += ('<form method="post" style="display:inline">' + _csrf_input()
                            + '<input type="hidden" name="__act" value="vault_doc_request">'
                            + f'<input type="hidden" name="req_id" value="{int(rid)}">'
                            + '<button style="background:var(--ok);font-size:11px;padding:3px 8px" '
                              'title="make the generated PDF a first-class vaulted document">'
                              'Save to vault</button></form> ')
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
            from urllib.parse import quote
            vault_ref = CD.generated_vault_ref(con, rid)
            vault_link = ('<a href="/share?doc_ref=' + quote(vault_ref or "", safe="")
                          + '" title="vaulted as a platform document — tag / version / share">'
                          '&#128274; vaulted</a>') if vault_ref else ""
            links = " ".join(filter(None, [
                _dr_doc_link(dr["generated_doc_id"], "generated"),
                vault_link,
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
        _banner_klass = "ok"          # green by default; an in-band failure sets "bad"
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
            elif act == "set_service":
                # Services control center: flip ONE service on/off by its setting key. Defence
                # in depth — only a key in services_status.TOGGLEABLE_SETTINGS may be written,
                # and only to "on"/"off"; nothing arbitrary.
                import services_status as _svc
                skey = (request.form.get("service") or "").strip()
                want = "on" if request.form.get("state") == "on" else "off"
                if skey in _svc.TOGGLEABLE_SETTINGS:
                    _auth.set_setting(skey, want)
                    _title = next((s["title"] for s in _svc.services()
                                   if s.get("setting") == skey), skey)
                    banner = f"<b>{esc(_title)}</b> turned <b>{'ON' if want=='on' else 'OFF'}</b>."
                else:
                    banner = "Unknown service toggle ignored."
            elif act == "set_ai_review":
                # advisory AI review backend (default 'none' = OFF). Reuses the same
                # API keys as the extractor backends; no new env vars.
                be = request.form.get("ai_review_backend", "none")
                if be not in ("none", "claude", "openai", "azure"):
                    be = "none"
                _auth.set_setting("ai_review_backend", be)
                banner = ("AI review assistant turned OFF." if be == "none"
                          else f"AI review assistant set to <b>{esc(be)}</b> (advisory only).")
            elif act == "set_ai_doc_chat":
                # advisory AI document assistant (chat-with-document). Default OFF; reuses the
                # ai_review backend selection (same key). Opt-in flag only — no backend here.
                on = request.form.get("ai_doc_chat_enabled") == "on"
                _auth.set_setting("ai_doc_chat_enabled", "on" if on else "off")
                banner = ("AI document assistant turned ON (advisory; derived-data-only)."
                          if on else "AI document assistant turned OFF.")
            elif act == "set_ai_verify":
                # advisory AI VERIFICATION against the ORIGINAL PDF (vision). Default OFF.
                # This is the deliberate exception that SENDS the PDF to the AI provider, so
                # the flag is opt-in only; the backend/key reuse the extractor selection.
                on = request.form.get("ai_verify_enabled") == "on"
                _auth.set_setting("ai_verify_enabled", "on" if on else "off")
                # OPTIONAL independent-second-opinion overrides: a DIFFERENT backend/model for
                # the VERIFY step than capture uses. Both default to BLANK = "same as capture"
                # (byte-identical). An invalid backend choice is coerced back to blank.
                import ai_verify as _aiv
                vbe = (request.form.get("ai_verify_backend") or "").strip().lower()
                if vbe not in _aiv.VISION_BACKENDS:
                    vbe = ""              # blank -> fall back to the shared capture backend
                _auth.set_setting(_aiv.BACKEND_SETTING, vbe)
                vmodel = (request.form.get("ai_verify_model") or "").strip()
                _auth.set_setting(_aiv.MODEL_SETTING, vmodel)
                _ov = []
                if vbe:
                    _ov.append(f"backend <b>{esc(vbe)}</b>")
                if vmodel:
                    _ov.append(f"model <b>{esc(vmodel)}</b>")
                _ovtxt = (" Independent verify " + " / ".join(_ov) + "."
                          if _ov else " Verify uses the same model as capture.")
                banner = (("AI PDF verification turned ON (advisory). Note: this sends the "
                           "ORIGINAL PDF to the configured AI provider." if on
                           else "AI PDF verification turned OFF.") + _ovtxt)
            elif act == "set_ai_vision_capture":
                # OPT-IN AI VISION CAPTURE (default OFF). THE DELIBERATE "AI for capture"
                # exception: this SENDS the original PDF page images to the AI provider to
                # read a comprehensive capture document. Opt-in flag only; the backend/key
                # reuse the extractor/verify selection (Claude/OpenAI, vision-capable).
                on = request.form.get("ai_vision_capture_enabled") == "on"
                _auth.set_setting("ai_vision_capture_enabled", "on" if on else "off")
                banner = ("AI vision capture turned ON (advisory). Note: this sends the "
                          "ORIGINAL PDF page images to the configured AI provider for "
                          "unknown-layout/scanned invoices." if on
                          else "AI vision capture turned OFF.")
            elif act == "set_dlp_policy":
                # DATA CLASSIFICATION / DLP: the OPT-IN external-AI sensitivity gate. The
                # default `restricted` is PERMISSIVE (allow everything) — tightening it BLOCKS
                # any document whose classified label exceeds the chosen max from the external
                # AI. No backend here; purely a policy setting.
                import classify as _cl
                want = (request.form.get("ai_external_max_sensitivity") or "").strip().lower()
                if want not in _cl.LABELS:
                    want = _cl.DEFAULT_MAX_SENSITIVITY
                _auth.set_setting(_cl.POLICY_SETTING, want)
                banner = (f"DLP external-AI sensitivity limit set to <b>{esc(want)}</b>."
                          + (" (Permissive — nothing is blocked.)"
                             if want == _cl.DEFAULT_MAX_SENSITIVITY else
                             " Documents classified above this are blocked from external AI."))
            elif act == "set_autopilot":
                # AUTO-PILOT INTAKE (default OFF). When ON, a document is AUTO-FILED into the
                # VAT pipeline WITHOUT human review — but ONLY when it is high-confidence AND
                # passes AI verification AND the deterministic validation gate. Anything
                # doubtful still waits for review. Opt-in flag only — no backend here; it
                # reuses the existing verify backend when verification is enabled.
                on = request.form.get("intake_autopilot_enabled") == "on"
                _auth.set_setting("intake_autopilot_enabled", "on" if on else "off")
                banner = ("Auto-pilot intake turned ON. High-confidence, verified documents "
                          "that pass the validation gate are now auto-filed; everything else "
                          "still waits for review." if on
                          else "Auto-pilot intake turned OFF (every document waits for review).")
            elif act == "set_api_keys":
                # Admin-managed provider API keys (sealed at rest via keyvault, applied to
                # os.environ immediately). A blank field LEAVES the existing key unchanged;
                # ticking "clear_<NAME>" REMOVES the stored key. A real systemd/shell env
                # var always wins and cannot be set/overridden here.
                import appsecrets as _aps
                saved, cleared = [], []
                for _nm in _aps.MANAGED:
                    if request.form.get("clear_" + _nm) == "on":
                        _aps.clear_secret(_nm)
                        cleared.append(_nm)
                        continue
                    _val = request.form.get("key_" + _nm, "")
                    if _val.strip():
                        _aps.set_secret(_nm, _val)
                        saved.append(_nm)
                _parts = []
                if saved:
                    _parts.append("saved " + ", ".join(esc(s) for s in saved))
                if cleared:
                    _parts.append("cleared " + ", ".join(esc(c) for c in cleared))
                banner = ("API keys updated — " + "; ".join(_parts) + "."
                          if _parts else
                          "No changes — blank fields leave existing keys untouched.")
            elif act == "test_ai_connection":
                # MINIMAL real round-trip to the configured vision backend (text-only, no
                # PDF) so the admin can verify the key/model in one click. Never logs/echoes
                # the API key; maps the provider error to a clear message.
                import ai_verify as _aiv
                res = _aiv.test_connection()
                if res["ok"]:
                    banner = (f"✅ OK — {esc(res['provider'])} responded "
                              f"(model <b>{esc(res['model'])}</b>).")
                else:
                    # A failed self-test is shown RED inline with the EXACT mapped reason
                    # (esc'd) — an explicit interactive probe, not a background app error,
                    # so it is NOT written to the error log. Pre-wrap as a 'bad' banner.
                    _banner_klass = "bad"
                    banner = f'❌ AI connection test failed: {esc(res["message"])}'
            elif act == "toggle":
                if tgt == session["user"]:
                    raise ValueError("you cannot disable your own account")
                u = _auth.get_user(tgt)
                _auth.set_active(tgt, 0 if u["active"] else 1)
                banner = f"<b>{esc(tgt)}</b> {'disabled' if u['active'] else 'enabled'}."
            elif act == "reset":
                _auth.add_user(tgt, request.form["password"])
                banner = f"Password of <b>{esc(tgt)}</b> reset."
            elif act == "set_email":
                # C3②: a user's optional contact email for secure-sharing alerts. A user
                # may set their OWN (the "my email" field); an admin may set any user's.
                who = tgt or session["user"]
                if who != session["user"] and session.get("role") != "admin":
                    raise ValueError("you can only change your own email")
                _auth.set_email(who, request.form.get("email", ""))
                banner = (f"Contact email for <b>{esc(who)}</b> updated — "
                          "secure-sharing alerts will go to it.")
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
            elif act == "rebuild_search":
                # Full reindex of the document/invoice corpus into the app-owned search.db
                # (reads the product DBs READ-ONLY). Idempotent; safe to re-run on demand.
                res = _search.rebuild()
                banner = (f"Search index rebuilt — <b>{res['rows']}</b> document/invoice "
                          f"row(s) indexed in {res['seconds']}s.")
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
            elif act == "set_brand":
                # C3③: custom branding for the PUBLIC viewer/room/gate pages only. Stored
                # in app_settings (org name + hex accent + optional small data-URL logo).
                # Validated so nothing unsafe reaches the CSP-scoped public response.
                _auth.set_setting("brand_name", request.form.get("brand_name", "").strip())
                accent = request.form.get("brand_accent", "").strip()
                if accent and not re.fullmatch(r"#[0-9A-Fa-f]{3,8}", accent):
                    raise ValueError("accent must be a hex color like #2d6cdf")
                _auth.set_setting("brand_accent", accent)
                logo = request.form.get("brand_logo", "").strip()
                if request.form.get("brand_logo_clear") == "on":
                    logo = ""
                if logo and not logo.startswith("data:image/"):
                    raise ValueError("logo must be a data:image/... URL "
                                     "(no remote origins — keep it small)")
                if len(logo) > 200000:
                    raise ValueError("logo data URL is too large — keep it under ~150 KB")
                if logo or request.form.get("brand_logo_clear") == "on":
                    _auth.set_setting("brand_logo", logo)
                banner = ("Public-viewer branding saved — it shows on /s and /r pages "
                          "(the authed app is unchanged).")
            elif act == "set_sso":
                # OPTIONAL single sign-on (OIDC). DEFAULT OFF; local username/password
                # ALWAYS stays available as the fallback. The client secret is sealed at
                # rest (keyvault) and write-only here — a blank field LEAVES it unchanged.
                import sso as _sso
                on = request.form.get("sso_enabled") == "on"
                _auth.set_setting("sso_enabled", "on" if on else "off")
                provider = (request.form.get("sso_provider") or "").strip()
                if provider not in _sso.PROVIDER_PRESETS:
                    provider = "custom"
                _auth.set_setting("sso_provider", _sso.PROVIDER_PRESETS[provider][0])
                _auth.set_setting("sso_issuer",
                                  (request.form.get("sso_issuer") or "").strip().rstrip("/"))
                _auth.set_setting("sso_client_id",
                                  (request.form.get("sso_client_id") or "").strip())
                _auth.set_setting("sso_allowed_domains",
                                  (request.form.get("sso_allowed_domains") or "").strip())
                _auth.set_setting("sso_auto_provision",
                                  "on" if request.form.get("sso_auto_provision") == "on"
                                  else "off")
                # Secret: blank leaves it unchanged; ticking clear removes it; otherwise seal.
                if request.form.get("sso_secret_clear") == "on":
                    _sso.set_secret("")
                elif (request.form.get("sso_client_secret") or "").strip():
                    _sso.set_secret(request.form.get("sso_client_secret"))
                if on and not _sso.enabled():
                    _banner_klass = "bad"
                    banner = ("SSO settings saved, but SSO is NOT yet active — it needs an "
                              "issuer URL, a client ID and a saved client secret before the "
                              "Sign-in-with-SSO button appears.")
                else:
                    banner = ("Single sign-on " + ("ENABLED" if on else "turned OFF")
                              + " — local username/password login always remains available.")
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
            banner = f'<div class="card"><b class="{_banner_klass}">{banner}</b></div>'
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
        # C3②: per-user contact email for secure-sharing alerts (admin may set any user's).
        email_form = (
            '<form method="post" style="display:inline">' + _csrf_input()
            + f'<input type="hidden" name="username" value="{esc(u["username"])}">'
            + f'<input type="email" name="email" value="{esc(u.get("email") or "")}" '
              'placeholder="email" style="width:150px"> '
              '<button name="__act" value="set_email">Set email</button></form>')
        utr.append([f'<td>{esc(u["username"])}{" <b>(you)</b>" if me else ""}</td>'
                    f'<td>{esc(u["role"])}</td>',
                    f'<td class="{ "ok" if u["active"] else "bad"}">'
                    f'{"active" if u["active"] else "DISABLED"}</td>',
                    f'<td>{email_form}</td>',
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
                  f'{_bkbtn("verify_metrics","✓ Drift-check settled metrics")}'
                  f'{_bkbtn("rebuild_search","🔎 Rebuild search index")}</div>'
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
    # C3③: custom branding for the PUBLIC viewer (the /s and /r pages + their gates).
    _brand = _share_brand()
    _has_logo = bool(_brand.get("logo"))
    brandcard = ('<div class="card"><h2>Public-viewer branding</h2>'
                 '<div class="note" style="margin-top:0">Brand the PUBLIC secure-sharing '
                 'pages (the /s document viewer, the /r data-room index, and their '
                 'password / NDA / email / sign gates) with your org name, accent color '
                 'and an optional small logo. <b>The signed-in app is unchanged.</b> The '
                 'logo must be a same-origin <code>data:</code> image (no remote URLs — '
                 'keep it small).</div>'
                 '<form method="post" class="f" style="margin-top:8px">' + _csrf_input()
                 + f'<label>Org display name<input name="brand_name" '
                   f'value="{esc(_brand.get("name") or "")}" '
                   'placeholder="e.g. Baltic Fleet Recovery"></label>'
                 + f'<label>Accent color (hex)<input name="brand_accent" '
                   f'value="{esc(_brand.get("accent") or "")}" placeholder="#2d6cdf" '
                   'style="width:120px"></label>'
                 + '<label>Logo (data:image/... URL, optional)'
                   '<textarea name="brand_logo" rows="2" '
                   'placeholder="data:image/png;base64,...  (leave blank to keep current)">'
                   '</textarea></label>'
                 + ('<label class="ck"><input type="checkbox" name="brand_logo_clear" '
                    'value="on"> Remove the current logo</label>' if _has_logo else '')
                 + '<button name="__act" value="set_brand">Save branding</button></form>'
                 + ('<div class="note">A logo is currently set.</div>' if _has_logo
                    else '<div class="note">No logo set — the public header shows the '
                         'org name only (or the generic header when blank).</div>')
                 + '</div>')
    modchecks = "".join(
        f'<label class="chk" style="display:flex;gap:7px;align-items:center;font-size:13px;'
        f'flex-direction:row;color:var(--ink);margin:3px 0">'
        f'<input type="checkbox" name="mod_{esc(k)}" {"checked" if module_enabled(k) else ""}> '
        f'<b>{esc(k)}</b> — {esc(lbl)}</label>'
        for k, (lbl, _eps) in MODULES.items())
    # Services control center — a visual switchboard: colored status dot + a big ON/OFF
    # switch per service, minimal text. No command line needed for anything toggleable.
    import services_status as _svc
    _DOT = {"active": "#1a7f37", "off": "#9aa6b2", "needs_setup": "#c98a00", "info": "#0e5fa8"}

    def _svc_row(s):
        dot = _DOT.get(s["status"], "#9aa6b2")
        warn = (f'<div style="font-size:12px;color:#9a6a00;margin-top:2px">⚠ {esc(s["reason"])}'
                + (f' — {esc(s["fix"])}' if s.get("fix") else "") + '</div>'
                if s["status"] == "needs_setup" else "")
        left = (f'<div style="display:flex;gap:11px;align-items:flex-start;min-width:0">'
                f'<span style="flex:0 0 auto;width:12px;height:12px;border-radius:50%;'
                f'background:{dot};margin-top:4px"></span>'
                f'<div style="min-width:0"><div style="font-weight:600">{esc(s["title"])}</div>'
                f'<div class="note" style="margin:0;font-size:12px">{esc(s["what"])}</div>'
                f'{warn}</div></div>')
        if s["toggleable"] and s["setting"]:
            on = s["on"]
            nxt = "off" if on else "on"
            bg = ("#1a7f37" if (on and s["status"] == "active")
                  else "#c98a00" if on else "#cfd6dd")
            fg = "#fff" if on else "#5b6b7a"
            right = (f'<form method="post" style="margin:0;flex:0 0 auto">' + _csrf_input()
                     + f'<input type="hidden" name="service" value="{esc(s["setting"])}">'
                     + f'<input type="hidden" name="state" value="{nxt}">'
                     + f'<button name="__act" value="set_service" '
                       f'title="Click to turn {nxt.upper()}" '
                       f'style="min-width:66px;padding:8px 16px;border:none;border-radius:999px;'
                       f'font-weight:700;cursor:pointer;background:{bg};color:{fg}">'
                       f'{"ON" if on else "OFF"}</button></form>')
        else:
            if s["status"] == "info":
                bg, fg = ("#e8f0f9", "#0e5fa8") if s["on"] else ("#fff4e0", "#9a6a00")
            else:
                bg, fg = ("#e7f5ec", "#1a7f37") if s["on"] else ("#eef1f4", "#5b6b7a")
            right = (f'<span style="flex:0 0 auto;padding:6px 12px;border-radius:999px;'
                     f'font-size:12px;font-weight:600;background:{bg};color:{fg}">'
                     f'{esc(s["reason"] or ("On" if s["on"] else "Off"))}</span>')
        return ('<div style="display:flex;justify-content:space-between;align-items:center;'
                f'gap:14px;padding:11px 0;border-top:1px solid #eef1f4">{left}{right}</div>')

    svccenter = ('<div class="card"><h2>Services — switch on / off</h2>'
                 '<div class="note" style="margin-top:0">'
                 '<span style="color:#1a7f37">●</span> on &amp; working &nbsp; '
                 '<span style="color:#c98a00">●</span> needs setup &nbsp; '
                 '<span style="color:#9aa6b2">●</span> off &nbsp;— tap a switch to change.</div>'
                 + "".join(_svc_row(s) for s in _svc.services()) + '</div>')
    modf = ('<div class="card"><h2>Modules — turn parts of the app on / off</h2>'
            '<div class="note" style="margin-top:0">Switch whole parts of the system on or off. '
            'A part that is off disappears from the menu and its pages are unavailable to everyone '
            '(you can turn it back on here at any time). Core pages — dashboard, entities, '
            'customers, suppliers and this panel — are always on.</div>'
            '<form method="post" style="margin-top:8px">'
            + _csrf_input() + modchecks
            + '<div style="margin-top:10px"><button name="__act" value="set_modules">'
              'Save modules</button></div></form></div>')
    # Provider API keys — admin-managed, sealed at rest (appsecrets). Lets the admin set
    # a key from the panel instead of a systemd drop-in; a real env var still WINS and is
    # shown as "from environment". The key is never rendered back (masked tail only).
    import appsecrets as _aps
    def _keyrow(nm, label, placeholder):
        src, hint = _aps.status(nm)
        if src == "env":
            badge = ('<b class="ok">from environment</b> '
                     '<span class="note">(set on the server — overrides this panel)</span>')
        elif src == "stored":
            badge = f'<b class="ok">stored &#10003;</b> <span class="note">{esc(hint)}</span>'
        else:
            badge = '<b class="bad">not set</b>'
        clear = ((f'<label class="note" style="font-weight:400;align-self:center">'
                  f'<input type="checkbox" name="clear_{nm}"> clear</label>')
                 if src == "stored" else "")
        itype = "text" if nm in _aps._NOT_SECRET else "password"
        return (f'<div style="margin:8px 0"><div style="font-size:13px">'
                f'<b>{esc(label)}</b> — {badge}</div>'
                f'<div class="f" style="margin-top:4px">'
                f'<input name="key_{nm}" type="{itype}" autocomplete="new-password" '
                f'placeholder="{esc(placeholder)}" style="min-width:340px"> {clear}</div></div>')
    apikeysf = ('<div class="card"><h2>AI provider API keys</h2>'
                '<div class="note" style="margin-top:0">Paste a provider API key here to power the '
                'AI features below — <b>no server/terminal access needed</b>. Keys are '
                '<b>encrypted at rest</b> (envelope encryption) and take effect immediately. '
                'A <b>blank</b> field leaves the existing key unchanged. If a key is provided by '
                'the server environment it shows as <b>from environment</b> and takes precedence. '
                'The key itself is never displayed back — only a masked tail.</div>'
                '<form method="post" style="margin-top:8px">'
                + _csrf_input()
                + _keyrow("ANTHROPIC_API_KEY", "Anthropic (Claude)", "sk-ant-api03-…")
                + _keyrow("OPENAI_API_KEY", "OpenAI", "sk-…")
                + '<details style="margin-top:6px"><summary class="note" style="cursor:pointer">'
                  'Azure OpenAI (optional)</summary>'
                + _keyrow("AZURE_OPENAI_KEY", "Azure OpenAI key", "key")
                + _keyrow("AZURE_OPENAI_ENDPOINT", "Azure endpoint", "https://….openai.azure.com")
                + _keyrow("AZURE_OPENAI_DEPLOYMENT", "Azure deployment", "deployment name")
                + '</details>'
                + '<div style="margin-top:10px"><button name="__act" value="set_api_keys">'
                  'Save API keys</button></div></form></div>')
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
    # AI document assistant (advisory chat-with-document) — default OFF; reuses the same
    # backend selection (and keys) as the AI review above. Sends DERIVED DATA ONLY.
    _doc_chat_on = str(_auth.get_setting("ai_doc_chat_enabled", "off") or "off").lower() in ("on", "1", "true", "yes")
    aidocchatf = ('<div class="card"><h2>AI document assistant (advisory chat)</h2>'
                  '<div class="note" style="margin-top:0">An <b>opt-in, advisory</b> '
                  'chat-with-document on a document view: ask free-form questions about an '
                  'invoice\'s ALREADY-extracted data. It sends <b>DERIVED DATA ONLY</b> '
                  '(header fields + line items) — <b>never the PDF, never an IBAN/bank '
                  'account, never a secret</b> — and is <b>read-only commentary</b> that '
                  'NEVER changes a figure, status, lock, fee, or payment. <b>Default OFF.</b> '
                  'It reuses the AI review backend above (same key); with no backend '
                  'configured it stays disabled and sends nothing.</div>'
                  '<form method="post" class="f" style="margin-top:8px">'
                  + _csrf_input()
                  + f'<label class="chk" style="display:flex;gap:7px;align-items:center">'
                    f'<input type="checkbox" name="ai_doc_chat_enabled" {"checked" if _doc_chat_on else ""}> '
                    f'Enable the AI document assistant</label>'
                  + '<button name="__act" value="set_ai_doc_chat">Save assistant setting</button>'
                  + '</form>'
                  '<div class="note" style="margin-top:8px">Requires an AI review backend '
                  '(above) to be configured; otherwise the panel shows a disabled notice and '
                  'makes no network call.</div>'
                  + '</div>')
    # AI VERIFICATION against the ORIGINAL PDF (vision) — default OFF. THE DELIBERATE
    # EXCEPTION: this feature SENDS the original PDF (possible IBANs/bank details) to the
    # external AI provider. Loudly gated with a red warning card; reuses the ai_review
    # backend selection (Claude/OpenAI only — vision-capable).
    import ai_verify as _aiv
    _verify_on = str(_auth.get_setting("ai_verify_enabled", "off") or "off").lower() in ("on", "1", "true", "yes")
    _verify_st = _aiv.status(_aiv.SETTING)
    _verify_active = _ai_status_line("AI vision verify", _verify_st)
    _testconn = _ai_test_connection_form()
    # OPTIONAL independent-second-opinion overrides: a DIFFERENT backend/model for VERIFY than
    # capture uses. Blank = "same as capture" (byte-identical default). Only vision backends.
    _vbe_cur = (_auth.get_setting(_aiv.BACKEND_SETTING, "") or "").strip().lower()
    _vbe_opts = "".join(
        f'<option value="{esc(b)}" {"selected" if b == _vbe_cur else ""}>'
        f'{esc(b or "(same as capture)")}</option>'
        for b in ("",) + _aiv.VISION_BACKENDS)
    _vmodel_cur = (_auth.get_setting(_aiv.MODEL_SETTING, "") or "").strip()
    aiverifyf = ('<div class="card" style="border-color:var(--bad)">'
                 '<h2>AI verification against the original PDF (advisory)</h2>'
                 '<div class="note bad" style="margin-top:0">⚠️ Enabling this sends the '
                 '<b>ORIGINAL invoice PDF</b> (which may contain <b>IBANs/bank details</b>) '
                 'to the configured AI provider (Claude/OpenAI) for verification. Ensure a '
                 'data-processing agreement and an appropriate data region are in place. '
                 '<b>Off by default.</b></div>'
                 f'<div class="note" style="margin-top:6px">{_verify_active}</div>'
                 '<div class="note" style="margin-top:6px">It compares the already-extracted '
                 'draft to the PDF page images and flags discrepancies for you. It is '
                 '<b>advisory and read-only</b> — it NEVER changes a figure, status, lock, '
                 'or fee, and NEVER gates the commit; you still confirm the draft.</div>'
                 '<form method="post" class="f" style="margin-top:8px">'
                 + _csrf_input()
                 + f'<label class="chk" style="display:flex;gap:7px;align-items:center">'
                   f'<input type="checkbox" name="ai_verify_enabled" {"checked" if _verify_on else ""}> '
                   f'Enable AI verification against the original PDF</label>'
                 + f'<label>Verify backend<select name="ai_verify_backend">{_vbe_opts}</select></label>'
                 + f'<label>Verify model<input type="text" name="ai_verify_model" '
                   f'value="{esc(_vmodel_cur)}" placeholder="(same as capture)" '
                   f'style="min-width:220px"></label>'
                 + '<button name="__act" value="set_ai_verify">Save verification setting</button>'
                 + '</form>'
                 + '<div class="note" style="margin-top:6px">Leave the verify backend/model '
                   '<b>blank</b> to use the same model as capture; set a <b>different</b> model '
                   '(or provider) here for an <b>independent second opinion</b> — verification '
                   'then cross-checks capture with a different model. The key is never shown.</div>'
                 + _testconn
                 + '</div>')
    # OPT-IN AI VISION CAPTURE — default OFF. THE DELIBERATE "AI for capture" exception:
    # for an unknown-layout / scanned PLAIN PDF, it SENDS the original PDF page images to
    # the vision provider to read a comprehensive capture document, which then flows into
    # the SAME review screen + AI verification. Loudly gated; reuses the ai_verify provider
    # selection (Claude/OpenAI). Structured e-invoice / hybrid PDFs stay deterministic.
    _vcap_on = str(_auth.get_setting("ai_vision_capture_enabled", "off") or "off").lower() in ("on", "1", "true", "yes")
    import vision_capture as _vc
    _vcap_st = _aiv.status(_vc.SETTING)
    _vcap_active = _ai_status_line("AI vision capture", _vcap_st)
    if _vcap_st["active"]:
        _vcap_active += (f' <span class="note">Page cap <b>{_vc.VISION_CAPTURE_MAX_PAGES}</b>.</span>')
    aicapf = ('<div class="card" style="border-color:var(--bad)">'
              '<h2>AI vision capture of scanned / unknown-layout invoices (advisory)</h2>'
              '<div class="note bad" style="margin-top:0">⚠️ Enabling this sends the '
              '<b>ORIGINAL invoice PDF page images</b> (which may contain <b>IBANs/bank '
              'details</b>) to the configured AI provider (Claude/OpenAI) to read a '
              'comprehensive capture document. Ensure a data-processing agreement and an '
              'appropriate data region are in place. <b>Off by default.</b></div>'
              f'<div class="note" style="margin-top:6px">{_vcap_active}</div>'
              '<div class="note" style="margin-top:6px">When ON, a PLAIN unknown-layout / '
              'scanned PDF is read by the vision model as the <b>preferred</b> draft (it '
              'falls back to the existing OCR/parser path on any error). <b>Structured '
              'e-invoices and hybrid Factur-X PDFs stay deterministic and AI-free.</b> The '
              'capture is <b>advisory</b> — a human still confirms every figure; it changes '
              'no figure, status, lock, or fee and never gates the commit.</div>'
              '<form method="post" class="f" style="margin-top:8px">'
              + _csrf_input()
              + f'<label class="chk" style="display:flex;gap:7px;align-items:center">'
                f'<input type="checkbox" name="ai_vision_capture_enabled" {"checked" if _vcap_on else ""}> '
                f'Enable AI vision capture of scanned / unknown-layout invoices</label>'
              + '<button name="__act" value="set_ai_vision_capture">Save capture setting</button>'
              + '</form>'
              + '</div>')
    # DATA CLASSIFICATION / DLP (Box-Shield-style). An app-owned overlay that scans a
    # document's text for sensitive-data TYPES, assigns a sensitivity LABEL, and (OPT-IN)
    # gates what may be sent to the EXTERNAL AI by sensitivity. Default policy is permissive
    # (`restricted` = allow everything) so it never blocks until an admin tightens it.
    import classify as _cl
    _dlp_max = _cl.max_sensitivity()
    _dlp_counts = _cl.counts_by_label()
    _dlp_opts = "".join(
        f'<option value="{esc(lbl)}" {"selected" if lbl == _dlp_max else ""}>'
        f'{esc(lbl)}{" (permissive — allow all)" if lbl == _cl.DEFAULT_MAX_SENSITIVITY else ""}'
        f'</option>' for lbl in _cl.LABELS)
    _dlp_count_rows = "".join(
        f'<li><b>{esc(lbl)}</b>: {esc(str(_dlp_counts.get(lbl, 0)))} document(s)</li>'
        for lbl in reversed(_cl.LABELS) if _dlp_counts.get(lbl))
    _dlp_count_block = (f'<ul class="note" style="margin-top:6px">{_dlp_count_rows}</ul>'
                        if _dlp_count_rows else
                        '<div class="note" style="margin-top:6px">No documents classified '
                        'yet (a label is assigned when a document\'s text is available).</div>')
    dlpf = ('<div class="card"><h2>Data classification / DLP</h2>'
            '<div class="note" style="margin-top:0">Documents are scanned for sensitive-data '
            '<b>types</b> (IBAN, bank account, BIC/SWIFT, card-like, email, phone, VAT id, '
            'personal names) and assigned a sensitivity label on the scale '
            '<code>public &lt; internal &lt; confidential &lt; restricted</code>. Only the '
            'finding <b>type + a count</b> is stored — <b>never the matched value</b>. The '
            'label is advisory and is shown on each document record.</div>'
            '<div class="note" style="margin-top:8px">The <b>external-AI gate</b> below is '
            '<b>opt-in</b>: it sets the maximum sensitivity that may be sent to the external '
            'AI (vision verify / capture). The default <code>restricted</code> is '
            '<b>permissive</b> (allow everything) — tighten it to BLOCK any document '
            'classified above the chosen limit from the external AI; such a document is kept '
            'on-server only. This gate <b>fails open</b> on a scan error and never blocks a '
            'deterministic / on-server path.</div>'
            '<form method="post" class="f" style="margin-top:8px">'
            + _csrf_input()
            + '<label>Maximum sensitivity allowed to external AI '
              f'<select name="ai_external_max_sensitivity">{_dlp_opts}</select></label>'
            + '<button name="__act" value="set_dlp_policy">Save DLP policy</button>'
            + '</form>'
            + f'<h3 style="margin-top:12px">Documents by sensitivity</h3>{_dlp_count_block}'
            + '</div>')
    # AUTO-PILOT INTAKE (advisory). Default OFF. When ON, a document is AUTO-FILED into the
    # VAT pipeline WITHOUT human review — but ONLY when it is high-confidence AND passes AI
    # verification AND the deterministic validation gate; everything doubtful still waits.
    _apilot_on = str(_auth.get_setting("intake_autopilot_enabled", "off") or "off").lower() in (
        "on", "1", "true", "yes")
    autopilotf = ('<div class="card" style="border-color:var(--bad)">'
                  '<h2>Auto-pilot intake (advisory)</h2>'
                  '<div class="note bad" style="margin-top:0">⚠️ This is a '
                  '<b>compliance-sensitive money path</b> (VAT filings). When ON, a document '
                  'is <b>auto-filed</b> (registered) into the VAT pipeline <b>without human '
                  'review</b> — but ONLY when it is <b>high-confidence</b> AND passes <b>AI '
                  'verification</b> (when verification is enabled) AND the <b>deterministic '
                  'validation gate</b>. <b>Off by default.</b></div>'
                  '<div class="note" style="margin-top:6px">Anything doubtful — lower '
                  'confidence, a failed verification, any validation error, a synthetic / '
                  'unmatched line, or any error — <b>still waits for review exactly as '
                  'today</b>. The legal gate is never bypassed: registration only happens on '
                  '<code>validate_batch().can_commit</code>. Auto-filed documents are audit-'
                  'logged under the actor <b>autopilot</b>.</div>'
                  '<form method="post" class="f" style="margin-top:8px">'
                  + _csrf_input()
                  + '<label class="chk" style="display:flex;gap:7px;align-items:center">'
                    f'<input type="checkbox" name="intake_autopilot_enabled" '
                    f'{"checked" if _apilot_on else ""}> '
                    'Enable auto-pilot intake (auto-file high-confidence, verified, valid documents)'
                    '</label>'
                  + '<button name="__act" value="set_autopilot">Save</button>'
                  + '</form>'
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
               '<code>X-API-Key</code>. See docs/MANUAL.md#external-api-apiv1-token-contract.</div>'
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
                  + tbl(["Username", "Role", "Status", "Email (alerts)", "Last login",
                         "Actions"], utr)
                  + addf
                  + '<div class="note">An optional <b>contact email</b> targets that '
                    'user\'s own secure-sharing alerts (a shared document viewed / signed, '
                    'a data-room question). Unset = the team relay (Email settings below).'
                    '</div></div>')
    security_card = (f'<div class="card"><h2>Security status</h2>'
                     f'<p>TLS certificate: '
                     f'{"<span class=ok>cert.pem present - app serves HTTPS</span>" if tls else "<span class=bad>none - run python3 make_cert.py (self-signed) or install a CA cert</span>"}'
                     f' &nbsp;|&nbsp; Password storage: <span class="ok">scrypt (salted)</span>'
                     f' &nbsp;|&nbsp; Session cookies: HttpOnly, SameSite'
                     f'{", Secure (HTTPS)" if tls else ""}</p></div>')
    # Single sign-on (SSO / OIDC) — optional, default off. Local username/password
    # always remains the fallback. The client secret is sealed at rest and write-only.
    import sso as _sso
    _scfg = _sso.config()
    _sso_redirect = url_for("sso_callback", _external=True)
    _sso_state = ('<span class="ok">ACTIVE</span>' if _scfg["enabled"]
                  else '<span class="bad">not active</span>')
    _sso_secret_state = ('<span class="ok">set ✓</span>' if _scfg["has_secret"]
                         else '<span class="bad">not set</span>')
    _provider_opts = "".join(
        f'<option value="{esc(k)}">{esc(lbl)}</option>'
        for k, (lbl, _hint) in _sso.PROVIDER_PRESETS.items())
    ssocard = ('<div class="card"><h2>Single sign-on (SSO)</h2>'
               f'<p>Status: {_sso_state} &nbsp;|&nbsp; client secret: {_sso_secret_state}</p>'
               '<div class="note" style="margin-top:0">Optional OpenID Connect (OIDC) '
               'login for <b>Google Workspace</b>, <b>Microsoft Entra ID</b> (Azure AD / '
               'Microsoft 365) or any standard OIDC provider. <b>Default off.</b> '
               'Local username/password login <b>always stays available</b> as the '
               'fallback, so an admin can never be locked out. New SSO users get the '
               '<b>processor</b> role only, and only when their verified email domain is '
               'in the allowlist below. Register this exact redirect URI at the provider: '
               f'<code>{esc(_sso_redirect)}</code></div>'
               '<form method="post" class="f" style="margin-top:8px">' + _csrf_input()
               + '<label class="ck"><input type="checkbox" name="sso_enabled" '
               + ('checked' if (_auth.get_setting("sso_enabled", "off") == "on") else '')
               + '> Enable single sign-on</label>'
               + f'<label>Provider preset<select name="sso_provider" '
                 'data-sso-preset>' + _provider_opts + '</select></label>'
               + f'<label>Issuer / discovery URL<input name="sso_issuer" '
                 f'value="{esc(_scfg["issuer"])}" '
                 'placeholder="https://accounts.google.com  or  '
                 'https://login.microsoftonline.com/&lt;tenant&gt;/v2.0"></label>'
               + f'<label>Client ID<input name="sso_client_id" '
                 f'value="{esc(_scfg["client_id"])}" placeholder="OIDC application/client ID"></label>'
               + '<label>Client secret<input type="password" name="sso_client_secret" '
                 'placeholder="leave blank to keep current" autocomplete="new-password"></label>'
               + ('<label class="ck"><input type="checkbox" name="sso_secret_clear" '
                  'value="on"> Remove the stored client secret</label>'
                  if _scfg["has_secret"] else '')
               + f'<label>Allowed email domains<input name="sso_allowed_domains" '
                 f'value="{esc(", ".join(_scfg["allowed_domains"]))}" '
                 'placeholder="example.com, sub.example.com (blank = allow any)"></label>'
               + '<label class="ck"><input type="checkbox" name="sso_auto_provision" '
               + ('checked' if _scfg["auto_provision"] else '')
               + '> Auto-create a processor account on first SSO login (within the '
                 'allowed domains)</label>'
               + '<button name="__act" value="set_sso">Save SSO settings</button></form>'
               + '<div class="note">The client secret is <b>sealed at rest</b> (envelope '
                 'encryption) and never shown — leave it blank to keep the current one. '
                 'With no allowed domains, sign-in is refused for everyone (set at least '
                 'one to go live).</div></div>')
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
            + ssocard
            + logins_card
            + errcard
            + apikeyf
            + '<h2 class="section" id="data">Data &amp; Backups</h2>'
            + backupcard
            + '<h2 class="section" id="notifications">Notifications</h2>'
            + smtpcard
            + brandcard
            + '<h2 class="section" id="modules">Modules &amp; AI</h2>'
            + svccenter
            + modf
            + apikeysf
            + aireviewf
            + aidocchatf
            + aiverifyf
            + aicapf
            + dlpf
            + autopilotf
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
    docs/STRATEGY.md#multi-tenancy-program-plan. Every value escaped via esc()."""
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
        '<code>docs/STRATEGY.md#multi-tenancy-program-plan</code>.</div>'
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
    # A record can exist while its stored file is missing/unreadable (NULL stored_path,
    # deleted/corrupt vault file). Guard so that is a clean 404, never a 500 crash.
    data = None
    try:
        if d["stored_path"]:
            data = document_vault.get_bytes(d["stored_path"], VR.DOCDIR)
    except Exception as e:
        _log_exc("doc_download get_bytes", e)
    if not data:
        _log_exc("doc_download", ValueError(
            f"document {doc_id} file missing/unreadable (stored_path={d['stored_path']!r})"))
        return page('<div class="card"><b class="bad">This document file is missing or '
                    'unreadable.</b><div class="note">The record exists but its stored file '
                    'could not be read — run “Check document integrity” in Admin.</div></div>',
                    ""), 404
    return send_file(io.BytesIO(data), as_attachment=True, download_name=d["filename"])


def _doc_subject(doc_id):
    """Assemble the DERIVED descriptor for the AI document assistant from a vaulted invoice
    document: the (entity, supplier, invoice_ref) of the document plus the matching
    supplier_invoices line (date / country / currency). NET-EUR basis. NO bank/secret field
    is ever read here — only header + line metadata; ai_assistant.build_context redacts again
    as a final guard. Returns (descriptor_dict, doc_row) or (None, None) if the doc is gone."""
    import vat_refund as VR
    con = VR.connect()
    try:
        d = con.execute("SELECT * FROM invoice_documents WHERE id=?", (doc_id,)).fetchone()
    finally:
        con.close()
    if d is None:
        return None, None
    sup = d["supplier"]; ref = d["invoice_ref"]; ent = d["entity"]
    line = {"invoice_no": ref}
    try:
        import supplier_master as SM
        scon = SM.connect()
        try:
            inv = scon.execute(
                "SELECT country, invoice_date, currency FROM supplier_invoices "
                "WHERE supplier=? AND invoice_no=?", (sup, ref)).fetchone()
        finally:
            scon.close()
        if inv is not None:
            line.update({"country": inv["country"], "date": inv["invoice_date"],
                         "currency": inv["currency"]})
    except Exception as e:
        _log_exc("doc-assistant supplier line", e)
    subject = {"kind": "invoice", "supplier": sup, "statement_ref": ref,
               "customer": ent, "lines": [line]}
    return subject, d


@app.route("/doc-assistant/<int:doc_id>", methods=["GET", "POST"])
def doc_assistant(doc_id):
    """ADVISORY AI document assistant (chat-with-document) over a vaulted invoice's DERIVED
    data. OPT-IN + default-OFF: only active when `ai_doc_chat_enabled` is ON AND a backend is
    configured (ai_assistant.enabled()); otherwise renders a disabled notice and makes NO
    network call. Access: `documents` capability (enforced in _guard). NEVER mutates a figure,
    status, lock, fee, or payment — it reads derived data and returns text; the only writes are
    chat-history rows in ai_assistant's own app-owned DB. The payload sent to the model is
    DERIVED DATA ONLY (header + line items) — never the PDF, never an IBAN/account/secret."""
    import ai_assistant
    subject, d = _doc_subject(doc_id)
    if subject is None:
        return page('<div class="card"><b class="bad">No such document.</b></div>', "doc"), 404
    subject_ref = f"doc:{doc_id}"
    label = f"{d['supplier']} · invoice {d['invoice_ref']} ({d['entity']})"

    disclaimer = ('<div class="note" style="margin-top:0">Answers are <b>advisory only</b>, '
                  'generated from this document\'s <b>derived/extracted data only</b> (header '
                  'fields + line items — never the PDF, never any bank account or secret). The '
                  'assistant is read-only commentary: it <b>never changes any figure, status, '
                  'lock, fee, or payment</b>.</div>')

    if not ai_assistant.enabled():
        body = (f'<div class="card"><h2>Ask the AI assistant — {esc(label)}</h2>'
                + disclaimer
                + '<div class="note" style="margin-top:8px"><b>Disabled</b> — configure an AI '
                  'backend and enable the AI document assistant in '
                  '<a href="/admin">Admin</a>. No request is sent while it is off.</div></div>'
                f'<p><a href="/doc/{doc_id}">&larr; download this document</a> · '
                '<a href="/documents">back to the document vault</a></p>')
        return page(body, "doc")

    banner = ""
    if request.method == "POST":
        question = (request.form.get("question", "") or "").strip()
        if question:
            chat_id = ai_assistant.start_chat(subject_ref, created_by=session.get("user", ""))
            ai_assistant.record_message(chat_id, "user", question)
            history = ai_assistant.messages_for(subject_ref)
            try:
                ctx = ai_assistant.build_context(subject)
                answer = ai_assistant.ask(ctx, question, history=history)
            except Exception as e:                 # ask() already never raises; belt-and-braces
                _log_exc("doc-assistant ask", e)
                answer = ("The AI assistant is unavailable right now — advisory only, nothing "
                          "was changed.")
            ai_assistant.record_message(chat_id, "assistant", answer)

    transcript = ai_assistant.messages_for(subject_ref)
    bubbles = ""
    for m in transcript:
        who = "You" if m["role"] == "user" else "AI assistant"
        side = "var(--mut)" if m["role"] == "user" else "var(--ok)"
        bubbles += (f'<div style="margin:6px 0;padding:8px;border-left:3px solid {side}">'
                    f'<b>{esc(who)}</b> <span class="note">{esc(m["created_at"])}</span>'
                    f'<div style="white-space:pre-wrap;margin-top:3px">{esc(m["content"])}</div>'
                    '</div>')
    if not bubbles:
        bubbles = '<div class="note">No questions asked yet.</div>'

    body = (f'<div class="card"><h2>Ask the AI assistant — {esc(label)}</h2>'
            + disclaimer
            + '<form method="post" style="margin-top:10px">'
            + _csrf_input()
            + '<textarea name="question" rows="3" style="width:100%" '
              'placeholder="Ask about this invoice\'s extracted data…"></textarea>'
            + '<div style="margin-top:8px"><button>Ask the assistant</button></div></form>'
            + f'<div style="margin-top:12px">{bubbles}</div></div>'
            f'<p><a href="/doc/{doc_id}">&larr; download this document</a> · '
            '<a href="/documents">back to the document vault</a></p>')
    return page(body, "doc")


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
    data = None
    try:
        if d["stored_path"]:
            data = document_vault.get_bytes(d["stored_path"], CD.DOCDIR)
    except Exception as e:
        _log_exc("customer doc_download get_bytes", e)
    if not data:
        _log_exc("customer doc_download", ValueError(
            f"customer document {doc_id} file missing/unreadable (stored_path={d['stored_path']!r})"))
        return page('<div class="card"><b class="bad">This document file is missing or '
                    'unreadable.</b></div>', ""), 404
    return send_file(io.BytesIO(data), as_attachment=True, download_name=d["filename"])


@app.route("/doc-requests", methods=["GET", "POST"])
def doc_requests():
    """The document-requests CONTROL BOARD — a cross-customer view of every contract /
    power-of-attorney request with its derived status (requested / generated / sent for
    signature / signed / received / cancelled / overdue), filters (status / customer /
    kind) and quick actions (generate, save-to-vault, mark received, open the vaulted
    file). CRM data, so same gating as /customers (admin-only, `customers` capability).
    Every DB value is esc-escaped; handled failures go through _log_exc."""
    import customer_master as CD
    from urllib.parse import quote
    banner = ""
    if request.method == "POST":
        act = request.form.get("__act", "")
        try:
            req_id = int(request.form.get("req_id", "0") or 0)
            if act == "gen_doc_request":
                con = CD.connect()
                CD.generate_request_document(con, req_id)
                con.close()
                banner = (f'<div class="card"><b class="ok">Request #{esc(str(req_id))} '
                          'generated and vaulted.</b></div>')
            elif act == "vault_doc_request":
                con = CD.connect()
                ref, m = CD.vault_generated_document(con, req_id, by=session.get("user"))
                con.close()
                if ref is None:
                    raise ValueError(m or "could not save to vault")
                banner = (f'<div class="card"><b class="ok">Request #{esc(str(req_id))} saved '
                          'to the vault as a platform document.</b></div>')
            elif act == "mark_received":
                # mark a request received WITHOUT a wet-signed upload (the board's quick
                # action); the per-customer register handles the file-upload path.
                f = request.files.get("signed_file")
                signed_bytes = f.read() if (f and f.filename) else None
                signed_name = f.filename if (f and f.filename) else None
                con = CD.connect()
                ok, m = CD.advance_document_request(
                    con, req_id, "received", signed_file=signed_bytes,
                    signed_filename=signed_name, by=session.get("user"))
                con.close()
                if not ok:
                    raise ValueError(m)
                banner = (f'<div class="card"><b class="ok">Request #{esc(str(req_id))}: '
                          f'{esc(m)}.</b></div>')
            else:
                raise ValueError("unknown action")
        except Exception as e:
            _log_exc("document-requests board", e)
            banner = f'<div class="card"><b class="bad">Error: {esc(str(e))}</b></div>'

    f_status = (request.args.get("status") or "").strip()
    f_customer = (request.args.get("customer") or "").strip().upper()
    f_kind = (request.args.get("kind") or "").strip()

    con = CD.connect()
    rows_data = CD.document_request_board(
        con, customer=f_customer or None,
        status=f_status or None, kind=f_kind or None)
    cust_codes = [r["code"] for r in con.execute(
        "SELECT code FROM customers ORDER BY code")]
    con.close()

    _BADGE = {"requested": "var(--mut)", "generated": "var(--mut)",
              "sent_for_signature": "var(--mut)", "signed": "var(--mut)",
              "received": "var(--ok)", "cancelled": "var(--bad)"}

    def _badge(st, overdue):
        bg = "var(--bad)" if overdue else _BADGE.get(st, "var(--mut)")
        label = "overdue" if overdue else (st or "").replace("_", " ")
        return (f'<span style="display:inline-block;padding:1px 7px;border-radius:9px;'
                f'font-size:11px;background:{bg};color:#fff">{esc(label)}</span>')

    def _qform(rid, act, label, bg, *, file_field=False, confirm=None):
        enc = ' enctype="multipart/form-data"' if file_field else ""
        file_in = ('<input type="file" name="signed_file" style="width:130px">'
                   if file_field else "")
        oc = f' onclick="return confirm(\'{esc(confirm)}\')"' if confirm else ""
        return ('<form method="post"' + enc + ' style="display:inline">' + _csrf_input()
                + f'<input type="hidden" name="__act" value="{esc(act)}">'
                + f'<input type="hidden" name="req_id" value="{int(rid)}">' + file_in
                + f'<button{oc} style="background:{bg};font-size:11px;padding:3px 8px">'
                + f'{esc(label)}</button></form> ')

    body_rows = []
    counts = {}
    for r in rows_data:
        st = r["status"]
        counts[st] = counts.get(st, 0) + 1
        rid = r["id"]
        valid = CD.DOC_REQUEST_TRANSITIONS.get(st, set())
        actions = ""
        if st in ("requested", "generated"):
            actions += _qform(rid, "gen_doc_request",
                              "Re-generate" if st == "generated" else "Generate", "var(--ok)")
        if r["generated_doc_id"] and not r["vault_ref"]:
            actions += _qform(rid, "vault_doc_request", "Save to vault", "var(--ok)")
        if st in ("signed", "sent_for_signature") and "received" in valid:
            actions += _qform(rid, "mark_received", "Mark received", "var(--ok)",
                             file_field=True)
        # document links: the generated draft, the vaulted platform ref (share), signed
        doc_links = []
        if r["generated_doc_id"]:
            doc_links.append(f'<a href="/customer-doc/{int(r["generated_doc_id"])}">generated</a>')
        if r["vault_ref"]:
            doc_links.append('<a href="/share?doc_ref=' + quote(r["vault_ref"] or "", safe="")
                             + '" title="vaulted platform document — tag / version / share">'
                             '&#128274; vaulted</a>')
        if r["signed_doc_id"]:
            doc_links.append(f'<a href="/customer-doc/{int(r["signed_doc_id"])}">signed</a>')
        body_rows.append([
            f'<td>{esc(r["customer"])}<div class="note">{esc(r.get("company_name") or "")}</div></td>',
            f'<td>{esc((r["kind"] or "").replace("_", " "))}</td>',
            f'<td>{esc(r["refund_country"] or "—")}</td>',
            f'<td>{_badge(st, r["overdue"])}</td>',
            f'<td class="note">{esc(r["requested_at"] or "")}<br>{esc(str(r.get("age_days") or 0))} d</td>',
            f'<td>{" ".join(doc_links) or "<span class=note>—</span>"}</td>',
            f'<td>{actions or "<span class=note>—</span>"}</td>',
        ])
    table = (tbl(["Customer", "Kind", "Country", "Status", "Requested", "Documents", "Actions"],
                 body_rows) if body_rows else '<p class="note">No document requests match.</p>')

    # filter form
    def _sel(name, options, cur, blank="(all)"):
        opts = f'<option value="">{esc(blank)}</option>' + "".join(
            f'<option value="{esc(v)}"{" selected" if v == cur else ""}>{esc(lbl)}</option>'
            for v, lbl in options)
        return f'<label>{esc(name)}<select name="{esc(name.lower())}">{opts}</select></label>'

    status_opts = [(s, s.replace("_", " ")) for s in CD.DOC_REQUEST_STATES]
    kind_opts = [(k, k.replace("_", " ")) for k in CD.DOC_REQUEST_KINDS]
    cust_opts = [(c, c) for c in cust_codes]
    filt = ('<form method="get" class="f" style="margin-bottom:10px">'
            + _sel("Status", status_opts, f_status)
            + _sel("Customer", cust_opts, f_customer)
            + _sel("Kind", kind_opts, f_kind)
            + '<button>Filter</button> '
            + ('<a href="/doc-requests" class="note" style="align-self:flex-end">clear</a>'
               if (f_status or f_customer or f_kind) else "")
            + '</form>')
    summary = " · ".join(f"{esc(k.replace('_',' '))}: {v}"
                         for k, v in sorted(counts.items())) or "no requests"
    body = (banner
            + '<div class="card"><h1>Document requests — control board</h1>'
            + '<p class="note">Every contract / power-of-attorney request across customers. '
              'Generate a draft, save it to the vault (so it can be tagged, versioned and '
              'shared as a platform document), then track it through to received. '
              'Generated drafts are vaulted automatically.</p>'
            + filt
            + f'<div class="note" style="margin-bottom:6px">{summary}</div>'
            + table + '</div>')
    return page(body, "dreq")

# ---------------------------------------------------------------- secure share links (B1)
# A Papermark/DocSend-style trackable PUBLIC link over a vaulted PDF. The management
# surface (create / list / per-link views / revoke) is authenticated + capability-gated
# ('share'); the public viewer (/s/<token>) and file stream (/s/<token>/file) are PUBLIC
# by design and run their OWN per-token gate (revoked/expired/password/email). Vault
# bytes are fetched strictly via document_vault by the stored doc_ref — no path the
# caller controls. See sharing.py.

def _vault_doc_choices():
    """(doc_ref, label) pairs of vaulted invoice documents, so the create form offers a
    pick-list instead of forcing a hand-typed locator. Read-only; never raises -> []."""
    try:
        import vat_refund as VR
        con = VR.connect()
        try:
            rows = con.execute(
                """SELECT stored_path, filename, entity, supplier, invoice_ref
                   FROM invoice_documents ORDER BY uploaded_at DESC LIMIT 500""").fetchall()
        finally:
            con.close()
        out = []
        for r in rows:
            label = " · ".join(x for x in (r["filename"], r["entity"], r["supplier"],
                                           r["invoice_ref"]) if x)
            out.append((r["stored_path"], label or r["stored_path"]))
        return out
    except Exception as e:
        _log_exc("share: list vault documents", e)
        return []


@app.route("/share", methods=["GET", "POST"])
def share_links_page():
    """List the current user's share links (with view counts) and revoke them."""
    import sharing
    actor = session.get("user", "")
    banner = ""
    if request.method == "POST" and request.form.get("__act") == "revoke":
        try:
            lid = int(request.form.get("link_id", "0"))
        except (TypeError, ValueError):
            lid = 0
        link = sharing.get_by_id(lid)
        if link and link.get("created_by") == actor:
            ok, msg = sharing.revoke(lid, actor)
            banner = ('<div class="card" style="border-left:4px solid var(--ok)">'
                      '<b class="ok">Link revoked.</b> It no longer serves the document.</div>'
                      if ok else
                      f'<div class="card" style="border-left:4px solid var(--bad)">'
                      f'<b class="bad">Could not revoke.</b> {esc(msg)}</div>')
        else:
            banner = ('<div class="card" style="border-left:4px solid var(--bad)">'
                      '<b class="bad">No such link.</b></div>')
    try:
        links = sharing.list_links(actor)
    except Exception as e:
        _log_exc("share: list links", e)
        links = []
    rows = []
    for l in links:
        url = f"/s/{l['token']}"
        gates = []
        if l.get("password_hash"):
            gates.append("password")
        if l.get("require_email"):
            gates.append("email")
        if l.get("nda_required"):
            gates.append("NDA")
        if l.get("watermark"):
            gates.append("watermark")
        if l.get("expires_at"):
            gates.append(f"expires {esc(l['expires_at'])}")
        state = ('<b class="bad">revoked</b>' if l.get("revoked")
                 else '<b class="bad">expired</b>' if sharing.is_expired(l)
                 else '<b class="ok">active</b>')
        revoke_btn = ""
        if not l.get("revoked"):
            revoke_btn = ('<form method="post" style="display:inline">' + _csrf_input()
                          + f'<input type="hidden" name="link_id" value="{l["id"]}">'
                          + '<button name="__act" value="revoke" '
                          'onclick="return confirm(\'Revoke this link?\')">Revoke</button></form>')
        rows.append([
            esc(l.get("title") or "(untitled)"),
            f'<a href="{esc(url)}">{esc(url)}</a>',
            (", ".join(gates) or "—"),
            state,
            f'<a href="/share/{l["id"]}/views">{l.get("views", 0)}</a>',
            esc(l.get("created_at") or ""),
            revoke_btn,
        ])
    table = (tbl(["Title", "Public link", "Gates", "State", "Views", "Created", ""], rows)
             if rows else '<p class="note">No share links yet.</p>')
    create_form = (
        '<div class="card"><h2>Create a share link</h2>'
        '<p class="note">A secure, trackable PUBLIC link to a vaulted document. '
        'Bytes are served same-origin in an iframe; every first view is logged.</p>'
        '<form method="post" action="/share/create" class="f">' + _csrf_input()
        + '<label>Document'
          '<select name="doc_ref">'
        + "".join(f'<option value="{esc(dr)}">{esc(lbl)}</option>'
                  for dr, lbl in _vault_doc_choices())
        + '</select></label>'
          '<label>…or paste a vault reference<input name="doc_ref_manual" '
          'value="' + str(esc(request.args.get("doc_ref", ""))) + '" '
          'placeholder="leave blank to use the pick-list above"></label>'
          '<label>Title<input name="title" placeholder="optional"></label>'
          '<label>Expires (UTC, optional)<input name="expires_at" '
          'placeholder="YYYY-MM-DD or YYYY-MM-DD HH:MM"></label>'
          '<label>Password (optional)<input name="password" type="password" '
          'autocomplete="new-password"></label>'
          '<label class="ck"><input type="checkbox" name="require_email" value="1"> '
          'Require the viewer to enter an email first</label>'
          '<label class="ck"><input type="checkbox" name="nda_required" value="1"> '
          'Require the viewer to accept an agreement (NDA) first — logged</label>'
          '<label>Agreement text (shown on the NDA gate; optional)'
          '<textarea name="agreement_text" rows="3" '
          'placeholder="leave blank for a default confidentiality notice"></textarea></label>'
          '<label class="ck"><input type="checkbox" name="watermark" value="1"> '
          'Watermark every page with the viewer + timestamp</label>'
          '<div style="margin-top:8px"><button>Create link</button></div>'
          '</form></div>')
    return page(banner + create_form
                + f'<div class="card"><h2>Your share links</h2>{table}</div>', "shr")


@app.route("/share/create", methods=["POST"])
def share_create():
    import sharing
    actor = session.get("user", "")
    doc_ref = (request.form.get("doc_ref_manual") or request.form.get("doc_ref") or "").strip()
    link, err = sharing.create_link(
        doc_ref, request.form.get("title", ""), actor,
        expires_at=(request.form.get("expires_at") or "").strip() or None,
        password=(request.form.get("password") or "") or None,
        require_email=bool(request.form.get("require_email")),
        nda_required=bool(request.form.get("nda_required")),
        agreement_text=(request.form.get("agreement_text") or "") or None,
        watermark=bool(request.form.get("watermark")))
    if err or not link:
        return page('<div class="card" style="border-left:4px solid var(--bad)">'
                    f'<b class="bad">Could not create the link.</b> {esc(err or "unknown error")}'
                    '</div><p><a href="/share">Back to share links</a></p>', "shr")
    url = f"/s/{link['token']}"
    return page('<div class="card" style="border-left:4px solid var(--ok)">'
                '<b class="ok">Share link created.</b>'
                f'<p>Public link: <a href="{esc(url)}">{esc(url)}</a></p>'
                '<p class="note">Anyone with this link can view the document '
                '(subject to any password / email / expiry you set). Revoke it any time '
                'on the share-links page.</p></div>'
                '<p><a href="/share">Back to share links</a></p>', "shr")


def _share_doc_page_count(link):
    """Best-effort total page count of the vaulted PDF behind a link, for the B3
    completion %. Returns an int or None (unknown). Never raises — a failure just means
    completion % is shown relative to the deepest page actually viewed instead."""
    try:
        import document_vault, vat_refund as VR, io
        data = document_vault.get_bytes(link["doc_ref"], VR.DOCDIR)
        from pypdf import PdfReader
        return len(PdfReader(io.BytesIO(data)).pages)
    except Exception as e:
        _log_exc("share: page count", e)
        return None


def _fmt_dwell(ms):
    """Human-readable dwell from milliseconds (e.g. '1m 05s', '12.3s')."""
    try:
        s = (int(ms) or 0) / 1000.0
    except (TypeError, ValueError):
        return "—"
    if s >= 60:
        return f"{int(s // 60)}m {int(s % 60):02d}s"
    return f"{s:.1f}s"


# ================================================================ ② E-SIGNATURE (SES)
# The AUTHED surface (capability `documents`, module `compliance`): a dashboard that
# (a) sends a vaulted document OR a generated CRM contract (item ①) FOR SIGNATURE by minting
# a signature_request + a share link with require_signature; and (b) lists every signature
# with its audit fields + a "verify hash" result + the produced signed PDF. SES only — every
# surface is labelled "not a qualified electronic signature". esc() everything; _log_exc.

def _esign_doc_choices():
    """(subject_ref, label) pairs the send-for-signature picker offers: vaulted invoice
    documents + the generated CRM contract drafts (item ①, by their vault locator). Read-only;
    never raises -> []."""
    out = list(_vault_doc_choices())
    try:
        import customer_master as CM
        con = CM.connect()
        try:
            board = CM.document_request_board(con)
        finally:
            con.close()
        for r in board:
            ref = r.get("vault_ref")
            if ref and r.get("kind") == "contract":
                lbl = (f"[contract] {r.get('company_name') or r.get('customer')} "
                       f"({r.get('status')})")
                # tie back to the document request so a signature attaches to the board.
                out.append((f"docreq:{r['id']}", lbl))
    except Exception as e:
        _log_exc("esign: contract choices", e)
    return out


def _esign_resolve_ref(subject_ref):
    """Resolve a send-for-signature subject_ref to the underlying VAULT locator (doc_ref) for
    the share link. A `docreq:<id>` reference resolves to the request's generated vault ref;
    a plain locator is returned as-is. Returns (doc_ref or None, subject_ref). Never raises."""
    sref = (subject_ref or "").strip()
    if not sref.startswith("docreq:"):
        return sref or None, sref
    try:
        import customer_master as CM
        req_id = int(sref.split(":", 1)[1])
        con = CM.connect()
        try:
            ref = CM.generated_vault_ref(con, req_id)
        finally:
            con.close()
        return (ref or None), sref
    except Exception as e:
        _log_exc("esign: resolve docreq ref", e)
        return None, sref


@app.route("/esign", methods=["GET"])
def esign_page():
    """The e-signature dashboard: a send-for-signature form + a table of every request and
    its signatures (audit fields + signed-PDF link + verify hint)."""
    import esign
    actor = session.get("user", "")
    try:
        requests_ = esign.list_requests()
    except Exception as e:
        _log_exc("esign: list requests", e)
        requests_ = []
    rows = []
    for r in requests_:
        try:
            sigs = esign.signatures_for(r["id"])
        except Exception as e:
            _log_exc("esign: signatures for", e)
            sigs = []
        link_html = "—"
        if sigs:
            who = "; ".join(esc(s.get("signer_name") or "") for s in sigs)
            link_html = f'<a href="/esign/{r["id"]}/verify">{who}</a>'
        status = esc(r.get("status") or "")
        void_btn = ""
        if r.get("status") == "pending":
            void_btn = ('<form method="post" action="/esign/void" style="display:inline">'
                        + _csrf_input()
                        + f'<input type="hidden" name="request_id" value="{r["id"]}">'
                        + '<button onclick="return confirm(\'Void this signature request?\')"'
                        '>Void</button></form>')
        rows.append([
            esc(r.get("title") or "(untitled)"),
            esc((r.get("subject_ref") or "")[:60]),
            status,
            esc(len(sigs)),
            link_html,
            esc(r.get("created_at") or ""),
            void_btn,
        ])
    table = (tbl(["Title", "Document", "Status", "Signatures", "Signers", "Created", ""], rows)
             if rows else '<p class="note">No signature requests yet.</p>')
    send_form = (
        '<div class="card"><h2>Send a document for signature</h2>'
        '<p class="note">Mints a Simple Electronic Signature (SES) request + a public '
        'share link that asks the recipient to review the document, consent and sign '
        '(typed/drawn name). <b>SES — not a qualified electronic signature.</b></p>'
        '<form method="post" action="/esign/send" class="f">' + _csrf_input()
        + '<label>Document or generated contract'
          '<select name="subject_ref">'
        + "".join(f'<option value="{esc(dr)}">{esc(lbl)}</option>'
                  for dr, lbl in _esign_doc_choices())
        + '</select></label>'
          '<label>…or paste a vault reference<input name="subject_ref_manual" '
          'placeholder="leave blank to use the pick-list above"></label>'
          '<label>Title<input name="title" placeholder="optional"></label>'
          '<label>Expires (UTC, optional)<input name="expires_at" '
          'placeholder="YYYY-MM-DD or YYYY-MM-DD HH:MM"></label>'
          '<label class="ck"><input type="checkbox" name="require_email" value="1"> '
          'Ask the signer for an email first</label>'
          '<div style="margin-top:8px"><button>Create signing link</button></div>'
          '</form></div>')
    return page(send_form
                + f'<div class="card"><h2>Signature requests</h2>{table}</div>', "esign")


@app.route("/esign/send", methods=["POST"])
def esign_send():
    """Create a signature_request over the chosen document/contract + a share link with
    require_signature, and surface the public signing URL."""
    import esign, sharing
    actor = session.get("user", "")
    subject_ref = (request.form.get("subject_ref_manual")
                   or request.form.get("subject_ref") or "").strip()
    doc_ref, subject_ref = _esign_resolve_ref(subject_ref)
    title = (request.form.get("title") or "").strip() or None
    if not doc_ref:
        return page('<div class="card" style="border-left:4px solid var(--bad)">'
                    '<b class="bad">Could not send for signature.</b> The selected document '
                    'has no resolvable vault reference.</div>'
                    '<p><a href="/esign">Back to e-signatures</a></p>', "esign")
    req, err = esign.create_request(subject_ref, title, actor)
    if err or not req:
        return page('<div class="card" style="border-left:4px solid var(--bad)">'
                    f'<b class="bad">Could not create the request.</b> {esc(err or "error")}'
                    '</div><p><a href="/esign">Back to e-signatures</a></p>', "esign")
    link, lerr = sharing.create_link(
        doc_ref, title or (req.get("title") or "Document to sign"), actor,
        expires_at=(request.form.get("expires_at") or "").strip() or None,
        require_email=bool(request.form.get("require_email")),
        require_signature=True, signature_request_id=req["id"])
    if lerr or not link:
        return page('<div class="card" style="border-left:4px solid var(--bad)">'
                    f'<b class="bad">Could not create the signing link.</b> {esc(lerr or "")}'
                    '</div><p><a href="/esign">Back to e-signatures</a></p>', "esign")
    url = f"/s/{link['token']}"
    return page('<div class="card" style="border-left:4px solid var(--ok)">'
                '<b class="ok">Signing link created.</b>'
                f'<p>Send this to the signer: <a href="{esc(url)}">{esc(url)}</a></p>'
                '<p class="note">They review the document, accept the consent statement '
                'and sign (typed/drawn name). The signed PDF is produced and vaulted, and '
                'you can verify the SHA-256 binding on the e-signatures page. '
                '<b>SES — not a qualified electronic signature.</b></p></div>'
                '<p><a href="/esign">Back to e-signatures</a></p>', "esign")


@app.route("/esign/void", methods=["POST"])
def esign_void():
    """Void a pending signature request (it can no longer be signed)."""
    import esign
    try:
        rid = int(request.form.get("request_id", "0"))
    except (TypeError, ValueError):
        rid = 0
    ok, msg = esign.void_request(rid, session.get("user", ""))
    banner = ('<div class="card" style="border-left:4px solid var(--ok)">'
              '<b class="ok">Request voided.</b></div>' if ok else
              '<div class="card" style="border-left:4px solid var(--bad)">'
              f'<b class="bad">Could not void.</b> {esc(msg)}</div>')
    return page(banner + '<p><a href="/esign">Back to e-signatures</a></p>', "esign")


@app.route("/esign/<int:request_id>/verify", methods=["GET"])
def esign_verify_page(request_id):
    """Per-request signature detail + a live "verify hash" result for each signature (re-hash
    the produced signed PDF against the recorded binding)."""
    import esign, document_vault, vat_refund as VR
    req = esign.get_request(request_id)
    if not req:
        return page('<div class="card"><b class="bad">No such signature request.</b></div>'
                    '<p><a href="/esign">Back to e-signatures</a></p>', "esign"), 404
    sigs = esign.signatures_for(request_id)
    blocks = []
    for s in sigs:
        try:
            v = esign.verify(s["id"], docdir=VR.DOCDIR)
        except Exception as e:
            _log_exc("esign: verify", e)
            v = {"ok": False, "detail": "verify failed", "signed_ok": None}
        badge = ('<b class="ok">PASS — binding intact</b>' if v.get("ok")
                 else f'<b class="bad">FAIL — {esc(v.get("detail") or "altered")}</b>')
        signed_link = "—"
        if s.get("signed_locator"):
            signed_link = (f'<a href="/esign/{request_id}/signed/{s["id"]}">'
                           'Download signed PDF</a>')
        img = ""
        if s.get("signature_image"):
            img = ('<p class="note">Drawn signature: <i>captured</i> '
                   f'({esc(len(s["signature_image"]))} bytes)</p>')
        detail_rows = [
            ["Signer name", esc(s.get("signer_name") or "")],
            ["Signer email", esc(s.get("signer_email") or "—")],
            ["Signed at (UTC)", esc(s.get("signed_at") or "")],
            ["IP", esc(s.get("ip") or "—")],
            ["User agent", esc((s.get("user_agent") or "")[:160])],
            ["Document SHA-256", esc(s.get("signed_doc_sha256") or "—")],
            ["Signed-PDF SHA-256", esc(s.get("signed_sha256") or "—")],
            ["Consent", esc(s.get("consent_text") or "")],
            ["Verify result", badge],
            ["Signed PDF", signed_link],
        ]
        blocks.append('<div class="card"><h3>Signature</h3>'
                      + tbl(["Field", "Value"], detail_rows) + img + '</div>')
    head = (f'<div class="card"><h2>Signature request — '
            f'{esc(req.get("title") or "(untitled)")}</h2>'
            f'<p class="note">Document: {esc((req.get("subject_ref") or "")[:80])} '
            f'&nbsp;·&nbsp; Status: <b>{esc(req.get("status") or "")}</b></p>'
            '<p class="note"><b>SES — not a qualified electronic signature.</b> '
            'The SHA-256 binding is the integrity anchor: any later byte change fails '
            'verification.</p></div>')
    body = head + ("".join(blocks) if blocks
                   else '<div class="card"><p class="note">No signatures recorded yet.</p>'
                        '</div>')
    return page(body + '<p><a href="/esign">Back to e-signatures</a></p>', "esign")


@app.route("/esign/<int:request_id>/signed/<int:signature_id>")
def esign_signed_download(request_id, signature_id):
    """Stream the produced signed PDF for a signature (authed; the e-sign dashboard surface)."""
    import esign, document_vault, vat_refund as VR
    sig = esign.get_signature(signature_id)
    if not sig or sig.get("request_id") != request_id or not sig.get("signed_locator"):
        return page('<div class="card"><b class="bad">No signed PDF available.</b></div>'
                    f'<p><a href="/esign/{request_id}/verify">Back</a></p>', "esign"), 404
    try:
        data = document_vault.get_bytes(sig["signed_locator"], VR.DOCDIR)
    except Exception as e:
        _log_exc("esign: signed download", e)
        return page('<div class="card"><b class="bad">Could not read the signed PDF.</b>'
                    f'</div><p><a href="/esign/{request_id}/verify">Back</a></p>',
                    "esign"), 404
    resp = Response(data, mimetype="application/pdf")
    resp.headers["Content-Disposition"] = "inline; filename=signed.pdf"
    resp.headers["Cache-Control"] = "no-store"
    return resp


# ---------------------------------------------------------------- WORKFLOW (Box Relay-style)
# A configurable, ORDERED approval/routing engine over a document/invoice (workflow.py). It is
# ADVISORY: a workflow approval is additive process tracking and NEVER overrides the VAT legal
# gates (checklist / locks / period-end still govern actual filing). The admin DEFINE/MANAGE
# pages are admin-only; the Tasks inbox + act + start-run are open to any logged-in user.
_WF_STATUS_BADGE = {
    "running": ('warn', 'running'), "approved": ('ok', 'approved'),
    "rejected": ('bad', 'rejected'), "done": ('ok', 'done'),
    "cancelled": ('mut', 'cancelled'),
}


def _wf_status_html(status):
    cls, label = _WF_STATUS_BADGE.get(status, ('mut', status or '—'))
    style = "" if cls == 'mut' else f' class="{cls}"'
    return f'<span{style}>{esc(label)}</span>'


@app.route("/tasks", methods=["GET"])
def tasks_page():
    """The Tasks / Approvals inbox: the PENDING workflow tasks assigned to the logged-in user
    (or their role, or unassigned), with Approve/Reject + a note. Open to any logged-in user."""
    import workflow
    user = session.get("user", "")
    role = session.get("role", "processor")
    try:
        tasks = workflow.my_tasks(user=user, role=role)
    except Exception as e:
        _log_exc("workflow: my_tasks", e)
        tasks = []
    rows = []
    for t in tasks:
        act_form = (
            '<form method="post" action="/tasks/act" class="f" style="margin:0;gap:6px">'
            + _csrf_input()
            + f'<input type="hidden" name="task_id" value="{int(t["id"])}">'
            + '<input name="note" placeholder="note (optional)" style="width:160px">'
            + '<button name="decision" value="approve">Approve</button>'
            + '<button name="decision" value="reject" '
              'style="background:var(--bad)">Reject</button></form>')
        rows.append([
            esc(t.get("workflow_name") or "—"),
            esc((t.get("subject_ref") or "")[:60]),
            esc(t.get("action") or ""),
            esc(t.get("assignee") or "anyone"),
            esc(t.get("created_at") or ""),
            act_form,
        ])
    table = (tbl(["Workflow", "Subject", "Step action", "Assigned to", "Created", "Decision"],
                 rows) if rows else '<p class="note">No pending tasks assigned to you.</p>')
    note = ('<p class="note">These are advisory process steps. Approving a workflow step '
            'records the approval and advances the run — it does <b>not</b> change a VAT '
            'claim status, lock, fee or figure; the statutory checklist, invoice locks and '
            'period-end gate remain the sole authority over what can actually be filed.</p>')
    return page(f'<div class="card"><h2>My tasks &amp; approvals</h2>{note}{table}</div>',
                "tasks")


@app.route("/tasks/act", methods=["POST"])
def task_act():
    """Record an approve/reject decision on a pending task and advance the run."""
    import workflow
    actor = session.get("user", "")
    try:
        task_id = int(request.form.get("task_id", "0"))
    except (TypeError, ValueError):
        task_id = 0
    decision = (request.form.get("decision") or "").strip().lower()
    note = (request.form.get("note") or "").strip() or None
    run, err = workflow.act_on_task(task_id, decision, actor, note)
    if err or not run:
        banner = ('<div class="card" style="border-left:4px solid var(--bad)">'
                  f'<b class="bad">Could not act on the task.</b> {esc(err or "error")}</div>')
    else:
        banner = ('<div class="card" style="border-left:4px solid var(--ok)">'
                  f'<b class="ok">Recorded.</b> The run is now '
                  f'{_wf_status_html(run.get("status"))}.</div>')
    return page(banner + '<p><a href="/tasks">Back to tasks</a></p>', "tasks")


@app.route("/workflow/start", methods=["POST"])
def workflow_start():
    """Start a workflow run over a subject (a doc:<id> / invoice ref). Open to any logged-in
    user — starting an advisory process is not a privileged action. `return_to` controls the
    redirect target (defaults back to the subject's runs view via /tasks)."""
    import workflow
    actor = session.get("user", "")
    try:
        workflow_id = int(request.form.get("workflow_id", "0"))
    except (TypeError, ValueError):
        workflow_id = 0
    subject_ref = (request.form.get("subject_ref") or "").strip()
    run, err = workflow.start_run(workflow_id, subject_ref, actor)
    if err or not run:
        banner = ('<div class="card" style="border-left:4px solid var(--bad)">'
                  f'<b class="bad">Could not start the workflow.</b> {esc(err or "error")}</div>')
    else:
        banner = ('<div class="card" style="border-left:4px solid var(--ok)">'
                  '<b class="ok">Workflow started.</b> The first step\'s task has been '
                  'assigned. This is an advisory process step and does not change any VAT '
                  'figure, status or lock.</div>')
    return page(banner + '<p><a href="/tasks">Back to tasks</a></p>', "tasks")


@app.route("/workflows", methods=["GET"])
def workflow_admin():
    """Admin: define + manage workflows (name + an ordered list of steps, each with an
    assignee role/user + an action). Admin-only (see ADMIN_ONLY)."""
    import workflow
    try:
        wfs = workflow.list_workflows()
    except Exception as e:
        _log_exc("workflow: list", e)
        wfs = []
    rows = []
    for w in wfs:
        step_html = " → ".join(
            esc(f'{s.get("name") or s.get("action")} '
                f'[{s.get("action")}'
                + (f' · {s.get("assignee")}' if s.get("assignee") else '')
                + ']')
            for s in (w.get("steps") or [])) or '—'
        toggle = (
            '<form method="post" action="/workflows/deactivate" style="display:inline">'
            + _csrf_input()
            + f'<input type="hidden" name="workflow_id" value="{int(w["id"])}">'
            + f'<input type="hidden" name="active" value="{0 if w.get("active") else 1}">'
            + f'<button>{"Deactivate" if w.get("active") else "Activate"}</button></form>')
        rows.append([
            esc(w.get("name") or ""),
            esc(w.get("trigger") or "manual"),
            step_html,
            ('<span class="ok">active</span>' if w.get("active")
             else '<span class="mut">inactive</span>'),
            toggle,
        ])
    table = (tbl(["Name", "Trigger", "Steps", "Status", ""], rows) if rows
             else '<p class="note">No workflows defined yet.</p>')

    actions = "".join(f'<option value="{esc(a)}">{esc(a)}</option>'
                      for a in workflow.STEP_ACTIONS)
    # the steps are entered as ONE line per step: "name | assignee | action | param=value".
    define_form = (
        '<div class="card"><h2>Define a workflow</h2>'
        '<p class="note">An ordered list of steps. Each step has an assignee (a role '
        '<i>admin</i>/<i>processor</i> or a username; blank = anyone), an action '
        '(<b>approve</b> = a human gate; <b>sign</b> = open an e-signature request; '
        '<b>notify</b> = send an alert email; <b>tag</b> = assign a document tag), and '
        'optional params. Enter one step per line as '
        '<code>name | assignee | action | param=value</code>.</p>'
        '<form method="post" action="/workflows/define" class="f">' + _csrf_input()
        + '<label>Name<input name="name" required></label>'
        + '<label>Trigger<select name="trigger">'
          '<option value="manual">manual</option></select></label>'
        + '<label style="flex:1 1 100%">Steps (one per line)'
          '<textarea name="steps" rows="4" style="width:100%;font:13px ui-monospace,monospace" '
          'placeholder="Manager approval | admin | approve&#10;'
          'Sign PoA | admin | sign | title=Power of attorney&#10;'
          'Tag reviewed | | tag | tag=Workflow-reviewed"></textarea></label>'
        + f'<span class="note" style="flex:1 1 100%">Available actions: {esc(", ".join(workflow.STEP_ACTIONS))}</span>'
        + '<div style="margin-top:8px"><button>Create workflow</button></div>'
          '</form></div>')
    advisory = ('<p class="note">Workflows are an <b>advisory</b> overlay: a workflow approval '
                'is process tracking only and never overrides the VAT legal gates (checklist, '
                'invoice locks, period-end) or mutates a claim figure, status or fee.</p>')
    return page(define_form
                + f'<div class="card"><h2>Workflows</h2>{advisory}{table}</div>', "wfadm")


def _parse_step_lines(raw):
    """Parse the textarea "one step per line" format into the step dicts normalize_steps wants.
    Each line: "name | assignee | action | k=v | k2=v2". Blank lines are skipped. Never raises."""
    steps = []
    for line in (raw or "").splitlines():
        line = line.strip()
        if not line:
            continue
        parts = [p.strip() for p in line.split("|")]
        name = parts[0] if len(parts) > 0 else ""
        assignee = parts[1] if len(parts) > 1 else ""
        action = (parts[2] if len(parts) > 2 else "approve").lower()
        params = {}
        for kv in parts[3:]:
            if "=" in kv:
                k, v = kv.split("=", 1)
                params[k.strip()] = v.strip()
        steps.append({"name": name, "assignee": assignee, "action": action,
                      "params": params})
    return steps


@app.route("/workflows/define", methods=["POST"])
def workflow_define():
    """Create a workflow from the admin form."""
    import workflow
    name = (request.form.get("name") or "").strip()
    trigger = (request.form.get("trigger") or "manual").strip()
    steps = _parse_step_lines(request.form.get("steps") or "")
    wf, err = workflow.define_workflow(name, steps, trigger)
    if err or not wf:
        banner = ('<div class="card" style="border-left:4px solid var(--bad)">'
                  f'<b class="bad">Could not create the workflow.</b> {esc(err or "error")}</div>')
    else:
        banner = ('<div class="card" style="border-left:4px solid var(--ok)">'
                  f'<b class="ok">Workflow created.</b> {esc(wf.get("name"))} with '
                  f'{len(wf.get("steps") or [])} step(s).</div>')
    return page(banner + '<p><a href="/workflows">Back to workflows</a></p>', "wfadm")


@app.route("/workflows/deactivate", methods=["POST"])
def workflow_deactivate():
    """Activate / deactivate a workflow (so it no longer offers a manual start)."""
    import workflow
    try:
        wid = int(request.form.get("workflow_id", "0"))
    except (TypeError, ValueError):
        wid = 0
    active = (request.form.get("active") or "0").strip() in ("1", "true", "on", "yes")
    ok, msg = workflow.deactivate_workflow(wid, active)
    banner = ('<div class="card" style="border-left:4px solid var(--ok)">'
              f'<b class="ok">Workflow {"activated" if active else "deactivated"}.</b></div>'
              if ok else
              '<div class="card" style="border-left:4px solid var(--bad)">'
              f'<b class="bad">Could not update.</b> {esc(msg)}</div>')
    return page(banner + '<p><a href="/workflows">Back to workflows</a></p>', "wfadm")


@app.route("/share/<int:link_id>/views")
def share_views_page(link_id):
    import sharing
    actor = session.get("user", "")
    link = sharing.get_by_id(link_id)
    if not link or link.get("created_by") != actor:
        return page('<div class="card"><b class="bad">No such link.</b></div>', "shr"), 404
    try:
        views = sharing.views_for(link_id)
        rows = [[esc(v.get("viewed_at") or ""), esc(v.get("viewer_email") or "—"),
                 esc(v.get("ip") or "—"), esc((v.get("user_agent") or "")[:120])]
                for v in views]
        table = (tbl(["When (UTC)", "Email", "IP", "User agent"], rows) if rows
                 else '<p class="note">No views recorded yet.</p>')

        # ---- B3: page-by-page engagement ----
        total_pages = _share_doc_page_count(link)
        eng = sharing.page_engagement(link_id, total_pages)
        timeline = sharing.visitor_timeline(link_id)
        # If the real page count is unknown, fall back to the deepest page seen so the
        # completion % stays meaningful.
        denom = total_pages or (max((p["page"] for p in eng["pages"]), default=0) or None)
        comp = eng.get("completion_pct")
        if comp is None and denom:
            comp = round(min(eng["pages_viewed"], denom) / denom * 100, 1)

        summary = (
            '<div class="card"><h3>Engagement</h3>'
            f'<p class="note">Net EUR/L basis n/a — this is viewer engagement.</p>'
            f'<p>Pages viewed: <b>{esc(eng["pages_viewed"])}</b>'
            + (f' of {esc(denom)}' if denom else '')
            + (f' &nbsp;·&nbsp; Completion: <b>{esc(comp)}%</b>' if comp is not None else '')
            + f' &nbsp;·&nbsp; Total time: <b>{esc(_fmt_dwell(eng["total_ms"]))}</b>'
              f' &nbsp;·&nbsp; Distinct visitors: <b>{esc(eng["visitors"])}</b></p></div>')

        if eng["pages"]:
            prows = [[esc(p["page"]), esc(_fmt_dwell(p["dwell_ms"])), esc(p["sessions"])]
                     for p in eng["pages"]]
            per_page = ('<div class="card"><h3>Time per page</h3>'
                        + tbl(["Page", "Total time", "Visitors"], prows) + '</div>')
        else:
            per_page = ('<div class="card"><h3>Time per page</h3>'
                        '<p class="note">No page-by-page engagement recorded yet.</p></div>')

        vis_blocks = []
        for v in timeline:
            pr = [[esc(p["page"]), esc(_fmt_dwell(p["dwell_ms"]))] for p in v["pages"]]
            vis_blocks.append(
                f'<div style="margin:0 0 14px"><b>Visitor {esc(v["view_session"][:10] or "—")}</b> '
                f'<span class="note">— {esc(v["pages_viewed"])} page(s), '
                f'{esc(_fmt_dwell(v["total_ms"]))}, last {esc(v["last_at"] or "—")}</span>'
                + tbl(["Page", "Time"], pr) + '</div>')
        visitors = ('<div class="card"><h3>Per-visitor breakdown</h3>'
                    + ("".join(vis_blocks) if vis_blocks
                       else '<p class="note">No per-visitor engagement yet.</p>') + '</div>')
    except Exception as e:
        _log_exc("share: views page", e)
        table = '<p class="bad">Could not load engagement.</p>'
        summary = per_page = visitors = ""

    return page(f'<div class="card"><h2>Views — {esc(link.get("title") or "(untitled)")}</h2>'
                f'<p class="note">Public link: /s/{esc(link["token"])}</p>{table}</div>'
                + summary + per_page + visitors
                + '<p><a href="/share">Back to share links</a></p>', "shr")


# ---- PUBLIC viewer + file stream (NO auth by design; per-token gate runs in-view) ----
def _share_gate_or_form(token, link, action=None, record_agreement=None):
    """Run the per-token public gates over a LINK-LIKE row. Returns:
      ("ok",   email)      -> gates passed, `email` is the captured email (or None)
      ("deny", None)       -> missing/revoked/expired (caller renders a 404/410)
      ("form", html)       -> a password / NDA / email-capture form to render (200)
    The verified-password marker, NDA-accepted marker + captured email all live in the
    session keyed by token, so a refresh / the iframe file fetch don't re-prompt within
    the same session. Gate ORDER: revoked/expired -> password -> NDA -> email-capture
    -> view (NDA is shown BEFORE the document, but AFTER the password so a stranger can't
    read the agreement text of a protected link).

    The gate is link-TYPE agnostic — `link` is any dict carrying the share_links gate
    columns (a B1/B2 document link OR a B4 data-room link); the gate only reads those
    columns and the per-token session markers. Two seams keep the two cases distinct:
      * `action` — where the gate forms POST (defaults to /s/<token>; rooms pass /r/...);
      * `record_agreement(link, email, ip, ua)` — how an NDA acceptance is logged
        (defaults to sharing.record_agreement for document links; rooms inject
        sharing.record_room_agreement so the two id namespaces never cross-count).
    This is a thin generalization of the B1/B2 helper, NOT a rewrite: with both kwargs
    omitted the behaviour is byte-identical to before."""
    if not link or not sharing_is_active(link):
        return "deny", None
    if action is None:
        action = f"/s/{token}"
    import sharing
    if record_agreement is None:
        record_agreement = sharing.record_agreement
    sess_pw = session.get("_share_pw", {})
    sess_nda = session.get("_share_nda", {})
    sess_em = session.get("_share_em", {})
    # password gate
    if link.get("password_hash") and not sess_pw.get(token):
        if request.method == "POST" and request.form.get("share_password") is not None:
            if sharing.check_password(link.get("password_hash"),
                                      request.form.get("share_password", "")):
                sess_pw[token] = True
                session["_share_pw"] = sess_pw
            else:
                return "form", _share_password_form(token, error=True, action=action)
        else:
            return "form", _share_password_form(token, error=False, action=action)
    # NDA / agreement gate — no document bytes until the viewer has accepted (logged).
    if link.get("nda_required") and not sharing.has_accepted(link, sess_nda.get(token)):
        if request.method == "POST" and request.form.get("share_agree"):
            email = (session.get("_share_em", {}) or {}).get(token)
            try:
                record_agreement(link, email, request.remote_addr or "",
                                 request.headers.get("User-Agent", ""))
            except Exception as e:
                _log_exc("share: record agreement", e)
            sess_nda[token] = True
            session["_share_nda"] = sess_nda
        else:
            return "form", _share_agreement_form(token, link, action=action)
    # email-capture gate
    email = sess_em.get(token)
    if link.get("require_email") and not email:
        if request.method == "POST" and request.form.get("share_email"):
            email = (request.form.get("share_email") or "").strip()[:200]
            sess_em[token] = email
            session["_share_em"] = sess_em
        else:
            return "form", _share_email_form(token, action=action)
    return "ok", email


def sharing_is_active(link):
    import sharing
    return sharing.is_active(link)


_SHARE_CSS = (
    "<style>body{font-family:system-ui,Arial,sans-serif;margin:0;background:#0e1726;"
    "color:#e6edf3}.wrap{max-width:760px;margin:0 auto;padding:24px}"
    ".box{background:#16213a;border:1px solid #25304a;border-radius:10px;padding:20px;"
    "margin-top:40px}input{width:100%;box-sizing:border-box;padding:9px;margin:6px 0 12px;"
    "border-radius:6px;border:1px solid #34405c;background:#0e1726;color:#e6edf3}"
    "button{padding:9px 16px;border:0;border-radius:6px;background:#2d6cdf;color:#fff;"
    "cursor:pointer}.err{color:#ff8a8a;margin:0 0 8px}h2{margin-top:0}"
    "iframe{width:100%;height:90vh;border:0;background:#fff}"
    ".bar{padding:10px 16px;background:#16213a;border-bottom:1px solid #25304a}"
    # B3 pdf.js page-by-page viewer surface
    "#pdf-root{padding:18px 0;display:flex;flex-direction:column;align-items:center;gap:18px}"
    ".pdf-page-wrap{box-shadow:0 2px 14px rgba(0,0,0,.4);background:#fff}"
    ".pdf-page-label{font:12px system-ui;color:#9fb0c8;text-align:center;padding:4px 0;"
    "background:transparent}.pdf-page{display:block}"
    ".pdf-error{color:#ff8a8a;text-align:center;padding:40px}"
    # C3③ custom branding for the PUBLIC viewer header
    ".brandbar{display:flex;align-items:center;gap:12px;padding:12px 16px;"
    "border-bottom:1px solid #25304a;background:#16213a}"
    ".brandbar img{height:32px;width:auto;display:block}"
    ".brandbar .bname{font-weight:600;font-size:16px}</style>")


def _share_brand():
    """C3③: the app-owned PUBLIC-viewer branding config (org display name, accent color,
    optional small logo data URL). Returns a dict {name, accent, logo} with the configured
    values or None each when unset. Read-only; never raises -> all-None (default header)."""
    brand = {"name": None, "accent": None, "logo": None}
    try:
        brand["name"] = (_auth.get_setting("brand_name") or "").strip() or None
        accent = (_auth.get_setting("brand_accent") or "").strip()
        # accept only a simple hex color so it can be safely interpolated into CSS.
        if re.fullmatch(r"#[0-9A-Fa-f]{3,8}", accent or ""):
            brand["accent"] = accent
        logo = (_auth.get_setting("brand_logo") or "").strip()
        # only a same-origin data: image URL — never a remote origin (CSP-safe).
        if logo.startswith("data:image/"):
            brand["logo"] = logo
    except Exception as e:
        _log_exc("share: brand config", e)
    return brand


def _brand_header():
    """The branded header bar for the PUBLIC pages (org name + optional logo), or '' when
    no brand is configured (so the page falls back to its generic header). Every value is
    esc()'d; the logo is a same-origin data: URL only. Never raises -> ''."""
    try:
        brand = _share_brand()
        if not (brand.get("name") or brand.get("logo")):
            return ""
        logo = (f'<img src="{esc(brand["logo"])}" alt="logo">' if brand.get("logo") else "")
        name = (f'<span class="bname">{esc(brand["name"])}</span>'
                if brand.get("name") else "")
        return f'<div class="brandbar">{logo}{name}</div>'
    except Exception as e:
        _log_exc("share: brand header", e)
        return ""


def _brand_style():
    """A small per-response <style> that recolors the public buttons/accents to the
    configured brand accent. '' when no accent is set. The accent is validated to a hex
    color in _share_brand, so this interpolation is CSP-safe. Never raises -> ''."""
    try:
        accent = _share_brand().get("accent")
        if not accent:
            return ""
        return (f"<style>.box button,.brandbar .bname{{}}"
                f"button{{background:{accent}}}.brandbar .bname{{color:{accent}}}"
                f"a{{color:{accent}}}</style>")
    except Exception as e:
        _log_exc("share: brand style", e)
        return ""


def _share_shell(title, inner, head_extra="", brand=True):
    """A minimal standalone HTML page for the PUBLIC surface (it does NOT use the
    authenticated BASE template / nav). Title is escaped by the caller as needed.
    `head_extra` lets the pdf.js viewer add its <script type=module> tag (served from
    /static — script-src 'self').

    C3③: when `brand` is True (the default for every public surface) a configured org
    name/logo/accent is applied — a branded header bar above the content + an accent
    restyle. An UNSET brand falls back to the generic header (no bar, default accent).
    The logo is a same-origin data: URL only; nothing here adds a remote origin, so the
    scoped/global CSP is untouched."""
    brand_head = _brand_style() if brand else ""
    brand_bar = _brand_header() if brand else ""
    return ("<!doctype html><html lang=en><head><meta charset=utf-8>"
            "<meta name=viewport content='width=device-width,initial-scale=1'>"
            f"<title>{esc(title)}</title>{_SHARE_CSS}{brand_head}{head_extra}</head>"
            f"<body>{brand_bar}{inner}</body></html>")


# SCOPED CSP for the PUBLIC pdf.js viewer page ONLY. pdf.js needs a Web Worker
# ('worker-src 'self'') and may use wasm ('wasm-unsafe-eval'); it renders into <canvas>
# from blob:/data: image sources. This is set ON THE VIEWER RESPONSE ONLY — every other
# page (and the global _security_headers hook) keeps the strict global _CSP unchanged.
_SHARE_VIEWER_CSP = (
    "default-src 'self'; script-src 'self' 'wasm-unsafe-eval'; worker-src 'self'; "
    "img-src 'self' blob: data:; style-src 'self' 'unsafe-inline'; object-src 'none'; "
    "connect-src 'self'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'")


def _share_viewer_response(html):
    """Wrap the viewer HTML in a Response carrying the SCOPED viewer CSP. Because
    _security_headers uses setdefault, our explicit header wins and the relaxed policy
    applies to THIS response only."""
    resp = Response(html)
    resp.headers["Content-Security-Policy"] = _SHARE_VIEWER_CSP
    resp.headers["Cache-Control"] = "no-store"
    return resp


def _share_password_form(token, error=False, action=None):
    err = '<p class="err">Incorrect password.</p>' if error else ""
    action = action or f"/s/{token}"
    return _share_shell(
        "Protected document",
        '<div class="wrap"><div class="box"><h2>This document is password-protected</h2>'
        + err
        + f'<form method="post" action="{esc(action)}">'
          '<label>Password<input type="password" name="share_password" '
          'autocomplete="off" autofocus></label>'
          '<button>View document</button></form></div></div>')


def _share_email_form(token, action=None):
    action = action or f"/s/{token}"
    return _share_shell(
        "Enter your email",
        '<div class="wrap"><div class="box"><h2>Please enter your email to continue</h2>'
        f'<form method="post" action="{esc(action)}">'
        '<label>Email<input type="email" name="share_email" autofocus required></label>'
        '<button>View document</button></form></div></div>')


def _share_agreement_form(token, link, action=None):
    """The NDA / agreement gate page: renders the (escaped) agreement_text and an
    "I agree" form. POSTing share_agree records a logged acceptance and proceeds."""
    action = action or f"/s/{token}"
    text = (link.get("agreement_text")
            or "By continuing you agree that the contents of this document are "
               "confidential and may not be redistributed.")
    # preserve the author's line breaks but keep every byte escaped.
    body = esc(text).replace("\n", "<br>")
    return _share_shell(
        "Confidentiality agreement",
        '<div class="wrap"><div class="box"><h2>Please review and accept to continue</h2>'
        f'<div style="max-height:50vh;overflow:auto;white-space:pre-wrap;'
        f'background:#0e1726;border:1px solid #34405c;border-radius:6px;padding:12px;'
        f'margin:0 0 14px;line-height:1.5">{body}</div>'
        f'<form method="post" action="{esc(action)}">'
        '<button name="share_agree" value="1">I agree</button></form></div></div>')


def _share_not_found():
    """Enumeration-safe response for missing/revoked/expired (all look identical)."""
    return (_share_shell("Not available",
            '<div class="wrap"><div class="box"><h2>This link is not available</h2>'
            '<p class="note">The link may have been revoked, expired, or never existed.</p>'
            '</div></div>'), 410)


def _share_view_session(token):
    """A stable, per-browser-session id used to attribute B3 page-engagement beacons to a
    single visit. We REUSE the existing share session dict the gates already keep, so it
    persists across the viewer page + its beacons without a new cookie. Never raises."""
    try:
        vs = session.get("_share_vs", {})
        sid = vs.get(token)
        if not sid:
            sid = secrets.token_urlsafe(12)
            vs[token] = sid
            session["_share_vs"] = vs
        return sid
    except Exception as e:
        _log_exc("share: view session", e)
        return ""


# ---------------------------------------------------------------- ② SES signing step
def _share_signed_marker(token):
    """True iff THIS session has already signed the `require_signature` link `token`.
    Mirrors the password / NDA / email per-token session markers. Never raises."""
    try:
        return bool((session.get("_share_sign", {}) or {}).get(token))
    except Exception as e:
        _log_exc("share: signed marker", e)
        return False


def _set_share_signed(token):
    try:
        sess = session.get("_share_sign", {})
        sess[token] = True
        session["_share_sign"] = sess
    except Exception as e:
        _log_exc("share: set signed marker", e)


def _share_sign_page(token, link, error=""):
    """The SES signing page: embeds the document (same-origin iframe over /s/<token>/file,
    which the NDA/email gates already protect), shows the consent + a typed name field, an
    OPTIONAL drawn-signature <canvas> (vanilla JS in /static/esign_sign.js, posted as a data
    URL) and a Sign button. Clearly labelled SES — not a qualified electronic signature."""
    import esign
    file_url = f"/s/{esc(token)}/file"
    title = link.get("title") or link.get("doc_ref") or "Document to sign"
    err = (f'<p class="err">{esc(error)}</p>' if error else "")
    consent = esign.DEFAULT_CONSENT
    notice = esign.SES_NOTICE
    inner = (
        f'<div class="bar"><b>{esc(title)}</b> '
        '<span class="note" style="color:#9fb0c8">— please review and sign</span></div>'
        f'<iframe src="{file_url}" title="document"></iframe>'
        '<div class="wrap"><div class="box"><h2>Sign this document</h2>'
        + err
        + f'<p class="note" style="color:#9fb0c8">{esc(notice)}</p>'
        f'<form method="post" action="/s/{esc(token)}/sign" id="esign-form">'
        '<label>Your full name (typed signature)'
        '<input name="signer_name" autocomplete="name" required maxlength="200"></label>'
        '<label>Your email (optional)'
        '<input name="signer_email" type="email" autocomplete="email" maxlength="200"></label>'
        '<p class="note" style="color:#9fb0c8;margin:10px 0 4px">Draw your signature '
        '(optional):</p>'
        '<canvas id="esign-canvas" width="360" height="120" '
        'style="background:#fff;border:1px solid #34405c;border-radius:6px;'
        'touch-action:none;max-width:100%"></canvas>'
        '<div style="margin:4px 0 12px">'
        '<button type="button" id="esign-clear" '
        'style="background:#33415c">Clear drawing</button></div>'
        '<input type="hidden" name="signature_image" id="esign-image">'
        f'<label class="ck" style="display:flex;gap:8px;align-items:flex-start">'
        f'<input type="checkbox" name="consent" value="1" required '
        'style="width:auto;margin-top:3px">'
        f'<span>{esc(consent)}</span></label>'
        '<div style="margin-top:10px"><button>Sign</button></div>'
        '</form></div></div>')
    head_extra = '<script type="module" src="/static/esign_sign.js"></script>'
    return _share_shell(f"Sign — {title}", inner, head_extra)


@app.route("/s/<token>/sign", methods=["POST"])
def share_sign(token):
    """PUBLIC: record an SES signature for a `require_signature` share link. Re-runs the SAME
    per-token gates (revoked/expired/password/NDA/email) — NOTHING is signed before they pass.
    Reads the EXACT vault bytes presented for signing, binds their SHA-256, produces+vaults a
    signed PDF, attaches it back to the document request (item ①) when applicable, and notifies
    the owner. Enumeration-safe; never raises a 500 to the public."""
    import sharing, esign, document_vault, vat_refund as VR
    try:
        link = sharing.get_by_token(token)
        _bind_link_tenant(link)   # public route: scope to THIS link's tenant
        state, payload = _share_gate_or_form(token, link)
        if state == "deny":
            return _share_not_found()
        if state == "form":
            return payload   # a gate (password/NDA/email) is not yet satisfied
        email = payload
        if not link.get("require_signature"):
            return redirect(f"/s/{token}")
        if not link.get("signature_request_id"):
            return _share_viewer_response(_share_sign_page(
                token, link, "This link is not configured for signing."))
        req = esign.get_request(link["signature_request_id"])
        if not req or req.get("status") == "void":
            return _share_viewer_response(_share_sign_page(
                token, link, "This signing request is no longer available."))
        signer_name = (request.form.get("signer_name") or "").strip()
        if not request.form.get("consent"):
            return _share_viewer_response(_share_sign_page(
                token, link, "You must accept the consent statement to sign."))
        if not signer_name:
            return _share_viewer_response(_share_sign_page(
                token, link, "Please type your full name to sign."))
        try:
            data = document_vault.get_bytes(link["doc_ref"], VR.DOCDIR)
        except Exception as e:
            _log_exc("share: sign vault read", e)
            return _share_viewer_response(_share_sign_page(
                token, link, "The document could not be read for signing."))
        sig, err = esign.record_signature(
            link["signature_request_id"], signer_name, data,
            signer_email=(request.form.get("signer_email") or email or "").strip() or None,
            signature_image=(request.form.get("signature_image") or "").strip() or None,
            ip=request.remote_addr or "", user_agent=request.headers.get("User-Agent", ""),
            docdir=VR.DOCDIR, filename=(link.get("title") or None))
        if err or not sig:
            return _share_viewer_response(_share_sign_page(
                token, link, f"Could not record the signature: {err or 'unknown error'}"))
        _set_share_signed(token)
        _attach_signed_to_request(link.get("signature_request_id"), sig)
        _notify_sign_owner(link, signer_name)
        return redirect(f"/s/{token}")
    except Exception as e:
        _log_exc("share: public sign", e)
        return _share_not_found()


def _attach_signed_to_request(signature_request_id, sig):
    """If the signed share link's signature_request was created from a CRM document request
    (item ①), advance that request to 'signed' so the signed copy is reflected on the board.
    Best-effort; never raises."""
    try:
        import esign
        req = esign.get_request(signature_request_id)
        if not req:
            return
        sref = str(req.get("subject_ref") or "")
        if not sref.startswith("docreq:"):
            return
        try:
            doc_req_id = int(sref.split(":", 1)[1])
        except (TypeError, ValueError):
            return
        import customer_master as CM
        con = CM.connect()
        try:
            ok, msg = CM.advance_document_request(
                con, doc_req_id, "signed",
                note=f"E-signed (SES) by {sig.get('signer_name')}; "
                     f"signed copy: {sig.get('signed_locator') or '(stamping fell back)'}")
            if not ok:
                _log_exc("share: attach signed to docreq",
                         RuntimeError(f"advance failed: {msg}"))
        finally:
            con.close()
    except Exception as e:
        _log_exc("share: attach signed to request", e)


def _notify_sign_owner(link, signer_name):
    """Best-effort owner alert that a shared document was e-signed. Never raises."""
    try:
        import notify
        notify.send_alert(
            "Fleet Fuel & VAT — a shared document was e-signed (SES)",
            [f"'{link.get('title') or link.get('doc_ref')}' (shared by "
             f"{link.get('created_by') or 'unknown'}) was electronically signed by "
             f"{signer_name}.",
             f"Link: /s/{link.get('token')}"],
            recipients=_creator_recipients(link.get("created_by")))
    except Exception as e:
        _log_exc("share: sign owner notify", e)


@app.route("/s/<token>", methods=["GET", "POST"])
def share_public(token):
    """PUBLIC viewer for a share link. Runs the per-token gate, then renders the PDF with
    the self-hosted pdf.js viewer (page-by-page, /static/share_viewer.js fetching
    /s/<token>/file) and records ONE view. The viewer page carries a SCOPED CSP (pdf.js
    worker/wasm) — every other page keeps the strict global policy. No auth."""
    import sharing
    try:
        link = sharing.get_by_token(token)
        _bind_link_tenant(link)   # public route: scope to THIS link's tenant
        state, payload = _share_gate_or_form(token, link)
        if state == "deny":
            return _share_not_found()
        if state == "form":
            return payload
        email = payload
        # ② SES signing step — AFTER the NDA/email gates passed, BEFORE the document
        # viewer. An opt-in `require_signature` link shows a signing page (document +
        # consent + typed/drawn name) until the viewer has signed in THIS session.
        if link.get("require_signature") and not _share_signed_marker(token):
            return _share_viewer_response(_share_sign_page(token, link))
        # gates passed — record ONE view (dedup window guards refreshes); notify owner
        # on the FIRST genuine view only.
        try:
            if sharing.record_view(link, email, request.remote_addr or "",
                                   request.headers.get("User-Agent", "")):
                _notify_share_owner(link, email)
        except Exception as e:
            _log_exc("share: record view", e)
        _share_view_session(token)   # establish the per-visit id for beacons
        file_url = f"/s/{esc(token)}/file"
        title = link.get("title") or link.get("doc_ref") or "Shared document"
        # No inline JS: the viewer logic lives entirely in the static module, satisfying
        # script-src 'self'. The placeholder carries the token + file URL as data-attrs.
        inner = (f'<div class="bar"><b>{esc(title)}</b></div>'
                 f'<div id="pdf-root" data-token="{esc(token)}" '
                 f'data-file="{file_url}"></div>'
                 '<noscript><p style="color:#9fb0c8;text-align:center;padding:20px">'
                 'This viewer needs JavaScript. '
                 f'<a href="{file_url}" style="color:#7fb0ff">Download the document</a>.'
                 '</p></noscript>')
        head_extra = ('<script type="module" src="/static/share_viewer.js"></script>')
        return _share_viewer_response(_share_shell(title, inner, head_extra))
    except Exception as e:
        _log_exc("share: public viewer", e)
        return _share_not_found()


@app.route("/s/<token>/event", methods=["POST"])
def share_event(token):
    """PUBLIC per-page engagement beacon for the pdf.js viewer (B3). It MUST re-run the
    SAME per-token gates as the file route — it records NOTHING unless the viewer has
    passed every gate (revoked/expired/password/NDA/email). The body is a compact JSON
    {"pages": [{"page": n, "dwell_ms": ms}, ...]}; page/dwell are validated + clamped in
    sharing.record_page_view. Always returns 204 (enumeration-safe: a gated/blocked link
    looks identical to a successful no-op), and never raises to the caller."""
    import sharing
    try:
        link = sharing.get_by_token(token)
        _bind_link_tenant(link)   # public route: scope to THIS link's tenant
        # SAME gate as the file route — but with NO side effects: a GET-style re-check.
        # _share_gate_or_form on a POST would try to consume password/email fields, so we
        # only treat it as "ok" when the session already satisfies the gates (state==ok
        # without a form being returned). A pre-gate beacon records nothing.
        state, _payload = _share_gate_or_form(token, link)
        if state != "ok":
            return Response(status=204)
        data = request.get_json(silent=True) or {}
        pages = data.get("pages")
        if not isinstance(pages, list):
            return Response(status=204)
        sid = (session.get("_share_vs", {}) or {}).get(token) or ""
        recorded = 0
        for item in pages[:200]:    # cap the batch size — ignore absurd payloads
            if not isinstance(item, dict):
                continue
            if sharing.record_page_view(link, sid, item.get("page"),
                                        item.get("dwell_ms")):
                recorded += 1
        return Response(status=204)
    except Exception as e:
        _log_exc("share: page event beacon", e)
        return Response(status=204)


@app.route("/s/<token>/file")
def share_file(token):
    """Stream the vaulted PDF for a share link — ONLY after the SAME per-token gates pass
    (revoked/expired/password/email). 404/410 otherwise. Fetched strictly via
    document_vault by the stored doc_ref (no caller-controlled path). The response is
    served with SAMEORIGIN framing so the viewer iframe can embed it WITHOUT weakening
    the global DENY default for any other page."""
    import sharing, document_vault, vat_refund as VR
    try:
        link = sharing.get_by_token(token)
        _bind_link_tenant(link)   # public route: scope to THIS link's tenant
        state, email = _share_gate_or_form(token, link)
        if state != "ok":
            # missing/revoked/expired OR gates not yet satisfied -> do not serve bytes.
            return _share_not_found()
        try:
            data = document_vault.get_bytes(link["doc_ref"], VR.DOCDIR)
        except Exception as e:
            _log_exc("share: vault read", e)
            return _share_not_found()
        if link.get("watermark"):
            # Opt-in: overlay a faint, tiled, per-viewer watermark on every page. On ANY
            # failure fall back to the original bytes (never break the viewer) — the helper
            # returns None and logs via applog.
            try:
                import share_watermark
                who = (email or session.get("_share_em", {}).get(token)
                       or request.remote_addr or "viewer")
                stamp = _dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
                marked = share_watermark.apply_watermark(
                    data, f"CONFIDENTIAL   {who}   {stamp}")
                if marked:
                    data = marked
            except Exception as e:
                _log_exc("share: watermark", e)
        resp = Response(data, mimetype="application/pdf")
        resp.headers["Content-Disposition"] = "inline"
        # Per-RESPONSE relaxation to SAME-ORIGIN framing (not weaker) so the viewer
        # iframe embeds it; every OTHER page keeps the global DENY / frame-ancestors
        # 'none'. object-src 'none' is irrelevant here (we use <iframe>, not <embed>).
        resp.headers["X-Frame-Options"] = "SAMEORIGIN"
        resp.headers["Content-Security-Policy"] = "frame-ancestors 'self'"
        resp.headers["Cache-Control"] = "no-store"
        return resp
    except Exception as e:
        _log_exc("share: file stream", e)
        return _share_not_found()


def _creator_recipients(created_by):
    """C3②: the per-owner alert target — the link CREATOR's own contact email if set,
    else None (so notify.send_alert falls back to the team relay). Best-effort; a lookup
    failure returns None so alerts are never broken. Never raises."""
    try:
        return _auth.user_email(created_by) or None
    except Exception as e:
        _log_exc("share: creator recipient lookup", e)
        return None


def _notify_share_owner(link, email):
    """Best-effort: alert that a shared document was viewed. C3②: the alert is targeted at
    the link CREATOR's own email when set, falling back to the team notify relay otherwise.
    A no-op when neither recipient nor SMTP is configured. Never raises."""
    try:
        import notify
        who = email or "an anonymous visitor"
        notify.send_alert(
            "Fleet Fuel & VAT — a shared document was viewed",
            [f"'{link.get('title') or link.get('doc_ref')}' (shared by "
             f"{link.get('created_by') or 'unknown'}) was viewed by {who}.",
             f"Link: /s/{link.get('token')}"],
            recipients=_creator_recipients(link.get("created_by")))
    except Exception as e:
        _log_exc("share: owner notify", e)


# ---------------------------------------------------------------- data rooms (B4)
# A data room groups several vaulted documents into one branded, access-controlled
# space behind a SINGLE gated link. It builds ON the B1-B3 sharing infrastructure: the
# AUTHED management surface is capability-gated ('share', MODULES key 'sharing'); the
# PUBLIC room (/r/<token>...) reuses the SAME per-token gate (_share_gate_or_form), the
# SAME scoped-CSP pdf.js viewer (_share_viewer_response + static/share_viewer.js) and
# the SAME page-engagement beacon machinery as a B3 share link. See sharing.py.

def _room_doc_page_count(doc_ref):
    """Best-effort total page count of a vaulted PDF (for the room completion %).
    Returns an int or None. Never raises."""
    try:
        import document_vault, vat_refund as VR, io
        data = document_vault.get_bytes(doc_ref, VR.DOCDIR)
        from pypdf import PdfReader
        return len(PdfReader(io.BytesIO(data)).pages)
    except Exception as e:
        _log_exc("dataroom: page count", e)
        return None


@app.route("/rooms", methods=["GET", "POST"])
def rooms_page():
    """List the current user's data rooms and create a new one."""
    import sharing
    actor = session.get("user", "")
    banner = ""
    if request.method == "POST":
        room, err = sharing.create_room(request.form.get("name", ""),
                                        request.form.get("title", ""), actor)
        banner = (f'<div class="card" style="border-left:4px solid var(--ok)">'
                  f'<b class="ok">Data room created.</b> '
                  f'<a href="/rooms/{room["id"]}">Open it</a> to add documents.</div>'
                  if room else
                  f'<div class="card" style="border-left:4px solid var(--bad)">'
                  f'<b class="bad">Could not create the room.</b> {esc(err)}</div>')
    try:
        rooms = sharing.list_rooms(actor)
    except Exception as e:
        _log_exc("dataroom: list rooms", e)
        rooms = []
    rows = []
    for r in rooms:
        rows.append([
            f'<a href="/rooms/{r["id"]}">{esc(r.get("name") or "(unnamed)")}</a>',
            esc(r.get("title") or "—"),
            esc(r.get("documents", 0)),
            esc(r.get("created_at") or ""),
        ])
    table = (tbl(["Name", "Title / branding", "Documents", "Created"], rows) if rows
             else '<p class="note">No data rooms yet.</p>')
    create_form = (
        '<div class="card"><h2>Create a data room</h2>'
        '<p class="note">Group several vaulted documents into one branded, '
        'access-controlled space behind a single shareable link.</p>'
        '<form method="post" action="/rooms" class="f">' + _csrf_input()
        + '<label>Room name<input name="name" required '
          'placeholder="e.g. Q2 due-diligence pack"></label>'
          '<label>Title / branding (shown on the public room; optional)'
          '<input name="title" placeholder="optional"></label>'
          '<div style="margin-top:8px"><button>Create room</button></div>'
          '</form></div>')
    return page(banner + create_form
                + f'<div class="card"><h2>Your data rooms</h2>{table}</div>', "rooms")


@app.route("/rooms/<int:room_id>", methods=["GET", "POST"])
def room_page(room_id):
    """Manage a single room: add vaulted documents (folder + order), mint a gated room
    link. Q&A and engagement live on their own sub-pages."""
    import sharing
    actor = session.get("user", "")
    room = sharing.get_room(room_id)
    if not room or room.get("created_by") != actor:
        return page('<div class="card"><b class="bad">No such room.</b></div>',
                    "rooms"), 404
    banner = ""
    if request.method == "POST":
        act = request.form.get("__act")
        if act == "add_doc":
            doc_ref = (request.form.get("doc_ref_manual")
                       or request.form.get("doc_ref") or "").strip()
            so = request.form.get("sort_order")
            try:
                so = int(so) if (so or "").strip() else None
            except (TypeError, ValueError):
                so = None
            _doc, err = sharing.add_document(
                room_id, doc_ref, title=request.form.get("title", ""),
                folder=request.form.get("folder", ""), sort_order=so)
            banner = ('<div class="card" style="border-left:4px solid var(--ok)">'
                      '<b class="ok">Document added.</b></div>' if not err else
                      f'<div class="card" style="border-left:4px solid var(--bad)">'
                      f'<b class="bad">Could not add document.</b> {esc(err)}</div>')
        elif act == "create_link":
            # C3①: an optional per-link document allow-list. Unchecked = expose ALL docs.
            allow_ids = []
            for v in request.form.getlist("allow_doc"):
                try:
                    allow_ids.append(int(v))
                except (TypeError, ValueError):
                    continue
            link, err = sharing.create_room_link(
                room_id, actor,
                expires_at=(request.form.get("expires_at") or "").strip() or None,
                password=(request.form.get("password") or "") or None,
                require_email=bool(request.form.get("require_email")),
                nda_required=bool(request.form.get("nda_required")),
                agreement_text=(request.form.get("agreement_text") or "") or None,
                watermark=bool(request.form.get("watermark")),
                allow_doc_ids=allow_ids or None)
            if link:
                url = f"/r/{link['token']}"
                banner = ('<div class="card" style="border-left:4px solid var(--ok)">'
                          '<b class="ok">Room link created.</b>'
                          f'<p>Public link: <a href="{esc(url)}">{esc(url)}</a></p></div>')
            else:
                banner = ('<div class="card" style="border-left:4px solid var(--bad)">'
                          f'<b class="bad">Could not create the link.</b> {esc(err)}</div>')
        elif act == "revoke_link":
            try:
                rlid = int(request.form.get("room_link_id", "0"))
            except (TypeError, ValueError):
                rlid = 0
            rl = next((x for x in sharing.list_room_links(room_id) if x["id"] == rlid),
                      None)
            if rl:
                sharing.revoke_room_link(rlid)
                banner = ('<div class="card" style="border-left:4px solid var(--ok)">'
                          '<b class="ok">Link revoked.</b></div>')

    docs = sharing.list_documents(room_id)
    drows = []
    for d in docs:
        drows.append([esc(d.get("folder") or "—"), esc(d.get("sort_order")),
                      esc(d.get("title") or d.get("doc_ref")), esc(d.get("doc_ref"))])
    doc_table = (tbl(["Folder", "Order", "Title", "Vault ref"], drows) if drows
                 else '<p class="note">No documents in this room yet.</p>')

    links = sharing.list_room_links(room_id)
    lrows = []
    for l in links:
        url = f"/r/{l['token']}"
        gates = []
        if l.get("password_hash"):
            gates.append("password")
        if l.get("require_email"):
            gates.append("email")
        if l.get("nda_required"):
            gates.append("NDA")
        if l.get("watermark"):
            gates.append("watermark")
        if l.get("expires_at"):
            gates.append(f"expires {esc(l['expires_at'])}")
        state = ('<b class="bad">revoked</b>' if l.get("revoked")
                 else '<b class="bad">expired</b>' if sharing.is_expired(l)
                 else '<b class="ok">active</b>')
        # C3①: 0 = no allow-list = exposes ALL room documents; >0 = restricted subset.
        n_allowed = l.get("allowed_docs", 0)
        docs_cell = (f'<b>{esc(n_allowed)}</b> selected' if n_allowed
                     else 'all documents')
        revoke_btn = ""
        if not l.get("revoked"):
            revoke_btn = ('<form method="post" style="display:inline">' + _csrf_input()
                          + f'<input type="hidden" name="room_link_id" value="{l["id"]}">'
                          + '<button name="__act" value="revoke_link" '
                          'onclick="return confirm(\'Revoke this link?\')">Revoke</button>'
                          '</form>')
        lrows.append([f'<a href="{esc(url)}">{esc(url)}</a>',
                      docs_cell, (", ".join(gates) or "—"), state,
                      esc(l.get("created_at") or ""), revoke_btn])
    link_table = (tbl(["Public link", "Documents", "Gates", "State", "Created", ""], lrows)
                  if lrows else '<p class="note">No room links yet.</p>')

    add_form = (
        '<div class="card"><h2>Add a vaulted document</h2>'
        '<form method="post" class="f">' + _csrf_input()
        + '<label>Document<select name="doc_ref">'
        + "".join(f'<option value="{esc(dr)}">{esc(lbl)}</option>'
                  for dr, lbl in _vault_doc_choices())
        + '</select></label>'
          '<label>…or paste a vault reference<input name="doc_ref_manual" '
          'placeholder="leave blank to use the pick-list above"></label>'
          '<label>Title (shown in the room; optional)<input name="title"></label>'
          '<label>Folder (optional grouping)<input name="folder" '
          'placeholder="e.g. Contracts"></label>'
          '<label>Order within folder (optional)<input name="sort_order" '
          'type="number"></label>'
          '<div style="margin-top:8px">'
          '<button name="__act" value="add_doc">Add document</button></div>'
          '</form></div>')
    # C3①: the per-link document picker — leave ALL unticked to expose the whole room.
    doc_picker = "".join(
        f'<label class="ck"><input type="checkbox" name="allow_doc" '
        f'value="{esc(d["id"])}"> {esc(d.get("title") or d.get("doc_ref"))}'
        + (f' <span class="note">({esc(d["folder"])})</span>' if d.get("folder") else '')
        + '</label>'
        for d in docs)
    doc_picker_block = (
        '<fieldset style="border:1px solid #34405c;border-radius:6px;padding:8px 10px;'
        'margin:6px 0"><legend class="note">Documents this link exposes '
        '(leave all unticked to expose the whole room)</legend>'
        + (doc_picker or '<span class="note">Add a document to the room first.</span>')
        + '</fieldset>') if docs else ''
    link_form = (
        '<div class="card"><h2>Create a gated room link</h2>'
        '<p class="note">One shareable PUBLIC link to the whole room. The same gate '
        'options as a share link apply to every document in the room. Optionally restrict '
        'a link to a SUBSET of the room\'s documents (per-recipient permissions).</p>'
        '<form method="post" class="f">' + _csrf_input()
        + '<label>Expires (UTC, optional)<input name="expires_at" '
          'placeholder="YYYY-MM-DD or YYYY-MM-DD HH:MM"></label>'
          '<label>Password (optional)<input name="password" type="password" '
          'autocomplete="new-password"></label>'
          '<label class="ck"><input type="checkbox" name="require_email" value="1"> '
          'Require the viewer to enter an email first</label>'
          '<label class="ck"><input type="checkbox" name="nda_required" value="1"> '
          'Require the viewer to accept an agreement (NDA) first — logged</label>'
          '<label>Agreement text (shown on the NDA gate; optional)'
          '<textarea name="agreement_text" rows="3"></textarea></label>'
          '<label class="ck"><input type="checkbox" name="watermark" value="1"> '
          'Watermark every page with the viewer + timestamp</label>'
        + doc_picker_block
        + '<div style="margin-top:8px">'
          '<button name="__act" value="create_link">Create room link</button></div>'
          '</form></div>')

    nav = (f'<p><a href="/rooms/{room_id}/qa">Q&amp;A</a> · '
           f'<a href="/rooms/{room_id}/engagement">Engagement</a> · '
           f'<a href="/rooms">All rooms</a></p>')
    head = (f'<div class="card"><h2>{esc(room.get("name") or "(unnamed)")}</h2>'
            f'<p class="note">{esc(room.get("title") or "")}</p>{nav}</div>')
    return page(banner + head
                + f'<div class="card"><h2>Documents</h2>{doc_table}</div>' + add_form
                + f'<div class="card"><h2>Room links</h2>{link_table}</div>' + link_form,
                "rooms")


@app.route("/rooms/<int:room_id>/qa", methods=["GET", "POST"])
def room_qa_page(room_id):
    """Q&A management: review viewer questions and answer them (status flips to
    'answered')."""
    import sharing
    actor = session.get("user", "")
    room = sharing.get_room(room_id)
    if not room or room.get("created_by") != actor:
        return page('<div class="card"><b class="bad">No such room.</b></div>',
                    "rooms"), 404
    banner = ""
    if request.method == "POST":
        try:
            qid = int(request.form.get("question_id", "0"))
        except (TypeError, ValueError):
            qid = 0
        q = sharing.get_question(qid)
        if q and q.get("room_id") == room_id:
            ok, err = sharing.answer_question(qid, request.form.get("answer", ""))
            banner = ('<div class="card" style="border-left:4px solid var(--ok)">'
                      '<b class="ok">Answer saved.</b></div>' if ok else
                      f'<div class="card" style="border-left:4px solid var(--bad)">'
                      f'<b class="bad">Could not save.</b> {esc(err)}</div>')
        else:
            banner = ('<div class="card" style="border-left:4px solid var(--bad)">'
                      '<b class="bad">No such question.</b></div>')
    blocks = []
    for q in sharing.questions_for(room_id):
        ans = (f'<p><b>Answer:</b> {esc(q.get("answer"))}</p>'
               if q.get("status") == "answered" else
               '<form method="post" class="f">' + _csrf_input()
               + f'<input type="hidden" name="question_id" value="{q["id"]}">'
               + '<label>Answer<textarea name="answer" rows="2" required></textarea></label>'
               + '<div><button>Answer</button></div></form>')
        blocks.append(
            f'<div style="margin:0 0 16px;padding:0 0 12px;border-bottom:1px solid #eee">'
            f'<p class="note">{esc(q.get("created_at") or "")} · '
            f'{esc(q.get("viewer_email") or "anonymous")} · '
            f'<b>{esc(q.get("status"))}</b></p>'
            f'<p><b>Q:</b> {esc(q.get("question"))}</p>{ans}</div>')
    body = ("".join(blocks) if blocks
            else '<p class="note">No questions asked yet.</p>')
    nav = f'<p><a href="/rooms/{room_id}">Back to room</a></p>'
    return page(banner + f'<div class="card"><h2>Q&amp;A — '
                f'{esc(room.get("name") or "")}</h2>{body}</div>' + nav, "rooms")


@app.route("/rooms/<int:room_id>/engagement")
def room_engagement_page(room_id):
    """Per-document page-by-page engagement for a room (reuses the B3 analytics shape)."""
    import sharing
    actor = session.get("user", "")
    room = sharing.get_room(room_id)
    if not room or room.get("created_by") != actor:
        return page('<div class="card"><b class="bad">No such room.</b></div>',
                    "rooms"), 404
    blocks = []
    try:
        docs = sharing.list_documents(room_id)
        pages_by_doc = {d["id"]: _room_doc_page_count(d["doc_ref"]) for d in docs}
        for e in sharing.room_engagement(room_id, pages_by_doc):
            comp = e.get("completion_pct")
            denom = e.get("total_pages") or (max((p["page"] for p in e["pages"]),
                                                 default=0) or None)
            if comp is None and denom:
                comp = round(min(e["pages_viewed"], denom) / denom * 100, 1)
            summary = (f'<p>Pages viewed: <b>{esc(e["pages_viewed"])}</b>'
                       + (f' of {esc(denom)}' if denom else '')
                       + (f' &nbsp;·&nbsp; Completion: <b>{esc(comp)}%</b>'
                          if comp is not None else '')
                       + f' &nbsp;·&nbsp; Total time: '
                         f'<b>{esc(_fmt_dwell(e["total_ms"]))}</b>'
                         f' &nbsp;·&nbsp; Visitors: <b>{esc(e["visitors"])}</b></p>')
            if e["pages"]:
                prows = [[esc(p["page"]), esc(_fmt_dwell(p["dwell_ms"])),
                          esc(p["sessions"])] for p in e["pages"]]
                ptab = tbl(["Page", "Total time", "Visitors"], prows)
            else:
                ptab = '<p class="note">No page-by-page engagement recorded yet.</p>'
            blocks.append(
                f'<div class="card"><h3>{esc(e.get("title") or e.get("doc_ref"))}</h3>'
                + (f'<p class="note">Folder: {esc(e["folder"])}</p>'
                   if e.get("folder") else '')
                + summary + ptab + '</div>')
    except Exception as ex:
        _log_exc("dataroom: engagement page", ex)
        blocks = ['<p class="bad">Could not load engagement.</p>']
    nav = f'<p><a href="/rooms/{room_id}">Back to room</a></p>'
    return page(f'<div class="card"><h2>Engagement — {esc(room.get("name") or "")}</h2>'
                '<p class="note">Per-document viewer engagement (not pricing).</p></div>'
                + "".join(blocks) + nav, "rooms")


# ---- PUBLIC room (NO auth by design; the room link's gate runs in-view, per-token) ----
def _room_gate(token, room_link, action):
    """Run the per-token room-link gate, injecting the room-scoped agreement logger so an
    NDA acceptance lands in dataroom_agreements (never share_agreements). Returns the same
    (state, payload) tri-state as _share_gate_or_form."""
    import sharing
    return _share_gate_or_form(token, room_link, action=action,
                               record_agreement=sharing.record_room_agreement)


@app.route("/r/<token>", methods=["GET", "POST"])
def room_public(token):
    """PUBLIC branded index of a data room — gated EXACTLY like a share link. Lists the
    room's documents grouped by folder (each linking to the in-room viewer) and offers a
    Q&A 'ask a question' form. No auth."""
    import sharing
    try:
        room_link = sharing.get_room_link_by_token(token)
        _bind_link_tenant(room_link)   # public route: scope to THIS link's tenant
        action = f"/r/{token}"
        state, payload = _room_gate(token, room_link, action)
        if state == "deny":
            return _share_not_found()
        if state == "form":
            return payload
        room = sharing.get_room(room_link["room_id"])
        if not room or room.get("archived"):
            return _share_not_found()
        _share_view_session(token)   # establish the per-visit id for beacons
        # C3①: only the documents this LINK is permitted to expose (an empty allow-list
        # = the whole room — backward compatible).
        docs = sharing.list_permitted_documents(room_link)
        # group by folder, preserving list_documents' folder/sort order.
        groups = {}
        for d in docs:
            groups.setdefault(d.get("folder") or "", []).append(d)
        sections = []
        for folder, items in groups.items():
            lis = []
            for d in items:
                url = f"/r/{esc(token)}/doc/{d['id']}"
                lis.append(f'<li><a href="{url}" style="color:#7fb0ff">'
                           f'{esc(d.get("title") or d.get("doc_ref"))}</a></li>')
            head = (f'<h3 style="color:#9fb0c8">{esc(folder)}</h3>' if folder else "")
            sections.append(head + '<ul>' + "".join(lis) + '</ul>')
        docs_html = ("".join(sections) if sections
                     else '<p class="note">This room has no documents yet.</p>')
        ask = (f'<form method="post" action="/r/{esc(token)}/ask" '
               'style="margin-top:24px">'
               '<h3 style="color:#9fb0c8">Ask a question</h3>'
               '<input type="email" name="email" placeholder="Your email (optional)">'
               '<textarea name="question" placeholder="Your question" '
               'style="width:100%;box-sizing:border-box;min-height:70px;padding:9px;'
               'border-radius:6px;border:1px solid #34405c;background:#0e1726;'
               'color:#e6edf3" required></textarea>'
               '<button>Send question</button></form>')
        title = room.get("title") or room.get("name") or "Data room"
        inner = (f'<div class="wrap"><div class="box"><h2>{esc(title)}</h2>'
                 + docs_html + ask + '</div></div>')
        resp = Response(_share_shell(title, inner))
        resp.headers["Cache-Control"] = "no-store"
        return resp
    except Exception as e:
        _log_exc("dataroom: public index", e)
        return _share_not_found()


@app.route("/r/<token>/doc/<int:doc_id>", methods=["GET", "POST"])
def room_doc_viewer(token, doc_id):
    """PUBLIC pdf.js viewer for ONE room document — gated by the room link, scoped-CSP,
    reusing static/share_viewer.js. The doc MUST belong to the room (else 404)."""
    import sharing
    try:
        room_link = sharing.get_room_link_by_token(token)
        _bind_link_tenant(room_link)   # public route: scope to THIS link's tenant
        action = f"/r/{token}/doc/{doc_id}"
        state, _payload = _room_gate(token, room_link, action)
        if state == "deny":
            return _share_not_found()
        if state == "form":
            return _payload
        # C3①: the doc must belong to the room AND be permitted by this link's allow-list.
        doc = sharing.get_permitted_document(room_link, doc_id)
        if not doc:
            return _share_not_found()   # not in this room OR not permitted by this link
        _share_view_session(token)
        file_url = f"/r/{esc(token)}/doc/{doc_id}/file"
        event_url = f"/r/{esc(token)}/doc/{doc_id}/event"
        title = doc.get("title") or doc.get("doc_ref") or "Document"
        inner = (f'<div class="bar"><b>{esc(title)}</b> '
                 f'<a href="/r/{esc(token)}" style="color:#7fb0ff;float:right">'
                 '← Room index</a></div>'
                 f'<div id="pdf-root" data-token="{esc(token)}" '
                 f'data-file="{file_url}" data-event="{event_url}"></div>'
                 '<noscript><p style="color:#9fb0c8;text-align:center;padding:20px">'
                 'This viewer needs JavaScript. '
                 f'<a href="{file_url}" style="color:#7fb0ff">Download the document</a>.'
                 '</p></noscript>')
        head_extra = '<script type="module" src="/static/share_viewer.js"></script>'
        return _share_viewer_response(_share_shell(title, inner, head_extra))
    except Exception as e:
        _log_exc("dataroom: doc viewer", e)
        return _share_not_found()


@app.route("/r/<token>/doc/<int:doc_id>/file")
def room_doc_file(token, doc_id):
    """Stream a room document's vaulted bytes — ONLY after the room-link gates pass and
    ONLY if the doc belongs to the room. 404/410 otherwise. Watermark applied if the room
    link sets it."""
    import sharing, document_vault, vat_refund as VR
    try:
        room_link = sharing.get_room_link_by_token(token)
        _bind_link_tenant(room_link)   # public route: scope to THIS link's tenant
        action = f"/r/{token}/doc/{doc_id}/file"
        state, email = _room_gate(token, room_link, action)
        if state != "ok":
            return _share_not_found()
        # C3①: room membership AND this link's per-link allow-list must both permit it.
        doc = sharing.get_permitted_document(room_link, doc_id)
        if not doc:
            return _share_not_found()
        try:
            data = document_vault.get_bytes(doc["doc_ref"], VR.DOCDIR)
        except Exception as e:
            _log_exc("dataroom: vault read", e)
            return _share_not_found()
        if room_link.get("watermark"):
            try:
                import share_watermark
                who = (email or session.get("_share_em", {}).get(token)
                       or request.remote_addr or "viewer")
                stamp = _dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
                marked = share_watermark.apply_watermark(
                    data, f"CONFIDENTIAL   {who}   {stamp}")
                if marked:
                    data = marked
            except Exception as e:
                _log_exc("dataroom: watermark", e)
        resp = Response(data, mimetype="application/pdf")
        resp.headers["Content-Disposition"] = "inline"
        resp.headers["X-Frame-Options"] = "SAMEORIGIN"
        resp.headers["Content-Security-Policy"] = "frame-ancestors 'self'"
        resp.headers["Cache-Control"] = "no-store"
        return resp
    except Exception as e:
        _log_exc("dataroom: file stream", e)
        return _share_not_found()


@app.route("/r/<token>/doc/<int:doc_id>/event", methods=["POST"])
def room_doc_event(token, doc_id):
    """PUBLIC per-page engagement beacon for a room document — re-runs the SAME room-link
    gate as the file route and records NOTHING unless every gate passes and the doc is in
    the room. Always 204 (enumeration-safe). Never raises."""
    import sharing
    try:
        room_link = sharing.get_room_link_by_token(token)
        _bind_link_tenant(room_link)   # public route: scope to THIS link's tenant
        state, _payload = _room_gate(token, room_link, f"/r/{token}/doc/{doc_id}/event")
        if state != "ok":
            return Response(status=204)
        # C3①: record nothing for a doc this link is not permitted to expose.
        doc = sharing.get_permitted_document(room_link, doc_id)
        if not doc:
            return Response(status=204)
        data = request.get_json(silent=True) or {}
        pages = data.get("pages")
        if not isinstance(pages, list):
            return Response(status=204)
        sid = (session.get("_share_vs", {}) or {}).get(token) or ""
        for item in pages[:200]:
            if not isinstance(item, dict):
                continue
            sharing.record_room_page_view(room_link, doc_id, sid,
                                          item.get("page"), item.get("dwell_ms"))
        return Response(status=204)
    except Exception as e:
        _log_exc("dataroom: page event beacon", e)
        return Response(status=204)


@app.route("/r/<token>/ask", methods=["POST"])
def room_ask(token):
    """PUBLIC Q&A: store a viewer question against the room and notify the owner. Gated
    by the room link exactly like the file/event routes; records NOTHING pre-gate. Always
    204 (enumeration-safe). Never raises."""
    import sharing
    try:
        room_link = sharing.get_room_link_by_token(token)
        _bind_link_tenant(room_link)   # public route: scope to THIS link's tenant
        state, payload = _room_gate(token, room_link, f"/r/{token}/ask")
        if state != "ok":
            return Response(status=204)
        room = sharing.get_room(room_link["room_id"])
        if not room:
            return Response(status=204)
        question = (request.form.get("question") or "").strip()
        if not question:
            return Response(status=204)
        email = ((request.form.get("email") or "").strip()
                 or session.get("_share_em", {}).get(token) or "")
        q, _err = sharing.ask_question(room["id"], email, question)
        if q:
            _notify_room_question(room, q)
        return redirect(f"/r/{token}")
    except Exception as e:
        _log_exc("dataroom: ask", e)
        return Response(status=204)


def _notify_room_question(room, question):
    """Best-effort: alert that a data-room question was asked. C3②: targeted at the room
    OWNER's own email when set, falling back to the team notify relay otherwise. A no-op
    when neither recipient nor SMTP is configured. Never raises."""
    try:
        import notify
        who = question.get("viewer_email") or "an anonymous visitor"
        notify.send_alert(
            "Fleet Fuel & VAT — a data-room question was asked",
            [f"Room '{room.get('name')}' (owner {room.get('created_by') or 'unknown'}) "
             f"received a question from {who}.",
             f"Q: {question.get('question')}",
             f"Answer it on: /rooms/{room.get('id')}/qa"],
            recipients=_creator_recipients(room.get("created_by")))
    except Exception as e:
        _log_exc("dataroom: question notify", e)


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
# The versioned, TOKEN-ONLY external contract (see docs/MANUAL.md#external-api-apiv1-token-contract). Auth + scope are
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
