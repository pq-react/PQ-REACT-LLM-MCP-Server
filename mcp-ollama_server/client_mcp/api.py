import json
import os
import sys
import traceback
from contextlib import asynccontextmanager
from typing import Optional
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel
sys.path.insert(0, os.path.dirname(__file__))
from client import MCPClient
from agents import SPECIALISTS, route as route_to_specialist


# ---------------- per-specialist Ollama backend resolution ----------------
#
# Two complementary env channels, both optional. Resolution precedence per
# specialist label:
#   1. SPECIALIST_<LABEL>_OLLAMA_HOST   (single-knob override, easiest)
#   2. OLLAMA_BACKENDS[label]           (JSON map, easiest when configuring
#                                        many specialists at once)
#   3. None  → MCPClient falls through to the OLLAMA_HOST env var, which
#              the ollama library reads natively (the legacy single-server
#              behaviour, unchanged for callers that don't set anything).
#
# Example deployment knob (run the regulation specialist on the Mac mini
# while keeping the heavy performance specialist on the local L4 GPU):
#
#   OLLAMA_BACKENDS='{"regulation":"http://mac-llm-host:11434",
#                     "qujata":"http://mac-llm-host:11434"}'
#
# OLLAMA_BACKENDS is parsed once at import time so a malformed JSON value
# fails loudly instead of silently disabling routing.
def _parse_ollama_backends() -> dict:
    raw = os.getenv("OLLAMA_BACKENDS", "").strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except Exception as e:
        raise RuntimeError(
            f"OLLAMA_BACKENDS is set but is not valid JSON: {e}"
        ) from e
    if not isinstance(parsed, dict):
        raise RuntimeError(
            "OLLAMA_BACKENDS must be a JSON object mapping specialist "
            "label → ollama URL (e.g. {\"regulation\": \"http://...\"})"
        )
    return {str(k): str(v) for k, v in parsed.items() if v}


OLLAMA_BACKENDS: dict[str, str] = _parse_ollama_backends()


def _resolve_specialist_host(label: str) -> Optional[str]:
    """Return the Ollama base URL this specialist should hit, or None to
    fall back to the OLLAMA_HOST env (i.e. the legacy single-server path)."""
    per_label = os.getenv(f"SPECIALIST_{label.upper()}_OLLAMA_HOST")
    if per_label:
        return per_label
    return OLLAMA_BACKENDS.get(label)

mcp_client: Optional[MCPClient] = None
is_connected: bool = False
connected_server: Optional[str] = None

# Specialist clients — built lazily on first orchestrator call. Each
# specialist gets its own MCPClient instance pointing at the same MCP
# server, with a distinct tool_filter + system_prompt_override + optional
# output_post_processor + optional OLLAMA_MODEL.
specialist_clients: dict[str, MCPClient] = {}

# Routing flip — when true, the public /chat endpoint forwards through
# the multi-agent orchestrator (with built-in fallback to the monolithic
# agent if a specialist fails). When false, /chat behaves as it did
# before commit 60671e4 — straight through the monolithic MCPClient.
# Default true because the QA-battery measurements
# (PQREACT_AB_PROMPT_AGENTS_EXPERIMENT.md) showed the orchestrator path
# is strictly equal-or-better on every metric: Q9 51.7→79.3, Q1 84→90,
# Q2 74→80, latency p50 cut in half, errors stay at 0%. Set to false
# in the unit file to roll back without redeploying code.
MULTI_AGENT_DEFAULT = os.getenv("MULTI_AGENT_DEFAULT", "true").lower() == "true"


