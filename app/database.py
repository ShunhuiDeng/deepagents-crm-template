from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, TypeVar
from uuid import UUID, uuid4

from psycopg import AsyncConnection, sql
from psycopg.errors import UniqueViolation
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool
from pydantic import BaseModel

from app.migrations import run_migrations
from app.permissions import (
    CurrentUser,
    CustomerVisibility,
    Permission,
    Role,
    customer_visibility_for,
    require_permission,
)
from app.schemas import (
    AccountChainTransferOut,
    AccountCreate,
    AccountOut,
    AccountOverviewOut,
    AccountUpdate,
    ActivityCreate,
    ActivityOut,
    ActivityUpdate,
    ContactCreate,
    ContactOut,
    ContactUpdate,
    ConversationMemoryCreate,
    ConversationMemoryOut,
    ConversationOut,
    CustomerCreate,
    CustomerOut,
    CustomerUpdate,
    DashboardOut,
    LeadConversionOut,
    LeadConversionRecordOut,
    LeadConversionRequest,
    LeadCreate,
    LeadOut,
    LeadUpdate,
    OpportunityCreate,
    OpportunityOut,
    OpportunityUpdate,
    PendingActionOut,
    RegisterRequest,
    UserOut,
)


class DuplicateUserError(ValueError):
    pass


class FirstAdminRegistrationError(ValueError):
    pass


class InvalidActionError(ValueError):
    pass


class EntityAccessError(InvalidActionError):
    pass


CUSTOMER_SELECT = """
    SELECT l.id,
           COALESCE(
               NULLIF(TRIM(CONCAT_WS(' ', l.first_name, l.last_name)), ''),
               '未命名客户'
           ) AS name,
           l.company_name AS company,
           l.job_title AS title,
           l.email,
           l.phone,
           l.status,
           l.source,
           l.description AS notes,
           l.extra,
           l.version,
           l.owner_id,
           COALESCE(u.display_name, u.username) AS owner_name,
           l.created_at,
           l.updated_at
    FROM leads l
    LEFT JOIN users u ON u.id = l.owner_id
"""


@dataclass(frozen=True, slots=True)
class EntitySpec:
    table: str
    entity_type: str
    owner_column: str
    writable_columns: tuple[str, ...]
    search_columns: tuple[str, ...]
    soft_delete: bool = True
    references: tuple[tuple[str, str], ...] = ()


ENTITY_SPECS = {
    "leads": EntitySpec(
        table="leads",
        entity_type="lead",
        owner_column="owner_id",
        writable_columns=(
            "first_name",
            "last_name",
            "company_name",
            "email",
            "phone",
            "job_title",
            "source",
            "status",
            "score",
            "owner_id",
            "description",
            "extra",
        ),
        search_columns=("first_name", "last_name", "company_name", "email", "phone"),
    ),
    "accounts": EntitySpec(
        table="accounts",
        entity_type="account",
        owner_column="owner_id",
        writable_columns=(
            "name",
            "industry",
            "website",
            "phone",
            "email",
            "address",
            "city",
            "state",
            "country",
            "employee_count",
            "annual_revenue",
            "status",
            "source",
            "owner_id",
            "description",
        ),
        search_columns=("name", "industry", "email", "phone", "city"),
    ),
    "contacts": EntitySpec(
        table="contacts",
        entity_type="contact",
        owner_column="owner_id",
        writable_columns=(
            "account_id",
            "first_name",
            "last_name",
            "title",
            "department",
            "email",
            "phone",
            "mobile",
            "wechat",
            "linkedin",
            "source",
            "owner_id",
            "description",
        ),
        search_columns=("first_name", "last_name", "email", "phone", "mobile", "wechat"),
        references=(("account_id", "accounts"),),
    ),
    "opportunities": EntitySpec(
        table="opportunities",
        entity_type="opportunity",
        owner_column="owner_id",
        writable_columns=(
            "account_id",
            "primary_contact_id",
            "name",
            "amount",
            "currency",
            "stage",
            "probability",
            "expected_close_date",
            "source",
            "owner_id",
            "description",
        ),
        search_columns=("name", "currency", "stage", "source"),
        references=(
            ("account_id", "accounts"),
            ("primary_contact_id", "contacts"),
        ),
    ),
    "activities": EntitySpec(
        table="activities",
        entity_type="activity",
        owner_column="assigned_user_id",
        writable_columns=(
            "type",
            "subject",
            "description",
            "status",
            "priority",
            "start_at",
            "end_at",
            "account_id",
            "contact_id",
            "lead_id",
            "opportunity_id",
            "assigned_user_id",
        ),
        search_columns=("type", "subject", "description", "status", "priority"),
        soft_delete=False,
        references=(
            ("account_id", "accounts"),
            ("contact_id", "contacts"),
            ("lead_id", "leads"),
            ("opportunity_id", "opportunities"),
        ),
    ),
}

ENTITY_MODELS: dict[str, tuple[type[BaseModel], type[BaseModel], type[BaseModel]]] = {
    "lead": (LeadCreate, LeadUpdate, LeadOut),
    "account": (AccountCreate, AccountUpdate, AccountOut),
    "contact": (ContactCreate, ContactUpdate, ContactOut),
    "opportunity": (OpportunityCreate, OpportunityUpdate, OpportunityOut),
    "activity": (ActivityCreate, ActivityUpdate, ActivityOut),
}
ENTITY_TABLES = {
    "lead": "leads",
    "account": "accounts",
    "contact": "contacts",
    "opportunity": "opportunities",
    "activity": "activities",
}

EntityOutT = TypeVar("EntityOutT", bound=BaseModel)


def _now() -> datetime:
    return datetime.now(UTC)


def _user_out(record: dict[str, Any]) -> UserOut:
    normalized = dict(record)
    normalized["display_name"] = normalized.get("display_name") or normalized["username"]
    return UserOut.model_validate(normalized)


def _current_user(record: dict[str, Any]) -> CurrentUser:
    return CurrentUser(
        id=record["id"],
        username=record["username"],
        email=record.get("email") or "",
        display_name=record.get("display_name") or record["username"],
        role=Role(record["role"]),
        is_active=record["is_active"],
    )


