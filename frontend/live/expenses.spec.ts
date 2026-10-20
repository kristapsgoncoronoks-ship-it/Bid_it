import { execFileSync } from "node:child_process";
import { randomUUID } from "node:crypto";
import { expect, test, type Page } from "@playwright/test";

const csv = Buffer.from("Date,Description,Amount,Currency\n2026-10-01,Synthetic hotel,-12.34,USD\n");
let previousOwnerStarted = 0;

function uuid(value: string): string {
  if (!/^[0-9a-f-]{36}$/i.test(value)) throw new Error("Expected a synthetic UUID");
  return value;
}

function sql(statement: string): string {
  return execFileSync("psql", ["-X", "-v", "ON_ERROR_STOP=1", "-At", "-c", statement],
    { encoding: "utf8", env: process.env }).trim();
}

async function owner(page: Page) {
  // Keep the production 20/minute auth limiter active. Each isolated browser
  // account does several auth reads on login/reload; pace fixture setup into a
  // fresh window instead of disabling the guard or disguising the client IP.
  const pause = Math.max(0, previousOwnerStarted + 61_000 - Date.now());
  if (pause > 0) {
    test.setTimeout(test.info().timeout + pause);
    await new Promise((resolve) => setTimeout(resolve, pause));
  }
  previousOwnerStarted = Date.now();
  await expect.poll(async () => {
    try { return (await page.request.get("http://127.0.0.1:8000/health")).status(); }
    catch { return 0; }
  }, { timeout: 30_000 }).toBe(200);
  const email = "r010-live-" + randomUUID() + "@example.com";
  const password = "synthetic-test-password";
  const registration = await page.request.post("/api/v1/auth/register", {
    data: { organization_name: "Synthetic R010 browser", name: "Owner", email, password },
  });
  expect(registration.status()).toBe(201);
  const identity = await registration.json();
  const headers = { Authorization: "Bearer " + identity.token.access_token };
  const active = await page.request.put("/api/v1/modules/expenses", { headers, data: { enabled: true } });
  expect(active.status()).toBe(200);
  await page.goto("/login?next=/expenses");
  await page.getByLabel("Email", { exact: true }).fill(email);
  await page.getByLabel("Password", { exact: true }).fill(password);
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Expenses", exact: true })).toBeVisible();
  await expect(page.getByText("No available transactions. Import a statement to get started.")).toBeVisible();
  return { orgId: uuid(identity.organization.id), userId: uuid(identity.user.id), headers };
}

async function importFile(page: Page) {
  const response = page.waitForResponse((r) => r.url().endsWith("/api/v1/expenses/import/bank-statement") && r.request().method() === "POST");
  const chooser = page.waitForEvent("filechooser");
  await page.getByRole("button", { name: "Import bank statement", exact: true }).click();
  await (await chooser).setFiles({ name: "synthetic.csv", mimeType: "text/csv", buffer: csv });
  const result = await response;
  expect(result.status()).toBe(200);
  return await result.json();
}

