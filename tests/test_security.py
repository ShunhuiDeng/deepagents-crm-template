from uuid import uuid4

import pytest
from fastapi import HTTPException
from psycopg import OperationalError
from psycopg_pool import AsyncConnectionPool, PoolTimeout
from starlette.requests import Request

from app.config import Settings
from app.main import (
    connection_pool_runtime_options,
    create_app,
    is_loopback_request,
    safe_pending_failure_detail,
)
from app.permissions import (
    CurrentUser,
    CustomerVisibility,
    Permission,
    Role,
    customer_visibility_for,
    has_permission,
    require_permission,
)
from app.security import (
    digest_session_token,
    generate_session_token,
    hash_password,
    verify_password,
)


def _user(role: Role) -> CurrentUser:
    return CurrentUser(
        id=uuid4(),
        username="tester",
        email="tester@example.com",
        display_name="Tester",
        role=role,
        is_active=True,
    )


@pytest.mark.asyncio
async def test_argon2_password_round_trip() -> None:
    password_hash = await hash_password("correct horse battery staple")
    assert password_hash.startswith("$argon2id$")
    assert await verify_password("correct horse battery staple", password_hash)
    assert not await verify_password("wrong password", password_hash)


def test_session_tokens_are_opaque_and_only_digest_is_stored() -> None:
    token = generate_session_token()
    assert len(token) >= 40
    assert digest_session_token(token) != token
    assert len(digest_session_token(token)) == 64


def test_roles_enforce_customer_permissions_and_visibility() -> None:
    admin = _user(Role.ADMIN)
    sales = _user(Role.SALES)
    viewer = _user(Role.VIEWER)

    assert has_permission(admin, Permission.USERS_MANAGE)
    assert has_permission(sales, Permission.CUSTOMER_UPDATE)
    assert not has_permission(sales, Permission.CUSTOMER_DELETE)
    assert customer_visibility_for(sales) is CustomerVisibility.OWNED
    assert customer_visibility_for(viewer) is CustomerVisibility.ALL
    with pytest.raises(HTTPException) as exc_info:
        require_permission(viewer, Permission.CUSTOMER_CREATE)
    assert exc_info.value.status_code == 403


@pytest.mark.parametrize("host", ["127.0.0.1", "::1"])
def test_first_admin_bootstrap_accepts_only_loopback(host: str) -> None:
    local_request = Request({"type": "http", "client": (host, 12345)})
    remote_request = Request({"type": "http", "client": ("192.168.1.20", 12345)})
    assert is_loopback_request(local_request)
    assert not is_loopback_request(remote_request)


def test_pending_failure_exposes_domain_error_but_hides_sql() -> None:
    detail = safe_pending_failure_detail("联系人仍被转换、商机或活动引用，不能更换公司")
    assert "不能更换公司" in detail
    unsafe = safe_pending_failure_detail(
        "psycopg error SELECT password FROM users; token=secret-value"
    )
    assert unsafe == "待确认操作执行失败，请刷新数据后重新发起"
    assert "secret-value" not in unsafe


def test_postgres_operational_errors_have_a_service_unavailable_handler() -> None:
    app = create_app()

    assert OperationalError in app.exception_handlers
    assert PoolTimeout in app.exception_handlers


def test_pool_checks_connections_before_reuse_and_fails_fast() -> None:
    settings = Settings(_env_file=None)
    options = connection_pool_runtime_options(settings)

    assert options["check"] is AsyncConnectionPool.check_connection
    assert options["timeout"] == 8
