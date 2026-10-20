import { test, expect, type Page } from "@playwright/test";

// Shared-helper contract evidence with synthetic API responses. These do not
// establish PostgreSQL commit/concurrency or live application completion.
const SCOPE = { userId: "adapter-user", orgId: "adapter-org" };
const KEY = "adapter-command-1";
const SLOT = `invoiceiq:command:${SCOPE.userId}:${SCOPE.orgId}:/expenses/manual`;
const REPORT = { id: "adapter-report", total: "0.30", currency: "EUR" };
const RECEIPT = { status: "committed", operation: "report_created", command_key: KEY, target_id: REPORT.id, result: REPORT };
type Branch = "explicit-check" | "existing-command" | "uncertain-post";

async function begin(page: Page, reply: unknown) {
  const requests: { method: string; path: string }[] = [];
  await page.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname.replace("/api/v1", "");
    requests.push({ method: request.method(), path });
    expect(request.headers()["x-expected-user-id"]).toBe(SCOPE.userId);
    expect(request.headers()["x-expected-org-id"]).toBe(SCOPE.orgId);
    if (request.method() === "POST") {
      expect(request.headers()["idempotency-key"]).toBe(KEY);
      return route.fulfill({ status: 503, contentType: "application/json", body: JSON.stringify({ detail: "Synthetic uncertain response" }) });
    }
    return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(reply) });
  });
  await page.goto("/design");
  await expect(page.getByRole("heading", { level: 1, name: /good morning/i })).toBeVisible();
  return requests;
}

async function invoke(page: Page, branch: Branch) {
  return page.evaluate(async ({ branch, scope, key, slot }) => {
    const modulePath = "/src/lib/invoiceCommands.ts";
    const { invoiceCommand, checkSavedInvoiceCommand } = await import(/* @vite-ignore */ modulePath);
    const payload = { items: [{ amount: "0.10" }, { amount: "0.20" }] };
    const bytes = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(JSON.stringify(payload)));
    const digest = Array.from(new Uint8Array(bytes), (byte) => byte.toString(16).padStart(2, "0")).join("");
    const saved = JSON.stringify({ key, digest });
    if (branch !== "uncertain-post") sessionStorage.setItem(slot, saved);
    let validations = 0;
    const adapt = (value: unknown, expectedKey: string) => {
      validations += 1;
      const receipt = value as { status?: string; operation?: string; command_key?: string; target_id?: string; result?: { id?: string } } | null;
      if (!receipt || receipt.status !== "committed" || receipt.operation !== "report_created" ||
          receipt.command_key !== expectedKey || typeof receipt.target_id !== "string" || !receipt.target_id ||
          !receipt.result || receipt.result.id !== receipt.target_id) {
        throw new Error("The saved command receipt could not be verified.");
      }
      return receipt.result;
    };
    let result: unknown = null;
    let error: string | null = null;
    try {
      const lookup = (commandKey: string) => `/expenses/commands/${commandKey}`;
      result = branch === "explicit-check"
        ? await checkSavedInvoiceCommand("/expenses/manual", scope, lookup, adapt)
        : await invoiceCommand("/expenses", payload, scope, lookup, key, "/expenses/manual", false, undefined, "post", adapt);
    } catch (caught) {
      error = caught instanceof Error ? caught.message : String(caught);
    }
    return { result, error, validations, saved, stored: sessionStorage.getItem(slot) };
  }, { branch, scope: SCOPE, key: KEY, slot: SLOT });
}

for (const branch of ["explicit-check", "existing-command", "uncertain-post"] as const) {
  test(`${branch}: a validated committed receipt is adapted before metadata is cleared`, async ({ page }) => {
    const requests = await begin(page, RECEIPT);
    const outcome = await invoke(page, branch);
    expect(outcome.error).toBeNull();
    expect(outcome.result).toEqual(REPORT);
    expect(outcome.validations).toBe(1);
    expect(outcome.stored).toBeNull();
    expect(requests).toEqual([
      ...(branch === "uncertain-post" ? [{ method: "POST", path: "/expenses" }] : []),
      { method: "GET", path: `/expenses/commands/${KEY}` },
    ]);
  });

  for (const invalid of [
    { name: "malformed 200", receipt: null },
    { name: "wrong command key", receipt: { ...RECEIPT, command_key: "another-command" } },
    { name: "wrong operation", receipt: { ...RECEIPT, operation: "candidate_removed" } },
    { name: "mismatched target", receipt: { ...RECEIPT, target_id: "another-report" } },
  ]) {
    test(`${branch}: ${invalid.name} retains the unresolved identity and cannot become success`, async ({ page }) => {
      const requests = await begin(page, invalid.receipt);
      const outcome = await invoke(page, branch);
      expect(outcome.error).toBeTruthy();
      expect(outcome.result).toBeNull();
      expect(outcome.validations).toBe(1);
      expect(outcome.stored).toBe(outcome.saved);
      expect(requests).toEqual([
        ...(branch === "uncertain-post" ? [{ method: "POST", path: "/expenses" }] : []),
        { method: "GET", path: `/expenses/commands/${KEY}` },
      ]);
    });
  }
}

