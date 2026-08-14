from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest

from app.database import CRMRepository, InvalidActionError
from app.permissions import CurrentUser, Role
from app.schemas import CustomerCreate, CustomerOut, CustomerUpdate


class FakeResult:
    def __init__(self, one: Any = None) -> None:
        self.one = one

    async def fetchone(self) -> Any:
        return self.one


class RecordingConnection:
    def __init__(self, results: list[FakeResult]) -> None:
        self.results = iter(results)
        self.calls: list[tuple[Any, Any]] = []

    async def execute(self, statement: Any, params: Any = None) -> FakeResult:
        self.calls.append((statement, params))
        return next(self.results)

    @asynccontextmanager
    async def transaction(self):  # type: ignore[no-untyped-def]
        yield


class FakePool:
    def __init__(self, connection: RecordingConnection) -> None:
        self.connection_value = connection

    @asynccontextmanager
    async def connection(self):  # type: ignore[no-untyped-def]
        yield self.connection_value


def user(role: Role) -> CurrentUser:
    return CurrentUser(
        id=uuid4(),
        username=role.value,
        email=f"{role.value}@example.com",
        display_name=role.value,
        role=role,
        is_active=True,
    )


def customer_record(
    owner_id: UUID,
    *,
    customer_id: UUID | None = None,
    status: str = "new",
    name: str = "旧接口客户",
    version: int = 1,
) -> dict[str, Any]:
    now = datetime.now(UTC)
    return {
        "id": customer_id or uuid4(),
        "name": name,
        "company": "示例公司",
        "title": None,
        "email": None,
        "phone": "13800000000",
        "status": status,
        "source": None,
        "notes": None,
        "extra": {},
        "version": version,
        "owner_id": owner_id,
        "owner_name": "负责人",
        "created_at": now,
        "updated_at": now,
    }


async def noop_audit(*_args: Any, **_kwargs: Any) -> None:
    return None


async def test_legacy_customer_create_and_pending_insert_cannot_claim_converted() -> None:
    manager = user(Role.MANAGER)
    data = CustomerCreate(name="旁路线索", phone="13800000000", status="converted")
    connection = RecordingConnection([])
    repo = CRMRepository(FakePool(connection))  # type: ignore[arg-type]

    with pytest.raises(InvalidActionError, match="正式转换流程"):
        await repo._insert_customer(connection, manager, data)
    with pytest.raises(InvalidActionError, match="正式转换流程"):
        await repo.create_pending_customer_insert(manager, uuid4(), data)

    assert connection.calls == []


@pytest.mark.parametrize(
    ("before_status", "submitted_status", "message"),
    [
        ("qualified", "converted", "正式转换流程"),
        ("converted", "new", "不能通过普通更新离开"),
    ],
)
async def test_legacy_customer_update_cannot_enter_or_leave_converted(
    before_status: str, submitted_status: str, message: str
) -> None:
    manager = user(Role.MANAGER)
    record = customer_record(manager.id, status=before_status)
    connection = RecordingConnection([FakeResult(record)])
    repo = CRMRepository(FakePool(connection))  # type: ignore[arg-type]

    with pytest.raises(InvalidActionError, match=message):
        await repo._update_customer(
            connection,
            manager,
            record["id"],
            CustomerUpdate(status=submitted_status),
        )

    assert len(connection.calls) == 1


async def test_legacy_converted_noop_status_does_not_block_other_edits() -> None:
    manager = user(Role.MANAGER)
    record = customer_record(manager.id, status="converted")
    after = {
        **record,
        "name": "更新后的名称",
        "version": record["version"] + 1,
        "updated_at": datetime.now(UTC),
    }
    connection = RecordingConnection(
        [FakeResult(record), FakeResult(), FakeResult(after)]
    )
    repo = CRMRepository(FakePool(connection))  # type: ignore[arg-type]
    repo._write_audit = noop_audit  # type: ignore[method-assign]

    result = await repo._update_customer(
        connection,
        manager,
        record["id"],
        CustomerUpdate(name="更新后的名称", status="converted"),
    )

    assert result and result.name == "更新后的名称"
    assert connection.calls[1][1] == ["更新后的名称", record["id"]]


