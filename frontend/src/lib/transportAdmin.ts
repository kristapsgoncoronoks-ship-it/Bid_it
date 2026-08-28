import type { VatCountryActivation, VatLifecycle } from "./types";

/**
 * Pure helpers for the transport ADMIN/CONFIG workspace (WO-80). No React, no
 * network — so the vocabulary mirrors and the ladder logic are testable through
 * the pages and cannot drift into a component.
 *
 * EVERYTHING HERE IS A MIRROR, NOT A SOURCE. Each constant below is quoted from
 * the backend module named beside it. The server is the only control: an
 * out-of-set cadence is refused `invalid_cadence`, an illegal lifecycle edge is
 * refused `lifecycle_transition_invalid`, and those refusals render through the
 * one refusal map in `transportClaims.ts`. This file exists so the UI does not
 * offer a value or a step the caller will only ever be refused — the same
 * cosmetic discipline as `roles.ts::hasVatPerm` and
 * `transportClaims.ts::isSyntheticRef` (master-context §6).
 */

/**
 * `app/models/transport/receipt_control.py::CADENCES` — the harvested closed
 * set. `set_cadence` refuses anything outside it (422 `invalid_cadence`).
 */
export const CADENCES = ["semi-monthly", "monthly", "monthly-per-country"] as const;
export type Cadence = (typeof CADENCES)[number];

/**
 * `app/models/transport/receipt_control.py::RECEIPT_STATUSES` — what one
 * persisted slot of the cadence × activity grid says, in an operator's words.
 *
 * Every sentence is deliberately a CHASE-LIST sentence. The grid is advisory
 * (master-context §4.19): `receipt_control.py`'s own docstring records that a
 * `missing` slot "NEVER gates a claim", and the blocking side of receipt
 * control is the claim-level document gate (R10) and the checklist (R45), not
 * this board. The copy must never imply otherwise.
 */
export const RECEIPT_STATUS_COPY: Record<string, { label: string; meaning: string }> = {
  no_activity: {
    label: "No activity",
    meaning: "Nothing was bought from this supplier in the slot, so no invoice is expected.",
  },
  received_doc: {
    label: "Invoice stored",
    meaning: "The slot has transactions and the supplier's invoice document is in the vault.",
  },
  received_no_doc: {
    label: "Invoice matched, document missing",
    meaning:
      "The slot's transactions resolve to a registered invoice, but no document is stored against it yet.",
  },
  missing: {
    label: "Chase the supplier",
    meaning:
      "The slot has activity and no invoice arrived. This is a worklist row — it blocks no claim and halts no close.",
  },
};

/** `app/models/transport/customer_lifecycle.py::CUSTOMER_STATES`. */
export const CUSTOMER_STATES = ["prospect", "pending", "active", "inactive"] as const;

/** `app/models/transport/customer_lifecycle.py::COUNTRY_STATES`. The third,
 * unnamed state is the ABSENCE of a row ((none)) — which the R44 gate refuses
 * exactly like `requested`. */
export const COUNTRY_STATES = ["requested", "active"] as const;

/** A transition the routes expose, as the page needs to render it. */
export interface LadderAction {
  /** The button's own words. */
  label: string;
  /** The route path suffix under `/transport/customers/{entity_id}`. */
  path: string;
  /** The POST body, or `undefined` for the body-less transitions. */
  body?: { active: boolean };
  /** Why this step exists — rendered as the control's hint. */
  hint: string;
}

/**
 * The customer-lifecycle transitions legal FROM a given state, mirroring
 * `app/services/transport/customer_lifecycle.py`:
 *
 *  - `(none) → prospect`   `add_prospect`      (idempotent, never a downgrade)
 *  - `prospect → pending`  `promote_prospect`  (409 `not_a_prospect` otherwise)
 *  - `pending ↔ active`    `set_activation`    (the explicit admin click)
 *  - `active → inactive`   `set_inactive`      (the churn edge)
 *
 * `inactive` is TERMINAL in this slice: no re-onboarding edge is harvested, and
 * inventing one in the UI would be inventing functionality (§10). Returning an
 * empty list is the honest answer — the page says so in words.
 */
export function lifecycleActions(status: string | null): LadderAction[] {
  switch (status) {
    case null:
    case undefined:
      return [
        {
          label: "Record as a prospect",
          path: "prospect",
          hint: "The first step. A prospect is a pre-sale lead — every claim gate still refuses it.",
        },
      ];
    case "prospect":
      return [
        {
          label: "Promote to onboarding",
          path: "promote",
          hint: "Moves the customer to pending. Promotion and activation are separate steps — neither is skippable.",
        },
      ];
    case "pending":
      return [
        {
          label: "Activate for refund claims",
          path: "activation",
          body: { active: true },
          hint: "Opens the customer to submissions. Until this click the claim gate refuses every filing.",
        },
      ];
    case "active":
      return [
        {
          label: "Return to onboarding",
          path: "activation",
          body: { active: false },
          hint: "Back to pending. Submissions are refused again from the moment this lands.",
        },
        {
          label: "Set inactive",
          path: "inactive",
          hint: "The churn switch. Historical claims are untouched; the next submission is refused.",
        },
      ];
    default:
      return [];
  }
}