test("real import, lost-response report recovery, replay and committed PG readback", async ({ page }) => {
  const { orgId, headers } = await owner(page);
  await page.getByLabel("Statement account reference", { exact: true }).fill("operating-card");
  await page.getByLabel("Currency if missing from the statement", { exact: true }).fill("USD");
  const imported = await importFile(page);
  expect(imported.imported).toBe(1);
  await expect(page.getByRole("checkbox", { name: "Select Synthetic hotel" })).toBeVisible();
  await page.getByRole("checkbox", { name: "Select Synthetic hotel" }).check();
  await page.getByLabel("Report title", { exact: true }).fill("Synthetic recovered report");
  let committedReport = "";
  let originalKey = "";
  let originalPayload: unknown;
  await page.route("**/api/v1/expenses", async (route) => {
    if (route.request().method() !== "POST") { await route.continue(); return; }
    originalKey = route.request().headers()["idempotency-key"];
    originalPayload = route.request().postDataJSON();
    const actual = await route.fetch({ maxRetries: 0 });
    expect(actual.status()).toBe(201);
    committedReport = uuid((await actual.json()).id);
    await route.abort("failed"); // Actual server committed; only its response is lost.
  });
  await page.getByRole("button", { name: /Create report from 1/ }).click();
  await expect(page.getByText(/Report saved: Synthetic recovered report/)).toBeVisible();
  await expect(page.getByText("No available transactions. Import a statement to get started.")).toBeVisible();
  expect(committedReport).not.toBe("");
  await page.unroute("**/api/v1/expenses");
  const replay = await page.request.post("/api/v1/expenses", {
    headers: { ...headers, "Idempotency-Key": originalKey }, data: originalPayload,
  });
  expect(replay.status()).toBe(201);
  expect((await replay.json()).id).toBe(committedReport);
  await page.reload();
  await expect(page.getByRole("heading", { name: "Expenses", exact: true })).toBeVisible();
  await expect(page.getByText("Synthetic recovered report", { exact: true })).toBeVisible();
  await page.getByLabel("Statement account reference", { exact: true }).fill("operating-card");
  await page.getByLabel("Currency if missing from the statement", { exact: true }).fill("USD");
  const again = await importFile(page);
  expect(again.batch_id).toBe(imported.batch_id);
  expect(again.imported).toBe(0);
  expect(again.assigned).toBe(1);
  for (let i = 0; i < 10; i++) {
    const readback = await page.request.get("/api/v1/expenses/" + committedReport, { headers });
    expect(readback.status()).toBe(200);
    expect((await readback.json()).total).toBe("12.34");
  }
  expect(sql("SELECT count(*) FROM expense_reports WHERE org_id='" + orgId + "'")).toBe("1");
  expect(sql("SELECT count(*) FROM expense_transactions WHERE org_id='" + orgId + "'")).toBe("1");
  expect(sql("SELECT currency || '|' || total::text FROM expense_reports WHERE id='" + committedReport + "' AND org_id='" + orgId + "'")).toBe("USD|12.34");
  expect(sql("SELECT currency || '|' || original_amount::text FROM expense_items WHERE report_id='" + committedReport + "' AND org_id='" + orgId + "'")).toBe("USD|12.34");
  expect(sql("SELECT count(*) FROM audit_events WHERE org_id='" + orgId + "' AND action='expense.report_created'")).toBe("1");
  console.log(JSON.stringify({ kind: "R010 unmocked completion plus response-loss injection", csv_bytes: csv.length, source_rows: 1, report_id: committedReport }));
});

test("real recoverable removal and nested-query failure remain distinct", async ({ page }) => {
  const { headers } = await owner(page);
  await page.getByLabel("Statement account reference", { exact: true }).fill("operating-card");
  await page.getByLabel("Currency if missing from the statement", { exact: true }).fill("USD");
  const imported = await importFile(page);
  const id = uuid(imported.transactions[0].id);
  await expect(page.getByRole("checkbox", { name: "Select Synthetic hotel" })).toBeVisible();
  await page.getByRole("button", { name: "remove", exact: true }).click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await page.getByRole("dialog").getByRole("button", { name: "Remove", exact: true }).click();
  await expect(page.getByText("No available transactions. Import a statement to get started.")).toBeVisible();
  expect(sql("SELECT count(*) FROM expense_transactions WHERE id='" + id + "' AND deleted_at IS NOT NULL")).toBe("1");
  const restored = await page.request.post("/api/v1/invoices/trash/other/expense_transaction/" + id + "/restore", { headers });
  expect(restored.status()).toBe(200);
  await page.reload();
  await expect(page.getByRole("checkbox", { name: "Select Synthetic hotel" })).toBeVisible();
  const read = await page.request.get("/api/v1/expenses/transactions", { headers });
  expect((await read.json())[0].currency).toBe("USD");
  // Mocked transport failure below is rendering evidence only.
  await page.route("**/api/v1/expenses/transactions", (route) => route.abort("internetdisconnected"));
  await page.reload();
  await expect(page.getByRole("heading", { name: "Expenses", exact: true })).toBeVisible();
  await expect(page.getByText("The available transactions could not be loaded. The inbox is not confirmed empty.")).toBeVisible({ timeout: 20_000 });
  await expect(page.getByText("No available transactions. Import a statement to get started.")).toHaveCount(0);
  await page.unroute("**/api/v1/expenses/transactions");
  await page.getByRole("button", { name: "Retry loading inbox", exact: true }).click();
  await expect(page.getByRole("checkbox", { name: "Select Synthetic hotel" })).toBeVisible();
});


