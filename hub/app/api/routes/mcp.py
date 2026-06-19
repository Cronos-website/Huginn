"""Per-user MCP tokens: each user manages their own; whoami for the MCP server."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import client_ip, get_principal
from app.core import audit
from app.core.principal import Principal
from app.db import get_session
from app.schemas.mcp import McpTokenCreate, McpTokenCreated, McpTokenOut, WhoAmI
from app.services import mcp_tokens as mcp_tokens_service

router = APIRouter(prefix="/api/mcp", tags=["mcp"])


def _require_user(principal: Principal) -> None:
    if principal.user is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "a user session is required")


@router.get("/tokens", response_model=list[McpTokenOut])
async def list_tokens(
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> list[McpTokenOut]:
    _require_user(principal)
    assert principal.user is not None
    return await mcp_tokens_service.list_for_user(session, principal.user.id)  # type: ignore[return-value]


@router.post("/tokens", response_model=McpTokenCreated, status_code=status.HTTP_201_CREATED)
async def create_token(
    body: McpTokenCreate,
    request: Request,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> McpTokenCreated:
    _require_user(principal)
    assert principal.user is not None
    token, plaintext = await mcp_tokens_service.create(session, principal.user, body.name)
    await audit.record(
        session,
        actor_type=principal.actor_type,
        actor_id=principal.actor_id,
        event_type="mcp_token_created",
        detail={"name": token.name},
        source_ip=client_ip(request),
    )
    await session.commit()
    return McpTokenCreated(id=token.id, name=token.name, token=plaintext)


@router.delete("/tokens/{token_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_token(
    token_id: uuid.UUID,
    request: Request,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> None:
    _require_user(principal)
    assert principal.user is not None
    if not await mcp_tokens_service.revoke(session, token_id, principal.user.id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "token not found")
    await audit.record(
        session,
        actor_type=principal.actor_type,
        actor_id=principal.actor_id,
        event_type="mcp_token_revoked",
        detail={"token_id": str(token_id)},
        source_ip=client_ip(request),
    )
    await session.commit()


@router.get("/whoami", response_model=WhoAmI)
async def whoami(principal: Principal = Depends(get_principal)) -> WhoAmI:
    """Resolve the caller (used by the MCP server to validate an on-behalf-of token)."""
    if principal.user is not None:
        return WhoAmI(username=principal.user.username, role=principal.user.role)
    return WhoAmI(username=principal.actor_id, role=principal.role)
