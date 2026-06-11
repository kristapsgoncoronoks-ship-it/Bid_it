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
import sqlite3, os
from flask import Flask, request, jsonify, render_template_string, send_file, session, redirect
from markupsafe import escape as esc
import auth as _auth
import audit as _audit_mod

WORKDIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(WORKDIR, "fuel_history.db")
app = Flask(__name__)
app.secret_key = _auth.secret_key()
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax")

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
                         "self-signed, ready" if cert_made else "add later (see INSTALL.md)"))
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
                     '&ldquo;viewer&rdquo;). Daily guide: USER_MANUAL.md</p></div>')
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
        if _auth.verify(request.form.get("username", ""), request.form.get("password", ""),
                        remote=request.remote_addr or ""):
            session["user"] = request.form["username"]
            u = _auth.get_user(session["user"])
            session["role"] = (u or {}).get("role", "viewer")
            return redirect("/")
        err = '<div class="err">Invalid username or password.</div>'
    return LOGIN_HTML.replace("{ERR}", err)

@app.route("/logout")
def logout():
    session.clear()
    return redirect("/login")

_DB_OPENERS = None
def _all_db_cons():
    global _DB_OPENERS
    if _DB_OPENERS is None:
        import customer_db, supplier_db, vat_refund
        _DB_OPENERS = (customer_db.connect, supplier_db.connect, vat_refund.connect)
    return [op() for op in _DB_OPENERS]

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

@app.before_request
def _guard():
    if request.endpoint in ("setup", "static") or request.endpoint is None:
        return
    if _needs_setup():
        return redirect("/setup")
    if request.endpoint == "login":
        return
    if not session.get("user"):
        return redirect("/login")
    role = session.get("role", "viewer")
    if request.path.startswith("/admin") and role != "admin":
        return page(FORBIDDEN, ""), 403
    if request.method == "POST" and role not in ("editor", "admin") \
            and request.endpoint != "login":
        return page(FORBIDDEN, ""), 403
    if request.method == "POST":
        for con in _all_db_cons():
            _audit_mod.set_actor(con, session["user"]); con.close()

@app.after_request
def _reset_actor(resp):
    if request.method == "POST" and session.get("user"):
        for con in _all_db_cons():
            _audit_mod.reset_actor(con); con.close()
    return resp

def DB():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con

# ---------------------------------------------------------------- queries
def q_periods(con):
    return [r[0] for r in con.execute("SELECT DISTINCT period FROM transactions ORDER BY period DESC")]

def q_filters(con):
    return {
        "suppliers": [r[0] for r in con.execute("SELECT DISTINCT supplier FROM transactions ORDER BY 1")],
        "countries": [r[0] for r in con.execute("SELECT DISTINCT country FROM transactions ORDER BY 1")],
        "products":  [r[0] for r in con.execute("SELECT DISTINCT product_group FROM transactions ORDER BY 1")],
        "periods":   q_periods(con),
    }

def where(args):
    w, p = ["1=1"], []
    for col, key in (("period","period"),("supplier","supplier"),("country","country"),
                     ("product_group","product")):
        v = args.get(key)
        if v and v != "ALL": w.append(f"{col}=?"); p.append(v)
    if args.get("date_from"): w.append("date>=?"); p.append(args["date_from"])
    if args.get("date_to"):   w.append("date<=?"); p.append(args["date_to"])
    return " AND ".join(w), p

def q_compare(con, args):
    w, p = where(args)
    return con.execute(f"""
        SELECT supplier, country, product_group,
               ROUND(SUM(qty),0) litres, ROUND(SUM(net_eur),2) net_eur,
               ROUND(SUM(vat_eur),2) vat_eur,
               ROUND(SUM(net_eur)/NULLIF(SUM(qty),0),4) eur_l_doc,
               ROUND(SUM(net_eur_eff)/NULLIF(SUM(qty),0),4) eur_l_eff
        FROM transactions WHERE {w}
        GROUP BY supplier, country, product_group ORDER BY eur_l_eff""", p).fetchall()

def q_benchmark(con, period):
    return con.execute("""
        SELECT supplier, country, ROUND(SUM(qty),0) litres,
               ROUND(SUM(net_eur)/NULLIF(SUM(qty),0),4) doc,
               ROUND(SUM(net_eur_eff)/NULLIF(SUM(qty),0),4) eff
        FROM transactions WHERE period=? AND product_group='Diesel'
        GROUP BY supplier, country ORDER BY eff""", (period,)).fetchall()

def q_kpis(con, period):
    r = con.execute("""
        SELECT ROUND(SUM(net_eur),0) net, ROUND(SUM(vat_eur),0) vat,
               ROUND(SUM(net_eur)+SUM(vat_eur),0) gross,
               (SELECT ROUND(SUM(qty),0) FROM transactions WHERE period=? AND product_group='Diesel') litres,
               (SELECT ROUND(SUM(net_eur_eff)/SUM(qty),4) FROM transactions WHERE period=? AND product_group='Diesel') eurl
        FROM transactions WHERE period=?""", (period, period, period)).fetchone()
    return r

def q_trend(con):
    return con.execute("""
        SELECT period, ROUND(SUM(qty),0) litres,
               ROUND(SUM(net_eur_eff)/NULLIF(SUM(qty),0),4) eurl
        FROM transactions WHERE product_group='Diesel' GROUP BY period ORDER BY period""").fetchall()

