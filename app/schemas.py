from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator

CustomerStatus = Literal["new", "contacted", "qualified", "converted", "lost"]
UserRole = Literal["admin", "manager", "sales", "viewer"]


class RegisterRequest(BaseModel):
    username: str = Field(min_length=3, max_length=50, pattern=r"^[A-Za-z0-9_.-]+$")
    email: EmailStr
    display_name: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=10, max_length=256)

    @field_validator("username")
    @classmethod
    def normalize_username(cls, value: str) -> str:
        return value.strip().lower()


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=254)
    password: str = Field(min_length=1, max_length=256)


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    username: str
    email: str
    display_name: str
    role: UserRole
    is_active: bool
    created_at: datetime
    last_login_at: datetime | None = None


class UserRoleUpdate(BaseModel):
    role: UserRole


class CustomerCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=100)
    company: str | None = Field(default=None, max_length=255)
    title: str | None = Field(default=None, max_length=150)
    email: EmailStr | None = None
    phone: str | None = Field(default=None, max_length=50)
    status: CustomerStatus = "new"
    source: str | None = Field(default=None, max_length=100)
    notes: str | None = Field(default=None, max_length=10_000)
    extra: dict[str, Any] = Field(default_factory=dict)

    @field_validator("name")
    @classmethod
    def trim_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("name 不能为空")
        return normalized

    @field_validator("company", "title", "phone", "source", "notes")
    @classmethod
    def trim_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @model_validator(mode="after")
    def require_contact_method(self) -> "CustomerCreate":
        if not self.email and not self.phone:
            raise ValueError("email 和 phone 至少填写一个")
        return self


class CustomerUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=100)
    company: str | None = Field(default=None, max_length=255)
    title: str | None = Field(default=None, max_length=150)
    email: EmailStr | None = None
    phone: str | None = Field(default=None, max_length=50)
    status: CustomerStatus | None = None
    source: str | None = Field(default=None, max_length=100)
    notes: str | None = Field(default=None, max_length=10_000)
    extra: dict[str, Any] | None = None

    @field_validator("name")
    @classmethod
    def trim_updated_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("name 不能为空")
        return normalized

    @model_validator(mode="after")
    def keep_required_database_fields_non_null(self) -> "CustomerUpdate":
        for field_name in ("name", "status", "extra"):
            if field_name in self.model_fields_set and getattr(self, field_name) is None:
                raise ValueError(f"{field_name} 不允许设为 null")
        return self


class CustomerOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    company: str | None
    title: str | None
    email: str | None
    phone: str | None
    # Existing systems may have additional status labels. Writes remain constrained
    # by CustomerCreate/CustomerUpdate, while reads stay resilient to legacy values.
    status: str
    source: str | None
    notes: str | None
    extra: dict[str, Any]
    version: int
    owner_id: UUID | None
    owner_name: str | None = None
    created_at: datetime
    updated_at: datetime


class DashboardOut(BaseModel):
    total_customers: int = Field(ge=0)
    status_counts: dict[str, int]
    recent_customers: list[CustomerOut] = Field(max_length=5)
    total_users: int | None = Field(default=None, ge=0)
    entity_counts: dict[str, int] = Field(default_factory=dict)


class LeadCreate(BaseModel):
    """Writable columns of the real ``leads`` table; DB-generated fields are omitted."""

    model_config = ConfigDict(extra="forbid")

    first_name: str | None = Field(default=None, max_length=100)
    last_name: str | None = Field(default=None, max_length=100)
    company_name: str | None = Field(default=None, max_length=255)
    email: str | None = Field(default=None, max_length=255)
    phone: str | None = Field(default=None, max_length=50)
    job_title: str | None = Field(default=None, max_length=150)
    source: str | None = Field(default=None, max_length=100)
    status: str = Field(default="new", min_length=1, max_length=50)
    score: int | None = Field(default=0, ge=-2_147_483_648, le=2_147_483_647)
    owner_id: UUID | None = None
    description: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


class LeadUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    first_name: str | None = Field(default=None, max_length=100)
    last_name: str | None = Field(default=None, max_length=100)
    company_name: str | None = Field(default=None, max_length=255)
    email: str | None = Field(default=None, max_length=255)
    phone: str | None = Field(default=None, max_length=50)
    job_title: str | None = Field(default=None, max_length=150)
    source: str | None = Field(default=None, max_length=100)
    status: str | None = Field(default=None, min_length=1, max_length=50)
    score: int | None = Field(default=None, ge=-2_147_483_648, le=2_147_483_647)
    owner_id: UUID | None = None
    description: str | None = None
    extra: dict[str, Any] | None = None

    @model_validator(mode="after")
    def reject_null_required_columns(self) -> "LeadUpdate":
        for field_name in ("status", "extra"):
            if field_name in self.model_fields_set and getattr(self, field_name) is None:
                raise ValueError(f"{field_name} 不允许设为 null")
        return self


class LeadOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    first_name: str | None
    last_name: str | None
    company_name: str | None
    email: str | None
    phone: str | None
    job_title: str | None
    source: str | None
    status: str
    score: int | None
    owner_id: UUID | None
    description: str | None
    extra: dict[str, Any]
    version: int
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None


class AccountCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=255)
    industry: str | None = Field(default=None, max_length=100)
    website: str | None = Field(default=None, max_length=500)
    phone: str | None = Field(default=None, max_length=50)
    email: str | None = Field(default=None, max_length=255)
    address: str | None = None
    city: str | None = Field(default=None, max_length=100)
    state: str | None = Field(default=None, max_length=100)
    country: str | None = Field(default=None, max_length=100)
    employee_count: int | None = Field(default=None, ge=-2_147_483_648, le=2_147_483_647)
    annual_revenue: Decimal | None = Field(default=None, max_digits=18, decimal_places=2)
    status: str = Field(default="active", min_length=1, max_length=50)
    source: str | None = Field(default=None, max_length=100)
    owner_id: UUID | None = None
    description: str | None = None


class AccountUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=255)
    industry: str | None = Field(default=None, max_length=100)
    website: str | None = Field(default=None, max_length=500)
    phone: str | None = Field(default=None, max_length=50)
    email: str | None = Field(default=None, max_length=255)
    address: str | None = None
    city: str | None = Field(default=None, max_length=100)
    state: str | None = Field(default=None, max_length=100)
    country: str | None = Field(default=None, max_length=100)
    employee_count: int | None = Field(default=None, ge=-2_147_483_648, le=2_147_483_647)
    annual_revenue: Decimal | None = Field(default=None, max_digits=18, decimal_places=2)
    status: str | None = Field(default=None, min_length=1, max_length=50)
    source: str | None = Field(default=None, max_length=100)
    owner_id: UUID | None = None
    description: str | None = None

    @model_validator(mode="after")
    def reject_null_required_columns(self) -> "AccountUpdate":
        for field_name in ("name", "status"):
            if field_name in self.model_fields_set and getattr(self, field_name) is None:
                raise ValueError(f"{field_name} 不允许设为 null")
        return self


class AccountOut(AccountCreate):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None


class ContactCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    account_id: UUID | None = None
    first_name: str | None = Field(default=None, max_length=100)
    last_name: str | None = Field(default=None, max_length=100)
    title: str | None = Field(default=None, max_length=150)
    department: str | None = Field(default=None, max_length=150)
    email: str | None = Field(default=None, max_length=255)
    phone: str | None = Field(default=None, max_length=50)
    mobile: str | None = Field(default=None, max_length=50)
    wechat: str | None = Field(default=None, max_length=100)
    linkedin: str | None = Field(default=None, max_length=500)
    source: str | None = Field(default=None, max_length=100)
    owner_id: UUID | None = None
    description: str | None = None


class ContactUpdate(ContactCreate):
    pass


class ContactOut(ContactCreate):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None


class OpportunityCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    account_id: UUID | None = None
    primary_contact_id: UUID | None = None
    name: str = Field(min_length=1, max_length=255)
    amount: Decimal | None = Field(default=None, max_digits=18, decimal_places=2)
    currency: str = Field(default="CNY", min_length=1, max_length=10)
    stage: str = Field(default="prospecting", min_length=1, max_length=50)
    probability: Decimal | None = Field(default=Decimal("0"), max_digits=5, decimal_places=2)
    expected_close_date: date | None = None
    source: str | None = Field(default=None, max_length=100)
    owner_id: UUID | None = None
    description: str | None = None


class OpportunityUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    account_id: UUID | None = None
    primary_contact_id: UUID | None = None
    name: str | None = Field(default=None, min_length=1, max_length=255)
    amount: Decimal | None = Field(default=None, max_digits=18, decimal_places=2)
    currency: str | None = Field(default=None, min_length=1, max_length=10)
    stage: str | None = Field(default=None, min_length=1, max_length=50)
    probability: Decimal | None = Field(default=None, max_digits=5, decimal_places=2)
    expected_close_date: date | None = None
    source: str | None = Field(default=None, max_length=100)
    owner_id: UUID | None = None
    description: str | None = None

    @model_validator(mode="after")
    def reject_null_required_columns(self) -> "OpportunityUpdate":
        for field_name in ("name", "currency", "stage"):
            if field_name in self.model_fields_set and getattr(self, field_name) is None:
                raise ValueError(f"{field_name} 不允许设为 null")
        return self


class OpportunityOut(OpportunityCreate):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None


class ActivityCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: str = Field(min_length=1, max_length=50)
    subject: str = Field(min_length=1, max_length=255)
    description: str | None = None
    status: str = Field(default="planned", min_length=1, max_length=50)
    priority: str = Field(default="normal", min_length=1, max_length=50)
    start_at: datetime | None = None
    end_at: datetime | None = None
    account_id: UUID | None = None
    contact_id: UUID | None = None
    lead_id: UUID | None = None
    opportunity_id: UUID | None = None
    assigned_user_id: UUID | None = None

    @field_validator("start_at", "end_at")
    @classmethod
    def require_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("活动时间必须包含时区偏移")
        return value


class ActivityUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: str | None = Field(default=None, min_length=1, max_length=50)
    subject: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    status: str | None = Field(default=None, min_length=1, max_length=50)
    priority: str | None = Field(default=None, min_length=1, max_length=50)
    start_at: datetime | None = None
    end_at: datetime | None = None
    account_id: UUID | None = None
    contact_id: UUID | None = None
    lead_id: UUID | None = None
    opportunity_id: UUID | None = None
    assigned_user_id: UUID | None = None

    @field_validator("start_at", "end_at")
    @classmethod
    def require_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("活动时间必须包含时区偏移")
        return value

    @model_validator(mode="after")
    def reject_null_required_columns(self) -> "ActivityUpdate":
        for field_name in ("type", "subject", "status", "priority"):
            if field_name in self.model_fields_set and getattr(self, field_name) is None:
                raise ValueError(f"{field_name} 不允许设为 null")
        return self


class ActivityOut(ActivityCreate):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    created_at: datetime
    updated_at: datetime


class LeadConversionRequest(BaseModel):
    """Convert a lead by linking existing records or atomically creating new ones."""

    model_config = ConfigDict(extra="forbid")

    account_id: UUID | None = None
    account: AccountCreate | None = None
    contact_id: UUID | None = None
    contact: ContactCreate | None = None
    opportunity: OpportunityCreate | None = None
    expected_version: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def reject_ambiguous_targets(self) -> "LeadConversionRequest":
        if self.account_id and self.account:
            raise ValueError("account_id 和 account 不能同时提供")
        if self.contact_id and self.contact:
            raise ValueError("contact_id 和 contact 不能同时提供")
        if self.contact_id and not self.account_id:
            raise ValueError("链接现有联系人时必须同时提供其 account_id")
        return self


class LeadConversionRecordOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    lead_id: UUID
    account_id: UUID
    contact_id: UUID
    opportunity_id: UUID | None
    converted_by: UUID | None
    converted_at: datetime
    snapshot: dict[str, Any]


class LeadConversionOut(BaseModel):
    conversion: LeadConversionRecordOut
    lead: LeadOut
    account: AccountOut
    contact: ContactOut
    opportunity: OpportunityOut | None = None


class AccountOverviewOut(BaseModel):
    account: AccountOut
    contacts: list[ContactOut]
    opportunities: list[OpportunityOut]
    activities: list[ActivityOut]
    conversion_sources: list[LeadConversionRecordOut]
    totals: dict[str, int] = Field(default_factory=dict)
    truncated: dict[str, bool] = Field(default_factory=dict)


class AccountChainTransferRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    new_owner_id: UUID


class AccountChainTransferOut(BaseModel):
    account_id: UUID
    previous_owner_id: UUID | None
    new_owner_id: UUID
    contacts_updated: int = Field(ge=0)
    opportunities_updated: int = Field(ge=0)
    leads_updated: int = Field(ge=0)
    activities_updated: int = Field(ge=0)


class ConversationMemoryCreate(BaseModel):
    content: str = Field(min_length=1, max_length=10_000)
    memory_type: str = Field(default="fact", min_length=1, max_length=50)
    importance: int = Field(default=3, ge=1, le=5)


class ConversationMemoryOut(ConversationMemoryCreate):
    id: UUID
    conversation_id: UUID
    created_at: datetime
    updated_at: datetime


class ConversationCreate(BaseModel):
    title: str = Field(default="新会话", min_length=1, max_length=120)


class ConversationUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=120)
    is_archived: bool | None = None


class ConversationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    title: str
    message_count: int
    is_archived: bool
    created_at: datetime
    updated_at: datetime
    last_message_at: datetime | None


class ConversationMessageOut(BaseModel):
    id: str
    role: Literal["user", "assistant"]
    content: str


class PendingActionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    conversation_id: UUID
    action_type: Literal[
        "insert_customer",
        "update_customer",
        "insert_lead",
        "update_lead",
        "insert_account",
        "update_account",
        "insert_contact",
        "update_contact",
        "insert_opportunity",
        "update_opportunity",
        "insert_activity",
        "update_activity",
        "convert_lead",
    ]
    payload: dict[str, Any]
    status: Literal["pending", "approved", "rejected", "expired", "failed"]
    result: dict[str, Any] | None = None
    error_message: str | None = None
    created_at: datetime
    expires_at: datetime
    decided_at: datetime | None = None


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=1, max_length=20_000)
    thread_id: UUID | None = None


class ChatResponse(BaseModel):
    thread_id: UUID
    answer: str
    pending_actions: list[PendingActionOut] = Field(default_factory=list)
