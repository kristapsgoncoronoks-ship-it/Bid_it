# PART B — THE FIRST TEN WORK ORDERS

Execute in order. Each assumes Part A is prepended. Each is self-contained enough to run in a fresh session.

**Dependency summary**

| WO | Board ids | Effort | Depends on | May run in parallel with |
|---|---|---|---|---|
| WO-1 | A1.1→A1.2→A1.3 | 13–18d | — | WO-4, WO-5, WO-6, WO-7, WO-8, WO-10 |
| WO-2 | A2.1+A2.2+A2.3 | 7–9d | WO-1 | WO-3 |
| WO-3 | A3.1 | 1–2d | WO-1 | WO-2 |
| WO-4 | B1.1+B1.2+B1.4 | 3–5d | — | anything |
| WO-5 | B1.6 | 1–2d | — | anything |
| WO-6 | G0.1+G0.2 | 2–4d | — | anything |
| WO-7 | C1.1 | 3–5d | — | anything |
| WO-8 | C1.2+C1.3+C1.4 | 7–10d | — | anything |
| WO-9 | D1.1+D1.2+D1.3 | 7–9d | WO-1, WO-2 | — |
| WO-10 | J1.1+J1.2+B1.3 | 5–7d | all of M0 for the exit gate | — |

**Parallel non-engineering ask, raise in week 1 (H1.1):** Stripe live credentials + per-plan Price IDs + Billing Meter `event_name`, plus the EU VAT seller-of-record decision; a dev IdP (Okta/Entra/Keycloak) for SAML; a real chart of accounts for DATEV/SAF-T. These have legal/finance lead times of weeks and sit on the critical path to revenue. Track them in `docs/DECISIONS-NEEDED.md`.

---
