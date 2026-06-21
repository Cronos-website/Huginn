"""Admin-defined custom commands runnable on VMs in ``custom`` exec mode.

Unlike the built-in whitelist (whose argv lives on the worker), a custom action's
argv is defined here and shipped to the worker in the task payload. It is run as a
**fixed argv with no shell** — same injection-safety as the built-ins — and is
gated twice: the target VM must be in ``custom``/``unrestricted`` mode AND carry
one of the action's allowed tags.
"""

from __future__ import annotations

import uuid

from sqlalchemy import JSON, Boolean, ForeignKey, PrimaryKeyConstraint, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.mixins import TimestampMixin


class CustomAction(Base, TimestampMixin):
    __tablename__ = "custom_actions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    description: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    # Fixed argv (list of strings); argv[0] is the binary. Never a shell string.
    argv: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_by: Mapped[str | None] = mapped_column(String(255), nullable=True)


class CustomActionTag(Base):
    """Which VM tags are allowed to run a custom action (many-to-many)."""

    __tablename__ = "custom_action_tags"
    __table_args__ = (PrimaryKeyConstraint("action_id", "tag_id"),)

    action_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("custom_actions.id", ondelete="CASCADE")
    )
    tag_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tags.id", ondelete="CASCADE"))
