"""The MCP tools delegate to the hub client and shape errors cleanly."""

from __future__ import annotations

import os

os.environ.setdefault("HUGINN_MCP_SERVICE_TOKEN", "test-token")

import pytest

import app.server as server
from app.hub_client import HubError


class FakeHub:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    async def list_vms(self, state=None):
        self.calls.append(("list_vms", state))
        return [
            {"id": "vm1", "name": "web-01", "state": state or "active",
             "exec_mode": "restricted", "ip_address": "10.0.0.1", "worker_version": "v1.0.0"}
        ]

    async def execute_command(self, vm_id, command, wait):
        raise HubError(403, "VM is not in unrestricted mode")

    async def trigger_update(self, vm_id):
        self.calls.append(("trigger_update", vm_id))
        return {"id": "t1", "type": "update"}

    async def wait_task(self, task_id, timeout):
        self.calls.append(("wait_task", task_id, timeout))
        return {"id": task_id, "status": "succeeded"}


@pytest.mark.asyncio
async def test_list_vms_tool_delegates(monkeypatch) -> None:
    fake = FakeHub()
    monkeypatch.setattr(server, "hub", fake)
    result = await server.list_vms(state="active")
    assert result[0]["id"] == "vm1" and result[0]["worker_version"] == "v1.0.0"
    assert fake.calls == [("list_vms", "active")]


@pytest.mark.asyncio
async def test_wait_for_task_tool_delegates(monkeypatch) -> None:
    fake = FakeHub()
    monkeypatch.setattr(server, "hub", fake)
    result = await server.wait_for_task("task-1", timeout=42)
    assert result == {"id": "task-1", "status": "succeeded"}
    assert fake.calls == [("wait_task", "task-1", 42)]


@pytest.mark.asyncio
async def test_list_vms_brief_projects_essentials(monkeypatch) -> None:
    monkeypatch.setattr(server, "hub", FakeHub())
    result = await server.list_vms(brief=True)
    assert result == [{"id": "vm1", "name": "web-01", "state": "active", "mode": "restricted"}]


@pytest.mark.asyncio
async def test_tool_converts_huberror_to_structured_error(monkeypatch) -> None:
    monkeypatch.setattr(server, "hub", FakeHub())
    result = await server.execute_command("vm1", "echo hi")
    assert result["error"]["status"] == 403
    assert "unrestricted" in result["error"]["detail"]


@pytest.mark.asyncio
async def test_trigger_update_tool_delegates(monkeypatch) -> None:
    fake = FakeHub()
    monkeypatch.setattr(server, "hub", fake)
    result = await server.trigger_update("vm1")
    assert result["type"] == "update"
    assert ("trigger_update", "vm1") in fake.calls