for (const caller of ["invoice-check", "cash-retry", "selected-report-retry"] as const) {
  test(`default ${caller} still receives its raw response and preserves the manual slot`, async ({ page }) => {
    const raw = caller === "cash-retry" ? { invoice: { id: "invoice-1" }, payment: { id: "payment-1" } } : REPORT;
    const requests = await begin(page, raw);
    const outcome = await page.evaluate(async ({ caller, scope, key, manualSlot }) => {
      const modulePath = "/src/lib/invoiceCommands.ts";
      const helpers = await import(/* @vite-ignore */ modulePath);
      const path = caller === "cash-retry" ? "/issued/invoice-1/payments" : caller === "selected-report-retry" ? "/expenses" : "/issued";
      const storagePath = caller === "cash-retry" ? "/issued/invoice-1/cash-command" : caller === "selected-report-retry" ? "/expenses/from-selected" : path;
      const payload = caller === "cash-retry" ? { amount: "0.30", date: "2026-10-20" } : { items: ["candidate-1"] };
      const fingerprint = caller === "invoice-check" ? payload : { path, payload };
      const bytes = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(JSON.stringify(fingerprint)));
      const digest = Array.from(new Uint8Array(bytes), (byte) => byte.toString(16).padStart(2, "0")).join("");
      const ownSlot = `invoiceiq:command:${scope.userId}:${scope.orgId}:${storagePath}`;
      sessionStorage.setItem(ownSlot, JSON.stringify({ key, digest, path, payload }));
      sessionStorage.setItem(manualSlot, "synthetic unrelated unresolved manual identity");
      const lookup = (commandKey: string) => `/legacy/commands/${commandKey}`;
      const result = caller === "cash-retry"
        ? await helpers.retrySavedCashCommand(storagePath, scope, lookup)
        : caller === "selected-report-retry"
          ? await helpers.retrySavedExpenseReport(scope)
          : await helpers.checkSavedInvoiceCommand(storagePath, scope, lookup);
      return { result, own: sessionStorage.getItem(ownSlot), manual: sessionStorage.getItem(manualSlot) };
    }, { caller, scope: SCOPE, key: KEY, manualSlot: SLOT });
    expect(outcome.result).toEqual(raw);
    expect(outcome.own).toBeNull();
    expect(outcome.manual).toBe("synthetic unrelated unresolved manual identity");
    expect(requests).toEqual([{ method: "GET", path: caller === "selected-report-retry" ? `/expenses/commands/${KEY}/result` : `/legacy/commands/${KEY}` }]);
  });
}

test("an optional response adapter validates a successful POST before clearing its identity", async ({ page }) => {
  let posts = 0;
  await page.route("**/api/v1/expenses", async (route) => {
    posts += 1;
    return route.fulfill({ status: 201, contentType: "application/json", body: JSON.stringify(REPORT) });
  });
  await page.goto("/design");
  await expect(page.getByRole("heading", { level: 1, name: /good morning/i })).toBeVisible();
  const outcome = await page.evaluate(async ({ scope, key, slot }) => {
    const modulePath = "/src/lib/invoiceCommands.ts";
    const { invoiceCommand } = await import(/* @vite-ignore */ modulePath);
    const validations: string[] = [];
    const adapt = (value: unknown, actualKey: string, source: string) => {
      validations.push(source);
      if (actualKey !== key || source !== "write" || (value as { id?: string })?.id !== "adapter-report") throw new Error("Unexpected write response");
      return value;
    };
    const result = await invoiceCommand("/expenses", { items: [] }, scope, (commandKey: string) => `/expenses/commands/${commandKey}`, key, "/expenses/manual", false, undefined, "post", adapt);
    return { result, validations, stored: sessionStorage.getItem(slot) };
  }, { scope: SCOPE, key: KEY, slot: SLOT });
  expect(outcome.result).toEqual(REPORT);
  expect(outcome.validations).toEqual(["write"]);
  expect(outcome.stored).toBeNull();
  expect(posts).toBe(1);
});

