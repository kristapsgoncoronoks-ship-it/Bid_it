# Flask cycle post-implementation review

## FLASK-P2-01 — JWT signing-purpose separation

**Reference Repository Agent:** PASS  
Transfers the key-fallback principle, not Flask's cookie-session implementation.

**InvoiceIQ Architect:** PASS  
Keeps FastAPI, DB sessions and local keyvault. More importantly, separates JWT
purpose from a secret that also has a KEK purpose.

**Security Engineer:** PASS WITH RUNBOOK REQUIREMENT  
Routine fallbacks are safe only for non-compromise rotation. Known-compromised
keys must be retired immediately. Session revocation remains authoritative.

**Adversarial Engineer:** PASS  
Asymmetric JWT/JWKS would add more infrastructure than this product currently
needs. Direct `secret_key` rotation is unsafe when local KEK derives from it.

**QA:** PARTIAL  
Patch/syntax checks passed. `python-jose` and actual InvoiceIQ suite were not
available in the artifact runtime.

**Lead:** APPROVE FOR BRANCH — NOT DONE.

## FLASK-P2-02 — route topology guard

**Reference Repository Agent:** PASS  
Preserves Flask's "setup topology must become deterministic" principle.

**InvoiceIQ Architect:** PASS  
One test, no runtime abstraction.

**Adversarial Engineer:** PASS  
Uses Starlette's compiled route regex rather than a custom parser.

**QA:** PASS ISOLATED / PARTIAL REPO  
Known-bad FastAPI fixture detected; known-good fixture passed. Actual InvoiceIQ
app test not executed.

**Lead:** APPROVE FOR BRANCH — NOT DONE.

## FLASK-P3-01 — warnings ratchet

**Lead:** APPROVE PRINCIPLE, DEFER ENFORCEMENT.  
Inventory required before changing pytest policy.