async function seedImport(page: Page, headers: Record<string, string>, account: string, key = randomUUID()) {
  const response = await page.request.post("/api/v1/expenses/import/bank-statement", {
    headers: { ...headers, "Idempotency-Key": key },
    multipart: {
      file: { name: "synthetic.csv", mimeType: "text/csv", buffer: csv },
      account_reference: account, statement_currency: "USD",
    },
  });
  expect(response.status()).toBe(200);
  return { key, result: await response.json() };
}

async function savePending(page: Page, userId: string, orgId: string, slot: string,
  key: string, payload?: unknown) {
  await page.evaluate(async ({ userId, orgId, slot, key, payload }) => {
    const path = slot === "/expenses/from-selected" ? "/expenses" : slot;
    const envelope = payload === undefined ? {} : { path, payload };
    const bytes = await crypto.subtle.digest("SHA-256",
      new TextEncoder().encode(JSON.stringify(payload === undefined ? {} : envelope)));
    const digest = Array.from(new Uint8Array(bytes), (v) => v.toString(16).padStart(2, "0")).join("");
    sessionStorage.setItem("invoiceiq:command:" + userId + ":" + orgId + ":" + slot,
      JSON.stringify({ key, digest, ...envelope }));
  }, { userId, orgId, slot, key, payload });
}

test("the three outcome-check controls only read existing committed outcomes", async ({ page }) => {
  const { orgId, userId, headers } = await owner(page);
  const imported = await seedImport(page, headers, "read-card");
  const row = imported.result.transactions[0];
  const reportKey = randomUUID();
  const report = await page.request.post("/api/v1/expenses", {
    headers: { ...headers, "Idempotency-Key": reportKey },
    data: { title: "Read-only recovered report", currency: "USD",
      transaction_ids: [row.id], transaction_versions: { [row.id]: row.version } },
  });
  expect(report.status()).toBe(201);
  const other = await seedImport(page, headers, "remove-card");
  const removedRow = other.result.transactions[0];
  const removeKey = randomUUID();
  const removed = await page.request.delete("/api/v1/expenses/transactions/" + removedRow.id, {
    headers: { ...headers, "Idempotency-Key": removeKey }, params: { version: removedRow.version },
  });
  expect(removed.status()).toBe(204);
  await savePending(page, userId, orgId, "/expenses/import/bank-statement", imported.key);
  await savePending(page, userId, orgId, "/expenses/from-selected", reportKey);
  await savePending(page, userId, orgId, "/expenses/remove", removeKey);
  await page.reload();
  await expect(page.getByRole("heading", { name: "Expenses", exact: true })).toBeVisible();
  const writes: string[] = [];
  page.on("request", (request) => {
    if (request.url().includes("/api/v1/expenses") && ["POST", "DELETE", "PATCH"].includes(request.method())) writes.push(request.method());
  });
  await page.getByRole("button", { name: "Check previous import outcome", exact: true }).click();
  await expect(page.getByText(/Import committed:/)).toBeVisible();
  await page.getByRole("button", { name: "Check previous report outcome", exact: true }).click();
  await expect(page.getByText(/Report saved: Read-only recovered report/)).toBeVisible();
  await page.getByRole("button", { name: "Check previous removal outcome", exact: true }).click();
  await expect(page.getByText("The previous removal committed. The inbox below shows its current state.")).toBeVisible();
  expect(writes).toEqual([]);
  expect(sql("SELECT count(*) FROM expense_reports WHERE org_id='" + orgId + "'")).toBe("1");
  expect(sql("SELECT count(*) FROM expense_transactions WHERE org_id='" + orgId + "'")).toBe("2");
  expect(sql("SELECT count(*) FROM audit_events WHERE org_id='" + orgId + "' AND action='expense.transaction_binned'")).toBe("1");
});

