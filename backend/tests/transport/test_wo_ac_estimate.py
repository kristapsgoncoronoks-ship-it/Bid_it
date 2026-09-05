"""WO-AC (G4.8, R43) — the refund-estimate funnel.

WHAT THESE TESTS ARE FOR
-------------------------
R43's rule is an ABSENCE — *"in-memory only, no product-DB write"* — and an
absence is the one thing a docstring cannot certify. The flagship test here
counts rows in every table the real intake path writes to, before and after,
and requires them equal. A funnel that quietly created a `fuel_transactions`
row would put a prospect's data into a workspace's real tables before anyone
decided to become a customer, and no assertion about the returned NUMBER would
notice.

The rest certify that the number is honest about what it could not see:

1. A line with no exchange rate is COUNTED and NAMED, never dropped — the
   failure mode that makes a sales tool understate the opportunity while
   looking confident.
2. `below_minimum` has three states, and `None` (could not be compared in the
   country's own currency) is not `False` (clears the threshold).
3. The Art. 17 threshold follows the PERIOD: €400 quarterly, €50 annual. The
   same figures flip from "below" to "clears" on nothing but the period.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.core.errors import PermissionError, ValidationError
from app.models.transport.fuel_transaction import FuelTransaction
from app.models.transport.supplier_registration import SupplierVatRegistration
from app.models.transport.vat_claim import VatRefundClaim, VatRefundClaimLine
from app.services.transport import estimate as estimate_svc
from tests.factories.transport import synthetic_eurowag_statement
from tests.transport.conftest import enable_transport, make_org

PERIOD_Q = "2026-Q2"


def _row(
    *,
    country: str,
    net: str,
    vat: str,
    currency: str = "EUR",
    qty: str = "100.00",
    date: str = "2026-06-03",
) -> dict[str, object]:
    gross = str(Decimal(net) + Decimal(vat))
    return {
        "txn_date": date,
        "txn_time": "08:12",
        "vehicle_ref": "LV-1234",
        "station": "Demo Fuel Hub",
        "country": country,
        "product": "DIESEL",
        "qty": qty,
        "currency": currency,
        "net_local": net,
        "vat_local": vat,
        "gross_local": gross,
        "invoice_ref": "INV-0001",
    }


def _statement(rows: list[dict[str, object]]) -> bytes:
    return synthetic_eurowag_statement(rows=rows).encode()


async def _org(db_session):
    org = await make_org(db_session)
    await enable_transport(db_session, org.id)
    await db_session.commit()
    return org


# --------------------------------------------------------------------------- #
# 1. R43's rule is an ABSENCE — prove it directly
# --------------------------------------------------------------------------- #

#: Every table the REAL intake path (`statement_ingest`) writes to. If the
#: funnel ever grows a write, it almost certainly lands in one of these.
_MUST_NOT_GROW = (FuelTransaction, SupplierVatRegistration, VatRefundClaim, VatRefundClaimLine)


async def _counts(db_session) -> dict[str, int]:
    out: dict[str, int] = {}
    for model in _MUST_NOT_GROW:
        out[model.__tablename__] = int(
            await db_session.scalar(select(func.count()).select_from(model)) or 0
        )
    return out


@pytest.mark.asyncio
async def test_wo_ac_the_estimate_writes_nothing(db_session):
    """R43, verbatim: in-memory only, NO product-DB write. Asserted by counting
    rows rather than by trusting the service's docstring — this is the one
    property of the funnel that a test of its return value could never see."""
    org = await _org(db_session)
    before = await _counts(db_session)

    result = await estimate_svc.estimate(
        db_session,
        org.id,
        filename="statement.csv",
        content=_statement([_row(country="BE", net="1000.00", vat="210.00")]),
        period=PERIOD_Q,
    )
    await db_session.commit()

    assert result.lines == 1, "the fixture must actually have parsed, or this proves nothing"
    assert await _counts(db_session) == before


# --------------------------------------------------------------------------- #
# 2. The headline figure, and the assumption behind it
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_wo_ac_recoverable_is_the_invoiced_vat_per_country(db_session):
    """§2.3: `recoverable_eur = vat_eur` — invoiced VAT assumed recoverable."""
    org = await _org(db_session)
    result = await estimate_svc.estimate(
        db_session,
        org.id,
        filename="statement.csv",
        content=_statement(
            [
                _row(country="BE", net="1000.00", vat="210.00"),
                _row(country="BE", net="2000.00", vat="420.00"),
                _row(country="FR", net="500.00", vat="100.00"),
            ]
        ),
        period=PERIOD_Q,
    )

    by_country = {c.country: c for c in result.countries}
    assert by_country["BE"].vat_eur == Decimal("630.00")
    assert by_country["FR"].vat_eur == Decimal("100.00")
    assert result.recoverable_eur == Decimal("730.00")
    assert by_country["BE"].lines == 2


@pytest.mark.asyncio
async def test_wo_ac_every_response_carries_the_r53_caveat(db_session):
    """R53 forbids flattening the framing. This analysis is 'indicative,
    verify' — never the claim-back wording — and the caveat rides every
    response so a client cannot render the number without it."""
    org = await _org(db_session)
    result = await estimate_svc.estimate(
        db_session,
        org.id,
        filename="statement.csv",
        content=_statement([_row(country="BE", net="1000.00", vat="210.00")]),
        period=PERIOD_Q,
    )
    assert result.caveat == estimate_svc.CAVEAT
    assert "never a filed figure" in result.caveat
    assert "verify" in result.caveat.lower()


# --------------------------------------------------------------------------- #
# 3. Honest about what it could not see
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_wo_ac_an_unconvertible_line_is_counted_and_named(db_session):
    """`fx` returns None rather than a guess when no rate exists. The funnel
    must not silently skip those lines: it would report a SMALLER opportunity
    than the file contains and give no sign it had done so."""
    org = await _org(db_session)
    result = await estimate_svc.estimate(
        db_session,
        org.id,
        filename="statement.csv",
        content=_statement(
            [
                _row(country="BE", net="1000.00", vat="210.00"),
                # ZAR is a structurally valid 3-letter code with NO entry in
                # `fx.FALLBACK_RATES`, so `resolve_rate` genuinely finds
                # nothing. Chosen deliberately: SEK looks unconvertible and is
                # NOT — the harness seeds every European currency plus the
                # global majors, so a SEK fixture here would have passed while
                # exercising the conversion path instead of the missing-rate
                # one, and reported false confidence in this very assertion.
                _row(country="BE", net="1000.00", vat="250.00", currency="ZAR"),
            ]
        ),
        period=PERIOD_Q,
    )

    be = next(c for c in result.countries if c.country == "BE")
    assert be.lines == 2, "the line is still counted as present"
    assert be.vat_eur == Decimal("210.00"), "but its money is not in the EUR figure"
    assert be.unconverted_lines == 1
    assert result.unconverted_lines == 1
    assert any("could not be converted" in w for w in result.warnings)
    assert any("larger than this estimate shows" in w for w in result.warnings)


@pytest.mark.asyncio
async def test_wo_ac_a_line_with_no_country_refuses_the_whole_file(db_session):
    """The parser validates country codes structurally and raises on a bad one,
    which refuses the WHOLE statement — so the funnel can never receive a line
    it would have to attribute to no refund regime.

    This test exists because the first draft of the service carried its own
    "count it as unattributable and warn" branch for this case. That branch was
    unreachable, and it was also WEAKER than what already happens: it would
    have quietly excluded money from a total while the parser's own answer is
    to reject the file. The behaviour asserted here is the real one."""
    org = await _org(db_session)
    with pytest.raises(ValidationError) as exc:
        await estimate_svc.estimate(
            db_session,
            org.id,
            filename="statement.csv",
            content=_statement(
                [
                    _row(country="BE", net="1000.00", vat="210.00"),
                    _row(country="", net="900.00", vat="189.00"),
                ]
            ),
            period=PERIOD_Q,
        )
    assert exc.value.code == "unrecognized_statement"
    assert "invalid country code" in str(exc.value)


# --------------------------------------------------------------------------- #
# 4. The Art. 17 minimum — three states, and the period decides the threshold
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_wo_ac_the_minimum_flag_follows_the_period_not_the_amount(db_session):
    """€400 quarterly, €50 annual. The SAME figures flip from below to clear on
    nothing but the period — which is why the route takes one and why using the
    wrong one would flag the wrong countries as too small to bother with."""
    org = await _org(db_session)
    content = _statement([_row(country="BE", net="1000.00", vat="210.00")])

    quarterly = await estimate_svc.estimate(
        db_session, org.id, filename="s.csv", content=content, period="2026-Q2"
    )
    annual = await estimate_svc.estimate(
        db_session, org.id, filename="s.csv", content=content, period="2026-YEAR"
    )

    assert quarterly.countries[0].below_minimum is True
    assert quarterly.countries[0].threshold == Decimal("400.00")
    assert annual.countries[0].below_minimum is False
    assert annual.countries[0].threshold == Decimal("50.00")


@pytest.mark.asyncio
async def test_wo_ac_a_local_basis_country_reports_its_own_currency(db_session):
    """Sweden's Art. 17 minimum is a SEK figure, and `minimum.min_for` says so.
    The estimate reports the threshold in the currency the comparison is
    actually made in, never a euro figure relabelled."""
    org = await _org(db_session)
    result = await estimate_svc.estimate(
        db_session,
        org.id,
        filename="s.csv",
        content=_statement([_row(country="SE", net="10000.00", vat="2500.00", currency="SEK")]),
        period=PERIOD_Q,
    )

    se = result.countries[0]
    assert se.threshold_currency == "SEK"
    assert se.threshold == Decimal("4000")
    assert se.currency == "SEK"
    assert se.vat_local == Decimal("2500.00")
    # 2500 SEK is below the 4000 SEK quarterly threshold — and the comparison
    # was made in SEK, not in the euro figure, which is the whole point of
    # `minimum`'s local basis.
    assert se.below_minimum is True


@pytest.mark.asyncio
async def test_wo_ac_mixed_currencies_make_the_minimum_uncomparable_not_false(db_session):
    """The three-state rule. A local-basis country whose lines arrive in more
    than one currency has no single local figure to compare, so the answer is
    `None` — "not compared". Reporting `False` would tell an operator the claim
    clears a threshold nobody checked."""
    org = await _org(db_session)
    result = await estimate_svc.estimate(
        db_session,
        org.id,
        filename="s.csv",
        content=_statement(
            [
                _row(country="SE", net="10000.00", vat="2500.00", currency="SEK"),
                _row(country="SE", net="100.00", vat="21.00", currency="EUR"),
            ]
        ),
        period=PERIOD_Q,
    )

    se = result.countries[0]
    assert se.currency is None
    assert se.below_minimum is None
    assert se.vat_local is None


@pytest.mark.asyncio
async def test_wo_ac_a_euro_basis_country_is_compared_even_in_one_currency(db_session):
    """The mirror of the test above: `None` must mean "could not compare", not
    "more than one thing happened". Belgium compares in EUR, so a single-
    currency Belgian block gets a real verdict."""
    org = await _org(db_session)
    result = await estimate_svc.estimate(
        db_session,
        org.id,
        filename="s.csv",
        content=_statement([_row(country="BE", net="5000.00", vat="1050.00")]),
        period=PERIOD_Q,
    )
    be = result.countries[0]
    assert be.below_minimum is False
    assert be.threshold_currency == "EUR"


# --------------------------------------------------------------------------- #
# 5. Refusals
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_wo_ac_a_malformed_period_is_refused_by_the_claim_validator(db_session):
    """The SAME `claim.validate_ref_period` a real claim uses — never a second
    regex that could drift from it."""
    org = await _org(db_session)
    with pytest.raises(ValidationError) as exc:
        await estimate_svc.estimate(
            db_session,
            org.id,
            filename="s.csv",
            content=_statement([_row(country="BE", net="1.00", vat="0.21")]),
            period="2026-06",
        )
    assert exc.value.code == "invalid_period"


@pytest.mark.asyncio
async def test_wo_ac_an_unrecognized_statement_names_the_supported_networks(db_session):
    """The parser's fail-closed network detection, surfaced as the sentence it
    wrote rather than flattened into a generic refusal — an operator whose file
    was rejected needs to know which networks DO work."""
    org = await _org(db_session)
    with pytest.raises(ValidationError) as exc:
        await estimate_svc.estimate(
            db_session,
            org.id,
            filename="mystery.csv",
            content=b"NOT A STATEMENT\na,b,c\n",
            period=PERIOD_Q,
        )
    assert exc.value.code == "unrecognized_statement"
    assert "Supported networks" in str(exc.value)


@pytest.mark.asyncio
async def test_wo_ac_the_module_gate_fails_closed(db_session):
    """The WO-49 convention: no transport service entry point trusts its caller
    to have checked the entitlement (ADR-P3 rule 3)."""
    org = await make_org(db_session)
    await db_session.commit()
    with pytest.raises(PermissionError) as exc:
        await estimate_svc.estimate(
            db_session,
            org.id,
            filename="s.csv",
            content=_statement([_row(country="BE", net="1.00", vat="0.21")]),
            period=PERIOD_Q,
        )
    assert exc.value.code == "module_not_enabled"
