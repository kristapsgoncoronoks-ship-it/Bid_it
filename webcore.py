"""
Shared Flask web rendering helpers.

This module is deliberately app-leaf code: route blueprints can import these helpers
without importing app.py and creating a circular dependency. app.py configures the
small bits of app-owned state at startup, then every route uses the same shell and
helper functions as before.
"""
from flask import Response, session
from markupsafe import escape as esc

import auth as _auth
import i18n

_app = None
_enabled_modules = None
_csrf_token = None


def configure(app, enabled_modules, csrf_token):
    """Bind app-owned dependencies used by the shared page shell.

    Kept explicit so this module remains importable from blueprints without importing
    app.py. Calling it again resets the compiled template, which is useful in tests.
    """
    global _app, _enabled_modules, _csrf_token, _BASE_TMPL
    _app = app
    _enabled_modules = enabled_modules
    _csrf_token = csrf_token
    _BASE_TMPL = None


def _require_config():
    if _app is None or _enabled_modules is None or _csrf_token is None:
        raise RuntimeError("webcore.configure() must be called before rendering pages")


def _csrf_input():
    _require_config()
    return f'<input type="hidden" name="_csrf" value="{esc(_csrf_token())}">'

BASE = """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Fleet Fuel Analytics</title><style>
/* DESIGN TOKENS — consolidated, reusable across every page.
   Colours: --ink/--mut text, --line/--line2 borders, --bg surface, --acc primary
   (blue, the established brand), --ok/--bad/--warn/--info semantic.
   SPACING SCALE --s1..--s5 (4/8/12/16/24px) — use for new gaps/padding, do not
   restructure existing layouts. TYPE SCALE --t-xs..--t-lg (12/13.5/15/17px).
   BUTTON HIERARCHY: .btn = primary (blue, unchanged), .btn-secondary = neutral
   outline, .btn-danger = red destructive (delete/withdraw). */
:root{--ink:#1a2733;--mut:#5b6b7a;--line:#dde4ea;--line2:#e7ecf1;--bg:#f4f6f8;--acc:#0e5fa8;--acc2:#0b4d89;--ok:#1b7340;--bad:#c8102e;--warn:#9a6700;--info:#0e5fa8;--card-sh:0 1px 2px rgba(26,39,51,.05),0 1px 3px rgba(26,39,51,.04);--radius:11px;
 --s1:4px;--s2:8px;--s3:12px;--s4:16px;--s5:24px;--t-xs:12px;--t-sm:13.5px;--t-md:15px;--t-lg:17px}
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
/* desktop: hover/focus opens the dropdown. Gated behind a fine pointer so a touch
   device never relies on a (non-existent) hover — there the app.js tap toggle
   adds .menu.open instead. */
@media (hover:hover){.menu:hover .mdrop,.menu:focus-within .mdrop{display:flex}}
.menu.open .mdrop{display:flex}
.mdrop>span{background:#223240;border:1px solid #34485a;border-radius:10px;padding:6px;min-width:185px;display:flex;flex-direction:column;gap:1px;box-shadow:0 14px 34px rgba(0,0,0,.45)}
.mdrop a{color:#cfe0f0;padding:7px 11px;border-radius:6px;white-space:nowrap;font-size:13px;transition:background .1s,color .1s}
.mdrop a:hover{background:#31485a;color:#fff}
.mdrop a.on{background:var(--acc);color:#fff}
.rightnav{margin-left:auto;display:flex;align-items:center;gap:14px}
.langsw{display:inline-flex;align-items:center;gap:4px;font-size:12px}
.langsw form{margin:0}
.langbtn{background:none;border:0;color:#9fb3c4;cursor:pointer;font-size:12px;padding:0 2px;font-weight:600}
.langbtn:hover{color:#fff;text-decoration:underline}
.langon{color:#fff;font-size:12px}
.langsep{color:#5f7385}
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
.kpi .v{font-size:23px;font-weight:800;line-height:1.05;letter-spacing:-.02em}.kpi .l{color:var(--mut);font-size:12px;margin-top:3px;font-weight:500}
.kpi .d{font-size:11.5px;font-weight:700;margin-top:2px}.kpi .d.up{color:var(--ok)}.kpi .d.down{color:var(--bad)}
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
/* BUTTON HIERARCHY — secondary (neutral outline) + danger (destructive red).
   Primary stays the default <button>/.btn blue above. */
button.btn-secondary,a.btn.btn-secondary,.btn-secondary{background:#fff;color:var(--ink);border:1px solid var(--line)}
button.btn-secondary:hover,a.btn.btn-secondary:hover,.btn-secondary:hover{background:#f4f7f9;border-color:#c4cfda;box-shadow:none}
button.btn-danger,a.btn.btn-danger,.btn-danger{background:var(--bad);color:#fff}
button.btn-danger:hover,a.btn.btn-danger:hover,.btn-danger:hover{background:#a50d26;box-shadow:0 1px 3px rgba(200,16,46,.3)}
/* SUBMIT / LOADING STATE — app.js adds .working on submit (anti double-submit). */
button.working,a.btn.working{opacity:.7;pointer-events:none;position:relative}
button.working::after,a.btn.working::after{content:"";display:inline-block;width:11px;height:11px;margin-left:7px;vertical-align:-1px;border:2px solid rgba(255,255,255,.5);border-top-color:#fff;border-radius:50%;animation:ffspin .6s linear infinite}
button.btn-secondary.working::after{border-color:rgba(26,39,51,.3);border-top-color:var(--ink)}
@keyframes ffspin{to{transform:rotate(360deg)}}
.note{color:var(--mut);font-size:12px;margin-top:8px;line-height:1.5}
/* MOBILE HELP TOGGLE — app.js injects a ".helptoggle" into cards that hold plain
   advisory .note help; on phones (≤640px) the help collapses and this little
   "ⓘ help" affordance reveals it. Hidden on desktop (everything shows as today). */
.helptoggle{display:none}
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
/* inline icon sizing helper */
.ic{font-style:normal;font-size:1.05em;line-height:1;margin-right:.3em;display:inline-block}
/* compact pill status badge */
.chip{display:inline-flex;align-items:center;gap:4px;font-size:11.5px;font-weight:600;line-height:1;padding:3px 9px;border-radius:999px;background:#eef2f6;color:var(--mut);border:1px solid var(--line);white-space:nowrap;vertical-align:middle}
.chip.ok{background:#e7f5ec;color:var(--ok);border-color:#bfe3cd}
.chip.warn{background:#fdf3e0;color:var(--warn);border-color:#f0d9a8}
.chip.bad{background:#fdeaec;color:var(--bad);border-color:#f4c6cd}
.chip.info{background:#eaf2fb;color:var(--info);border-color:#c6dcf3}
/* SEMANTIC STATUS CHIPS — fixed colour mapping. VAT claim stages: grey/blue early,
   amber in-progress, green filed/paid, red blocked (see _status_chip in app.py).
   Also supplier status (active/provisional) + confidence (high/med/low). */
.chip.s-neutral{background:#eef2f6;color:var(--mut);border-color:var(--line)}
.chip.s-early{background:#eaf2fb;color:var(--info);border-color:#c6dcf3}
.chip.s-progress{background:#fdf3e0;color:var(--warn);border-color:#f0d9a8}
.chip.s-done{background:#e7f5ec;color:var(--ok);border-color:#bfe3cd}
.chip.s-blocked{background:#fdeaec;color:var(--bad);border-color:#f4c6cd}
.chip-legend{display:flex;flex-wrap:wrap;gap:6px;align-items:center;font-size:11.5px;color:var(--mut);margin:0 0 10px}
.chip-legend .chip{font-size:11px;padding:2px 8px}
/* RIGHT-ALIGNED MONEY cells (alias of .r; semantic name for amounts) */
td.num,th.num{text-align:right;font-variant-numeric:tabular-nums}
/* ---- INTAKE REVIEW COCKPIT --------------------------------------------------
   The high-traffic "verify the extracted draft" screen: a fields|PDF two-column
   layout (stacks on mobile), a live line-sum tie-out indicator, amber needs-check
   highlights and a "why can't I file this?" checklist. */
.rvk{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:16px;align-items:start}
.rvk-fields{min-width:0}
.rvk-src{min-width:0;position:sticky;top:calc(var(--navh,56px) + 8px)}
.rvk-src object,.rvk-src embed{width:100%;height:78vh;min-height:420px;border:1px solid var(--line);border-radius:var(--radius);background:#f7f9fb}
.rvk-src .rvk-srchead{font-size:12px;color:var(--mut);margin:0 0 6px}
@media (max-width:900px){.rvk{grid-template-columns:1fr}.rvk-src{position:static}.rvk-src object,.rvk-src embed{height:60vh}}
/* amber "verify this" highlight on a low-confidence / empty-required field */
.needs-check{background:#fdf6e3;outline:1px solid #f0d9a8;border-radius:4px}
input.needs-check,select.needs-check{border-color:#d9a514;box-shadow:0 0 0 2px rgba(217,165,20,.14)}
.field-hint{display:block;font-size:11px;color:var(--warn);margin-top:2px}
/* live tie-out indicator (JS-driven; server renders a neutral placeholder) */
.tieout{display:inline-flex;align-items:center;gap:6px;font-size:13px;font-weight:600;padding:4px 11px;border-radius:999px;border:1px solid var(--line);background:#fff;color:var(--mut)}
.tieout.ok{background:#e7f5ec;color:var(--ok);border-color:#bfe3cd}
.tieout.bad{background:#fdeaec;color:var(--bad);border-color:#f4c6cd}
.tieout .tdot{width:8px;height:8px;border-radius:50%;background:currentColor;flex:none}
/* "why can't I file this?" checklist near Confirm */
.filelist{margin:10px 0 0;padding:12px 14px;border:1px solid var(--line);border-radius:var(--radius);background:#fff}
.filelist.ready{background:#e7f5ec;border-color:#bfe3cd}
.filelist.blocked{background:#fdeaec;border-color:#f4c6cd}
.filelist .flh{font-weight:700;font-size:13.5px;margin:0 0 2px}
.filelist ul{margin:6px 0 0 18px;font-size:13px}
.filelist li.blk{color:var(--bad)}
button[disabled].btn,button.btn:disabled{opacity:.55;cursor:not-allowed;pointer-events:none}
/* EMPTY STATE — shown instead of an empty table/list */
.empty{display:flex;flex-direction:column;align-items:center;text-align:center;gap:8px;background:#fff;border:1px dashed var(--line);border-radius:var(--radius);padding:34px 24px;color:var(--mut);box-shadow:var(--card-sh)}
.empty .eic{font-size:38px;line-height:1;opacity:.85}
.empty .et{font-size:15px;font-weight:700;color:var(--ink)}
.empty .ed{font-size:13px;max-width:420px}
.empty .btn{margin-top:6px}
/* TOAST NOTIFICATIONS — transient action feedback (server drives via #flash[data-toast]).
   Persistent integrity/error BANNERS stay as banners; toasts never replace them. */
#toasts{position:fixed;right:16px;bottom:16px;z-index:60;display:flex;flex-direction:column;gap:8px;max-width:340px}
.toast{background:#223240;color:#fff;border-radius:9px;padding:11px 14px 11px 13px;font-size:13.5px;font-weight:500;box-shadow:0 6px 24px rgba(10,20,30,.32);display:flex;align-items:flex-start;gap:9px;border-left:4px solid #6db1e8;animation:fftoastin .18s ease-out}
.toast.success{border-left-color:#36c08a}.toast.error{border-left-color:#ff6b7d}.toast.info{border-left-color:#6db1e8}
.toast .tx{font-size:15px;line-height:1.2}
.toast.leaving{animation:fftoastout .2s ease-in forwards}
@keyframes fftoastin{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:none}}
@keyframes fftoastout{to{opacity:0;transform:translateY(8px)}}
@media (max-width:640px){ #toasts{left:12px;right:12px;max-width:none;bottom:12px}}
@media print{ #toasts{display:none!important}}
/* landing: responsive grid of icon tiles */
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:14px;margin-bottom:20px}
.tile{display:flex;flex-direction:column;align-items:center;text-align:center;gap:6px;background:#fff;border:1px solid var(--line);border-radius:var(--radius);padding:22px 16px 18px;box-shadow:var(--card-sh);text-decoration:none;color:inherit;transition:border-color .12s,box-shadow .14s,transform .08s}
.tile:hover{border-color:#bcd3ec;box-shadow:0 4px 16px rgba(14,95,168,.13);transform:translateY(-3px)}
.tile .tic{font-size:34px;line-height:1;margin-bottom:2px}
.tile .tt{font-size:14.5px;font-weight:700;color:var(--ink);letter-spacing:-.01em}
.tile .td{font-size:12px;color:var(--mut);line-height:1.45}
.tile:hover .tt{color:var(--acc)}
@media (max-width:760px){.tiles{grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px}.tile{padding:18px 12px 14px}}
/* ---- home "needs attention" ACTION tiles (count + label + click-through) -- */
.atiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:13px;margin-bottom:18px}
.atile{display:flex;flex-direction:column;gap:5px;background:#fff;border:1px solid var(--line);border-left:4px solid var(--line);border-radius:var(--radius);padding:15px 16px 14px;box-shadow:var(--card-sh);text-decoration:none;color:inherit;transition:border-color .12s,box-shadow .14s,transform .08s}
.atile:hover{box-shadow:0 4px 16px rgba(14,95,168,.13);transform:translateY(-2px)}
.atile .an{font-size:27px;font-weight:800;line-height:1.02;letter-spacing:-.02em;color:var(--ink)}
.atile .al{font-size:12.5px;color:var(--mut);font-weight:500;line-height:1.35}
.atile .ag{margin-top:auto;font-size:11.5px;font-weight:600;color:var(--acc)}
.atile.act{border-left-color:var(--warn);background:#fffdf7}.atile.act .an{color:#9a6a06}
.atile.alert{border-left-color:var(--bad);background:#fff9f9}.atile.alert .an{color:var(--bad)}
.atile.clear{border-left-color:var(--ok)}.atile.clear .an{color:var(--ok);font-size:20px}
.atile.dim{border-left-color:var(--line)}.atile.dim .an{color:var(--mut);font-size:20px}
@media (max-width:480px){.atiles{grid-template-columns:1fr 1fr}}
/* ---- mobile / touch (phones, 360-640px) ---------------------------------- */
@media (max-width:640px){
  main{margin:14px auto;padding:0 12px}
  /* header: brand + items wrap into tappable rows; rightnav (sign out) stays reachable */
  header{gap:8px 14px;padding:9px 12px;row-gap:6px}
  header b{font-size:15px;width:100%;margin-right:0}
  header>a,.mlabel{padding:6px 2px;font-size:14px}
  .rightnav{margin-left:auto;gap:10px}
  /* dropdowns drop to a full-width, comfortably-tappable panel */
  .menu{position:static}
  .menu .mdrop{position:absolute;left:0;right:0;width:100%;padding-top:4px}
  .menu.open .mdrop>span{min-width:0;width:100%;padding:8px}
  .mdrop a{padding:11px 12px;font-size:14px}
  /* forms stack full-width; inputs sized for thumbs + 16px to stop iOS zoom-on-focus */
  form.f{flex-direction:column;align-items:stretch;gap:12px}
  form.f label{width:100%}
  select,input,.rowfilter{width:100%;min-height:42px;font-size:16px}
  form.f label select,form.f label input{width:100%}
  button,a.btn{min-height:42px;font-size:15px}
  form.f button,form.f a.btn{width:100%}
  /* wide tables: keep the page from sideways-scrolling — each table scrolls itself */
  .tablewrap{-webkit-overflow-scrolling:touch}
  table{display:block;overflow-x:auto;white-space:nowrap;-webkit-overflow-scrolling:touch}
  /* badges wrap rather than overflow */
  .chip{white-space:normal}
  /* dense admin forms: long checkbox/permission/2FA labels (inline display:flex with no
     flex-wrap) must wrap instead of forcing the page wider than the iPhone viewport */
  label.chk{flex-wrap:wrap}
  /* belt-and-braces: a card never spills sideways (its tables scroll themselves above) */
  .card{overflow-x:hidden}
  .card,.section{overflow-wrap:anywhere}
  .subnav{padding:8px 10px}
  .khgrid{grid-template-columns:1fr}
  /* MOBILE DECLUTTER — collapse plain advisory help so controls come first.
     app.js marks each card that holds collapsible help with `.has-help` and
     injects a `.helptoggle`. Plain `.note` help hides by default and reveals
     only when the card carries `.help-open`. STATUS notes (.note.bad / .note.ok)
     and any banner/alert ALWAYS stay visible — never collapsed. Cards with NO
     collapsible help are untouched (no `.has-help`, no toggle). With JS off no
     card gets `.has-help`, so this whole block is inert and help shows (the
     `.has-help` gate is the progressive-enhancement fallback). */
  .has-help>.note:not(.bad):not(.ok),
  .has-help .note:not(.bad):not(.ok){display:none}
  .has-help.help-open>.note:not(.bad):not(.ok),
  .has-help.help-open .note:not(.bad):not(.ok){display:block}
  .helptoggle{display:inline-flex;align-items:center;gap:5px;margin:6px 0 2px;
    padding:5px 11px;border:1px solid var(--line);border-radius:999px;background:#fff;
    color:var(--mut);font-size:12.5px;font-weight:600;line-height:1;cursor:pointer;
    -webkit-appearance:none;min-height:0}
  .helptoggle:hover{background:#f4f7f9;border-color:#c4cfda;box-shadow:none}
  .helptoggle .hti{font-style:normal;font-weight:700;color:var(--acc)}
  .has-help.help-open>.helptoggle,.has-help.help-open .helptoggle{
    background:#eef5fc;border-color:#bcd3ec;color:var(--acc)}
}
@media (max-width:480px){
  main{padding:0 10px}
  .kpis,.kpis.metrics,.kpis.status{grid-template-columns:1fr}
  .tiles{grid-template-columns:1fr 1fr}
}
</style></head><body>
<header><b>🚛 ⛽ Fleet Fuel</b>
<a href="/" class="{{'on' if page=='home'}}"><span class="ic">🏠</span>{{ t('Home') }}</a>
{% if 'intake' in modules and 'data_import' in perms %}<div class="menu" tabindex="0"><span class="mlabel {{'on' if page in ['ext','queue','imp','fil','min','eml'] else ''}}"><span class="ic">📥</span>{{ t('Intake') }}</span><div class="mdrop"><span>
  <a href="/extract" class="{{'on' if page=='ext'}}">Import batch</a>
  <a href="/queue" class="{{'on' if page=='queue'}}">Waiting room</a>
  {% if is_admin %}<a href="/email-intake" class="{{'on' if page=='eml'}}">Email inbox</a>{% endif %}
  <a href="/imports" class="{{'on' if page=='imp'}}">Import log</a>
  <a href="/files" class="{{'on' if page=='fil'}}">File archive</a>
  <a href="/mining" class="{{'on' if page=='min'}}">Doc mining</a>
</span></div></div>{% endif %}
{% if 'compliance' in modules and ('invoice_control' in perms or 'documents' in perms) %}<div class="menu" tabindex="0"><span class="mlabel {{'on' if page in ['doc','srch','esign','inv','con'] else ''}}"><span class="ic">📄</span>{{ t('Documents') }}</span><div class="mdrop"><span>
  {% if 'documents' in perms %}<a href="/documents" class="{{'on' if page=='doc'}}">Documents</a>
  <a href="/search" class="{{'on' if page=='srch'}}">Search</a>
  <a href="/esign" class="{{'on' if page=='esign'}}">E-signatures</a>{% endif %}
  {% if 'invoice_control' in perms %}<a href="/invoices" class="{{'on' if page=='inv'}}">Invoice control</a>
  <a href="/contracts" class="{{'on' if page=='con'}}">Contract audit</a>{% endif %}
</span></div></div>{% endif %}
{% if 'sharing' in modules and 'share' in perms %}<div class="menu" tabindex="0"><span class="mlabel {{'on' if page in ['shr','rooms'] else ''}}"><span class="ic">🔗</span>{{ t('Sharing') }}</span><div class="mdrop"><span>
  <a href="/share" class="{{'on' if page=='shr'}}">Share links</a>
  <a href="/rooms" class="{{'on' if page=='rooms'}}">Data rooms</a>
</span></div></div>{% endif %}
{% if 'analytics' in modules %}<div class="menu" tabindex="0"><span class="mlabel {{'on' if page in ['ana','rep','sav','exp','int','cmp','txn','h2h','stn','ano','pri','rel'] else ''}}"><span class="ic">📊</span>{{ t('Analytics') }}</span><div class="mdrop"><span>
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
<div class="menu" tabindex="0"><span class="mlabel {{'on' if page in ['ent','vat','rdy','rec','rcv','fin','rcn','fx','rcd','ovc','ent2'] else ''}}"><span class="ic">💶</span>{{ t('VAT & Recovery') }}</span><div class="mdrop"><span>
  <a href="/entities" class="{{'on' if page=='ent'}}">Entities &amp; VAT</a>
  {% if is_admin and 'vat' in modules %}<a href="/recovery-dashboard" class="{{'on' if page=='rcd'}}">💶 Recovery dashboard</a>
  <a href="/vat" class="{{'on' if page=='vat'}}">VAT refunds</a>
  <a href="/readiness" class="{{'on' if page=='rdy'}}">Claims readiness</a>
  <a href="/overcharges" class="{{'on' if page=='ovc'}}">Overcharge claim-back</a>
  <a href="/entitlement" class="{{'on' if page=='ent2'}}">VAT recoverability</a>
  <a href="/recovery" class="{{'on' if page=='rec'}}">Recovery &amp; fees</a>
  <a href="/receivables" class="{{'on' if page=='rcv'}}">Receivables &amp; forecast</a>
  <a href="/financing" class="{{'on' if page=='fin'}}">Embedded finance</a>
  <a href="/recon" class="{{'on' if page=='rcn'}}">Bank reconciliation</a>{% endif %}
  {% if 'fx' in modules %}<a href="/fx" class="{{'on' if page=='fx'}}">FX vs ECB</a>{% endif %}
</span></div></div>
<div class="menu" tabindex="0"><span class="mlabel {{'on' if page in ['sup','cus','dreq','dat'] else ''}}"><span class="ic">🗂️</span>{{ t('Master data') }}</span><div class="mdrop"><span>
  <a href="/suppliers" class="{{'on' if page=='sup'}}">Suppliers</a>
  {% if is_admin %}<a href="/customers" class="{{'on' if page=='cus'}}">Customers (CRM)</a>{% endif %}
  {% if is_admin %}<a href="/doc-requests" class="{{'on' if page=='dreq'}}">Document requests</a>{% endif %}
  {% if 'data_import' in perms %}<a href="/data" class="{{'on' if page=='dat'}}">Data manager</a>{% endif %}
</span></div></div>
{% if is_admin and 'invoicing' in modules %}<div class="menu" tabindex="0"><span class="mlabel {{'on' if page in ['ivc','ivcc','ivci','ivcr','ivcrec'] else ''}}"><span class="ic">🧾</span>{{ t('Invoicing') }}</span><div class="mdrop"><span>
  <a href="/invoicing" class="{{'on' if page=='ivc'}}">{{ t('Invoices') }}</a>
  <a href="/invoicing/recurring" class="{{'on' if page=='ivcrec'}}">{{ t('Recurring invoices') }}</a>
  <a href="/invoicing/customers" class="{{'on' if page=='ivcc'}}">{{ t('Customer book') }}</a>
  <a href="/invoicing/issuer" class="{{'on' if page=='ivci'}}">{{ t('Issuer profile') }}</a>
  <a href="/invoicing/reports" class="{{'on' if page=='ivcr'}}">{{ t('Invoicing reports') }}</a>
</span></div></div>{% endif %}
<a href="/history" class="{{'on' if page=='his'}}"><span class="ic">🕘</span>{{ t('History') }}</a>
{% if 'workflow' in modules %}<div class="menu" tabindex="0"><span class="mlabel {{'on' if page in ['tasks','wfadm'] else ''}}"><span class="ic">✅</span>{{ t('Tasks') }}</span><div class="mdrop"><span>
  <a href="/tasks" class="{{'on' if page=='tasks'}}">My tasks &amp; approvals</a>
  {% if is_admin %}<a href="/workflows" class="{{'on' if page=='wfadm'}}">Manage workflows</a>{% endif %}
</span></div></div>{% endif %}
<span class="rightnav">
{% if 'exports' in perms %}<div class="menu" tabindex="0"><span class="mlabel"><span class="ic">⬇️</span>{{ t('Export') }}</span><div class="mdrop"><span>
  <a href="/export/summary">Summary report</a><a href="/export/master">Master workbook</a><a href="/export/history">History report</a>
  {% if 'analytics' in modules %}<a href="/exports">Accounting &amp; ERP exports</a>{% endif %}
</span></div></div>{% endif %}
{% if role == 'admin' %}<a href="/close" class="{{'on' if page=='close'}}"><span class="ic">🔒</span>{{ t('Monthly close') }}</a>
<a href="/admin" class="{{'on' if page=='adm'}}"><span class="ic">⚙️</span>{{ t('Admin') }}</a>{% endif %}
<span class="note" style="color:#9fb3c4">{{ user }} ({{ role }})</span>
<a href="/account">{{ t('Account') }}</a>
<a href="/logout">{{ t('Sign out') }}</a>
<span class="langsw">{% for code in langs %}{% if code == lang %}<b class="langon">{{ lang_labels[code] }}</b>{% else %}<form method="post" action="/lang/{{ code }}" style="display:inline">{{ csrf_field|safe }}<button class="langbtn" type="submit">{{ lang_labels[code] }}</button></form>{% endif %}{% if not loop.last %}<span class="langsep">|</span>{% endif %}{% endfor %}</span>
</span>
</header><main>{{ body|safe }}</main><div id="toasts" aria-live="polite"></div><script src="/app.js" defer></script></body></html>"""

