from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from app.database import CRMRepository
from app.permissions import CurrentUser, Role


class FakeResult:
    def __init__(self, value: Any) -> None:
        self.value = value

    async def fetchall(self) -> list[dict[str, Any]]:
        return self.value

    async def fetchone(self) -> dict[str, Any]:
        return self.value


class FakeConnection:
    def __init__(self, responses: list[Any]) -> None:
        self.responses = iter(responses)
        self.calls: list[tuple[str, Any]] = []

    async def execute(self, statement: str, params: Any = None) -> FakeResult:
        self.calls.append((statement, params))
        return FakeResult(next(self.responses))


class FakePool:
    def __init__(self, connection: FakeConnection) -> None:
        self.fake_connection = connection

    @asynccontextmanager
    async def connection(self):  # type: ignore[no-untyped-def]
        yield self.fake_connection


def user(role: Role) -> CurrentUser:
    return CurrentUser(
        id=uuid4(),
        username=f"{role.value}-user",
        email=f"{role.value}@example.com",
        display_name=role.value,
        role=role,
        is_active=True,
    )


def customer_record(*, owner_id=None, status: str = "new") -> dict[str, Any]:
    now = datetime.now(UTC)
    return {
        "id": uuid4(),
        "name": "测试客户",
        "company": "测试公司",
        "title": None,
        "email": "customer@example.com",
        "phone": None,
        "status": status,
        "source": None,
        "notes": None,
        "extra": {},
        "version": 1,
        "owner_id": owner_id,
        "owner_name": "销售员" if owner_id else None,
        "created_at": now,
        "updated_at": now,
    }


async def test_sales_dashboard_is_scoped_to_owned_customers() -> None:
    sales = user(Role.SALES)
    connection = FakeConnection(
        [
            [{"status": "new", "count": 2}, {"status": "nurturing", "count": 1}],
            [customer_record(owner_id=sales.id)],
            {"leads": 3, "accounts": 2, "contacts": 4, "opportunities": 1, "activities": 5},
        ]
    )
    repository = CRMRepository(FakePool(connection))  # type: ignore[arg-type]

    dashboard = await repository.get_dashboard(sales)

    assert dashboard.total_customers == 3
    assert dashboard.status_counts["new"] == 2
    assert dashboard.status_counts["contacted"] == 0
    assert dashboard.status_counts["nurturing"] == 1
    assert dashboard.total_users is None
    assert dashboard.entity_counts["accounts"] == 2
    assert len(connection.calls) == 3
    assert all("l.owner_id = %s" in statement for statement, _ in connection.calls[:2])
    assert all(params == [sales.id] for _, params in connection.calls[:2])
    assert connection.calls[2][1] == [sales.id] * 5
    assert "assigned_user_id = %s" in connection.calls[2][0]


async def test_admin_dashboard_includes_user_count_and_all_customers() -> None:
    admin = user(Role.ADMIN)
    connection = FakeConnection(
        [
            [{"status": "qualified", "count": 4}],
            [customer_record(status="qualified")],
            {"leads": 4, "accounts": 3, "contacts": 5, "opportunities": 2, "activities": 8},
            {"count": 7},
        ]
    )
    repository = CRMRepository(FakePool(connection))  # type: ignore[arg-type]

    dashboard = await repository.get_dashboard(admin)

    assert dashboard.total_customers == 4
    assert dashboard.status_counts["qualified"] == 4
    assert dashboard.total_users == 7
    assert len(dashboard.recent_customers) == 1
    assert dashboard.entity_counts["activities"] == 8
    assert len(connection.calls) == 4
    assert "l.owner_id = %s" not in connection.calls[0][0]
    assert "l.owner_id = %s" not in connection.calls[1][0]
    assert connection.calls[0][1] == []
    assert connection.calls[1][1] == []
    assert connection.calls[2][1] == []
    assert "password_hash IS NOT NULL" in connection.calls[3][0]
