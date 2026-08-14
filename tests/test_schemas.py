import pytest
from pydantic import ValidationError

from app.schemas import (
    ChatRequest,
    ConversationCreate,
    CustomerCreate,
    CustomerUpdate,
    DashboardOut,
)


def test_customer_requires_email_or_phone() -> None:
    with pytest.raises(ValidationError, match="email 和 phone 至少填写一个"):
        CustomerCreate(name="王小明")


def test_customer_accepts_phone() -> None:
    customer = CustomerCreate(name="王小明", phone="13800138000")
    assert customer.status == "new"


def test_patch_preserves_explicit_null() -> None:
    patch = CustomerUpdate(company=None)
    assert patch.model_dump(exclude_unset=True) == {"company": None}


def test_patch_rejects_unknown_database_field_names() -> None:
    with pytest.raises(ValidationError):
        CustomerUpdate(description="应使用 notes")


@pytest.mark.parametrize("field", ["name", "status", "extra"])
def test_patch_rejects_null_for_required_database_fields(field: str) -> None:
    with pytest.raises(ValidationError, match="不允许设为 null"):
        CustomerUpdate.model_validate({field: None})


def test_customer_output_accepts_legacy_status() -> None:
    from datetime import UTC, datetime
    from uuid import uuid4

    from app.schemas import CustomerOut

    now = datetime.now(UTC)
    customer = CustomerOut(
        id=uuid4(),
        name="Legacy lead",
        company=None,
        title=None,
        email=None,
        phone="13800138000",
        status="nurturing",
        source=None,
        notes=None,
        extra={},
        version=1,
        owner_id=uuid4(),
        created_at=now,
        updated_at=now,
    )
    assert customer.status == "nurturing"


def test_customer_output_accepts_unassigned_legacy_lead() -> None:
    from datetime import UTC, datetime
    from uuid import uuid4

    from app.schemas import CustomerOut

    now = datetime.now(UTC)
    customer = CustomerOut(
        id=uuid4(),
        name="Unassigned lead",
        company=None,
        title=None,
        email=None,
        phone="13800138000",
        status="new",
        source=None,
        notes=None,
        extra={},
        version=1,
        owner_id=None,
        created_at=now,
        updated_at=now,
    )
    assert customer.owner_id is None


def test_conversation_defaults_to_new_title() -> None:
    assert ConversationCreate().title == "新会话"


def test_chat_thread_id_must_be_uuid() -> None:
    with pytest.raises(ValidationError):
        ChatRequest(message="你好", thread_id="not-a-uuid")


def test_chat_rejects_client_supplied_user_identity() -> None:
    with pytest.raises(ValidationError):
        ChatRequest(message="你好", user_id="someone-else")


def test_dashboard_rejects_more_than_five_recent_customers() -> None:
    from datetime import UTC, datetime
    from uuid import uuid4

    now = datetime.now(UTC)
    customer = {
        "id": uuid4(),
        "name": "测试客户",
        "company": None,
        "title": None,
        "email": None,
        "phone": "13800138000",
        "status": "new",
        "source": None,
        "notes": None,
        "extra": {},
        "version": 1,
        "owner_id": uuid4(),
        "created_at": now,
        "updated_at": now,
    }
    with pytest.raises(ValidationError):
        DashboardOut(
            total_customers=6,
            status_counts={"new": 6},
            recent_customers=[customer] * 6,
        )
