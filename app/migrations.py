"""Small, append-only PostgreSQL migration runner for the CRM application tables."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from psycopg import AsyncConnection


@dataclass(frozen=True)
class Migration:
    migration_id: str
    statements: tuple[str, ...]

    @property
    def checksum(self) -> str:
        payload = "\n-- statement --\n".join(self.statements).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()


MIGRATIONS = (
    Migration(
        migration_id="crm_app_000_core_schema",
        statements=(
            """
            CREATE TABLE IF NOT EXISTS users (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                username VARCHAR(100) NOT NULL UNIQUE,
                email VARCHAR(255) UNIQUE,
                first_name VARCHAR(100),
                last_name VARCHAR(100),
                role VARCHAR(50) NOT NULL DEFAULT 'sales',
                is_active BOOLEAN NOT NULL DEFAULT TRUE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS leads (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                first_name VARCHAR(100),
                last_name VARCHAR(100),
                company_name VARCHAR(255),
                email VARCHAR(255),
                phone VARCHAR(50),
                job_title VARCHAR(150),
                source VARCHAR(100),
                status VARCHAR(50) NOT NULL DEFAULT 'new',
                score INTEGER DEFAULT 0,
                owner_id UUID REFERENCES users(id) ON DELETE SET NULL,
                description TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                deleted_at TIMESTAMPTZ
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_leads_company_name ON leads(company_name)",
            "CREATE INDEX IF NOT EXISTS idx_leads_email ON leads(email)",
            "CREATE INDEX IF NOT EXISTS idx_leads_owner_id ON leads(owner_id)",
            "CREATE INDEX IF NOT EXISTS idx_leads_status ON leads(status)",
            """
            CREATE TABLE IF NOT EXISTS accounts (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                name VARCHAR(255) NOT NULL,
                industry VARCHAR(100),
                website VARCHAR(500),
                phone VARCHAR(50),
                email VARCHAR(255),
                address TEXT,
                city VARCHAR(100),
                state VARCHAR(100),
                country VARCHAR(100),
                employee_count INTEGER,
                annual_revenue NUMERIC(18, 2),
                status VARCHAR(50) NOT NULL DEFAULT 'active',
                source VARCHAR(100),
                owner_id UUID REFERENCES users(id) ON DELETE SET NULL,
                description TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                deleted_at TIMESTAMPTZ
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_accounts_name ON accounts(name)",
            "CREATE INDEX IF NOT EXISTS idx_accounts_owner_id ON accounts(owner_id)",
            """
            CREATE TABLE IF NOT EXISTS contacts (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                account_id UUID REFERENCES accounts(id) ON DELETE SET NULL,
                first_name VARCHAR(100),
                last_name VARCHAR(100),
                title VARCHAR(150),
                department VARCHAR(150),
                email VARCHAR(255),
                phone VARCHAR(50),
                mobile VARCHAR(50),
                wechat VARCHAR(100),
                linkedin VARCHAR(500),
                source VARCHAR(100),
                owner_id UUID REFERENCES users(id) ON DELETE SET NULL,
                description TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                deleted_at TIMESTAMPTZ
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_contacts_account_id ON contacts(account_id)",
            "CREATE INDEX IF NOT EXISTS idx_contacts_email ON contacts(email)",
            "CREATE INDEX IF NOT EXISTS idx_contacts_owner_id ON contacts(owner_id)",
            """
            CREATE TABLE IF NOT EXISTS opportunities (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                account_id UUID REFERENCES accounts(id) ON DELETE SET NULL,
                name VARCHAR(255) NOT NULL,
                amount NUMERIC(18, 2),
                currency VARCHAR(10) NOT NULL DEFAULT 'CNY',
                stage VARCHAR(50) NOT NULL DEFAULT 'prospecting',
                probability NUMERIC(5, 2) DEFAULT 0,
                expected_close_date DATE,
                source VARCHAR(100),
                owner_id UUID REFERENCES users(id) ON DELETE SET NULL,
                description TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                deleted_at TIMESTAMPTZ
            )
            """,
            """
            CREATE INDEX IF NOT EXISTS idx_opportunities_account_id
            ON opportunities(account_id)
            """,
            """
            CREATE INDEX IF NOT EXISTS idx_opportunities_expected_close_date
            ON opportunities(expected_close_date)
            """,
            """
            CREATE INDEX IF NOT EXISTS idx_opportunities_owner_id
            ON opportunities(owner_id)
            """,
            "CREATE INDEX IF NOT EXISTS idx_opportunities_stage ON opportunities(stage)",
            """
            CREATE TABLE IF NOT EXISTS activities (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                type VARCHAR(50) NOT NULL,
                subject VARCHAR(255) NOT NULL,
                description TEXT,
                status VARCHAR(50) NOT NULL DEFAULT 'planned',
                priority VARCHAR(50) NOT NULL DEFAULT 'normal',
                start_at TIMESTAMPTZ,
                end_at TIMESTAMPTZ,
                account_id UUID REFERENCES accounts(id) ON DELETE CASCADE,
                contact_id UUID REFERENCES contacts(id) ON DELETE CASCADE,
                lead_id UUID REFERENCES leads(id) ON DELETE CASCADE,
                opportunity_id UUID REFERENCES opportunities(id) ON DELETE CASCADE,
                assigned_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_activities_account_id ON activities(account_id)",
            """
            CREATE INDEX IF NOT EXISTS idx_activities_assigned_user_id
            ON activities(assigned_user_id)
            """,
            "CREATE INDEX IF NOT EXISTS idx_activities_contact_id ON activities(contact_id)",
            "CREATE INDEX IF NOT EXISTS idx_activities_lead_id ON activities(lead_id)",
            """
            CREATE INDEX IF NOT EXISTS idx_activities_opportunity_id
            ON activities(opportunity_id)
            """,
            "CREATE INDEX IF NOT EXISTS idx_activities_start_at ON activities(start_at)",
        ),
    ),
    Migration(
        migration_id="crm_app_001_auth_memory_and_audit",
        statements=(
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS password_hash TEXT",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS display_name VARCHAR(100)",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS last_login_at TIMESTAMPTZ",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS password_changed_at TIMESTAMPTZ",
            "ALTER TABLE leads ADD COLUMN IF NOT EXISTS extra JSONB NOT NULL DEFAULT '{}'::jsonb",
            "ALTER TABLE leads ADD COLUMN IF NOT EXISTS version INTEGER NOT NULL DEFAULT 1",
            """
            CREATE INDEX IF NOT EXISTS leads_owner_active_idx
            ON leads(owner_id, updated_at DESC) WHERE deleted_at IS NULL
            """,
            """
            CREATE TABLE IF NOT EXISTS crm_sessions (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                token_digest CHAR(64) NOT NULL UNIQUE,
                user_agent TEXT,
                ip_address TEXT,
                expires_at TIMESTAMPTZ NOT NULL,
                last_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """,
            """
            CREATE INDEX IF NOT EXISTS crm_sessions_user_idx
            ON crm_sessions(user_id, expires_at DESC)
            """,
            "CREATE INDEX IF NOT EXISTS crm_sessions_expiry_idx ON crm_sessions(expires_at)",
            """
            CREATE TABLE IF NOT EXISTS crm_conversations (
                id UUID PRIMARY KEY,
                owner_user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                title TEXT NOT NULL DEFAULT '新会话',
                message_count INTEGER NOT NULL DEFAULT 0 CHECK (message_count >= 0),
                is_archived BOOLEAN NOT NULL DEFAULT FALSE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                last_message_at TIMESTAMPTZ,
                UNIQUE (owner_user_id, id)
            )
            """,
            """
            CREATE INDEX IF NOT EXISTS crm_conversations_owner_updated_idx
            ON crm_conversations(owner_user_id, is_archived, updated_at DESC)
            """,
            """
            CREATE TABLE IF NOT EXISTS crm_conversation_memories (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                owner_user_id UUID NOT NULL,
                conversation_id UUID NOT NULL,
                memory_type TEXT NOT NULL DEFAULT 'fact',
                content TEXT NOT NULL,
                importance SMALLINT NOT NULL DEFAULT 3 CHECK (importance BETWEEN 1 AND 5),
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                FOREIGN KEY (owner_user_id, conversation_id)
                    REFERENCES crm_conversations(owner_user_id, id) ON DELETE CASCADE
            )
            """,
            """
            CREATE INDEX IF NOT EXISTS crm_conversation_memories_lookup_idx
            ON crm_conversation_memories(
                owner_user_id, conversation_id, importance DESC, updated_at DESC
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS crm_pending_actions (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                user_id UUID NOT NULL,
                conversation_id UUID NOT NULL,
                action_type TEXT NOT NULL
                    CHECK (action_type IN ('insert_customer', 'update_customer')),
                payload JSONB NOT NULL,
                idempotency_key CHAR(64) NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending', 'approved', 'rejected', 'expired', 'failed')),
                result JSONB,
                error_message TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                expires_at TIMESTAMPTZ NOT NULL,
                decided_at TIMESTAMPTZ,
                FOREIGN KEY (user_id, conversation_id)
                    REFERENCES crm_conversations(owner_user_id, id) ON DELETE CASCADE
            )
            """,
            """
            CREATE UNIQUE INDEX IF NOT EXISTS crm_pending_actions_open_uidx
            ON crm_pending_actions(user_id, conversation_id, idempotency_key)
            WHERE status = 'pending'
            """,
            """
            CREATE INDEX IF NOT EXISTS crm_pending_actions_user_idx
            ON crm_pending_actions(user_id, status, created_at DESC)
            """,
            """
            CREATE TABLE IF NOT EXISTS crm_audit_log (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                actor_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
                action TEXT NOT NULL,
                entity_type TEXT NOT NULL,
                entity_id UUID,
                conversation_id UUID,
                request_id TEXT,
                before_data JSONB,
                after_data JSONB,
                metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """,
            """
            CREATE INDEX IF NOT EXISTS crm_audit_actor_created_idx
            ON crm_audit_log(actor_user_id, created_at DESC)
            """,
            """
            CREATE INDEX IF NOT EXISTS crm_audit_entity_idx
            ON crm_audit_log(entity_type, entity_id, created_at DESC)
            """,
        ),
    ),
    Migration(
        migration_id="crm_app_002_multi_entity_pending_actions",
        statements=(
            """
            ALTER TABLE crm_pending_actions
            DROP CONSTRAINT IF EXISTS crm_pending_actions_action_type_check
            """,
            """
            ALTER TABLE crm_pending_actions
            ADD CONSTRAINT crm_pending_actions_action_type_check
            CHECK (
                action_type IN (
                    'insert_customer', 'update_customer',
                    'insert_lead', 'update_lead',
                    'insert_account', 'update_account',
                    'insert_contact', 'update_contact',
                    'insert_opportunity', 'update_opportunity',
                    'insert_activity', 'update_activity'
                )
            )
            """,
        ),
    ),
    Migration(
        migration_id="crm_app_003_lead_conversion_and_primary_contacts",
        statements=(
            """
            ALTER TABLE opportunities
            ADD COLUMN IF NOT EXISTS primary_contact_id UUID
            REFERENCES contacts(id) ON DELETE SET NULL
            """,
            """
            CREATE INDEX IF NOT EXISTS opportunities_primary_contact_idx
            ON opportunities(primary_contact_id) WHERE deleted_at IS NULL
            """,
            """
            CREATE TABLE IF NOT EXISTS lead_conversions (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                lead_id UUID NOT NULL UNIQUE REFERENCES leads(id) ON DELETE RESTRICT,
                account_id UUID NOT NULL REFERENCES accounts(id) ON DELETE RESTRICT,
                contact_id UUID NOT NULL REFERENCES contacts(id) ON DELETE RESTRICT,
                opportunity_id UUID REFERENCES opportunities(id) ON DELETE SET NULL,
                converted_by UUID REFERENCES users(id) ON DELETE SET NULL,
                converted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                snapshot JSONB NOT NULL DEFAULT '{}'::jsonb
            )
            """,
            """
            CREATE INDEX IF NOT EXISTS lead_conversions_account_idx
            ON lead_conversions(account_id, converted_at DESC)
            """,
            """
            ALTER TABLE crm_pending_actions
            DROP CONSTRAINT IF EXISTS crm_pending_actions_action_type_check
            """,
            """
            ALTER TABLE crm_pending_actions
            ADD CONSTRAINT crm_pending_actions_action_type_check
            CHECK (
                action_type IN (
                    'insert_customer', 'update_customer',
                    'insert_lead', 'update_lead',
                    'insert_account', 'update_account',
                    'insert_contact', 'update_contact',
                    'insert_opportunity', 'update_opportunity',
                    'insert_activity', 'update_activity',
                    'convert_lead'
                )
            )
            """,
        ),
    ),
)


async def run_migrations(conn: AsyncConnection) -> None:
    """Apply each migration once and reject checksum drift."""
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS crm_app_migrations (
            migration_id TEXT PRIMARY KEY,
            checksum CHAR(64) NOT NULL,
            applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    async with conn.transaction():
        await conn.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended('deepagents_crm_migrations', 0))"
        )
        for migration in MIGRATIONS:
            result = await conn.execute(
                "SELECT checksum FROM crm_app_migrations WHERE migration_id = %s",
                (migration.migration_id,),
            )
            record = await result.fetchone()
            if record:
                actual = record["checksum"] if isinstance(record, dict) else record[0]
                if actual.strip() != migration.checksum:
                    raise RuntimeError(f"迁移 {migration.migration_id} 的 checksum 已发生变化")
                continue
            for statement in migration.statements:
                await conn.execute(statement)
            await conn.execute(
                "INSERT INTO crm_app_migrations (migration_id, checksum) VALUES (%s, %s)",
                (migration.migration_id, migration.checksum),
            )