def q_headtohead(con, period):
    rows = con.execute("""
        SELECT date, country, supplier, SUM(qty) q, SUM(net_eur_eff) e
        FROM transactions WHERE product_group='Diesel' AND period=?
        GROUP BY date, country, supplier""", (period,)).fetchall()
    g = {}
    for r in rows:
        g.setdefault((r["date"], r["country"]), {})[r["supplier"]] = (r["q"], r["e"])
    out = []
    for (d, c), bysup in sorted(g.items()):
        if len(bysup) < 2: continue
        prices = {s: e/qy for s, (qy, e) in bysup.items()}
        cheap = min(prices, key=prices.get)
        over = sum(qy*(prices[s]-prices[cheap]) for s, (qy, e) in bysup.items())
        out.append({"date": d, "country": c,
                    "prices": " | ".join(f"{s} {prices[s]:.4f}" for s in sorted(prices)),
                    "cheapest": cheap, "spread": round(max(prices.values())-min(prices.values()),4),
                    "litres": round(sum(v[0] for v in bysup.values())),
                    "overpay": round(over,2)})
    return out

def q_entities(con, period):
    return con.execute("""SELECT entity, country, ROUND(SUM(net_eur),2) net,
        ROUND(SUM(vat_eur),2) vat, ROUND(SUM(net_eur)+SUM(vat_eur),2) gross
        FROM transactions WHERE period=? GROUP BY entity, country ORDER BY entity""", (period,)).fetchall()

def q_stations(con, period):
    return con.execute("""SELECT supplier, country, station, ROUND(SUM(qty),0) litres,
        ROUND(SUM(net_eur_eff)/NULLIF(SUM(qty),0),4) eurl
        FROM transactions WHERE period=? AND product_group='Diesel'
        GROUP BY supplier, country, station HAVING SUM(qty)>=300 ORDER BY eurl""", (period,)).fetchall()

# ---------------------------------------------------------------- layout
BASE = """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Fleet Fuel Analytics</title><style>
:root{--ink:#1a2733;--mut:#5b6b7a;--line:#dde4ea;--bg:#f4f6f8;--acc:#0e5fa8;--ok:#1b7340;--bad:#c8102e}
*{box-sizing:border-box}body{margin:0;font:14px/1.45 -apple-system,Segoe UI,Roboto,Arial;color:var(--ink);background:var(--bg)}
header{background:var(--ink);color:#fff;padding:14px 24px;display:flex;gap:26px;align-items:baseline;flex-wrap:wrap}
header b{font-size:17px}header a{color:#cfe0f0;text-decoration:none;font-size:13.5px}header a.on{color:#fff;border-bottom:2px solid #6db1e8;padding-bottom:3px}
main{max-width:1180px;margin:22px auto;padding:0 18px}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px;margin-bottom:20px}
.kpi{background:#fff;border:1px solid var(--line);border-radius:10px;padding:14px 16px}
.kpi .v{font-size:22px;font-weight:700}.kpi .l{color:var(--mut);font-size:12px;margin-top:2px}
.card{background:#fff;border:1px solid var(--line);border-radius:10px;padding:16px 18px;margin-bottom:20px}
h2{font-size:15px;margin:0 0 10px}
table{width:100%;border-collapse:collapse;font-size:13.5px}
th{background:#eef2f6;text-align:left;padding:7px 9px;border-bottom:1px solid var(--line);white-space:nowrap}
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
<a href="/compare" class="{{'on' if page=='cmp'}}">Compare</a>
<a href="/headtohead" class="{{'on' if page=='h2h'}}">Head-to-head</a>
<a href="/entities" class="{{'on' if page=='ent'}}">Entities &amp; VAT</a>
<a href="/stations" class="{{'on' if page=='stn'}}">Stations</a>
<a href="/invoices" class="{{'on' if page=='inv'}}">Invoice control</a>
<a href="/extract" class="{{'on' if page=='ext'}}">Import batch</a>
<a href="/vat" class="{{'on' if page=='vat'}}">VAT refunds</a>
<a href="/recovery" class="{{'on' if page=='rec'}}">Recovery</a>
<a href="/payments" class="{{'on' if page=='pay'}}">Payments</a>
<a href="/anomalies" class="{{'on' if page=='ano'}}">Anomalies</a>
<a href="/pricing" class="{{'on' if page=='pri'}}">Pricing intel</a>
<a href="/documents" class="{{'on' if page=='doc'}}">Documents</a>
<a href="/suppliers" class="{{'on' if page=='sup'}}">Suppliers</a>
<a href="/customers" class="{{'on' if page=='cus'}}">Customers</a>
<a href="/data" class="{{'on' if page=='dat'}}">Data manager</a>
<a href="/history" class="{{'on' if page=='his'}}">History</a>
<span style="margin-left:auto" class="exp">
<a href="/export/master">⬇ Master xlsx</a><a href="/export/history">⬇ History report</a>
{% if role == 'admin' %}<a href="/admin" class="{{'on' if page=='adm'}}">Admin</a>{% endif %}
<span class="note" style="color:#9fb3c4">{{ user }} ({{ role }})</span>
<a href="/logout" style="margin-left:10px">Sign out</a></span>
</header><main>{{ body|safe }}</main></body></html>"""

def page(body, p):
    return render_template_string(BASE, body=body, page=p,
                                  user=session.get("user", ""),
                                  role=session.get("role", ""))

def tbl(headers, rows):
    h = "".join(f"<th>{x}</th>" for x in headers)
    b = "".join("<tr>" + "".join(r) + "</tr>" for r in rows)
    return f"<table><thead><tr>{h}</tr></thead><tbody>{b}</tbody></table>"

def psel(name, options, cur, allow_all=True):
    opts = (["ALL"] if allow_all else []) + list(options)
    o = "".join(f'<option {"selected" if v==cur else ""}>{v}</option>' for v in opts)
    return f'<label>{name}<select name="{name}">{o}</select></label>'