async def _try_autoconnect(url: str) -> bool:
    """Try once to (re)connect mcp_client to `url`. Returns True on success.
    Used by both the lifespan retry loop and the per-/chat lazy reconnect.
    Safe to call when already connected: tears down the old client first."""
    global mcp_client, is_connected, connected_server
    try:
        if mcp_client:
            try: await mcp_client.cleanup()
            except Exception: pass
        mcp_client = MCPClient()
        await mcp_client.connect_to_server(url)
        is_connected = True
        connected_server = url
        return True
    except Exception as e:
        # Surface the inner cause cleanly — the SSE TaskGroup wraps the
        # real httpx.ConnectError inside an ExceptionGroup which makes
        # the journal log ~50 lines of traceback. Compact to one line.
        cause = e
        while getattr(cause, "__cause__", None) is not None:
            cause = cause.__cause__
        if hasattr(e, "exceptions") and e.exceptions:
            cause = e.exceptions[0]
            while getattr(cause, "__cause__", None) is not None:
                cause = cause.__cause__
        print(
            f"[autoconnect] {url} failed: {type(cause).__name__}: {cause}",
            flush=True,
        )
        mcp_client = None
        is_connected = False
        return False


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Auto-connect to MCP_SERVER_URL with retry+backoff.

    Discovered 2026-05-19: a single failed attempt at startup left the
    chat permanently disconnected (no auto-recovery; /chat returned 400
    forever) when the MCP server was briefly unreachable during the
    uvicorn boot window. Fix:
      - retry up to MCP_AUTOCONNECT_ATTEMPTS (default 8) with
        exponential backoff (1, 2, 4, 8, … capped at 30 s)
      - the /chat handler also lazily reconnects on its next call if
        the connection has dropped (see _ensure_connected below)
    """
    autoconnect_url = os.getenv("MCP_SERVER_URL")
    if autoconnect_url:
        attempts = max(1, int(os.getenv("MCP_AUTOCONNECT_ATTEMPTS", "8")))
        import asyncio as _asyncio
        for i in range(attempts):
            ok = await _try_autoconnect(autoconnect_url)
            if ok:
                print(f"[lifespan] auto-connected to {autoconnect_url} "
                      f"(attempt {i + 1}/{attempts})", flush=True)
                break
            if i < attempts - 1:
                delay = min(30, 1 << i)   # 1, 2, 4, 8, 16, 30, 30, 30
                print(f"[lifespan] retrying in {delay}s "
                      f"({i + 1}/{attempts} attempts used)", flush=True)
                await _asyncio.sleep(delay)
        else:
            print(f"[lifespan] giving up after {attempts} attempts; "
                  f"/chat will lazy-reconnect on first request",
                  flush=True)
    yield
    if mcp_client:
        await mcp_client.cleanup()


async def _ensure_connected() -> None:
    """Lazy reconnect — called by /chat handlers before they hit MCP.

    When the chat client started during a flaky window and gave up on
    auto-connect, the next user query would return 400 forever until
    someone manually issued /connect. Instead, try once to reconnect
    using the env-configured URL; only fail if that attempt also fails.
    Per-call cost is one TCP connect when already connected (the
    `is_connected` short-circuit), so the warm path is unaffected.
    """
    if is_connected and mcp_client:
        return
    url = os.getenv("MCP_SERVER_URL")
    if not url:
        raise HTTPException(
            status_code=400,
            detail="Not connected and MCP_SERVER_URL is not set on this server.",
        )
    if not await _try_autoconnect(url):
        raise HTTPException(
            status_code=503,
            detail=f"MCP server at {url} is unreachable; lazy reconnect failed. "
                   f"Will retry on the next /chat call.",
        )


app = FastAPI(title="PQC by pq-react", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

class ConnectRequest(BaseModel):
    server_path: str

class ChatTurn(BaseModel):
    role: str        # 'user' | 'assistant'
    content: str


class ChatRequest(BaseModel):
    query: str
    # Prior turns the caller wants the agent to be aware of. Optional —
    # the original /chat shape (just {query}) keeps working unchanged.
    # Caller (the SPA) is responsible for trimming to a reasonable length
    # before sending; the orchestrator caps it again server-side.
    history: list[ChatTurn] | None = None

@app.get("/", response_class=HTMLResponse)
async def root():
    path = os.path.join(os.path.dirname(__file__), "index.html")
    with open(path, encoding="utf-8") as f:
        html = f.read()
    # Inject MCP_SERVER_URL as a meta tag so script.js can read it
    # without a hardcoded fallback. Empty string ⇒ same-origin /sse.
    server_url = os.environ.get("MCP_SERVER_URL", "")
    if server_url:
        meta = f'<meta name="mcp-server-url" content="{server_url}">'
        html = html.replace("</head>", f"  {meta}\n</head>", 1)
    return html

@app.get("/style.css")
async def stylesheet():
    return FileResponse(
        os.path.join(os.path.dirname(__file__), "style.css"),
        media_type="text/css",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )

@app.get("/script.js")
async def javascript():
    return FileResponse(
        os.path.join(os.path.dirname(__file__), "script.js"),
        media_type="application/javascript",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )

@app.get("/pq-console.css")
async def pq_console_css():
    """Shared PQ-REACT operator-console skin (loaded after style.css)."""
    return FileResponse(
        os.path.join(os.path.dirname(__file__), "pq-console.css"),
        media_type="text/css",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )


# ── Static image assets (favicon + horizontal PQ-REACT logo) ─────────────
# Kept in client_mcp/img/ next to index.html. Two routes so legacy
# bookmarks of /favicon.ico still work *and* the page can refer to
# /img/<name> directly. Both serve PNG; modern browsers accept PNG
# content under a .ico filename.
_IMG_DIR = os.path.join(os.path.dirname(__file__), "img")
_IMG_ALLOWED = {".png", ".svg", ".jpg", ".jpeg", ".webp", ".ico"}


@app.get("/favicon.ico")
async def favicon():
    p = os.path.join(_IMG_DIR, "pqreact-favicon.png")
    if os.path.exists(p):
        return FileResponse(p, media_type="image/png")
    return JSONResponse({"ok": True}, status_code=204)


@app.get("/img/{name}")
async def image(name: str):
    # Defence-in-depth: refuse anything that looks like a path-traversal
    # attempt or an extension we don't intend to serve.
    if "/" in name or "\\" in name or name.startswith("."):
        raise HTTPException(400, "invalid image name")
    ext = os.path.splitext(name)[1].lower()
    if ext not in _IMG_ALLOWED:
        raise HTTPException(400, f"unsupported extension '{ext}'")
    p = os.path.join(_IMG_DIR, name)
    if not os.path.exists(p):
        raise HTTPException(404, f"image '{name}' not found")
    media = {".png": "image/png", ".svg": "image/svg+xml",
             ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
             ".webp": "image/webp", ".ico": "image/vnd.microsoft.icon",
             }.get(ext, "application/octet-stream")
    return FileResponse(p, media_type=media)

@app.get("/status")
async def status():
    return {
        "connected": is_connected,
        "server": connected_server,
        "model": mcp_client.ollama_model if mcp_client else None,
        # New fields — additive, legacy parsers keep working.
        "chat_mode": "orchestrator" if MULTI_AGENT_DEFAULT else "monolith",
        "multi_agent_default": MULTI_AGENT_DEFAULT,
        "warmed_specialists": sorted(specialist_clients.keys()),
        "endpoints": {
            "default":             "/chat",
            "explicit_monolith":   "/chat/v1/monolith",
            "explicit_orchestrator": "/agents/v2/chat",
            "agent_status":        "/agents/v2/status",
        },
    }

@app.post("/connect")
async def connect(req: ConnectRequest):
    global mcp_client, is_connected, connected_server

    if mcp_client:
        await mcp_client.cleanup()
        mcp_client = None
        is_connected = False
        connected_server = None

    mcp_client = MCPClient()
    try:
        await mcp_client.connect_to_server(req.server_path)
        is_connected = True
        connected_server = req.server_path

        response = await mcp_client.session.list_tools()
        tools = [{"name": t.name, "description": t.description} for t in response.tools]

        return {"status": "connected", "server": req.server_path, "tools": tools}

    except Exception as e:
        mcp_client = None
        is_connected = False
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=traceback.format_exc())

def _history_as_dicts(history) -> list[dict]:
    """Normalise the optional chat history coming off the wire to plain
    dicts MCPClient.process_query understands. Pydantic models go in,
    plain {role, content} dicts come out. Robust to None / missing fields."""
    if not history:
        return []
    out: list[dict] = []
    for h in history:
        # Either a Pydantic ChatTurn or a raw dict — handle both.
        role = getattr(h, "role", None) if not isinstance(h, dict) else h.get("role")
        content = getattr(h, "content", None) if not isinstance(h, dict) else h.get("content")
        if role in ("user", "assistant") and isinstance(content, str) and content:
            out.append({"role": role, "content": content})
    return out


async def _monolith_chat(query: str, history: list | None = None) -> dict:
    """Run a query through the monolithic MCPClient (the original
    pre-multi-agent path). Returned dict matches the legacy /chat
    response shape: {response, tool_calls}. Used by:
      - the public /chat endpoint when MULTI_AGENT_DEFAULT is false
      - /chat/v1/monolith for explicit fallback / A/B testing
      - the orchestrator's exception handler (specialist crash → monolith)
    """
    await _ensure_connected()
    response = await mcp_client.process_query(query, history=_history_as_dicts(history))
    tool_calls = getattr(mcp_client, "_last_tool_calls", []) or []
    return {"response": response, "tool_calls": tool_calls}


async def _orchestrator_chat(query: str, history: list | None = None) -> dict:
    """Run a query through the multi-agent orchestrator. Routes to one
    specialist via the keyword classifier; on specialist exception,
    falls back to _monolith_chat. Returned dict adds `agent_used` and
    `categories_matched` to the legacy {response, tool_calls} shape —
    additive, so legacy parsers (CAM chat_advisor, qa_battery.sh, the
    SPA) keep working unchanged.

    The classifier reads ONLY the new query (not the history), because
    routing should reflect the user's current intent. The history is
    passed to the specialist so the model can resolve back-references
    like 'list 10 of them' from the prior turn."""
    await _ensure_connected()
    cats = MCPClient._keyword_categories(query)
    label = route_to_specialist(cats)
    hist = _history_as_dicts(history)
    try:
        sc = await _get_specialist(label)
        resp = await sc.process_query(query, history=hist)
        tool_calls = getattr(sc, "_last_tool_calls", []) or []
        return {
            "response": resp,
            "tool_calls": tool_calls,
            "agent_used": label,
            "categories_matched": cats,
        }
    except Exception as e:
        # Fallback to monolith — never leave the user empty.
        try:
            mono = await _monolith_chat(query, history=hist)
            mono["agent_used"] = f"fallback-monolith (specialist={label} failed: {e})"
            mono["categories_matched"] = cats
            return mono
        except Exception as fallback_e:
            raise HTTPException(500, f"specialist+fallback both failed: {e!r}; {fallback_e!r}")


@app.post("/chat")
async def chat(req: ChatRequest):
    """Default chat endpoint. By default — and per the QA-battery
    measurements — this forwards through the multi-agent orchestrator
    (Q9 51.7→79.3, Q1 84→90, Q2 74→80, latency p50 −50 %, errors 0 %).
    Set MULTI_AGENT_DEFAULT=false on the systemd unit to roll back to
    the monolith path without redeploying code."""
    try:
        if MULTI_AGENT_DEFAULT:
            return await _orchestrator_chat(req.query, history=req.history)
        return await _monolith_chat(req.query, history=req.history)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/chat/v1/monolith")
async def chat_v1_monolith(req: ChatRequest):
    """Explicit pre-orchestrator path. Useful for:
      - A/B testing the orchestrator vs the monolith from a single
        client (CAM, qa_battery.sh, the SPA) by varying the URL only
      - hard-rollback path when /chat itself is misbehaving
      - diagnosing whether a regression is in the orchestrator or in
        the underlying MCPClient
    """
    try:
        return await _monolith_chat(req.query, history=req.history)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


async def _get_specialist(label: str) -> MCPClient:
    """Lazily build (and cache) a specialist MCPClient. Each specialist
    runs in the same process as the monolithic /chat client, sharing the
    same SSE connection origin to the MCP server but with its own
    tool_filter + system_prompt_override + optional model override."""
    if label in specialist_clients:
        return specialist_clients[label]
    if label not in SPECIALISTS:
        raise HTTPException(400, f"unknown specialist '{label}'")
    spec = SPECIALISTS[label]
    # Per-specialist model override (e.g. SPECIALIST_REGULATION_MODEL).
    env_model = os.getenv(f"SPECIALIST_{label.upper()}_MODEL")
    # Per-specialist Ollama backend override — see _resolve_specialist_host
    # for the precedence rules. None means "fall back to OLLAMA_HOST env".
    env_host = _resolve_specialist_host(label)
    sc = MCPClient(
        ollama_model=env_model,
        ollama_host=env_host,
        tool_filter=spec["tools"],
        system_prompt_override=spec["prompt"],
        agent_label=label,
        output_post_processor=spec.get("post_processor"),
    )
    if not connected_server:
        raise HTTPException(400, "Not connected to any MCP server.")
    await sc.connect_to_server(connected_server)
    specialist_clients[label] = sc
    return sc


@app.post("/agents/v2/chat")
async def agents_v2_chat(req: ChatRequest):
    """Explicit multi-agent endpoint. Same as /chat when
    MULTI_AGENT_DEFAULT is true (the default), but kept as a stable URL
    for callers that always want the orchestrator path regardless of the
    /chat default. Implementation reuses _orchestrator_chat so the
    routing/fallback logic is defined in exactly one place."""
    try:
        return await _orchestrator_chat(req.query, history=req.history)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/agents/v2/status")
async def agents_v2_status():
    """Per-specialist warm/cold + tool-filter + backend summary for
    observability. The `host` field shows which Ollama server this
    specialist will (or did) call; `null` means it inherits the
    process-wide OLLAMA_HOST env."""
    return {
        "specialists": [
            {
                "label": label,
                "warmed": label in specialist_clients,
                "model": (specialist_clients[label].ollama_model
                          if label in specialist_clients
                          else os.getenv(f"SPECIALIST_{label.upper()}_MODEL", os.getenv("OLLAMA_MODEL"))),
                "host": (specialist_clients[label].ollama_host
                         if label in specialist_clients
                         else _resolve_specialist_host(label)),
                "tool_count": len(spec["tools"]),
                "categories": sorted(spec["categories"]),
                "post_processor": (
                    spec.get("post_processor").__name__
                    if spec.get("post_processor") else None
                ),
            }
            for label, spec in SPECIALISTS.items()
        ],
        "categories_priority": ["REGULATIONS", "INVENTORY", "DEVICES", "QUJATA", "KATANA", "DB"],
        "ollama_backends_configured": sorted(OLLAMA_BACKENDS.keys()),
        "default_ollama_host": os.getenv("OLLAMA_HOST") or None,
    }


@app.post("/disconnect")
async def disconnect():
    global mcp_client, is_connected, connected_server, specialist_clients

    if mcp_client:
        await mcp_client.cleanup()
        mcp_client = None
    # Clean up any warmed specialist clients on disconnect too.
    for sc in specialist_clients.values():
        try:
            await sc.cleanup()
        except Exception:
            pass
    specialist_clients = {}
    is_connected = False
    connected_server = None

    return {"status": "disconnected"}
