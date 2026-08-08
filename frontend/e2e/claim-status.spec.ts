import { test, expect, type Page, type Route } from "@playwright/test";
import { readFileSync, readdirSync, statSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

/**
 * The CLIENT CLAIM-STATUS portal (WO-93) over the LIVE app shell, API mocked via
 * `page.route` — the deterministic pattern of `excise.spec.ts`, no backend
 * involved.
 *
 * What this suite is actually proving:
 *  - the six harvested stages and the claims table render the WO-93 wire shape
 *    field for field, with the SERVER's own label and sentence for each stage,
 *  - **R39's three absences survive contact with a UI**: no internal workflow
 *    identifier reaches the screen (every stage cell is asserted to carry a
 *    label from the response and nothing else), no wording about what we charge
 *    and no action verb appears in the new modules (scanned at source, with a
 *    seeded-violation self-test so the scan cannot quietly stop working), and
 *    the results panel renders NO control at all — not a disabled one, none,
 *  - **the SPA invents no wording**: every stage label and sentence comes off
 *    the wire, and none of them exists anywhere under `src/`,
 *  - a claim with no stateable figure renders a DASH, never €0.00,
 *  - money renders from the wire STRING with no float round-trip anywhere —
 *    asserted both on a value a double cannot hold and by a source grep,
 *  - permission-gated visibility for the nav entry (granted/denied), module
 *    gating, and the loading / empty / error / refusal states.
 *
 * Synthetic fixtures only (master context §10): fictional company names and
 * figures. No Fleet Fuel bytes, and no literal shaped like a real VAT id, IBAN
 * or registration number.
 */

const ORG = { id: "org-1", name: "Test Workspace", status: "active" };

type Role = "owner" | "finance_manager" | "accountant" | "auditor" | "user" | "user_free";

function user(role: Role) {
  return {
    id: "user-1",
    email: "client@test.io",
    name: "Test Client",
    role,
    org_id: "org-1",
    is_platform_admin: false,
  };
}

const TRANSPORT_MODULE = {
  key: "transport",
  name: "Transport & VAT refunds (EU cross-border)",
  description: "",
  core: false,
  enabled: true,
  requires_issuer: false,
  ready: true,
};

// More significant digits than an IEEE-754 double can hold:
// Number("99999999999999.99") is 100000000000000. If any float path existed
// between the wire and the screen, these digits would not survive.
const HUGE = "99999999999999.99";

/**
 * The six labels and sentences `app/services/transport/client_status.py`
 * actually emits, copied here as FIXTURES. The page must render whatever the
 * wire says — that is the whole reason they ride the response — and the
 * "no wording of its own" test below proves none of them lives under
 * `frontend/src/`.
 */
const STAGE_COPY: Record<string, { label: string; description: string }> = {
  prep: {
    label: "In preparation",
    description: "We are still putting this claim together for you.",
  },
  ready: {
    label: "Ready for filing",
    description: "Everything this claim needs is in place.",
  },
  filed: {
    label: "Filed with the tax authority",
    description: "This claim is with the refunding country's tax authority.",
  },
  awaiting: {
    label: "Decision received, money on its way",
    description: "The country has decided. The money has not arrived yet.",
  },
  refunded: {
    label: "Refunded",
    description: "The refund for this claim has arrived.",
  },
  needs_attention: {
    label: "Needs attention",
    description: "Something is holding this claim up. We will be in touch.",
  },
};

const STAGE_ORDER = ["prep", "ready", "filed", "awaiting", "refunded", "needs_attention"];

function stages(counts: Record<string, number>, euros: Record<string, string> = {}) {
  return STAGE_ORDER.map((stage) => ({
    stage,
    label: STAGE_COPY[stage].label,
    description: STAGE_COPY[stage].description,
    claims: counts[stage] ?? 0,
    vat_eur: euros[stage] ?? "0.00",
  }));
}

const PORTAL = {
  year: 2026,
  currency: "EUR",
  total_claims: 4,
  stages: stages(
    { ready: 1, filed: 1, refunded: 1, needs_attention: 1 },
    { ready: "1250.00", filed: "3180.00", refunded: HUGE, needs_attention: "0.00" },
  ),
  claims: [
    {
      entity_name: "Northbound Haulage OU",
      refund_country: "LV",
      period: "2026-Q2",
      stage: "ready",
      vat_eur: "1250.00",
    },
    {
      entity_name: "Northbound Haulage OU",
      refund_country: "LT",
      period: "2026-Q1",
      stage: "filed",
      vat_eur: "3180.00",
    },
    {
      entity_name: "Southbound Freight SIA",
      refund_country: "PL",
      period: "2026-Q1",
      stage: "refunded",
      vat_eur: HUGE,
    },
    {
      // No single-currency figure can be stated for this one — the wire carries
      // null and the page must show a dash, never €0.00.
      entity_name: "Southbound Freight SIA",
      refund_country: "EE",
      period: "2026-Q3",
      stage: "needs_attention",
      vat_eur: null,
    },
  ],
  not_shown_claims: 1,
};

const EMPTY_PORTAL = {
  year: 2019,
  currency: "EUR",
  total_claims: 0,
  stages: stages({}),
  claims: [],
  not_shown_claims: 0,
};

interface MockOpts {
  role?: Role;
  moduleEnabled?: boolean;
  portal?: unknown;
  refuse?: Record<string, { status: number; code: string; detail: string }>;
  /** Fail the portal GET with a non-refusal HTTP error (the QueryState lane). */
  status?: number;
  delayMs?: number;
}

async function mockApi(page: Page, opts: MockOpts = {}): Promise<void> {
  const {
    role = "user_free",
    moduleEnabled = true,
    portal = PORTAL,
    refuse = {},
    status,
    delayMs = 0,
  } = opts;

  await page.addInitScript(() => localStorage.setItem("invoiceiq_token", "e2e-token"));

  const json = (body: unknown, code = 200) => ({
    status: code,
    contentType: "application/json",
    body: JSON.stringify(body),
  });

  await page.route("**/api/v1/**", async (route: Route) => {
    const url = new URL(route.request().url());
    const path = url.pathname.replace(/^.*\/api\/v1/, "") + url.search;

    if (path === "/auth/me") return route.fulfill(json({ user: user(role), organization: ORG }));
    if (path === "/auth/organizations") return route.fulfill(json([ORG]));
    if (path === "/modules")
      return route.fulfill(json([{ ...TRANSPORT_MODULE, enabled: moduleEnabled }]));

    for (const [fragment, r] of Object.entries(refuse)) {
      if (path.includes(fragment)) {
        return route.fulfill(json({ detail: r.detail, code: r.code }, r.status));
      }
    }

    if (path.startsWith("/transport/claim-status")) {
      if (delayMs) await new Promise((res) => setTimeout(res, delayMs));
      if (status && status >= 400)
        return route.fulfill(json({ detail: "boom", code: "mocked_failure" }, status));
      return route.fulfill(json(portal));
    }

    return route.fulfill(json({}));
  });
}

async function open(page: Page, opts: MockOpts = {}) {
  await mockApi(page, opts);
  await page.goto("/claim-status");
}

/** The page body — the `<main>` landmark the app shell already renders (no test
 * hook is added to production code for this: every other spec in this suite
 * selects by role and name, and so does this one). Scoping to it keeps every
 * assertion off the shell's own navigation and chrome. */
function panel(page: Page) {
  return page.getByRole("main");
}

// ---------------------------------------------------------------------------
// 1. The stages
// ---------------------------------------------------------------------------

test("stages: each stage renders its server-owned label, sentence and count", async ({ page }) => {
  await open(page);
  for (const stage of STAGE_ORDER) {
    await expect(panel(page).getByText(STAGE_COPY[stage].label, { exact: true }).first()).toBeVisible();
    await expect(panel(page).getByText(STAGE_COPY[stage].description).first()).toBeVisible();
  }
});

test("stages: all six render even when the year is empty", async ({ page }) => {
  await open(page, { portal: EMPTY_PORTAL });
  for (const stage of STAGE_ORDER) {
    await expect(panel(page).getByText(STAGE_COPY[stage].label, { exact: true }).first()).toBeVisible();
  }
});

// ---------------------------------------------------------------------------
// 2. The claims
// ---------------------------------------------------------------------------

test("claims: each row renders the company, country, period, stage and amount", async ({
  page,
}) => {
  await open(page);
  const row = page.getByRole("row", { name: /Northbound Haulage OU/ }).first();
  await expect(row).toContainText("LV");
  await expect(row).toContainText("2026-Q2");
  await expect(row).toContainText("Ready for filing");
  await expect(row).toContainText("€1,250.00");

  const filed = page.getByRole("row", { name: /2026-Q1/ }).first();
  await expect(filed).toContainText("Filed with the tax authority");
  await expect(filed).toContainText("€3,180.00");
});

test("claims: a claim with no stateable figure renders a dash, never €0.00", async ({ page }) => {
  await open(page);
  const row = page.getByRole("row", { name: /Southbound Freight SIA/ }).nth(1);
  await expect(row).toContainText("EE");
  await expect(row).toContainText("Needs attention");
  await expect(row).toContainText("—");
  // The claims table must not describe that claim with a zero.
  await expect(row).not.toContainText("€0.00");
  await expect(
    panel(page).getByText(/A dash means we cannot state a single figure/),
  ).toBeVisible();
});

test("claims: the count of claims outside the ladder is explained, never named", async ({
  page,
}) => {
  await open(page);
  await expect(panel(page).getByText(/Claims we are no longer pursuing/)).toBeVisible();
  // The internal engine vocabulary for WHY is not on the screen.
  await expect(panel(page)).not.toContainText("withdrawn");
  await expect(panel(page)).not.toContainText("rejected");
});

// ---------------------------------------------------------------------------
// 3. Empty, loading, error, refusal
// ---------------------------------------------------------------------------

test("empty: a year with no claims renders the empty state, not an error", async ({ page }) => {
  await open(page, { portal: EMPTY_PORTAL });
  await expect(page.getByText("No refund claims for this year yet")).toBeVisible();
  await expect(page.getByRole("alert")).toHaveCount(0);
  await expect(page.getByRole("table")).toHaveCount(0);
});

test("loading: a loading state renders before the API resolves", async ({ page }) => {
  await mockApi(page, { delayMs: 1500 });
  await page.goto("/claim-status");
  await expect(page.getByRole("heading", { name: "Claim status" })).toBeVisible();
  await expect(page.getByText("Northbound Haulage OU")).toHaveCount(0);
});

test("error: a 500 renders the error state, not a refusal", async ({ page }) => {
  await open(page, { status: 500 });
  await expect(page.getByText("Couldn’t load your claims")).toBeVisible();
});

test("refusal: invalid_year renders its sentence, not the slug", async ({ page }) => {
  await open(page, {
    refuse: {
      "/transport/claim-status": {
        status: 422,
        code: "invalid_year",
        detail: "'99999' is not a valid refund year — use a four-digit year",
      },
    },
  });
  const alert = page.getByRole("alert");
  await expect(alert).toContainText("That isn’t a refund year this service can report on");
  await expect(page.getByText("invalid_year", { exact: true })).toHaveCount(0);
});

test("perm: a server 403 renders through the refusal path", async ({ page }) => {
  await open(page, {
    refuse: {
      "/transport/claim-status": {
        status: 403,
        code: "module_not_enabled",
        detail: "The Transport & VAT refunds module is not activated.",
      },
    },
  });
  await expect(page.getByRole("alert")).toContainText("module isn’t active for this workspace");
});

// ---------------------------------------------------------------------------
// 4. Permission and module gating
// ---------------------------------------------------------------------------

test("perm: vat.read gates the nav entry (granted / denied)", async ({ page }) => {
  // READ_ONLY (`user_free`) is the CLIENT this surface exists for, and it holds
  // VAT_READ in `app/core/authz.py::ROLE_PERMISSIONS`.
  await mockApi(page, { role: "user_free" });
  await page.goto("/claim-status");
  await expect(
    page.getByRole("navigation").getByRole("link", { name: "Claim status" }),
  ).toBeVisible();

  // EMPLOYEE (`user`) holds no VAT permission at all.
  await mockApi(page, { role: "user" });
  await page.goto("/claim-status");
  await expect(
    page.getByRole("navigation").getByRole("link", { name: "Claim status" }),
  ).toHaveCount(0);
});

test("perm: the client role sees every figure on the page", async ({ page }) => {
  await open(page, { role: "user_free" });
  await expect(panel(page).getByText("€1,250.00").first()).toBeVisible();
  await expect(panel(page).getByText("€99,999,999,999,999.99").first()).toBeVisible();
});

test("module: transport off renders the module notice and no nav entry", async ({ page }) => {
  await mockApi(page, { moduleEnabled: false });
  await page.goto("/claim-status");
  await expect(page.getByText(/Transport & VAT refunds/).first()).toBeVisible();
  await expect(page.getByRole("table")).toHaveCount(0);
  await expect(
    page.getByRole("navigation").getByRole("link", { name: "Claim status" }),
  ).toHaveCount(0);
});

// ---------------------------------------------------------------------------
// 5. R39 — the three absences (the governing constraint)
// ---------------------------------------------------------------------------

/**
 * The internal workflow vocabulary `app/services/transport/status.py` defines
 * (`AUTO_CODES` + `MANUAL_CODES`) plus `"2"`, which `MANUAL_CODES` excludes
 * because only `lock.submit_claim` stamps it.
 *
 * Only the MULTI-character ones can be searched for in rendered text: a
 * single-character code is indistinguishable from a legitimate claim COUNT on a
 * page that renders counts. That is precisely why the constraint's primary
 * enforcement is at the WIRE — `tests/transport/test_wo93_client_surface.py`
 * asserts no leaf string of the response equals ANY code, single-character ones
 * included — and why the stage cells are asserted below to carry a label from
 * the response and nothing else.
 */
const MULTI_CHAR_CODES = ["1A", "1B", "1C", "1E", "2A", "2B", "3A", "3B", "3C", "3D", "4A"];

test("constraint: no internal workflow identifier appears anywhere on the page", async ({
  page,
}) => {
  await open(page);
  await expect(panel(page).getByRole("table")).toBeVisible();
  const body = (await page.locator("body").innerText()).toUpperCase();
  for (const code of MULTI_CHAR_CODES) {
    expect(new RegExp(`\\b${code}\\b`).test(body), `${code} reached the client's screen`).toBe(
      false,
    );
  }
});

test("constraint: every stage cell carries a label from the response, nothing else", async ({
  page,
}) => {
  await open(page);
  // Wait for the panel before reading the DOM: an immediate read would race the
  // query and pass on an empty page, which would prove nothing.
  await expect(panel(page).getByRole("table")).toBeVisible();
  const labels = new Set(Object.values(STAGE_COPY).map((c) => c.label));
  const badges = await panel(page).locator("table tbody span").allInnerTexts();
  expect(badges.length, "the claims table must render a stage per row").toBeGreaterThan(0);
  for (const text of badges) {
    expect(labels.has(text.trim()), `a stage cell rendered "${text}"`).toBe(true);
  }
});

test("constraint: the results panel renders no control at all", async ({ page }) => {
  await open(page);
  await expect(panel(page).getByRole("table")).toBeVisible();
  // A read-only view means NO control, not a disabled one. The ONLY interactive
  // element on the whole page is the year filter — which selects what to READ
  // and does nothing to a claim — so it is named explicitly and everything else
  // is asserted absent.
  await expect(panel(page).getByRole("button")).toHaveCount(0);
  await expect(panel(page).getByRole("link")).toHaveCount(0);
  await expect(panel(page).getByRole("combobox")).toHaveCount(0);
  await expect(panel(page).getByRole("checkbox")).toHaveCount(0);
  const boxes = panel(page).getByRole("textbox");
  await expect(boxes).toHaveCount(1);
  await expect(boxes).toHaveValue(/^\d{4}$/);
});

/**
 * The vocabulary this surface must not use, in any identifier, string or
 * comment of the new modules: what we CHARGE for the work, the internal
 * identifier field, and the app's own ACTION verbs. Note what is deliberately
 * NOT here — `filed` and `refunded` are the spec's own STAGE names; this list
 * names the application's actions, not the English language.
 */
const FORBIDDEN =
  /\bfees?\b|\bcommission\w*|\bpayout\w*|\bbilled\b|status_code|\bsubmit\w*|\bwithdraw\w*|\bapprove\w*|\bfreez\w*|\block\w*|\bwaive\w*|\boverride\w*|\bpackage\w*|\bsend\w*|\bconfirm\w*/gi;

function forbiddenHits(source: string): string[] {
  return source.match(FORBIDDEN) ?? [];
}

function here(): string {
  return dirname(fileURLToPath(import.meta.url));
}

function readSource(rel: string): string {
  return readFileSync(join(here(), "..", rel), "utf8");
}

function walk(dir: string, out: string[] = []): string[] {
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) walk(full, out);
    else out.push(full);
  }
  return out;
}