_BASE_TMPL = None   # compiled once; render_template_string would recompile per call
def page(body, p):
    global _BASE_TMPL
    _require_config()
    if _BASE_TMPL is None:
        _BASE_TMPL = _app.jinja_env.from_string(BASE)
    role = session.get("role", "processor")
    # i18n: pass the translator + the active language + the switch context. `lang` is
    # 'en' by default (the source language) so the chrome renders byte-identically until a
    # user switches to Latvian. `csrf_field` carries the hidden CSRF input for the POST
    # switch (only meaningful once a session token exists; harmless empty otherwise).
    import i18n
    return _BASE_TMPL.render(body=body, page=p,
                             user=session.get("user", ""), role=role, is_admin=(role == "admin"),
                             modules=_enabled_modules() if session.get("user") else set(),
                             perms=_auth.permissions_for(role) if session.get("user") else set(),
                             t=i18n.t, lang=i18n.current_lang(), langs=i18n.LANGS,
                             lang_labels=i18n.LANG_LABELS, csrf_field=_csrf_input())

def _review_page(body, p):
    """Render an intake-review page like page(), but with a CSP that permits the
    same-origin PDF SOURCE pane: the global policy is object-src 'none', so the
    review cockpit's `<object data="/extract/review/pdf/…">` embed needs object-src
    'self' (and frame-src 'self' for browsers that promote the PDF object to a frame).
    Everything else stays identical (script-src 'self', no inline script)."""
    from flask import Response
    resp = Response(page(body, p))
    resp.headers["Content-Security-Policy"] = (
        "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
        "script-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'; "
        "object-src 'self'; frame-src 'self'")
    return resp


def tbl(headers, rows, sortable=False):
    h = "".join(f"<th>{x}</th>" for x in headers)
    b = "".join("<tr>" + "".join(r) + "</tr>" for r in rows)
    cls = ' class="sortable"' if sortable else ""
    return f"<table{cls}><thead><tr>{h}</tr></thead><tbody>{b}</tbody></table>"


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
