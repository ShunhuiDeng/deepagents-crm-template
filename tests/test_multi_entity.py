from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import httpx
import pytest

from app.config import Settings
from app.database import ENTITY_SPECS, CRMRepository, EntityAccessError, InvalidActionError
from app.dependencies import get_current_user, get_repo
from app.main import create_app
from app.permissions import CurrentUser, Role
from app.schemas import AccountCreate, AccountOut, AccountUpdate, ActivityCreate


class FakeResult:
    def __init__(self, *, one: Any = None, many: list[Any] | None = None) -> None:
        self.one = one
        self.many = many or []

    async def fetchone(self):  # type: ignore[no-untyped-def]
        return self.one

    async def fetchall(self):  # type: ignore[no-untyped-def]
        return self.many


class RecordingConnection:
    def __init__(self, results: list[FakeResult]) -> None:
        self.results = iter(results)
        self.calls: list[tuple[Any, Any]] = []

    async def execute(self, statement, params=None):  # type: ignore[no-untyped-def]
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


def account_record(owner_id):  # type: ignore[no-untyped-def]
    now = datetime.now(UTC)
    return {
        "id": uuid4(),
        "name": "示例客户",
        "industry": None,
        "website": None,
        "phone": None,
        "email": None,
        "address": None,
        "city": None,
        "state": None,
        "country": None,
        "employee_count": None,
        "annual_revenue": None,
        "status": "active",
        "source": None,
        "owner_id": owner_id,
        "description": None,
        "created_at": now,
        "updated_at": now,
        "deleted_at": None,
    }


def test_sales_owner_is_derived_at_transactional_create_stage() -> None:
    sales = user(Role.SALES)
    values = CRMRepository._prepare_entity_values(
        sales,
        ENTITY_SPECS["accounts"],
        AccountCreate(name="示例测试公司"),
        creating=True,
    )
    assert "owner_id" not in values


def test_sales_cannot_assign_entity_to_another_user() -> None:
    sales = user(Role.SALES)
    with pytest.raises(EntityAccessError, match="不能把业务数据分配"):
        CRMRepository._prepare_entity_values(
            sales,
            ENTITY_SPECS["accounts"],
            AccountCreate(name="示例测试公司", owner_id=uuid4()),
            creating=True,
        )


async def test_sales_list_activity_is_scoped_to_assigned_user() -> None:
    sales = user(Role.SALES)
    connection = RecordingConnection([FakeResult(many=[])])
    repo = CRMRepository(FakePool(connection))  # type: ignore[arg-type]

    await repo.list_activities(sales)

    statement, params = connection.calls[0]
    assert "assigned_user_id" in statement.as_string()
    assert params == [sales.id, 50, 0]


async def test_manager_can_filter_accounts_by_owner() -> None:
    manager = user(Role.MANAGER)
    owner_id = uuid4()
    connection = RecordingConnection([FakeResult(many=[account_record(owner_id)])])
    repo = CRMRepository(FakePool(connection))  # type: ignore[arg-type]

    accounts = await repo.list_accounts(manager, owner_id=owner_id)

    statement, params = connection.calls[0]
    assert '"owner_id" = %s' in statement.as_string()
    assert params == [owner_id, 50, 0]
    assert [account.owner_id for account in accounts] == [owner_id]


@pytest.mark.parametrize("request_self", [False, True])
async def test_sales_account_owner_filter_never_escapes_own_scope(
    request_self: bool,
) -> None:
    sales = user(Role.SALES)
    requested_owner = sales.id if request_self else uuid4()
    rows = [account_record(sales.id)] if request_self else []
    connection = RecordingConnection([FakeResult(many=rows)])
    repo = CRMRepository(FakePool(connection))  # type: ignore[arg-type]

    accounts = await repo.list_accounts(sales, owner_id=requested_owner)

    statement, params = connection.calls[0]
    assert statement.as_string().count('"owner_id" = %s') == 2
    assert params == [sales.id, requested_owner, 50, 0]
    assert len(accounts) == int(request_self)


async def test_accounts_route_forwards_owner_filter() -> None:
    manager = user(Role.MANAGER)
    owner_id = uuid4()
    captured: dict[str, Any] = {}

    class RouteRepository:
        async def list_accounts(
            self, current_user: CurrentUser, **kwargs: Any
        ) -> list[AccountOut]:
            captured.update(current_user=current_user, **kwargs)
            return []

    app = create_app(
        Settings(_env_file=None, MODEL_NAME="anthropic:claude-sonnet-4-6")
    )
    app.dependency_overrides[get_current_user] = lambda: manager
    app.dependency_overrides[get_repo] = RouteRepository
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/api/accounts",
            params={"owner_id": str(owner_id), "status": "active", "limit": 25, "offset": 5},
        )

    assert response.status_code == 200
    assert response.json() == []
    assert captured == {
        "current_user": manager,
        "query": None,
        "status": "active",
        "owner_id": owner_id,
        "limit": 25,
        "offset": 5,
    }


