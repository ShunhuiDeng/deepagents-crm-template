from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.database import ENTITY_SPECS, CRMRepository, InvalidActionError
from app.permissions import CurrentUser, Role
from app.schemas import (
    AccountCreate,
    ActivityCreate,
    ActivityUpdate,
    ContactCreate,
    LeadConversionRequest,
    LeadCreate,
    LeadOut,
    LeadUpdate,
    OpportunityCreate,
)


def user(role: Role) -> CurrentUser:
    return CurrentUser(
        id=uuid4(),
        username=role.value,
        email=f"{role.value}@example.com",
        display_name=role.value,
        role=role,
        is_active=True,
    )


class Result:
    def __init__(self, one=None):  # type: ignore[no-untyped-def]
        self.one = one

    async def fetchone(self):  # type: ignore[no-untyped-def]
        return self.one


class SequenceConnection:
    def __init__(self, values):  # type: ignore[no-untyped-def]
        self.values = iter(values)
        self.calls = []

    async def execute(self, statement, params=None):  # type: ignore[no-untyped-def]
        self.calls.append((statement, params))
        return Result(next(self.values))


def test_conversion_schema_rejects_existing_contact_without_account() -> None:
    with pytest.raises(ValueError, match="必须同时提供其 account_id"):
        LeadConversionRequest(contact_id=uuid4())


async def test_generic_lead_insert_cannot_claim_converted() -> None:
    repo = CRMRepository(None)  # type: ignore[arg-type]
    with pytest.raises(InvalidActionError, match="正式转换流程"):
        await repo._create_entity_conn(  # type: ignore[arg-type]
            None,
            user(Role.MANAGER),
            ENTITY_SPECS["leads"],
            LeadCreate(first_name="测试", status="converted"),
            object,
        )


async def test_converted_lead_allows_noop_status_with_other_edits() -> None:
    manager = user(Role.MANAGER)
    now = datetime.now(UTC)
    record = {
        "id": uuid4(),
        "first_name": "原姓名",
        "last_name": None,
        "company_name": "示例公司",
        "email": None,
        "phone": None,
        "job_title": None,
        "source": None,
        "status": "converted",
        "score": 0,
        "owner_id": manager.id,
        "description": None,
        "extra": {},
        "version": 2,
        "created_at": now,
        "updated_at": now,
        "deleted_at": None,
    }
    after = {**record, "description": "跟进说明", "version": 3}
    connection = SequenceConnection([record, {"id": manager.id}, after])
    repo = CRMRepository(None)  # type: ignore[arg-type]
    repo._write_audit = _noop_audit  # type: ignore[method-assign]

    result = await repo._update_entity_conn(
        connection,
        manager,
        ENTITY_SPECS["leads"],
        record["id"],
        LeadUpdate(description="跟进说明", status="converted"),
        LeadOut,
    )

    assert result and result.description == "跟进说明"
    update_statement, update_params = connection.calls[-1]
    rendered = update_statement.as_string()
    assert '"description" = %s' in rendered
    assert '"status" = %s' not in rendered
    assert rendered.count("%s") == len(update_params) == 2
    assert update_params == ["跟进说明", record["id"]]


async def _noop_audit(*_args, **_kwargs) -> None:  # type: ignore[no-untyped-def]
    return None


async def test_viewer_cannot_convert_lead() -> None:
    repo = CRMRepository(None)  # type: ignore[arg-type]
    with pytest.raises(HTTPException) as exc:
        await repo.convert_lead(user(Role.VIEWER), uuid4(), LeadConversionRequest())
    assert exc.value.status_code == 403


async def test_all_roles_inherit_contact_owner_from_account() -> None:
    manager = user(Role.MANAGER)
    owner_id = uuid4()
    account_id = uuid4()
    connection = SequenceConnection([{"owner_id": owner_id}])
    values = {"account_id": account_id, "first_name": "联系人"}

    result = await CRMRepository(None)._inherit_and_validate_relationship_owners(  # type: ignore[arg-type]
        connection, ENTITY_SPECS["contacts"], values, creating=True
    )

    assert result["owner_id"] == owner_id
    assert manager.role is Role.MANAGER


async def test_activity_rejects_lead_plus_relations_without_conversion() -> None:
    lead_id = uuid4()
    account_id = uuid4()
    connection = SequenceConnection(
        [
            None,
        ]
    )
    repo = CRMRepository(None)  # type: ignore[arg-type]

    with pytest.raises(InvalidActionError, match="尚未正式转换"):
        await repo._validate_entity_consistency(
            connection,
            ENTITY_SPECS["activities"],
            {"lead_id": lead_id, "account_id": account_id},
        )


async def test_activity_rejects_wrong_conversion_account() -> None:
    connection = SequenceConnection(
        [
            {
                "account_id": uuid4(),
                "contact_id": uuid4(),
                "opportunity_id": None,
            }
        ]
    )
    repo = CRMRepository(None)  # type: ignore[arg-type]

    with pytest.raises(InvalidActionError, match="公司与线索转换结果不一致"):
        await repo._validate_entity_consistency(
            connection,
            ENTITY_SPECS["activities"],
            {"lead_id": uuid4(), "account_id": uuid4()},
        )


