import type { Tone } from "../components/ui";

/**
 * Pure helpers for the recovery-intelligence workspace (WO-86). No React, no
 * network — so the vocabulary these three screens render, and the transition
 * ladder they offer, are testable through the pages and cannot drift into a
 * component.
 *
 * EVERY MAP HERE IS A MIRROR OF A SERVER CONSTANT, NOT A SECOND SOURCE OF
 * TRUTH. `BUCKET_COPY` names the six readiness states
 * `app/services/transport/recovery.py::READINESS_STATES` emits, in that
 * module's own order; `OVERCHARGE_TRANSITIONS` is
 * `app/services/transport/overcharge.py::TRANSITIONS` verbatim. The server
 * re-validates every edge (`overcharge_transition_invalid`, 409, with nothing
 * mutated), so a drift here costs a refused click, never a wrong write — the
 * same posture `roles.ts::hasVatPerm` and `transportAdmin.ts::lifecycleActions`
 * take. A dead button is worse than no button; a button that writes something
 * the server would refuse is not possible.
 *
 * NOTHING IN THIS FILE TOUCHES A NUMBER. There is no arithmetic of any kind
 * here: every figure these screens show arrives already totalled and already
 * quantized by the service and is rendered from its wire string (§4.9/§4.10).
 */

// ---------------------------------------------------------------------------
// The six readiness states (WO-81 / `BA_fleet_fuel.md` §2.4)
// ---------------------------------------------------------------------------

export interface BucketCopy {
  /** The operator-facing name. The raw state slug is shown beside it, never
   * instead of it — it is the service's own vocabulary and stays visible. */
  label: string;
  /** What this bucket MEANS — i.e. what is true of every claim in it. */
  meaning: string;
  /** What to do about it. The buckets answer "what to do"; the deadline-risk
   * count beside them answers "how urgent" (WO-81 interpretation 3). */
  next: string;
  tone: Tone;
}

/**
 * Keyed by the exact state slug the service emits. Rendering order comes from
 * the RESPONSE (the service always sends all six in its own order), not from
 * this object — so a state added server-side appears rather than disappearing.
 */
export const BUCKET_COPY: Record<string, BucketCopy> = {
  ready: {
    label: "Ready to file",
    meaning: "Nothing is blocking these claims and the filing deadline is not close.",
    next: "File them. Submitting freezes the VAT base and takes the invoice locks.",
    tone: "success",
  },
  deadline: {
    label: "Deadline approaching",
    meaning:
      "These claims are otherwise fileable and are inside the 60-day window before the 30 September filing cut-off.",
    next: "File these first. A missed cut-off forfeits the refund permanently — it cannot be filed late.",
    tone: "danger",
  },
  missing: {
    label: "Missing documents or data",
    meaning: "A checklist item has not passed, so these claims cannot be filed as they stand.",
    next: "Open each claim and work its checklist — the failing item names what is absent.",
    tone: "warning",
  },
  below: {
    label: "Below the country minimum",
    meaning:
      "The claimable VAT is under the refunding country's Art. 17 minimum for this period length.",
    next: "Add more invoices from the period, or roll it into the annual claim.",
    tone: "neutral",
  },
  submitted: {
    label: "Filed, awaiting the refund",
    meaning: "These claims are with the refunding member state. The figure is frozen.",
    next: "Nothing to do here — chase only once the country's own deadline has passed.",
    tone: "info",
  },
  paid: {
    label: "Refunded",
    meaning: "The money arrived. This is the recovered half of the north star.",
    next: "Nothing to do.",
    tone: "brand",
  },
};

/** Fallback for a state this mirror has no sentence for: the slug is shown as
 * it arrived rather than hidden, because an unnamed bucket still carries claims
 * and euros an operator must see. */
export function bucketCopy(state: string): BucketCopy {
  return (
    BUCKET_COPY[state] ?? {
      label: state,
      meaning: "This readiness state is newer than this screen.",
      next: "Its claims and euros are shown as the service reported them.",
      tone: "neutral",
    }
  );
}

/**
 * Why a claim matches none of the six readiness states. Mirrors
 * `recovery.EXCLUDED_STATUSES`. These claims are reported rather than dropped
 * precisely so the row count cannot lie, and they are NOT a seventh bucket —
 * they carry no recoverable euro at all.
 */