async def test_activity_account_filter_uses_full_company_relationship_graph() -> None:
    manager = user(Role.MANAGER)
    account_id = uuid4()
    connection = RecordingConnection([FakeResult(many=[])])
    repo = CRMRepository(FakePool(connection))  # type: ignore[arg-type]

    await repo.list_activities(manager, account_id=account_id)

    statement, params = connection.calls[0]
    rendered = statement.as_string()
    assert "a.account_id = %s" in rendered
    assert "c_scope.id = a.contact_id" in rendered
    assert "o_scope.id = a.opportunity_id" in rendered
    assert "lc_scope.lead_id = a.lead_id" in rendered
    assert params == [account_id, account_id, account_id, account_id, 50, 0]


async def test_sales_company_graph_activity_filter_keeps_assignee_scope() -> None:
    sales = user(Role.SALES)
    account_id = uuid4()
    connection = RecordingConnection([FakeResult(many=[])])
    repo = CRMRepository(FakePool(connection))  # type: ignore[arg-type]

    await repo.list_activities(sales, account_id=account_id, limit=100, offset=20)

    statement, params = connection.calls[0]
    rendered = statement.as_string()
    assert 'a."assigned_user_id" = %s' in rendered
    assert "lc_scope.account_id = %s" in rendered
    assert params == [sales.id, account_id, account_id, account_id, account_id, 100, 20]


async def test_sales_activity_cannot_reference_another_sales_account() -> None:
    sales = user(Role.SALES)
    account_id = uuid4()
    another_owner = uuid4()
    connection = RecordingConnection(
        [
            FakeResult(one={"owner_id": another_owner}),
        ]
    )
    repo = CRMRepository(FakePool(connection))  # type: ignore[arg-type]
    values = repo._prepare_entity_values(
        sales,
        ENTITY_SPECS["activities"],
        ActivityCreate(type="call", subject="回访", account_id=account_id),
        creating=True,
    )

    with pytest.raises(EntityAccessError, match="不能关联不属于自己的"):
        await repo._validate_entity_references(
            connection, sales, ENTITY_SPECS["activities"], values
        )


def test_real_table_output_schema_contains_database_timestamps() -> None:
    from app.schemas import ActivityOut

    now = datetime.now(UTC)
    activity = ActivityOut(
        id=uuid4(),
        type="meeting",
        subject="需求沟通",
        assigned_user_id=None,
        created_at=now,
        updated_at=now,
    )
    assert activity.status == "planned"
    assert activity.priority == "normal"


async def test_generic_pending_insert_uses_entity_action_name(monkeypatch) -> None:
    sales = user(Role.SALES)
    connection = RecordingConnection([FakeResult(one={"id": sales.id})])
    repo = CRMRepository(FakePool(connection))  # type: ignore[arg-type]
    captured: dict[str, Any] = {}

    async def capture(_user, _conversation_id, *, action_type, payload):  # type: ignore[no-untyped-def]
        captured.update(action_type=action_type, payload=payload)
        return "pending"

    monkeypatch.setattr(repo, "_create_pending_action", capture)
    result = await repo.create_pending_entity_insert(
        sales, uuid4(), "account", AccountCreate(name="示例客户")
    )

    assert result == "pending"
    assert captured["action_type"] == "insert_account"
    assert captured["payload"]["entity_type"] == "account"
    assert captured["payload"]["fields"] == {"name": "示例客户"}


async def test_conn_update_rejects_stale_optimistic_timestamp() -> None:
    manager = user(Role.MANAGER)
    now = datetime.now(UTC)
    record = AccountOut(
        id=uuid4(),
        name="原名称",
        owner_id=manager.id,
        created_at=now,
        updated_at=now,
        deleted_at=None,
    ).model_dump(mode="python")
    connection = RecordingConnection([FakeResult(one=record)])
    repo = CRMRepository(FakePool(connection))  # type: ignore[arg-type]

    with pytest.raises(InvalidActionError, match="已被其他操作更新"):
        await repo._update_entity_conn(
            connection,
            manager,
            ENTITY_SPECS["accounts"],
            record["id"],
            AccountUpdate(name="新名称"),
            AccountOut,
            expected_updated_at=now.replace(year=now.year - 1),
        )

    assert len(connection.calls) == 1
