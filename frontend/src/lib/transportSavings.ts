/**
 * Pure helpers for the negotiation-evidence workspace (WO-90) over the three
 * WO-87 read routes. No React, no network, no arithmetic — so the vocabulary
 * this screen renders is testable through the page and cannot drift into a
 * component.
 *
 * WHAT THIS FILE IS REALLY FOR: R53's SECOND FRAMING
 * ----------------------------------------------------
 * `BA_fleet_fuel.md` R53 keeps two analyses apart that a reader would otherwise
 * flatten into one. The supplier-contract analysis measures a breach of an
 * agreed term, and is presented as a contractual entitlement with a formal
 * letter behind it. **These three analyses are not that.** Nobody agreed that a supplier would match the
 * cheapest rival network on the day, so there is no term, no breach, and nothing
 * a supplier is obliged to pay: the figures are evidence to take into a
 * negotiation, and the backend says so on every response in one string,
 * `savings.LEGAL_FRAMING`.
 *
 * Which is why this module holds NO framing string of its own. The framing is
 * rendered VERBATIM from the wire (`legal_framing`), exactly as WO-83 printed it
 * on its artifacts and WO-86 rendered it on the audit screen: a paraphrase is
 * how a framing gets softened, and there is deliberately only one string. What
 * lives here instead is the surrounding explanation — why a day without a rival
 * is not a zero, why the two grains do not reconcile, and what these numbers are
 * for — none of which restates the framing and none of which borrows a word from
 * the family of vocabulary the backend refuses to use.
 *
 * R52 — THE NON-RECONCILIATION IS COPY, NOT A BUG REPORT
 * --------------------------------------------------------
 * The same rows produce two different totals at two different grains, and R52's
 * requirement is that *"both figures appear with their grain in the label; no
 * surface implies they should match"*. The grain labels come off the responses
 * themselves (`grain`); `NON_RECONCILIATION` is the sentence that turns "these
 * two numbers disagree" from a support ticket into a documented property.
 *
 * NOTHING HERE TOUCHES A NUMBER. Every figure the screen shows arrives already
 * totalled and already quantized by the service and is rendered from its wire
 * string (§4.9/§4.10). The two helpers below are SHAPE checks on typed input —
 * they parse nothing and compare nothing to a bound.
 */

/** The three analyses, in the order the service's own module presents them.
 * Labels name the analysis, never a consequence of it. */
export const SAVINGS_TABS: { value: SavingsTabKey; label: string }[] = [
  { value: "same-day", label: "Same-day overpay" },
  { value: "benchmark", label: "Internal benchmark" },
  { value: "rebate", label: "Expected rebate" },
];

export type SavingsTabKey = "same-day" | "benchmark" | "rebate";

/**
 * R52, in the operator's words. Rendered on the page beside both overpay panels
 * so a reader who opens each in turn is TOLD why the totals differ rather than
 * concluding that one of them is wrong.
 */
export const NON_RECONCILIATION =
  "These two totals are measured at different grains over the same fuel lines, and they are not expected to agree. " +
  "The same-day figure compares each supplier against the cheapest rival network that actually traded in that country on that day. " +
  "The monthly figure compares each supplier against the best price you yourself paid in that country across the whole month. " +
  "Different comparison, different denominator — a difference between them is the design, not a fault, and neither number should be added to the other.";

/**
 * The presentation rule this analysis is most likely to be got wrong. §2.5
 * requires at least two suppliers on a day before any comparison is made; a day
 * with one supplier therefore produces NO finding. Reporting that as €0.00 would
 * assert something the data never said — that you were competitive that day.
 */
export const NO_RIVAL_EXPLANATION =
  "A day is only compared when at least two suppliers traded in that country. On the days counted here just one did, " +
  "so there was no rival price to compare against and no finding was produced. That is not the same as a zero: " +
  "it means the comparison could not be made, not that nothing could have been saved.";

/** What these three surfaces do to the underlying data: nothing (§4.19). */
export const ADVISORY_NOTE =
  "All three analyses are read-only. Opening this page changes no figure, no status and no document — " +
  "it re-reads the validated fuel lines and totals them, and nothing here gates or blocks any other part of the workspace.";

/**
 * Why the same-day analysis takes no supplier filter, in WO-87's own reasoning.
 * Stated on the page because the absence otherwise reads as an oversight.
 */
export const NO_SUPPLIER_FILTER_REASON =
  "There is deliberately no supplier filter here. Narrowing the rows would change which supplier was the cheapest that day, " +
  "so the same question would quietly return a different answer. The per-supplier table below attributes the total instead, " +
  "computed over the complete set of suppliers.";

/** The learned-rebate analysis, stated so it cannot be read as an entitlement.
 * A missing rebate is a document to go and find. */
export const EXPECTED_REBATE_NOTE =
  "This compares each line against what that supplier and country usually grant, learned from your own history. " +
  "A flagged line is a document to go and find — a rebate that often arrives on a separate invoice — and the euro beside it " +
  "is the size of what is absent, nothing more.";

/** An ISO 3166-1 alpha-2 code as typed. SHAPE only: the service owns the real
 * refusal (`invalid_country`), and it is the only control. Empty is legal — the
 * filter is optional and an empty box means "every country". */
export function isCountryShape(value: string): boolean {
  const v = value.trim();
  return v === "" || /^[A-Za-z]{2}$/.test(v);
}

/** A supplier / fuel-card network code as typed. The service takes any string
 * and simply matches nothing if it is unknown, so this only refuses whitespace
 * that would otherwise be sent as a meaningless filter. */
export function isSupplierShape(value: string): boolean {
  return value.trim() === value;
}
