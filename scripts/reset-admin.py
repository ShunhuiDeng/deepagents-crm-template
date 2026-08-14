#!/usr/bin/env python3
"""Replace all CRM login accounts with one administrator account.

This is a maintenance-only command. It removes credential users, their web
sessions, conversations, pending actions, memories, and LangGraph checkpoints.
It deliberately refuses to run while CRM business rows are owned by an old
account, because silently orphaning customer data would violate the CRM model.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import hashlib
import sys
from importlib import import_module
from pathlib import Path
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

# Make direct execution from ``scripts/`` resolve the local ``app`` package.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
get_settings = import_module("app.config").get_settings
hash_password = import_module("app.security").hash_password
RegisterRequest = import_module("app.schemas").RegisterRequest


CONFIRMATION = "RESET-DEEPAGENTS-CRM-ADMIN"

OWNERSHIP_CHECKS = {
    "leads": "owner_id",
    "accounts": "owner_id",
    "contacts": "owner_id",
    "opportunities": "owner_id",
    "activities": "assigned_user_id",
    "lead_conversions": "converted_by",
}


def _storage_thread_id(user_id: Any, conversation_id: Any) -> str:
    value = f"{user_id}\0{conversation_id}".encode()
    return hashlib.sha256(value).hexdigest()


def _password_from_tty() -> str:
    password = getpass.getpass("新管理员密码: ")
    repeated = getpass.getpass("再次输入密码: ")
    if password != repeated:
        raise SystemExit("两次密码不一致，未修改数据库")
    return password


def _owned_business_counts(
    conn: psycopg.Connection[Any], old_user_ids: list[Any]
) -> dict[str, int]:
    counts: dict[str, int] = {}
    for table, owner_column in OWNERSHIP_CHECKS.items():
        result = conn.execute(
            psycopg.sql.SQL(
                "SELECT COUNT(*)::bigint AS count FROM {} WHERE {} = ANY(%s)"
            ).format(psycopg.sql.Identifier(table), psycopg.sql.Identifier(owner_column)),
            (old_user_ids,),
        )
        counts[table] = int(result.fetchone()["count"])
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--username", required=True)
    parser.add_argument("--email", required=True)
    parser.add_argument("--display-name", required=True)
    parser.add_argument(
        "--confirm",
        required=True,
        help=f"destructive-operation guard; must equal {CONFIRMATION}",
    )
    args = parser.parse_args()
    if args.confirm != CONFIRMATION:
        raise SystemExit("确认文本不匹配，未修改数据库")

    password = _password_from_tty()
    registration = RegisterRequest(
        username=args.username,
        email=args.email,
        display_name=args.display_name,
        password=password,
    )
    password_hash = asyncio.run(hash_password(registration.password))

    settings = get_settings()
    with psycopg.connect(settings.database_url, row_factory=dict_row) as conn:
        with conn.transaction():
            conn.execute("SELECT pg_advisory_xact_lock(%s::bigint)", (8_426_081_302,))
            conn.execute("LOCK TABLE users IN SHARE ROW EXCLUSIVE MODE")

            old_users_result = conn.execute(
                "SELECT id FROM users WHERE password_hash IS NOT NULL ORDER BY id FOR UPDATE"
            )
            old_user_ids = [row["id"] for row in old_users_result.fetchall()]
            ownership_counts = _owned_business_counts(conn, old_user_ids)
            owned_rows = {name: count for name, count in ownership_counts.items() if count}
            if owned_rows:
                raise RuntimeError(
                    "旧账号仍负责业务数据，必须先完成负责人整链转移；事务已回滚: "
                    f"{owned_rows}"
                )

            conflict_result = conn.execute(
                """
                SELECT 1
                FROM users
                WHERE LOWER(username) = %s OR LOWER(email) = %s
                LIMIT 1
                """,
                (registration.username, str(registration.email).lower()),
            )
            if conflict_result.fetchone():
                raise RuntimeError("新管理员用户名或邮箱已存在，未修改数据库")

            new_user_result = conn.execute(
                """
                INSERT INTO users
                    (username, email, display_name, password_hash,
                     password_changed_at, role, is_active)
                VALUES (%s, %s, %s, %s, NOW(), 'admin', TRUE)
                RETURNING id
                """,
                (
                    registration.username,
                    str(registration.email).lower(),
                    registration.display_name.strip(),
                    password_hash,
                ),
            )
            new_user_id = new_user_result.fetchone()["id"]

            conversations_result = conn.execute(
                """
                SELECT owner_user_id, id
                FROM crm_conversations
                WHERE owner_user_id = ANY(%s)
                """,
                (old_user_ids,),
            )
            storage_thread_ids = [
                _storage_thread_id(row["owner_user_id"], row["id"])
                for row in conversations_result.fetchall()
            ]
            if storage_thread_ids:
                for table in ("checkpoint_writes", "checkpoint_blobs", "checkpoints"):
                    conn.execute(
                        psycopg.sql.SQL("DELETE FROM {} WHERE thread_id = ANY(%s)").format(
                            psycopg.sql.Identifier(table)
                        ),
                        (storage_thread_ids,),
                    )

            if old_user_ids:
                conn.execute(
                    "DELETE FROM crm_pending_actions WHERE user_id = ANY(%s)",
                    (old_user_ids,),
                )
                conn.execute(
                    "DELETE FROM crm_conversation_memories WHERE owner_user_id = ANY(%s)",
                    (old_user_ids,),
                )
                conn.execute(
                    "DELETE FROM crm_conversations WHERE owner_user_id = ANY(%s)",
                    (old_user_ids,),
                )
                conn.execute(
                    "DELETE FROM crm_sessions WHERE user_id = ANY(%s)",
                    (old_user_ids,),
                )
                conn.execute("DELETE FROM users WHERE id = ANY(%s)", (old_user_ids,))

            conn.execute(
                """
                INSERT INTO crm_audit_log
                    (actor_user_id, action, entity_type, entity_id, after_data, metadata)
                VALUES (%s, 'system.admin.reset', 'user', %s, %s, %s)
                """,
                (
                    new_user_id,
                    new_user_id,
                    Jsonb({"username": registration.username, "role": "admin"}),
                    Jsonb({"removed_credential_users": len(old_user_ids)}),
                ),
            )

            final_result = conn.execute(
                """
                SELECT COUNT(*)::int AS count,
                       COUNT(*) FILTER (WHERE role = 'admin' AND is_active)::int AS admins
                FROM users
                WHERE password_hash IS NOT NULL
                """
            )
            final = final_result.fetchone()
            if final != {"count": 1, "admins": 1}:
                raise RuntimeError("账号重置后的安全校验失败，事务已回滚")

    print("账号重置完成：credential_users=1, active_admins=1")


if __name__ == "__main__":
    main()
