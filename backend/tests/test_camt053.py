"""camt.053 statement import (Phase 20): the reconciliation importer accepts an
ISO 20022 bank-to-customer statement XML (credits + debits), not only CSV/PDF."""

import io

import pytest

from app.services import bank_statement

CAMT = """<?xml version="1.0" encoding="UTF-8"?>
<Document xmlns="urn:iso:std:iso:20022:tech:xsd:camt.053.001.02">
  <BkToCstmrStmt>
    <Stmt>
      <Id>STMT-1</Id>
      <Ntry>
        <Amt Ccy="EUR">1210.00</Amt>
        <CdtDbtInd>CRDT</CdtDbtInd>
        <BookgDt><Dt>2026-02-05</Dt></BookgDt>
        <NtryDtls><TxDtls><RmtInf><Ustrd>Payment from Acme INV-1</Ustrd></RmtInf></TxDtls></NtryDtls>
      </Ntry>
      <Ntry>
        <Amt Ccy="EUR">250.00</Amt>
        <CdtDbtInd>DBIT</CdtDbtInd>
        <BookgDt><Dt>2026-02-10</Dt></BookgDt>
        <AddtlNtryInf>Payroll payout PAYROLL-1</AddtlNtryInf>
      </Ntry>
    </Stmt>
  </BkToCstmrStmt>
</Document>
"""


def test_parser_reads_both_directions():
    res = bank_statement.parse("stmt.xml", CAMT.encode())
    assert res.method == "camt.053" and len(res.transactions) == 2
    by_dir = {t.direction: t for t in res.transactions}
    assert str(by_dir["credit"].amount) == "1210.00"
    assert str(by_dir["debit"].amount) == "250.00"
    assert "Acme" in by_dir["credit"].description


@pytest.mark.asyncio
async def test_reconciliation_import_camt(auth_client):
    await auth_client.put("/api/v1/modules/issuing", json={"enabled": True})
    files = {"file": ("stmt.xml", io.BytesIO(CAMT.encode()), "application/xml")}
    r = await auth_client.post("/api/v1/reconciliation/import", files=files)
    assert r.status_code == 201, r.text
    assert r.json()["imported"] == 2 and r.json()["method"] == "camt.053"

    stmts = (await auth_client.get("/api/v1/reconciliation/statements")).json()
    assert stmts[0]["source_format"] == "camt" and stmts[0]["unmatched"] == 2
    lines = (
        await auth_client.get(f"/api/v1/reconciliation/statements/{stmts[0]['id']}/lines")
    ).json()
    by_dir = {ln["direction"]: ln for ln in lines}
    assert by_dir["credit"]["amount"] == "1210.00"
    assert by_dir["debit"]["amount"] == "-250.00"
