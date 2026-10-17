# V04 — Narrow source-map-js repair

AI-assisted dependency review and verification. Implemented on the development
branch; pending current-head CI/review, not deployed.

Only source-map-js changes version in package-lock.json: 1.2.1 to 1.2.2 with the
registry integrity digest. npm 11 also removed six optional Rolldown libc metadata
fields during lock normalization; those unrelated metadata edits were restored.
No package.json dependency, Playwright version, bundle budget or baseline changed.

npm ci, full npm audit including dev/transitives (zero findings), frontend lint,
TypeScript/build and bundle gate passed: 435.7 kB raw / 127.6 kB gzip, existing
460/135 limits. The exact lock also built with Node 22 in the Docker build stage.
The local full browser run passed 563 and failed 3 under concurrent build/test
load. All three failing cases subsequently passed in an unchanged targeted run
of their full modules (20 passed, 2 workers). This is not recorded as an entirely
green full browser run; exact-head hosted/full validation remains required. No
assertion, timeout, retry setting or fixture was weakened to obtain that result.

The Node build image's bundled npm/native compiler have separate scanner findings
tracked in V05b; npm's application lock audit does not certify those tool binaries.
No database migration or user contract change. Rollback must use a compatible
patched resolution rather than silently restoring affected 1.2.1 as cleared.
