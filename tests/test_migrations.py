import re

from app.migrations import MIGRATIONS, Migration

CORE_TABLES = {
    "users",
    "leads",
    "accounts",
    "contacts",
    "opportunities",
    "activities",
}


def _migration(migration_id: str) -> Migration:
    return next(item for item in MIGRATIONS if item.migration_id == migration_id)


def _normalized(statement: str) -> str:
    return " ".join(statement.split())


def test_core_schema_is_the_first_migration_and_creates_all_core_tables() -> None:
    migration = MIGRATIONS[0]

    assert migration.migration_id == "crm_app_000_core_schema"
    created_tables = {
        match.group(1)
        for statement in migration.statements
        if (match := re.match(r"CREATE TABLE IF NOT EXISTS (\w+)", statement.strip()))
    }
    assert created_tables == CORE_TABLES


def test_core_schema_uses_idempotent_create_statements() -> None:
    migration = _migration("crm_app_000_core_schema")

    for statement in migration.statements:
        normalized = _normalized(statement).upper()
        if normalized.startswith("CREATE TABLE"):
            assert normalized.startswith("CREATE TABLE IF NOT EXISTS")
        if normalized.startswith("CREATE INDEX"):
            assert normalized.startswith("CREATE INDEX IF NOT EXISTS")


def test_core_schema_matches_critical_column_contract() -> None:
    sql = "\n".join(_normalized(statement) for statement in MIGRATIONS[0].statements)

    expected_columns = (
        "username VARCHAR(100) NOT NULL UNIQUE",
        "email VARCHAR(255) UNIQUE",
        "role VARCHAR(50) NOT NULL DEFAULT 'sales'",
        "is_active BOOLEAN NOT NULL DEFAULT TRUE",
        "company_name VARCHAR(255)",
        "job_title VARCHAR(150)",
        "status VARCHAR(50) NOT NULL DEFAULT 'new'",
        "score INTEGER DEFAULT 0",
        "name VARCHAR(255) NOT NULL",
        "website VARCHAR(500)",
        "annual_revenue NUMERIC(18, 2)",
        "status VARCHAR(50) NOT NULL DEFAULT 'active'",
        "department VARCHAR(150)",
        "linkedin VARCHAR(500)",
        "amount NUMERIC(18, 2)",
        "currency VARCHAR(10) NOT NULL DEFAULT 'CNY'",
        "stage VARCHAR(50) NOT NULL DEFAULT 'prospecting'",
        "probability NUMERIC(5, 2) DEFAULT 0",
        "expected_close_date DATE",
        "type VARCHAR(50) NOT NULL",
        "subject VARCHAR(255) NOT NULL",
        "priority VARCHAR(50) NOT NULL DEFAULT 'normal'",
        "start_at TIMESTAMPTZ",
        "end_at TIMESTAMPTZ",
    )
    for column in expected_columns:
        assert column in sql

    assert "extra JSONB" not in sql
    assert "version INTEGER" not in sql
    assert "primary_contact_id UUID" not in sql
    assert "password_hash TEXT" not in sql


def test_core_schema_matches_foreign_key_delete_actions() -> None:
    sql = "\n".join(_normalized(statement) for statement in MIGRATIONS[0].statements)

    assert sql.count("owner_id UUID REFERENCES users(id) ON DELETE SET NULL") == 4
    assert "account_id UUID REFERENCES accounts(id) ON DELETE SET NULL" in sql
    assert "account_id UUID REFERENCES accounts(id) ON DELETE CASCADE" in sql
    assert "contact_id UUID REFERENCES contacts(id) ON DELETE CASCADE" in sql
    assert "lead_id UUID REFERENCES leads(id) ON DELETE CASCADE" in sql
    assert "opportunity_id UUID REFERENCES opportunities(id) ON DELETE CASCADE" in sql
    assert "assigned_user_id UUID REFERENCES users(id) ON DELETE SET NULL" in sql


def test_core_schema_creates_the_real_contract_indexes() -> None:
    migration = _migration("crm_app_000_core_schema")
    index_names = {
        match.group(1)
        for statement in migration.statements
        if (
            match := re.match(
                r"CREATE INDEX IF NOT EXISTS (\w+)",
                statement.strip(),
            )
        )
    }

    assert index_names == {
        "idx_leads_company_name",
        "idx_leads_email",
        "idx_leads_owner_id",
        "idx_leads_status",
        "idx_accounts_name",
        "idx_accounts_owner_id",
        "idx_contacts_account_id",
        "idx_contacts_email",
        "idx_contacts_owner_id",
        "idx_opportunities_account_id",
        "idx_opportunities_expected_close_date",
        "idx_opportunities_owner_id",
        "idx_opportunities_stage",
        "idx_activities_account_id",
        "idx_activities_assigned_user_id",
        "idx_activities_contact_id",
        "idx_activities_lead_id",
        "idx_activities_opportunity_id",
        "idx_activities_start_at",
    }


def test_applied_migration_checksums_remain_unchanged() -> None:
    assert {
        item.migration_id: item.checksum for item in MIGRATIONS[1:]
    } == {
        "crm_app_001_auth_memory_and_audit": (
            "d3c5dc158624eda12f4e40423417a27f2ba9d8c6264b03e8fd841e32d3834135"
        ),
        "crm_app_002_multi_entity_pending_actions": (
            "d445c97a24a17d314afc6a06eb690b75b645bad2959f2eb1fae5e5ea846afc6d"
        ),
        "crm_app_003_lead_conversion_and_primary_contacts": (
            "0a2c8c0066d271e70e5321c5e6441e2c9eb5dc19a34fa79e0795531d07e54c33"
        ),
    }