async def test_activity_accepts_second_contact_in_converted_account() -> None:
    account_id = uuid4()
    connection = SequenceConnection(
        [
            {"account_id": account_id},
            {
                "account_id": account_id,
                "contact_id": uuid4(),
                "opportunity_id": None,
            },
        ]
    )
    await CRMRepository(None)._validate_entity_consistency(  # type: ignore[arg-type]
        connection,
        ENTITY_SPECS["activities"],
        {"lead_id": uuid4(), "contact_id": uuid4()},
    )


async def test_opportunity_primary_contact_must_match_account() -> None:
    connection = SequenceConnection([{"account_id": uuid4()}])
    repo = CRMRepository(None)  # type: ignore[arg-type]

    with pytest.raises(InvalidActionError, match="必须属于同一公司"):
        await repo._validate_entity_consistency(
            connection,
            ENTITY_SPECS["opportunities"],
            {"account_id": uuid4(), "primary_contact_id": uuid4()},
        )


async def test_conversion_rejects_stale_version_before_writes() -> None:
    owner = user(Role.SALES)
    now = datetime.now(UTC)
    lead = {
        "id": uuid4(),
        "first_name": "测试",
        "last_name": None,
        "company_name": "示例公司",
        "email": None,
        "phone": None,
        "job_title": None,
        "source": None,
        "status": "qualified",
        "score": 0,
        "owner_id": owner.id,
        "description": None,
        "extra": {},
        "version": 2,
        "created_at": now,
        "updated_at": now,
        "deleted_at": None,
    }
    connection = SequenceConnection([lead])
    repo = CRMRepository(None)  # type: ignore[arg-type]

    with pytest.raises(InvalidActionError, match="已被其他操作更新"):
        await repo._convert_lead_conn(
            connection,
            owner,
            lead["id"],
            LeadConversionRequest(account=AccountCreate(name="示例公司")),
            expected_version=1,
        )


async def test_sales_conversion_target_owner_must_match_lead() -> None:
    lead_owner = uuid4()
    with pytest.raises(InvalidActionError, match="负责人必须与线索负责人一致"):
        CRMRepository._require_matching_owner(lead_owner, uuid4(), "公司")


def test_new_opportunity_contract_contains_primary_contact() -> None:
    contact_id = uuid4()
    opportunity = OpportunityCreate(name="采购", primary_contact_id=contact_id)
    assert opportunity.primary_contact_id == contact_id


def test_activity_contract_supports_all_relationships() -> None:
    activity = ActivityCreate(
        type="meeting",
        subject="需求沟通",
        account_id=uuid4(),
        contact_id=uuid4(),
        lead_id=uuid4(),
        opportunity_id=uuid4(),
    )
    assert activity.account_id and activity.contact_id


@pytest.mark.parametrize("model", [ActivityCreate, ActivityUpdate])
@pytest.mark.parametrize("field", ["start_at", "end_at"])
def test_activity_contract_rejects_datetime_without_timezone(model, field: str) -> None:  # type: ignore[no-untyped-def]
    values = {field: "2026-08-13T10:00:00"}
    if model is ActivityCreate:
        values.update(type="meeting", subject="时区测试")

    with pytest.raises(ValueError, match="必须包含时区偏移"):
        model.model_validate(values)


@pytest.mark.parametrize("model", [ActivityCreate, ActivityUpdate])
def test_activity_contract_accepts_offset_or_utc_datetime(model) -> None:  # type: ignore[no-untyped-def]
    values = {"start_at": "2026-08-13T10:00:00+08:00", "end_at": "2026-08-13T03:00:00Z"}
    if model is ActivityCreate:
        values.update(type="meeting", subject="时区测试")

    activity = model.model_validate(values)
    assert activity.start_at and activity.start_at.utcoffset() is not None
    assert activity.end_at and activity.end_at.utcoffset() is not None


async def test_activity_time_order_is_checked_after_partial_update_hydration() -> None:
    start_at = datetime(2026, 8, 13, 10, tzinfo=UTC)
    connection = SequenceConnection(
        [
            {
                "start_at": start_at,
                "account_id": None,
                "contact_id": None,
                "lead_id": None,
                "opportunity_id": None,
            }
        ]
    )
    repo = CRMRepository(None)  # type: ignore[arg-type]

    hydrated = await repo._hydrate_relationship_values(
        connection,
        ENTITY_SPECS["activities"],
        uuid4(),
        {"end_at": start_at - timedelta(minutes=1)},
    )
    with pytest.raises(InvalidActionError, match="结束时间不能早于开始时间"):
        await repo._validate_entity_consistency(
            connection,
            ENTITY_SPECS["activities"],
            hydrated,
        )


def test_new_contact_contract_can_inherit_owner() -> None:
    contact = ContactCreate(first_name="王")
    assert contact.owner_id is None