test("constraint: the new modules carry no charge, identifier or action vocabulary", () => {
  for (const rel of ["src/pages/ClaimStatus.tsx", "src/lib/transportClaimStatus.ts"]) {
    expect(forbiddenHits(readSource(rel)), `${rel} must carry none of it`).toEqual([]);
  }
});

test("constraint: the new wire interfaces carry no forbidden vocabulary", () => {
  const types = readSource("src/lib/types.ts");
  for (const name of ["ClientClaimStage", "ClientClaimRow", "ClientClaimStatus"]) {
    const m = new RegExp(`export interface ${name}\\s*\\{([^}]*)\\}`).exec(types);
    expect(m, `${name} must exist in lib/types.ts`).not.toBeNull();
    // Field names only — the surrounding prose explains WHY the vocabulary is
    // absent and necessarily names it.
    const fields = (m as RegExpExecArray)[1]
      .split("\n")
      .filter((line) => /^\s*\w+\??:/.test(line))
      .join("\n");
    expect(forbiddenHits(fields), `${name} field names`).toEqual([]);
  }
});

test("constraint: the vocabulary scan detects a seeded violation", () => {
  // A scan that cannot fail proves nothing. Each of these is a plausible way
  // R39 would be undone by a well-meaning edit.
  expect(forbiddenHits("<th>Our fee</th>")).not.toEqual([]);
  expect(forbiddenHits("const fee = claim.fee_eur;")).not.toEqual([]);
  expect(forbiddenHits("<span>{c.status_code}</span>")).not.toEqual([]);
  expect(forbiddenHits("<Button>Submit this claim</Button>")).not.toEqual([]);
  expect(forbiddenHits("// let the client withdraw it here")).not.toEqual([]);
  expect(forbiddenHits("<Button>Confirm</Button>")).not.toEqual([]);
  // …and does not fire on the spec's own stage vocabulary or innocent prose.
  expect(forbiddenHits("Filed with the tax authority. Refunded. In preparation.")).toEqual([]);
  expect(forbiddenHits("the refund has arrived and the claim is closed")).toEqual([]);
});

