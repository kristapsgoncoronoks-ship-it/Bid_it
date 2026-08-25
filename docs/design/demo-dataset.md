# Demo dataset — design brief (research-backed, 2026-08-25)

Verified rules the seed MUST follow (from the 12-agent research run):

**Safe synthetic identities (the compliance part)**
- Emails/domains: only `…@example.com/.net/.org` (IANA-held, RFC 2606/6761;
  mail cannot reach a third party). NEVER invented plausible domains —
  test.com is a real company and a documented breach vector.
- IBANs: Latvian format LV + 2 check digits + 4-char bank code + 13 digits;
  use the fictitious bank code `BANK` (the ISO registry's own LV example
  uses it: LV80BANK0000435195001) with mod-97-valid check digits — passes
  validation, can never route.
- VAT/reg numbers: LV + 11 digits sharing the live company-register space →
  collision risk is real. Use 9-prefixed 11-digit numbers (outside the
  4xxx/5xxx legal-entity ranges) and never call live VIES on demo records.
- Phones: +371 5-series (reserved-for-future-use per the numbering plan =
  currently unassignable), sequential +371 5000 000x.
- Company names: Latvian-flavoured sample names (SIA "Paraugs", SIA
  "Piemērs" = sample/example) — the Microsoft-Contoso convention, localized.

**Tooling**: zero new dependencies. Hand-crafted narrative constants for the
hero entities + the existing `random.Random(fixed_seed)` for filler. (Faker
would be fine — MIT, active, has lv_LV — but a narrative seed doesn't need
it, and the research's own recommendation is hand-crafted heroes + faker
only for bulk, which our existing rng already covers.)

**Demo-design rules (sales-engineering corroborated)**
- Storyline first: hero customers with a legible arc, not feature noise.
- Time-RELATIVE dates (offsets from today at seed time) so it never stales.
- In-progress and imperfect states included: an overdue invoice, a stale
  sent offer (red on the Pipeline), a draft, a dormant customer, a partially
  invoiced project — everything-perfect reads as fake.
- No impossible time series: offer → accept → plan → invoice → payment in
  strictly consistent order.
- Planted analytics: one supplier repeat-buys the same items across months
  with a price rise, so Supplier costs shows real movers.

**Seeding strategy (community consensus)**
- Demo data stays OUT of Alembic migrations (already true here).
- Seed through the SERVICE layer where invariants/audit matter (offers,
  assignments, notes, acceptance) — ORM events don't fire on bulk inserts;
  the existing seed already does this for the AP cycle ("exactly as it
  would be for a live customer").
- Idempotent by skip-if-exists on the demo org's natural key (existing
  pattern: the demo email guard).
- Precedents: Odoo's data-vs-demo manifest split; Contoso Demo Data Tool.

**The storyline** (one demo workspace, industry-neutral):
- 5 customers: 1 prospect (offer sent 20d ago, unanswered → stale/rotting),
  1 active hero "Riverside Office Centre" (full arc: offer viewed→accepted,
  plan, assignments done, acceptance recorded, final invoice issued & paid,
  photos-ready project, shared contract, portal link),
  1 active with work THIS WEEK (confirmed assignments, arrival-notice-ready),
  1 dormant (last invoice 8 months ago), 1 lost (rejected offer + note why).
- Notes on each; lifecycle set accordingly; portal link issued for the hero.
- Supplier side: existing vendors + "repeat-item" purchases with +12% drift.
- Next actions populated naturally (stale offer nudge, overdue chase).
