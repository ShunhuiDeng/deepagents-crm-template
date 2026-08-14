from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID, uuid4

import pytest
from langchain.tools import ToolRuntime
from pydantic import BaseModel

from app.agents.context import CRMContext
from app.agents.crud_agent.tools import build_crud_tools


@dataclass
class FakeRepository:
    inserts: list[tuple[Any, UUID, str, BaseModel]] = field(default_factory=list)
    updates: list[tuple[Any, UUID, str, UUID, BaseModel]] = field(default_factory=list)
    conversions: list[tuple[Any, UUID, UUID, BaseModel]] = field(default_factory=list)
    selections: list[tuple[str, Any, dict[str, Any]]] = field(default_factory=list)

    async def list_leads(self, user: Any, **kwargs: Any) -> list[dict[str, Any]]:
        self.selections.append(("leads", user, kwargs))
        return [{"id": str(uuid4()), "first_name": "林", "last_name": "明"}]

    async def list_activities(self, user: Any, **kwargs: Any) -> list[dict[str, Any]]:
        self.selections.append(("activities", user, kwargs))
        return []

    async def get_account_overview(self, user: Any, account_id: UUID) -> dict[str, Any]:
        self.selections.append(("account_overview", user, {"account_id": account_id}))
        return {
            "account": {"id": str(account_id), "name": "示例科技"},
            "contacts": [],
            "opportunities": [],
            "activities": [],
            "conversion_sources": [],
        }

    async def create_pending_entity_insert(
        self,
        user: Any,
        conversation_id: UUID,
        entity_type: str,
        data: BaseModel,
    ) -> dict[str, Any]:
        self.inserts.append((user, conversation_id, entity_type, data))
        return {"id": str(uuid4()), "action_type": f"insert_{entity_type}"}

    async def create_pending_entity_update(
        self,
        user: Any,
        conversation_id: UUID,
        entity_type: str,
        entity_id: UUID,
        data: BaseModel,
    ) -> dict[str, Any]:
        self.updates.append((user, conversation_id, entity_type, entity_id, data))
        return {"id": str(uuid4()), "action_type": f"update_{entity_type}"}

    async def create_pending_lead_conversion(
        self,
        user: Any,
        conversation_id: UUID,
        lead_id: UUID,
        data: BaseModel,
    ) -> dict[str, Any]:
        self.conversions.append((user, conversation_id, lead_id, data))
        return {"id": str(uuid4()), "action_type": "convert_lead"}


def _runtime(role: str = "sales") -> ToolRuntime[CRMContext, Any]:
    context = CRMContext(
        user_id=uuid4(),
        username="agent-test",
        display_name="Agent Test",
        email="agent-test@example.com",
        role=role,
        permissions=frozenset(),
        conversation_id=uuid4(),
        request_id="test-request",
    )
    return ToolRuntime(
        state={},
        context=context,
        config={},
        stream_writer=lambda _event: None,
        tool_call_id="test-call",
        store=None,
    )


def _tools(repository: FakeRepository) -> dict[str, Any]:
    return {item.name: item for item in build_crud_tools(repository)}  # type: ignore[arg-type]


def test_tool_schemas_never_expose_ownership_inputs() -> None:
    tools = _tools(FakeRepository())

    assert len(tools) == 17
    for agent_tool in tools.values():
        assert "owner_id" not in agent_tool.args
        assert "assigned_user_id" not in agent_tool.args


async def test_select_leads_uses_authenticated_runtime_and_bounds_limit() -> None:
    repository = FakeRepository()
    runtime = _runtime()
    result = await _tools(repository)["select_leads"].coroutine(
        runtime=runtime,
        query="林明",
        limit=500,
    )

    assert result["ok"] is True
    assert result["count"] == 1
    entity, current_user, kwargs = repository.selections[0]
    assert entity == "leads"
    assert current_user.id == runtime.context.user_id
    assert kwargs == {"query": "林明", "status": None, "limit": 50}


