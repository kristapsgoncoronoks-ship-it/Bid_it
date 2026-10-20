import http from "node:http";
import https from "node:https";

// These belong to one HTTP connection, not to the forwarded message. In
// particular Node must generate the framing for each side of a chunked body.
function endToEnd(headers) {
  const copy = { ...headers };
  const connection = String(copy.connection ?? "").split(",");
  for (const name of [
    ...connection, "connection", "keep-alive", "proxy-authenticate",
    "proxy-authorization", "te", "trailer", "transfer-encoding", "upgrade",
  ]) delete copy[name.trim().toLowerCase()];
  return copy;
}

/**
 * Vite's ordinary proxy can lose an early upload refusal to a write EPIPE.
 * Use the standard Expect handshake only for multipart / inbound JSON uploads:
 * Uvicorn sends Continue on its first receive(), after the ingress checks.
 * Never buffer or drain a rejected body. Other API traffic stays in Vite.
 */
export function createUploadProxy(target, { continueTimeoutMs = 10_000 } = {}) {
  const origin = new URL(target);
  if (!["http:", "https:"].includes(origin.protocol)) {
    throw new Error("The API development proxy requires HTTP or HTTPS.");
  }
  const transport = origin.protocol === "https:" ? https : http;
  return (req, res, next) => {
    const path = (req.url ?? "").split("?")[0];
    const multipart = /^multipart\/form-data(?:\s*;|\s*$)/i.test(req.headers["content-type"] ?? "");
    if (req.method !== "POST" || !/^\/api(?:\/|$)/.test(path)
      || !(multipart || /\/email\/inbound\/?$/.test(path))) return next();

    let response;
    let finished = false;
    const headers = endToEnd(req.headers);
    headers.host = origin.host; // Same changeOrigin behavior as the ordinary proxy.
    headers.expect = "100-continue";
    // A refusal can finish without the declared body ever being sent. That
    // upstream connection cannot safely carry the next request: Uvicorn may
    // still be awaiting those bytes. Give every upload its own connection.
    headers.connection = "close";
    const outgoing = transport.request(origin, {
      method: req.method,
      path: origin.pathname.replace(/\/$/, "") + req.url,
      headers,
    });
    const timer = setTimeout(() => fail(504, "API upload handshake timed out. Check upload status before retrying."), continueTimeoutMs);
    timer.unref();

    function stopBody() {
      req.unpipe(outgoing);
      req.pause();
    }
    function fail(status, detail) {
      if (finished || response) return;
      finished = true;
      clearTimeout(timer);
      stopBody();
      outgoing.destroy();
      // Close after flushing the error; do not consume an unbounded rejected
      // client stream to keep this development connection alive.
      res.writeHead(status, {
        "Content-Type": "application/json",
        "Cache-Control": "no-store",
        Connection: "close",
      });
      res.end(JSON.stringify({ code: "development_proxy_unavailable", detail }));
    }
    function abort() {
      finished = true;
      clearTimeout(timer);
      stopBody();
      outgoing.destroy();
      response?.destroy();
    }
    req.once("aborted", abort);
    req.once("error", abort);
    res.once("close", () => { if (!res.writableFinished) abort(); });
    outgoing.once("continue", () => {
      clearTimeout(timer);
      if (!response && !finished) req.pipe(outgoing);
    });
    outgoing.on("error", () => {
      // A known upstream response owns its own error/aborted handling below.
      // Never overwrite its status with a later request-side write failure.
      fail(502, "API connection failed. Check upload status before retrying.");
    });
    outgoing.once("response", (incoming) => {
      clearTimeout(timer);
      if (finished) { incoming.destroy(); return; }
      response = incoming;
      stopBody();
      const responseHeaders = endToEnd(incoming.headers);
      if (!req.readableEnded) responseHeaders.connection = "close";
      // Native HTTP streams preserve compressed/binary bytes, repeated cookies
      // and redirect status/Location, with backpressure and no fetch decoding.
      res.writeHead(incoming.statusCode, responseHeaders);
      incoming.once("error", () => res.destroy());
      incoming.once("aborted", () => res.destroy());
      incoming.pipe(res);
      if (!outgoing.writableEnded) outgoing.end();
    });
    // Sending headers alone is intentional: pipe only after the upstream's
    // Continue. There is no eager body forwarding or timer-based fallback.
    outgoing.flushHeaders();
  };
}
