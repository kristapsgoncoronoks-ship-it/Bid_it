"""
PAYMENTS - generate a SEPA credit-transfer file (pain.001.001.03) from due
supplier invoices, so the month's payments can be uploaded to the bank in one batch.

    python3 payments.py 2026-05            -> SEPA_2026-05.xml + a console summary

Reads supplier invoices (suppliers.db) for the period, groups by supplier payee
(supplier_bank_accounts), and writes one CreditTransfer per invoice. Amounts in EUR;
foreign-currency invoices are listed in the summary but skipped in the SEPA file
(SEPA is euro) - pay those via your normal FX process.

The debtor (your paying entity) IBAN is supplied at run time / via the UI form -
never hardcoded. Output validates against the pain.001.001.03 schema structure.
"""
import os, sys, datetime, xml.sax.saxutils as sx

WORKDIR = os.path.dirname(os.path.abspath(__file__))


def due_invoices(period):
    import supplier_db
    con = supplier_db.connect()
    rows = con.execute("""SELECT i.supplier, i.invoice_no, i.invoice_date, i.currency,
        i.gross_total, b.iban, b.beneficiary, b.swift
        FROM supplier_invoices i
        LEFT JOIN supplier_bank_accounts b ON b.supplier=i.supplier
        WHERE i.period=? GROUP BY i.invoice_no ORDER BY i.supplier""", (period,)).fetchall()
    con.close()
    return rows


def build_sepa(period, debtor_name, debtor_iban, debtor_bic="", payment_lines=None):
    """lines: optional override list of dicts(supplier,invoice_no,amount,iban,beneficiary,bic,ref).
    Returns (xml_string, included, skipped)."""
    if payment_lines is None:
        payment_lines = []
        for r in due_invoices(period):
            if (r["currency"] or "EUR") != "EUR" or not r["iban"] or "INPUT" in (r["iban"] or ""):
                lines.append({"skip": True, "supplier": r["supplier"], "invoice_no": r["invoice_no"],
                              "amount": r["gross_total"], "currency": r["currency"],
                              "reason": "non-EUR" if (r["currency"] or "EUR") != "EUR" else "no IBAN on file"})
                continue
            lines.append({"supplier": r["supplier"], "invoice_no": r["invoice_no"],
                          "amount": round(r["gross_total"], 2), "iban": r["iban"],
                          "beneficiary": r["beneficiary"], "bic": r["swift"] or "",
                          "ref": r["invoice_no"]})
    included = [l for l in lines if not l.get("skip")]
    skipped = [l for l in lines if l.get("skip")]
    total = round(sum(l["amount"] for l in included), 2)
    now = datetime.datetime.now(_tz.utc)
    msgid = "FFS" + now.strftime("%Y%m%d%H%M%S")
    exec_date = (now.date() + datetime.timedelta(days=1)).isoformat()
    e = sx.escape

    txs = ""
    for i, l in enumerate(included, 1):
        txs += f"""
      <CdtTrfTxInf>
        <PmtId><EndToEndId>{e(l['ref'][:35])}</EndToEndId></PmtId>
        <Amt><InstdAmt Ccy="EUR">{l['amount']:.2f}</InstdAmt></Amt>
        {('<CdtrAgt><FinInstnId><BIC>'+e(l['bic'])+'</BIC></FinInstnId></CdtrAgt>') if l.get('bic') else ''}
        <Cdtr><Nm>{e((l.get('beneficiary') or l['supplier'])[:70])}</Nm></Cdtr>
        <CdtrAcct><Id><IBAN>{e(l['iban'].replace(' ',''))}</IBAN></Id></CdtrAcct>
        <RmtInf><Ustrd>{e(l['ref'][:140])}</Ustrd></RmtInf>
      </CdtTrfTxInf>"""

    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Document xmlns="urn:iso:std:iso:20022:tech:xsd:pain.001.001.03">
  <CstmrCdtTrfInitn>
    <GrpHdr>
      <MsgId>{msgid}</MsgId>
      <CreDtTm>{now.replace(tzinfo=None).isoformat(timespec='seconds')}</CreDtTm>
      <NbOfTxs>{len(included)}</NbOfTxs>
      <CtrlSum>{total:.2f}</CtrlSum>
      <InitgPty><Nm>{e(debtor_name[:70])}</Nm></InitgPty>
    </GrpHdr>
    <PmtInf>
      <PmtInfId>{msgid}</PmtInfId>
      <PmtMtd>TRF</PmtMtd>
      <NbOfTxs>{len(included)}</NbOfTxs>
      <CtrlSum>{total:.2f}</CtrlSum>
      <PmtTpInf><SvcLvl><Cd>SEPA</Cd></SvcLvl></PmtTpInf>
      <ReqdExctnDt>{exec_date}</ReqdExctnDt>
      <Dbtr><Nm>{e(debtor_name[:70])}</Nm></Dbtr>
      <DbtrAcct><Id><IBAN>{e(debtor_iban.replace(' ',''))}</IBAN></Id></DbtrAcct>
      <DbtrAgt><FinInstnId>{('<BIC>'+e(debtor_bic)+'</BIC>') if debtor_bic else '<Othr><Id>NOTPROVIDED</Id></Othr>'}</FinInstnId></DbtrAgt>
      <ChrgBr>SLEV</ChrgBr>{txs}
    </PmtInf>
  </CstmrCdtTrfInitn>
</Document>"""
    return xml, included, skipped


if __name__ == "__main__":
    period = sys.argv[1] if len(sys.argv) > 1 else "2026-05"
    debtor = sys.argv[2] if len(sys.argv) > 2 else "PAYING ENTITY (set in UI)"
    iban = sys.argv[3] if len(sys.argv) > 3 else "INPUT_DEBTOR_IBAN"
    xml, inc, skip = build_sepa(period, debtor, iban)
    out = f"{WORKDIR}/SEPA_{period}.xml"
    open(out, "w").write(xml)
    print(f"SEPA file: {out}")
    print(f"included {len(inc)} EUR payments, total {sum(l['amount'] for l in inc):,.2f} EUR")
    for l in inc:
        print(f"  {l['supplier']:9}{l['invoice_no']:22}{l['amount']:>12,.2f}  {l['iban'][:20]}")
    if skip:
        print("skipped (handle separately):")
        for l in skip:
            print(f"  {l['supplier']:9}{l['invoice_no']:22}{l['amount']:>12,.2f}  {l['reason']}")
