"""
FLEET FUEL CONTROL PANEL - basic local web UI for the monthly pipeline.
Zero dependencies (Python standard library only).

Run:    python3 ui.py          then open  http://localhost:8080
Stop:   Ctrl+C

What it does:
  - Dashboard: loaded periods, suppliers, last pipeline run log
  - One-click pipeline: 1) Consolidate & validate  2) Build master  3) Update history
  - Data views straight from fuel_history.db: benchmark, head-to-head, entity/VAT, stations
  - Read-only SQL box for ad-hoc questions (SELECT only)
  - Download buttons for the Excel reports and the database
"""
import http.server, sqlite3, subprocess, urllib.parse, html, os, json

WORKDIR = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(WORKDIR, "fuel_history.db")
PORT = 8080
LOG = os.path.join(WORKDIR, "last_run.log")
DOWNLOADS = ["Fleet_Fuel_Master_2026-05.xlsx", "Fleet_Fuel_History_Report.xlsx", "fuel_history.db"]
STEPS = {"consolidate": "consolidate.py", "master": "build_master.py", "history": "history.py"}

CSS = """<style>
body{font-family:Segoe UI,Arial,sans-serif;margin:0;background:#f4f6f8;color:#1a2733}
header{background:#1F3864;color:#fff;padding:14px 24px;font-size:20px;font-weight:600}
nav{background:#2a4a80;padding:8px 24px}nav a{color:#dce6f1;margin-right:18px;text-decoration:none;font-size:14px}
nav a:hover{color:#fff}main{padding:20px 24px;max-width:1200px}
.card{background:#fff;border-radius:8px;padding:16px 20px;margin-bottom:16px;box-shadow:0 1px 3px rgba(0,0,0,.08)}
h2{font-size:16px;margin:0 0 10px}table{border-collapse:collapse;width:100%;font-size:13px}
th{background:#1F3864;color:#fff;padding:6px 10px;text-align:left}td{padding:5px 10px;border-bottom:1px solid #e3e8ee}
tr:hover td{background:#eef3fa}.num{text-align:right;font-variant-numeric:tabular-nums}
.btn{display:inline-block;background:#1F3864;color:#fff;padding:8px 16px;border-radius:6px;border:0;
text-decoration:none;font-size:14px;margin-right:8px;cursor:pointer}.btn.green{background:#1B7340}
.btn.gray{background:#5a6b7d}pre{background:#0f1722;color:#c9e3c9;padding:12px;border-radius:6px;
font-size:12px;overflow-x:auto;max-height:340px}.ok{color:#1B7340;font-weight:600}.fail{color:#C8102E;font-weight:600}
input[type=text]{width:75%;padding:8px;font-size:13px;border:1px solid #b9c4d0;border-radius:6px}
.kpi{display:inline-block;background:#eef3fa;border-radius:8px;padding:10px 18px;margin-right:12px}
.kpi b{display:block;font-size:20px;color:#1F3864}.kpi span{font-size:12px;color:#5a6b7d}</style>"""

def page(title, body):
    nav = ('<a href="/">Dashboard</a><a href="/benchmark">Benchmark</a><a href="/compare">Head-to-head</a>'
           '<a href="/entity">Entity & VAT</a><a href="/stations">Stations</a><a href="/query">SQL</a>')
    return (f"<!doctype html><html><head><meta charset='utf-8'><title>{title}</title>{CSS}</head>"
            f"<body><header>Fleet Fuel Control Panel</header><nav>{nav}</nav><main>{body}</main></body></html>").encode()

def table(headers, rows, numcols=()):
    h = "".join(f"<th>{html.escape(str(x))}</th>" for x in headers)
    body = ""
    for r in rows:
        tds = "".join(f"<td class='{'num' if i in numcols else ''}'>"
                      f"{'' if v is None else (f'{v:,.4f}' if i in numcols and isinstance(v,float) else html.escape(str(v)))}</td>"
                      for i, v in enumerate(r))
        body += f"<tr>{tds}</tr>"
    return f"<table><tr>{h}</tr>{body}</table>"

def q(sql, args=()):
    con = sqlite3.connect(DB); con.row_factory = sqlite3.Row
    try:
        cur = con.execute(sql, args)
        return [c[0] for c in cur.description], [tuple(r) for r in cur.fetchall()]
    finally:
        con.close()

