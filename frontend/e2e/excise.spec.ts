import { test, expect, type Page, type Route } from "@playwright/test";
import { readFileSync, readdirSync, statSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

/**
 * The DIESEL-EXCISE screen (WO-92) over the LIVE app shell, API mocked via
 * `page.route` — the deterministic pattern of `savings.spec.ts`, no backend
 * involved.
 *
 * What this suite is actually proving:
 *  - the two panels render the WO-91 wire shapes field for field,
 *  - **the eligibility non-assertion survives contact with a UI**: the
 *    statement, the rate caveat, the framing, the customs addressee and the
 *    litre basis are rendered VERBATIM off the wire, the frontend holds none of
 *    those strings anywhere under `src/`, `eligibility_asserted: false` is
 *    stated rather than dropped, and the new modules borrow no word from the
 *    vocabulary family that would turn an indicative figure into a sum somebody
 *    is said to be holding for this haulier (scanned at source, with a
 *    seeded-violation self-test so the scan itself cannot quietly stop working),
 *  - **a country with no rate held is no finding, never €0.00** — it is listed
 *    with its litres and its line count and no euro appears anywhere in that
 *    table,
 *  - every rate is shown beside its source, so the shipped placeholder is never
 *    presented as a statutory figure,
 *  - the customs packet downloads with the current scope, and each of the
 *    surface's six refusal codes renders a human sentence rather than a slug,
 *  - money renders from the wire STRING with no float round-trip anywhere —
 *    asserted both on a value a double cannot hold and by a source grep,
 *  - permission-gated visibility (granted/denied) for the nav entry AND for the
 *    two rate writes, module gating, and the loading / empty / error / refusal
 *    states on both panels.
 *
 * Synthetic fixtures only (master context §10): fictional entity names and
 * figures. No Fleet Fuel bytes, and no literal shaped like a real VAT id, IBAN
 * or registration number.
 */

const ORG = { id: "org-1", name: "Test Workspace", status: "active" };

type Role =
  | "owner"
  | "finance_manager"
  | "accountant"
  | "auditor"
  | "user"
  | "user_free";

function user(role: Role) {
  return {
    id: "user-1",
    email: "operator@test.io",
    name: "Test Operator",
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

// The five constants `app/services/transport/excise.py` actually emits, copied
// here as FIXTURES. The page must render whatever the wire says — that is the
// whole reason they ride the response — and the last test in this file proves
// none of them lives anywhere under `frontend/src/`.
const ELIGIBILITY =
  "This figure asserts NO eligibility. The conditions that qualify a haulier for a " +
  "commercial-diesel excise refund — vehicle weight (>= 7.5 t) and carrier registration — " +
  "are deliberately not modelled by this product. Read it as excise you may be able to " +
  "reclaim IF you qualify, never as excise you are owed. Confirm eligibility with the " +
  "customs authority of the country of supply before filing.";
const RATE_CAVEAT =
  "Rates are indicative defaults, not statutory figures. The default of " +
  "EUR 30.00 per 1,000 litres is an explicit placeholder in a reported EUR 25-33 band; " +
  "the duty is not harmonised across the EU and national rates are revised (quarterly, " +
  "per the underlying research). Verify the rate with customs and override it here.";
const FRAMING = "Indicative / advisory — verify before relying";
const FILED_WITH =
  "Customs authority of the country of supply (a separate regime from the VAT refund)";
const LITRE_BASIS =
  "Validated diesel litres as invoiced; the rate is NET EUR per 1,000 litres";

const REPORT = {
  period: "2026-05",
  entity_id: null,
  country: null,
  currency: "EUR",
  product_group: "Diesel",
  litre_basis: LITRE_BASIS,
  legal_framing: FRAMING,
  eligibility: ELIGIBILITY,
  eligibility_asserted: false,
  rate_caveat: RATE_CAVEAT,
  filed_with: FILED_WITH,
  rows: [
    {
      entity_id: "ent-north",
      entity_name: "Northbound Haulage",
      country: "FR",
      litres: "12400.000",
      rate_eur_per_1000l: "30.0000",
      rate_is_override: false,
      indicative_excise_eur: HUGE,
      lines: 7,
    },
    {
      entity_id: "ent-south",
      entity_name: "Southbound Freight",
      country: "IT",
      litres: "2100.000",
      rate_eur_per_1000l: "22.5000",
      rate_is_override: true,
      indicative_excise_eur: "47.25",
      lines: 3,
    },
  ],
  skipped_countries: [{ country: "LV", litres: "8100.000", lines: 4 }],
  litres: "14500.000",
  indicative_excise_eur: HUGE,
  lines_examined: 14,
};

const REPORT_EMPTY = {
  ...REPORT,
  rows: [],
  skipped_countries: [],
  litres: "0.000",
  indicative_excise_eur: "0.00",
  lines_examined: 0,
};

const COUNTRIES = ["BE", "ES", "FR", "HR", "HU", "IT", "SI"];

const RATES = {
  countries: COUNTRIES,
  default_rate_eur_per_1000l: "30.0000",
  rates: COUNTRIES.map((country) => ({
    country,
    rate_eur_per_1000l: country === "IT" ? "22.5000" : "30.0000",
    is_override: country === "IT",
    rate_caveat: RATE_CAVEAT,
  })),
  eligibility: ELIGIBILITY,
  eligibility_asserted: false,
  rate_caveat: RATE_CAVEAT,
  legal_framing: FRAMING,
  currency: "EUR",
};

interface MockOpts {
  role?: Role;
  moduleEnabled?: boolean;
  report?: unknown;
  rates?: unknown;
  /** Refusals keyed by a path fragment, optionally narrowed to one method. */
  refuse?: Record<
    string,
    { status: number; code: string; detail: string; method?: string }
  >;
  /** Fail the report GET with a non-refusal HTTP error (the QueryState lane). */
  status?: number;
  delayMs?: number;
  /** Collects every API request the page issued, for the write/download proofs. */
  seen?: { method: string; path: string; body: string }[];
}

async function mockApi(page: Page, opts: MockOpts = {}): Promise<void> {
  const {
    role = "owner",
    moduleEnabled = true,
    report = REPORT,
    rates = RATES,
    refuse = {},
    status,
    delayMs = 0,
    seen,
  } = opts;

  await page.addInitScript(() =>
    localStorage.setItem("invoiceiq_token", "e2e-token"),
  );

  const json = (body: unknown, code = 200) => ({
    status: code,
    contentType: "application/json",
    body: JSON.stringify(body),
  });

  await page.route("**/api/v1/**", async (route: Route) => {
    const url = new URL(route.request().url());
    const path = url.pathname.replace(/^.*\/api\/v1/, "") + url.search;
    const method = route.request().method();
    if (seen)
      seen.push({ method, path, body: route.request().postData() ?? "" });

    if (path === "/auth/me")
      return route.fulfill(json({ user: user(role), organization: ORG }));
    if (path === "/auth/organizations") return route.fulfill(json([ORG]));
    if (path === "/modules")
      return route.fulfill(
        json([{ ...TRANSPORT_MODULE, enabled: moduleEnabled }]),
      );

    for (const [fragment, r] of Object.entries(refuse)) {
      if (path.includes(fragment) && (!r.method || r.method === method)) {
        return route.fulfill(json({ detail: r.detail, code: r.code }, r.status));
      }
    }

    if (path.startsWith("/transport/excise")) {
      if (delayMs) await new Promise((res) => setTimeout(res, delayMs));
      if (path.startsWith("/transport/excise/rates"))
        return route.fulfill(json(rates));
      if (path.startsWith("/transport/excise/packet"))
        return route.fulfill({
          status: 200,
          contentType:
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
          body: "PK-not-a-real-workbook",
        });
      if (status && status >= 400)
        return route.fulfill(
          json({ detail: "boom", code: "mocked_failure" }, status),
        );
      return route.fulfill(json(report));
    }

    return route.fulfill(json({}));
  });
}

async function open(page: Page, tab: string, opts: MockOpts = {}) {
  await mockApi(page, opts);
  await page.goto("/excise");
  if (tab !== "Monthly figures")
    await page.getByRole("tab", { name: tab }).click();
}

// ---------------------------------------------------------------------------
// 1. The month's figures
// ---------------------------------------------------------------------------

test("figures: each row renders its entity, country, litres, the rate and the euro", async ({
  page,
}) => {
  await open(page, "Monthly figures");
  const row = page.getByRole("row", { name: /Northbound Haulage/ }).first();
  await expect(row).toContainText("ent-north");
  await expect(row).toContainText("FR");
  await expect(row).toContainText("12400.000");
  await expect(row).toContainText("30.0000");
  await expect(row).toContainText("7");

  const second = page.getByRole("row", { name: /Southbound Freight/ }).first();
  await expect(second).toContainText("IT");
  await expect(second).toContainText("2100.000");
  await expect(second).toContainText("22.5000");
  await expect(second).toContainText("€47.25");
});

test("figures: the rate source is visible on every row", async ({ page }) => {
  await open(page, "Monthly figures");
  await expect(
    page.getByRole("row", { name: /Northbound Haulage/ }).first(),
  ).toContainText("Indicative default");
  await expect(
    page.getByRole("row", { name: /Southbound Freight/ }).first(),
  ).toContainText("Verified override");
});

test("figures: the totals render exactly as the wire strings", async ({
  page,
}) => {
  await open(page, "Monthly figures");
  await expect(page.getByText("€99,999,999,999,999.99").first()).toBeVisible();
  await expect(page.getByText("14500.000")).toBeVisible();
  await expect(page.getByText("Lines examined")).toBeVisible();
});

test("figures: the scope echo renders the period, product group and currency", async ({
  page,
}) => {
  await open(page, "Monthly figures");
  await expect(page.getByText("Diesel only, 2026-05")).toBeVisible();
  await expect(page.getByText("Indicative excise (EUR)")).toBeVisible();
});

test("figures: a month with nothing to report renders the zero-state, not an error", async ({
  page,
}) => {
  await open(page, "Monthly figures", { report: REPORT_EMPTY });
  await expect(page.getByText("No figure for this month")).toBeVisible();
  await expect(
    page.getByText("Couldn’t run the diesel-excise analysis"),
  ).toHaveCount(0);
  await expect(
    page.getByText(/Every country with diesel litres this month has a rate/),
  ).toBeVisible();
});

test("figures: a loading state renders before the API resolves", async ({
  page,
}) => {
  await mockApi(page, { delayMs: 1500 });
  await page.goto("/excise");
  await expect(
    page.getByRole("heading", { name: "Diesel excise" }),
  ).toBeVisible();
  await expect(page.getByText("Northbound Haulage")).toHaveCount(0);
});

test("figures: a 500 renders the error state", async ({ page }) => {
  await open(page, "Monthly figures", { status: 500 });
  await expect(
    page.getByText("Couldn’t run the diesel-excise analysis"),
  ).toBeVisible();
});

test("figures: invalid_period gives the MONTH instruction, not the claim one", async ({
  page,
}) => {
  await open(page, "Monthly figures", {
    refuse: {
      "/transport/excise?": {
        status: 422,
        code: "invalid_period",
        detail: "'2026-13' is not a valid period — use YYYY-MM",
      },
    },
  });
  const alert = page.getByRole("alert");
  await expect(alert).toContainText("accounting month");
  await expect(alert).toContainText("Use a month such as 2026-04");
  await expect(alert).not.toContainText("2026-Q2");
  await expect(page.getByText("invalid_period")).toHaveCount(0);
});

test("figures: invalid_country renders its sentence, not the slug", async ({
  page,
}) => {
  await open(page, "Monthly figures", {
    refuse: {
      "/transport/excise?": {
        status: 422,
        code: "invalid_country",
        detail: "'FRA' is not an ISO 3166-1 alpha-2 country",
      },
    },
  });
  const alert = page.getByRole("alert");
  await expect(alert).toContainText("country code this service accepts");
  await expect(alert).toContainText("'FRA'");
  await expect(page.getByText("invalid_country")).toHaveCount(0);
});

// ---------------------------------------------------------------------------
// 2. No rate held is NO finding — never €0.00
// ---------------------------------------------------------------------------

function noRateCard(page: Page) {
  return page
    .locator("section")
    .filter({ hasText: "Countries with no rate held" })
    .last();
}

test("no-rate: a country with no rate held is listed with its litres and no euro", async ({
  page,
}) => {
  await open(page, "Monthly figures");
  const card = noRateCard(page);
  await expect(card).toContainText("LV");
  await expect(card).toContainText("8100.000");
  await expect(card).toContainText("4");
});

test("no-rate: the page states that no rate held is not a figure of zero", async ({
  page,
}) => {
  await open(page, "Monthly figures");
  await expect(
    page.getByText(/That is not a figure of zero/),
  ).toBeVisible();
  await expect(
    page.getByText(/we hold no rate to compute anything with/),
  ).toBeVisible();
});

test("no-rate: no euro figure of any kind appears in that table", async ({
  page,
}) => {
  await open(page, "Monthly figures");
  // The wire shape carries no euro field for these countries, so the table
  // cannot render one — and a "€0.00" here would say "this state refunds you
  // nothing", which is a different and false statement.
  const card = noRateCard(page);
  const rows = card.locator("tbody tr");
  await expect(rows).toHaveCount(1);
  await expect(rows.first()).not.toContainText("€");
  await expect(card).not.toContainText("€0.00");
});

// ---------------------------------------------------------------------------
// 3. The customs packet
// ---------------------------------------------------------------------------

test("packet: the download issues the packet GET with the current scope", async ({
  page,
}) => {
  const seen: { method: string; path: string; body: string }[] = [];
  await open(page, "Monthly figures", { seen });
  await page.getByLabel("Period").fill("2026-05");
  await page.getByLabel("Country").fill("FR");
  await page.getByRole("button", { name: "Download the customs packet" }).click();
  await expect
    .poll(() => seen.filter((r) => r.path.includes("/excise/packet")).length)
    .toBeGreaterThan(0);
  const req = seen.find((r) => r.path.includes("/excise/packet"))!;
  expect(req.method).toBe("GET");
  expect(req.path).toContain("period=2026-05");
  expect(req.path).toContain("country=FR");
});

test("packet: no_excise_findings renders its sentence, not the slug", async ({
  page,
}) => {
  await open(page, "Monthly figures", {
    refuse: {
      "/transport/excise/packet": {
        status: 422,
        code: "no_excise_findings",
        detail: "2026-05 has no diesel litres in a state a rate is held for.",
      },
    },
  });
  await page.getByRole("button", { name: "Download the customs packet" }).click();
  const alert = page.getByRole("alert");
  await expect(alert).toContainText("nothing for a customs packet");
  await expect(alert).toContainText("looks like a filing and supports nothing");
  await expect(page.getByText("no_excise_findings")).toHaveCount(0);
});

test("packet: a refused download leaves the figures on screen", async ({
  page,
}) => {
  await open(page, "Monthly figures", {
    refuse: {
      "/transport/excise/packet": {
        status: 422,
        code: "no_excise_findings",
        detail: "nothing to package",
      },
    },
  });
  await page.getByRole("button", { name: "Download the customs packet" }).click();
  await expect(page.getByRole("alert")).toBeVisible();
  await expect(page.getByText("Northbound Haulage")).toBeVisible();
  await expect(page.getByText("€99,999,999,999,999.99").first()).toBeVisible();
});

// ---------------------------------------------------------------------------
// 4. The rate registry
// ---------------------------------------------------------------------------

test("rates: every state renders with its resolved rate and its source", async ({
  page,
}) => {
  await open(page, "Country rates");
  for (const c of COUNTRIES) {
    await expect(page.getByRole("row", { name: new RegExp(`^${c}\\b`) }).first()).toBeVisible();
  }
  const it = page.getByRole("row", { name: /^IT\b/ }).first();
  await expect(it).toContainText("22.5000");
  await expect(it).toContainText("Verified override");
  const fr = page.getByRole("row", { name: /^FR\b/ }).first();
  await expect(fr).toContainText("30.0000");
  await expect(fr).toContainText("Indicative default");
});

test("rates: the shipped default is presented as a placeholder", async ({
  page,
}) => {
  await open(page, "Country rates");
  await expect(page.getByText("Indicative default").first()).toBeVisible();
  await expect(page.getByText("30.0000 EUR / 1,000 L")).toBeVisible();
  await expect(
    page.getByText(/an explicit placeholder in a reported EUR 25-33 band/).first(),
  ).toBeVisible();
  await expect(
    page.getByText(/is the placeholder this product ships/),
  ).toBeVisible();
});

test("rates: setting a rate PUTs the typed string", async ({ page }) => {
  const seen: { method: string; path: string; body: string }[] = [];
  await open(page, "Country rates", { seen });
  await page.getByLabel("Rate for FR").fill("22.5000");
  await page
    .getByRole("row", { name: /^FR\b/ })
    .first()
    .getByRole("button", { name: "Save" })
    .click();
  await expect.poll(() => seen.filter((r) => r.method === "PUT").length).toBe(1);
  const put = seen.find((r) => r.method === "PUT")!;
  expect(put.path).toBe("/transport/excise/rates");
  // The rate crosses the wire as the STRING the operator typed — never parsed.
  expect(JSON.parse(put.body)).toEqual({
    country: "FR",
    rate_eur_per_1000l: "22.5000",
  });
});

test("rates: clearing an override DELETEs for that country", async ({
  page,
}) => {
  const seen: { method: string; path: string; body: string }[] = [];
  await open(page, "Country rates", { seen });
  await page
    .getByRole("row", { name: /^IT\b/ })
    .first()
    .getByRole("button", { name: "Clear override" })
    .click();
  await expect
    .poll(() => seen.filter((r) => r.method === "DELETE").length)
    .toBe(1);
  expect(seen.find((r) => r.method === "DELETE")!.path).toContain("country=IT");
});

test("rates: the clear control only exists where an override exists", async ({
  page,
}) => {
  await open(page, "Country rates");
  await expect(
    page
      .getByRole("row", { name: /^IT\b/ })
      .first()
      .getByRole("button", { name: "Clear override" }),
  ).toBeVisible();
  await expect(
    page
      .getByRole("row", { name: /^FR\b/ })
      .first()
      .getByRole("button", { name: "Clear override" }),
  ).toHaveCount(0);
});

test("rates: excise_country_not_supported renders its sentence, not the slug", async ({
  page,
}) => {
  await open(page, "Country rates", {
    refuse: {
      "/transport/excise/rates": {
        method: "PUT",
        status: 422,
        code: "excise_country_not_supported",
        detail: "'LV' is not one of the states this product records.",
      },
    },
  });
  await page.getByLabel("Rate for FR").fill("22.5");
  await page
    .getByRole("row", { name: /^FR\b/ })
    .first()
    .getByRole("button", { name: "Save" })
    .click();
  const alert = page.getByRole("alert");
  await expect(alert).toContainText("records no diesel-excise refund regime");
  await expect(page.getByText("excise_country_not_supported")).toHaveCount(0);
});

test("rates: invalid_excise_rate renders its sentence, not the slug", async ({
  page,
}) => {
  await open(page, "Country rates", {
    refuse: {
      "/transport/excise/rates": {
        method: "PUT",
        status: 422,
        code: "invalid_excise_rate",
        detail: "'0' is not a usable excise rate",
      },
    },
  });
  await page.getByLabel("Rate for FR").fill("0");
  await page
    .getByRole("row", { name: /^FR\b/ })
    .first()
    .getByRole("button", { name: "Save" })
    .click();
  const alert = page.getByRole("alert");
  await expect(alert).toContainText("isn’t a usable excise rate");
  await expect(alert).toContainText("never by a rate of zero");
  await expect(page.getByText("invalid_excise_rate")).toHaveCount(0);
});

test("rates: a role without vat.write sees the rates but no write control", async ({
  page,
}) => {
  // AUDITOR holds transport.read and vat.read, and NOT vat.write — the
  // permission `routes/transport/excise.py` declares on the two rate routes.
  await open(page, "Country rates", { role: "auditor" });
  await expect(page.getByRole("row", { name: /^FR\b/ }).first()).toContainText(
    "30.0000",
  );
  await expect(page.getByRole("button", { name: "Save" })).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "Clear override" }),
  ).toHaveCount(0);
  await expect(page.getByText("Rates are read-only for your role")).toBeVisible();
});

