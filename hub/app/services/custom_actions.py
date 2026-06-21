"""Admin-defined custom commands: CRUD + per-VM authorization.

A custom action is a fixed argv (no shell, no parameters) that a VM in
``custom``/``unrestricted`` mode may run if it carries one of the action's
allowed tags. Argv is validated here so a definition can never carry a shell or
malformed element.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.custom_action import CustomAction, CustomActionTag
from app.models.tag import VMTag

_MAX_ARGV = 64
_MAX_ELEM = 1024


def validate_argv(argv: object) -> list[str]:
    """Validate a fixed argv: a non-empty list of non-empty, null-free strings."""
    if not isinstance(argv, list) or not argv:
        raise ValueError("argv must be a non-empty list of strings")
    if len(argv) > _MAX_ARGV:
        raise ValueError(f"argv has too many elements (max {_MAX_ARGV})")
    out: list[str] = []
    for elem in argv:
        if not isinstance(elem, str) or elem == "":
            raise ValueError("each argv element must be a non-empty string")
        if "\x00" in elem or len(elem) > _MAX_ELEM:
            raise ValueError("argv element contains a null byte or is too long")
        out.append(elem)
    return out


async def list_all(session: AsyncSession) -> list[CustomAction]:
    result = await session.execute(select(CustomAction).order_by(CustomAction.name))
    return list(result.scalars())


async def get(session: AsyncSession, action_id: uuid.UUID) -> CustomAction | None:
    return await session.get(CustomAction, action_id)


async def get_by_name(session: AsyncSession, name: str) -> CustomAction | None:
    result = await session.execute(select(CustomAction).where(CustomAction.name == name))
    return result.scalar_one_or_none()


async def tag_ids_for(session: AsyncSession, action_id: uuid.UUID) -> list[uuid.UUID]:
    result = await session.execute(
        select(CustomActionTag.tag_id).where(CustomActionTag.action_id == action_id)
    )
    return list(result.scalars())


async def _set_tags(
    session: AsyncSession, action_id: uuid.UUID, tag_ids: Sequence[uuid.UUID]
) -> None:
    await session.execute(
        delete(CustomActionTag).where(CustomActionTag.action_id == action_id)
    )
    for tag_id in dict.fromkeys(tag_ids):  # dedupe, keep order
        session.add(CustomActionTag(action_id=action_id, tag_id=tag_id))


async def create(
    session: AsyncSession,
    *,
    name: str,
    description: str,
    argv: list[str],
    tag_ids: Sequence[uuid.UUID],
    created_by: str | None,
) -> CustomAction:
    action = CustomAction(
        name=name,
        description=description,
        argv=validate_argv(argv),
        created_by=created_by,
    )
    session.add(action)
    await session.flush()
    await _set_tags(session, action.id, tag_ids)
    await session.flush()
    return action


async def update(
    session: AsyncSession,
    action_id: uuid.UUID,
    *,
    description: str | None = None,
    argv: list[str] | None = None,
    enabled: bool | None = None,
    tag_ids: Sequence[uuid.UUID] | None = None,
) -> CustomAction | None:
    action = await session.get(CustomAction, action_id)
    if action is None:
        return None
    if description is not None:
        action.description = description
    if argv is not None:
        action.argv = validate_argv(argv)
    if enabled is not None:
        action.enabled = enabled
    if tag_ids is not None:
        await _set_tags(session, action_id, tag_ids)
    await session.flush()
    return action


async def remove(session: AsyncSession, action_id: uuid.UUID) -> bool:
    action = await session.get(CustomAction, action_id)
    if action is None:
        return False
    await session.delete(action)
    await session.flush()
    return True


async def vm_allowed(session: AsyncSession, action_id: uuid.UUID, vm_id: uuid.UUID) -> bool:
    """True if the VM carries at least one of the action's allowed tags.

    An action with no allowed tags runs nowhere (explicit-scope by design).
    """
    action_tags = set(await tag_ids_for(session, action_id))
    if not action_tags:
        return False
    vm_tags = set(
        (await session.execute(select(VMTag.tag_id).where(VMTag.vm_id == vm_id))).scalars()
    )
    return bool(action_tags & vm_tags)
