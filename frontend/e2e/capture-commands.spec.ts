import { test, expect, type Page, type Route } from "@playwright/test";

const USER = { id: "user-1", name: "Synthetic Owner", email: "synthetic@example.com", role: "owner", org_id: "org-1" };
const ORG = { id: "org-1", name: "Synthetic Commands", status: "active" };
const SLOT = "invoiceiq:capture-commands:user-1:org-1";
const content = "description,quantity,unit_price\nSynthetic source,1,100\n";
const json = (body: unknown, status = 200) => ({ status, contentType: "application/json", body: JSON.stringify(body) });
const result = (names: string[], ids: string[]) => ({ accepted: names.length, rejected: 0,
  outcomes: names.map((filename, i) => ({ filename, accepted: true, extraction_run_id: ids[i] })) });
function keys(route: Route): string[] {
  const body = route.request().postDataBuffer()?.toString() ?? "";
  const raw = body.match(/name="command_keys"\r\n\r\n([^\r]+)/)?.[1];
  expect(raw).toBeTruthy();
  return JSON.parse(raw!);
}
async function shell(page: Page, handler: (path: string, route: Route) => Promise<unknown>, maxBytes = 15728640, maxBatchBytes = maxBytes * 25) {
  await page.addInitScript(() => { localStorage.setItem("invoiceiq_token", "synthetic-token"); });
  await page.route("**/api/v1/**", async route => {
    const path = new URL(route.request().url()).pathname.replace(/^.*\/api\/v1/, "");
    if (path === "/auth/me") return route.fulfill(json({ user: USER, organization: ORG }));
    if (path === "/auth/organizations") return route.fulfill(json([ORG]));
    if (path === "/modules") return route.fulfill(json([]));
    if (path === "/invoices/upload-policy") return route.fulfill(json({ max_bytes: maxBytes, max_files: 25, max_batch_bytes: maxBatchBytes }));
    if (path === "/access/usage") return route.fulfill(json({ invoices_remaining: null }));
    if (await handler(path, route)) return;
    return route.fulfill(json({}));
  });
}
async function choose(page: Page, files = [{ name: "source.csv", body: content }]) {
  await page.locator('input[type="file"]').setInputFiles(files.map(f => ({ name: f.name, mimeType: "text/csv", buffer: Buffer.from(f.body) })));
}

test("a committed response lost in transport is recovered by lookup without a second POST", async ({ page }) => {
  let posts = 0;
  let committed = "";
  await shell(page, async (path, route) => {
    if (path === "/invoices/upload/batch") {
      posts++;
      committed = keys(route)[0];
      expect(route.request().headers()["x-expected-org-id"]).toBe("org-1");
      expect(route.request().headers()["x-expected-user-id"]).toBe("user-1");
      await route.abort("failed"); return true;
    }
    if (path === `/invoices/upload-commands/${committed}`) {
      await route.fulfill(json({ extraction_run_id: "recovered-run", status: "parsed" })); return true;
    }
  });
  await page.goto("/upload"); await choose(page);
  await page.getByRole("button", { name: "Upload", exact: true }).click();
  await expect(page).toHaveURL(/\/captures\/recovered-run$/);
  expect(posts).toBe(1);
  expect(await page.evaluate(s => JSON.parse(sessionStorage.getItem(s)!), SLOT)).toEqual([]);
});

