# Flask → InvoiceIQ knowledge-transfer bundle

Reference:
`pallets/flask@d318b683471101618febed18996405ad26462110`

InvoiceIQ base:
`525470a154d9f2b85941daca783f0a3499ec6526`

## Approved implementation patches
- `patches/flask-jwt-signing-key-rotation.patch`
- `patches/flask-route-topology-guard.patch`

## Planned quality ratchet
- `docs/reference/FLASK-P3-01-deprecation-ratchet.md`

## Main architectural conclusions
1. Separate JWT signing purpose from app/local-KEK secret.
2. Current key signs; bounded previous keys verify routine in-flight artifacts.
3. Server-side jti revocation remains authoritative.
4. Route registration order is a CI-testable structural invariant.
5. Keep FastAPI APIRouters, explicit DI, AppError, server-side sessions.
6. Do not import Flask architecture for its own sake.

## Delivery
Analysis: COMPLETE
Patch preparation: COMPLETE
Isolated validation: COMPLETE
GitHub write: BLOCKED (403)
Actual repo tests: NOT RUN
Definition of Done: NOT SATISFIED