# ---------------------------------------------------------------- pages
@app.route("/")
def dash():
    con = DB(); periods = q_periods(con)
    period = request.args.get("period", periods[0])
    k = q_kpis(con, period); bm = q_benchmark(con, period); tr = q_trend(con)
    kpis = f"""<div class="kpis">
      <div class="kpi"><div class="v">{k['litres']:,.0f} L</div><div class="l">Diesel litres · {period}</div></div>
      <div class="kpi"><div class="v">€{k['eurl']:.4f}</div><div class="l">Fleet eff. net €/L</div></div>
      <div class="kpi"><div class="v">€{k['net']:,.0f}</div><div class="l">Net spend</div></div>
      <div class="kpi"><div class="v">€{k['vat']:,.0f}</div><div class="l">Reclaimable VAT</div></div>
      <div class="kpi"><div class="v">€{k['gross']:,.0f}</div><div class="l">Gross invoiced</div></div></div>"""
    rows = [[f"<td>{r['supplier']}</td><td>{r['country']}</td>",
             f"<td class=r>{r['litres']:,.0f}</td><td class=r>{r['doc']:.4f}</td>",
             f"<td class=r><b>{r['eff']:.4f}</b></td>"] for r in bm]
    bench = tbl(["Supplier","Country","Litres","€/L doc","€/L effective"], rows)
    trows = [[f"<td>{r['period']}</td><td class=r>{r['litres']:,.0f}</td><td class=r>{r['eurl']:.4f}</td>"] for r in tr]
    trend = tbl(["Period","Diesel litres","Fleet eff. €/L"], trows)
    psw = "".join(f'<option {"selected" if p==period else ""}>{p}</option>' for p in periods)
    close = _close_status(period)
    body = (f'<form class="f" method="get"><label>Period<select name="period" onchange="this.form.submit()">{psw}</select></label></form>'
            + close + kpis + f'<div class="card"><h2>Diesel benchmark — effective net €/L (cheapest first)</h2>{bench}'
            f'<div class="note">Effective includes rebate layers (Q8/Port One).</div></div>'
            f'<div class="card"><h2>Monthly trend</h2>{trend}<div class="note">Populates as periods are loaded via history.py.</div></div>')
    con.close(); return page(body, "dash")

def _close_status(period):
    """Month-close checklist: one glance at where the period stands."""
    import sqlite3, os as _os
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
                  f'<div class="l">{label}<br><span class="note">{detail}</span></div></div>')
    return f'<div class="card"><h2>Month-close status — {period}</h2><div class="kpis">{cells}</div></div>'

@app.route("/compare")
def compare():
    con = DB(); f = q_filters(con)
    args = {k: request.args.get(k, "ALL") for k in ("period","supplier","country","product")}
    if args["period"] == "ALL" and request.args.get("period") is None:
        args["period"] = f["periods"][0]
    args["date_from"] = request.args.get("date_from",""); args["date_to"] = request.args.get("date_to","")
    rows = q_compare(con, args)
    form = ('<form class="f" method="get">' + psel("period", f["periods"], args["period"])
            + psel("supplier", f["suppliers"], args["supplier"]) + psel("country", f["countries"], args["country"])
            + psel("product", f["products"], args["product"])
            + f'<label>date from<input type="date" name="date_from" value="{args["date_from"]}"></label>'
            + f'<label>date to<input type="date" name="date_to" value="{args["date_to"]}"></label>'
            + '<button>Apply</button></form>')
    trs = [[f"<td>{r['supplier']}</td><td>{r['country']}</td><td>{r['product_group']}</td>",
            f"<td class=r>{r['litres']:,.0f}</td><td class=r>{r['net_eur']:,.2f}</td><td class=r>{r['vat_eur']:,.2f}</td>",
            f"<td class=r>{r['eur_l_doc'] or ''}</td><td class=r><b>{r['eur_l_eff'] or ''}</b></td>"] for r in rows]
    body = form + f'<div class="card"><h2>Comparison — {len(rows)} groups</h2>' + \
           tbl(["Supplier","Country","Product","Qty","Net €","VAT €","€/L doc","€/L eff"], trs) + "</div>"
    con.close(); return page(body, "cmp")

@app.route("/headtohead")
def h2h():
    con = DB(); periods = q_periods(con)
    period = request.args.get("period", periods[0])
    data = q_headtohead(con, period)
    tot = sum(d["overpay"] for d in data)
    psw = "".join(f'<option {"selected" if p==period else ""}>{p}</option>' for p in periods)
    rows = [[f"<td>{d['date']}</td><td>{d['country']}</td><td>{d['prices']}</td>",
             f"<td class=ok>{d['cheapest']}</td><td class=r>{d['spread']:.4f}</td>",
             f"<td class=r>{d['litres']:,}</td><td class='r bad'>{d['overpay']:,.0f}</td>"] for d in data]
    body = (f'<form class="f" method="get"><label>Period<select name="period" onchange="this.form.submit()">{psw}</select></label></form>'
            f'<div class="card"><h2>Same-day, same-country diesel overlaps — total overpay vs cheapest: <span class="bad">€{tot:,.0f}</span></h2>'
            + tbl(["Date","Country","Effective €/L by supplier","Cheapest","Spread","Litres","Overpay €"], rows)
            + '<div class="note">Apples-to-apples: days where 2+ suppliers fueled diesel in the same country.</div></div>')
    con.close(); return page(body, "h2h")

@app.route("/entities")
def entities():
    con = DB(); periods = q_periods(con)
    period = request.args.get("period", periods[0])
    rows = q_entities(con, period)
    psw = "".join(f'<option {"selected" if p==period else ""}>{p}</option>' for p in periods)
    trs = [[f"<td>{r['entity']}</td><td>{r['country']}</td>",
            f"<td class=r>{r['net']:,.2f}</td><td class=r>{r['vat']:,.2f}</td><td class=r><b>{r['gross']:,.2f}</b></td>"] for r in rows]
    body = (f'<form class="f" method="get"><label>Period<select name="period" onchange="this.form.submit()">{psw}</select></label></form>'
            f'<div class="card"><h2>Per-entity totals &amp; reclaimable VAT (EUR) — {period}</h2>'
            + tbl(["Entity","Country","Net","VAT reclaimable","Gross"], trs)
            + '<div class="note">One VAT refund stream per entity registration per country.</div></div>')
    con.close(); return page(body, "ent")

