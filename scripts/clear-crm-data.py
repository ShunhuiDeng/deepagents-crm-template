#!/usr/bin/env python3
"""Clear Intelligent CRM application data while preserving accounts and schema metadata."""

from __future__ import annotations

import argparse
import sys
from importlib import import_module
from pathlib import Path
from typing import Any

import psycopg
from psycopg import sql
from psycopg.rows import dict_row

# Make direct execution from ``scripts/`` resolve the local ``app`` package.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
get_settings = import_module("app.config").get_settings


CONFIRMATION = "CLEAR-DEEPAGENTS-CRM-DATA"

# Explicit allow-list: never add ``users`` or migration/system routing tables here.
REQUIRED_CLEAR_TABLES = (
    "activities",
    "lead_conversions",
    "opportunities",
    "contacts",
    "leads",
    "accounts",
    "crm_pending_actions",
    "crm_conversation_memories",
    "crm_conversations",
    "crm_sessions",
    "crm_audit_log",
    "checkpoint_writes",
    "checkpoint_blobs",
    "checkpoints",
)

# These tables are owned by the optional knowledge-base subsystem.
OPTIONAL_CLEAR_TABLES = (
    "knowledge_chunks",
    "knowledge_documents",
    "content_chunks",
    "structured_review_sessions",
    "document_units",
    "document_versions",
    "documents",
    "ingestion_jobs",
    "structured_data_staging",
)
CLEAR_TABLES = REQUIRED_CLEAR_TABLES + OPTIONAL_CLEAR_TABLES


def _account_snapshot(conn: psycopg.Connection[Any]) -> list[dict[str, Any]]:
    result = conn.execute(
        """
        SELECT id, username, email, display_name, password_hash, role, is_active,
               created_at, last_login_at, password_changed_at
        FROM users
        ORDER BY id
        """
    )
    return [dict(row) for row in result.fetchall()]


def _existing_clear_tables(conn: psycopg.Connection[Any]) -> tuple[str, ...]:
    result = conn.execute(
        """
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema = current_schema()
          AND table_type = 'BASE TABLE'
          AND table_name = ANY(%s)
        """,
        (list(CLEAR_TABLES),),
    )
    existing = {row["table_name"] for row in result.fetchall()}
    missing_required = [table for table in REQUIRED_CLEAR_TABLES if table not in existing]
    if missing_required:
        missing = ", ".join(missing_required)
        raise RuntimeError(f"缺少必须的 CRM 数据表，未执行清空: {missing}")
    return tuple(table for table in CLEAR_TABLES if table in existing)


def _counts(
    conn: psycopg.Connection[Any], tables: tuple[str, ...]
) -> dict[str, int]:
    counts: dict[str, int] = {}
    for table in tables:
        result = conn.execute(
            sql.SQL("SELECT COUNT(*)::bigint AS count FROM {}").format(sql.Identifier(table))
        )
        counts[table] = int(result.fetchone()["count"])
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--confirm",
        required=True,
        help=f"destructive-operation guard; must equal {CONFIRMATION}",
    )
    args = parser.parse_args()
    if args.confirm != CONFIRMATION:
        raise SystemExit("确认文本不匹配，未执行任何删除")

    settings = get_settings()
    with psycopg.connect(settings.database_url, row_factory=dict_row) as conn:
        before_accounts = _account_snapshot(conn)
        clear_tables = _existing_clear_tables(conn)
        before_counts = _counts(conn, clear_tables)
        with conn.transaction():
            if clear_tables:
                conn.execute(
                    sql.SQL("TRUNCATE TABLE {}").format(
                        sql.SQL(", ").join(sql.Identifier(name) for name in clear_tables)
                    )
                )
            after_accounts = _account_snapshot(conn)
            after_counts = _counts(conn, clear_tables)
            if after_accounts != before_accounts:
                raise RuntimeError("账号或密码数据发生变化，事务已回滚")
            not_empty = {name: count for name, count in after_counts.items() if count}
            if not_empty:
                raise RuntimeError(f"仍有数据未清空，事务已回滚: {not_empty}")

    print(f"账号保留: {len(before_accounts)}")
    for table in clear_tables:
        print(f"{table}: {before_counts[table]} -> 0")


if __name__ == "__main__":
    main()