test("wire-sized chunks retain same-name order and retry only the unconfirmed chunk", async ({ page }) => {
  const posted: string[][] = [];
  let retrying = false;
  await shell(page, async (path, route) => {
    if (path === "/invoices/upload/batch") {
      posted.push(keys(route));
      const sources = [...route.request().postDataBuffer()!.toString().matchAll(/filename="([^"]+)"/g)].map(m => m[1]);
      if (posted.length === 2) await route.abort("failed");
      else await route.fulfill(json(result(sources, sources.map((_n, i) => `chunk-${posted.length}-${i}`)), 202));
      return true;
    }
    if (path.startsWith("/invoices/upload-commands/")) {
      await route.fulfill(json({ detail: "Synthetic lookup" }, retrying ? 404 : 503)); return true;
    }
  }, Buffer.byteLength(content), Buffer.byteLength(content) * 2);
  await page.goto("/upload");
  await choose(page, [100, 101, 102].map(amount => ({ name: "same.csv", body: content.replace("100", String(amount)) })));
  await page.getByRole("button", { name: "Upload 3 files", exact: true }).click();
  await expect(page.getByText("2 of 3 uploads confirmed")).toBeVisible();
  expect(posted.map(p => p.length)).toEqual([2, 1]);
  expect(new Set(posted.flat()).size).toBe(3);
  expect(JSON.parse(await page.evaluate(s => sessionStorage.getItem(s)!, SLOT)).map((c: { key: string }) => c.key)).toEqual(posted[1]);
  retrying = true;
  await page.getByRole("button", { name: "Retry upload", exact: true }).click();
  await expect(page).toHaveURL(/\/captures\/chunk-3-0$/);
  expect(posted).toHaveLength(3);
  expect(posted[2]).toEqual(posted[1]);
});