for (const lookupValid of [true, false]) {
  test(`a malformed POST201 checks the typed receipt and ${lookupValid ? "recovers" : "retains uncertainty"}`, async ({ page }) => {
    const requests: string[] = [];
    await page.route("**/api/v1/**", async (route) => {
      const method = route.request().method();
      requests.push(method);
      return route.fulfill({
        status: method === "POST" ? 201 : 200,
        contentType: "application/json",
        body: JSON.stringify(method === "POST" || !lookupValid ? { unexpected: "not a report" } : RECEIPT),
      });
    });
    await page.goto("/design");
    await expect(page.getByRole("heading", { level: 1, name: /good morning/i })).toBeVisible();
    const outcome = await page.evaluate(async ({ scope, key, slot }) => {
      const modulePath = "/src/lib/invoiceCommands.ts";
      const { invoiceCommand } = await import(/* @vite-ignore */ modulePath);
      const validations: string[] = [];
      const adapt = (value: unknown, expectedKey: string, source: string) => {
        validations.push(source);
        const receipt = value as { status?: string; operation?: string; command_key?: string; target_id?: string; result?: { id?: string } };
        if (source === "write" || !receipt || receipt.status !== "committed" || receipt.operation !== "report_created" ||
            receipt.command_key !== expectedKey || receipt.target_id !== "adapter-report" || receipt.result?.id !== receipt.target_id) {
          throw new Error("Unverified response");
        }
        return receipt.result;
      };
      let result: unknown = null;
      let error: string | null = null;
      try {
        result = await invoiceCommand("/expenses", { items: [] }, scope, (commandKey: string) => `/expenses/commands/${commandKey}`, key, "/expenses/manual", false, undefined, "post", adapt);
      } catch (caught) { error = caught instanceof Error ? caught.message : String(caught); }
      return { result, error, validations, stored: sessionStorage.getItem(slot) };
    }, { scope: SCOPE, key: KEY, slot: SLOT });
    expect(outcome.validations).toEqual(["write", "lookup"]);
    expect(requests).toEqual(["POST", "GET"]);
    if (lookupValid) {
      expect(outcome.result).toEqual(REPORT);
      expect(outcome.error).toBeNull();
      expect(outcome.stored).toBeNull();
    } else {
      expect(outcome.result).toBeNull();
      expect(outcome.error).toMatch(/outcome is unknown/);
      expect(JSON.parse(outcome.stored!).key).toBe(KEY);
    }
  });
}

