"""CBOM "Security Scanner" — FastAPI front end for a CBOM / crypto-scanner
MCP server (tools: scan_local_path_tool, get_sbom_tool,
analyze_security_tool) with an Ollama-backed Q&A endpoint.

Captured from the PQ-REACT GPU/Ollama testbed VM (~/cbom_mcp/client) on
2026-11-10. Only the client existed there; the scanner MCP server it
talked to (SSE, port 8002) ran on another host and was not available
for capture. Without MCP_SERVER_URL the app tries to spawn
../mcp_server/server.py over stdio, which is likewise not included.

Run:  MCP_SERVER_URL=http://<cbom-mcp-host>:8002/sse \
      uvicorn app:app --host 0.0.0.0 --port 8080
Env:  OLLAMA_HOST (default http://localhost:11434),
      OLLAMA_MODEL (default glm-4.7-flash:q4_K_M), MCP_SERVER_URL.
"""
import json
import os
from contextlib import asynccontextmanager, AsyncExitStack
from typing import Optional

import requests as http_requests
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.sse import sse_client

OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "glm-4.7-flash:q4_K_M")
MCP_SERVER_URL = os.getenv("MCP_SERVER_URL", "")  # e.g. http://<cbom-mcp-host>:8002/sse

SERVER_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "mcp_server"))
SERVER_SCRIPT = os.path.join(SERVER_DIR, "server.py")

mcp_session: Optional[ClientSession] = None
_exit_stack = AsyncExitStack()


@asynccontextmanager
async def lifespan(app: FastAPI):
    global mcp_session, _exit_stack

    if MCP_SERVER_URL:
        transport = await _exit_stack.enter_async_context(sse_client(MCP_SERVER_URL))
        print(f"Connected to remote MCP server: {MCP_SERVER_URL}")
    else:
        server_params = StdioServerParameters(
            command="python3",
            args=[SERVER_SCRIPT],
            env={**os.environ, "PYTHONPATH": SERVER_DIR},
        )
        transport = await _exit_stack.enter_async_context(stdio_client(server_params))
        print(f"Connected to local MCP server: {SERVER_SCRIPT}")

    read, write = transport
    mcp_session = await _exit_stack.enter_async_context(ClientSession(read, write))
    await mcp_session.initialize()

    yield

    await _exit_stack.aclose()


app = FastAPI(title="Security Scanner", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


class PathRequest(BaseModel):
    path: str


class ChatRequest(BaseModel):
    question: str
    context: dict = {}


async def call_tool(name: str, args: dict):
    if not mcp_session:
        raise HTTPException(500, "MCP server not connected")
    result = await mcp_session.call_tool(name, args)
    text = "\n".join(c.text for c in result.content if hasattr(c, "text"))
    if getattr(result, "isError", False):
        raise HTTPException(400, text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        raise HTTPException(500, f"Invalid response from scanner: {text}")


@app.get("/", response_class=HTMLResponse)
async def root():
    html_path = os.path.join(os.path.dirname(__file__), "index.html")
    with open(html_path, encoding="utf-8") as f:
        return f.read()


@app.get("/status")
async def status():
    return {"connected": mcp_session is not None, "model": OLLAMA_MODEL}


@app.post("/scan")
async def scan(req: PathRequest):
    return await call_tool("scan_local_path_tool", {"path": req.path})


@app.post("/sbom")
async def sbom(req: PathRequest):
    return await call_tool("get_sbom_tool", {"path": req.path})


@app.post("/analyze")
async def analyze(req: PathRequest):
    result = await call_tool("analyze_security_tool", {"path": req.path})
    if isinstance(result, dict) and "text" in result:
        return {"analysis": result["text"]}
    return {"analysis": str(result)}


@app.post("/chat")
async def chat(req: ChatRequest):
    ctx = req.context
    summary = ctx.get("summary", {})
    vuln = ctx.get("quantum_vulnerable", [])
    weak = ctx.get("weak_classical", [])
    safe = ctx.get("safe", [])
    pqc = ctx.get("pqc", [])

    context_block = ""
    if summary:
        context_block = f"""
Current scan context:
- Overall status: {summary.get('overall_status', 'unknown')}
- Quantum-vulnerable algorithms ({summary.get('quantum_vulnerable', 0)}): {', '.join(a['name'] for a in vuln) or 'none'}
- Weak classical algorithms ({summary.get('weak_classical', 0)}): {', '.join(a['name'] for a in weak) or 'none'}
- Safe algorithms ({summary.get('safe', 0)}): {', '.join(a['name'] for a in safe) or 'none'}
- Post-quantum algorithms ({summary.get('pqc', 0)}): {', '.join(a['name'] for a in pqc) or 'none'}
"""

    prompt = (
        "You are a software security expert specializing in cryptography and post-quantum security. "
        "Answer the following question concisely and practically.\n"
        f"{context_block}\n"
        f"Question: {req.question}"
    )

    try:
        resp = http_requests.post(
            f"{OLLAMA_HOST}/api/generate",
            json={"model": OLLAMA_MODEL, "prompt": prompt, "stream": False},
            timeout=120,
        )
        resp.raise_for_status()
        return {"response": resp.json().get("response", "No response.")}
    except http_requests.exceptions.ConnectionError:
        raise HTTPException(503, f"Cannot connect to Ollama at {OLLAMA_HOST}. Run: ollama serve")
    except Exception as e:
        raise HTTPException(500, f"Ollama error: {e}")