class CRMRepository:
    def __init__(self, pool: AsyncConnectionPool) -> None:
        self.pool = pool

    async def setup(self) -> None:
        async with self.pool.connection() as conn:
            await run_migrations(conn)

    async def register_user(
        self,
        data: RegisterRequest,
        password_hash: str,
        *,
        first_user_is_admin: bool,
        allow_first_admin: bool,
    ) -> UserOut:
        async with self.pool.connection() as conn, conn.transaction():
            await conn.execute("LOCK TABLE users IN SHARE ROW EXCLUSIVE MODE")
            count_result = await conn.execute(
                "SELECT COUNT(*)::int AS count FROM users WHERE password_hash IS NOT NULL"
            )
            count = (await count_result.fetchone())["count"]
            if first_user_is_admin and count == 0 and not allow_first_admin:
                raise FirstAdminRegistrationError(
                    "首个管理员账号只能在运行服务的电脑上通过 localhost 注册"
                )
            role = Role.ADMIN if first_user_is_admin and count == 0 else Role.SALES
            try:
                result = await conn.execute(
                    """
                    INSERT INTO users
                        (username, email, display_name, password_hash, password_changed_at, role)
                    VALUES (%s, %s, %s, %s, NOW(), %s)
                    RETURNING id, username, email, display_name, role, is_active,
                              created_at, last_login_at
                    """,
                    (
                        data.username,
                        str(data.email).lower(),
                        data.display_name.strip(),
                        password_hash,
                        role.value,
                    ),
                )
            except UniqueViolation as exc:
                raise DuplicateUserError("用户名或邮箱已被注册") from exc
            record = await result.fetchone()
            await self._write_audit(
                conn,
                actor_user_id=record["id"],
                action="user.register",
                entity_type="user",
                entity_id=record["id"],
                after_data={"username": record["username"], "role": record["role"]},
            )
        return _user_out(record)

    async def get_auth_record(self, login: str) -> dict[str, Any] | None:
        normalized = login.strip().lower()
        async with self.pool.connection() as conn:
            result = await conn.execute(
                """
                SELECT id, username, email, display_name, password_hash, role, is_active,
                       created_at, last_login_at
                FROM users
                WHERE LOWER(username) = %s OR LOWER(email) = %s
                """,
                (normalized, normalized),
            )
            return await result.fetchone()

    async def mark_login(self, user_id: UUID) -> None:
        async with self.pool.connection() as conn:
            await conn.execute("UPDATE users SET last_login_at = NOW() WHERE id = %s", (user_id,))

    async def create_session(
        self,
        user_id: UUID,
        token_digest: str,
        *,
        ttl_hours: int,
        user_agent: str | None,
        ip_address: str | None,
    ) -> None:
        expires_at = _now() + timedelta(hours=ttl_hours)
        async with self.pool.connection() as conn, conn.transaction():
            await conn.execute("DELETE FROM crm_sessions WHERE expires_at <= NOW()")
            await conn.execute(
                """
                INSERT INTO crm_sessions
                    (user_id, token_digest, user_agent, ip_address, expires_at)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (user_id, token_digest, user_agent, ip_address, expires_at),
            )

    async def get_user_by_session(self, token_digest: str) -> CurrentUser | None:
        async with self.pool.connection() as conn:
            result = await conn.execute(
                """
                SELECT u.id, u.username, u.email, u.display_name, u.role, u.is_active
                FROM crm_sessions s
                JOIN users u ON u.id = s.user_id
                WHERE s.token_digest = %s AND s.expires_at > NOW() AND u.is_active = TRUE
                """,
                (token_digest,),
            )
            record = await result.fetchone()
            if record:
                await conn.execute(
                    "UPDATE crm_sessions SET last_seen_at = NOW() WHERE token_digest = %s",
                    (token_digest,),
                )
        return _current_user(record) if record else None

    async def delete_session(self, token_digest: str) -> None:
        async with self.pool.connection() as conn:
            await conn.execute("DELETE FROM crm_sessions WHERE token_digest = %s", (token_digest,))

    async def get_user(self, user_id: UUID) -> UserOut | None:
        async with self.pool.connection() as conn:
            result = await conn.execute(
                """
                SELECT id, username, email, display_name, role, is_active,
                       created_at, last_login_at
                FROM users WHERE id = %s
                """,
                (user_id,),
            )
            record = await result.fetchone()
        return _user_out(record) if record else None

    async def list_users(self, current_user: CurrentUser) -> list[UserOut]:
        require_permission(current_user, Permission.USERS_MANAGE)
        async with self.pool.connection() as conn:
            result = await conn.execute(
                """
                SELECT id, username, email, display_name, role, is_active,
                       created_at, last_login_at
                FROM users
                WHERE password_hash IS NOT NULL
                ORDER BY created_at ASC
                """
            )
            records = await result.fetchall()
        return [_user_out(record) for record in records]

    async def update_user_role(
        self, current_user: CurrentUser, user_id: UUID, role: Role
    ) -> UserOut | None:
        require_permission(current_user, Permission.USERS_MANAGE)
        async with self.pool.connection() as conn, conn.transaction():
            # Role changes for different administrator rows must still serialize:
            # otherwise two transactions can each observe two active admins and
            # concurrently demote both of them.  A fixed transaction-scoped lock
            # makes the subsequent active-admin count a single global decision.
            await conn.execute(
                "SELECT pg_advisory_xact_lock(%s::bigint)",
                (8_426_081_301,),
            )
            target_result = await conn.execute(
                "SELECT id, role FROM users WHERE id = %s FOR UPDATE", (user_id,)
            )
            target = await target_result.fetchone()
            if not target:
                return None
            if target["role"] == Role.ADMIN.value and role is not Role.ADMIN:
                admin_result = await conn.execute(
                    "SELECT COUNT(*)::int AS count FROM users WHERE role = 'admin' AND is_active"
                )
                if (await admin_result.fetchone())["count"] <= 1:
                    raise ValueError("不能移除最后一个启用中的管理员")
            result = await conn.execute(
                """
                UPDATE users SET role = %s, updated_at = NOW()
                WHERE id = %s
                RETURNING id, username, email, display_name, role, is_active,
                          created_at, last_login_at
                """,
                (role.value, user_id),
            )
            record = await result.fetchone()
            await self._write_audit(
                conn,
                actor_user_id=current_user.id,
                action="user.role.update",
                entity_type="user",
                entity_id=user_id,
                before_data={"role": target["role"]},
                after_data={"role": role.value},
            )
        return _user_out(record)

    async def create_conversation(
        self,
        owner_user_id: UUID,
        title: str = "新会话",
        conversation_id: UUID | None = None,
    ) -> ConversationOut:
        conversation_id = conversation_id or uuid4()
        async with self.pool.connection() as conn:
            result = await conn.execute(
                """
                INSERT INTO crm_conversations (id, owner_user_id, title)
                VALUES (%s, %s, %s)
                RETURNING *
                """,
                (conversation_id, owner_user_id, title),
            )
            record = await result.fetchone()
        return ConversationOut.model_validate(record)

    async def list_conversations(
        self,
        owner_user_id: UUID,
        *,
        archived: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> list[ConversationOut]:
        async with self.pool.connection() as conn:
            result = await conn.execute(
                """
                SELECT * FROM crm_conversations
                WHERE owner_user_id = %s AND is_archived = %s
                ORDER BY COALESCE(last_message_at, created_at) DESC, created_at DESC
                LIMIT %s OFFSET %s
                """,
                (owner_user_id, archived, limit, offset),
            )
            records = await result.fetchall()
        return [ConversationOut.model_validate(record) for record in records]

    async def get_conversation(
        self, owner_user_id: UUID, conversation_id: UUID
    ) -> ConversationOut | None:
        async with self.pool.connection() as conn:
            result = await conn.execute(
                """
                SELECT * FROM crm_conversations
                WHERE owner_user_id = %s AND id = %s
                """,
                (owner_user_id, conversation_id),
            )
            record = await result.fetchone()
        return ConversationOut.model_validate(record) if record else None

    async def update_conversation(
        self,
        owner_user_id: UUID,
        conversation_id: UUID,
        *,
        title: str | None = None,
        is_archived: bool | None = None,
    ) -> ConversationOut | None:
        assignments: list[sql.Composable] = []
        params: list[Any] = []
        if title is not None:
            assignments.append(sql.SQL("title = %s"))
            params.append(title)
        if is_archived is not None:
            assignments.append(sql.SQL("is_archived = %s"))
            params.append(is_archived)
        if not assignments:
            return await self.get_conversation(owner_user_id, conversation_id)
        query = sql.SQL(
            """
            UPDATE crm_conversations SET {}, updated_at = NOW()
            WHERE owner_user_id = %s AND id = %s
            RETURNING *
            """
        ).format(sql.SQL(", ").join(assignments))
        params.extend([owner_user_id, conversation_id])
        async with self.pool.connection() as conn:
            result = await conn.execute(query, params)
            record = await result.fetchone()
        return ConversationOut.model_validate(record) if record else None

    async def record_conversation_turn(
        self, owner_user_id: UUID, conversation_id: UUID, first_user_message: str
    ) -> ConversationOut | None:
        generated_title = " ".join(first_user_message.split())[:36]
        async with self.pool.connection() as conn:
            result = await conn.execute(
                """
                UPDATE crm_conversations
                SET title = CASE
                        WHEN message_count = 0 AND title = '新会话' THEN %s
                        ELSE title
                    END,
                    message_count = message_count + 2,
                    last_message_at = NOW(),
                    updated_at = NOW()
                WHERE owner_user_id = %s AND id = %s
                RETURNING *
                """,
                (generated_title or "新会话", owner_user_id, conversation_id),
            )
            record = await result.fetchone()
        return ConversationOut.model_validate(record) if record else None

    async def delete_conversation(self, owner_user_id: UUID, conversation_id: UUID) -> bool:
        async with self.pool.connection() as conn:
            result = await conn.execute(
                "DELETE FROM crm_conversations WHERE owner_user_id = %s AND id = %s",
                (owner_user_id, conversation_id),
            )
        return result.rowcount == 1

    async def add_conversation_memory(
        self,
        owner_user_id: UUID,
        conversation_id: UUID,
        data: ConversationMemoryCreate,
    ) -> ConversationMemoryOut | None:
        async with self.pool.connection() as conn:
            result = await conn.execute(
                """
                INSERT INTO crm_conversation_memories
                    (owner_user_id, conversation_id, memory_type, content, importance)
                SELECT %s, %s, %s, %s, %s
                WHERE EXISTS (
                    SELECT 1 FROM crm_conversations
                    WHERE owner_user_id = %s AND id = %s
                )
                RETURNING *
                """,
                (
                    owner_user_id,
                    conversation_id,
                    data.memory_type,
                    data.content,
                    data.importance,
                    owner_user_id,
                    conversation_id,
                ),
            )
            record = await result.fetchone()
        return ConversationMemoryOut.model_validate(record) if record else None

    async def recall_conversation_memories(
        self,
        owner_user_id: UUID,
        conversation_id: UUID,
        query: str | None = None,
        limit: int = 20,
    ) -> list[ConversationMemoryOut]:
        pattern = f"%{query}%" if query else None
        async with self.pool.connection() as conn:
            result = await conn.execute(
                """
                SELECT * FROM crm_conversation_memories
                WHERE owner_user_id = %s AND conversation_id = %s
                  AND (%s::text IS NULL OR content ILIKE %s OR memory_type ILIKE %s)
                ORDER BY importance DESC, updated_at DESC
                LIMIT %s
                """,
                (owner_user_id, conversation_id, pattern, pattern, pattern, limit),
            )
            records = await result.fetchall()
        return [ConversationMemoryOut.model_validate(record) for record in records]

    @staticmethod
    def _visibility_clause(current_user: CurrentUser, alias: str = "l") -> tuple[str, list[Any]]:
        visibility = customer_visibility_for(current_user)
        if visibility is CustomerVisibility.OWNED:
            return f" AND {alias}.owner_id = %s", [current_user.id]
        return "", []

    async def list_customers(
        self,
        current_user: CurrentUser,
        query: str | None = None,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[CustomerOut]:
        require_permission(current_user, Permission.CUSTOMER_READ)
        visibility_sql, visibility_params = self._visibility_clause(current_user)
        pattern = f"%{query}%" if query else None
        statement = (
            CUSTOMER_SELECT
            + """
            WHERE l.deleted_at IS NULL
              AND (%s::text IS NULL OR l.first_name ILIKE %s OR l.last_name ILIKE %s
                   OR l.company_name ILIKE %s OR l.email ILIKE %s OR l.phone ILIKE %s)
              AND (%s::text IS NULL OR l.status = %s)
            """
            + visibility_sql
            + " ORDER BY l.updated_at DESC LIMIT %s OFFSET %s"
        )
        params: list[Any] = [
            pattern,
            pattern,
            pattern,
            pattern,
            pattern,
            pattern,
            status,
            status,
            *visibility_params,
            limit,
            offset,
        ]
        async with self.pool.connection() as conn:
            result = await conn.execute(statement, params)
            records = await result.fetchall()
        return [CustomerOut.model_validate(record) for record in records]

    async def get_dashboard(self, current_user: CurrentUser) -> DashboardOut:
        """Return role-scoped CRM metrics without crossing customer visibility boundaries."""
        require_permission(current_user, Permission.CUSTOMER_READ)
        visibility_sql, visibility_params = self._visibility_clause(current_user)
        status_statement = (
            """
            SELECT COALESCE(NULLIF(l.status, ''), 'unknown') AS status,
                   COUNT(*)::int AS count
            FROM leads l
            WHERE l.deleted_at IS NULL
            """
            + visibility_sql
            + " GROUP BY COALESCE(NULLIF(l.status, ''), 'unknown')"
        )
        recent_statement = (
            CUSTOMER_SELECT
            + " WHERE l.deleted_at IS NULL"
            + visibility_sql
            + " ORDER BY l.updated_at DESC, l.created_at DESC LIMIT 5"
        )

        async with self.pool.connection() as conn:
            status_result = await conn.execute(status_statement, visibility_params)
            status_records = await status_result.fetchall()
            recent_result = await conn.execute(recent_statement, visibility_params)
            recent_records = await recent_result.fetchall()

            owned_scope = current_user.role is Role.SALES
            entity_scope = " AND owner_id = %s" if owned_scope else ""
            activity_scope = " WHERE assigned_user_id = %s" if owned_scope else ""
            count_params = [current_user.id] * 5 if owned_scope else []
            count_result = await conn.execute(
                f"""
                SELECT
                    (SELECT COUNT(*)::int FROM leads
                     WHERE deleted_at IS NULL{entity_scope}) AS leads,
                    (SELECT COUNT(*)::int FROM accounts
                     WHERE deleted_at IS NULL{entity_scope}) AS accounts,
                    (SELECT COUNT(*)::int FROM contacts
                     WHERE deleted_at IS NULL{entity_scope}) AS contacts,
                    (SELECT COUNT(*)::int FROM opportunities
                     WHERE deleted_at IS NULL{entity_scope}) AS opportunities,
                    (SELECT COUNT(*)::int FROM activities{activity_scope}) AS activities
                """,
                count_params,
            )
            entity_counts = await count_result.fetchone()

            total_users: int | None = None
            if current_user.role is Role.ADMIN:
                user_result = await conn.execute(
                    """
                    SELECT COUNT(*)::int AS count
                    FROM users
                    WHERE password_hash IS NOT NULL
                    """
                )
                total_users = (await user_result.fetchone())["count"]

        status_counts = {
            "new": 0,
            "contacted": 0,
            "qualified": 0,
            "converted": 0,
            "lost": 0,
        }
        for record in status_records:
            status_counts[record["status"]] = record["count"]

        return DashboardOut(
            total_customers=sum(status_counts.values()),
            status_counts=status_counts,
            recent_customers=[CustomerOut.model_validate(record) for record in recent_records],
            total_users=total_users,
            entity_counts=entity_counts,
        )

    async def get_customer(
        self, current_user: CurrentUser, customer_id: UUID
    ) -> CustomerOut | None:
        require_permission(current_user, Permission.CUSTOMER_READ)
        visibility_sql, visibility_params = self._visibility_clause(current_user)
        async with self.pool.connection() as conn:
            result = await conn.execute(
                CUSTOMER_SELECT + " WHERE l.id = %s AND l.deleted_at IS NULL" + visibility_sql,
                [customer_id, *visibility_params],
            )
            record = await result.fetchone()
        return CustomerOut.model_validate(record) if record else None

    async def _insert_customer(
        self,
        conn: AsyncConnection,
        current_user: CurrentUser,
        data: CustomerCreate,
        *,
        conversation_id: UUID | None = None,
        request_id: str | None = None,
    ) -> CustomerOut:
        values = data.model_dump(mode="python")
        self._validate_lead_create_status(values.get("status"))
        result = await conn.execute(
            """
            INSERT INTO leads
                (first_name, last_name, company_name, email, phone, job_title,
                 source, status, owner_id, description, extra)
            VALUES (%s, NULL, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                values["name"],
                values["company"],
                str(values["email"]) if values["email"] else None,
                values["phone"],
                values["title"],
                values["source"],
                values["status"],
                current_user.id,
                values["notes"],
                Jsonb(values["extra"]),
            ),
        )
        customer_id = (await result.fetchone())["id"]
        selected = await conn.execute(CUSTOMER_SELECT + " WHERE l.id = %s", (customer_id,))
        customer = CustomerOut.model_validate(await selected.fetchone())
        await self._write_audit(
            conn,
            actor_user_id=current_user.id,
            action="customer.create",
            entity_type="lead",
            entity_id=customer.id,
            conversation_id=conversation_id,
            request_id=request_id,
            after_data=customer.model_dump(mode="json"),
        )
        return customer

    async def create_customer(
        self,
        current_user: CurrentUser,
        data: CustomerCreate,
        *,
        request_id: str | None = None,
    ) -> CustomerOut:
        require_permission(current_user, Permission.CUSTOMER_CREATE)
        async with self.pool.connection() as conn, conn.transaction():
            return await self._insert_customer(conn, current_user, data, request_id=request_id)

    async def _update_customer(
        self,
        conn: AsyncConnection,
        current_user: CurrentUser,
        customer_id: UUID,
        data: CustomerUpdate,
        *,
        conversation_id: UUID | None = None,
        request_id: str | None = None,
        expected_version: int | None = None,
    ) -> CustomerOut | None:
        values = data.model_dump(exclude_unset=True, mode="python")
        if not values:
            raise InvalidActionError("至少提供一个需要更新的字段")
        visibility_sql, visibility_params = self._visibility_clause(current_user)
        before_result = await conn.execute(
            CUSTOMER_SELECT
            + " WHERE l.id = %s AND l.deleted_at IS NULL"
            + visibility_sql
            + " FOR UPDATE OF l",
            [customer_id, *visibility_params],
        )
        before_record = await before_result.fetchone()
        if not before_record:
            return None
        if expected_version is not None and before_record["version"] != expected_version:
            raise InvalidActionError("客户资料已被其他操作更新，请重新查询后再提交")
        values = self._normalize_lead_status_update(before_record["status"], values)
        if not values:
            return CustomerOut.model_validate(before_record)
        field_map = {
            "name": "first_name",
            "company": "company_name",
            "title": "job_title",
            "email": "email",
            "phone": "phone",
            "status": "status",
            "source": "source",
            "notes": "description",
            "extra": "extra",
        }
        assignments: list[sql.Composable] = []
        params: list[Any] = []
        for field, value in values.items():
            assignments.append(sql.SQL("{} = %s").format(sql.Identifier(field_map[field])))
            if field == "extra" and value is not None:
                value = Jsonb(value)
            elif field == "email" and value is not None:
                value = str(value)
            params.append(value)
        update_query = sql.SQL(
            "UPDATE leads SET {}, updated_at = NOW(), version = version + 1 WHERE id = %s"
        ).format(sql.SQL(", ").join(assignments))
        await conn.execute(update_query, [*params, customer_id])
        after_result = await conn.execute(CUSTOMER_SELECT + " WHERE l.id = %s", (customer_id,))
        after = CustomerOut.model_validate(await after_result.fetchone())
        await self._write_audit(
            conn,
            actor_user_id=current_user.id,
            action="customer.update",
            entity_type="lead",
            entity_id=customer_id,
            conversation_id=conversation_id,
            request_id=request_id,
            before_data=CustomerOut.model_validate(before_record).model_dump(mode="json"),
            after_data=after.model_dump(mode="json"),
        )
        return after

    async def update_customer(
        self,
        current_user: CurrentUser,
        customer_id: UUID,
        data: CustomerUpdate,
        *,
        request_id: str | None = None,
    ) -> CustomerOut | None:
        require_permission(current_user, Permission.CUSTOMER_UPDATE)
        if not data.model_dump(exclude_unset=True):
            return await self.get_customer(current_user, customer_id)
        async with self.pool.connection() as conn, conn.transaction():
            return await self._update_customer(
                conn, current_user, customer_id, data, request_id=request_id
            )

    async def delete_customer(
        self,
        current_user: CurrentUser,
        customer_id: UUID,
        *,
        request_id: str | None = None,
    ) -> bool:
        require_permission(current_user, Permission.CUSTOMER_DELETE)
        visibility_sql, visibility_params = self._visibility_clause(current_user)
        async with self.pool.connection() as conn, conn.transaction():
            before_result = await conn.execute(
                CUSTOMER_SELECT
                + " WHERE l.id = %s AND l.deleted_at IS NULL"
                + visibility_sql
                + " FOR UPDATE OF l",
                [customer_id, *visibility_params],
            )
            record = await before_result.fetchone()
            if not record:
                return False
            await self._validate_entity_deletion(conn, ENTITY_SPECS["leads"], customer_id)
            await conn.execute(
                """
                UPDATE leads
                SET deleted_at = NOW(), updated_at = NOW(), version = version + 1
                WHERE id = %s
                """,
                (customer_id,),
            )
            await self._write_audit(
                conn,
                actor_user_id=current_user.id,
                action="customer.delete",
                entity_type="lead",
                entity_id=customer_id,
                request_id=request_id,
                before_data=CustomerOut.model_validate(record).model_dump(mode="json"),
            )
        return True

    @staticmethod
    def _entity_visibility(
        current_user: CurrentUser, spec: EntitySpec
    ) -> tuple[sql.Composable | None, list[Any]]:
        visibility = customer_visibility_for(current_user)
        if visibility is CustomerVisibility.OWNED:
            return (
                sql.SQL("{} = %s").format(sql.Identifier(spec.owner_column)),
                [current_user.id],
            )
        return None, []

    async def _validate_entity_references(
        self,
        conn: AsyncConnection,
        current_user: CurrentUser,
        spec: EntitySpec,
        values: dict[str, Any],
    ) -> None:
        """Validate active FK targets and enforce the sales ownership boundary."""
        if spec.owner_column in values and values[spec.owner_column] is not None:
            owner_result = await conn.execute(
                "SELECT id FROM users WHERE id = %s AND is_active = TRUE",
                (values[spec.owner_column],),
            )
            if not await owner_result.fetchone():
                raise InvalidActionError(f"{spec.owner_column} 指向的有效账号不存在")
        for field_name, referenced_table in spec.references:
            if field_name not in values or values[field_name] is None:
                continue
            referenced_spec = ENTITY_SPECS[referenced_table]
            conditions = [sql.SQL("id = %s")]
            if referenced_spec.soft_delete:
                conditions.append(sql.SQL("deleted_at IS NULL"))
            statement = sql.SQL("SELECT {} AS owner_id FROM {} WHERE {}").format(
                sql.Identifier(referenced_spec.owner_column),
                sql.Identifier(referenced_table),
                sql.SQL(" AND ").join(conditions),
            )
            statement += sql.SQL(" FOR KEY SHARE")
            result = await conn.execute(statement, (values[field_name],))
            target = await result.fetchone()
            if not target:
                raise InvalidActionError(f"{field_name} 指向的有效记录不存在")
            if current_user.role is Role.SALES and target["owner_id"] != current_user.id:
                raise EntityAccessError(f"销售账号不能关联不属于自己的 {referenced_table} 记录")
        await self._validate_entity_consistency(conn, spec, values)

    async def _validate_entity_consistency(
        self,
        conn: AsyncConnection,
        spec: EntitySpec,
        values: dict[str, Any],
    ) -> None:
        """Prevent individually valid foreign keys from forming contradictory CRM links."""
        if spec.table == "opportunities" and values.get("primary_contact_id"):
            contact_result = await conn.execute(
                "SELECT account_id FROM contacts WHERE id = %s AND deleted_at IS NULL",
                (values["primary_contact_id"],),
            )
            contact = await contact_result.fetchone()
            if not contact:
                raise InvalidActionError("primary_contact_id 指向的有效联系人不存在")
            if contact["account_id"] != values.get("account_id"):
                raise InvalidActionError("商机主要联系人必须属于同一公司")
        if spec.table != "activities":
            return
        start_at = values.get("start_at")
        end_at = values.get("end_at")
        if start_at is not None and end_at is not None and end_at < start_at:
            raise InvalidActionError("活动结束时间不能早于开始时间")
        account_id = values.get("account_id")
        contact_id = values.get("contact_id")
        opportunity_id = values.get("opportunity_id")
        contact_account_id: UUID | None = None
        if contact_id:
            result = await conn.execute(
                "SELECT account_id FROM contacts WHERE id = %s", (contact_id,)
            )
            contact = await result.fetchone()
            contact_account_id = contact["account_id"] if contact else None
            if account_id and contact_account_id != account_id:
                raise InvalidActionError("活动联系人必须属于所选公司")
        opportunity_account_id: UUID | None = None
        if opportunity_id:
            result = await conn.execute(
                "SELECT account_id, primary_contact_id FROM opportunities WHERE id = %s",
                (opportunity_id,),
            )
            opportunity = await result.fetchone()
            opportunity_account_id = opportunity["account_id"] if opportunity else None
            if opportunity and account_id and opportunity_account_id != account_id:
                raise InvalidActionError("活动商机必须属于所选公司")
            if (
                opportunity
                and contact_id
                and opportunity["primary_contact_id"]
                and opportunity["primary_contact_id"] != contact_id
            ):
                raise InvalidActionError("活动联系人和商机主要联系人不一致")
        if contact_id and opportunity_id and contact_account_id != opportunity_account_id:
            raise InvalidActionError("活动联系人和商机必须属于同一公司")
        lead_id = values.get("lead_id")
        if lead_id and (account_id or contact_id or opportunity_id):
            result = await conn.execute(
                """
                SELECT account_id, contact_id, opportunity_id
                FROM lead_conversions WHERE lead_id = %s
                """,
                (lead_id,),
            )
            conversion = await result.fetchone()
            if not conversion:
                raise InvalidActionError("线索尚未正式转换，不能同时关联公司、联系人或商机")
            if account_id and conversion["account_id"] != account_id:
                raise InvalidActionError("活动公司与线索转换结果不一致")
            if contact_id and contact_account_id != conversion["account_id"]:
                raise InvalidActionError("活动联系人不属于线索转换后的公司")
            if opportunity_id and opportunity_account_id != conversion["account_id"]:
                raise InvalidActionError("活动商机不属于线索转换后的公司")

    async def _hydrate_relationship_values(
        self,
        conn: AsyncConnection,
        spec: EntitySpec,
        entity_id: UUID,
        values: dict[str, Any],
    ) -> dict[str, Any]:
        """Merge persisted relationship columns so partial PATCHes are checked as a whole."""
        hydrated = dict(values)
        relationship_columns = {
            "opportunities": ("account_id", "primary_contact_id"),
            "activities": (
                "start_at",
                "end_at",
                "account_id",
                "contact_id",
                "lead_id",
                "opportunity_id",
            ),
        }.get(spec.table)
        if not relationship_columns:
            return hydrated
        missing = [column for column in relationship_columns if column not in hydrated]
        if not missing:
            return hydrated
        result = await conn.execute(
            sql.SQL("SELECT {} FROM {} WHERE id = %s").format(
                sql.SQL(", ").join(sql.Identifier(column) for column in missing),
                sql.Identifier(spec.table),
            ),
            (entity_id,),
        )
        current = await result.fetchone()
        if current:
            return {**{column: current[column] for column in missing}, **hydrated}
        return hydrated

    async def _inherit_and_validate_relationship_owners(
        self,
        conn: AsyncConnection,
        spec: EntitySpec,
        values: dict[str, Any],
        *,
        creating: bool,
    ) -> dict[str, Any]:
        """Enforce one owner across every linked CRM chain, regardless of caller role."""
        related: list[tuple[str, str]] = []
        if spec.table in {"contacts", "opportunities"} and values.get("account_id"):
            related.append(("account_id", "accounts"))
        if spec.table == "opportunities" and values.get("primary_contact_id"):
            related.append(("primary_contact_id", "contacts"))
        if spec.table == "activities":
            for field, table in (
                ("account_id", "accounts"),
                ("contact_id", "contacts"),
                ("lead_id", "leads"),
                ("opportunity_id", "opportunities"),
            ):
                if values.get(field):
                    related.append((field, table))
        owners: set[UUID | None] = set()
        for field, table in related:
            target_spec = ENTITY_SPECS[table]
            result = await conn.execute(
                sql.SQL("SELECT {} AS owner_id FROM {} WHERE id = %s").format(
                    sql.Identifier(target_spec.owner_column), sql.Identifier(table)
                )
                + sql.SQL(" FOR KEY SHARE"),
                (values[field],),
            )
            target = await result.fetchone()
            if target:
                owners.add(target["owner_id"])
        if len(owners) > 1:
            raise InvalidActionError("所有关联记录必须属于同一负责人")
        has_relationship_owner = bool(owners)
        related_owner = next(iter(owners), None)
        if has_relationship_owner:
            owner_column = spec.owner_column
            supplied_owner = values.get(owner_column)
            if owner_column in values and supplied_owner != related_owner:
                raise InvalidActionError("负责人必须与关联记录负责人一致")
            if creating and owner_column not in values:
                values[owner_column] = related_owner
        return values

    @staticmethod
    def _prepare_entity_values(
        current_user: CurrentUser,
        spec: EntitySpec,
        data: BaseModel,
        *,
        creating: bool,
    ) -> dict[str, Any]:
        values = data.model_dump(exclude_unset=True, mode="python")
        values = {key: value for key, value in values.items() if key in spec.writable_columns}
        if current_user.role is Role.SALES:
            assigned = values.get(spec.owner_column)
            if spec.owner_column in values and assigned != current_user.id:
                raise EntityAccessError("销售账号不能把业务数据分配给其他账号或取消负责人")
            # Sales-created records always belong to the authenticated user. This value is
            # server-derived, so a client cannot escape its row scope by omitting the field.
        if "extra" in values and values["extra"] is not None:
            values["extra"] = Jsonb(values["extra"])
        return values

    async def _list_entities(
        self,
        current_user: CurrentUser,
        spec: EntitySpec,
        output_type: type[EntityOutT],
        *,
        query: str | None = None,
        filters: dict[str, Any] | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[EntityOutT]:
        require_permission(current_user, Permission.CUSTOMER_READ)
        conditions: list[sql.Composable] = []
        params: list[Any] = []
        if spec.soft_delete:
            conditions.append(sql.SQL("deleted_at IS NULL"))
        visibility, visibility_params = self._entity_visibility(current_user, spec)
        if visibility is not None:
            conditions.append(visibility)
            params.extend(visibility_params)
        if query:
            pattern = f"%{query}%"
            conditions.append(
                sql.SQL("(")
                + sql.SQL(" OR ").join(
                    sql.SQL("{} ILIKE %s").format(sql.Identifier(column))
                    for column in spec.search_columns
                )
                + sql.SQL(")")
            )
            params.extend([pattern] * len(spec.search_columns))
        for column, value in (filters or {}).items():
            if value is not None and column in spec.writable_columns:
                conditions.append(sql.SQL("{} = %s").format(sql.Identifier(column)))
                params.append(value)
        where_clause = (
            sql.SQL(" WHERE ") + sql.SQL(" AND ").join(conditions) if conditions else sql.SQL("")
        )
        statement = sql.SQL("SELECT * FROM {}{}").format(
            sql.Identifier(spec.table), where_clause
        ) + sql.SQL(" ORDER BY updated_at DESC, created_at DESC LIMIT %s OFFSET %s")
        params.extend([limit, offset])
        async with self.pool.connection() as conn:
            result = await conn.execute(statement, params)
            records = await result.fetchall()
        return [output_type.model_validate(record) for record in records]

    async def _get_entity(
        self,
        current_user: CurrentUser,
        spec: EntitySpec,
        entity_id: UUID,
        output_type: type[EntityOutT],
    ) -> EntityOutT | None:
        require_permission(current_user, Permission.CUSTOMER_READ)
        conditions: list[sql.Composable] = [sql.SQL("id = %s")]
        params: list[Any] = [entity_id]
        if spec.soft_delete:
            conditions.append(sql.SQL("deleted_at IS NULL"))
        visibility, visibility_params = self._entity_visibility(current_user, spec)
        if visibility is not None:
            conditions.append(visibility)
            params.extend(visibility_params)
        statement = sql.SQL("SELECT * FROM {} WHERE {}").format(
            sql.Identifier(spec.table), sql.SQL(" AND ").join(conditions)
        )
        async with self.pool.connection() as conn:
            result = await conn.execute(statement, params)
            record = await result.fetchone()
        return output_type.model_validate(record) if record else None

    async def _create_entity(
        self,
        current_user: CurrentUser,
        spec: EntitySpec,
        data: BaseModel,
        output_type: type[EntityOutT],
        *,
        request_id: str | None = None,
    ) -> EntityOutT:
        require_permission(current_user, Permission.CUSTOMER_CREATE)
        async with self.pool.connection() as conn, conn.transaction():
            return await self._create_entity_conn(
                conn,
                current_user,
                spec,
                data,
                output_type,
                request_id=request_id,
            )

    async def _create_entity_conn(
        self,
        conn: AsyncConnection,
        current_user: CurrentUser,
        spec: EntitySpec,
        data: BaseModel,
        output_type: type[EntityOutT],
        *,
        conversation_id: UUID | None = None,
        request_id: str | None = None,
    ) -> EntityOutT:
        """Insert, validate and audit using the caller's transaction/connection."""
        require_permission(current_user, Permission.CUSTOMER_CREATE)
        values = self._prepare_entity_values(current_user, spec, data, creating=True)
        if spec.table == "leads":
            self._validate_lead_create_status(values.get("status"))
        values = await self._inherit_and_validate_relationship_owners(
            conn, spec, values, creating=True
        )
        if values.get(spec.owner_column) is None:
            has_relationship = any(values.get(field) for field, _table in spec.references) or (
                spec.table == "activities"
                and any(
                    values.get(field)
                    for field in ("account_id", "contact_id", "lead_id", "opportunity_id")
                )
            )
            if has_relationship:
                raise InvalidActionError("关联记录没有负责人，请先执行整链负责人转移")
            values[spec.owner_column] = current_user.id
        columns = list(values)
        if columns:
            statement = sql.SQL("INSERT INTO {} ({}) VALUES ({}) RETURNING *").format(
                sql.Identifier(spec.table),
                sql.SQL(", ").join(sql.Identifier(column) for column in columns),
                sql.SQL(", ").join(sql.Placeholder() for _ in columns),
            )
        else:
            statement = sql.SQL("INSERT INTO {} DEFAULT VALUES RETURNING *").format(
                sql.Identifier(spec.table)
            )
        await self._validate_entity_references(conn, current_user, spec, values)
        result = await conn.execute(statement, [values[column] for column in columns])
        record = await result.fetchone()
        entity = output_type.model_validate(record)
        await self._write_audit(
            conn,
            actor_user_id=current_user.id,
            action=f"{spec.entity_type}.create",
            entity_type=spec.entity_type,
            entity_id=entity.id,
            conversation_id=conversation_id,
            request_id=request_id,
            after_data=entity.model_dump(mode="json"),
        )
        return entity

    async def _update_entity(
        self,
        current_user: CurrentUser,
        spec: EntitySpec,
        entity_id: UUID,
        data: BaseModel,
        output_type: type[EntityOutT],
        *,
        request_id: str | None = None,
    ) -> EntityOutT | None:
        require_permission(current_user, Permission.CUSTOMER_UPDATE)
        values = self._prepare_entity_values(current_user, spec, data, creating=False)
        if not values:
            return await self._get_entity(current_user, spec, entity_id, output_type)
        async with self.pool.connection() as conn, conn.transaction():
            return await self._update_entity_conn(
                conn,
                current_user,
                spec,
                entity_id,
                data,
                output_type,
                request_id=request_id,
            )

    async def _update_entity_conn(
        self,
        conn: AsyncConnection,
        current_user: CurrentUser,
        spec: EntitySpec,
        entity_id: UUID,
        data: BaseModel,
        output_type: type[EntityOutT],
        *,
        expected_updated_at: datetime | None = None,
        expected_version: int | None = None,
        conversation_id: UUID | None = None,
        request_id: str | None = None,
    ) -> EntityOutT | None:
        """Lock, update and audit using the caller's atomic transaction."""
        require_permission(current_user, Permission.CUSTOMER_UPDATE)
        values = self._prepare_entity_values(current_user, spec, data, creating=False)
        if not values:
            raise InvalidActionError("至少提供一个需要更新的字段")
        conditions: list[sql.Composable] = [sql.SQL("id = %s")]
        select_params: list[Any] = [entity_id]
        if spec.soft_delete:
            conditions.append(sql.SQL("deleted_at IS NULL"))
        visibility, visibility_params = self._entity_visibility(current_user, spec)
        if visibility is not None:
            conditions.append(visibility)
            select_params.extend(visibility_params)
        select_statement = sql.SQL("SELECT * FROM {} WHERE {} FOR UPDATE").format(
            sql.Identifier(spec.table), sql.SQL(" AND ").join(conditions)
        )
        before_result = await conn.execute(select_statement, select_params)
        before = await before_result.fetchone()
        if not before:
            return None
        if spec.table == "leads":
            values = self._normalize_lead_status_update(before["status"], values)
        if not values:
            return output_type.model_validate(before)
        if spec.owner_column in values:
            await self._validate_owner_change(conn, spec, entity_id, values[spec.owner_column])
        await self._validate_relationship_change(conn, spec, entity_id, before, values)
        if expected_version is not None and before.get("version") != expected_version:
            raise InvalidActionError("记录已被其他操作更新，请重新查询后再提交")
        if expected_updated_at is not None and before["updated_at"] != expected_updated_at:
            raise InvalidActionError("记录已被其他操作更新，请重新查询后再提交")
        relationship_values = await self._hydrate_relationship_values(
            conn, spec, entity_id, values
        )
        relationship_values.setdefault(spec.owner_column, before[spec.owner_column])
        relationship_values = await self._inherit_and_validate_relationship_owners(
            conn, spec, relationship_values, creating=False
        )
        await self._validate_entity_references(conn, current_user, spec, relationship_values)
        # Build both SQL and parameters from the final write-only mapping. Relationship
        # hydration above deliberately works on a copy, so validation-only owner/FK
        # values cannot add parameters without matching SET placeholders.
        assignments = [sql.SQL("{} = %s").format(sql.Identifier(column)) for column in values]
        assignments.append(sql.SQL("updated_at = NOW()"))
        if spec.table == "leads":
            assignments.append(sql.SQL("version = version + 1"))
        update_statement = sql.SQL("UPDATE {} SET {} WHERE id = %s RETURNING *").format(
            sql.Identifier(spec.table), sql.SQL(", ").join(assignments)
        )
        update_params = [*values.values(), entity_id]
        result = await conn.execute(update_statement, update_params)
        entity = output_type.model_validate(await result.fetchone())
        await self._write_audit(
            conn,
            actor_user_id=current_user.id,
            action=f"{spec.entity_type}.update",
            entity_type=spec.entity_type,
            entity_id=entity_id,
            conversation_id=conversation_id,
            request_id=request_id,
            before_data=output_type.model_validate(before).model_dump(mode="json"),
            after_data=entity.model_dump(mode="json"),
        )
        return entity

    async def _validate_relationship_change(
        self,
        conn: AsyncConnection,
        spec: EntitySpec,
        entity_id: UUID,
        before: dict[str, Any],
        values: dict[str, Any],
    ) -> None:
        if spec.table == "contacts" and "account_id" in values:
            if values["account_id"] == before["account_id"]:
                return
            blockers = (
                "SELECT 1 FROM lead_conversions WHERE contact_id = %s",
                "SELECT 1 FROM opportunities WHERE primary_contact_id = %s AND deleted_at IS NULL",
                "SELECT 1 FROM activities WHERE contact_id = %s",
            )
            for statement in blockers:
                result = await conn.execute(statement + " LIMIT 1", (entity_id,))
                if await result.fetchone():
                    raise InvalidActionError("联系人仍被转换、商机或活动引用，不能更换公司")
        if spec.table == "opportunities" and any(
            field in values and values[field] != before[field]
            for field in ("account_id", "primary_contact_id")
        ):
            for statement in (
                "SELECT 1 FROM lead_conversions WHERE opportunity_id = %s",
                "SELECT 1 FROM activities WHERE opportunity_id = %s",
            ):
                result = await conn.execute(statement + " LIMIT 1", (entity_id,))
                if await result.fetchone():
                    raise InvalidActionError("商机仍被转换或活动引用，不能更改关联公司/联系人")

    async def _validate_owner_change(
        self,
        conn: AsyncConnection,
        spec: EntitySpec,
        entity_id: UUID,
        new_owner_id: UUID | None,
    ) -> None:
        """Reject isolated owner edits that would split an existing CRM chain."""
        checks: dict[str, tuple[str, ...]] = {
            "accounts": (
                "SELECT 1 FROM contacts WHERE account_id = %s AND deleted_at IS NULL "
                "AND owner_id IS DISTINCT FROM %s",
                "SELECT 1 FROM opportunities WHERE account_id = %s AND deleted_at IS NULL "
                "AND owner_id IS DISTINCT FROM %s",
                "SELECT 1 FROM activities WHERE account_id = %s "
                "AND assigned_user_id IS DISTINCT FROM %s",
            ),
            "contacts": (
                "SELECT 1 FROM accounts a JOIN contacts c ON c.account_id = a.id "
                "WHERE c.id = %s AND a.deleted_at IS NULL AND a.owner_id IS DISTINCT FROM %s",
                "SELECT 1 FROM opportunities WHERE primary_contact_id = %s "
                "AND deleted_at IS NULL AND owner_id IS DISTINCT FROM %s",
                "SELECT 1 FROM activities WHERE contact_id = %s "
                "AND assigned_user_id IS DISTINCT FROM %s",
            ),
            "opportunities": (
                "SELECT 1 FROM accounts a JOIN opportunities o ON o.account_id = a.id "
                "WHERE o.id = %s AND a.deleted_at IS NULL AND a.owner_id IS DISTINCT FROM %s",
                "SELECT 1 FROM activities WHERE opportunity_id = %s "
                "AND assigned_user_id IS DISTINCT FROM %s",
            ),
            "leads": (
                "SELECT 1 FROM lead_conversions lc JOIN accounts a ON a.id = lc.account_id "
                "WHERE lc.lead_id = %s AND a.owner_id IS DISTINCT FROM %s",
                "SELECT 1 FROM activities WHERE lead_id = %s "
                "AND assigned_user_id IS DISTINCT FROM %s",
            ),
        }
        for statement in checks.get(spec.table, ()):
            result = await conn.execute(statement + " LIMIT 1", (entity_id, new_owner_id))
            if await result.fetchone():
                raise InvalidActionError("不能单独修改负责人；请先解除关联或执行整链转移")

    async def _delete_entity(
        self,
        current_user: CurrentUser,
        spec: EntitySpec,
        entity_id: UUID,
        output_type: type[EntityOutT],
        *,
        request_id: str | None = None,
    ) -> bool:
        require_permission(current_user, Permission.CUSTOMER_DELETE)
        conditions: list[sql.Composable] = [sql.SQL("id = %s")]
        params: list[Any] = [entity_id]
        if spec.soft_delete:
            conditions.append(sql.SQL("deleted_at IS NULL"))
        visibility, visibility_params = self._entity_visibility(current_user, spec)
        if visibility is not None:
            conditions.append(visibility)
            params.extend(visibility_params)
        select_statement = sql.SQL("SELECT * FROM {} WHERE {} FOR UPDATE").format(
            sql.Identifier(spec.table), sql.SQL(" AND ").join(conditions)
        )
        if spec.soft_delete:
            delete_statement = sql.SQL(
                "UPDATE {} SET deleted_at = NOW(), updated_at = NOW() WHERE id = %s"
            ).format(sql.Identifier(spec.table))
            if spec.table == "leads":
                delete_statement = sql.SQL(
                    "UPDATE leads SET deleted_at = NOW(), updated_at = NOW(), "
                    "version = version + 1 WHERE id = %s"
                )
        else:
            delete_statement = sql.SQL("DELETE FROM {} WHERE id = %s").format(
                sql.Identifier(spec.table)
            )
        async with self.pool.connection() as conn, conn.transaction():
            result = await conn.execute(select_statement, params)
            before = await result.fetchone()
            if not before:
                return False
            await self._validate_entity_deletion(conn, spec, entity_id)
            await conn.execute(delete_statement, (entity_id,))
            await self._write_audit(
                conn,
                actor_user_id=current_user.id,
                action=f"{spec.entity_type}.delete",
                entity_type=spec.entity_type,
                entity_id=entity_id,
                request_id=request_id,
                before_data=output_type.model_validate(before).model_dump(mode="json"),
            )
        return True

    async def _validate_entity_deletion(
        self, conn: AsyncConnection, spec: EntitySpec, entity_id: UUID
    ) -> None:
        """Keep active/converted relationship graphs intact under application soft deletion."""
        blockers: dict[str, tuple[str, ...]] = {
            "accounts": (
                "SELECT 1 FROM contacts WHERE account_id = %s AND deleted_at IS NULL",
                "SELECT 1 FROM opportunities WHERE account_id = %s AND deleted_at IS NULL",
                "SELECT 1 FROM lead_conversions WHERE account_id = %s",
                "SELECT 1 FROM activities WHERE account_id = %s",
            ),
            "contacts": (
                "SELECT 1 FROM opportunities WHERE primary_contact_id = %s AND deleted_at IS NULL",
                "SELECT 1 FROM lead_conversions WHERE contact_id = %s",
                "SELECT 1 FROM activities WHERE contact_id = %s",
            ),
            "opportunities": (
                "SELECT 1 FROM lead_conversions WHERE opportunity_id = %s",
                "SELECT 1 FROM activities WHERE opportunity_id = %s",
            ),
            "leads": (
                "SELECT 1 FROM lead_conversions WHERE lead_id = %s",
                "SELECT 1 FROM activities WHERE lead_id = %s",
            ),
        }
        for statement in blockers.get(spec.table, ()):
            result = await conn.execute(statement + " LIMIT 1", (entity_id,))
            if await result.fetchone():
                raise InvalidActionError("记录仍被有效 CRM 关系引用，不能删除")

    @staticmethod
    def _validate_lead_create_status(status: Any) -> None:
        """Reserve ``converted`` for the atomic lead-conversion workflow."""
        if status == "converted":
            raise InvalidActionError("线索只能通过正式转换流程进入 converted 状态")

    @staticmethod
    def _normalize_lead_status_update(
        before_status: str, values: dict[str, Any]
    ) -> dict[str, Any]:
        """Enforce the lead conversion state machine across every write surface."""
        normalized = dict(values)
        if "status" not in normalized:
            return normalized
        submitted_status = normalized["status"]
        if submitted_status == "converted" and before_status != "converted":
            raise InvalidActionError("线索只能通过正式转换流程进入 converted 状态")
        if before_status == "converted" and submitted_status != "converted":
            raise InvalidActionError("已转换线索不能通过普通更新离开 converted 状态")
        if before_status == "converted" and submitted_status == "converted":
            normalized.pop("status")
        return normalized

    async def list_leads(
        self,
        current_user: CurrentUser,
        *,
        query: str | None = None,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[LeadOut]:
        return await self._list_entities(
            current_user,
            ENTITY_SPECS["leads"],
            LeadOut,
            query=query,
            filters={"status": status},
            limit=limit,
            offset=offset,
        )

    async def get_lead(self, current_user: CurrentUser, entity_id: UUID) -> LeadOut | None:
        return await self._get_entity(current_user, ENTITY_SPECS["leads"], entity_id, LeadOut)

    async def create_lead(
        self, current_user: CurrentUser, data: LeadCreate, *, request_id: str | None = None
    ) -> LeadOut:
        return await self._create_entity(
            current_user, ENTITY_SPECS["leads"], data, LeadOut, request_id=request_id
        )

    async def update_lead(
        self,
        current_user: CurrentUser,
        entity_id: UUID,
        data: LeadUpdate,
        *,
        request_id: str | None = None,
    ) -> LeadOut | None:
        return await self._update_entity(
            current_user,
            ENTITY_SPECS["leads"],
            entity_id,
            data,
            LeadOut,
            request_id=request_id,
        )

    async def delete_lead(
        self, current_user: CurrentUser, entity_id: UUID, *, request_id: str | None = None
    ) -> bool:
        return await self._delete_entity(
            current_user, ENTITY_SPECS["leads"], entity_id, LeadOut, request_id=request_id
        )

    async def list_accounts(
        self,
        current_user: CurrentUser,
        *,
        query: str | None = None,
        status: str | None = None,
        owner_id: UUID | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[AccountOut]:
        return await self._list_entities(
            current_user,
            ENTITY_SPECS["accounts"],
            AccountOut,
            query=query,
            filters={"status": status, "owner_id": owner_id},
            limit=limit,
            offset=offset,
        )

    async def get_account(self, current_user: CurrentUser, entity_id: UUID) -> AccountOut | None:
        return await self._get_entity(current_user, ENTITY_SPECS["accounts"], entity_id, AccountOut)

    async def create_account(
        self,
        current_user: CurrentUser,
        data: AccountCreate,
        *,
        request_id: str | None = None,
    ) -> AccountOut:
        return await self._create_entity(
            current_user,
            ENTITY_SPECS["accounts"],
            data,
            AccountOut,
            request_id=request_id,
        )

    async def update_account(
        self,
        current_user: CurrentUser,
        entity_id: UUID,
        data: AccountUpdate,
        *,
        request_id: str | None = None,
    ) -> AccountOut | None:
        return await self._update_entity(
            current_user,
            ENTITY_SPECS["accounts"],
            entity_id,
            data,
            AccountOut,
            request_id=request_id,
        )

    async def delete_account(
        self, current_user: CurrentUser, entity_id: UUID, *, request_id: str | None = None
    ) -> bool:
        return await self._delete_entity(
            current_user,
            ENTITY_SPECS["accounts"],
            entity_id,
            AccountOut,
            request_id=request_id,
        )

    async def list_contacts(
        self,
        current_user: CurrentUser,
        *,
        query: str | None = None,
        account_id: UUID | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[ContactOut]:
        return await self._list_entities(
            current_user,
            ENTITY_SPECS["contacts"],
            ContactOut,
            query=query,
            filters={"account_id": account_id},
            limit=limit,
            offset=offset,
        )

    async def get_contact(self, current_user: CurrentUser, entity_id: UUID) -> ContactOut | None:
        return await self._get_entity(current_user, ENTITY_SPECS["contacts"], entity_id, ContactOut)

    async def create_contact(
        self,
        current_user: CurrentUser,
        data: ContactCreate,
        *,
        request_id: str | None = None,
    ) -> ContactOut:
        return await self._create_entity(
            current_user,
            ENTITY_SPECS["contacts"],
            data,
            ContactOut,
            request_id=request_id,
        )

    async def update_contact(
        self,
        current_user: CurrentUser,
        entity_id: UUID,
        data: ContactUpdate,
        *,
        request_id: str | None = None,
    ) -> ContactOut | None:
        return await self._update_entity(
            current_user,
            ENTITY_SPECS["contacts"],
            entity_id,
            data,
            ContactOut,
            request_id=request_id,
        )

    async def delete_contact(
        self, current_user: CurrentUser, entity_id: UUID, *, request_id: str | None = None
    ) -> bool:
        return await self._delete_entity(
            current_user,
            ENTITY_SPECS["contacts"],
            entity_id,
            ContactOut,
            request_id=request_id,
        )

    async def list_opportunities(
        self,
        current_user: CurrentUser,
        *,
        query: str | None = None,
        stage: str | None = None,
        account_id: UUID | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[OpportunityOut]:
        return await self._list_entities(
            current_user,
            ENTITY_SPECS["opportunities"],
            OpportunityOut,
            query=query,
            filters={"stage": stage, "account_id": account_id},
            limit=limit,
            offset=offset,
        )

    async def get_opportunity(
        self, current_user: CurrentUser, entity_id: UUID
    ) -> OpportunityOut | None:
        return await self._get_entity(
            current_user, ENTITY_SPECS["opportunities"], entity_id, OpportunityOut
        )

    async def create_opportunity(
        self,
        current_user: CurrentUser,
        data: OpportunityCreate,
        *,
        request_id: str | None = None,
    ) -> OpportunityOut:
        return await self._create_entity(
            current_user,
            ENTITY_SPECS["opportunities"],
            data,
            OpportunityOut,
            request_id=request_id,
        )

    async def update_opportunity(
        self,
        current_user: CurrentUser,
        entity_id: UUID,
        data: OpportunityUpdate,
        *,
        request_id: str | None = None,
    ) -> OpportunityOut | None:
        return await self._update_entity(
            current_user,
            ENTITY_SPECS["opportunities"],
            entity_id,
            data,
            OpportunityOut,
            request_id=request_id,
        )

    async def delete_opportunity(
        self, current_user: CurrentUser, entity_id: UUID, *, request_id: str | None = None
    ) -> bool:
        return await self._delete_entity(
            current_user,
            ENTITY_SPECS["opportunities"],
            entity_id,
            OpportunityOut,
            request_id=request_id,
        )

    async def list_activities(
        self,
        current_user: CurrentUser,
        *,
        query: str | None = None,
        status: str | None = None,
        assigned_user_id: UUID | None = None,
        account_id: UUID | None = None,
        contact_id: UUID | None = None,
        lead_id: UUID | None = None,
        opportunity_id: UUID | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[ActivityOut]:
        require_permission(current_user, Permission.CUSTOMER_READ)
        spec = ENTITY_SPECS["activities"]
        conditions: list[sql.Composable] = []
        params: list[Any] = []
        visibility, visibility_params = self._entity_visibility(current_user, spec)
        if visibility is not None:
            conditions.append(
                sql.SQL("a.{} = %s").format(sql.Identifier(spec.owner_column))
            )
            params.extend(visibility_params)
        if query:
            pattern = f"%{query}%"
            conditions.append(
                sql.SQL("(")
                + sql.SQL(" OR ").join(
                    sql.SQL("a.{} ILIKE %s").format(sql.Identifier(column))
                    for column in spec.search_columns
                )
                + sql.SQL(")")
            )
            params.extend([pattern] * len(spec.search_columns))
        for column, value in (
            ("status", status),
            ("assigned_user_id", assigned_user_id),
            ("contact_id", contact_id),
            ("lead_id", lead_id),
            ("opportunity_id", opportunity_id),
        ):
            if value is not None:
                conditions.append(sql.SQL("a.{} = %s").format(sql.Identifier(column)))
                params.append(value)
        if account_id is not None:
            conditions.append(self._activity_account_chain_condition())
            params.extend([account_id] * 4)
        where_clause = (
            sql.SQL(" WHERE ") + sql.SQL(" AND ").join(conditions) if conditions else sql.SQL("")
        )
        statement = (
            sql.SQL("SELECT a.* FROM activities a{} ").format(where_clause)
            + sql.SQL("ORDER BY a.updated_at DESC, a.created_at DESC LIMIT %s OFFSET %s")
        )
        params.extend([limit, offset])
        async with self.pool.connection() as conn:
            result = await conn.execute(statement, params)
            records = await result.fetchall()
        return [ActivityOut.model_validate(record) for record in records]

    @staticmethod
    def _activity_account_chain_condition() -> sql.Composable:
        """Match every activity that belongs to an account's full CRM relationship graph."""
        return sql.SQL(
            "(a.account_id = %s OR "
            "EXISTS (SELECT 1 FROM contacts c_scope "
            "WHERE c_scope.id = a.contact_id AND c_scope.account_id = %s "
            "AND c_scope.deleted_at IS NULL) OR "
            "EXISTS (SELECT 1 FROM opportunities o_scope "
            "WHERE o_scope.id = a.opportunity_id AND o_scope.account_id = %s "
            "AND o_scope.deleted_at IS NULL) OR "
            "EXISTS (SELECT 1 FROM lead_conversions lc_scope "
            "WHERE lc_scope.lead_id = a.lead_id AND lc_scope.account_id = %s))"
        )

    async def get_activity(self, current_user: CurrentUser, entity_id: UUID) -> ActivityOut | None:
        return await self._get_entity(
            current_user, ENTITY_SPECS["activities"], entity_id, ActivityOut
        )

    async def create_activity(
        self,
        current_user: CurrentUser,
        data: ActivityCreate,
        *,
        request_id: str | None = None,
    ) -> ActivityOut:
        return await self._create_entity(
            current_user,
            ENTITY_SPECS["activities"],
            data,
            ActivityOut,
            request_id=request_id,
        )

    async def update_activity(
        self,
        current_user: CurrentUser,
        entity_id: UUID,
        data: ActivityUpdate,
        *,
        request_id: str | None = None,
    ) -> ActivityOut | None:
        return await self._update_entity(
            current_user,
            ENTITY_SPECS["activities"],
            entity_id,
            data,
            ActivityOut,
            request_id=request_id,
        )

    async def delete_activity(
        self, current_user: CurrentUser, entity_id: UUID, *, request_id: str | None = None
    ) -> bool:
        return await self._delete_entity(
            current_user,
            ENTITY_SPECS["activities"],
            entity_id,
            ActivityOut,
            request_id=request_id,
        )

    async def get_account_overview(
        self, current_user: CurrentUser, account_id: UUID
    ) -> AccountOverviewOut | None:
        """Return an owner-scoped, active-only 360 degree company view."""
        account = await self.get_account(current_user, account_id)
        if not account:
            return None
        contacts = await self.list_contacts(
            current_user, account_id=account_id, limit=100, offset=0
        )
        opportunities = await self.list_opportunities(
            current_user, account_id=account_id, limit=100, offset=0
        )
        visibility, visibility_params = self._entity_visibility(current_user, ENTITY_SPECS["leads"])
        conditions = [sql.SQL("lc.account_id = %s"), sql.SQL("l.deleted_at IS NULL")]
        params: list[Any] = [account_id]
        if visibility is not None:
            conditions.append(sql.SQL("l.owner_id = %s"))
            params.extend(visibility_params)
        async with self.pool.connection() as conn:
            count_scope = " AND owner_id = %s" if current_user.role is Role.SALES else ""
            count_params: list[Any] = [account_id]
            if current_user.role is Role.SALES:
                count_params.append(current_user.id)
            contact_count = await conn.execute(
                f"SELECT COUNT(*)::int AS count FROM contacts WHERE account_id = %s "
                f"AND deleted_at IS NULL{count_scope}",
                count_params,
            )
            opportunity_count = await conn.execute(
                f"SELECT COUNT(*)::int AS count FROM opportunities WHERE account_id = %s "
                f"AND deleted_at IS NULL{count_scope}",
                count_params,
            )
            contact_total = (await contact_count.fetchone())["count"]
            opportunity_total = (await opportunity_count.fetchone())["count"]
            activity_conditions = [self._activity_account_chain_condition()]
            activity_params: list[Any] = [account_id] * 4
            if current_user.role is Role.SALES:
                activity_conditions.append(sql.SQL("a.assigned_user_id = %s"))
                activity_params.append(current_user.id)
            activity_count_result = await conn.execute(
                sql.SQL(
                    "SELECT COUNT(*)::int AS count FROM activities a WHERE {}"
                ).format(sql.SQL(" AND ").join(activity_conditions)),
                activity_params,
            )
            activity_total = (await activity_count_result.fetchone())["count"]
            activity_result = await conn.execute(
                sql.SQL(
                    "SELECT a.* FROM activities a WHERE {} "
                    "ORDER BY a.updated_at DESC, a.created_at DESC LIMIT 100"
                ).format(sql.SQL(" AND ").join(activity_conditions)),
                activity_params,
            )
            activity_records = await activity_result.fetchall()
            result = await conn.execute(
                sql.SQL(
                    "SELECT lc.* FROM lead_conversions lc JOIN leads l ON l.id = lc.lead_id "
                    "WHERE {} ORDER BY lc.converted_at DESC"
                ).format(sql.SQL(" AND ").join(conditions)),
                params,
            )
            conversions = await result.fetchall()
        return AccountOverviewOut(
            account=account,
            contacts=contacts,
            opportunities=opportunities,
            activities=[ActivityOut.model_validate(record) for record in activity_records],
            conversion_sources=[
                LeadConversionRecordOut.model_validate(record) for record in conversions
            ],
            totals={
                "contacts": contact_total,
                "opportunities": opportunity_total,
                "activities": activity_total,
                "conversion_sources": len(conversions),
            },
            truncated={
                "contacts": contact_total > len(contacts),
                "opportunities": opportunity_total > len(opportunities),
                "activities": activity_total > len(activity_records),
                "conversion_sources": False,
            },
        )

    async def transfer_account_chain(
        self,
        current_user: CurrentUser,
        account_id: UUID,
        new_owner_id: UUID,
        *,
        request_id: str | None = None,
    ) -> AccountChainTransferOut | None:
        """Atomically transfer an entire company graph; only admins may manage users."""
        require_permission(current_user, Permission.USERS_MANAGE)
        async with self.pool.connection() as conn, conn.transaction():
            target = await conn.execute(
                "SELECT id FROM users WHERE id = %s AND is_active = TRUE FOR KEY SHARE",
                (new_owner_id,),
            )
            if not await target.fetchone():
                raise InvalidActionError("新负责人不是有效账号")
            account_result = await conn.execute(
                """
                SELECT * FROM accounts
                WHERE id = %s AND deleted_at IS NULL FOR UPDATE
                """,
                (account_id,),
            )
            account = await account_result.fetchone()
            if not account:
                return None
            conversion_result = await conn.execute(
                """
                SELECT lead_id FROM lead_conversions
                WHERE account_id = %s FOR UPDATE
                """,
                (account_id,),
            )
            lead_ids = [row["lead_id"] for row in await conversion_result.fetchall()]
            # Lock every current graph node before changing owners. Concurrent child/activity
            # inserts take FOR KEY SHARE on these rows and therefore wait until the transfer
            # commits, then observe the new owner during their own validation.
            await conn.execute(
                """
                SELECT id FROM contacts
                WHERE account_id = %s AND deleted_at IS NULL FOR UPDATE
                """,
                (account_id,),
            )
            await conn.execute(
                """
                SELECT id FROM opportunities
                WHERE account_id = %s AND deleted_at IS NULL FOR UPDATE
                """,
                (account_id,),
            )
            if lead_ids:
                await conn.execute(
                    "SELECT id FROM leads WHERE id = ANY(%s) FOR UPDATE", (lead_ids,)
                )
            contact_result = await conn.execute(
                """
                UPDATE contacts SET owner_id = %s, updated_at = NOW()
                WHERE account_id = %s AND deleted_at IS NULL
                """,
                (new_owner_id, account_id),
            )
            opportunity_result = await conn.execute(
                """
                UPDATE opportunities SET owner_id = %s, updated_at = NOW()
                WHERE account_id = %s AND deleted_at IS NULL
                """,
                (new_owner_id, account_id),
            )
            leads_updated = 0
            if lead_ids:
                leads_result = await conn.execute(
                    """
                    UPDATE leads SET owner_id = %s, updated_at = NOW(), version = version + 1
                    WHERE id = ANY(%s) AND deleted_at IS NULL
                    """,
                    (new_owner_id, lead_ids),
                )
                leads_updated = leads_result.rowcount
            activity_result = await conn.execute(
                """
                UPDATE activities a
                SET assigned_user_id = %s, updated_at = NOW()
                WHERE a.account_id = %s
                   OR a.contact_id IN (
                       SELECT id FROM contacts WHERE account_id = %s
                   )
                   OR a.opportunity_id IN (
                       SELECT id FROM opportunities WHERE account_id = %s
                   )
                   OR a.lead_id = ANY(%s)
                """,
                (new_owner_id, account_id, account_id, account_id, lead_ids),
            )
            await conn.execute(
                "UPDATE accounts SET owner_id = %s, updated_at = NOW() WHERE id = %s",
                (new_owner_id, account_id),
            )
            outcome = AccountChainTransferOut(
                account_id=account_id,
                previous_owner_id=account["owner_id"],
                new_owner_id=new_owner_id,
                contacts_updated=contact_result.rowcount,
                opportunities_updated=opportunity_result.rowcount,
                leads_updated=leads_updated,
                activities_updated=activity_result.rowcount,
            )
            await self._write_audit(
                conn,
                actor_user_id=current_user.id,
                action="account.chain.transfer",
                entity_type="account",
                entity_id=account_id,
                request_id=request_id,
                before_data={"owner_id": str(account["owner_id"]) if account["owner_id"] else None},
                after_data={"owner_id": str(new_owner_id)},
                metadata=outcome.model_dump(mode="json"),
            )
            return outcome

    async def convert_lead(
        self,
        current_user: CurrentUser,
        lead_id: UUID,
        data: LeadConversionRequest,
        *,
        request_id: str | None = None,
    ) -> LeadConversionOut:
        require_permission(current_user, Permission.CUSTOMER_CREATE)
        require_permission(current_user, Permission.CUSTOMER_UPDATE)
        if data.expected_version is None:
            raise InvalidActionError("转换确认缺少 expected_version，请刷新线索后重试")
        async with self.pool.connection() as conn, conn.transaction():
            return await self._convert_lead_conn(
                conn,
                current_user,
                lead_id,
                data,
                request_id=request_id,
                expected_version=data.expected_version,
            )

    async def _convert_lead_conn(
        self,
        conn: AsyncConnection,
        current_user: CurrentUser,
        lead_id: UUID,
        data: LeadConversionRequest,
        *,
        conversation_id: UUID | None = None,
        request_id: str | None = None,
        expected_version: int | None = None,
    ) -> LeadConversionOut:
        """Atomically convert one lead into its permanent account/contact graph."""
        require_permission(current_user, Permission.CUSTOMER_CREATE)
        require_permission(current_user, Permission.CUSTOMER_UPDATE)
        visibility, visibility_params = self._entity_visibility(current_user, ENTITY_SPECS["leads"])
        conditions = [sql.SQL("id = %s"), sql.SQL("deleted_at IS NULL")]
        params: list[Any] = [lead_id]
        if visibility is not None:
            conditions.append(visibility)
            params.extend(visibility_params)
        lead_result = await conn.execute(
            sql.SQL("SELECT * FROM leads WHERE {} FOR UPDATE").format(
                sql.SQL(" AND ").join(conditions)
            ),
            params,
        )
        lead_record = await lead_result.fetchone()
        if not lead_record:
            raise InvalidActionError("线索不存在或当前账号无权访问")
        if expected_version is not None and lead_record["version"] != expected_version:
            raise InvalidActionError("线索已被其他操作更新，请重新发起转换")
        existing_result = await conn.execute(
            "SELECT id FROM lead_conversions WHERE lead_id = %s", (lead_id,)
        )
        if await existing_result.fetchone():
            raise InvalidActionError("该线索已经转换，不能重复转换")
        lead = LeadOut.model_validate(lead_record)
        if lead.status == "converted":
            raise InvalidActionError("该线索已标记为已转换，不能重复转换")
        owner_id = lead.owner_id
        if owner_id is None:
            raise InvalidActionError("线索没有负责人，转换前请先分配负责人")

        account: AccountOut
        if data.account_id:
            account = await self._get_locked_conversion_target(
                conn, ENTITY_SPECS["accounts"], data.account_id, AccountOut
            )
            self._require_matching_owner(owner_id, account.owner_id, "公司")
        else:
            account_data = data.account
            if account_data is None:
                if not lead.company_name:
                    raise InvalidActionError("线索没有公司名称，请提供 account_id 或 account")
                account_data = AccountCreate(
                    name=lead.company_name,
                    phone=lead.phone,
                    email=lead.email,
                    source=lead.source,
                    owner_id=owner_id,
                    description=lead.description,
                )
            else:
                account_data = account_data.model_copy(update={"owner_id": owner_id})
            account = await self._create_entity_conn(
                conn,
                current_user,
                ENTITY_SPECS["accounts"],
                account_data,
                AccountOut,
                conversation_id=conversation_id,
                request_id=request_id,
            )

        contact: ContactOut
        if data.contact_id:
            contact = await self._get_locked_conversion_target(
                conn, ENTITY_SPECS["contacts"], data.contact_id, ContactOut
            )
            self._require_matching_owner(owner_id, contact.owner_id, "联系人")
            if contact.account_id != account.id:
                raise InvalidActionError("现有联系人不属于所选公司")
        else:
            contact_data = data.contact or ContactCreate(
                first_name=lead.first_name,
                last_name=lead.last_name,
                title=lead.job_title,
                email=lead.email,
                phone=lead.phone,
                source=lead.source,
                description=lead.description,
            )
            contact_data = contact_data.model_copy(
                update={"account_id": account.id, "owner_id": owner_id}
            )
            contact = await self._create_entity_conn(
                conn,
                current_user,
                ENTITY_SPECS["contacts"],
                contact_data,
                ContactOut,
                conversation_id=conversation_id,
                request_id=request_id,
            )

        opportunity: OpportunityOut | None = None
        if data.opportunity:
            opportunity_data = data.opportunity.model_copy(
                update={
                    "account_id": account.id,
                    "primary_contact_id": contact.id,
                    "owner_id": owner_id,
                }
            )
            opportunity = await self._create_entity_conn(
                conn,
                current_user,
                ENTITY_SPECS["opportunities"],
                opportunity_data,
                OpportunityOut,
                conversation_id=conversation_id,
                request_id=request_id,
            )

        before_data = lead.model_dump(mode="json")
        lead_update = await conn.execute(
            """
            UPDATE leads
            SET status = 'converted', updated_at = NOW(), version = version + 1
            WHERE id = %s RETURNING *
            """,
            (lead_id,),
        )
        converted_lead = LeadOut.model_validate(await lead_update.fetchone())
        snapshot = {
            "lead": before_data,
            "account_id": str(account.id),
            "contact_id": str(contact.id),
            "opportunity_id": str(opportunity.id) if opportunity else None,
        }
        try:
            conversion_result = await conn.execute(
                """
                INSERT INTO lead_conversions
                    (lead_id, account_id, contact_id, opportunity_id,
                     converted_by, snapshot)
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING *
                """,
                (
                    lead_id,
                    account.id,
                    contact.id,
                    opportunity.id if opportunity else None,
                    current_user.id,
                    Jsonb(snapshot),
                ),
            )
        except UniqueViolation as exc:
            raise InvalidActionError("该线索已经转换，不能重复转换") from exc
        conversion = LeadConversionRecordOut.model_validate(await conversion_result.fetchone())
        outcome = LeadConversionOut(
            conversion=conversion,
            lead=converted_lead,
            account=account,
            contact=contact,
            opportunity=opportunity,
        )
        await self._write_audit(
            conn,
            actor_user_id=current_user.id,
            action="lead.convert",
            entity_type="lead",
            entity_id=lead_id,
            conversation_id=conversation_id,
            request_id=request_id,
            before_data=before_data,
            after_data=outcome.model_dump(mode="json"),
        )
        return outcome

    async def _get_locked_conversion_target(
        self,
        conn: AsyncConnection,
        spec: EntitySpec,
        entity_id: UUID,
        output_type: type[EntityOutT],
    ) -> EntityOutT:
        conditions = [sql.SQL("id = %s")]
        if spec.soft_delete:
            conditions.append(sql.SQL("deleted_at IS NULL"))
        result = await conn.execute(
            sql.SQL("SELECT * FROM {} WHERE {} FOR UPDATE").format(
                sql.Identifier(spec.table), sql.SQL(" AND ").join(conditions)
            ),
            (entity_id,),
        )
        record = await result.fetchone()
        if not record:
            raise InvalidActionError(f"{spec.entity_type} 不存在或已删除")
        return output_type.model_validate(record)

    @staticmethod
    def _require_matching_owner(
        lead_owner_id: UUID, target_owner_id: UUID | None, label: str
    ) -> None:
        if target_owner_id != lead_owner_id:
            raise EntityAccessError(f"{label}负责人必须与线索负责人一致")

    async def create_pending_customer_insert(
        self,
        current_user: CurrentUser,
        conversation_id: UUID,
        data: CustomerCreate,
    ) -> PendingActionOut:
        require_permission(current_user, Permission.CUSTOMER_CREATE)
        self._validate_lead_create_status(data.status)
        return await self._create_pending_action(
            current_user,
            conversation_id,
            action_type="insert_customer",
            payload=data.model_dump(mode="json"),
        )

    async def create_pending_lead_conversion(
        self,
        current_user: CurrentUser,
        conversation_id: UUID,
        lead_id: UUID,
        data: LeadConversionRequest,
    ) -> PendingActionOut:
        require_permission(current_user, Permission.CUSTOMER_CREATE)
        require_permission(current_user, Permission.CUSTOMER_UPDATE)
        current = await self.get_lead(current_user, lead_id)
        if not current:
            raise InvalidActionError("线索不存在或当前账号无权访问")
        if current.status == "converted":
            raise InvalidActionError("该线索已标记为已转换，不能重复转换")
        if current.owner_id is None:
            raise InvalidActionError("线索没有负责人，转换前请先分配负责人")
        if not data.account_id and not data.account and not current.company_name:
            raise InvalidActionError("线索没有公司名称，请提供 account_id 或 account")
        if data.account_id:
            account = await self.get_account(current_user, data.account_id)
            if not account:
                raise InvalidActionError("公司不存在或当前账号无权访问")
            self._require_matching_owner(current.owner_id, account.owner_id, "公司")
        if data.contact_id:
            contact = await self.get_contact(current_user, data.contact_id)
            if not contact:
                raise InvalidActionError("联系人不存在或当前账号无权访问")
            self._require_matching_owner(current.owner_id, contact.owner_id, "联系人")
            if contact.account_id != data.account_id:
                raise InvalidActionError("现有联系人不属于所选公司")
        async with self.pool.connection() as conn:
            converted = await conn.execute(
                "SELECT id FROM lead_conversions WHERE lead_id = %s", (lead_id,)
            )
            if await converted.fetchone():
                raise InvalidActionError("该线索已经转换，不能重复转换")
        return await self._create_pending_action(
            current_user,
            conversation_id,
            action_type="convert_lead",
            payload={
                "lead_id": str(lead_id),
                "fields": data.model_dump(exclude={"expected_version"}, mode="json"),
                "expected_version": current.version,
                "current": current.model_dump(mode="json"),
            },
        )

    async def create_pending_customer_update(
        self,
        current_user: CurrentUser,
        conversation_id: UUID,
        customer_id: str,
        data: CustomerUpdate,
    ) -> PendingActionOut:
        require_permission(current_user, Permission.CUSTOMER_UPDATE)
        try:
            parsed_customer_id = UUID(customer_id)
        except ValueError as exc:
            raise InvalidActionError("customer_id 不是有效 UUID") from exc
        fields = data.model_dump(exclude_unset=True, mode="json")
        if not fields:
            raise InvalidActionError("至少提供一个需要更新的字段")
        current = await self.get_customer(current_user, parsed_customer_id)
        if not current:
            raise InvalidActionError("客户不存在或当前账号无权访问")
        fields = self._normalize_lead_status_update(current.status, fields)
        if not fields:
            raise InvalidActionError("至少提供一个实际发生变化的字段")
        return await self._create_pending_action(
            current_user,
            conversation_id,
            action_type="update_customer",
            payload={
                "customer_id": str(parsed_customer_id),
                "fields": fields,
                "expected_version": current.version,
                "current": current.model_dump(mode="json"),
            },
        )

    async def create_pending_entity_insert(
        self,
        current_user: CurrentUser,
        conversation_id: UUID,
        entity_type: str,
        data: BaseModel,
    ) -> PendingActionOut:
        """Stage a validated insert for one real CRM table."""
        require_permission(current_user, Permission.CUSTOMER_CREATE)
        models = ENTITY_MODELS.get(entity_type)
        if not models:
            raise InvalidActionError("不支持的 CRM 实体类型")
        create_model, _, _ = models
        payload_model = create_model.model_validate(
            data.model_dump(exclude_unset=True, mode="json")
        )
        if entity_type == "lead":
            self._validate_lead_create_status(payload_model.status)
        spec = ENTITY_SPECS[ENTITY_TABLES[entity_type]]
        prepared = self._prepare_entity_values(current_user, spec, payload_model, creating=True)
        async with self.pool.connection() as conn:
            await self._validate_entity_references(conn, current_user, spec, prepared)
        fields = payload_model.model_dump(exclude_unset=True, mode="json")
        return await self._create_pending_action(
            current_user,
            conversation_id,
            action_type=f"insert_{entity_type}",
            payload={
                "entity_type": entity_type,
                "fields": fields,
            },
        )

    async def create_pending_entity_update(
        self,
        current_user: CurrentUser,
        conversation_id: UUID,
        entity_type: str,
        entity_id: UUID,
        data: BaseModel,
    ) -> PendingActionOut:
        """Stage an owner-scoped update with an optimistic concurrency token."""
        require_permission(current_user, Permission.CUSTOMER_UPDATE)
        models = ENTITY_MODELS.get(entity_type)
        table = ENTITY_TABLES.get(entity_type)
        if not models or not table:
            raise InvalidActionError("不支持的 CRM 实体类型")
        _, update_model, output_model = models
        fields = data.model_dump(exclude_unset=True, mode="json")
        payload_model = update_model.model_validate(fields)
        fields = payload_model.model_dump(exclude_unset=True, mode="json")
        if not fields:
            raise InvalidActionError("至少提供一个需要更新的字段")
        spec = ENTITY_SPECS[table]
        current = await self._get_entity(
            current_user,
            spec,
            entity_id,
            output_model,
        )
        if not current:
            raise InvalidActionError("记录不存在或当前账号无权访问")
        if entity_type == "lead":
            fields = self._normalize_lead_status_update(current.status, fields)
        if not fields:
            raise InvalidActionError("至少提供一个实际发生变化的字段")
        payload: dict[str, Any] = {
            "entity_type": entity_type,
            "entity_id": str(entity_id),
            "fields": fields,
            "expected_updated_at": current.updated_at.isoformat(),
            "current": current.model_dump(mode="json"),
        }
        if entity_type == "lead":
            payload["expected_version"] = current.version
        return await self._create_pending_action(
            current_user,
            conversation_id,
            action_type=f"update_{entity_type}",
            payload=payload,
        )

    async def _create_pending_action(
        self,
        current_user: CurrentUser,
        conversation_id: UUID,
        *,
        action_type: str,
        payload: dict[str, Any],
    ) -> PendingActionOut:
        if not await self.get_conversation(current_user.id, conversation_id):
            raise InvalidActionError("当前会话不存在")
        canonical = json.dumps(
            {"action_type": action_type, "payload": payload},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        idempotency_key = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        expires_at = _now() + timedelta(minutes=30)
        async with self.pool.connection() as conn:
            result = await conn.execute(
                """
                INSERT INTO crm_pending_actions
                    (user_id, conversation_id, action_type, payload,
                     idempotency_key, expires_at)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (user_id, conversation_id, idempotency_key)
                    WHERE status = 'pending'
                DO UPDATE SET updated_at = NOW()
                RETURNING *
                """,
                (
                    current_user.id,
                    conversation_id,
                    action_type,
                    Jsonb(payload),
                    idempotency_key,
                    expires_at,
                ),
            )
            record = await result.fetchone()
        return PendingActionOut.model_validate(record)

    async def list_pending_actions(
        self,
        current_user: CurrentUser,
        *,
        conversation_id: UUID | None = None,
        pending_only: bool = True,
    ) -> list[PendingActionOut]:
        params: list[Any] = [current_user.id, conversation_id, conversation_id]
        status_clause = " AND status = 'pending'" if pending_only else ""
        async with self.pool.connection() as conn, conn.transaction():
            await conn.execute(
                """
                UPDATE crm_pending_actions
                SET status = 'expired', updated_at = NOW()
                WHERE user_id = %s AND status = 'pending' AND expires_at <= NOW()
                """,
                (current_user.id,),
            )
            result = await conn.execute(
                """
                SELECT * FROM crm_pending_actions
                WHERE user_id = %s AND (%s::uuid IS NULL OR conversation_id = %s)
                """
                + status_clause
                + " ORDER BY created_at DESC LIMIT 100",
                params,
            )
            records = await result.fetchall()
        return [PendingActionOut.model_validate(record) for record in records]

    async def decide_pending_action(
        self,
        current_user: CurrentUser,
        action_id: UUID,
        *,
        approve: bool,
        request_id: str | None = None,
    ) -> PendingActionOut | None:
        async with self.pool.connection() as conn, conn.transaction():
            action_result = await conn.execute(
                """
                SELECT * FROM crm_pending_actions
                WHERE id = %s AND user_id = %s
                FOR UPDATE
                """,
                (action_id, current_user.id),
            )
            action = await action_result.fetchone()
            if not action:
                return None
            if action["status"] != "pending":
                return PendingActionOut.model_validate(action)
            if action["expires_at"] <= _now():
                result = await conn.execute(
                    """
                    UPDATE crm_pending_actions
                    SET status = 'expired', updated_at = NOW()
                    WHERE id = %s RETURNING *
                    """,
                    (action_id,),
                )
                return PendingActionOut.model_validate(await result.fetchone())
            if not approve:
                result = await conn.execute(
                    """
                    UPDATE crm_pending_actions
                    SET status = 'rejected', decided_at = NOW(), updated_at = NOW()
                    WHERE id = %s RETURNING *
                    """,
                    (action_id,),
                )
                return PendingActionOut.model_validate(await result.fetchone())
            try:
                async with conn.transaction():
                    if action["action_type"] == "insert_customer":
                        require_permission(current_user, Permission.CUSTOMER_CREATE)
                        payload = CustomerCreate.model_validate(action["payload"])
                        customer = await self._insert_customer(
                            conn,
                            current_user,
                            payload,
                            conversation_id=action["conversation_id"],
                            request_id=request_id,
                        )
                    elif action["action_type"] == "update_customer":
                        require_permission(current_user, Permission.CUSTOMER_UPDATE)
                        payload = action["payload"]
                        customer = await self._update_customer(
                            conn,
                            current_user,
                            UUID(payload["customer_id"]),
                            CustomerUpdate.model_validate(payload["fields"]),
                            conversation_id=action["conversation_id"],
                            request_id=request_id,
                            expected_version=payload["expected_version"],
                        )
                        if customer is None:
                            raise InvalidActionError("客户不存在或当前账号无权访问")
                    elif action["action_type"] == "convert_lead":
                        payload = action["payload"]
                        conversion = await self._convert_lead_conn(
                            conn,
                            current_user,
                            UUID(payload["lead_id"]),
                            LeadConversionRequest.model_validate(payload["fields"]),
                            expected_version=payload["expected_version"],
                            conversation_id=action["conversation_id"],
                            request_id=request_id,
                        )
                    elif action["action_type"].startswith("insert_"):
                        entity_type = action["action_type"].removeprefix("insert_")
                        models = ENTITY_MODELS.get(entity_type)
                        table = ENTITY_TABLES.get(entity_type)
                        if not models or not table:
                            raise InvalidActionError("不支持的待确认实体新增动作")
                        create_model, _, output_model = models
                        payload = action["payload"]
                        if payload.get("entity_type") != entity_type:
                            raise InvalidActionError("待确认动作实体不一致")
                        entity = await self._create_entity_conn(
                            conn,
                            current_user,
                            ENTITY_SPECS[table],
                            create_model.model_validate(payload["fields"]),
                            output_model,
                            conversation_id=action["conversation_id"],
                            request_id=request_id,
                        )
                    elif action["action_type"].startswith("update_"):
                        entity_type = action["action_type"].removeprefix("update_")
                        models = ENTITY_MODELS.get(entity_type)
                        table = ENTITY_TABLES.get(entity_type)
                        if not models or not table:
                            raise InvalidActionError("不支持的待确认实体更新动作")
                        _, update_model, output_model = models
                        payload = action["payload"]
                        if payload.get("entity_type") != entity_type:
                            raise InvalidActionError("待确认动作实体不一致")
                        entity = await self._update_entity_conn(
                            conn,
                            current_user,
                            ENTITY_SPECS[table],
                            UUID(payload["entity_id"]),
                            update_model.model_validate(payload["fields"]),
                            output_model,
                            expected_updated_at=datetime.fromisoformat(
                                payload["expected_updated_at"]
                            ),
                            expected_version=payload.get("expected_version"),
                            conversation_id=action["conversation_id"],
                            request_id=request_id,
                        )
                        if entity is None:
                            raise InvalidActionError("记录不存在或当前账号无权访问")
                    else:
                        raise InvalidActionError("不支持的待确认动作")
                if action["action_type"] in {"insert_customer", "update_customer"}:
                    result_payload = {"customer": customer.model_dump(mode="json")}
                elif action["action_type"] == "convert_lead":
                    result_payload = {
                        "entity_type": "lead_conversion",
                        "entity": conversion.model_dump(mode="json"),
                    }
                else:
                    result_payload = {
                        "entity_type": entity_type,
                        "entity": entity.model_dump(mode="json"),
                    }
                result = await conn.execute(
                    """
                    UPDATE crm_pending_actions
                    SET status = 'approved', result = %s, decided_at = NOW(), updated_at = NOW()
                    WHERE id = %s RETURNING *
                    """,
                    (Jsonb(result_payload), action_id),
                )
            except Exception as exc:
                result = await conn.execute(
                    """
                    UPDATE crm_pending_actions
                    SET status = 'failed', error_message = %s,
                        decided_at = NOW(), updated_at = NOW()
                    WHERE id = %s RETURNING *
                    """,
                    (str(exc)[:2000], action_id),
                )
            return PendingActionOut.model_validate(await result.fetchone())

    async def _write_audit(
        self,
        conn: AsyncConnection,
        *,
        actor_user_id: UUID | None,
        action: str,
        entity_type: str,
        entity_id: UUID | None = None,
        conversation_id: UUID | None = None,
        request_id: str | None = None,
        before_data: dict[str, Any] | None = None,
        after_data: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        await conn.execute(
            """
            INSERT INTO crm_audit_log
                (actor_user_id, action, entity_type, entity_id, conversation_id,
                 request_id, before_data, after_data, metadata)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                actor_user_id,
                action,
                entity_type,
                entity_id,
                conversation_id,
                request_id,
                Jsonb(before_data) if before_data is not None else None,
                Jsonb(after_data) if after_data is not None else None,
                Jsonb(metadata or {}),
            ),
        )
