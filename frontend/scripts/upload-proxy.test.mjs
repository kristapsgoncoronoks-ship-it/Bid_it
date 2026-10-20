import assert from "node:assert/strict";
import { once } from "node:events";
import http from "node:http";
import https from "node:https";
import { execFileSync } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { gzipSync } from "node:zlib";
import { after, before, test } from "node:test";
import { createServer } from "vite";

let upstream;
let vite;
let base;
const received = [];
const refused = [];
const compressed = gzipSync(Buffer.from("synthetic binary download\0"));
let releaseStream;
let abandoned;

before(async () => {
  function handle(req, res) {
    const url = new URL(req.url, "http://fixture.invalid");
    const refusal = Number(url.searchParams.get("refuse"));
    if (refusal) {
      const record = { bytes: 0, expect: req.headers.expect, connection: req.headers.connection };
      refused.push(record);
      req.on("data", (chunk) => { record.bytes += chunk.length; });
      res.writeHead(refusal, {
        "Content-Type": "application/json",
        "Cache-Control": "no-store",
        "Retry-After": "1",
        Connection: "close",
      });
      res.end(JSON.stringify({ detail: "fixture refusal", code: "upload_busy" }));
      return;
    }
    if (url.searchParams.has("hang")) {
      abandoned?.(req);
      return;
    }
    if (req.headers.expect) res.writeContinue();
    let bytes = 0;
    req.on("data", (chunk) => {
      bytes += chunk.length;
      if (url.searchParams.has("midstream") && !res.headersSent) {
        res.writeHead(413, {
          "Content-Type": "application/json",
          ...(url.searchParams.has("close") && { Connection: "close" }),
        });
        res.write(JSON.stringify({ detail: "stream limit" }));
        if (url.searchParams.has("close")) res.end();
        // Otherwise leave the response open until the sender stops its body.
        // This proves preservation when response headers are actually received.
      }
    });
    req.on("end", () => {
      received.push({ bytes, url: req.url, headers: req.headers });
      if (res.headersSent) { res.end(); return; }
      if (url.searchParams.has("drop")) { req.socket.destroy(); return; }
      res.setHeader("Set-Cookie", ["first=1; HttpOnly", "second=2; HttpOnly"]);
      if (url.searchParams.has("redirect")) {
        res.writeHead(307, { Location: "/api/v1/synthetic-target?kept=1" });
        res.end(); return;
      }
      if (url.searchParams.has("gzip")) {
        res.writeHead(200, { "Content-Encoding": "gzip", "Content-Length": compressed.length });
        res.end(compressed); return;
      }
      if (url.searchParams.has("stream")) {
        res.writeHead(200, { "Content-Type": "application/octet-stream" });
        res.write("first");
        releaseStream = () => res.end("second");
        return;
      }
      if (url.searchParams.has("truncate")) {
        res.writeHead(200, { "Content-Length": 1000 });
        res.write("prefix");
        setImmediate(() => res.destroy()); return;
      }
      res.end(JSON.stringify({ bytes, key: req.headers["idempotency-key"] }));
    });
  }
  upstream = http.createServer(handle);
  upstream.on("checkContinue", handle);
  upstream.listen(0, "127.0.0.1");
  await once(upstream, "listening");
  process.env.VITE_API_PROXY = `http://127.0.0.1:${upstream.address().port}`;
  vite = await createServer({ server: { host: "127.0.0.1", port: 0 }, logLevel: "silent" });
  await vite.listen();
  base = `http://127.0.0.1:${vite.httpServer.address().port}`;
});

after(async () => {
  await vite?.close();
  upstream?.closeAllConnections();
  await new Promise((resolve) => upstream?.close(resolve) ?? resolve());
  delete process.env.VITE_API_PROXY;
});

function upload(path, { length = 16 * 1024 * 1024, chunked = false, contentType = "multipart/form-data; boundary=fixture", method = "POST" } = {}) {
  return new Promise((resolve, reject) => {
    const req = http.request(base + path, {
      method,
      headers: {
        "Content-Type": contentType,
        "Idempotency-Key": "synthetic-command",
        "Authorization": "Bearer synthetic-proxy-test",
        "Cookie": "synthetic=1",
        "X-Expected-Org-Id": "synthetic-workspace",
        ...(!chunked && { "Content-Length": length }),
      },
    });
    req.on("response", async (res) => {
      try {
        const chunks = [];
        for await (const chunk of res) chunks.push(chunk);
        resolve({ status: res.statusCode, headers: res.headers, body: Buffer.concat(chunks) });
      } catch (error) { reject(error); }
    });
    // A refusing server may close while the client is still sending. Keep its
    // real response authoritative, but fail a transport-only outcome promptly.
    req.on("error", (error) => { if (!req.res) reject(error); });
    let sent = 0;
    function write() {
      while (sent < length && !req.destroyed && !req.res) {
        const size = Math.min(64 * 1024, length - sent);
        sent += size;
        if (!req.write(Buffer.alloc(size, 120))) { req.once("drain", write); return; }
      }
      if (!req.destroyed && !req.res) req.end();
    }
    write();
  });
}

