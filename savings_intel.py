"""
SAVINGS & INTELLIGENCE — one consolidated fuel-cost intelligence surface.

A THIN orchestrator: it adds NO detection logic of its own. It calls the existing
detectors and totals what they already produce, quantizing the EUR headline figures
HALF_UP (money.f2/fsum), consistent with the project's money basis:

  • avoidable overpay   pricing_intelligence.internal_benchmark()  (self-sourced,
                        best-of-your-own-suppliers; per country × period)
  • recoverable € per
    contract breach     contract_audit.audit()  (short discount / over ceiling)
  • anomalies           anomaly.find()  (relative price/volume/off-period flags)

The headline number is the TOTAL ADDRESSABLE €:
    total_addressable_eur = avoidable_overpay_eur + recoverable_contract_eur

This is fuel-cost intelligence ONLY — no VAT/claim/recovery figures (those live on
the admin-only recovery surface). All prices NET EUR/L, final (VAT excluded, rebates
applied). This is an INTERNAL capability over each client's OWN data — no external/
scraped data, so no peer-benchmark legal guardrail applies here. A future M1 (peer
benchmark across clients) would plug a NEW addressable source in alongside
`avoidable_overpay` (see `# M1 plug-in point` below), gated by anonymisation rules.

Transactions are read ONLY through the read-only product boundary INSIDE each
detector (pricing_intelligence.product_connect / the audit/anomaly read handles);
this module never opens a DB itself.
"""
import money


def _latest_period():
    """Default period = the most recent loaded period, read READ-ONLY from the
    engine-owned product DB via the dataproduct boundary. Returns None when no data
    is loaded."""
    import dataproduct
    con = dataproduct.connect("fuel_history")
    try:
        row = con.execute(
            "SELECT MAX(period) p FROM transactions").fetchone()
    finally:
        con.close()
    return row["p"] if row and row["p"] else None


def summary(period=None):
    """Aggregate the three detectors into ONE dict for the /intel surface + Excel.

    Returns:
        {
          period,                       resolved period (or None when no data)
          avoidable_overpay_eur,        float, money.f2 — sum over internal_benchmark cells
          recoverable_contract_eur,     float, money.f2 — contract_audit total recoverable
          anomaly_count,                int   — anomaly.find flag count
          total_addressable_eur,        float, money.f2 — overpay + recoverable
          by_country,                   [{country, overpay_eur, recover_eur, addressable_eur}]
                                        sorted by addressable € desc
          top_actions,                  [{kind, country, detail, eur}] biggest € first
        }
    All € figures quantized HALF_UP (money.f2/fsum). No detection logic here — this
    just sums and orders what the detectors already returned.
    """
    import pricing_intelligence
    import contract_audit
    import anomaly

    period = period or _latest_period()

    overpay_rows, overpay_summ = pricing_intelligence.internal_benchmark(period=period)
    breach_flags, breach_summ = contract_audit.audit(period=period)
    anomaly_flags = anomaly.find(period) if period else []

    # headline totals — re-quantize over the detector rows so the surface owns its own
    # HALF_UP sum (the detector summaries already quantize, but summing per-country here
    # keeps one consistent basis with the by_country breakdown below).
    avoidable_overpay_eur = money.fsum(r["overpay_eur"] for r in overpay_rows)
    recoverable_contract_eur = money.fsum(f["recover_eur"] for f in breach_flags)
    total_addressable_eur = money.f2(avoidable_overpay_eur + recoverable_contract_eur)

    # by-country breakdown: overpay (per-cell country) + recoverable (per-flag country)
    by_ctry = {}
    for r in overpay_rows:
        by_ctry.setdefault(r["country"], {"overpay": 0.0, "recover": 0.0})
        by_ctry[r["country"]]["overpay"] += r["overpay_eur"]
    for f in breach_flags:
        by_ctry.setdefault(f["country"], {"overpay": 0.0, "recover": 0.0})
        by_ctry[f["country"]]["recover"] += f["recover_eur"]
    by_country = []
    for c, v in by_ctry.items():
        ov, rec = money.f2(v["overpay"]), money.f2(v["recover"])
        by_country.append({"country": c, "overpay_eur": ov, "recover_eur": rec,
                           "addressable_eur": money.f2(ov + rec)})
    by_country.sort(key=lambda x: x["addressable_eur"], reverse=True)

    # top actions: the biggest single € opportunities across both monetary detectors,
    # ordered by €. (Anomalies carry no € — they're surfaced as a count + the worklist.)
    actions = []
    for r in overpay_rows:
        actions.append({
            "kind": "Route volume to cheaper supplier",
            "country": r["country"],
            "detail": f"{r['country']} {r['bucket']}: best {r['best_supplier']} "
                      f"@ {r['best_price']:.4f} €/L vs your avg {r['your_avg']:.4f} "
                      f"({r['litres']:,.0f} L)",
            "eur": r["overpay_eur"]})
    for f in breach_flags:
        actions.append({
            "kind": f"Recover contract breach — {f['issue']}",
            "country": f["country"],
            "detail": f"{f['supplier']} {f['country']} {f['station']} {f['period']}: "
                      f"{f['issue']} (exp {f['expected']:.4f} vs act {f['actual']:.4f} "
                      f"€/L, {f['litres']:,.0f} L)",
            "eur": f["recover_eur"]})
    # M1 plug-in point: a peer-benchmark detector (overpay vs an anonymised cross-client
    # market) would append its addressable-€ rows here and into avoidable_overpay_eur /
    # by_country — same shape, same money basis — once the anonymisation guardrail lands.
    actions.sort(key=lambda a: a["eur"], reverse=True)

    return {
        "period": period,
        "avoidable_overpay_eur": money.f2(avoidable_overpay_eur),
        "recoverable_contract_eur": money.f2(recoverable_contract_eur),
        "anomaly_count": len(anomaly_flags),
        "total_addressable_eur": total_addressable_eur,
        "by_country": by_country,
        "top_actions": actions,
    }


if __name__ == "__main__":
    import sys
    s = summary(sys.argv[1] if len(sys.argv) > 1 else None)
    print(f"=== Savings & Intelligence — {s['period']} ===")
    print(f"avoidable overpay   EUR {s['avoidable_overpay_eur']:,.2f}")
    print(f"recoverable contract EUR {s['recoverable_contract_eur']:,.2f}")
    print(f"anomalies            {s['anomaly_count']}")
    print(f"TOTAL ADDRESSABLE    EUR {s['total_addressable_eur']:,.2f}  (NET EUR/L basis)")
    for a in s["top_actions"][:15]:
        print(f"  EUR {a['eur']:>10,.2f}  {a['kind']}: {a['detail']}")
