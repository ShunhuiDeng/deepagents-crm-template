from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, TypeVar
from uuid import UUID

from langchain.tools import ToolRuntime, tool
from langchain_core.tools import BaseTool
from pydantic import BaseModel

from app.agents.context import CRMContext
from app.database import CRMRepository
from app.permissions import Permission, has_permission
from app.schemas import (
    AccountCreate,
    AccountUpdate,
    ActivityCreate,
    ActivityUpdate,
    ContactCreate,
    ContactUpdate,
    LeadConversionRequest,
    LeadCreate,
    LeadUpdate,
    OpportunityCreate,
    OpportunityUpdate,
)

ModelT = TypeVar("ModelT", bound=BaseModel)


def _dump(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return value


def _uuid(value: str | UUID, field_name: str) -> UUID:
    try:
        return value if isinstance(value, UUID) else UUID(value)
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} 不是有效 UUID") from exc


def _optional_uuid(value: str | UUID | None, field_name: str) -> UUID | None:
    return None if value is None else _uuid(value, field_name)


def _bounded_limit(limit: int) -> int:
    return min(max(limit, 1), 50)


def _validated_update(
    model_type: type[ModelT],
    fields: dict[str, Any],
    *,
    forbidden_fields: frozenset[str],
) -> ModelT:
    forbidden = sorted(forbidden_fields.intersection(fields))
    if forbidden:
        raise ValueError(f"Agent 不允许设置数据归属字段: {', '.join(forbidden)}")
    payload = model_type.model_validate(fields)
    if not payload.model_fields_set:
        raise ValueError("至少提供一个需要更新的字段")
    return payload


def _validated_nested_create(
    model_type: type[ModelT],
    fields: dict[str, Any] | None,
    *,
    label: str,
) -> ModelT | None:
    if fields is None:
        return None
    if "owner_id" in fields or "assigned_user_id" in fields:
        raise ValueError(f"Agent 不允许在{label}中设置数据归属字段")
    return model_type.model_validate(fields)


def _selected(entity_key: str, records: list[Any] | tuple[Any, ...]) -> dict[str, Any]:
    return {
        "ok": True,
        "count": len(records),
        entity_key: [_dump(record) for record in records],
    }


def _permission_error(operation: str) -> dict[str, Any]:
    return {"ok": False, "error": f"当前账号没有{operation} CRM 数据的权限"}


def _pending_result(action: Any, entity_label: str, operation: str) -> dict[str, Any]:
    return {
        "ok": True,
        "requires_approval": True,
        "pending_action": _dump(action),
        "message": f"{entity_label}尚未{operation}；等待当前登录用户在界面确认。",
    }


