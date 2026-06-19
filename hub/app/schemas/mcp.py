"""Per-user MCP token schemas."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import UserRole


class McpTokenCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)


class McpTokenOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    created_at: datetime
    last_used_at: datetime | None


class McpTokenCreated(BaseModel):
    id: uuid.UUID
    name: str
    token: str  # plaintext, shown once


class WhoAmI(BaseModel):
    username: str
    role: UserRole