for (const transition of ["new-token", "generation-only", "raw-token", "round-trip"] as const) {
  for (const existing of [false, true]) {
    test(`a ${transition} transition during digest leaves ${existing ? "existing" : "empty"} metadata untouched and sends no command`, async ({ page }) => {
      const requests: { method: string; authorization?: string }[] = [];
      await page.route("**/api/v1/**", async (route) => {
        requests.push({ method: route.request().method(), authorization: route.request().headers().authorization });
        return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(REPORT) });
      });
      await page.goto("/design");
      await expect(page.getByRole("heading", { level: 1, name: /good morning/i })).toBeVisible();
      const outcome = await page.evaluate(async ({ scope, key, slot, transition, existing }) => {
        const helperPath = "/src/lib/invoiceCommands.ts";
        const apiPath = "/src/lib/api.ts";
        const { invoiceCommand } = await import(/* @vite-ignore */ helperPath);
        const { tokenStore, invalidateSessionRequests } = await import(/* @vite-ignore */ apiPath);
        tokenStore.set("synthetic-token-before");
        const payload = { items: [{ amount: "0.30" }] };
        const originalDigest = crypto.subtle.digest.bind(crypto.subtle);
        const bytes = await originalDigest("SHA-256", new TextEncoder().encode(JSON.stringify(payload)));
        const digest = Array.from(new Uint8Array(bytes), (byte) => byte.toString(16).padStart(2, "0")).join("");
        const saved = JSON.stringify({ key, digest });
        if (existing) sessionStorage.setItem(slot, saved);
        const unrelatedSlot = `${slot}:other-account`;
        sessionStorage.setItem(unrelatedSlot, "synthetic other account identity");
        let release: (() => void) | undefined;
        Object.defineProperty(crypto.subtle, "digest", { configurable: true, value: (algorithm: AlgorithmIdentifier, data: BufferSource) => new Promise<ArrayBuffer>((resolve, reject) => {
          release = () => { originalDigest(algorithm, data).then(resolve, reject); };
        }) });
        const pending = invoiceCommand("/expenses", payload, scope, (commandKey: string) => `/expenses/commands/${commandKey}`, key, "/expenses/manual")
          .then(() => null, (error: unknown) => error instanceof Error ? error.message : String(error));
        if (transition === "generation-only") invalidateSessionRequests();
        else if (transition === "raw-token") localStorage.setItem(tokenStore.key, "synthetic-token-after");
        else {
          tokenStore.set("synthetic-token-after");
          if (transition === "round-trip") tokenStore.set("synthetic-token-before");
        }
        if (!release) throw new Error("The real command did not reach digest.");
        release();
        Object.defineProperty(crypto.subtle, "digest", { configurable: true, value: originalDigest });
        const error = await pending;
        return { error, stored: sessionStorage.getItem(slot), saved, unrelated: sessionStorage.getItem(unrelatedSlot), token: tokenStore.get() };
      }, { scope: SCOPE, key: KEY, slot: SLOT, transition, existing });
      expect(outcome.error).toMatch(/Session changed/);
      expect(requests).toEqual([]);
      expect(outcome.stored).toBe(existing ? outcome.saved : null);
      expect(outcome.unrelated).toBe("synthetic other account identity");
      expect(outcome.token).toBe(transition === "new-token" || transition === "raw-token" ? "synthetic-token-after" : "synthetic-token-before");
    });
  }
}

test("a late prior-session POST response retains its command without lookup under the new token", async ({ page }) => {
  let release: (() => void) | undefined;
  let announce: (() => void) | undefined;
  const started = new Promise<void>((resolve) => { announce = resolve; });
  const requests: { method: string; authorization?: string }[] = [];
  await page.route("**/api/v1/**", async (route) => {
    requests.push({ method: route.request().method(), authorization: route.request().headers().authorization });
    if (route.request().method() === "POST") {
      const response = new Promise<void>((resolve) => { release = resolve; });
      announce!();
      await response;
    }
    return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(REPORT) });
  });
  await page.goto("/design");
  await expect(page.getByRole("heading", { level: 1, name: /good morning/i })).toBeVisible();
  const outcomePromise = page.evaluate(async ({ scope, key, slot }) => {
    const helperPath = "/src/lib/invoiceCommands.ts";
    const apiPath = "/src/lib/api.ts";
    const { invoiceCommand } = await import(/* @vite-ignore */ helperPath);
    const { tokenStore } = await import(/* @vite-ignore */ apiPath);
    tokenStore.set("synthetic-token-before");
    let error: string | null = null;
    try { await invoiceCommand("/expenses", { items: [] }, scope, (commandKey: string) => `/expenses/commands/${commandKey}`, key, "/expenses/manual"); }
    catch (caught) { error = caught instanceof Error ? caught.message : String(caught); }
    return { error, stored: sessionStorage.getItem(slot), token: tokenStore.get() };
  }, { scope: SCOPE, key: KEY, slot: SLOT });
  await started;
  await page.evaluate(async () => {
    const apiPath = "/src/lib/api.ts";
    const { tokenStore } = await import(/* @vite-ignore */ apiPath);
    tokenStore.set("synthetic-token-after");
  });
  release!();
  const outcome = await outcomePromise;
  expect(outcome.error).toMatch(/Session changed/);
  expect(requests).toEqual([{ method: "POST", authorization: "Bearer synthetic-token-before" }]);
  expect(JSON.parse(outcome.stored!).key).toBe(KEY);
  expect(outcome.token).toBe("synthetic-token-after");
});