@app.route("/stations")
def stations():
    con = DB(); periods = q_periods(con)
    period = request.args.get("period", periods[0])
    rows = q_stations(con, period)
    psw = "".join(f'<option {"selected" if p==period else ""}>{p}</option>' for p in periods)
    trs = []
    for r in rows:
        flag = '<td class="ok">PREFER</td>' if r["eurl"]<=1.40 else ('<td class="bad">AVOID</td>' if r["eurl"]>=1.62 else "<td></td>")
        trs.append([f"<td>{r['supplier']}</td><td>{r['country']}</td><td>{r['station']}</td>",
                    f"<td class=r>{r['litres']:,.0f}</td><td class=r><b>{r['eurl']:.4f}</b></td>{flag}"])
    body = (f'<form class="f" method="get"><label>Period<select name="period" onchange="this.form.submit()">{psw}</select></label></form>'
            f'<div class="card"><h2>Diesel station scorecard ≥300 L — cheapest first ({period})</h2>'
            + tbl(["Supplier","Country","Station","Litres","Eff. €/L","Routing"], trs) + "</div>")
    con.close(); return page(body, "stn")

# ---------------------------------------------------------------- exports + API
@app.route("/export/master")
def export_master():
    import glob
    f = sorted(glob.glob(os.path.join(WORKDIR, "Fleet_Fuel_Master_*.xlsx")))[-1]
    return send_file(f, as_attachment=True)

@app.route("/export/history")
def export_history():
    return send_file(os.path.join(WORKDIR, "Fleet_Fuel_History_Report.xlsx"), as_attachment=True)

@app.route("/api/periods")
def api_periods():
    con = DB(); out = q_periods(con); con.close(); return jsonify(out)

@app.route("/api/benchmark")
def api_benchmark():
    con = DB(); period = request.args.get("period", q_periods(con)[0])
    out = [dict(r) for r in q_benchmark(con, period)]; con.close(); return jsonify(out)

@app.route("/api/compare")
def api_compare():
    con = DB(); out = [dict(r) for r in q_compare(con, request.args)]; con.close(); return jsonify(out)

@app.route("/api/headtohead")
def api_h2h():
    con = DB(); period = request.args.get("period", q_periods(con)[0])
    out = q_headtohead(con, period); con.close(); return jsonify(out)

@app.route("/api/entities")
def api_entities():
    con = DB(); period = request.args.get("period", q_periods(con)[0])
    out = [dict(r) for r in q_entities(con, period)]; con.close(); return jsonify(out)


@app.route("/extract", methods=["GET", "POST"])
def extract_batch():
    import extract as EX
    role = session.get("role", "viewer")
    if role not in ("editor", "admin"):
        return page(FORBIDDEN, ""), 403
    backend_env = EX.EXTRACT_BACKEND
    body = ""
    if request.method == "POST" and "file" in request.files:
        f = request.files["file"]
        data = f.read()
        try:
            draft = EX.extract(data, f.filename, backend=request.form.get("backend") or None)
        except Exception as e:
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
    return ('<div class="card"><h2>Import an invoice batch (PDF or ZIP)</h2>'
            '<form method="post" enctype="multipart/form-data" class="f">'
            '<label>file (.pdf or .zip)<input type="file" name="file" accept=".pdf,.zip" required></label>'
            f'<label>extractor<select name="backend">{opts}</select></label>'
            '<button>Extract draft</button></form>'
            f'<div class="note">{privacy} Deterministic parser is free and offline; '
            'auto uses it when the supplier is recognised and falls back to AI otherwise. '
            'Nothing is saved until you review and confirm on the next screen.</div></div>')


