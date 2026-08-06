import type { VatClaim, VatClaimLine } from "./types";

/**
 * Pure helpers for the VAT claims workspace (WO-78). No React, no network — so
 * the refusal vocabulary and the ladder logic are testable through the page and
 * cannot drift into a component.
 *
 * THE REFUSAL MAP IS THE POINT OF THIS FILE. The D5 gate chain
 * (`app/services/transport/lock.py::submit_claim`) is fail-CLOSED by design and
 * its whole value to an operator is seeing, in time, WHY a claim was refused and
 * WHAT TO DO about it. The backend renders every refusal as `{"detail", "code"}`
 * where `code` is a stable slug raised by the service itself — the UI's job is to
 * turn that slug into a sentence, never to show it raw and never to swallow it.
 *
 * Every code below was read off the services (verified, not guessed):
 * `lock.py`, `minimum.py`, `duplicate.py`, `document_gate.py`, `claim_gates.py`,
 * `claim.py`, `claim_lines.py`, `claim_pack.py`, `status.py`,
 * `customer_lifecycle.py`, `modules.py`.
 */

export interface Refusal {
  /** What is wrong, in the operator's words. Never the raw slug. */
  title: string;
  /** What to do next. Always actionable, never an apology. */
  next: string;
}

const REFUSALS: Record<string, Refusal> = {
  // --- entitlement / lookup -------------------------------------------------
  module_not_enabled: {
    title: "The Transport & VAT refunds module isn’t active for this workspace",
    next: "Activate it in Settings → Modules, then reload this page.",
  },
  claim_not_found: {
    title: "This claim isn’t available",
    next: "It may have been removed. Go back to the claims list and open it again.",
  },
  entity_not_found: {
    title: "That legal entity doesn’t exist in this workspace",
    next: "Pick an entity from the list, or add it under Issuer settings first.",
  },
  invalid_period: {
    title: "That isn’t a reference period this service can file",
    next: "Use a quarter such as 2026-Q2, or a year such as 2026-YEAR.",
  },

  // --- lifecycle ------------------------------------------------------------
  claim_not_draft: {
    title: "This claim has already left draft",
    next: "Only a draft claim can be changed. Withdraw it first if you need to rebuild it.",
  },
  claim_not_locked: {
    title: "There’s nothing to withdraw on this claim",
    next: "Withdrawing releases the invoice locks a filed claim holds — this one holds none.",
  },
  claim_not_submitted: {
    title: "A workflow code can only be set once the claim is filed",
    next: "Submit the claim first. Before that the stage is derived from the checklist and isn’t settable.",
  },
  status_code_system_controlled: {
    title: "That code is set by the system, not by hand",
    next: "The pre-filing stages follow the checklist automatically. Pick one of the workflow codes instead.",
  },
  unknown_status_code: {
    title: "That isn’t a workflow code this service recognises",
    next: "Pick a code from the list.",
  },

  // --- the D5 submit gate chain --------------------------------------------
  empty_claim_set: {
    title: "Add at least one invoice before filing",
    next: "List every invoice going into this claim: the supplier, its invoice reference, and the fuel transaction it settles.",
  },
  period_not_ended: {
    title: "The reference period hasn’t finished yet",
    next: "A refund claim can only be filed once its whole period has closed. Come back after the last day of the period.",
  },
  below_minimum: {
    title: "The claim is below the minimum refundable amount for this country",
    next: "Add more invoices from this period, or roll it into the annual claim. If you have a reason to file anyway, use the override — it is recorded on the claim.",
  },
  customer_not_active: {
    title: "This entity isn’t activated for refund claims",
    next: "Finish onboarding and set the entity active, then file again.",
  },
  country_not_activated: {
    title: "This refund country isn’t activated for the entity",
    next: "Request the country first, then activate it once the registration is in place.",
  },
  unresolved_invoice_refs: {
    title: "Some claim lines aren’t tied to a real invoice yet",
    next: "Register the missing supplier invoices, or map the statement note to an invoice reference, then rebuild the lines.",
  },
  duplicate_invoice_lock: {
    title: "One of these invoices is already claimed on another filing",
    next: "An invoice can only be claimed once. Drop it here, or withdraw the claim that holds it.",
  },
  invoice_document_missing: {
    title: "An invoice on this claim has no stored document",
    next: "Upload the original invoice for each reference named below — the refunding member state requires it in the filing bundle.",
  },
  claim_line_mixed_currency: {
    title: "A claim line mixes currencies",
    next: "The transactions grouped under that invoice and product don’t agree on a currency. Correct the source transactions, then rebuild the lines.",
  },

  // --- filing artifacts -----------------------------------------------------
  claim_not_frozen: {
    title: "The filing pack isn’t available until the claim is filed",
    next: "Submitting freezes the claim’s lines, and the pack is built from those frozen figures.",
  },
  synthetic_line_in_pack: {
    title: "A frozen line isn’t tied to a real invoice",
    next: "This claim can’t be packaged. Withdraw it, resolve the line to a real invoice reference, and file it again.",
  },
  claim_totals_drift: {
    title: "The claim total no longer matches its lines",
    next: "Nothing is packaged from figures that disagree. Withdraw the claim and rebuild it before filing again.",
  },
  claim_currency_mismatch: {
    title: "The frozen lines span more than one currency",
    next: "A claim is filed in a single currency. Split it by currency and file each part separately.",
  },
  evidence_document_unavailable: {
    title: "An invoice document in the pack can’t be read",
    next: "The workbook still downloads. Re-upload the invoice named below, then build the evidence pack again.",
  },

  // --- the admin/config surfaces (WO-80's screens over the WO-77 routes) ----
  // Every code below is raised by a service in `app/services/transport/`:
  // `waiver.py`, `checklist.py`, `receipt_control.py`, `invoice_match.py`,
  // `tie_out.py`, `customer_lifecycle.py`. Verified against the source, not
  // guessed — the same discipline the D5 block above follows.
  waiver_supplier_has_invoices: {
    title: "That supplier does have a registered invoice for this country",
    next: "Waiving it would drop claimable VAT. If a transaction looks unmatched, map its statement note to the invoice reference instead.",
  },
  checklist_rule_not_found: {
    title: "That checklist rule doesn’t exist in this workspace",
    next: "Seed the default rules first, then toggle the one you need.",
  },
  invalid_cadence: {
    title: "That isn’t an invoicing cadence this service recognises",
    next: "Choose semi-monthly, monthly, or monthly-per-country.",
  },
  receipt_control_not_found: {
    title: "That receipt-control row isn’t available",
    next: "The grid may have been rebuilt by a later close. Reload the period and try again.",
  },
  override_note_is_synthetic: {
    title: "There’s nothing real to map that reference onto",
    next: "A placeholder reference (UNMATCHED, an aggregate, an input line) can’t be overridden. Use the reference printed on the statement line.",
  },
  override_target_not_registered: {
    title: "That invoice isn’t registered for this supplier and country",
    next: "Register the supplier invoice for this refund country first, then point the override at it.",
  },
  invalid_currency: {
    title: "That isn’t a currency code this service accepts",
    next: "Use the three-letter ISO code the supplier invoiced in, e.g. EUR or PLN.",
  },
  invalid_expected_lines: {
    title: "The expected line count can’t be negative",
    next: "Type the number of lines printed on the supplier's invoice, or zero if it has none.",
  },
  invalid_gross_tolerance: {
    title: "That tolerance is outside the band this service allows",
    next: "Use a value between 0.02 and 0.05. The tolerance decides how far the engine may differ from the typed gross before the close halts.",
  },
  tieout_expectation_not_found: {
    title: "There’s no typed expectation for that supplier, period and currency",
    next: "It may already have been removed. Reload the period to see what is typed.",
  },
  not_a_prospect: {
    title: "This customer isn’t a prospect any more",
    next: "Promotion is the step from prospect to onboarding. Use the transition that matches the state shown above.",
  },
  lifecycle_transition_invalid: {
    title: "That isn’t a step this customer can take from where it is",
    next: "The ladder is prospect → pending → active → inactive, one step at a time. Reload to see the current state.",
  },
  invalid_country: {
    title: "That isn’t a country code this service accepts",
    next: "Use the two-letter ISO code of the refunding member state, e.g. LV.",
  },
  country_not_requested: {
    title: "That country was never requested for this customer",
    next: "Request it first — that step is where the country's registration and power of attorney are gathered — then activate it.",
  },
  country_transition_invalid: {
    title: "That isn’t a step this country can take from where it is",
    next: "A country goes requested → active, and back. Reload to see the current state.",
  },
};