test("a session transition after the saved lookup404 cannot start its replay POST", async ({ page }) => {
  const requests: { method: string; authorization?: string }[] = [];
  await page.route("**/api/v1/**", async (route) => {
    const method = route.request().method();
    requests.push({ method, authorization: route.request().headers().authorization });
    return route.fulfill({ status: method === "GET" ? 404 : 201, contentType: "application/json", body: JSON.stringify(method === "GET" ? { detail: "Not found" } : REPORT) });
  });
  await page.goto("/design");
  await expect(page.getByRole("heading", { level: 1, name: /good morning/i })).toBeVisible();
  const outcome = await page.evaluate(async ({ scope, key, slot }) => {
    const helperPath = "/src/lib/invoiceCommands.ts";
    const apiPath = "/src/lib/api.ts";
    const { invoiceCommand } = await import(/* @vite-ignore */ helperPath);
    const { api, tokenStore } = await import(/* @vite-ignore */ apiPath);
    tokenStore.set("synthetic-token-before");
    const payload = { items: [] };
    const bytes = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(JSON.stringify(payload)));
    const digest = Array.from(new Uint8Array(bytes), (byte) => byte.toString(16).padStart(2, "0")).join("");
    const saved = JSON.stringify({ key, digest });
    sessionStorage.setItem(slot, saved);
    // The existing identity interceptor has already classified this 404. A
    // following promise continuation changes session before the helper resumes.
    const interceptor = api.interceptors.response.use(undefined, (error: unknown) => {
      tokenStore.set("synthetic-token-after");
      return Promise.reject(error);
    });
    let error: string | null = null;
    try { await invoiceCommand("/expenses", payload, scope, (commandKey: string) => `/expenses/commands/${commandKey}`, key, "/expenses/manual"); }
    catch (caught) { error = caught instanceof Error ? caught.message : String(caught); }
    finally { api.interceptors.response.eject(interceptor); }
    return { error, saved, stored: sessionStorage.getItem(slot), token: tokenStore.get() };
  }, { scope: SCOPE, key: KEY, slot: SLOT });
  expect(requests).toEqual([{ method: "GET", authorization: "Bearer synthetic-token-before" }]);
  expect(outcome.error).toMatch(/Session changed/);
  expect(outcome.stored).toBe(outcome.saved);
  expect(outcome.token).toBe("synthetic-token-after");
});

for (const timing of ["before invocation", "during digest"] as const) {
  for (const existing of [false, true]) {
    test(`an abort ${timing} preserves ${existing ? "existing" : "empty"} metadata and sends no request`, async ({ page }) => {
      const requests: string[] = [];
      await page.route("**/api/v1/**", async (route) => {
        requests.push(route.request().method());
        return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(REPORT) });
      });
      await page.goto("/design");
      await expect(page.getByRole("heading", { level: 1, name: /good morning/i })).toBeVisible();
      const outcome = await page.evaluate(async ({ scope, key, slot, timing, existing }) => {
        const helperPath = "/src/lib/invoiceCommands.ts";
        const { invoiceCommand } = await import(/* @vite-ignore */ helperPath);
        const controller = new AbortController();
        const payload = { items: [] };
        const originalDigest = crypto.subtle.digest.bind(crypto.subtle);
        const bytes = await originalDigest("SHA-256", new TextEncoder().encode(JSON.stringify(payload)));
        const digest = Array.from(new Uint8Array(bytes), (byte) => byte.toString(16).padStart(2, "0")).join("");
        const saved = JSON.stringify({ key, digest });
        if (existing) sessionStorage.setItem(slot, saved);
        let digestCalls = 0;
        let release: (() => void) | undefined;
        Object.defineProperty(crypto.subtle, "digest", { configurable: true, value: (algorithm: AlgorithmIdentifier, data: BufferSource) => {
          digestCalls += 1;
          if (timing === "before invocation") return originalDigest(algorithm, data);
          return new Promise<ArrayBuffer>((resolve, reject) => { release = () => { originalDigest(algorithm, data).then(resolve, reject); }; });
        } });
        if (timing === "before invocation") controller.abort();
        const pending = invoiceCommand("/expenses", payload, scope, (commandKey: string) => `/expenses/commands/${commandKey}`, key, "/expenses/manual", false, undefined, "post", undefined, controller.signal)
          .then(() => null, (error: unknown) => error instanceof Error ? error.message : String(error));
        if (timing === "during digest") {
          if (!release) throw new Error("The real command did not reach digest.");
          controller.abort();
          release();
        }
        const error = await pending;
        Object.defineProperty(crypto.subtle, "digest", { configurable: true, value: originalDigest });
        return { error, stored: sessionStorage.getItem(slot), saved, digestCalls };
      }, { scope: SCOPE, key: KEY, slot: SLOT, timing, existing });
      expect(requests).toEqual([]);
      expect(outcome.error).toMatch(/interrupted/);
      expect(outcome.stored).toBe(existing ? outcome.saved : null);
      expect(outcome.digestCalls).toBe(timing === "before invocation" ? 0 : 1);
    });
  }
}