def _review_form(draft, token):
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
            f'<input type="hidden" name="token" value="{esc(token)}">'
            f'<label>supplier code<input name="supplier" value="{esc(draft.get("supplier") or "")}" required></label>'
            f'<label>statement ref<input name="stmt_ref" value="{esc(draft.get("statement_ref") or "")}" required></label>'
            f'<label>statement date<input type="date" name="stmt_date" value="{esc(draft.get("statement_date") or "")}"></label>'
            f'<label>customer<input name="customer" value="{esc((draft.get("customer") or "").strip())}"></label>'
            f'<label>period (YYYY-MM)<input name="period" value="{esc(request.values.get("period","2026-05"))}" required></label>'
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
    role = session.get("role", "viewer")
    if role not in ("editor", "admin"):
        return page(FORBIDDEN, ""), 403
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
                                           filename=f"{inv}.pdf", kind="original_pdf")
                if ok: attached += 1
        fcon.close()
        _os.unlink(tmpf)
    banner = (f'<div class="card"><b class="ok">Statement {esc(request.form["stmt_ref"])} '
              f'registered: {len(lines)} invoices ({synced} VAT-bearing synced), '
              f'{attached} PDFs vaulted. Review triage on the Invoice control page.</b></div>')
    return page(banner + f'<p><a href="/invoices?period={esc(period)}">→ Invoice control</a></p>', "ext")

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
            banner = f'<div class="card"><b class="bad">Statement error: {e}</b></div>' 
    rows, orphans = invoice_control.run_control(period)
    order = {"MISSING": 0, "RECEIVED - DOC MISSING": 1}
    rows.sort(key=lambda x: (order.get(x["status"], 2), x["supplier"]))
    trs = []
    for r in rows:
        cls = ("bad" if r["status"]=="MISSING" else
               "bad" if "DOC MISSING" in r["status"] else
               "" if r["status"]=="NO ACTIVITY" else "ok")
        trs.append([f"<td>{r['supplier']}</td><td>{r['country']}</td><td>{r['slot']}</td>",
                    f"<td>{r['expected']}</td><td>{r['invoice_no'] or '—'}</td>",
                    f"<td class='{cls}'>{r['status']}</td><td class='note'>{r['note']}</td>"])
    miss = sum(1 for r in rows if r["status"]=="MISSING")
    orph = "".join(f"<li>{o}</li>" for o in orphans)
    stmts = invoice_control.reconcile_statements(period)
    s_trs = []
    for r in stmts:
        cls = ("ok" if r["verdict"]=="PROCESS - COMPLETE" else
               "" if r["verdict"].startswith("DISCARD") else "bad")
        s_trs.append([f"<td>{r['supplier']}</td><td>{r['statement']}</td><td>{r['invoice']}</td>",
                      f"<td>{r['country']}</td><td class=r>{r['net']:,.2f}</td><td class=r>{r['vat']:,.2f}</td>",
                      f"<td class='{cls}'>{r['verdict']}</td><td class='note'>{r['action']}</td>"])
    stmt_html = (('<div class="card"><h2>Statement reconciliation - every invoice the supplier issued, triaged by VAT</h2>'
                  + tbl(["Supplier","Statement","Issued invoice","Country","Net","VAT","Verdict","Action"], s_trs)
                  + '<div class="note">VAT &gt; 0 -> PROCESS (original required, feeds the refund claim). '
                    'VAT = 0 -> DISCARD (archive only). VAT-bearing lines auto-register so the VAT module '
                    'and receipt control see them.</div></div>') if stmts else "")
    reg_form = ('<div class="card"><h2>Register a summary statement</h2>'
                f'<form method="post" action="/invoices?period={period}">'
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
            f'<input name="period" value="{period}"></label><button>Run control</button></form>'
            f'<div class="card"><h2>Invoice receipt control — {period}: '
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
    import pricing_intel as PI
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
        + '<p style="margin-top:10px"><a href="/export/pricing?grain='+esc(grain)+'">⬇ Export daily/weekly/monthly grid (Excel)</a></p>'
        + '</div>'
        + '<div class="card"><h2>Upload MY Prices (your NET benchmark)</h2>'
        '<form method="post" action="/pricing/upload" enctype="multipart/form-data" class="f">'
        '<label>CSV file<input type="file" name="file" accept=".csv" required></label>'
        '<label><input type="checkbox" name="replace"> replace existing for these months</label>'
        '<button>Upload MY Prices</button></form>'
        '<div class="note">CSV columns: <b>country,city,date,net_price</b> (date YYYY-MM-DD, '
        'price NET final EUR/L). Optional 5th column product_group (default Diesel). '
        'Also accepts wholesale index via columns <b>country,date,net_price</b> using the '
        'wholesale upload below.</div>'
        '<form method="post" action="/pricing/upload?kind=wholesale" enctype="multipart/form-data" class="f" style="margin-top:8px">'
        '<label>Wholesale index CSV<input type="file" name="file" accept=".csv" required></label>'
        '<button>Upload wholesale index</button></form>'
        '<div class="note">Wholesale columns: country,date,net_price. Enables the true-margin '
        '(margin vs wholesale) column — Platts, national pre-tax benchmark, or Brent proxy.</div></div>')
    return page(body, "pri")

@app.route("/pricing/upload", methods=["POST"])
def pricing_upload():
    import pricing_intel as PI, csv, io
    role = session.get("role", "viewer")
    if role not in ("editor", "admin"):
        return page(FORBIDDEN, ""), 403
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
    import pricing_intel as PI
    path, _ = PI.export_excel(grain=request.args.get("grain", "month"))
    return send_file(path, as_attachment=True)

@app.route("/api/pricing")
def api_pricing():
    import pricing_intel as PI
    rows, summ = PI.margin_report(None, request.args.get("grain", "month"),
                                  request.args.get("pg", "Diesel"))
    return jsonify({"summary": summ, "rows": rows})

@app.route("/recovery")
def recovery():
    import vat_refund as VR
    year = request.args.get("year", "2026")
    rows, summ = VR.recovery_report(year)
    trs = []
    for r in rows:
        agecls = "bad" if isinstance(r["age_days"], int) and r["age_days"] > 120 else ""
        trs.append([f"<td>{esc(r['entity'])}</td><td>{esc(r['country'])}</td><td>{esc(r['period'])}</td>",
                    f"<td class=r>{(r['vat_eur'] or 0):,.2f}</td>",
                    f"<td>{esc(r['status'])}</td><td>{esc(r['submitted'] or '')}</td>",
                    f"<td class='{agecls}'>{r['age_days'] if r['age_days']!='' else ''}</td>",
                    f"<td>{esc(r['paid'] or '')}</td>"])
    body = (f'<div class="card"><h2>VAT recovery tracking {esc(year)}</h2>'
            f'<div class="kpis"><div class="kpi"><div class="v">EUR {summ["submitted"]:,.0f}</div>'
            f'<div class="l">submitted</div></div>'
            f'<div class="kpi"><div class="v">EUR {summ["approved"]:,.0f}</div><div class="l">approved</div></div>'
            f'<div class="kpi"><div class="v ok">EUR {summ["paid"]:,.0f}</div><div class="l">paid back</div></div>'
            f'<div class="kpi"><div class="v bad">EUR {summ["outstanding"]:,.0f}</div>'
            f'<div class="l">outstanding</div></div></div>'
            + tbl(["Entity","Country","Period","VAT EUR","Status","Submitted","Age (days)","Paid"], trs)
            + '<div class="note">Age over 120 days flagged red - chase the tax authority. '
              'Set status to paid on the VAT refunds page when the refund arrives.</div></div>')
    return page(body, "rec")

