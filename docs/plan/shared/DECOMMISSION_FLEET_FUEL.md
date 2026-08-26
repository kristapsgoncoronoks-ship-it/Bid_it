# PART F — FLEET FUEL DECOMMISSION (RUN ONCE)

> **STATUS: EXECUTED.** The repository was deleted (archive made 2026-07-25;
> deletion owner-confirmed 2026-08-26). F.4 verification re-run 2026-08-26:
> no CI/config/code references remain — documentation references only — and
> the tree + full-history PII scans are clean. The deny-list still awaits the
> owner's `identifiers_for_denylist.txt` from the decommission archive
> (docs/transport/harvest-protocol.md). Kept verbatim below as the historical
> runbook; do not run it again.

> One-time runbook prompt. Execute AFTER WO-6 Step 1 (deny-list) has been built and AFTER
> `docs/plan/*` is committed and pushed to Bid_it. Human-in-the-loop: Phase 3 is irreversible
> and only the repository owner can perform it.

<!-- ═══════════════ COPY FROM HERE: DECOMMISSION ═══════════════ -->

You are decommissioning the retired Fleet Fuel system. The decision is made: the
`kristapsgoncoronoks-ship-it/fleet_fuel_system` repository will be **deleted**, and all future
development happens in Bid_it. Your job is to make that deletion safe, verifiable, and compliant.

## F.0 — Why this is delicate

1. The repo contains **real client PII and business records** (client masters, VAT registrations,
   bank references, committed SQLite databases with transaction history). Deleting it from GitHub
   is a privacy *improvement* — but those business records may be subject to **statutory retention**
   (EU VAT records: 5–10 years depending on member state). Deletion must not destroy the only copy.
2. Git deletion is **effectively irreversible**: GitHub support can restore a deleted private repo
   for a limited window (~90 days); after that, only your own archive exists.
3. The specification harvested from it (`docs/plan/BA_fleet_fuel.md`, R1–R76) is now the sole
   engineering source. The deletion must not orphan any reference to the old repo.

## F.1 — Preconditions (verify, do not assume)

- [ ] `docs/plan/BA_fleet_fuel.md`, `BA_bidit.md`, `ARCH_plan.md`, `PROMPTS.md` are committed AND
      pushed on Bid_it `main` (or an open PR). Run: `git -C /home/user/Bid_it log --oneline -3 -- docs/plan/`.
- [ ] WO-6 Step 1 (salted-hash deny-list) is built, or a documented decision to rely on structural
      patterns only exists in `docs/transport/harvest-protocol.md`.
- [ ] Fleet Fuel `main` (trunk merge commit `5075e08`) is pushed — nothing unmerged on any branch:
      `git -C <fleet_clone> for-each-ref refs/remotes --format='%(refname:short) %(objectname:short)'`
      and confirm every branch tip is an ancestor of `main`.
- [ ] Confirm with the OWNER that no deployed instance of the Flask app serves live clients from
      this repo's clones — **deleting the GitHub repo does not stop a running deployment**, and any
      live VAT-claim operation (30-Sep filing deadline!) must have an owner-approved continuity plan.
      This runbook does NOT decide that; it only refuses to proceed without an explicit answer.

## F.2 — Owner-held archive (before deletion)

From an up-to-date clone:

```bash
git -C <fleet_clone> fetch --all --tags
git -C <fleet_clone> bundle create fleet_fuel_FULL_$(date +%Y%m%d).bundle --all
git bundle verify fleet_fuel_FULL_*.bundle          # must print "is okay"
zip -r fleet_fuel_worktree_$(date +%Y%m%d).zip <fleet_clone> -x '*.git*'
sha256sum fleet_fuel_FULL_*.bundle fleet_fuel_worktree_*.zip > fleet_fuel_archive.SHA256
```

Hand all three files to the business owner for **offline, access-controlled storage** (encrypted
disk / vault). **Never** push the bundle or zip to any git host, cloud drive shared link, or the
Bid_it repo — it contains the PII the quarantine exists to contain. Record WHERE it is stored and
WHO holds it in the owner's records (not in the repo).

## F.3 — Deletion (owner performs)

1. GitHub → `fleet_fuel_system` → Settings → General → Danger Zone → **Delete this repository**
   (type the full name to confirm). Owner-only; 2FA prompt expected.
2. Remove the repo from every automation that references it: CI secrets/deploy keys, Claude
   session sources, scheduled triggers, local `git remote` entries.
3. Delete all working clones EXCEPT the archive from F.2 (`rm -rf <fleet_clone>` on each machine).

## F.4 — Post-deletion verification

- [ ] The repo 404s on GitHub and no longer appears in the session's repo list.
- [ ] `grep -RIn "fleet_fuel_system" /home/user/Bid_it --include='*.yml' --include='*.yaml' --include='*.json' --include='*.toml'`
      returns only documentation references (docs/plan, this file) — no CI/config/code reference.
- [ ] Bid_it CI is green; the PII scan job (WO-6) is a required check.
- [ ] `docs/transport/harvest-protocol.md` records: archive created (date, SHA-256s), repo deleted
      (date), deny-list status, and that `docs/plan/BA_fleet_fuel.md` is the sole surviving spec.

## F.5 — Rollback

Within GitHub's support window (~90 days): contact GitHub support to restore the deleted repo.
After that: restore from the owner's bundle (`git clone fleet_fuel_FULL_<date>.bundle restored/`).
If neither exists, the code is gone — the specification in `docs/plan/BA_fleet_fuel.md` and the
2,422-test behavioural knowledge it encodes are what remain. This is accepted by the decision.

<!-- ═══════════════ COPY TO HERE ═══════════════ -->