class H(http.server.BaseHTTPRequestHandler):
    def _send(self, body, ctype="text/html"):
        self.send_response(200); self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

    def log_message(self, *a): pass

    def do_GET(self):
        path, _, qs = self.path.partition("?")
        params = urllib.parse.parse_qs(qs)
        if path == "/": return self._send(page("Dashboard", self.dashboard()))
        if path == "/benchmark": return self._send(page("Benchmark", self.benchmark()))
        if path == "/compare": return self._send(page("Head-to-head", self.compare()))
        if path == "/entity": return self._send(page("Entity & VAT", self.entity()))
        if path == "/stations": return self._send(page("Stations", self.stations()))
        if path == "/query": return self._send(page("SQL", self.querybox(params)))
        if path.startswith("/download/"):
            fn = os.path.basename(urllib.parse.unquote(path[10:]))
            fp = os.path.join(WORKDIR, fn)
            if fn in DOWNLOADS and os.path.exists(fp):
                data = open(fp, "rb").read()
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Disposition", f'attachment; filename="{fn}"')
                self.send_header("Content-Length", str(len(data))); self.end_headers()
                return self.wfile.write(data)
        self.send_response(404); self.end_headers()

    def do_POST(self):
        if self.path.startswith("/run/"):
            step = self.path[5:]
            if step in STEPS:
                r = subprocess.run(["python3", os.path.join(WORKDIR, STEPS[step])],
                                   capture_output=True, text=True, cwd=WORKDIR)
                open(LOG, "w").write(f"$ python3 {STEPS[step]}\n{r.stdout}{r.stderr}\nexit code {r.returncode}")
            self.send_response(303); self.send_header("Location", "/"); self.end_headers(); return
        self.send_response(404); self.end_headers()

    # ---------- views ----------
    def dashboard(self):
        kpis = ""
        try:
            _, p = q("SELECT COUNT(DISTINCT period), COUNT(*), COUNT(DISTINCT supplier), COUNT(DISTINCT entity) FROM transactions")
            _, d = q("SELECT ROUND(SUM(qty),0), ROUND(SUM(net_eur_eff)/SUM(qty),4) FROM transactions WHERE product_group='Diesel'")
            for v, lbl in ((p[0][0],"periods loaded"),(p[0][1],"transactions"),(p[0][2],"suppliers"),
                           (p[0][3],"entities"),(f"{d[0][0]:,.0f}","diesel litres"),(d[0][1],"fleet eff EUR/L")):
                kpis += f"<div class='kpi'><b>{v}</b><span>{lbl}</span></div>"
        except Exception as e:
            kpis = f"<p>No database yet ({html.escape(str(e))}) - run the pipeline below.</p>"
        btns = ('<form method="post" action="/run/consolidate" style="display:inline">'
                '<button class="btn">1. Consolidate & validate</button></form>'
                '<form method="post" action="/run/master" style="display:inline">'
                '<button class="btn green">2. Build master workbook</button></form>'
                '<form method="post" action="/run/history" style="display:inline">'
                '<button class="btn gray">3. Update history & report</button></form>')
        log = open(LOG).read() if os.path.exists(LOG) else "No pipeline run yet this session."
        ok = "PASS" in log and "FAIL" not in log.replace("VALIDATION FAILURES", "")
        status = "<span class='ok'>PASS</span>" if "PASS" in log and "** FAIL **" not in log else \
                 ("<span class='fail'>FAIL</span>" if "FAIL" in log else "")
        dls = "".join(f'<a class="btn gray" href="/download/{f}">{f}</a> ' for f in DOWNLOADS
                      if os.path.exists(os.path.join(WORKDIR, f)))
        return (f"<div class='card'><h2>Status</h2>{kpis}</div>"
                f"<div class='card'><h2>Monthly pipeline</h2><p>Drop the new supplier files into this folder, "
                f"update month_config.py, then:</p>{btns}</div>"
                f"<div class='card'><h2>Last run {status}</h2><pre>{html.escape(log)}</pre></div>"
                f"<div class='card'><h2>Downloads</h2>{dls}</div>")

    def benchmark(self):
        h, rows = q("""SELECT period, supplier, country, litres, eur_l_doc, eur_l_eff
                       FROM v_supplier_month WHERE product_group='Diesel' ORDER BY period, eur_l_eff""")
        return f"<div class='card'><h2>Diesel benchmark (effective EUR/L, cheapest first)</h2>{table(h, rows, (3,4,5))}</div>"

    def compare(self):
        h, rows = q("""SELECT date, country,
                       GROUP_CONCAT(supplier || ' ' || ROUND(eurl,4), '  |  ') prices,
                       ROUND(MAX(eurl)-MIN(eurl),4) spread, ROUND(SUM(litres),0) litres
                       FROM (SELECT date, country, supplier, SUM(qty) litres,
                                    SUM(net_eur_eff)/SUM(qty) eurl FROM transactions
                             WHERE product_group='Diesel' GROUP BY date, country, supplier)
                       GROUP BY date, country HAVING COUNT(*)>=2 ORDER BY date""")
        return (f"<div class='card'><h2>Head-to-head: 2+ suppliers, same day, same country (diesel)</h2>"
                f"{table(h, rows, (3,4))}</div>")

    def entity(self):
        h, rows = q("SELECT * FROM v_entity_month ORDER BY period, entity, country")
        return f"<div class='card'><h2>Entity totals & reclaimable VAT (EUR)</h2>{table(h, rows, (3,4,5))}</div>"

    def stations(self):
        h, rows = q("""SELECT * FROM v_station_month WHERE litres>=300 ORDER BY period, eur_l_eff""")
        return f"<div class='card'><h2>Diesel stations >= 300 L/month (cheapest first)</h2>{table(h, rows, (4,5))}</div>"

    def querybox(self, params):
        sql = params.get("sql", ["SELECT period, supplier, ROUND(SUM(net_eur_eff)/SUM(qty),4) AS eur_l "
                                 "FROM transactions WHERE product_group='Diesel' GROUP BY period, supplier"])[0]
        out = ""
        if params.get("run"):
            if sql.strip().lower().startswith("select"):
                try:
                    h, rows = q(sql)
                    out = table(h, rows) + f"<p>{len(rows)} rows</p>"
                except Exception as e:
                    out = f"<p class='fail'>{html.escape(str(e))}</p>"
            else:
                out = "<p class='fail'>Read-only: SELECT statements only.</p>"
        return (f"<div class='card'><h2>Ad-hoc SQL (read-only)</h2>"
                f"<form method='get' action='/query'><input type='text' name='sql' value='{html.escape(sql, quote=True)}'>"
                f"<input type='hidden' name='run' value='1'> <button class='btn'>Run</button></form>"
                f"<p style='font-size:12px;color:#5a6b7d'>Tables: transactions | Views: v_supplier_month, "
                f"v_entity_month, v_station_month</p></div><div class='card'>{out}</div>")

if __name__ == "__main__":
    print(f"Fleet Fuel Control Panel -> http://localhost:{PORT}   (Ctrl+C to stop)")
    http.server.ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