def build_crud_tools(repository: CRMRepository) -> tuple[BaseTool, ...]:
    """Build the only data subagent's five-entity select/insert/update tools."""

    @tool
    async def select_leads(
        runtime: ToolRuntime[CRMContext],
        lead_id: str | None = None,
        query: str | None = None,
        status: str | None = None,
        limit: int = 10,
    ) -> dict[str, Any]:
        """按 UUID 读取一条可见线索，或按姓名、公司、邮箱、电话搜索线索。"""
        user = runtime.context.current_user()
        if not has_permission(user, Permission.CUSTOMER_READ):
            return _permission_error("读取")
        try:
            if lead_id:
                record = await repository.get_lead(user, _uuid(lead_id, "lead_id"))
                return _selected("leads", [record] if record else [])
            records = await repository.list_leads(
                user,
                query=query,
                status=status,
                limit=_bounded_limit(limit),
            )
            return _selected("leads", records)
        except (TypeError, ValueError) as exc:
            return {"ok": False, "error": str(exc)}

    @tool
    async def insert_lead(
        runtime: ToolRuntime[CRMContext],
        first_name: str | None = None,
        last_name: str | None = None,
        company_name: str | None = None,
        email: str | None = None,
        phone: str | None = None,
        job_title: str | None = None,
        source: str | None = None,
        status: str = "new",
        score: int | None = 0,
        description: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """用 leads 表真实字段发起线索录入，等待人工确认；不直接写数据库。"""
        user = runtime.context.current_user()
        if not has_permission(user, Permission.CUSTOMER_CREATE):
            return _permission_error("新增")
        try:
            payload = LeadCreate(
                first_name=first_name,
                last_name=last_name,
                company_name=company_name,
                email=email,
                phone=phone,
                job_title=job_title,
                source=source,
                status=status,
                score=score,
                description=description,
                extra=extra or {},
            )
            action = await repository.create_pending_entity_insert(
                user, runtime.context.conversation_id, "lead", payload
            )
            return _pending_result(action, "线索", "录入")
        except (TypeError, ValueError) as exc:
            return {"ok": False, "error": str(exc)}

    @tool
    async def update_lead(
        lead_id: str,
        fields: dict[str, Any],
        runtime: ToolRuntime[CRMContext],
    ) -> dict[str, Any]:
        """发起线索更新。fields 只可含 leads 表可写字段，但不可含 owner_id。"""
        user = runtime.context.current_user()
        if not has_permission(user, Permission.CUSTOMER_UPDATE):
            return _permission_error("更新")
        try:
            entity_id = _uuid(lead_id, "lead_id")
            payload = _validated_update(
                LeadUpdate, fields, forbidden_fields=frozenset({"owner_id"})
            )
            action = await repository.create_pending_entity_update(
                user,
                runtime.context.conversation_id,
                "lead",
                entity_id,
                payload,
            )
            return _pending_result(action, "线索", "更新")
        except (TypeError, ValueError) as exc:
            return {"ok": False, "error": str(exc)}

    @tool
    async def convert_lead(
        lead_id: str,
        runtime: ToolRuntime[CRMContext],
        account_id: str | None = None,
        account: dict[str, Any] | None = None,
        contact_id: str | None = None,
        contact: dict[str, Any] | None = None,
        opportunity: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """发起线索一键转换，只产生一个待确认动作。

        account_id/account 与 contact_id/contact 分别互斥；都不提供时，后端从
        线索原始信息生成新公司和新联系人。opportunity 可选，用真实商机字段。
        """
        user = runtime.context.current_user()
        if not has_permission(user, Permission.CUSTOMER_CREATE) or not has_permission(
            user, Permission.CUSTOMER_UPDATE
        ):
            return _permission_error("转换")
        try:
            payload = LeadConversionRequest(
                account_id=_optional_uuid(account_id, "account_id"),
                account=_validated_nested_create(AccountCreate, account, label="新公司"),
                contact_id=_optional_uuid(contact_id, "contact_id"),
                contact=_validated_nested_create(ContactCreate, contact, label="新联系人"),
                opportunity=_validated_nested_create(
                    OpportunityCreate, opportunity, label="新商机"
                ),
            )
            action = await repository.create_pending_lead_conversion(
                user,
                runtime.context.conversation_id,
                _uuid(lead_id, "lead_id"),
                payload,
            )
            return {
                "ok": True,
                "requires_approval": True,
                "pending_action": _dump(action),
                "message": "线索尚未转换；一个待确认动作将在批准后原子完成全部转换。",
            }
        except (TypeError, ValueError) as exc:
            return {"ok": False, "error": str(exc)}

    @tool
    async def select_accounts(
        runtime: ToolRuntime[CRMContext],
        account_id: str | None = None,
        query: str | None = None,
        status: str | None = None,
        limit: int = 10,
    ) -> dict[str, Any]:
        """按 UUID 读取一条可见公司，或按名称、行业、网站、邮箱、电话搜索公司。"""
        user = runtime.context.current_user()
        if not has_permission(user, Permission.CUSTOMER_READ):
            return _permission_error("读取")
        try:
            if account_id:
                record = await repository.get_account(user, _uuid(account_id, "account_id"))
                return _selected("accounts", [record] if record else [])
            records = await repository.list_accounts(
                user,
                query=query,
                status=status,
                limit=_bounded_limit(limit),
            )
            return _selected("accounts", records)
        except (TypeError, ValueError) as exc:
            return {"ok": False, "error": str(exc)}

    @tool
    async def select_account_overview(
        account_id: str,
        runtime: ToolRuntime[CRMContext],
    ) -> dict[str, Any]:
        """读取一个可见公司的完整总览：公司、联系人、商机、活动和线索转换来源。"""
        user = runtime.context.current_user()
        if not has_permission(user, Permission.CUSTOMER_READ):
            return _permission_error("读取")
        try:
            overview = await repository.get_account_overview(
                user, _uuid(account_id, "account_id")
            )
            if overview is None:
                return {"ok": False, "error": "公司不存在或当前账号无权访问"}
            return {"ok": True, "overview": _dump(overview)}
        except (TypeError, ValueError) as exc:
            return {"ok": False, "error": str(exc)}

    @tool
    async def insert_account(
        name: str,
        runtime: ToolRuntime[CRMContext],
        industry: str | None = None,
        website: str | None = None,
        phone: str | None = None,
        email: str | None = None,
        address: str | None = None,
        city: str | None = None,
        state: str | None = None,
        country: str | None = None,
        employee_count: int | None = None,
        annual_revenue: Decimal | None = None,
        status: str = "active",
        source: str | None = None,
        description: str | None = None,
    ) -> dict[str, Any]:
        """用 accounts 表真实字段发起公司录入，等待人工确认；不直接写数据库。"""
        user = runtime.context.current_user()
        if not has_permission(user, Permission.CUSTOMER_CREATE):
            return _permission_error("新增")
        try:
            payload = AccountCreate(
                name=name,
                industry=industry,
                website=website,
                phone=phone,
                email=email,
                address=address,
                city=city,
                state=state,
                country=country,
                employee_count=employee_count,
                annual_revenue=annual_revenue,
                status=status,
                source=source,
                description=description,
            )
            action = await repository.create_pending_entity_insert(
                user, runtime.context.conversation_id, "account", payload
            )
            return _pending_result(action, "公司", "录入")
        except (TypeError, ValueError) as exc:
            return {"ok": False, "error": str(exc)}

    @tool
    async def update_account(
        account_id: str,
        fields: dict[str, Any],
        runtime: ToolRuntime[CRMContext],
    ) -> dict[str, Any]:
        """发起公司更新。fields 只可含 accounts 表可写字段，但不可含 owner_id。"""
        user = runtime.context.current_user()
        if not has_permission(user, Permission.CUSTOMER_UPDATE):
            return _permission_error("更新")
        try:
            entity_id = _uuid(account_id, "account_id")
            payload = _validated_update(
                AccountUpdate, fields, forbidden_fields=frozenset({"owner_id"})
            )
            action = await repository.create_pending_entity_update(
                user,
                runtime.context.conversation_id,
                "account",
                entity_id,
                payload,
            )
            return _pending_result(action, "公司", "更新")
        except (TypeError, ValueError) as exc:
            return {"ok": False, "error": str(exc)}

    @tool
    async def select_contacts(
        runtime: ToolRuntime[CRMContext],
        contact_id: str | None = None,
        query: str | None = None,
        account_id: str | None = None,
        limit: int = 10,
    ) -> dict[str, Any]:
        """按 UUID 读取一条可见联系人，或按姓名、邮箱、电话、微信搜索联系人。"""
        user = runtime.context.current_user()
        if not has_permission(user, Permission.CUSTOMER_READ):
            return _permission_error("读取")
        try:
            if contact_id:
                record = await repository.get_contact(user, _uuid(contact_id, "contact_id"))
                return _selected("contacts", [record] if record else [])
            records = await repository.list_contacts(
                user,
                query=query,
                account_id=_optional_uuid(account_id, "account_id"),
                limit=_bounded_limit(limit),
            )
            return _selected("contacts", records)
        except (TypeError, ValueError) as exc:
            return {"ok": False, "error": str(exc)}

    @tool
    async def insert_contact(
        runtime: ToolRuntime[CRMContext],
        account_id: str | None = None,
        first_name: str | None = None,
        last_name: str | None = None,
        title: str | None = None,
        department: str | None = None,
        email: str | None = None,
        phone: str | None = None,
        mobile: str | None = None,
        wechat: str | None = None,
        linkedin: str | None = None,
        source: str | None = None,
        description: str | None = None,
    ) -> dict[str, Any]:
        """用 contacts 表真实字段发起联系人录入，等待人工确认；不直接写数据库。"""
        user = runtime.context.current_user()
        if not has_permission(user, Permission.CUSTOMER_CREATE):
            return _permission_error("新增")
        try:
            payload = ContactCreate(
                account_id=_optional_uuid(account_id, "account_id"),
                first_name=first_name,
                last_name=last_name,
                title=title,
                department=department,
                email=email,
                phone=phone,
                mobile=mobile,
                wechat=wechat,
                linkedin=linkedin,
                source=source,
                description=description,
            )
            action = await repository.create_pending_entity_insert(
                user, runtime.context.conversation_id, "contact", payload
            )
            return _pending_result(action, "联系人", "录入")
        except (TypeError, ValueError) as exc:
            return {"ok": False, "error": str(exc)}

    @tool
    async def update_contact(
        contact_id: str,
        fields: dict[str, Any],
        runtime: ToolRuntime[CRMContext],
    ) -> dict[str, Any]:
        """发起联系人更新。fields 只可含 contacts 表可写字段，但不可含 owner_id。"""
        user = runtime.context.current_user()
        if not has_permission(user, Permission.CUSTOMER_UPDATE):
            return _permission_error("更新")
        try:
            entity_id = _uuid(contact_id, "contact_id")
            payload = _validated_update(
                ContactUpdate, fields, forbidden_fields=frozenset({"owner_id"})
            )
            action = await repository.create_pending_entity_update(
                user,
                runtime.context.conversation_id,
                "contact",
                entity_id,
                payload,
            )
            return _pending_result(action, "联系人", "更新")
        except (TypeError, ValueError) as exc:
            return {"ok": False, "error": str(exc)}

    @tool
    async def select_opportunities(
        runtime: ToolRuntime[CRMContext],
        opportunity_id: str | None = None,
        query: str | None = None,
        stage: str | None = None,
        account_id: str | None = None,
        limit: int = 10,
    ) -> dict[str, Any]:
        """按 UUID 读取一条可见商机，或按名称、阶段及所属公司搜索商机。"""
        user = runtime.context.current_user()
        if not has_permission(user, Permission.CUSTOMER_READ):
            return _permission_error("读取")
        try:
            if opportunity_id:
                record = await repository.get_opportunity(
                    user, _uuid(opportunity_id, "opportunity_id")
                )
                return _selected("opportunities", [record] if record else [])
            records = await repository.list_opportunities(
                user,
                query=query,
                stage=stage,
                account_id=_optional_uuid(account_id, "account_id"),
                limit=_bounded_limit(limit),
            )
            return _selected("opportunities", records)
        except (TypeError, ValueError) as exc:
            return {"ok": False, "error": str(exc)}

    @tool
    async def insert_opportunity(
        name: str,
        runtime: ToolRuntime[CRMContext],
        account_id: str | None = None,
        primary_contact_id: str | None = None,
        amount: Decimal | None = None,
        currency: str = "CNY",
        stage: str = "prospecting",
        probability: Decimal | None = Decimal("0"),
        expected_close_date: date | None = None,
        source: str | None = None,
        description: str | None = None,
    ) -> dict[str, Any]:
        """用 opportunities 表真实字段发起商机录入，等待人工确认；不直接写数据库。"""
        user = runtime.context.current_user()
        if not has_permission(user, Permission.CUSTOMER_CREATE):
            return _permission_error("新增")
        try:
            payload = OpportunityCreate(
                account_id=_optional_uuid(account_id, "account_id"),
                primary_contact_id=_optional_uuid(
                    primary_contact_id, "primary_contact_id"
                ),
                name=name,
                amount=amount,
                currency=currency,
                stage=stage,
                probability=probability,
                expected_close_date=expected_close_date,
                source=source,
                description=description,
            )
            action = await repository.create_pending_entity_insert(
                user, runtime.context.conversation_id, "opportunity", payload
            )
            return _pending_result(action, "商机", "录入")
        except (TypeError, ValueError) as exc:
            return {"ok": False, "error": str(exc)}

    @tool
    async def update_opportunity(
        opportunity_id: str,
        fields: dict[str, Any],
        runtime: ToolRuntime[CRMContext],
    ) -> dict[str, Any]:
        """发起商机更新。fields 只可含 opportunities 表可写字段，但不可含 owner_id。"""
        user = runtime.context.current_user()
        if not has_permission(user, Permission.CUSTOMER_UPDATE):
            return _permission_error("更新")
        try:
            entity_id = _uuid(opportunity_id, "opportunity_id")
            payload = _validated_update(
                OpportunityUpdate, fields, forbidden_fields=frozenset({"owner_id"})
            )
            action = await repository.create_pending_entity_update(
                user,
                runtime.context.conversation_id,
                "opportunity",
                entity_id,
                payload,
            )
            return _pending_result(action, "商机", "更新")
        except (TypeError, ValueError) as exc:
            return {"ok": False, "error": str(exc)}

    @tool
    async def select_activities(
        runtime: ToolRuntime[CRMContext],
        activity_id: str | None = None,
        query: str | None = None,
        status: str | None = None,
        account_id: str | None = None,
        contact_id: str | None = None,
        lead_id: str | None = None,
        opportunity_id: str | None = None,
        limit: int = 10,
    ) -> dict[str, Any]:
        """按 UUID 读取活动，或按主题、状态及关联的公司/联系人/线索/商机筛选。"""
        user = runtime.context.current_user()
        if not has_permission(user, Permission.CUSTOMER_READ):
            return _permission_error("读取")
        try:
            if activity_id:
                record = await repository.get_activity(
                    user, _uuid(activity_id, "activity_id")
                )
                return _selected("activities", [record] if record else [])
            records = await repository.list_activities(
                user,
                query=query,
                status=status,
                account_id=_optional_uuid(account_id, "account_id"),
                contact_id=_optional_uuid(contact_id, "contact_id"),
                lead_id=_optional_uuid(lead_id, "lead_id"),
                opportunity_id=_optional_uuid(opportunity_id, "opportunity_id"),
                limit=_bounded_limit(limit),
            )
            return _selected("activities", records)
        except (TypeError, ValueError) as exc:
            return {"ok": False, "error": str(exc)}

    @tool
    async def insert_activity(
        type: str,
        subject: str,
        runtime: ToolRuntime[CRMContext],
        description: str | None = None,
        status: str = "planned",
        priority: str = "normal",
        start_at: datetime | None = None,
        end_at: datetime | None = None,
        account_id: str | None = None,
        contact_id: str | None = None,
        lead_id: str | None = None,
        opportunity_id: str | None = None,
    ) -> dict[str, Any]:
        """用 activities 表真实字段发起活动录入，等待人工确认；不直接写数据库。"""
        user = runtime.context.current_user()
        if not has_permission(user, Permission.CUSTOMER_CREATE):
            return _permission_error("新增")
        try:
            payload = ActivityCreate(
                type=type,
                subject=subject,
                description=description,
                status=status,
                priority=priority,
                start_at=start_at,
                end_at=end_at,
                account_id=_optional_uuid(account_id, "account_id"),
                contact_id=_optional_uuid(contact_id, "contact_id"),
                lead_id=_optional_uuid(lead_id, "lead_id"),
                opportunity_id=_optional_uuid(opportunity_id, "opportunity_id"),
            )
            action = await repository.create_pending_entity_insert(
                user, runtime.context.conversation_id, "activity", payload
            )
            return _pending_result(action, "活动", "录入")
        except (TypeError, ValueError) as exc:
            return {"ok": False, "error": str(exc)}

    @tool
    async def update_activity(
        activity_id: str,
        fields: dict[str, Any],
        runtime: ToolRuntime[CRMContext],
    ) -> dict[str, Any]:
        """发起活动更新。fields 只可含 activities 表可写字段，但不可含 assigned_user_id。"""
        user = runtime.context.current_user()
        if not has_permission(user, Permission.CUSTOMER_UPDATE):
            return _permission_error("更新")
        try:
            entity_id = _uuid(activity_id, "activity_id")
            payload = _validated_update(
                ActivityUpdate,
                fields,
                forbidden_fields=frozenset({"assigned_user_id"}),
            )
            action = await repository.create_pending_entity_update(
                user,
                runtime.context.conversation_id,
                "activity",
                entity_id,
                payload,
            )
            return _pending_result(action, "活动", "更新")
        except (TypeError, ValueError) as exc:
            return {"ok": False, "error": str(exc)}

    return (
        select_leads,
        insert_lead,
        update_lead,
        convert_lead,
        select_accounts,
        select_account_overview,
        insert_account,
        update_account,
        select_contacts,
        insert_contact,
        update_contact,
        select_opportunities,
        insert_opportunity,
        update_opportunity,
        select_activities,
        insert_activity,
        update_activity,
    )