// ---------------------------------------------------------------------------
// 5. The eligibility non-assertion (the governing constraint)
// ---------------------------------------------------------------------------

test("eligibility: the statement is rendered verbatim from the wire", async ({
  page,
}) => {
  await open(page, "Monthly figures");
  await expect(page.getByText(ELIGIBILITY).first()).toBeVisible();
});

test("eligibility: the statement rides the figures panel as well as the header", async ({
  page,
}) => {
  await open(page, "Monthly figures");
  // Two renderings: the page-level block (off the rates response) and the
  // figures panel's own (off the report response). R42's acceptance is "every
  // surface that shows the number", and a panel scrolled away from the header is
  // its own surface.
  await expect(page.getByText(ELIGIBILITY)).toHaveCount(2);
});

test("eligibility: the statement is on the rates panel too", async ({
  page,
}) => {
  await open(page, "Country rates");
  await expect(page.getByText(ELIGIBILITY).first()).toBeVisible();
});

test("eligibility: the rate caveat is rendered verbatim from the wire", async ({
  page,
}) => {
  await open(page, "Monthly figures");
  await expect(page.getByText(RATE_CAVEAT).first()).toBeVisible();
});

test("eligibility: eligibility_asserted false is stated, not dropped", async ({
  page,
}) => {
  await open(page, "Monthly figures");
  await expect(
    page.getByText("Eligibility asserted by this figure: none.").first(),
  ).toBeVisible();
});

