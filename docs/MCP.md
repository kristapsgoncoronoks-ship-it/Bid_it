# MCP server — read-only AI access to the platform

The Fleet Fuel & VAT Refund System ships an **MCP (Model Context Protocol) server**
that exposes the platform's business data as **read-only tools** an AI agent (Claude
Desktop, the claude.ai connectors, or any MCP client) can call. It is modeled on Box's
MCP server: the agent asks questions in natural language and the model calls the
matching tool to fetch concise, structured data.

## What it is — and the security posture

- **Read-only (v1).** There are NO write or action tools. Every tool reads the
  engine-owned product DBs strictly through `dataproduct.connect()` (a `mode=ro`
  handle — a stray write raises) and the app's existing read functions. No tool can
  create, mutate, lock, submit, or pay anything.
- **No bank / secret data, ever.** Per the project AI-privacy rule, NO tool returns an
  IBAN, bank account, SWIFT/BIC, secret, credential, or the claim payout route.
  `list_suppliers` / `list_customers` select only identity columns (code, name,
  country, VAT id, status) and never touch the separate `*_bank_accounts` tables; a
  defense-in-depth filter strips any bank/secret-ish key from every result.
- **Token-gated HTTP.** The network (streamable-HTTP) transport REQUIRES a bearer
  token; an unauthenticated or wrong-token request is rejected `401`. The local stdio
  transport is trusted (Claude Desktop spawns the process locally) and needs no token.
- **Tenant-aware.** The tools call read functions that already honor
  `tenancy.scope_clause()`. With the `multitenant` switch OFF (the default) this is
  inert; ON, a tool only sees the bound tenant's rows (or, under owner scope, the
  audited cross-tenant analytics view). With the switch ON and no principal bound, the
  tools fail CLOSED (return nothing) rather than leak.
- **Never raises.** Every tool returns a JSON-serializable structure on success or
  `{"error": "..."}` on any failure — the agent always gets a clean answer.

> Exposing business data to an AI agent is a deliberate decision: keep it read-only,
> keep bank/secret fields out, and keep the HTTP surface token-gated behind your
> reverse proxy + TLS.

## The tools

| Tool | Purpose | Data source |
|------|---------|-------------|
| `search_documents(query, limit=20)` | Full-text search over the invoice/document corpus | `search.search` (FTS index) |
| `reclaimable_vat(year, period=None, country=None)` | Reclaimable cross-border VAT (refund owed), NET EUR | `transactions` (read-only) |
| `claim_status(year=None)` | Claim status (submitted/approved/paid) + aging + receivable | `vat_refund.recovery_report` |
| `list_claims(year, status=None, country=None)` | Individual claims, filterable | `vat_refund` (filtered) |
| `supplier_benchmark(period, country=None)` | Per-supplier effective diesel NET €/L | `queries.q_benchmark` |
| `period_kpis(period)` | Litres, eff €/L, net, VAT, gross for a period | `queries.q_kpis` |
| `monthly_trend()` | Per-period diesel litres + eff €/L | `queries.q_trend` |
| `list_suppliers()` | Supplier identity (code/name/group/country/status) | `suppliers.db` (safe cols) |
| `list_customers()` | Customer identity (code/name/country/VAT/status) | `customer_master.list_customers` (safe cols) |
| `document_metadata(doc_ref)` | Tags + custom fields for a document ref | `metadata` |
| `overdue_document_requests()` | Open/overdue customer document requests | `customer_master.document_request_board` |

All prices are NET EUR/L, final (VAT excluded, rebates applied).

## Architecture

- **`mcp_tools.py`** — the PLAIN tool functions (the logic). NO `mcp` SDK dependency,
  so the web app and the test-suite import it freely. `mcp_tools.TOOLS` is the
  read-only registry.
- **`mcp_server.py`** — the FastMCP server. It imports the `mcp` SDK ONLY inside its
  factory / `__main__` (never at repo import time), so nothing else in the codebase —
  and no test — needs the SDK installed. It registers each `mcp_tools` function as an
  `@mcp.tool`, using the function's docstring as the tool description MCP surfaces.

## Install

The MCP SDK is an OPTIONAL extra, deliberately kept OUT of the main `requirements.txt`
so the web app / waitress server is not burdened. Install it only on the host that runs
the MCP server:

```bash
pip install -r requirements-mcp.txt
```

## Run

### stdio (local, for Claude Desktop)

```bash
python mcp_server.py
```

No token required — stdio is local/trusted.

### streamable-HTTP (production, behind nginx + TLS)

Set a strong bearer token first, then start the HTTP transport (loopback by default):

```bash
export FFS_MCP_TOKEN="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
python mcp_server.py --http --host 127.0.0.1 --port 8765
```

The server rejects any HTTP request without `Authorization: Bearer <FFS_MCP_TOKEN>`
with `401`. If `FFS_MCP_TOKEN` is unset, the server falls back to validating against
the platform's existing `api_keys` bearer tokens (any active key authenticates) — but
setting a dedicated `FFS_MCP_TOKEN` is recommended.

Put it behind your existing nginx with TLS, e.g.:

```nginx
location /mcp/ {
    proxy_pass http://127.0.0.1:8765/;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $remote_addr;
    proxy_buffering off;     # streamable-HTTP is long-lived
}
```

## Connect Claude Desktop (stdio)

Add to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "fleet-fuel-vat": {
      "command": "python",
      "args": ["/absolute/path/to/fleet_fuel_system/mcp_server.py"]
    }
  }
}
```

## Connect a claude.ai / remote connector (HTTP)

Point the connector at your token-gated HTTPS endpoint and supply the bearer token:

```json
{
  "url": "https://your-host.example.com/mcp/",
  "headers": { "Authorization": "Bearer <FFS_MCP_TOKEN>" }
}
```

## Tests

`tests/test_mcp_tools.py` covers the PLAIN functions only (no `mcp` SDK dependency):
each tool's shape against the demo data, a planted bank value never surfacing,
bad-arg → `{"error": ...}` without raising, the read-only product boundary, and
tenant fail-closed behavior. It also asserts `import mcp_tools` works WITHOUT the SDK
and that the suite import never pulls in `mcp`.

```bash
python -m pytest tests/test_mcp_tools.py -q
```