test("retry original report looks up status before one same-key command", async ({ page }) => {
  const { orgId, userId, headers } = await owner(page);
  const imported = await seedImport(page, headers, "pending-card");
  const row = imported.result.transactions[0];
  const key = randomUUID();
  const payload = { title: "Original retry report", currency: "USD",
    transaction_ids: [row.id], transaction_versions: { [row.id]: row.version } };
  await savePending(page, userId, orgId, "/expenses/from-selected", key, payload);
  await page.reload();
  await expect(page.getByRole("heading", { name: "Expenses", exact: true })).toBeVisible();
  const attempts: string[] = [];
  page.on("request", (request) => {
    if (request.url().includes("/expenses/commands/" + key + "/result")) attempts.push("lookup");
    if (request.url().endsWith("/api/v1/expenses") && request.method() === "POST") {
      expect(request.headers()["idempotency-key"]).toBe(key);
      attempts.push("write");
    }
  });
  await page.getByRole("button", { name: "Retry original report request", exact: true }).click();
  await expect(page.getByText(/Report saved: Original retry report/)).toBeVisible();
  expect(attempts[0]).toBe("lookup");
  expect(attempts.filter((value) => value === "write")).toHaveLength(1);
  expect(sql("SELECT count(*) FROM expense_reports WHERE org_id='" + orgId + "'")).toBe("1");
});

test("unreadable saved input explains recovery and starts no financial request", async ({ page }) => {
  const { userId, orgId } = await owner(page);
  await page.evaluate(({ userId, orgId }) => {
    sessionStorage.setItem("invoiceiq:command:" + userId + ":" + orgId + ":/expenses/from-selected", "{bad");
  }, { userId, orgId });
  await page.reload();
  await expect(page.getByRole("heading", { name: "Expenses", exact: true })).toBeVisible();
  const writes: string[] = [];
  page.on("request", (request) => {
    if (request.url().includes("/api/v1/expenses") && request.method() === "POST") writes.push(request.url());
  });
  await page.getByRole("button", { name: "Retry original report request", exact: true }).click();
  await expect(page.getByRole("alert").filter({ hasText: "previous report request is unreadable" })).toBeVisible();
  await page.getByRole("button", { name: "Check previous report outcome", exact: true }).click();
  await expect(page.getByRole("alert").filter({ hasText: "previous request is unreadable" })).toBeVisible();
  expect(writes).toEqual([]);
  expect(sql("SELECT count(*) FROM expense_reports WHERE org_id='" + orgId + "'")).toBe("0");
});

test("a rate-limited status lookup retains identity and cannot resend the report", async ({ page }) => {
  const { userId, orgId } = await owner(page);
  const key = randomUUID(), id = randomUUID();
  await savePending(page, userId, orgId, "/expenses/from-selected", key, {
    title: "Synthetic unresolved", currency: "USD",
    transaction_ids: [id], transaction_versions: { [id]: 1 },
  });
  await page.reload();
  await expect(page.getByRole("heading", { name: "Expenses", exact: true })).toBeVisible();
  await page.route("**/api/v1/expenses/commands/" + key + "/result", (route) =>
    route.fulfill({ status: 429, contentType: "application/json",
      body: JSON.stringify({ detail: "Wait before checking the saved outcome again.", code: "rate_limited" }) }));
  const writes: string[] = [];
  page.on("request", (request) => {
    if (request.url().endsWith("/api/v1/expenses") && request.method() === "POST") writes.push(request.url());
  });
  await page.getByRole("button", { name: "Check previous report outcome", exact: true }).click();
  await expect(page.getByRole("alert").filter({ hasText: "Wait before checking" })).toBeVisible();
  await page.getByRole("button", { name: "Retry original report request", exact: true }).click();
  await expect(page.getByRole("alert").filter({ hasText: "Could not check the previous request" })).toBeVisible();
  expect(writes).toEqual([]);
  expect(sql("SELECT count(*) FROM expense_reports WHERE org_id='" + orgId + "'")).toBe("0");
});


async function openManual(page: Page, title: string, amounts: string[]) {
  await page.getByRole("button", { name: "+ New expense report (manual)", exact: true }).click();
  await page.getByLabel("Title", { exact: true }).fill(title);
  for (const [index, amount] of amounts.entries()) {
    if (index > 0) await page.getByRole("button", { name: "+ Add expense", exact: true }).click();
    await page.getByLabel(`Item ${index + 1} description`, { exact: true }).fill(`Synthetic manual line ${index + 1}`);
    await page.getByLabel(`Item ${index + 1} amount`, { exact: true }).fill(amount);
    await page.getByLabel(`Item ${index + 1} VAT`, { exact: true }).fill("0");
  }
}

