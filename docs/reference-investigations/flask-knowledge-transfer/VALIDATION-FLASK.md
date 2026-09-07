# Flask cycle validation

## Pinned sources
- Flask: `d318b683471101618febed18996405ad26462110`
- InvoiceIQ: `525470a154d9f2b85941daca783f0a3499ec6526`

## Executed in this session

### Patch construction
- proposed Flask-cycle Python snippets: **syntax PASS**
- `flask-jwt-signing-key-rotation.patch`: `git apply --check` **PASS**
  against synthetic preimages built from exact inspected GitHub source contexts.
- `flask-route-topology-guard.patch`: `git apply --check` **PASS** as a new file.

These are patch-format/applicability checks, NOT a claim that the patch was
applied to the real repository.

### Route topology semantic validation
Executed with the installed FastAPI runtime:

Bad fixture:
- `/items/{item_id}` registered before `/items/special`
- detector result:
  `GET '/items/special' shadowed by '/items/{item_id}'`

Good fixture:
- `/items/special` before `/items/{item_id}`
- detector result: empty

Result: **PASS**.

### JWT semantic runtime
The working Python environment does not have `python-jose` installed, so the
real InvoiceIQ JWT implementation could not be executed here. The proposed
fallback loop was syntax checked only.

The design is nonetheless bounded:
- only current + configured fallback keys are tried;
- fallbacks are verify-only;
- existing jti/server-session gate remains downstream;
- OIDC IdP JWT verification is untouched.

## GitHub write attempt

Attempted:
`chatgpt/flask-reference-learnings`

Base:
`525470a154d9f2b85941daca783f0a3499ec6526`

Result:
`403 Resource not accessible by integration`

GitHub repository permissions reported `pull=true`, `push=false`.

## Not executed

- real patch application in InvoiceIQ Git tree;
- InvoiceIQ pytest;
- python-jose access-token/state tests;
- Ruff;
- mypy;
- Postgres CI;
- frontend/e2e;
- full CI/deploy;
- DeprecationWarning full-suite inventory.

## Definition of Done

**NOT SATISFIED.** The analysis and patch design are complete, but repository
implementation and real CI remain blocked by GitHub write permission.