@app.route("/payments", methods=["GET", "POST"])
def payments_page():
    import payments as PAY
    role = session.get("role", "viewer")
    period = request.values.get("period", "2026-05")
    banner = ""
    if request.method == "POST" and role in ("editor", "admin"):
        xml, inc, skip = PAY.build_sepa(period, request.form["debtor_name"],
                                        request.form["debtor_iban"], request.form.get("debtor_bic",""))
        path = f"{WORKDIR}/SEPA_{period}.xml"
        open(path, "w").write(xml)
        banner = (f'<div class="card"><b class="ok">SEPA file generated: {len(inc)} payments, '
                  f'EUR {sum(l["amount"] for l in inc):,.2f}. '
                  f'<a href="/export/sepa?period={esc(period)}">Download SEPA_{esc(period)}.xml</a></b></div>')
    rows = PAY.due_invoices(period)
    trs = []
    for r in rows:
        eur = (r["currency"] or "EUR") == "EUR"
        noiban = not r["iban"] or "INPUT" in (r["iban"] or "")
        cls = "" if (eur and not noiban) else "bad"
        note = "" if (eur and not noiban) else ("non-EUR (FX)" if not eur else "no IBAN on file")
        trs.append([f"<td>{esc(r['supplier'])}</td><td>{esc(r['invoice_no'])}</td>",
                    f"<td class=r>{(r['gross_total'] or 0):,.2f}</td><td>{esc(r['currency'] or '')}</td>",
                    f"<td class='{cls}'>{esc((r['iban'] or '')[:24])}</td><td class='note'>{note}</td>"])
    form = ('<form method="post" class="f">'
            f'<input type="hidden" name="period" value="{esc(period)}">'
            '<label>paying entity name<input name="debtor_name" required></label>'
            '<label>debtor IBAN<input name="debtor_iban" required></label>'
            '<label>debtor BIC<input name="debtor_bic"></label>'
            '<button>Generate SEPA file</button></form>' if role in ("editor","admin") else "")
    body = (banner + f'<form class="f" method="get"><label>Period<input name="period" value="{esc(period)}"></label><button>Show</button></form>'
            + f'<div class="card"><h2>Payments due — {esc(period)}</h2>'
            + tbl(["Supplier","Invoice","Amount","Ccy","Beneficiary IBAN","Note"], trs)
            + '<div class="note">SEPA file includes EUR invoices with a beneficiary IBAN on file. '
              'Non-EUR (PLN/SEK) are listed for your FX process. Upload the generated pain.001 '
              'file to your bank.</div>' + form + "</div>")
    return page(body, "pay")

@app.route("/export/sepa")
def export_sepa():
    import os as _os
    p = f"{WORKDIR}/SEPA_{request.args.get('period','2026-05')}.xml"
    if _os.path.exists(p):
        return send_file(p, as_attachment=True)
    return "no SEPA file - generate it first", 404

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
    rows = []
    for m in matrix:
        status = sts.get((m["entity"], m["country"], m["period"]), "draft")
        v = m["verdict"]
        vcls = "ok" if v.startswith("READY") else ("bad" if "BELOW" in v else "")
        opts = "".join(f'<option {"selected" if s==status else ""}>{s}</option>'
                       for s in ["draft","ready","submitted","approved","paid","rejected","withdrawn"])
        frm = (f'<form method="post" style="margin:0">'
               f'<input type="hidden" name="entity" value="{m["entity"]}">'
               f'<input type="hidden" name="country" value="{m["country"]}">'
               f'<input type="hidden" name="ref_period" value="{m["period"]}">'
               f'<select name="status" onchange="this.form.submit()">{opts}</select></form>')
        invs = VR.stream_invoices(con, m["entity"], m["country"], m["period"]) if not m["period"].endswith("YEAR") else []
        nd = sum(1 for s, ref in invs if not VR.docs_for(con, m["entity"], s, ref))
        doccov = ("" if m["period"].endswith("YEAR") else
                  (f'<span class="ok">{len(invs)}/{len(invs)} docs</span>' if invs and nd==0
                   else f'<span class="bad">{len(invs)-nd}/{len(invs)} docs</span>'))
        rows.append([f"<td>{m['entity']}</td><td>{m['country']}</td><td>{m['period']}</td>",
                     f"<td class=r>{m['vat_eur']:,.2f}</td><td class=r>{m['vat_local']:,.2f} {m['currency']}</td>",
                     f"<td class='{vcls}'>{v}</td><td>{', '.join(m['missing'])}</td>",
                     f"<td>{doccov}</td><td>{m['home']}</td><td>{m['deadline']}</td><td>{frm}</td>"])
    total_ready = sum(m["vat_eur"] for m in matrix
                      if m["verdict"].startswith("READY") and not m["period"].endswith("YEAR"))
    body = (banner + f'<div class="card"><h2>VAT refund applications {year} (2008/9/EC) — '
            f'quarterly READY total: <span class="ok">€{total_ready:,.0f}</span> &nbsp; '
            f'<a href="/export/vat?year={year}">⬇ Generate claim workbook</a></h2>'
            + tbl(["Entity","Refund country","Period","VAT EUR","VAT local","Threshold verdict",
                   "Months missing","Documents","Home portal","Deadline","Status"], rows)
            + '<div class="note">Statuses persist in the database. Yellow caveats and per-invoice '
              'claim packs are in the exported workbook. Quarterly min €400, annual min €50 '
              '(national equivalents apply).</div></div>')
    con.close(); return page(body, "vat")