async def test_account_overview_uses_runtime_identity_and_exact_uuid() -> None:
    repository = FakeRepository()
    runtime = _runtime()
    account_id = uuid4()

    result = await _tools(repository)["select_account_overview"].coroutine(
        runtime=runtime,
        account_id=str(account_id),
    )

    assert result["ok"] is True
    entity, current_user, kwargs = repository.selections[0]
    assert entity == "account_overview"
    assert current_user.id == runtime.context.user_id
    assert kwargs == {"account_id": account_id}


async def test_activity_selection_forwards_all_cross_entity_filters() -> None:
    repository = FakeRepository()
    runtime = _runtime()
    account_id, contact_id, lead_id, opportunity_id = (uuid4() for _ in range(4))

    result = await _tools(repository)["select_activities"].coroutine(
        runtime=runtime,
        account_id=str(account_id),
        contact_id=str(contact_id),
        lead_id=str(lead_id),
        opportunity_id=str(opportunity_id),
        limit=100,
    )

    assert result == {"ok": True, "count": 0, "activities": []}
    entity, current_user, kwargs = repository.selections[0]
    assert entity == "activities"
    assert current_user.id == runtime.context.user_id
    assert kwargs == {
        "query": None,
        "status": None,
        "account_id": account_id,
        "contact_id": contact_id,
        "lead_id": lead_id,
        "opportunity_id": opportunity_id,
        "limit": 50,
    }


async def test_convert_lead_creates_exactly_one_atomic_pending_action() -> None:
    repository = FakeRepository()
    runtime = _runtime()
    lead_id = uuid4()

    result = await _tools(repository)["convert_lead"].coroutine(
        runtime=runtime,
        lead_id=str(lead_id),
        opportunity={"name": "首年采购", "amount": "120000.00"},
    )

    assert result["ok"] is True
    assert result["requires_approval"] is True
    assert len(repository.conversions) == 1
    assert repository.inserts == []
    assert repository.updates == []
    current_user, conversation_id, recorded_lead_id, payload = repository.conversions[0]
    assert current_user.id == runtime.context.user_id
    assert conversation_id == runtime.context.conversation_id
    assert recorded_lead_id == lead_id
    assert payload.account_id is None
    assert payload.account is None
    assert payload.contact_id is None
    assert payload.contact is None
    assert payload.opportunity.name == "首年采购"


async def test_convert_lead_accepts_existing_targets_as_uuids() -> None:
    repository = FakeRepository()
    account_id, contact_id = uuid4(), uuid4()

    result = await _tools(repository)["convert_lead"].coroutine(
        runtime=_runtime(),
        lead_id=str(uuid4()),
        account_id=str(account_id),
        contact_id=str(contact_id),
    )

    assert result["ok"] is True
    payload = repository.conversions[0][3]
    assert payload.account_id == account_id
    assert payload.contact_id == contact_id


@pytest.mark.parametrize(
    "nested_field",
    ["account", "contact", "opportunity"],
)
async def test_convert_lead_rejects_nested_owner_injection(nested_field: str) -> None:
    repository = FakeRepository()

    result = await _tools(repository)["convert_lead"].coroutine(
        runtime=_runtime(),
        lead_id=str(uuid4()),
        **{nested_field: {"name": "恶意数据", "owner_id": str(uuid4())}},
    )

    assert result["ok"] is False
    assert "归属字段" in result["error"]
    assert repository.conversions == []


async def test_convert_lead_rejects_existing_and_new_account_together() -> None:
    repository = FakeRepository()

    result = await _tools(repository)["convert_lead"].coroutine(
        runtime=_runtime(),
        lead_id=str(uuid4()),
        account_id=str(uuid4()),
        account={"name": "重复目标"},
    )

    assert result["ok"] is False
    assert "不能同时提供" in result["error"]
    assert repository.conversions == []