test("eligibility: the legal framing is rendered verbatim from the wire", async ({
  page,
}) => {
  await open(page, "Monthly figures");
  await expect(page.getByText(FRAMING).first()).toBeVisible();
});

test("eligibility: the customs addressee and the litre basis come off the wire", async ({
  page,
}) => {
  await open(page, "Monthly figures");
  await expect(page.getByText(`Filed with: ${FILED_WITH}`)).toBeVisible();
  await expect(page.getByText(`Basis: ${LITRE_BASIS}`)).toBeVisible();
});

/**
 * WO-87's `CLAIM_WORDS` — the list WO-91's backend scan imports rather than
 * re-types — plus `entitle`, which this order's own constraint names. Matched at
 * word boundaries so an innocent substring is not a false positive while
 * `recoverable_eur` is caught.
 */
const FORBIDDEN =
  /\brecover\w*|\bowed\b|\bowes\b|\bclaim\w*|\bdemand\w*|\bdue\b|\bdebt\w*|\bpayable\b|\bentitle\w*/gi;

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

test("eligibility: the new modules carry no entitlement vocabulary", () => {
  for (const rel of ["src/pages/Excise.tsx", "src/lib/transportExcise.ts"]) {
    expect(
      forbiddenHits(readSource(rel)),
      `${rel} must carry none of it`,
    ).toEqual([]);
  }
});