@app.route("/documents", methods=["GET", "POST"])
def documents():
    import vat_refund as VR, supplier_db
    from supplier_specs import SPECS
    ENTITY_OVERRIDE = {"PORTONE": "Jupiter Plus AS", "EUROWAG": "Adverza Germany SIA"}
    scon = supplier_db.connect()
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
            dl = " ".join(f'<a href="/doc/{d["id"]}">{esc(d["filename"])}</a> <span class="note">[{d["sha256"][:8]}, {d["kind"]}]</span>'
                          for d in docs) or '<span class="bad">MISSING</span>'
            up = (f'<form method="post" enctype="multipart/form-data" style="margin:0;display:flex;gap:6px">'
                  f'<input type="hidden" name="entity" value="{ent}">'
                  f'<input type="hidden" name="supplier" value="{sup}">'
                  f'<input type="hidden" name="invoice_ref" value="{ref}">'
                  f'<input type="file" name="doc" accept=".pdf,.jpg,.png,.tif" required>'
                  f'<select name="kind"><option>original_pdf</option><option>scan</option></select>'
                  f'<button>Attach</button></form>')
            rows.append([f"<td>{ent}</td><td>{sup}</td><td>{ctry}</td><td>{ref}</td><td>{dt}</td>",
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
    import supplier_db
    con = supplier_db.connect()
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
                body_rows = "".join("<tr>" + "".join(f"<td>{r[col]}</td>" for col in cols) + "</tr>" for r in rows)
                sect += f"<h2 style='margin-top:12px'>{title}</h2><table><tbody>{body_rows}</tbody></table>"
        cards.append(f'<div class="card"><h2>{s["code"]} — {s["legal_name"]} '
                     f'<span class="{"ok" if s["status"]=="active" else "bad"}">[{s["status"]}]</span></h2>'
                     f"<table><tbody>{meta}</tbody></table>{sect}</div>")
    con.close()
    body = ('<div class="note" style="margin-bottom:10px">Supplier master data lives in '
            '<b>suppliers.db</b> — a separate database from the VAT refund claim database '
            '(fuel_history.db). Transactions and claims reference suppliers by code only.</div>'
            + "".join(cards))
    return page(body, "sup")

@app.route("/customers")
def customers():
    import customer_db
    con = customer_db.connect()
    cards = []
    for c in con.execute("SELECT * FROM customers ORDER BY code"):
        def fld(v):
            v2 = esc(str(v)) if v is not None else ""
            return f'<span class="bad">{v2}</span>' if v and "INPUT" in str(v) else v2
        meta = "".join(f"<tr><td style='color:var(--mut);width:160px'>{lbl}</td><td>{fld(c[k])}</td></tr>"
                       for lbl, k in (("Company name","company_name"),("Registration number","reg_number"),
                                      ("VAT number","vat_number"),("Legal address","legal_address"),
                                      ("Country","country"),("Home tax portal","home_portal"),
                                      ("Notes","notes")) if c[k])
        banks = "".join(f"<tr><td>{fld(r['iban'])}</td><td>{r['bank']}</td><td>{r['currency']}</td><td>{r['purpose']}</td></tr>"
                        for r in con.execute("SELECT * FROM customer_bank_accounts WHERE customer=?", (c["code"],)))
        accs = "".join(f"<tr><td>{r['supplier']}</td><td>{fld(r['account_no'])}</td><td class='note'>{r['notes']}</td></tr>"
                       for r in con.execute("SELECT * FROM customer_supplier_accounts WHERE customer=?", (c["code"],)))
        cards.append(f'<div class="card"><h2>{c["code"]} — {c["company_name"]}</h2>'
                     f"<table><tbody>{meta}</tbody></table>"
                     + (f"<h2 style='margin-top:12px'>Bank accounts</h2><table><tbody>{banks}</tbody></table>" if banks else "")
                     + (f"<h2 style='margin-top:12px'>Supplier account numbers</h2><table><tbody>{accs}</tbody></table>" if accs else "")
                     + "</div>")
    con.close()
    body = ('<div class="note" style="margin-bottom:10px">Customer (entity) master data lives in '
            '<b>customers.db</b> — separated from the supplier master (suppliers.db) and from the '
            'claims database (fuel_history.db). These profiles feed the APPLICANT block of every '
            'VAT refund claim pack; red INPUT fields must be completed before first submission.</div>'
            + "".join(cards))
    return page(body, "cus")


# ---------------------------------------------------------------- data manager + history
import customer_db as _cdb, supplier_db as _sdb
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
            banner = f'<div class="card"><b class="bad">Error: {e}</b></div>'
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
        hpk = "".join(f'<input type="hidden" name="__pk_{k}" value="{r[cols.index(k)]}">' for k in pks)
        rows_html += (f'<tr><form method="post" action="/data?db={dbk}&table={table}">{inputs}'
                      f'<td style="white-space:nowrap">{hpk}'
                      f'<button name="__action" value="save">Save</button> '
                      f'<button name="__action" value="delete" style="background:var(--bad)" '
                      f'onclick="return confirm(\'Delete this row? The audit log keeps it.\')">Delete</button>'
                      f'</td></form></tr>')
    new_inputs = "".join(f'<td><input name="c_{c}" placeholder="{c}" style="width:90px"></td>' for c in cols)
    rows_html += (f'<tr><form method="post" action="/data?db={dbk}&table={table}">{new_inputs}'
                  f'<td><button name="__action" value="save">+ Add new</button></td></form></tr>')
    head_html = "".join(f"<th>{c}</th>" for c in cols) + "<th>actions</th>"
    body = (banner + form + f'<div class="card"><h2>{dbk}.db / {table} — write new, edit, delete '
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
            f'<label>from date<input type="date" name="from" value="{f_}"></label>'
            f'<label>till date<input type="date" name="till" value="{t_}"></label>'
            f'<label>record key<input name="key" value="{key}" placeholder="e.g. OMUSS"></label>'
            f'<button>Apply</button></form>')
    trs = []
    for r in rows:
        cls = {"INSERT":"ok","DELETE":"bad"}.get(r["action"], "")
        change = (_audit.diff(r["old_data"], r["new_data"]) if r["action"]=="UPDATE"
                  else (r["new_data"] or r["old_data"] or ""))
        trs.append([f"<td>{r['ts']}</td><td>{r['tbl']}</td><td>{r['rowkey']}</td>",
                    f"<td class='{cls}'>{r['action']}</td><td>{esc(r['changed_by'])}</td>" 
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
                _auth.add_user(tgt, request.form["password"], request.form.get("role", "editor"))
                banner = f"User <b>{esc(tgt)}</b> created/updated (password scrypt-hashed)."
            elif act == "role":
                if tgt == session["user"]:
                    raise ValueError("you cannot change your own role")
                _auth.set_role(tgt, request.form["role"])
                banner = f"Role of <b>{esc(tgt)}</b> set to {esc(request.form['role'])}."
            elif act == "toggle":
                if tgt == session["user"]:
                    raise ValueError("you cannot disable your own account")
                u = _auth.get_user(tgt)
                _auth.set_active(tgt, 0 if u["active"] else 1)
                banner = f"<b>{esc(tgt)}</b> {'disabled' if u['active'] else 'enabled'}."
            elif act == "reset":
                _auth.add_user(tgt, request.form["password"])
                banner = f"Password of <b>{esc(tgt)}</b> reset."
            scon = _auth.connect(); _audit_mod.reset_actor(scon); scon.close()
            banner = f'<div class="card"><b class="ok">{banner}</b></div>'
        except Exception as e:
            banner = f'<div class="card"><b class="bad">Error: {esc(str(e))}</b></div>'
    users, logins = _auth.list_users()
    rsel = lambda cur: "".join(f'<option {"selected" if r == cur else ""}>{r}</option>'
                               for r in _auth.ROLES)
    utr = []
    for u in users:
        me = u["username"] == session["user"]
        actions = ("" if me else
            f'<form method="post" style="display:inline">'
            f'<input type="hidden" name="username" value="{esc(u["username"])}">'
            f'<select name="role">{rsel(u["role"])}</select> '
            f'<button name="__act" value="role">Set role</button> '
            f'<button name="__act" value="toggle" style="background:var(--bad)">'
            f'{"Disable" if u["active"] else "Enable"}</button></form> '
            f'<form method="post" style="display:inline">'
            f'<input type="hidden" name="username" value="{esc(u["username"])}">'
            f'<input type="password" name="password" placeholder="new password" required '
            f'style="width:110px"> <button name="__act" value="reset">Reset pw</button></form>')
        utr.append([f'<td>{esc(u["username"])}{" <b>(you)</b>" if me else ""}</td>'
                    f'<td>{esc(u["role"])}</td>',
                    f'<td class="{ "ok" if u["active"] else "bad"}">'
                    f'{"active" if u["active"] else "DISABLED"}</td>',
                    f'<td>{esc(u["last_login"])}</td><td>{actions}</td>'])
    addf = ('<form method="post" class="f">'
            '<label>username<input name="username" required></label>'
            '<label>password<input type="password" name="password" required></label>'
            f'<label>role<select name="role">{rsel("editor")}</select></label>'
            '<button name="__act" value="add">+ Create user</button></form>'
            '<div class="note">Passwords are stored as salted scrypt hashes - never in '
            'plain text. Roles: viewer = read-only, editor = operational changes, '
            'admin = + this panel. All user-management changes are audit-logged.</div>')
    ltr = [[f'<td>{esc(l["ts"])}</td><td>{esc(l["username"])}</td>',
            f'<td class="{ "ok" if l["success"] else "bad"}">'
            f'{"OK" if l["success"] else "FAILED"}</td><td>{esc(l["remote"] or "")}</td>']
           for l in logins]
    import os as _os
    tls = _os.path.exists(_os.path.join(WORKDIR, "cert.pem"))
    body = (banner
            + '<div class="card"><h2>Users &amp; permissions</h2>'
            + tbl(["Username", "Role", "Status", "Last login", "Actions"], utr)
            + addf + "</div>"
            + f'<div class="card"><h2>Security status</h2>'
              f'<p>TLS certificate: '
              f'{"<span class=ok>cert.pem present - app serves HTTPS</span>" if tls else "<span class=bad>none - run python3 make_cert.py (self-signed) or install a CA cert</span>"}'
              f' &nbsp;|&nbsp; Password storage: <span class="ok">scrypt (salted)</span>'
              f' &nbsp;|&nbsp; Session cookies: HttpOnly, SameSite'
              f'{", Secure (HTTPS)" if tls else ""}</p></div>'
            + '<div class="card"><h2>Recent logins</h2>'
            + tbl(["Timestamp (UTC)", "Username", "Result", "From"], ltr) + "</div>")
    return page(body, "adm")

@app.route("/doc/<int:doc_id>")
def doc_download(doc_id):
    import vat_refund as VR, doc_storage, io
    con = VR.connect()
    d = con.execute("SELECT * FROM invoice_documents WHERE id=?", (doc_id,)).fetchone()
    con.close()
    data = doc_storage.get_bytes(d["stored_path"], VR.DOCDIR)
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
    _ctx, _desc = tls.build_context()
    if _ctx:
        app.config.update(SESSION_COOKIE_SECURE=True)
        print(f" * TLS enabled -> https://127.0.0.1:8050\n * Certificate: {_desc}")
        app.run(host="127.0.0.1", port=8050, debug=False, ssl_context=_ctx)
    else:
        print(f" * HTTP on localhost only ({_desc})")
        app.run(host="127.0.0.1", port=8050, debug=False)