function manualCounts(orgId: string, reportId: string, key: string) {
  uuid(orgId); uuid(reportId); uuid(key);
  return {
    reports: sql("SELECT count(*) FROM expense_reports WHERE org_id='" + orgId + "'"),
    allItems: sql("SELECT count(*) FROM expense_items WHERE org_id='" + orgId + "'"),
    allCommands: sql("SELECT count(*) FROM expense_commands WHERE org_id='" + orgId + "'"),
    allReportCreatedAudits: sql("SELECT count(*) FROM audit_events WHERE org_id='" + orgId + "' AND action='expense.report_created'"),
    items: sql("SELECT count(*) FROM expense_items WHERE org_id='" + orgId + "' AND report_id='" + reportId + "'"),
    commands: sql("SELECT count(*) FROM expense_commands WHERE org_id='" + orgId + "' AND operation='report_created' AND command_key='" + key + "' AND target_id='" + reportId + "'"),
    audits: sql("SELECT count(*) FROM audit_events WHERE org_id='" + orgId + "' AND action='expense.report_created' AND target_type='expense_report' AND target_id='" + reportId + "' AND meta::jsonb->>'command_key'='" + key + "'"),
  };
}

test("manual save recovers a real committed lost response through GET-only typed receipts", async ({ page }) => {
  await page.setExtraHTTPHeaders({ "X-R010-Journey": "manual" });
  const { orgId, userId, headers } = await owner(page);
  const title = "Synthetic exact manual draft";
  await openManual(page, title, ["0.10", "0.20"]);
  await expect(page.getByText("Total:", { exact: false }).filter({ hasText: "€0.30" })).toBeVisible();
  const slot = "invoiceiq:command:" + userId + ":" + orgId + ":/expenses/manual";
  const writes: { key: string; payload: unknown }[] = [];
  let committedReport = "";
  let originalKey = "";
  let originalPayload: unknown;
  let blockedLookups = 0;
  await page.route(/\/api\/v1\/expenses\/commands\/[^/]+$/, (route) => {
    blockedLookups += 1;
    return route.fulfill({ status: 503, contentType: "application/json",
      body: JSON.stringify({ detail: "Synthetic receipt transport unavailable" }) });
  });
  await page.route("**/api/v1/expenses", async (route) => {
    if (route.request().method() !== "POST") { await route.continue(); return; }
    originalKey = uuid(route.request().headers()["idempotency-key"]);
    originalPayload = route.request().postDataJSON();
    writes.push({ key: originalKey, payload: originalPayload });
    expect(route.request().headers()["x-expected-org-id"]).toBe(orgId);
    expect(route.request().headers()["x-expected-user-id"]).toBe(userId);
    const actual = await route.fetch({ maxRetries: 0 });
    expect(actual.status()).toBe(201);
    const report = await actual.json();
    committedReport = uuid(report.id);
    expect(report.total).toBe("0.30");
    expect(report.vat_total).toBe("0.00");
    await route.abort("failed"); // Actual PostgreSQL commit; only response delivery is lost.
  });
  const started = performance.now();
  await page.getByRole("button", { name: "Save draft", exact: true }).click();
  await expect.poll(() => committedReport).not.toBe("");
  await expect.poll(() => blockedLookups).toBeGreaterThan(0);
  const check = page.getByRole("button", { name: "Check previous manual draft outcome", exact: true });
  await expect(check).toBeEnabled();
  await expect.poll(() => page.evaluate((key) => sessionStorage.getItem(key), slot)).not.toBeNull();
  const saved = await page.evaluate((key) => JSON.parse(sessionStorage.getItem(key)!), slot);
  expect(saved.key).toBe(originalKey);
  expect(Object.keys(saved).sort()).toEqual(["digest", "key"]);
  expect(saved.digest).toMatch(/^[a-f0-9]{64}$/);
  expect(writes).toHaveLength(1);
  const expected = { reports: "1", allItems: "2", allCommands: "1", allReportCreatedAudits: "1", items: "2", commands: "1", audits: "1" };
  expect(manualCounts(orgId, committedReport, originalKey)).toEqual(expected);
  expect(sql("SELECT currency || '|' || total::text || '|' || vat_total::text || '|' || status FROM expense_reports WHERE id='" + committedReport + "' AND org_id='" + orgId + "'")).toBe("EUR|0.30|0.00|draft");
  expect(sql("SELECT string_agg(amount::text, '|' ORDER BY amount) FROM expense_items WHERE report_id='" + committedReport + "' AND org_id='" + orgId + "'")).toBe("0.10|0.20");
  await page.unroute(/\/api\/v1\/expenses\/commands\/[^/]+$/);
  await page.unroute("**/api/v1/expenses");
  const recoveryWrites: string[] = [];
  const readMethods: string[] = [];
  page.on("request", (request) => {
    if (!request.url().includes("/api/v1/expenses")) return;
    if (request.method() !== "GET") recoveryWrites.push(request.method());
    if (request.url().endsWith("/expenses/commands/" + originalKey)) readMethods.push(request.method());
  });
  for (let activation = 0; activation < 2; activation++) {
    const receipt = page.waitForResponse((response) => response.url().endsWith("/expenses/commands/" + originalKey));
    await check.click();
    expect((await receipt).status()).toBe(200);
    await expect(page.getByRole("link", { name: "Open saved draft", exact: true })).toHaveAttribute("href", "/expenses/" + committedReport);
    await expect(check).toBeEnabled();
    expect(manualCounts(orgId, committedReport, originalKey)).toEqual(expected);
  }
  expect(readMethods).toEqual(["GET", "GET"]);
  expect(recoveryWrites).toEqual([]);
  expect(writes).toHaveLength(1);
  expect(await page.evaluate((key) => sessionStorage.getItem(key), slot)).toBeNull();
  // This fixture replay is outside the GET-only user-action assertion.
  const replay = await page.request.post("/api/v1/expenses", {
    headers: { ...headers, "Idempotency-Key": originalKey, "X-R010-Journey": "manual" }, data: originalPayload,
  });
  expect(replay.status()).toBe(201);
  expect((await replay.json()).id).toBe(committedReport);
  expect(manualCounts(orgId, committedReport, originalKey)).toEqual(expected);
  console.log(JSON.stringify({ kind: "R010 manual actual browser/API/PostgreSQL commit and injected response loss", journey: "manual", source_rows: 0, manual_items: 2,
    report_id: committedReport, total: "0.30", manual_posts: writes.length, recovery_gets: readMethods.length,
    user_recovery_writes: recoveryWrites.length, pg_counts: expected, elapsed_ms: performance.now() - started }));
});

