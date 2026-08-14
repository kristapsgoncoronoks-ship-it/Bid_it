"""Why the machine picked THAT supplier (H-3) — automation provenance + abstain.

THE GAP THIS CLOSES
-------------------
We already refuse to let a derived value overwrite a human (I-18; `capture_memory`
is read-only by construction). What we lacked is the third leg: **the reason, on
the record, in front of the operator**. Supplier resolution is exact-name and
silent, so both of its outcomes are invisible:

* it matched an existing supplier — the operator cannot see WHY, so they cannot
  tell a correct match from a coincidence;
* it matched nothing and created a new supplier — so a captured name with one
  character wrong quietly forks the master data into two suppliers that will
  never agree on a balance, and nothing on any screen said so.

Trust in automation is bounded by the ability to interrogate one instance of it.

ABSTAIN ON AMBIGUITY
--------------------
The second rule from the harvest: never take first-match. When there is no exact
match but there ARE near ones, this module picks NOTHING and returns the
candidates with a per-candidate reason. Silently creating is a decision, and a
decision made without evidence is the thing to avoid — but so is silently
attaching an invoice to the wrong supplier because their names nearly agree.

The right behaviour is to hand the choice back with the evidence attached, and
that is what `needs_decision` is.

THIS MODULE DECIDES NOTHING AND WRITES NOTHING
----------------------------------------------
It is read-only: no vendor is created, renamed or linked here. The confirm path
keeps its existing behaviour exactly; this explains it, and warns before it. A
provenance module that could itself mutate the master data would be the same
class of hazard it exists to expose.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.vendor import Vendor

# How a match was arrived at. Closed vocabulary — a screen branches on the code,
# a human reads the sentence.
BASIS_EXACT_NAME = "exact_name"
BASIS_NONE = "none"

# Why a NEAR match is near. One reason per candidate, so "did you mean…" can say
# what it noticed rather than presenting an unexplained list.
NEAR_CASE = "case_or_spacing"
NEAR_LEGAL_FORM = "legal_form_suffix"
NEAR_PUNCTUATION = "punctuation_or_accents"

# Common legal-form suffixes across the markets this product serves. Stripping
# them is ONLY ever used to raise a "did you mean" — never to make a match — so a
# missing entry costs a hint, never a wrong link.
_LEGAL_FORMS = {
    "ltd",
    "limited",
    "plc",
    "llc",
    "inc",
    "corp",
    "gmbh",
    "ag",
    "bv",
    "nv",
    "sa",
    "sas",
    "sarl",
    "srl",
    "spa",
    "oy",
    "ab",
    "as",
    "a/s",
    "aps",
    "ou",
    "oü",
    "sia",
    "uab",
    "ao",
    "zoo",
    "sp",
    "kft",
    "doo",
    "dd",
    "eood",
    "ood",
}

_PUNCT = re.compile(r"[^\w\s]", re.UNICODE)
_SPACE = re.compile(r"\s+")


def _fold(name: str) -> str:
    """Case, accent and punctuation-insensitive form. Used ONLY for suggesting,
    never for matching — see the module docstring."""
    decomposed = unicodedata.normalize("NFKD", name)
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return _SPACE.sub(" ", _PUNCT.sub(" ", stripped.casefold())).strip()


def _without_legal_form(folded: str) -> str:
    parts = [p for p in folded.split(" ") if p and p not in _LEGAL_FORMS]
    return " ".join(parts)


def _near_reason(candidate: str, wanted: str) -> str | None:
    """Why these two names are close, or None when they are not close at all.

    Deliberately three explicit, explainable rules rather than an edit-distance
    score. A number ("87% similar") tells the operator nothing they can check; a
    sentence ("same name apart from the legal form") is something they can agree
    or disagree with, which is the whole point of showing it.
    """
    a, b = _fold(candidate), _fold(wanted)
    if not a or not b:
        return None
    if a == b:
        # The stored and captured names differ only in case, spacing,
        # punctuation or accents.
        same_letters = candidate.casefold().replace(" ", "") == wanted.casefold().replace(" ", "")
        return NEAR_CASE if same_letters else NEAR_PUNCTUATION
    if _without_legal_form(a) == _without_legal_form(b) and _without_legal_form(a):
        return NEAR_LEGAL_FORM
    return None


_NEAR_TEXT = {
    NEAR_CASE: "the same name, differing only in capitalisation or spacing",
    NEAR_PUNCTUATION: "the same name, differing only in punctuation or accents",
    NEAR_LEGAL_FORM: "the same name apart from the company-form suffix",
}


@dataclass
class VendorCandidate:
    vendor_id: str
    name: str
    near_kind: str
    reason: str


@dataclass
class VendorResolution:
    """What the machine would do with this captured supplier name, and why."""

    captured_name: str
    basis: str
    reason: str
    vendor_id: str | None = None
    vendor_name: str | None = None
    # True when nothing matched exactly but something nearly did. The caller must
    # NOT pick one — it must ask.
    needs_decision: bool = False
    candidates: list[VendorCandidate] = field(default_factory=list)
    # What happens on confirm if the operator changes nothing. Stated plainly,
    # because "a new supplier will be created" is the consequence they most need
    # to see BEFORE it happens, not after.
    outcome: str = ""


# Bounded so an org with thousands of suppliers cannot turn a review screen into
# a table scan of the whole master. Suggestions are a courtesy; correctness never
# depends on the list being complete, and the cap is stated rather than hidden.
_CANDIDATE_SCAN_LIMIT = 2000
_MAX_CANDIDATES = 5


async def resolve(db: AsyncSession, org_id: str, name: str) -> VendorResolution:
    """Explain how this captured supplier name resolves. Read-only.

    Mirrors `vendors.get_or_create_vendor`'s matching rule EXACTLY — exact
    stripped name — because an explanation that describes a different rule from
    the one that runs is worse than no explanation.
    """
    captured = (name or "").strip()
    if not captured:
        return VendorResolution(
            captured_name="",
            basis=BASIS_NONE,
            reason="No supplier name was captured from the document.",
            outcome="You will need to choose or type the supplier before this can be saved.",
        )

    exact = await db.scalar(select(Vendor).where(Vendor.org_id == org_id, Vendor.name == captured))
    if exact is not None:
        return VendorResolution(
            captured_name=captured,
            basis=BASIS_EXACT_NAME,
            reason=(
                f"The name on the document, “{captured}”, is exactly the name of a "
                "supplier already in your list."
            ),
            vendor_id=exact.id,
            vendor_name=exact.name,
            outcome="This invoice will be filed under that existing supplier.",
        )

    rows = list(
        await db.scalars(
            select(Vendor)
            .where(Vendor.org_id == org_id)
            .order_by(Vendor.name)
            .limit(_CANDIDATE_SCAN_LIMIT)
        )
    )
    candidates: list[VendorCandidate] = []
    for v in rows:
        kind = _near_reason(v.name, captured)
        if kind is not None:
            candidates.append(
                VendorCandidate(
                    vendor_id=v.id,
                    name=v.name,
                    near_kind=kind,
                    reason=f"“{v.name}” is {_NEAR_TEXT[kind]}.",
                )
            )
        if len(candidates) >= _MAX_CANDIDATES:
            break

    if candidates:
        # Abstain. Two suppliers whose names nearly agree are the classic way
        # master data forks, and picking the first is how a machine turns a
        # typo into a permanent second supplier.
        return VendorResolution(
            captured_name=captured,
            basis=BASIS_NONE,
            reason=(
                f"No supplier is named exactly “{captured}”, but "
                f"{'one is' if len(candidates) == 1 else f'{len(candidates)} are'} very close."
            ),
            needs_decision=True,
            candidates=candidates,
            outcome=(
                "Nothing has been chosen for you. If you confirm without picking one, "
                f"a NEW supplier called “{captured}” will be created alongside the "
                "existing one."
            ),
        )

    return VendorResolution(
        captured_name=captured,
        basis=BASIS_NONE,
        reason=f"No supplier named “{captured}” is in your list, and none is close to it.",
        outcome=f"A new supplier called “{captured}” will be created when you confirm.",
    )


def audit_meta(res: VendorResolution) -> dict:
    """The provenance to put on the permanent record at confirm.

    The audit chain is where "why did the machine do this" belongs: it is
    immutable, hash-chained and already read when someone asks that question
    months later. Storing the sentence on the invoice instead would go stale the
    moment a supplier is renamed, and would then be a confident false account of
    history — worse than none.
    """
    return {
        "captured_name": res.captured_name,
        "basis": res.basis,
        "matched_vendor_id": res.vendor_id,
        "needs_decision": res.needs_decision,
        "near_candidates": [c.name for c in res.candidates],
    }