test("constraint: the SPA holds no stage wording of its own", () => {
  // Every label and sentence lives in `app/services/transport/client_status.py`
  // and crosses the wire. If any were ALSO in the SPA, a re-wording here could
  // soften what the client is told, and the two copies would drift silently.
  //
  // TWO SCOPES, for a reason found while writing this spec rather than assumed.
  // The six DESCRIPTIONS are long, unique sentences, so a copy of one anywhere
  // under `src/` is unambiguously a duplicate: they are scanned tree-wide. The
  // LABELS are short, and short English phrases legitimately recur on OTHER
  // surfaces meaning other things — `lib/transportRecovery.ts` names an
  // operator READINESS BUCKET "Refunded", and `pages/CashPosition.tsx` has its
  // own "Needs attention". Those are different vocabularies for different
  // audiences and neither can reach this page, so the label scan is scoped to
  // the two modules that COULD render one: a hard-coded label there would be a
  // fallback this page could actually show. (The `ready` stage was renamed
  // "Ready for filing" for the same finding — the operator bucket already says
  // "Ready to file" and the two mean different things.)
  const files = walk(join(here(), "..", "src"));
  const sentences = Object.values(STAGE_COPY).map((c) => c.description);
  for (const file of files) {
    const src = readFileSync(file, "utf8");
    for (const needle of sentences) {
      expect(src.includes(needle), `${file} must not restate: ${needle}`).toBe(false);
    }
  }
  for (const rel of ["src/pages/ClaimStatus.tsx", "src/lib/transportClaimStatus.ts"]) {
    const src = readSource(rel);
    for (const { label } of Object.values(STAGE_COPY)) {
      expect(src.includes(label), `${rel} must not hold the label: ${label}`).toBe(false);
    }
  }
});