@pytest.mark.parametrize(
    ("tool_name", "arguments", "entity_type"),
    [
        ("insert_lead", {"first_name": "林", "phone": "13800000000"}, "lead"),
        ("insert_account", {"name": "示例科技"}, "account"),
        ("insert_contact", {"first_name": "周", "wechat": "zhou"}, "contact"),
        ("insert_opportunity", {"name": "年度采购"}, "opportunity"),
        ("insert_activity", {"type": "call", "subject": "首次回访"}, "activity"),
    ],
)
async def test_each_insert_only_creates_a_pending_action(
    tool_name: str,
    arguments: dict[str, Any],
    entity_type: str,
) -> None:
    repository = FakeRepository()
    runtime = _runtime()

    result = await _tools(repository)[tool_name].coroutine(runtime=runtime, **arguments)

    assert result["ok"] is True
    assert result["requires_approval"] is True
    assert len(repository.inserts) == 1
    current_user, conversation_id, recorded_type, payload = repository.inserts[0]
    assert current_user.id == runtime.context.user_id
    assert conversation_id == runtime.context.conversation_id
    assert recorded_type == entity_type
    assert "owner_id" not in payload.model_fields_set
    assert "assigned_user_id" not in payload.model_fields_set


@pytest.mark.parametrize(
    ("tool_name", "id_field", "fields", "entity_type"),
    [
        ("update_lead", "lead_id", {"status": "contacted"}, "lead"),
        ("update_account", "account_id", {"industry": "制造业"}, "account"),
        ("update_contact", "contact_id", {"wechat": "new-wechat"}, "contact"),
        ("update_opportunity", "opportunity_id", {"stage": "won"}, "opportunity"),
        ("update_activity", "activity_id", {"status": "completed"}, "activity"),
    ],
)
async def test_each_update_only_creates_a_pending_action(
    tool_name: str,
    id_field: str,
    fields: dict[str, Any],
    entity_type: str,
) -> None:
    repository = FakeRepository()
    runtime = _runtime()
    entity_id = uuid4()

    result = await _tools(repository)[tool_name].coroutine(
        runtime=runtime,
        fields=fields,
        **{id_field: str(entity_id)},
    )

    assert result["ok"] is True
    assert result["requires_approval"] is True
    assert len(repository.updates) == 1
    current_user, conversation_id, recorded_type, recorded_id, payload = repository.updates[0]
    assert current_user.id == runtime.context.user_id
    assert conversation_id == runtime.context.conversation_id
    assert recorded_type == entity_type
    assert recorded_id == entity_id
    assert payload.model_fields_set == set(fields)


@pytest.mark.parametrize(
    ("tool_name", "id_field", "ownership_field"),
    [
        ("update_lead", "lead_id", "owner_id"),
        ("update_account", "account_id", "owner_id"),
        ("update_contact", "contact_id", "owner_id"),
        ("update_opportunity", "opportunity_id", "owner_id"),
        ("update_activity", "activity_id", "assigned_user_id"),
    ],
)
async def test_updates_reject_model_supplied_ownership(
    tool_name: str,
    id_field: str,
    ownership_field: str,
) -> None:
    repository = FakeRepository()
    result = await _tools(repository)[tool_name].coroutine(
        runtime=_runtime(),
        fields={ownership_field: str(uuid4())},
        **{id_field: str(uuid4())},
    )

    assert result["ok"] is False
    assert ownership_field in result["error"]
    assert repository.updates == []


async def test_viewer_cannot_stage_insert() -> None:
    repository = FakeRepository()

    result = await _tools(repository)["insert_account"].coroutine(
        runtime=_runtime("viewer"),
        name="无权限公司",
    )

    assert result["ok"] is False
    assert repository.inserts == []


async def test_opportunity_tools_accept_primary_contact_relation() -> None:
    repository = FakeRepository()
    runtime = _runtime()
    primary_contact_id = uuid4()

    insert_result = await _tools(repository)["insert_opportunity"].coroutine(
        runtime=runtime,
        name="联系人主导商机",
        primary_contact_id=str(primary_contact_id),
    )
    opportunity_id = uuid4()
    update_result = await _tools(repository)["update_opportunity"].coroutine(
        runtime=runtime,
        opportunity_id=str(opportunity_id),
        fields={"primary_contact_id": str(primary_contact_id)},
    )

    assert insert_result["ok"] is True
    assert repository.inserts[0][3].primary_contact_id == primary_contact_id
    assert update_result["ok"] is True
    assert repository.updates[0][3] == opportunity_id
    assert repository.updates[0][4].primary_contact_id == primary_contact_id