/**
 * The per-country transitions legal FROM a given state, mirroring
 * `request_country` / `set_country_activation`:
 *
 *  - `(none) → requested`  (idempotent, never a downgrade)
 *  - `requested ↔ active`  (activating a never-requested country is refused
 *                           409 `country_not_requested` — the harvested ladder
 *                           is linear because the request step is where the
 *                           country's document set is gathered)
 */
export function countryActions(status: string | null): LadderAction[] {
  switch (status) {
    case null:
    case undefined:
      return [
        {
          label: "Request",
          path: "request",
          hint: "Start the registration and power of attorney for this member state.",
        },
      ];
    case "requested":
      return [
        {
          label: "Activate",
          path: "activation",
          body: { active: true },
          hint: "Confirms the registration is in place. Only an active country passes the claim gate.",
        },
      ];
    case "active":
      return [
        {
          label: "Deactivate",
          path: "activation",
          body: { active: false },
          hint: "Back to requested. Claims for this country are refused again.",
        },
      ];
    default:
      return [];
  }
}

/** The activation state of one country for an entity — `null` when the entity
 * has no row for it at all, which is a real state the R44 gate refuses. */
export function countryStatus(
  lifecycle: VatLifecycle | undefined,
  country: string,
): string | null {
  const row = lifecycle?.countries.find(
    (c: VatCountryActivation) => c.country.toUpperCase() === country.toUpperCase(),
  );
  return row?.status ?? null;
}

/** The current month as `"YYYY-MM"` — the shape every period-scoped transport
 * route requires (`min_length=7, max_length=7`). Formatting only: no period
 * arithmetic, and a malformed value is still the service's `invalid_period`. */
export function currentPeriod(now: Date = new Date()): string {
  const month = `${now.getMonth() + 1}`.padStart(2, "0");
  return `${now.getFullYear()}-${month}`;
}

/** Does this look like the `"YYYY-MM"` the period-scoped routes require? Used
 * only to decide whether to ISSUE the request — never to judge the value, which
 * the service does (`invalid_period`). */
export function isPeriodShape(value: string): boolean {
  return /^\d{4}-\d{2}$/.test(value.trim());
}

/**
 * `app/models/transport/claimant_document.py::DOC_KINDS` — the document
 * catalogue §3.F F3 names, in an operator's words (WO-AB).
 *
 * A MIRROR, not a source: the server sends its own `kinds` list with every
 * document listing and the CHECK constraint refuses anything outside it. This
 * map only supplies the LABEL, so an unknown kind still renders — as its own
 * key rather than as blank, because a document nobody can name is still a
 * document the workspace holds.
 */
export const DOC_KIND_LABELS: Record<string, string> = {
  power_of_attorney: "Power of attorney",
  vat_certificate: "VAT certificate",
  tax_mandate: "Tax mandate",
  fleet_list: "Fleet list",
  company_extract: "Company extract",
  signatory_id: "Signatory ID",
  signed_contract: "Signed contract",
  trade_registry: "Trade register extract",
};

/** The kinds §3.E's `scope="country"` rules ask for — the screen requires a
 * country for these and refuses to send one for the rest, matching the
 * `country = '' OR length(country) = 2` constraint rather than discovering it
 * as a 422. */
export const COUNTRY_SCOPED_DOC_KINDS = ["power_of_attorney"] as const;

export function docKindLabel(kind: string): string {
  return DOC_KIND_LABELS[kind] ?? kind;
}

/** What a held document's validity says, in words. `null` is NOT "expired" —
 * it is a document with no stated expiry, which the checklist reads as
 * permanently valid (`claimant_documents._state`). */
export function documentValidity(
  validUntil: string | null,
  today: string,
): { label: string; tone: "success" | "warning" | "danger" } {
  if (validUntil === null) return { label: "No stated expiry", tone: "success" };
  if (validUntil < today) return { label: `Expired ${validUntil}`, tone: "danger" };
  const soon = new Date(today);
  soon.setDate(soon.getDate() + 60);
  if (validUntil <= soon.toISOString().slice(0, 10))
    return { label: `Expires ${validUntil}`, tone: "warning" };
  return { label: `Valid to ${validUntil}`, tone: "success" };
}