for (const status of [413, 503]) {
  for (const chunked of [false, true]) {
    test(`early ${status}, ${chunked ? "chunked" : "declared"}, retains API outcome`, { timeout: 20_000 }, async () => {
      for (let attempt = 0; attempt < 5; attempt++) {
        const result = await upload(`/api/v1/invoices/upload?refuse=${status}`, { chunked });
        assert.equal(result.status, status);
        assert.equal(JSON.parse(result.body).detail, "fixture refusal");
        assert.equal(result.headers["cache-control"], "no-store");
        assert.equal(result.headers["retry-after"], "1");
        assert.equal(refused.at(-1).expect, "100-continue");
        assert.equal(refused.at(-1).bytes, 0);
        assert.equal(refused.at(-1).connection, "close");
      }
    });
  }
}

test("accepted upload forwards exact bytes/key and multiple cookies", { timeout: 20_000 }, async () => {
  const result = await upload("/api/v1/invoices/upload?accepted=1", { length: 3 * 1024 * 1024 });
  assert.equal(result.status, 200);
  assert.deepEqual(JSON.parse(result.body), { bytes: 3 * 1024 * 1024, key: "synthetic-command" });
  assert.deepEqual(result.headers["set-cookie"], ["first=1; HttpOnly", "second=2; HttpOnly"]);
  assert.equal(received.at(-1).url, "/api/v1/invoices/upload?accepted=1");
  assert.equal(received.at(-1).headers.authorization, "Bearer synthetic-proxy-test");
  assert.equal(received.at(-1).headers.cookie, "synthetic=1");
  assert.equal(received.at(-1).headers["x-expected-org-id"], "synthetic-workspace");
  assert.equal(received.at(-1).headers.host, `127.0.0.1:${upstream.address().port}`);
});

test("accepted chunked upload preserves exact bytes", async () => {
  const result = await upload("/api/v1/invoices/upload", { length: 2 * 1024 * 1024, chunked: true });
  assert.equal(result.status, 200);
  assert.equal(JSON.parse(result.body).bytes, 2 * 1024 * 1024);
});

test("inbound JSON also negotiates before forwarding", async () => {
  const result = await upload("/api/v1/email/inbound?refuse=413", { contentType: "application/json" });
  assert.equal(result.status, 413);
  assert.equal(refused.at(-1).expect, "100-continue");
  assert.equal(refused.at(-1).bytes, 0);
});

test("mid-stream size refusal keeps its actual status and body", async () => {
  const result = await upload("/api/v1/invoices/upload?midstream=1", { chunked: true });
  assert.equal(result.status, 413);
  assert.equal(JSON.parse(result.body).detail, "stream limit");
});

test("abrupt mid-stream close reports the known response or explicit uncertainty", async () => {
  // HTTP cannot recover a response that a transport error discarded before
  // headers were parsed. It must never infer an API refusal from EPIPE alone.
  const result = await upload("/api/v1/invoices/upload?midstream=1&close=1", { chunked: true });
  if (result.status === 413) assert.equal(JSON.parse(result.body).detail, "stream limit");
  else {
    assert.equal(result.status, 502);
    assert.equal(JSON.parse(result.body).code, "development_proxy_unavailable");
    assert.match(JSON.parse(result.body).detail, /Check upload status before retrying/);
  }
});

test("redirect status and Location pass through without following", async () => {
  const result = await upload("/api/v1/invoices/upload?redirect=1", { length: 100 });
  assert.equal(result.status, 307);
  assert.equal(result.headers.location, "/api/v1/synthetic-target?kept=1");
  assert.deepEqual(result.headers["set-cookie"], ["first=1; HttpOnly", "second=2; HttpOnly"]);
});

test("compressed download bytes and encoding/length stay consistent", async () => {
  const result = await upload("/api/v1/invoices/upload?gzip=1", { length: 100 });
  assert.equal(result.status, 200);
  assert.equal(result.headers["content-encoding"], "gzip");
  assert.equal(Number(result.headers["content-length"]), compressed.length);
  assert.deepEqual(result.body, compressed);
});

test("response streams before the upstream finishes", { timeout: 5000 }, async () => {
  const res = await new Promise((resolve, reject) => {
    const req = http.request(base + "/api/v1/invoices/upload?stream=1", {
      method: "POST", headers: { "Content-Type": "multipart/form-data", "Content-Length": 4 },
    }, resolve);
    req.on("error", reject);
    req.end("body");
  });
  const stream = res[Symbol.asyncIterator]();
  assert.equal((await stream.next()).value.toString(), "first");
  releaseStream();
  assert.equal((await stream.next()).value.toString(), "second");
  assert.equal((await stream.next()).done, true);
});

test("truncated upstream response fails instead of completing successfully", async () => {
  await assert.rejects(upload("/api/v1/invoices/upload?truncate=1", { length: 100 }));
});