/**
 * Map a server refusal onto an actionable message.
 *
 * Fail-OPEN on presentation: an unmapped code (or a refusal with no code at all,
 * like the router-level 403 from `require_perm`, which carries only a detail)
 * falls back to the server's OWN sentence. Showing what the server said always
 * beats swallowing it — and presentation gates nothing, so opening here is safe.
 * The `detail` is returned alongside so the caller can render the specifics the
 * service named (which suppliers, which invoice refs, which period) verbatim
 * underneath: this map adds the action, it never replaces the server's facts.
 */
export function claimRefusal(code: string | null, detail: string): Refusal {
  const mapped = code ? REFUSALS[code] : undefined;
  if (mapped) return mapped;
  return { title: detail, next: "" };
}

/** True when the map has a sentence for this code — the pages use it only to
 * decide whether to show the server `detail` as SUPPORTING text (it is already
 * the title otherwise). */
export function isMappedRefusal(code: string | null): boolean {
  return !!code && code in REFUSALS;
}

/**
 * Mirror of `app/services/transport/claim_gates.py::is_synthetic` — a claim-line
 * reference that is a placeholder rather than a real invoice number. Used ONLY
 * to decide which lines can pre-seed the submit dialog; the blocking decision
 * stays with the server (`unresolved_invoice_refs`), which this never pre-empts.
 */
