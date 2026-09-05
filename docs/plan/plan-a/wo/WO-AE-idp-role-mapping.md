# WO-AE — IdP role mapping for the four business roles: one role vocabulary

**Shipped 2026-09-05.** No migration.

## The gap, as found in the tree

The premise in the backlog was "SSO role mappings are never applied". That was
wrong — `oidc.role_from_groups` runs on every login (`oidc.py:303`) — and the
first grep had simply searched the wrong filenames. The real gap was narrower
and worse, because it was silent:

| Where | What it said | What it did |
|---|---|---|
| `roles.ASSIGNABLE_ROLES` | eight roles, the vocabulary a member may hold | — |
| `oidc._ASSIGNABLE` | `(user_free, user, admin)` — written before A1.5 made the four business roles assignable, never widened | a group mapped to `finance_manager`, `accountant`, `approver` or `auditor` hit `continue`; the user landed on the JIT default; **the admin who saved the mapping saw no error** |
| `SsoConnectionUpdate.default_role` | regex `^(user_free|user|admin)$` | the four could not be the JIT default either; the refusal was a pattern string |
| Settings screen | default-role select offering `user` / `processor` / `admin` | `processor` is not a role — the screen offered a value the API refused, and there was **no group → role mapping editor at all**: the mapping was configurable only over the API (the WO-U shape) |
| `ROLE_RANK` | the four business roles rank `1`, level with `user` | "highest wins" was a tie, and `role_from_groups` returned whichever key the JSON happened to list first — not a rule anyone can state |

Three vocabularies drifted apart because each was a restatement of a list
that had one owner. The fix is not "widen the tuple"; it is to stop
restating.

## What shipped

- **One vocabulary.** `roles.IDP_ASSIGNABLE_ROLES` is `ASSIGNABLE_ROLES` minus
  `owner`, DERIVED beside the list it derives from. `oidc._ASSIGNABLE` *is*
  that tuple; the schema validates against it; the route serves it. A role
  added to `ASSIGNABLE_ROLES` is IdP-assignable the same day, and
  `test_wo_ae_the_two_role_vocabularies_cannot_drift` pins the derivation.
- **`owner` stays out, on every path.** Mapped: `role_from_groups` still
  returns `None` for it. JIT default: a stored `default_role="owner"` — now
  unreachable through the schema, possible in a row written before it — falls
  back to `user`. SCIM default: the same. An identity provider never grants the
  founder's role, and there are now tests for each door it could come through.
- **A validator, not a regex.** `default_role` and every value in
  `role_mappings` must be in the vocabulary, refused with a sentence that names
  it (`'processor' is not a role an identity provider may assign; must be one
  of: user_free, user, admin, finance_manager, accountant, approver, auditor`)
  and, for a mapping, names the group. Refusing at save time replaces the
  worst failure mode there was: accepted, then ignored at login, with the
  admin told it worked.
- **A stated tie-break.** Equal rank → later in `ASSIGNABLE_ROLES` wins. So a
  business role beats plain `user`, and the business roles order among
  themselves as `authz.Role` declares them (`finance_manager < accountant <
  approver < auditor` in tie-break, all below `admin`). Both orderings of the
  same JSON now agree, and the manual says the rule in one sentence.
- **The server serves the vocabulary.** `SsoConnectionOut.assignable_roles`
  and `GET /sso/assignable-roles` (for the moment before the first save, when
  the connection is `null`). The settings screen's selects render that list
  and nothing else — an e2e spec serves a shorter list and asserts the missing
  role is absent, which is the only way to prove a select is not a page
  constant.
- **A mapping editor.** Group → role rows on the SSO panel, with Add / Remove,
  the `groups_claim` field and the role-sync toggle — the three things that
  were configurable only over the API. Untouched rows are not re-sent (the
  PUT is `exclude_none`; a missing key leaves the mapping alone); touched rows
  are sent whole, so a removed group stops mapping.

## Certification

- Backend: 11 new tests in `test_sso_role_mapping.py` — the four business roles
  map; owner does not, via any of three doors; the derivation is pinned; the
  tie-break is deterministic in both orderings and rank still wins; the schema
  accepts `auditor` and refuses `processor`/`owner`/a typo for the default and
  per-group for a mapping; the vocabulary is served on the connection and
  standalone, and the two agree.
- Browser: `sso-role-mapping.spec.ts` (5) — the select equals the served list
  (auditor in; owner and processor out; human labels, stored values); a shorter
  served list removes the option; a saved mapping renders as rows and adding
  one PUTs the whole mapping; removing a row PUTs without it; an untouched
  mapping is not re-sent.
- Seeded violations: (a) restore the three-member tuple → the business-role
  test and the drift pin fail; (b) drop `_TIE_ORDER` from the comparison → the
  tie test fails on the reversed JSON; (c) remove the `role_mappings`
  validator → the save-time refusal test fails; (d) hard-code the select's
  options in the page → the "not a page constant" spec fails. Each restored by
  inverse edit.

## Deliberately not done

- `ROLE_RANK` itself is unchanged. Ranking the business roles above `user`
  would change `is_admin_or_above` semantics nothing here asked for; the
  tie-break is a separate, stated rule and lives only in the mapping resolver.
- No migration and no backfill of existing `role_mappings` rows: a row that
  carries `owner` or a typo keeps it and keeps being ignored at login, exactly
  as before; the next save through the screen refuses it with the sentence.
- SAML stays scaffolded; the mapping editor speaks OIDC's `groups` claim.
