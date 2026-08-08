import type { Tone } from "../components/ui";

/**
 * Pure helpers for the client claim-status portal (WO-93, G4.4/R39). No React,
 * no network, no arithmetic.
 *
 * WHAT IS DELIBERATELY NOT IN THIS FILE: a stage LABEL, a stage DESCRIPTION, or
 * any other wording for a stage. Every word the client reads about a stage
 * arrives on the wire from `app/services/transport/client_status.py`'s
 * `STAGE_LABELS` / `STAGE_DESCRIPTIONS`, and is rendered verbatim — the same
 * posture `lib/transportSavings.ts` takes with R53's framing strings and
 * `lib/transportExcise.ts` with the eligibility statement.
 *
 * The reason is R39 itself. Its acceptance line is an ABSENCE — a client-role
 * session must not be able to see the internal workflow vocabulary, or what we
 * charge, anywhere — and the words that keep that true are
 * `BA_fleet_fuel.md` §3.D's, not ours. A label held here could be softened,
 * shortened or "improved" in a UI edit that no backend test would ever see; a
 * label held on the server is scanned by the backend's own vocabulary tests
 * every run. `e2e/claim-status.spec.ts` asserts the other half: the label text
 * appears in no file under `src/`.
 *
 * What IS here is presentation only — which colour a stage wears, and the copy
 * for the states the wire has no field for (an empty year, claims outside the
 * ladder). Colour is not vocabulary: it says nothing the words do not.
 */

/**
 * A tone per stage slug, keyed by the exact value the service emits. Rendering
 * ORDER comes from the response (the service always emits all six in its own
 * order), never from this object — so a stage added server-side appears rather
 * than disappearing, and an unknown slug falls back to neutral rather than
 * crashing the row.
 *
 * The ladder reads warm-to-cool as a claim progresses, and `needs_attention` is
 * the only one that raises its voice — it is the single stage where something
 * is genuinely stuck.
 */
const STAGE_TONES: Record<string, Tone> = {
  prep: "neutral",
  ready: "info",
  filed: "brand",
  awaiting: "warning",
  refunded: "success",
  needs_attention: "danger",
};

export function stageTone(stage: string): Tone {
  return STAGE_TONES[stage] ?? "neutral";
}

/** The heading for a year that holds no claims at all. The server answers an
 * empty year with a 200 and six zeroed stages (never a 404), so the page has to
 * SAY it — six zeroes on their own look exactly like a broken load. */
export const EMPTY_YEAR_TITLE = "No refund claims for this year yet";

export const EMPTY_YEAR_BODY =
  "Nothing is in progress for this year because no claim has been opened. Pick a different year above if you are looking for an earlier one.";

/** What `not_shown_claims` means, in the client's language. The count exists so
 * the arithmetic is honest; the reason a claim left the ladder is internal, and
 * naming it here would undo the whole point of the surface. */
export const NOT_SHOWN_NOTE =
  "Claims we are no longer pursuing are not listed above. Ask us any time and we will explain where one went.";

/** The page's own basis line. The figures are net EUR exactly as the service
 * totalled them, and this page adds nothing up (§4.9/§4.10). */
export const BASIS_NOTE =
  "Amounts are the reclaimable VAT on each claim, in net EUR, exactly as we calculated them.";

/** What a dash in the amount column means. The wire carries `null`, never
 * `"0.00"`, when no single-currency figure can be stated for a claim yet —
 * rendering that as a zero would tell the client something false. */
export const NO_FIGURE_NOTE =
  "A dash means we cannot state a single figure for that claim yet, not that it is worth nothing.";

/** A four-digit refund year. Shape only — the server owns the refusal
 * (`invalid_year`), and it fails CLOSED deliberately: an empty result for a
 * typo would read as "you have no claims". Imported from the recovery helpers
 * rather than re-declared: one shape check, one source. */
export { isYearShape } from "./transportRecovery";