test("manual invalid amount and VAT input refuses every write; exact limits and zero drafts remain supported", async ({ page }) => {
  await page.setExtraHTTPHeaders({ "X-R010-Journey": "manual" });
  const { orgId } = await owner(page);
  await openManual(page, "Synthetic validation draft", ["0.005", "0.005"]);
  const writes: string[] = [];
  page.on("request", (request) => {
    if (request.url().endsWith("/api/v1/expenses") && request.method() === "POST") writes.push(request.url());
  });
  const save = page.getByRole("button", { name: "Save draft", exact: true });
  await save.click();
  await expect(page.getByRole("alert").filter({ hasText: /Item 1 amount/i })).toBeVisible();
  expect(writes).toEqual([]);
  await page.getByLabel("Item 2 amount", { exact: true }).fill("0");
  const invalid = ["0.005", "-1", "NaN", "Infinity", "1e3", "", "1000000000000.00"];
  for (const field of ["amount", "VAT"]) {
    await page.getByLabel("Item 1 amount", { exact: true }).fill("1.00");
    await page.getByLabel("Item 1 VAT", { exact: true }).fill("0");
    for (const value of invalid) {
      await page.getByLabel("Item 1 " + field, { exact: true }).fill(value);
      await save.click();
      await expect(page.getByRole("alert").filter({ hasText: new RegExp("Item 1 " + field, "i") })).toBeVisible();
      expect(writes).toEqual([]);
    }
  }
  await page.getByLabel("Item 1 amount", { exact: true }).fill("999999999999.98");
  await page.getByLabel("Item 1 VAT", { exact: true }).fill("0");
  await page.getByLabel("Item 2 amount", { exact: true }).fill("0.01");
  await expect(page.getByText("Total:", { exact: false }).filter({ hasText: "€999,999,999,999.99" })).toBeVisible();
  await page.getByLabel("Item 2 amount", { exact: true }).fill("0.02");
  await save.click();
  await expect(page.getByRole("alert").filter({ hasText: /Report totals cannot exceed|split.*reports/i })).toBeVisible();
  expect(writes).toEqual([]);
  expect(sql("SELECT count(*) FROM expense_reports WHERE org_id='" + orgId + "'")).toBe("0");
  expect(sql("SELECT count(*) FROM expense_items WHERE org_id='" + orgId + "'")).toBe("0");
  expect(sql("SELECT count(*) FROM expense_commands WHERE org_id='" + orgId + "'")).toBe("0");
  // Valid zero is a supported, independent intention, unlike the invalid inputs above.
  await page.getByLabel("Item 1 amount", { exact: true }).fill("0");
  await page.getByLabel("Item 2 amount", { exact: true }).fill("0.00");
  await expect(page.getByText("Total:", { exact: false }).filter({ hasText: "€0.00" })).toBeVisible();
  const response = page.waitForResponse((result) => result.url().endsWith("/api/v1/expenses") && result.request().method() === "POST");
  await save.click();
  const actual = await response;
  expect(actual.status()).toBe(201);
  const report = await actual.json();
  expect(report.total).toBe("0.00");
  await expect(page.getByRole("link", { name: "Open saved draft", exact: true })).toHaveAttribute("href", "/expenses/" + report.id);
  expect(writes).toHaveLength(1);
  expect(sql("SELECT total::text || '|' || status FROM expense_reports WHERE id='" + uuid(report.id) + "' AND org_id='" + orgId + "'")).toBe("0.00|draft");
  console.log(JSON.stringify({ kind: "R010 manual actual browser zero-POST validation and zero-draft completion", journey: "manual", invalid_field_cases: 15, invalid_total_cases: 1, invalid_posts: 0, valid_zero_posts: writes.length, report_id: report.id }));
});