test("an abort after POST dispatch retains its unknown outcome reference and sends no recovery request", async ({ page }) => {
  let release: (() => void) | undefined;
  let announce: (() => void) | undefined;
  const started = new Promise<void>((resolve) => { announce = resolve; });
  const requests: string[] = [];
  await page.route("**/api/v1/**", async (route) => {
    const method = route.request().method();
    requests.push(method);
    if (method === "POST") {
      const response = new Promise<void>((resolve) => { release = resolve; });
      announce!();
      await response;
    }
    return route.fulfill({ status: 201, contentType: "application/json", body: JSON.stringify(REPORT) });
  });
  await page.goto("/design");
  await expect(page.getByRole("heading", { level: 1, name: /good morning/i })).toBeVisible();
  const outcomePromise = page.evaluate(async ({ scope, key, slot }) => {
    const helperPath = "/src/lib/invoiceCommands.ts";
    const { invoiceCommand } = await import(/* @vite-ignore */ helperPath);
    const controller = new AbortController();
    (window as unknown as { abortManualCommand: () => void }).abortManualCommand = () => controller.abort();
    let error: string | null = null;
    try { await invoiceCommand("/expenses", { items: [] }, scope, (commandKey: string) => `/expenses/commands/${commandKey}`, key, "/expenses/manual", false, undefined, "post", undefined, controller.signal); }
    catch (caught) { error = caught instanceof Error ? caught.message : String(caught); }
    return { error, stored: sessionStorage.getItem(slot) };
  }, { scope: SCOPE, key: KEY, slot: SLOT });
  await started;
  await page.evaluate(() => (window as unknown as { abortManualCommand: () => void }).abortManualCommand());
  release!();
  const outcome = await outcomePromise;
  expect(requests).toEqual(["POST"]);
  expect(outcome.error).toMatch(/interrupted/);
  expect(JSON.parse(outcome.stored!).key).toBe(KEY);
});

for (const status of [201, 403]) {
  test(`an abort after the API classifies POST${status} retains the reference before the helper resumes`, async ({ page }) => {
    const requests: string[] = [];
    await page.route("**/api/v1/**", async (route) => {
      requests.push(route.request().method());
      return route.fulfill({ status, contentType: "application/json", body: JSON.stringify(status === 201 ? REPORT : { detail: "Synthetic permission refusal" }) });
    });
    await page.goto("/design");
    await expect(page.getByRole("heading", { level: 1, name: /good morning/i })).toBeVisible();
    const outcome = await page.evaluate(async ({ scope, key, slot }) => {
      const helperPath = "/src/lib/invoiceCommands.ts";
      const apiPath = "/src/lib/api.ts";
      const { invoiceCommand } = await import(/* @vite-ignore */ helperPath);
      const { api } = await import(/* @vite-ignore */ apiPath);
      const controller = new AbortController();
      const interceptor = api.interceptors.response.use(
        (response: unknown) => { controller.abort(); return response; },
        (error: unknown) => { controller.abort(); return Promise.reject(error); },
      );
      let error: string | null = null;
      try { await invoiceCommand("/expenses", { items: [] }, scope, (commandKey: string) => `/expenses/commands/${commandKey}`, key, "/expenses/manual", false, undefined, "post", undefined, controller.signal); }
      catch (caught) { error = caught instanceof Error ? caught.message : String(caught); }
      finally { api.interceptors.response.eject(interceptor); }
      return { error, stored: sessionStorage.getItem(slot) };
    }, { scope: SCOPE, key: KEY, slot: SLOT });
    expect(requests).toEqual(["POST"]);
    expect(outcome.error).toMatch(/interrupted/);
    expect(JSON.parse(outcome.stored!).key).toBe(KEY);
  });
}

