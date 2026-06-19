"""Per-user MCP token management and resolution."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import security
from app.models.mcp_token import McpToken
from app.models.mixins import utcnow
from app.models.user import User
from app.services import users as users_service


async def list_for_user(session: AsyncSession, user_id: uuid.UUID) -> list[McpToken]:
    result = await session.execute(
        select(McpToken)
        .where(McpToken.user_id == user_id, McpToken.revoked_at.is_(None))
        .order_by(McpToken.created_at)
    )
    return list(result.scalars())


async def create(session: AsyncSession, user: User, name: str) -> tuple[McpToken, str]:
    """Create a token for the user; return (row, plaintext) — plaintext shown once."""
    plaintext = security.generate_secret()
    token = McpToken(
        user_id=user.id,
        token_hash=security.hash_secret(plaintext),
        name=name or "token",
    )
    session.add(token)
    await session.flush()
    return token, plaintext


async def revoke(session: AsyncSession, token_id: uuid.UUID, user_id: uuid.UUID) -> bool:
    """Revoke a token the caller owns. Returns False if not found/not owned."""
    token = await session.get(McpToken, token_id)
    if token is None or token.user_id != user_id or token.revoked_at is not None:
        return False
    token.revoked_at = utcnow()
    await session.flush()
    return True


async def resolve(session: AsyncSession, presented: str) -> User | None:
    """Map a presented MCP token to its active owner, or None.

    Deterministic keyed HMAC means a single indexed lookup; bumps last_used_at.
    """
    if not presented:
        return None
    token_hash = security.hash_secret(presented)
    token = (
        await session.execute(
            select(McpToken).where(
                McpToken.token_hash == token_hash, McpToken.revoked_at.is_(None)
            )
        )
    ).scalar_one_or_none()
    if token is None:
        return None
    user = await users_service.get_by_id(session, token.user_id)
    if user is None or not user.is_active:
        return None
    token.last_used_at = utcnow()
    await session.flush()
    return user