export function isSyntheticRef(ref: string, vatId?: string | null): boolean {
  return (
    ref.includes("INPUT") ||
    ref.startsWith("ALL:") ||
    ref === "UNMATCHED" ||
    (vatId != null && vatId.includes("INPUT"))
  );
}

/** The distinct, non-synthetic invoice references on a claim's materialized
 * lines — the refs an operator can legitimately put in the submit set. */
export function claimableRefs(lines: VatClaimLine[]): string[] {
  const seen = new Set<string>();
  for (const line of lines) {
    if (!isSyntheticRef(line.invoice_ref, line.vat_id)) seen.add(line.invoice_ref);
  }
  return [...seen].sort();
}

export interface LadderStep {
  code: string;
  /** "done" (passed), "current" (where the claim is now), or "todo". */
  state: "done" | "current" | "todo";
}

/**
 * The claim's position on the status-code ladder.
 *
 * The ladder is the service's OWN vocabulary, read from
 * `GET /transport/status-codes`: the system-derived `auto` codes (1A/1B/1C/1E),
 * then the submit stamp "2", then the `manual` workflow codes. This codebase
 * deliberately carries NO label mapping for those codes (WO-77 decision 5 — the
 * harvested `STATUS_LABELS` was never brought across), so the codes are rendered
 * as the codes they are; nothing is invented here.
 *
 * Position: on a DRAFT claim it is the stage the service derived (`GET
 * .../stage`); afterwards it is the claim's own stamped `status_code`. A claim
 * with neither is shown entirely un-walked rather than guessed at.
 */
export function stageLadder(
  codes: { auto: string[]; manual: string[] } | undefined,
  claim: Pick<VatClaim, "status" | "status_code">,
  derivedStage: string | null,
): LadderStep[] {
  const auto = codes?.auto ?? [];
  const manual = codes?.manual ?? [];
  // "2" is the submit stamp: `lock.submit_claim` writes it, and `set_status_code`
  // deliberately refuses it, so it is in neither list but sits between them.
  const sequence = [...auto, "2", ...manual];
  const at = claim.status === "draft" ? derivedStage : claim.status_code;
  const index = at ? sequence.indexOf(at) : -1;
  return sequence.map((code, i) => ({
    code,
    state: index < 0 ? "todo" : i < index ? "done" : i === index ? "current" : "todo",
  }));
}
