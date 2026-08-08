/**
 * Pure helpers for the diesel-excise screen (WO-92) over the five WO-91 routes.
 * No React, no network, no arithmetic — so the vocabulary this screen renders is
 * testable through the page and cannot drift into a component.
 *
 * WHAT THIS FILE IS REALLY FOR: THE ELIGIBILITY NON-ASSERTION
 * ------------------------------------------------------------
 * Around seven EU states refund part of the excise a haulier pays on commercial
 * diesel. This product multiplies validated diesel litres by a per-1,000-litre
 * rate and stops there. The conditions that decide whether a given haulier
 * actually qualifies — vehicle weight and carrier registration — are
 * deliberately NOT modelled, and `app/services/transport/excise.py` says so in
 * one server-side sentence that rides every response.
 *
 * Which is why this module holds NO framing string of its own. The eligibility
 * statement, the rate caveat, the legal framing, the customs addressee and the
 * litre basis are all rendered VERBATIM from the wire, exactly as
 * `transportSavings.ts` deliberately holds none of its own: a paraphrase is how
 * a limitation gets softened, and there is intentionally only one string. What
 * lives here instead is the surrounding explanation — why a country we hold no
 * rate for produces nothing rather than a zero, what changing a rate does, and
 * what the screen does to the underlying data (nothing) — none of which restates
 * the server's wording and none of which borrows a word from the family of
 * vocabulary this surface must never use.
 *
 * THE RATE IS A PLACEHOLDER UNTIL SOMEONE VERIFIES IT. The shipped default is an
 * explicit placeholder in a reported band, not a statutory figure: the duty is
 * not harmonised across the EU and national rates are revised. So every rate the
 * screen shows is shown beside its SOURCE, and `RATE_SOURCE_LABELS` is that pair
 * of words — the same two the customs workbook prints, so the spreadsheet and
 * the screen say the same thing about the same rate.
 *
 * NOTHING HERE TOUCHES A NUMBER. Every figure the screen shows arrives already
 * multiplied and already quantized by the service and is rendered from its wire
 * string (§4.9/§4.10). There is no arithmetic of any kind in this module.
 */

/** The two panels, in the order the service's own module presents them: the
 * period's figures first, the rate registry they are computed from second. */
export const EXCISE_TABS: { value: ExciseTabKey; label: string }[] = [
  { value: "figures", label: "Monthly figures" },
  { value: "rates", label: "Country rates" },
];

export type ExciseTabKey = "figures" | "rates";

/**
 * The two words a rate's source may read, keyed by the wire's own boolean
 * (`rate_is_override` on a cell, `is_override` on a rate row). They are the
 * customs workbook's own pair, so the sheet a haulier files and the screen an
 * operator reads describe the same rate identically.
 */
export const RATE_SOURCE_LABELS = {
  override: "Verified override",
  placeholder: "Indicative default",
} as const;

export function rateSourceLabel(isOverride: boolean): string {
  return isOverride
    ? RATE_SOURCE_LABELS.override
    : RATE_SOURCE_LABELS.placeholder;
}

/**
 * The presentation rule this surface is most likely to get wrong, and the one
 * the whole screen is arranged around.
 *
 * A country this product holds no rate for produces NO figure. Printing €0.00
 * for it would say "this state refunds you nothing", which is a different
 * statement from "we hold no rate for this state" and is materially false. The
 * wire shape for those countries carries litres and a line count and no euro
 * field at all, so the table below cannot render one even by accident.
 */
export const NO_RATE_EXPLANATION =
  "These countries had validated diesel litres in the month, and this product holds no rate for them. " +
  "They are listed with their litres so the size of what is not being reported is visible. " +
  "That is not a figure of zero: zero would mean the state refunds nothing, and what is actually true is " +
  "that we hold no rate to compute anything with. Type a rate under Country rates and they will appear above.";

/**
 * The rate half of the same honesty, in the operator's words rather than the
 * service's (the service's own `rate_caveat` is rendered verbatim beside this).
 * Stated because a rate shown without its provenance reads as a statutory
 * figure, and the shipped one is not.
 */
export const RATE_SOURCE_NOTE =
  "Every rate on this screen is shown with its source. An indicative default is the placeholder this product ships " +
  "for a state nobody has verified yet; a verified override is a rate an operator typed after checking it with the " +
  "customs authority. Overriding a rate changes only what the indicative figure is computed from — it files nothing, " +
  "sends nothing and changes no other figure in the workspace.";

/** What this screen does to the underlying data: nothing (§4.19). The rate
 * registry is the one exception and it is configuration, not a financial
 * record. */
export const ADVISORY_NOTE =
  "The analysis and the customs packet are read-only. Opening this page changes no figure, no status and no document — " +
  "it re-reads the validated diesel lines, sums their litres and applies the rate, and nothing here gates or blocks any " +
  "other part of the workspace. Setting or clearing a country rate is the only change this screen can make, and it is " +
  "recorded against the operator who made it.";

/** What the packet is and who it goes to — the addressee itself comes off the
 * wire (`filed_with`); this is the surrounding explanation of the artifact. */
export const PACKET_NOTE =
  "The evidence packet is the spreadsheet a haulier files with customs: the same litres, the same rate and the same " +
  "figure as the table above, with a total row and the same caveats printed on both sheets. It is generated from this " +
  "month's analysis, so it cannot disagree with what is on screen. A month with nothing to report produces no packet — " +
  "an empty filing looks like a filing and supports nothing.";