test("ordinary JSON and GET download keep Vite's existing path", async () => {
  for (const method of ["POST", "GET"]) {
    const result = await upload("/api/v1/ordinary", { method, length: 100, contentType: "application/json" });
    assert.equal(result.status, 200);
    assert.equal(JSON.parse(result.body).bytes, 100);
    assert.equal(received.at(-1).headers.expect, undefined);
  }
  const result = await upload("/api/v1/download?gzip=1", { method: "GET", length: 0, contentType: "application/json" });
  assert.deepEqual(result.body, compressed);
});

test("transport-only failure is an explicit unknown outcome and retry keeps key", async () => {
  const result = await upload("/api/v1/invoices/upload?drop=1", { length: 100 });
  assert.equal(result.status, 502);
  assert.equal(JSON.parse(result.body).code, "development_proxy_unavailable");
  assert.match(JSON.parse(result.body).detail, /Check upload status before retrying/);
  assert.equal(result.headers["cache-control"], "no-store");
  const retry = await upload("/api/v1/invoices/upload", { length: 100 });
  assert.equal(retry.status, 200);
  assert.equal(JSON.parse(retry.body).key, "synthetic-command");
});

test("client cancellation closes a waiting upstream and recovers", { timeout: 5000 }, async () => {
  const observed = new Promise((resolve) => { abandoned = resolve; });
  const req = http.request(base + "/api/v1/invoices/upload?hang=1", {
    method: "POST", headers: { "Content-Type": "multipart/form-data", "Content-Length": 100 },
  });
  req.on("error", () => {});
  req.flushHeaders();
  const inbound = await observed;
  const closed = new Promise((resolve) => inbound.socket.once("close", resolve));
  req.destroy();
  await closed;
  abandoned = undefined;
  assert.equal((await upload("/api/v1/invoices/upload", { length: 100 })).status, 200);
});

test("missing Continue has a bounded truthful timeout", { timeout: 15_000 }, async () => {
  const result = await upload("/api/v1/invoices/upload?hang=1", { length: 100 });
  assert.equal(result.status, 504);
  assert.match(JSON.parse(result.body).detail, /handshake timed out/);
});

test("refused connection remains unknown and recovers after upstream restart", async () => {
  const port = upstream.address().port;
  upstream.closeAllConnections();
  await new Promise((resolve) => upstream.close(resolve));
  const result = await upload("/api/v1/invoices/upload", { length: 100 });
  assert.equal(result.status, 502);
  assert.equal(JSON.parse(result.body).code, "development_proxy_unavailable");
  upstream.listen(port, "127.0.0.1");
  await once(upstream, "listening");
  const retry = await upload("/api/v1/invoices/upload", { length: 100 });
  assert.equal(retry.status, 200);
  assert.equal(JSON.parse(retry.body).key, "synthetic-command");
});

test("HTTPS upstream retains certificate verification and accepts a trusted fixture CA", async () => {
  const dir = mkdtempSync(join(tmpdir(), "sys10-tls-"));
  const previousTarget = process.env.VITE_API_PROXY;
  const previousCA = https.globalAgent.options.ca;
  const previousBase = base;
  let tlsUpstream;
  let tlsVite;
  try {
    execFileSync("openssl", ["req", "-x509", "-newkey", "rsa:2048", "-nodes", "-sha256", "-days", "1",
      "-subj", "/CN=localhost", "-addext", "subjectAltName=IP:127.0.0.1", "-keyout", join(dir, "key.pem"), "-out", join(dir, "cert.pem")], { stdio: "ignore" });
    const cert = readFileSync(join(dir, "cert.pem"));
    tlsUpstream = https.createServer({ key: readFileSync(join(dir, "key.pem")), cert }, (req, res) => {
      let bytes = 0;
      req.on("data", (chunk) => { bytes += chunk.length; });
      req.on("end", () => res.end(JSON.stringify({ bytes })));
    });
    tlsUpstream.listen(0, "127.0.0.1");
    await once(tlsUpstream, "listening");
    process.env.VITE_API_PROXY = `https://127.0.0.1:${tlsUpstream.address().port}`;
    tlsVite = await createServer({ server: { host: "127.0.0.1", port: 0 }, logLevel: "silent" });
    await tlsVite.listen();
    base = `http://127.0.0.1:${tlsVite.httpServer.address().port}`;
    const untrusted = await upload("/api/v1/invoices/upload", { length: 100 });
    assert.equal(untrusted.status, 502);
    assert.equal(JSON.parse(untrusted.body).code, "development_proxy_unavailable");
    // Explicitly trust only this ephemeral test CA. Verification stays enabled.
    https.globalAgent.options.ca = cert;
    const trusted = await upload("/api/v1/invoices/upload", { length: 100 });
    assert.equal(trusted.status, 200);
    assert.equal(JSON.parse(trusted.body).bytes, 100);
  } finally {
    base = previousBase;
    https.globalAgent.options.ca = previousCA;
    process.env.VITE_API_PROXY = previousTarget;
    await tlsVite?.close();
    tlsUpstream?.closeAllConnections();
    if (tlsUpstream) await new Promise((resolve) => tlsUpstream.close(resolve));
    rmSync(dir, { recursive: true, force: true });
  }
});