// ---------------------------------------------------------------------------
// 6. Money
// ---------------------------------------------------------------------------

test("money: every euro renders from the wire string with no float round-trip", async ({
  page,
}) => {
  await open(page);
  // Number("99999999999999.99") is 100000000000000 — these digits only survive
  // if the value was never a number.
  await expect(panel(page).getByText("€99,999,999,999,999.99").first()).toBeVisible();
  await expect(panel(page).getByText("€1,250.00").first()).toBeVisible();
  await expect(panel(page).getByText("€3,180.00").first()).toBeVisible();
});

test("money: the new modules perform no float arithmetic on an amount", () => {
  // `parseFloat`/`Number(`/`toFixed`/`Math.` are the four ways a decimal string
  // becomes an IEEE-754 double in this codebase. None may appear: every figure
  // is the server's own string, rendered through `decimalMoney` (string
  // surgery, no arithmetic).
  const banned = [/parseFloat/, /\bNumber\s*\(/, /toFixed/, /\bMath\./];
  for (const rel of ["src/pages/ClaimStatus.tsx", "src/lib/transportClaimStatus.ts"]) {
    const src = readSource(rel);
    for (const pattern of banned) {
      expect(pattern.test(src), `${rel} must not contain ${pattern}`).toBe(false);
    }
  }
});
