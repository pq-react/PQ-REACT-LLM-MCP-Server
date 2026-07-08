"""SSE wrapper around mariadb_mcp.

The module instantiates `mcp = FastMCP(...)` at import time and registers
all tools via @mcp.tool() decorators. Importing it captures that instance;
we then run with SSE transport bound to the configured host/port.

Required env: DB_PASSWORD (mariadb_mcp refuses to start without one).
Optional env: MCP_BIND_HOST (default 0.0.0.0), MCP_BIND_PORT (default 5040),
plus QUJATA_DB_* / QUJATA_API_BASE / KATANA_NBI_BASE / KATANA_KPI_BASE
for the cross-stack tools.
"""
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(ROOT, "mcp-ollama_server", "database"))

import mariadb_mcp  # noqa: F401  (import triggers @mcp.tool() registrations)

mcp = mariadb_mcp.mcp

HOST = os.environ.get("MCP_BIND_HOST", "0.0.0.0")
PORT = int(os.environ.get("MCP_BIND_PORT", "5040"))

try:
    mcp.settings.host = HOST
    mcp.settings.port = PORT
except Exception as e:
    print(f"[serve_sse] WARN: could not mutate mcp.settings: {e}", flush=True)

tool_count = "?"
try:
    tool_count = len(mcp._tool_manager._tools)
except Exception:
    pass

print(f"[serve_sse] starting MCP SSE on {HOST}:{PORT} with {tool_count} tools", flush=True)
mcp.run(transport="sse")
