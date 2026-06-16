/* B3 — page-by-page share viewer.
 *
 * A vanilla ES-module (no framework) that renders a vaulted PDF with pdf.js into
 * stacked <canvas> pages, tracks which page is in view + how long the viewer dwells
 * on it, and POSTs compact engagement beacons to /s/<token>/event. Everything ships
 * from this static file so the page satisfies `script-src 'self'` (NO inline JS).
 *
 * The viewer page (app.py share_public) carries a SCOPED CSP that allows the pdf.js
 * worker + wasm; every other page keeps the strict global policy. The element this
 * script needs is a <div id="pdf-root" data-token="..." data-file="..."> placeholder.
 *
 * Beacons are best-effort: navigator.sendBeacon where available (survives unload),
 * a keepalive fetch fallback otherwise. We flush on visibility-change and pagehide so
 * a closed tab still reports its final dwell. The server re-runs the per-token gate on
 * every beacon and clamps page/dwell — this client is purely advisory.
 */
import * as pdfjsLib from "./vendor/pdfjs/build/pdf.min.mjs";

(function () {
  "use strict";

  var root = document.getElementById("pdf-root");
  if (!root) { return; }
  var TOKEN = root.getAttribute("data-token") || "";
  var FILE_URL = root.getAttribute("data-file") || "";
  var EVENT_URL = "/s/" + encodeURIComponent(TOKEN) + "/event";
  if (!TOKEN || !FILE_URL) { return; }

  // Self-hosted worker — same origin, satisfies worker-src 'self'.
  pdfjsLib.GlobalWorkerOptions.workerSrc = "./vendor/pdfjs/build/pdf.worker.min.mjs";

  // Per-page dwell accounting. `current` is the page (1-based) presently "in view";
  // `since` is the ms timestamp it became current. We accumulate dwell into `pending`
  // and flush in batches. Absurd dwell is clamped server-side too.
  var current = 0;
  var since = 0;
  var pending = {};          // page_number -> accumulated ms not yet flushed
  var totalPages = 0;
  var FLUSH_MS = 5000;       // periodic flush cadence
  var MAX_DWELL = 30 * 60 * 1000;   // ignore single spans > 30 min (tab left open)

  function now() { return Date.now(); }

  function accrue() {
    // Bank the dwell on the page we are leaving (or refreshing) into `pending`.
    if (current >= 1 && since > 0) {
      var ms = now() - since;
      if (ms > 0 && ms <= MAX_DWELL) {
        pending[current] = (pending[current] || 0) + ms;
      }
      since = now();
    }
  }

  function setCurrent(page) {
    if (page === current) { return; }
    accrue();
    current = page;
    since = now();
  }

  function buildBatch(includeCurrent) {
    if (includeCurrent) { accrue(); }
    var batch = [];
    for (var p in pending) {
      if (Object.prototype.hasOwnProperty.call(pending, p)) {
        var ms = Math.round(pending[p]);
        if (ms > 0) { batch.push({ page: parseInt(p, 10), dwell_ms: ms }); }
      }
    }
    return batch;
  }

  function send(batch, useBeacon) {
    if (!batch.length) { return; }
    var payload = JSON.stringify({ token: TOKEN, pages: batch });
    var ok = false;
    if (useBeacon && navigator.sendBeacon) {
      try {
        ok = navigator.sendBeacon(EVENT_URL,
          new Blob([payload], { type: "application/json" }));
      } catch (e) { ok = false; }
    }
    if (!ok) {
      try {
        fetch(EVENT_URL, {
          method: "POST", body: payload, keepalive: true,
          headers: { "Content-Type": "application/json" }
        });
        ok = true;
      } catch (e) { ok = false; }
    }
    if (ok) { pending = {}; }   // only clear what we believe shipped
  }

  function flush(useBeacon) {
    var batch = buildBatch(true);
    send(batch, useBeacon);
  }

  // ---- render ----
  function renderPage(pdf, num, container) {
    return pdf.getPage(num).then(function (page) {
      var scale = 1.3;
      var viewport = page.getViewport({ scale: scale });
      var canvas = document.createElement("canvas");
      canvas.className = "pdf-page";
      canvas.setAttribute("data-page", String(num));
      var ctx = canvas.getContext("2d");
      var ratio = window.devicePixelRatio || 1;
      canvas.width = Math.floor(viewport.width * ratio);
      canvas.height = Math.floor(viewport.height * ratio);
      canvas.style.width = Math.floor(viewport.width) + "px";
      canvas.style.height = Math.floor(viewport.height) + "px";
      var wrap = document.createElement("div");
      wrap.className = "pdf-page-wrap";
      var label = document.createElement("div");
      label.className = "pdf-page-label";
      label.textContent = "Page " + num;
      wrap.appendChild(label);
      wrap.appendChild(canvas);
      container.appendChild(wrap);
      return page.render({
        canvasContext: ctx, viewport: viewport,
        transform: ratio !== 1 ? [ratio, 0, 0, ratio, 0, 0] : null
      }).promise.then(function () { return canvas; });
    });
  }

  function trackVisibility(canvases) {
    // The page whose canvas covers the most of the viewport is "current".
    if (!("IntersectionObserver" in window)) {
      // Fallback: treat page 1 as current.
      if (canvases.length) { setCurrent(1); }
      return;
    }
    var ratios = {};
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (en) {
        var p = parseInt(en.target.getAttribute("data-page"), 10);
        ratios[p] = en.isIntersecting ? en.intersectionRatio : 0;
      });
      var best = 0, bestR = 0;
      for (var p in ratios) {
        if (ratios[p] > bestR) { bestR = ratios[p]; best = parseInt(p, 10); }
      }
      if (best >= 1) { setCurrent(best); }
    }, { threshold: [0, 0.25, 0.5, 0.75, 1] });
    canvases.forEach(function (c) { io.observe(c); });
  }

  function showError(msg) {
    var e = document.createElement("div");
    e.className = "pdf-error";
    e.textContent = msg;
    root.appendChild(e);
  }

  pdfjsLib.getDocument({ url: FILE_URL }).promise.then(function (pdf) {
    totalPages = pdf.numPages;
    root.setAttribute("data-pages", String(totalPages));
    var chain = Promise.resolve();
    var canvases = [];
    var i;
    for (i = 1; i <= totalPages; i++) {
      (function (n) {
        chain = chain.then(function () {
          return renderPage(pdf, n, root).then(function (c) { canvases.push(c); });
        });
      })(i);
    }
    return chain.then(function () {
      trackVisibility(canvases);
      setCurrent(1);
    });
  }).catch(function (err) {
    showError("This document could not be displayed.");
    try { console.error("pdf load failed", err); } catch (e) {}
  });

  // periodic + lifecycle flushing
  setInterval(function () { flush(false); }, FLUSH_MS);
  document.addEventListener("visibilitychange", function () {
    if (document.visibilityState === "hidden") { flush(true); }
  });
  window.addEventListener("pagehide", function () { flush(true); });
  window.addEventListener("beforeunload", function () { flush(true); });
})();