test("a real manual commit survives a failed current read and retries loading with GET only", async ({ page }) => {
  await page.setExtraHTTPHeaders({ "X-R010-Journey": "manual" });
  const { orgId } = await owner(page);
  await openManual(page, "Synthetic committed read recovery", ["1.25"]);
  let reportId = "", commandKey = "";
  let failCurrent = true;
  let blockedCurrentReads = 0;
  await page.route(/\/api\/v1\/expenses\/[0-9a-f-]{36}$/, async (route) => {
    if (failCurrent && route.request().method() === "GET") {
      blockedCurrentReads += 1;
      await route.fulfill({ status: 503, contentType: "application/json", body: JSON.stringify({ detail: "Synthetic current report unavailable" }) });
    } else await route.continue();
  });
  const savedResponse = page.waitForResponse((response) => response.url().endsWith("/api/v1/expenses") && response.request().method() === "POST");
  await page.getByRole("button", { name: "Save draft", exact: true }).click();
  const actual = await savedResponse;
  expect(actual.status()).toBe(201);
  commandKey = uuid(actual.request().headers()["idempotency-key"]);
  reportId = uuid((await actual.json()).id);
  await expect(page.getByRole("link", { name: "Open saved draft", exact: true })).toHaveAttribute("href", "/expenses/" + reportId);
  await expect(page.getByRole("button", { name: "Retry loading saved report", exact: true })).toBeVisible();
  expect(blockedCurrentReads).toBeGreaterThan(0);
  const expected = { reports: "1", allItems: "1", allCommands: "1", allReportCreatedAudits: "1", items: "1", commands: "1", audits: "1" };
  expect(manualCounts(orgId, reportId, commandKey)).toEqual(expected);
  const retryWrites: string[] = [];
  page.on("request", (request) => {
    if (request.url().includes("/api/v1/expenses") && request.method() !== "GET") retryWrites.push(request.method());
  });
  failCurrent = false;
  const currentRead = page.waitForResponse((response) => response.url().endsWith("/api/v1/expenses/" + reportId) && response.request().method() === "GET");
  await page.getByRole("button", { name: "Retry loading saved report", exact: true }).click();
  const restored = await currentRead;
  expect(restored.status()).toBe(200);
  expect((await restored.json()).id).toBe(reportId);
  await expect(page.getByRole("button", { name: "+ New expense report (manual)", exact: true })).toBeEnabled();
  expect(retryWrites).toEqual([]);
  expect(manualCounts(orgId, reportId, commandKey)).toEqual(expected);
  console.log(JSON.stringify({ kind: "R010 manual actual commit plus mocked current-read transport failure", journey: "manual", report_id: reportId, blocked_current_reads: blockedCurrentReads, restored_current_status: restored.status(), recovery_writes: retryWrites.length, pg_counts: expected }));
});
