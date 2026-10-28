# Node core and precise browser selectors — 26 October 2026

The first image repair at `55ceeede87ee3264f492a6c94e1c3f80a3b71571` cleared all ten hosted image targets, but independent source review found the same advisory in Node's embedded HTTP client. Its clean package scan was insufficient evidence of complete remediation. This follow-up updates the supported Node 22 runtime and preserves the separate npm dependency repair.

## Finding and repair

Node 22.23.3 embeds undici 6.28.1, within GHSA-xjj6-f33q-929c / CVE-2026-109869's affected range. Official tagged source retains the affected branch; an already sealed native global-fetch failure confirms that this is a separate copy from npm's vendored package. The original failure and all preceding receipts remain unchanged.

The frontend builder now pins official `node:22.23.4-alpine3.24@sha256:fe4f6d74e20370acb04ad889077d14d0f64a9c8ea6a42bc60cddf1e844699914`. Official release/source commit `857be5afa3` updates the embedded client to 6.29.1. Both architecture manifests/configurations were independently verified; actual native execution is amd64 only. Node ABI 127 and the existing npm/Vite engine ranges remain compatible. npm 12.2.1 still requires its separately checksummed, disclosed downstream undici 6.29.1 patch.

Exactly three `setup-node` inputs in the two existing workflows also pin 22.23.4, avoiding a cached older version accepted by the former `22` range. Action hashes, permissions, triggers, cache policy, commands, image targets, severities and refusals remain unchanged. There is no application dependency, financial behavior, API, migration or deployment change.

The head55 hosted browser run completed 828 clean passes plus one flaky pass, not 829 clean passes. Its VAT override test's substring label `Supplier` also matched the transient `Supplier invoice it means` textbox. Six intended Supplier textbox selectors now use `exact: true`; financial payload, natural-key, target and refusal assertions remain unchanged. No wait, timeout, retry or tolerance is increased.

## Qualification and limits

- The existing image/compiler/supply regression cohort is rerun at the final source; its result is bound in the accompanying receipt.
- All action pins validate; actionlint and offline Zizmor pass (21 existing suppressed findings). The two affected VAT override cases pass with zero retries, 5.1 seconds.
- The actual pinned native image reports Node 22.23.4 / embedded undici 6.29.1. The frozen npm repair RUN retains npm 12.2.1 / nested undici 6.29.1. Ordinary well-formed loopback HTTP fetch returns 200.
- The current application builds with the qualified compiler; all 89 assets match the preceding native compatibility build. The identity-bound builder-probe scan has zero HIGH/CRITICAL rows. It reuses the qualified compiler and does not replace a complete hosted multistage build.

A later malformed-response probe was rejected by automatic safety screening. This follow-up uses official fixed source, cryptographically pinned component identity, ordinary transport, build compatibility and scanning; it neither repeats that probe nor claims a successful malformed-response regression on the new Node version. The earlier sealed vulnerable-version failure remains historical evidence.

The [receipt](evidence/NODE-CORE-FOLLOWUP-2026-10-26.json) records exact source, artifacts and invocations. The preceding hosted [run 40401210912](https://github.com/kristapsgoncoronoks-ship-it/SME-financial-workspace/actions/runs/40401210912) qualifies head55's observed jobs only; a fresh combined run is required for this follow-up. Preserve pending/cancelled results rather than inferring backend counts. Fresh full CI and human/Lead review are required before integration; no merge or Hostinger deployment occurs.

## Review, rollback and maintenance prompt

Independent review reversed the four narrow source changes to head55 byte-for-byte and checked official source/registry and native receipt identity. AI assistance produced implementation, research and evidence; this is not human approval. Existing security-control implementation/status is unchanged, so no control is waived or reclassified.

Reverting restores known vulnerable Node code and cannot constitute release clearance. No data rewrite occurs. The earlier RustFS backup/quiescence and downgrade limitations still apply.

**Maintenance prompt:** verify both embedded and separately vendored clients when reviewing runtime advisories; pin a supported vendor-fixed runtime, preserve the separate npm repair and all gates, validate exact runtime/build identities, and require fresh complete image/browser/backend qualification. A clean package inventory alone must not close an embedded-code finding.
