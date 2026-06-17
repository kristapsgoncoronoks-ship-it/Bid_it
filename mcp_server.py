"""
MCP SERVER — exposes the platform's READ-ONLY data tools (mcp_tools.TOOLS) to an AI
agent (Claude Desktop / claude.ai connectors) over the Model Context Protocol, via the
official `mcp` Python SDK (FastMCP). Modeled on Box's MCP server.

v1 IS READ-ONLY. Only the read tools in `mcp_tools` are registered; there is NO write
or action tool. Every tool reads the engine-owned product DBs strictly read-only
(dataproduct), never returns IBAN/bank/secret fields, and is tenant-aware.

SDK IMPORT IS GUARDED. The `mcp` package is imported ONLY inside this module's factory /
`__main__` — never at repo import time. So `import mcp_tools` (and the whole test-suite)
works WITHOUT the SDK installed; only running THIS server needs `pip install -r
requirements-mcp.txt`. Nothing else in the codebase imports `mcp_server`.

TRANSPORTS:
  • stdio (dev / local) — TRUSTED: Claude Desktop spawns this process locally and talks
    over stdin/stdout. No network exposure, so no token is required.
  • streamable-http (prod) — NETWORK exposed, so it REQUIRES a bearer token. The token is
    a configured secret (the `FFS_MCP_TOKEN` env var, or — if unset — any active
    `api_keys` token). An unauthenticated / wrong-token HTTP request is REJECTED 401.

Run:
    python mcp_server.py                 # stdio (default; for Claude Desktop)
    python mcp_server.py --http          # streamable-HTTP on 127.0.0.1:8765
    python mcp_server.py --http --host 0.0.0.0 --port 9000
Set the HTTP token first:
    export FFS_MCP_TOKEN="$(python -c 'import secrets;print(secrets.token_urlsafe(32))')"
"""
import os
import sys

import applog

log = applog.get("mcp_server")

# Default bind for the HTTP transport — loopback only; put it behind nginx + TLS in prod.
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765

# The env var that carries the HTTP bearer token. When set it is the authoritative
# token; when unset we fall back to validating against the existing api_keys store.
TOKEN_ENV = "FFS_MCP_TOKEN"


def _check_token(presented):
    """True iff `presented` is a valid HTTP bearer token. Two sources, in order:
      1. FFS_MCP_TOKEN env var — a constant-time compare against the configured secret.
      2. otherwise, an active api_keys token (reuses the platform's admin-issued bearer
         tokens; any active key authenticates the read-only MCP surface).
    Never raises — an auth-path failure denies (returns False), never crashes the server."""
    presented = (presented or "").strip()
    if not presented:
        return False
    try:
        import secrets
        configured = (os.environ.get(TOKEN_ENV) or "").strip()
        if configured:
            return secrets.compare_digest(presented, configured)
        # No env token configured — accept any active api_keys token.
        import api_keys
        return api_keys.verify(presented) is not None
    except Exception as e:  # noqa: BLE001 - auth must fail CLOSED, never crash
        log.warning("MCP token check failed (denying): %s", e)
        return False


def build_server():
    """Construct the FastMCP server and register every read-only tool from
    `mcp_tools.TOOLS`. Imports the `mcp` SDK HERE (not at module import time) so the rest
    of the repo / the test-suite never needs the SDK. Returns the `FastMCP` instance."""
    from mcp.server.fastmcp import FastMCP   # guarded: only imported when the server runs

    import mcp_tools

    mcp = FastMCP("fleet-fuel-vat")

    # Register each plain tool function as an MCP tool. FastMCP reads the function's
    # name, signature and docstring (which we author for an LLM caller) as the tool spec.
    for fn in mcp_tools.TOOLS:
        mcp.tool(name=fn.__name__, description=(fn.__doc__ or "").strip())(fn)

    log.info("MCP server built with %d read-only tool(s)", len(mcp_tools.TOOLS))
    return mcp


def _bearer_auth_middleware(app):
    """Wrap the streamable-HTTP ASGI app so every request must carry a valid
    `Authorization: Bearer <token>` (see _check_token). A missing/invalid token is
    rejected 401 BEFORE the MCP app sees it. stdio never goes through this — it is
    trusted/local."""
    async def guarded(scope, receive, send):
        if scope["type"] != "http":
            await app(scope, receive, send)
            return
        headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
        auth = headers.get("authorization", "")
        token = auth[7:] if auth.lower().startswith("bearer ") else ""
        if not _check_token(token):
            body = b'{"error": "unauthorized: a valid bearer token is required"}'
            await send({"type": "http.response.start", "status": 401,
                        "headers": [(b"content-type", b"application/json"),
                                    (b"content-length", str(len(body)).encode())]})
            await send({"type": "http.response.body", "body": body})
            return
        await app(scope, receive, send)
    return guarded


def run_http(host=DEFAULT_HOST, port=DEFAULT_PORT):
    """Serve over streamable-HTTP with mandatory bearer-token auth. Requires a token
    source (FFS_MCP_TOKEN or at least one active api_keys token) — refuses to start
    UNAUTHENTICATED so the network surface is never wide open by accident."""
    if not (os.environ.get(TOKEN_ENV) or "").strip():
        # No env token: only proceed if the api_keys store can authenticate. We can't be
        # sure a key exists, so warn LOUD — an HTTP surface must be token-gated.
        log.warning("%s is not set; HTTP auth will fall back to api_keys tokens. "
                    "Set %s to a strong secret for a dedicated MCP token.",
                    TOKEN_ENV, TOKEN_ENV)
    import uvicorn
    mcp = build_server()
    app = mcp.streamable_http_app()
    guarded = _bearer_auth_middleware(app)
    log.info("MCP streamable-HTTP serving on %s:%s (bearer-token gated)", host, port)
    uvicorn.run(guarded, host=host, port=port)


def run_stdio():
    """Serve over stdio (trusted/local). Claude Desktop spawns this process and speaks
    MCP over stdin/stdout; no network, so no token is required."""
    mcp = build_server()
    mcp.run(transport="stdio")


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--http" in argv:
        host = DEFAULT_HOST
        port = DEFAULT_PORT
        if "--host" in argv:
            host = argv[argv.index("--host") + 1]
        if "--port" in argv:
            port = int(argv[argv.index("--port") + 1])
        run_http(host, port)
    else:
        run_stdio()


if __name__ == "__main__":
    main()