export const EXCLUDED_COPY: Record<string, string> = {
  withdrawn: "Withdrawn — the filing was pulled back and its invoice locks released.",
  rejected: "Rejected — the refunding member state refused the claim.",
};

export function excludedCopy(reason: string): string {
  return EXCLUDED_COPY[reason] ?? `Excluded (${reason}) — no readiness state applies.`;
}

// ---------------------------------------------------------------------------
// The claim-back lifecycle (WO-82)
// ---------------------------------------------------------------------------

/**
 * Verbatim from `app/services/transport/overcharge.py::TRANSITIONS`, drawn as
 * `BA_fleet_fuel.md` §4.5 draws it: `detected → packaged → claimed →
 * {recovered, rejected, written_off}`. A state whose list is empty is TERMINAL.
 * No shortcut edge is invented — writing a claim-back off straight from
 * `detected` is not a move the service allows, so it is not a button here.
 */
export const OVERCHARGE_TRANSITIONS: Record<string, string[]> = {
  detected: ["packaged"],
  packaged: ["claimed"],
  claimed: ["recovered", "rejected", "written_off"],
  recovered: [],
  rejected: [],
  written_off: [],
};

/** The one target state that carries a booked-cash amount. Every other target
 * refuses one (`recovered_amount_not_applicable`). */
export const RECOVERED_STATE = "recovered";

/** Which moves this claim-back can make from where it is. An unknown status
 * offers nothing — safer than guessing an edge the server would refuse. */
export function overchargeActions(status: string): string[] {
  return OVERCHARGE_TRANSITIONS[status] ?? [];
}

export function isTerminal(status: string): boolean {
  return overchargeActions(status).length === 0;
}

export const OVERCHARGE_STATUS_TONE: Record<string, Tone> = {
  detected: "warning",
  packaged: "info",
  claimed: "brand",
  recovered: "success",
  rejected: "danger",
  written_off: "neutral",
};

export function statusTone(status: string): Tone {
  return OVERCHARGE_STATUS_TONE[status] ?? "neutral";
}

/** What each claim-back state means, in the operator's words. The state slug
 * itself stays on screen — it is the service's vocabulary and this codebase
 * deliberately carries no invented labels for it. */
export const OVERCHARGE_STATUS_COPY: Record<string, string> = {
  detected: "Opened from a contract audit. The demanded euro is frozen at this point.",
  packaged: "The evidence has been assembled and is ready to send.",
  claimed: "The demand has gone to the supplier. Awaiting their answer.",
  recovered: "The supplier credited us. This books into the recovered total.",
  rejected: "The supplier refused the claim-back.",
  written_off: "Closed without recovery.",
};

/** The two harvested breach flags (`contract_audit.FLAGS`), explained. The flag
 * string itself is what the server sent and is always rendered as-is. */
export const BREACH_FLAG_COPY: Record<string, string> = {
  "short discount": "The agreed per-litre discount was not applied in full.",
  "over ceiling": "The net price per litre exceeded the agreed ceiling.",
};

// ---------------------------------------------------------------------------
// Small shape checks (no arithmetic — see the module docstring)
// ---------------------------------------------------------------------------

/** "YYYY-MM", the accounting month every period-scoped transport surface uses.
 * Shape only: the service owns the real refusal (`invalid_period`). */
export function isPeriodShape(value: string): boolean {
  return /^\d{4}-(0[1-9]|1[0-2])$/.test(value.trim());
}

/** A four-digit refund year. Shape only — `recovery.validate_year` owns the
 * refusal (`invalid_year`), and it fails CLOSED deliberately: an empty result
 * for a typo'd year is indistinguishable from "you have nothing to recover". */
export function isYearShape(value: string): boolean {
  return /^\d{4}$/.test(value.trim());
}

/** A decimal figure as TYPED, kept a string all the way to the wire. Accepts
 * what the operator can legitimately enter for a €/L rate or an amount; the
 * service quantizes and refuses (`invalid_term_rate`, `rebate_amount_invalid`,
 * `recovered_amount_invalid`). Deliberately NOT a value check — comparing it to
 * a bound would mean parsing it, and no money figure is parsed in this UI. */
export function isDecimalShape(value: string): boolean {
  return /^\d+(\.\d+)?$/.test(value.trim());
}