for (const mode of ["already-aborted", "classified200-abort", "classified404-abort", "classified200-session", "ordinary404"] as const) {
  test(`a saved lookup with ${mode} preserves its command reference`, async ({ page }) => {
    const requests: string[] = [];
    await page.route("**/api/v1/**", async (route) => {
      requests.push(route.request().method());
      const status = mode.includes("404") ? 404 : 200;
      return route.fulfill({ status, contentType: "application/json", body: JSON.stringify(status === 404 ? { detail: "Not found" } : REPORT) });
    });
    await page.goto("/design");
    await expect(page.getByRole("heading", { level: 1, name: /good morning/i })).toBeVisible();
    const outcome = await page.evaluate(async ({ mode, scope, key, slot }) => {
      const helperPath = "/src/lib/invoiceCommands.ts";
      const apiPath = "/src/lib/api.ts";
      const { checkSavedInvoiceCommand } = await import(/* @vite-ignore */ helperPath);
      const { api, tokenStore } = await import(/* @vite-ignore */ apiPath);
      tokenStore.set("synthetic-token-before");
      const saved = JSON.stringify({ key, digest: "a".repeat(64) });
      sessionStorage.setItem(slot, saved);
      const controller = new AbortController();
      if (mode === "already-aborted") controller.abort();
      const changeContext = () => {
        if (mode === "classified200-session") tokenStore.set("synthetic-token-after");
        else if (mode.startsWith("classified")) controller.abort();
      };
      const interceptor = api.interceptors.response.use(
        (response: unknown) => { changeContext(); return response; },
        (error: unknown) => { changeContext(); return Promise.reject(error); },
      );
      let result: unknown = null;
      let error: string | null = null;
      try { result = await checkSavedInvoiceCommand("/expenses/manual", scope, (commandKey: string) => `/expenses/commands/${commandKey}`, undefined, mode === "classified200-session" ? undefined : controller.signal); }
      catch (caught) { error = caught instanceof Error ? caught.message : String(caught); }
      finally { api.interceptors.response.eject(interceptor); }
      return { result, error, saved, stored: sessionStorage.getItem(slot), token: tokenStore.get() };
    }, { mode, scope: SCOPE, key: KEY, slot: SLOT });
    expect(requests).toEqual(mode === "already-aborted" ? [] : ["GET"]);
    expect(outcome.result).toBeNull();
    if (mode === "ordinary404") expect(outcome.error).toBeNull();
    else expect(outcome.error).toMatch(mode === "classified200-session" ? /Session changed/ : /interrupted/);
    expect(outcome.stored).toBe(outcome.saved);
    expect(outcome.token).toBe(mode === "classified200-session" ? "synthetic-token-after" : "synthetic-token-before");
  });
}

test("an interrupted saved lookup retains metadata without inferring any new write", async ({ page }) => {
  let release: (() => void) | undefined;
  let announce: (() => void) | undefined;
  const started = new Promise<void>((resolve) => { announce = resolve; });
  const requests: string[] = [];
  await page.route("**/api/v1/**", async (route) => {
    requests.push(route.request().method());
    const response = new Promise<void>((resolve) => { release = resolve; });
    announce!();
    await response;
    return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(REPORT) });
  });
  await page.goto("/design");
  await expect(page.getByRole("heading", { level: 1, name: /good morning/i })).toBeVisible();
  const outcomePromise = page.evaluate(async ({ scope, key, slot }) => {
    const helperPath = "/src/lib/invoiceCommands.ts";
    const { checkSavedInvoiceCommand } = await import(/* @vite-ignore */ helperPath);
    const controller = new AbortController();
    (window as unknown as { abortManualLookup: () => void }).abortManualLookup = () => controller.abort();
    const saved = JSON.stringify({ key, digest: "a".repeat(64) });
    sessionStorage.setItem(slot, saved);
    let error: string | null = null;
    try { await checkSavedInvoiceCommand("/expenses/manual", scope, (commandKey: string) => `/expenses/commands/${commandKey}`, undefined, controller.signal); }
    catch (caught) { error = caught instanceof Error ? caught.message : String(caught); }
    return { error, saved, stored: sessionStorage.getItem(slot) };
  }, { scope: SCOPE, key: KEY, slot: SLOT });
  await started;
  await page.evaluate(() => (window as unknown as { abortManualLookup: () => void }).abortManualLookup());
  release!();
  const outcome = await outcomePromise;
  expect(requests).toEqual(["GET"]);
  expect(outcome.error).toMatch(/interrupted/);
  expect(outcome.stored).toBe(outcome.saved);
});
