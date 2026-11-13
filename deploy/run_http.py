"""Authenticated Streamable HTTP adapter for the pinned MailBridge upstream."""
from __future__ import annotations

import asyncio
import importlib.util
import json
import os
from pathlib import Path
import secrets
import socket
import sys
from urllib.parse import urlsplit

import uvicorn
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import CallToolResult, TextContent, Tool
from starlette.responses import JSONResponse

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE / "upstream" / "server"))
spec = importlib.util.spec_from_file_location("mailbridge_upstream", BASE / "upstream/server/server.py")
upstream = importlib.util.module_from_spec(spec)
spec.loader.exec_module(upstream)
upstream.i18n.localize_server(upstream.server)

socket.setdefaulttimeout(60)
PORT = int(os.environ.get("MAILBRIDGE_PORT", "18760"))
TOKEN = Path(os.environ["MAILBRIDGE_TOKEN_FILE"]).read_text().strip().encode("ascii")
if len(TOKEN) < 32:
    raise RuntimeError("MailBridge bearer token must contain at least 32 characters")

public_url = urlsplit(os.environ["PUBLIC_URL"])
if public_url.scheme not in ("https", "http") or not public_url.netloc:
    raise RuntimeError("PUBLIC_URL must be an absolute HTTP(S) URL")
public_origin = f"{public_url.scheme}://{public_url.netloc}"

mcp = FastMCP(
    "mailbridge", instructions=upstream.INSTRUCTIONS,
    host="127.0.0.1", port=PORT, streamable_http_path="/mcp",
    stateless_http=True, json_response=True,
    transport_security=TransportSecuritySettings(
        allowed_hosts=["127.0.0.1:*", "localhost:*", public_url.netloc],
        allowed_origins=[public_origin],
    ),
)
mcp._mcp_server.version = upstream.VERSION
lock = asyncio.Lock()


@mcp._mcp_server.list_tools()
async def list_tools() -> list[Tool]:
    return [Tool.model_validate(tool.spec()) for tool in upstream.server._tools.values()]


@mcp._mcp_server.call_tool(validate_input=True)
async def call_tool(name: str, arguments: dict) -> CallToolResult:
    tool = upstream.server._tools.get(name)
    if tool is None:
        raise ValueError(f"Unknown MailBridge tool: {name}")
    # The upstream caches one IMAP connection per account: serialize access.
    async with lock:
        try:
            value = await asyncio.to_thread(tool.handler, **arguments)
        except Exception as exc:
            return CallToolResult(
                content=[TextContent(type="text", text=str(exc))], isError=True,
            )
    return CallToolResult(
        content=[TextContent(type="text", text=json.dumps(value, ensure_ascii=False, default=str))],
        isError=False,
    )


class BearerGate:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            if scope["path"] == "/healthz":
                response = JSONResponse({"status": "ok", "service": "mailbridge", "version": upstream.VERSION})
                await response(scope, receive, send)
                return
            headers = dict(scope.get("headers", []))
            header = headers.get(b"authorization", b"")
            scheme, _, supplied = header.partition(b" ")
            if scheme.lower() != b"bearer" or not secrets.compare_digest(supplied, TOKEN):
                response = JSONResponse(
                    {"error": "unauthorized"}, status_code=401,
                    headers={"WWW-Authenticate": 'Bearer realm="mailbridge"', "Cache-Control": "no-store"},
                )
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)


app = BearerGate(mcp.streamable_http_app())

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=PORT, access_log=False)