test("eligibility: the new wire interfaces carry no entitlement vocabulary", () => {
  const types = readSource("src/lib/types.ts");
  const names = [
    "ExciseRate",
    "ExciseRates",
    "ExciseCell",
    "ExciseSkippedCountry",
    "ExciseReport",
  ];
  for (const name of names) {
    const m = new RegExp(`export interface ${name}\\s*\\{([^}]*)\\}`).exec(
      types,
    );
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

test("eligibility: the vocabulary scan detects a seeded violation", () => {
  // A scan that cannot fail proves nothing. Each of these is a plausible way the
  // non-assertion would be undone by a well-meaning edit.
  expect(
    forbiddenHits("const recoverable_eur = row.indicative_excise_eur;"),
  ).not.toEqual([]);
  expect(forbiddenHits("<th>Excise owed to you</th>")).not.toEqual([]);
  expect(forbiddenHits("<Button>File the excise claim</Button>")).not.toEqual(
    [],
  );
  expect(forbiddenHits("// you are entitled to this refund")).not.toEqual([]);
  expect(forbiddenHits("<p>Amount due from customs</p>")).not.toEqual([]);
  // …and does not fire on an innocent substring.
  expect(
    forbiddenHits("the residual litres were produced by a duty-free supplier"),
  ).toEqual([]);
});

test("eligibility: the frontend holds no framing string of its own", () => {
  // The statement, the caveat, the framing, the addressee and the basis live in
  // `app/services/transport/excise.py` and cross the wire. If any of them were
  // ALSO in the SPA, a re-wording here could soften what a customs-bound figure
  // is presented with, and the two copies would drift silently.
  const files = walk(join(here(), "..", "src"));
  const needles = [
    "This figure asserts NO eligibility.",
    "an explicit placeholder in a reported EUR 25-33 band",
    "Indicative / advisory \u2014 verify before relying",
    "Customs authority of the country of supply (a separate regime",
    "Validated diesel litres as invoiced; the rate is NET EUR per 1,000 litres",
  ];
  for (const file of files) {
    const src = readFileSync(file, "utf8");
    for (const needle of needles) {
      expect(src.includes(needle), `${file} must not restate: ${needle}`).toBe(
        false,
      );
    }
  }
});

// ---------------------------------------------------------------------------
// 6. Permission, module and money
// ---------------------------------------------------------------------------

test("perm: a read-only role sees every figure and no write control", async ({
  page,
}) => {
  await open(page, "Monthly figures", { role: "user_free" });
  await expect(page.getByText("€99,999,999,999,999.99").first()).toBeVisible();
  await expect(page.getByText(ELIGIBILITY).first()).toBeVisible();
  await page.getByRole("tab", { name: "Country rates" }).click();
  await expect(page.getByRole("button", { name: "Save" })).toHaveCount(0);
});

test("perm: transport.read gates the nav entry (granted / denied)", async ({
  page,
}) => {
  await mockApi(page, { role: "auditor" }); // AUDITOR holds TRANSPORT_READ
  await page.goto("/excise");
  await expect(
    page.getByRole("navigation").getByRole("link", { name: "Diesel excise" }),
  ).toBeVisible();

  await mockApi(page, { role: "user" }); // EMPLOYEE holds neither VAT_READ nor TRANSPORT_READ
  await page.goto("/excise");
  await expect(
    page.getByRole("navigation").getByRole("link", { name: "Diesel excise" }),
  ).toHaveCount(0);
});

test("perm: a server 403 renders through the refusal path", async ({ page }) => {
  await open(page, "Monthly figures", {
    refuse: {
      "/transport/excise?": {
        status: 403,
        code: "module_not_enabled",
        detail: "The Transport & VAT refunds module is not activated.",
      },
    },
  });
  await expect(page.getByRole("alert")).toContainText(
    "module isn’t active for this workspace",
  );
});

test("module: transport off renders the module notice and no nav entry", async ({
  page,
}) => {
  await mockApi(page, { moduleEnabled: false });
  await page.goto("/excise");
  await expect(page.getByText(/Transport & VAT refunds/).first()).toBeVisible();
  await expect(page.getByRole("tab", { name: "Monthly figures" })).toHaveCount(
    0,
  );
  await expect(
    page.getByRole("navigation").getByRole("link", { name: "Diesel excise" }),
  ).toHaveCount(0);
});

test("money: the new modules perform no float arithmetic on an amount", () => {
  // `parseFloat`/`Number(`/`toFixed`/`Math.` are the four ways a decimal string
  // becomes an IEEE-754 double in this codebase. None may appear: every figure
  // is the server's own string, euros rendered through `decimalMoney` (string
  // surgery, no arithmetic) and rates and litres rendered as received.
  const banned = [/parseFloat/, /\bNumber\s*\(/, /toFixed/, /\bMath\./];
  for (const rel of ["src/pages/Excise.tsx", "src/lib/transportExcise.ts"]) {
    const src = readSource(rel);
    for (const pattern of banned) {
      expect(pattern.test(src), `${rel} must not contain ${pattern}`).toBe(
        false,
      );
    }
  }
});
