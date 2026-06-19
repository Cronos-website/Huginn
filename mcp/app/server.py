"""Huginn MCP server.

Exposes the hub's fleet-management capabilities as MCP tools so an external agent
(e.g. "Hermes") can drive the fleet. Every tool is a thin delegation to the hub's
REST API via :class:`HubClient` — no business logic is duplicated here, and the
MCP server never talks to workers directly.

Run with stdio (default) or as a remote streamable-HTTP server:

    python -m app.server                 # stdio
    HUGINN_MCP_TRANSPORT=streamable-http python -m app.server

When using streamable-http, each agent authenticates with a **per-user MCP
token** (``Authorization: Bearer <token>``). The server validates it against the
hub and forwards the user identity, so every action is attributed to that user.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP

from app.config import get_settings
from app.context import current_client_ip, current_obo_token
from app.hub_client import HubClient, HubError

logger = logging.getLogger("huginn.mcp")

settings = get_settings()
# Stateless HTTP: each request is handled in its own task started from the
# request context, so the per-request on-behalf-of token (set by BearerAuthASGI)
# reliably reaches the tool's hub call. A long-lived session task would instead
# capture the token from session-creation time and reuse it for every later
# request — attributing all calls to whoever opened the session.
mcp = FastMCP(
    "Huginn",
    host=settings.host,
    port=settings.port,
    stateless_http=True,
    json_response=True,
)
hub = HubClient(settings)


# ---------------------------------------------------------------------------
# HTTP bearer-token ASGI middleware (streamable-http only)
# ---------------------------------------------------------------------------

class TokenValidator:
    """Validates a presented per-user MCP token against the hub.

    Calls the hub's ``/api/mcp/whoami`` (authenticated with the service token +
    the presented token as on-behalf-of). Results are cached briefly; the hub
    re-validates on every actual tool call, so this is just a connect-time gate.
    """

    # Cap so a flood of distinct bogus bearers can't grow the cache unbounded.
    _MAX_ENTRIES = 1024

    def __init__(self, ttl: float = 30.0) -> None:
        self._ttl = ttl
        self._cache: dict[str, tuple[bool, float]] = {}

    async def valid(self, presented: str) -> bool:
        if not presented:
            return False
        now = time.monotonic()
        cached = self._cache.get(presented)
        if cached is not None and (now - cached[1]) < self._ttl:
            return cached[0]
        ok = await self._check(presented)
        if len(self._cache) >= self._MAX_ENTRIES:
            self._cache.clear()  # cheap bound; entries are short-lived anyway
        self._cache[presented] = (ok, now)
        return ok

    async def _check(self, presented: str) -> bool:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    f"{settings.hub_url.rstrip('/')}/api/mcp/whoami",
                    headers={
                        "X-MCP-Service-Token": settings.service_token,
                        "X-MCP-On-Behalf-Of": presented,
                    },
                )
            return resp.status_code == 200
        except Exception:  # noqa: BLE001 - hub unreachable → deny
            logger.warning("could not validate MCP token against hub")
            return False


def _client_ip(scope: dict, headers: dict) -> str | None:
    """Originating client IP: the first X-Forwarded-For hop (set by Caddy), else
    the direct peer."""
    xff = headers.get(b"x-forwarded-for", b"").decode()
    if xff:
        first = xff.split(",")[0].strip()
        if first:
            return first
    client = scope.get("client")
    return client[0] if client else None


class BearerAuthASGI:
    """Pure ASGI middleware: require a valid per-user Bearer token.

    Validates ``Authorization: Bearer <token>`` against the hub, stashes the
    token in the request context (so HubClient forwards it as on-behalf-of), then
    passes the request to the inner app. 401 for missing/invalid tokens.
    """

    def __init__(self, app: Any, validator: TokenValidator) -> None:  # noqa: ANN401
        self._app = app
        self._validator = validator

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:  # noqa: ANN401
        if scope["type"] == "http":
            headers = dict(scope.get("headers", []))
            auth = headers.get(b"authorization", b"").decode()
            presented = auth[7:] if auth.startswith("Bearer ") else ""
            if await self._validator.valid(presented):
                ctx_token = current_obo_token.set(presented)
                ctx_ip = current_client_ip.set(_client_ip(scope, headers))
                try:
                    await self._app(scope, receive, send)
                finally:
                    current_obo_token.reset(ctx_token)
                    current_client_ip.reset(ctx_ip)
                return
            # Reject with 401
            await send({
                "type": "http.response.start",
                "status": 401,
                "headers": [[b"content-type", b"application/json"]],
            })
            await send({
                "type": "http.response.body",
                "body": json.dumps({"error": "unauthorized"}).encode(),
            })
        elif scope["type"] == "websocket":
            # Reject WebSocket connections (MCP uses HTTP POST, not WS)
            await send({"type": "websocket.close", "code": 4001})
        else:
            await self._app(scope, receive, send)


# ---------------------------------------------------------------------------
# MCP tools
# ---------------------------------------------------------------------------

async def _safe(coro: Any) -> Any:
    """Await a hub call, converting HubError into a structured tool error."""
    try:
        return await coro
    except HubError as exc:
        return {"error": {"status": exc.status_code, "detail": exc.detail}}


@mcp.tool()
async def list_vms(state: str | None = None) -> Any:
    """List fleet VMs. Optionally filter by state: pending, active, offline, revoked."""
    return await _safe(hub.list_vms(state))


@mcp.tool()
async def get_vm_status(vm_id: str) -> Any:
    """Get a single VM's full status (state, mode, worker version, heartbeat)."""
    return await _safe(hub.get_vm(vm_id))


@mcp.tool()
async def execute_action(
    vm_id: str, action: str, params: dict[str, str] | None = None, wait: bool = False
) -> Any:
    """Run a whitelisted action on a VM.

    Allowed actions: status, metrics, restart_service (param: service),
    list_upgradable_packages, apt_upgrade, update_worker. With ``wait=true`` the
    call blocks briefly for a result; otherwise it returns a task to poll.
    """
    return await _safe(hub.execute_action(vm_id, action, params or {}, wait))


@mcp.tool()
async def execute_command(vm_id: str, command: str, wait: bool = False) -> Any:
    """Run a free-form shell command on a VM.

    Only permitted when the VM is in 'unrestricted' mode (enabled by an admin in
    the dashboard). Subject to the same auth, rate-limit, and audit rules as the
    dashboard.
    """
    return await _safe(hub.execute_command(vm_id, command, wait))


@mcp.tool()
async def trigger_update(vm_id: str) -> Any:
    """Trigger a worker self-update on a VM toward the hub's target version."""
    return await _safe(hub.trigger_update(vm_id))


@mcp.tool()
async def get_task(task_id: str) -> Any:
    """Poll a previously-created task by id for its status and result."""
    return await _safe(hub.get_task(task_id))


@mcp.tool()
async def get_audit_log(
    vm_id: str | None = None, event_type: str | None = None, limit: int = 100
) -> Any:
    """Read recent audit-log entries, optionally filtered by VM or event type."""
    return await _safe(hub.get_audit_log(vm_id, event_type, limit))


def main() -> None:
    if settings.transport == "streamable-http":
        import uvicorn

        raw_app = mcp.streamable_http_app()
        app = BearerAuthASGI(raw_app, TokenValidator())
        logger.info("per-user MCP token auth enabled (validated against hub)")

        uvicorn.run(
            app,
            host=settings.host,
            port=settings.port,
            log_level="info",
        )
    else:
        mcp.run(transport=settings.transport)


if __name__ == "__main__":
    main()