async def test_legacy_pending_update_uses_the_same_converted_state_machine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = user(Role.MANAGER)
    record = CustomerOut.model_validate(customer_record(manager.id, status="converted"))
    repo = CRMRepository(None)  # type: ignore[arg-type]
    captured: dict[str, Any] = {}

    async def get_customer(_current_user: CurrentUser, _customer_id: UUID) -> CustomerOut:
        return record

    async def capture(
        _current_user: CurrentUser,
        _conversation_id: UUID,
        *,
        action_type: str,
        payload: dict[str, Any],
    ) -> str:
        captured.update(action_type=action_type, payload=payload)
        return "pending"

    monkeypatch.setattr(repo, "get_customer", get_customer)
    monkeypatch.setattr(repo, "_create_pending_action", capture)

    with pytest.raises(InvalidActionError, match="不能通过普通更新离开"):
        await repo.create_pending_customer_update(
            manager, uuid4(), str(record.id), CustomerUpdate(status="new")
        )

    result = await repo.create_pending_customer_update(
        manager,
        uuid4(),
        str(record.id),
        CustomerUpdate(name="可编辑", status="converted"),
    )
    assert result == "pending"
    assert captured["action_type"] == "update_customer"
    assert captured["payload"]["fields"] == {"name": "可编辑"}


@pytest.mark.parametrize("blocked_by", ["conversion", "activity"])
async def test_legacy_customer_delete_preserves_active_crm_relationships(
    blocked_by: str,
) -> None:
    manager = user(Role.MANAGER)
    record = customer_record(manager.id)
    blocker_results = (
        [FakeResult({"exists": 1})]
        if blocked_by == "conversion"
        else [FakeResult(), FakeResult({"exists": 1})]
    )
    connection = RecordingConnection([FakeResult(record), *blocker_results])
    repo = CRMRepository(FakePool(connection))  # type: ignore[arg-type]

    with pytest.raises(InvalidActionError, match="仍被有效 CRM 关系引用"):
        await repo.delete_customer(manager, record["id"])

    statements = [str(statement) for statement, _params in connection.calls]
    assert not any("UPDATE leads" in statement for statement in statements)


def updated_user_record(user_id: UUID, role: Role) -> dict[str, Any]:
    return {
        "id": user_id,
        "username": f"user-{str(user_id)[:8]}",
        "email": f"{user_id}@example.com",
        "display_name": "管理员",
        "role": role.value,
        "is_active": True,
        "created_at": datetime.now(UTC),
        "last_login_at": None,
    }


async def test_role_changes_share_one_global_lock_before_counting_admins() -> None:
    actor = user(Role.ADMIN)
    lock_calls: list[tuple[Any, Any]] = []

    for target_id in (uuid4(), uuid4()):
        connection = RecordingConnection(
            [
                FakeResult(),
                FakeResult({"id": target_id, "role": Role.ADMIN.value}),
                FakeResult({"count": 2}),
                FakeResult(updated_user_record(target_id, Role.SALES)),
            ]
        )
        repo = CRMRepository(FakePool(connection))  # type: ignore[arg-type]
        repo._write_audit = noop_audit  # type: ignore[method-assign]

        await repo.update_user_role(actor, target_id, Role.SALES)

        lock_calls.append(connection.calls[0])
        assert "FOR UPDATE" in connection.calls[1][0]
        assert "COUNT(*)" in connection.calls[2][0]

    assert all("pg_advisory_xact_lock" in statement for statement, _params in lock_calls)
    assert lock_calls[0][1] == lock_calls[1][1] == (8_426_081_301,)


async def test_last_admin_guard_runs_while_global_role_lock_is_held() -> None:
    actor = user(Role.ADMIN)
    target_id = actor.id
    connection = RecordingConnection(
        [
            FakeResult(),
            FakeResult({"id": target_id, "role": Role.ADMIN.value}),
            FakeResult({"count": 1}),
        ]
    )
    repo = CRMRepository(FakePool(connection))  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="最后一个启用中的管理员"):
        await repo.update_user_role(actor, target_id, Role.SALES)

    assert "pg_advisory_xact_lock" in connection.calls[0][0]
    assert len(connection.calls) == 3