test("busy capacity explains recovery and retries the same command", async ({ page }) => {
  const posted: string[][] = [];
  await shell(page, async (path, route) => {
    if (path === "/invoices/upload/batch") {
      posted.push(keys(route));
      if (posted.length === 1) await route.fulfill(json({ code: "upload_busy", detail: "Upload capacity is busy. Wait, then retry." }, 503));
      else await route.fulfill(json(result(["source.csv"], ["capacity-recovered"]), 202));
      return true;
    }
    if (path.startsWith("/invoices/upload-commands/")) {
      await route.fulfill(json({ detail: "Not committed" }, 404)); return true;
    }
  });
  await page.goto("/upload"); await choose(page);
  await page.getByRole("button", { name: "Upload", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("Upload capacity is busy");
  await expect(page.getByText("0 of 1 uploads confirmed")).toBeVisible();
  await page.getByRole("button", { name: "Retry upload", exact: true }).click();
  await expect(page).toHaveURL(/\/captures\/capacity-recovered$/);
  expect(posted).toHaveLength(2);
  expect(posted[1]).toEqual(posted[0]);
});

test("an unavailable lookup blocks another POST; a later 404 retries the same key", async ({ page }) => {
  let posts = 0;
  const posted: string[][] = [];
  let lookupStatus = 503;
  let lookups = 0;
  let releaseUnavailable!: () => void;
  let retryLookupStarted!: () => void;
  const unavailable = new Promise<void>(resolve => { releaseUnavailable = resolve; });
  const retryLookup = new Promise<void>(resolve => { retryLookupStarted = resolve; });
  await shell(page, async (path, route) => {
    if (path === "/invoices/upload/batch") {
      posted.push(keys(route)); posts++;
      if (posts === 1) await route.abort("failed");
      else await route.fulfill(json(result(["source.csv"], ["retried-run"]), 202));
      return true;
    }
    if (path.startsWith("/invoices/upload-commands/")) {
      const status = lookupStatus;
      lookups++;
      if (lookups === 2) { retryLookupStarted(); await unavailable; }
      await route.fulfill(json({ detail: "Synthetic unavailable" }, status)); return true;
    }
  });
  await page.goto("/upload"); await choose(page);
  await page.getByRole("button", { name: "Upload", exact: true }).click();
  await expect(page.getByText("0 of 1 uploads confirmed")).toBeVisible();
  await page.getByRole("button", { name: "Retry upload", exact: true }).click();
  await retryLookup;
  await expect(page.getByRole("button", { name: "Retry upload", exact: true })).toBeDisabled();
  expect(posts).toBe(1);
  releaseUnavailable();
  await expect(page.getByRole("button", { name: "Retry upload", exact: true })).toBeEnabled();
  expect(posts).toBe(1);
  lookupStatus = 404;
  await page.getByRole("button", { name: "Retry upload", exact: true }).click();
  await expect(page).toHaveURL(/\/captures\/retried-run$/);
  expect(posted).toHaveLength(2); expect(posted[1]).toEqual(posted[0]);
});

test("reload preserves the key and refuses changed file bytes before a new request", async ({ page }) => {
  let posts = 0;
  let found = false;
  await shell(page, async (path, route) => {
    if (path === "/invoices/upload/batch") { posts++; keys(route); await route.abort("failed"); return true; }
    if (path.startsWith("/invoices/upload-commands/")) {
      await route.fulfill(found ? json({ extraction_run_id: "original-run", status: "parsed" }) : json({}, 503)); return true;
    }
  });
  await page.goto("/upload"); await choose(page);
  await page.getByRole("button", { name: "Upload", exact: true }).click();
  await expect(page.getByRole("button", { name: "Check previous uploads" })).toBeVisible();
  const retained = await page.evaluate(s => sessionStorage.getItem(s), SLOT);
  expect(retained).not.toContain("Synthetic source");
  await page.reload(); await choose(page, [{ name: "source.csv", body: content.replace("100", "200") }]);
  await page.getByRole("button", { name: "Upload", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("earlier upload has an unresolved result");
  expect(posts).toBe(1);
  found = true; await page.getByRole("button", { name: "Check previous uploads" }).click();
  await expect(page.getByRole("region", { name: "Upload results" }).getByRole("link", { name: "Review", exact: true })).toHaveAttribute("href", "/captures/original-run");
});

test("same-name batch recovery retries only its missing source with that source's key", async ({ page }) => {
  const posted: string[][] = [];
  await shell(page, async (path, route) => {
    if (path === "/invoices/upload/batch") {
      posted.push(keys(route));
      if (posted.length === 1) await route.abort("failed");
      else await route.fulfill(json(result(["same.csv"], ["second-run"]), 202));
      return true;
    }
    if (path.startsWith("/invoices/upload-commands/")) {
      await route.fulfill(path.endsWith(posted[0]?.[0]) ? json({ extraction_run_id: "first-run", status: "queued" }) : json({}, 404)); return true;
    }
  });
  await page.goto("/upload"); await choose(page, [{ name: "same.csv", body: content }, { name: "same.csv", body: content.replace("100", "200") }]);
  await page.getByRole("button", { name: "Upload 2 files" }).click();
  await expect(page.getByText("1 of 2 uploads confirmed")).toBeVisible();
  await page.getByRole("button", { name: "Retry upload", exact: true }).click();
  await expect(page).toHaveURL(/\/captures\/second-run$/);
  expect(posted[0]).toHaveLength(2); expect(new Set(posted[0]).size).toBe(2);
  expect(posted[1]).toEqual([posted[0][1]]);
});

test("the served byte limit refuses a large file before hashing and POST", async ({ page }) => {
  let posts = 0;
  await shell(page, async (path, route) => {
    if (path === "/invoices/upload/batch") { posts++; await route.fulfill(json({})); return true; }
  }, 5);
  await page.goto("/upload"); await choose(page);
  await page.getByRole("button", { name: "Upload", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("exceeds the upload limit");
  expect(posts).toBe(0);
});

test("unavailable browser recovery storage prevents admission", async ({ page }) => {
  let posts = 0;
  await page.addInitScript(() => {
    const original = Storage.prototype.setItem;
    Storage.prototype.setItem = function(key, value) {
      if (key.startsWith("invoiceiq:capture-commands:")) throw new DOMException("Synthetic full storage", "QuotaExceededError");
      return original.call(this, key, value);
    };
  });
  await shell(page, async (path, route) => {
    if (path === "/invoices/upload/batch") { posts++; await route.fulfill(json({})); return true; }
  });
  await page.goto("/upload"); await choose(page);
  await page.getByRole("button", { name: "Upload", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("browser upload recovery information");
  expect(posts).toBe(0);
});

test("recovery is scoped to the current account and workspace", async ({ page }) => {
  await page.addInitScript(({ ownSlot }) => {
    sessionStorage.setItem(ownSlot, JSON.stringify([{ key: "own-command-0001", filename: "Own source.csv", fingerprint: "synthetic" }]));
    sessionStorage.setItem("invoiceiq:capture-commands:other-user:other-org", JSON.stringify([{ key: "other-command-0001", filename: "Other space source.csv", fingerprint: "synthetic" }]));
  }, { ownSlot: SLOT });
  const checked: string[] = [];
  await shell(page, async (path, route) => {
    if (path.startsWith("/invoices/upload-commands/")) {
      checked.push(path);
      expect(route.request().headers()["x-expected-org-id"]).toBe("org-1");
      await route.fulfill(json({ extraction_run_id: "own-run", status: "queued" })); return true;
    }
  });
  await page.goto("/upload");
  await expect(page.getByText(/1 upload result\(s\) need checking/)).toBeVisible();
  await page.getByRole("button", { name: "Check previous uploads" }).click();
  await expect(page.getByRole("region", { name: "Upload results" })).toContainText("Own source.csv");
  await expect(page.getByText("Other space source.csv")).toHaveCount(0);
  expect(checked).toEqual(["/invoices/upload-commands/own-command-0001"]);
});

test("a retired command reports the committed removal without recreating a capture", async ({ page }) => {
  await page.addInitScript(s => sessionStorage.setItem(s, JSON.stringify([{ key: "retired-command-0001", filename: "Retired source.csv", fingerprint: "synthetic" }])), SLOT);
  let posts = 0;
  await shell(page, async (path, route) => {
    if (path.startsWith("/invoices/upload-commands/")) { await route.fulfill(json({ extraction_run_id: null, status: "retired" })); return true; }
    if (path === "/invoices/upload/batch") { posts++; await route.fulfill(json({})); return true; }
  });
  await page.goto("/upload"); await page.getByRole("button", { name: "Check previous uploads" }).click();
  await expect(page.getByRole("region", { name: "Upload results" })).toContainText("capture is no longer available");
  await expect(page.getByRole("button", { name: "Upload anyway" })).toHaveCount(0);
  expect(posts).toBe(0);
  expect(await page.evaluate(s => JSON.parse(sessionStorage.getItem(s)!), SLOT)).toEqual([]);
});

test("a failed policy read has an actionable retry and never enables an unbounded upload", async ({ page }) => {
  await shell(page, async () => false);
  let restored = false;
  await page.route("**/api/v1/invoices/upload-policy", route => route.fulfill(json(restored ? { max_bytes: 15728640, max_files: 25, max_batch_bytes: 393216000 } : {})));
  await page.goto("/upload"); await choose(page);
  await expect(page.getByRole("button", { name: "Retry upload limit" })).toBeVisible({ timeout: 10000 });
  await expect(page.getByRole("button", { name: "Upload", exact: true })).toBeDisabled();
  restored = true; await page.getByRole("button", { name: "Retry upload limit" }).click();
  await expect(page.getByRole("button", { name: "Upload", exact: true })).toBeEnabled();
});

test("refresh and reselection retry the previously authorized override with its original key", async ({ page }) => {
  const posted: { keys: string[]; override: boolean }[] = [];
  await shell(page, async (path, route) => {
    if (path === "/invoices/upload/batch") {
      posted.push({ keys: keys(route), override: new URL(route.request().url()).searchParams.get("override") === "true" });
      if (posted.length === 1) await route.fulfill(json({ accepted: 0, rejected: 1, outcomes: [{ filename: "source.csv", accepted: false, code: "duplicate_upload", message: "Synthetic known duplicate" }] }, 202));
      else if (posted.length === 2) await route.abort("failed");
      else await route.fulfill(json(result(["source.csv"], ["override-recovered"]), 202));
      return true;
    }
    if (path.startsWith("/invoices/upload-commands/")) { await route.fulfill(json({}, 404)); return true; }
  });
  await page.goto("/upload"); await choose(page);
  await page.getByRole("button", { name: "Upload", exact: true }).click();
  await page.getByRole("button", { name: "Upload anyway", exact: true }).click();
  await expect(page.getByRole("button", { name: "Check previous uploads" })).toBeVisible();
  await page.reload(); await choose(page);
  await page.getByRole("button", { name: "Upload", exact: true }).click();
  await expect(page).toHaveURL(/\/captures\/override-recovered$/);
  expect(posted.map(p => p.override)).toEqual([false, true, true]);
  expect(posted[1].keys).toEqual(posted[2].keys);
  expect(posted[0].keys).not.toEqual(posted[1].keys);
});
