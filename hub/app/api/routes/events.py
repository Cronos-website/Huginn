"""Server-Sent Events stream for the dashboard's live updates.

Connected dashboards receive tiny hints (``{"type": "tasks"}`` / ``"vms"``) and
invalidate the matching query cache, so the UI refreshes the instant something
changes instead of waiting for its periodic poll.

EventSource cannot send an Authorization header, so the JWT is passed as a
``token`` query parameter (same-origin, over TLS). The DB session is used only to
authenticate and is released before streaming, so an open stream never holds a
pooled connection.
"""

from __future__ import annotations

import asyncio
import json
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import events
from app.core.jwt import MFA_SCOPES, TokenError, decode_access_token
from app.db import get_session
from app.services import users as users_service

router = APIRouter(prefix="/api", tags=["events"])

_KEEPALIVE_SECONDS = 20.0


async def _authenticate(session: AsyncSession, token: str) -> None:
    try:
        payload = decode_access_token(token)
        user_id = uuid.UUID(payload["sub"])
    except (TokenError, KeyError, ValueError) as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid token") from exc
    if payload.get("scope") in MFA_SCOPES:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "token not valid for API access")
    user = await users_service.get_by_id(session, user_id)
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "user not found or inactive")


@router.get("/events")
async def events_stream(
    request: Request,
    token: str = Query(...),
    session: AsyncSession = Depends(get_session),
) -> StreamingResponse:
    await _authenticate(session, token)
    # Release the pooled connection before the (potentially long-lived) stream;
    # the stream itself never touches the database.
    await session.close()
    queue = events.subscribe()

    async def gen():  # type: ignore[no-untyped-def]
        try:
            yield ": connected\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=_KEEPALIVE_SECONDS)
                    yield f"data: {json.dumps(event)}\n\n"
                except TimeoutError:
                    yield ": keepalive\n\n"  # keep the connection (and proxies) warm
        finally:
            events.unsubscribe(queue)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
