#!/usr/bin/env python3
"""Import one UTF-8 Markdown or text file into the pgvector knowledge base."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings  # noqa: E402
from app.database import CRMRepository  # noqa: E402
from app.knowledge import KnowledgeService  # noqa: E402
from app.permissions import CurrentUser, Role  # noqa: E402
from app.schemas import KnowledgeDocumentCreate  # noqa: E402


def _read_text(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix not in {".txt", ".md", ".markdown"}:
        raise ValueError("当前仅支持 UTF-8 编码的 .txt、.md 和 .markdown 文档")
    return path.read_text(encoding="utf-8")


async def _run(args: argparse.Namespace) -> None:
    path = Path(args.file).resolve(strict=True)
    content = _read_text(path)
    if not content.strip():
        raise ValueError("未能从文档中提取可索引文本")
    settings = get_settings()
    pool = AsyncConnectionPool(
        conninfo=settings.database_url,
        min_size=1,
        max_size=1,
        open=False,
        kwargs={"autocommit": True, "row_factory": dict_row},
    )
    await pool.open()
    await pool.wait()
    try:
        repository = CRMRepository(pool)
        await repository.setup()
        async with pool.connection() as conn:
            result = await conn.execute(
                """
                SELECT id, username, email, display_name, role, is_active
                FROM users WHERE username = %s
                """,
                (args.owner_username.lower(),),
            )
            record = await result.fetchone()
        if not record:
            raise ValueError("指定的知识库管理员账号不存在")
        user = CurrentUser(
            id=record["id"],
            username=record["username"],
            email=record["email"],
            display_name=record["display_name"],
            role=Role(record["role"]),
            is_active=record["is_active"],
        )
        service = KnowledgeService(
            pool,
            api_key=settings.embedding_api_key(),
            embedding_model=settings.knowledge_embedding_model,
            chunk_size=settings.knowledge_chunk_size,
            chunk_overlap=settings.knowledge_chunk_overlap,
        )
        document = await service.ingest_document(
            user,
            KnowledgeDocumentCreate(
                title=args.title or path.stem,
                content=content,
                source=args.source or str(path),
                visibility="private" if args.private else "shared",
                metadata={"filename": path.name},
            ),
        )
    finally:
        await pool.close()
    print(f"已入库: {document.id} | {document.title} | {document.chunk_count} 个分块")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("file", help="要导入的 UTF-8 .txt 或 .md 文件")
    parser.add_argument("--owner-username", required=True, help="执行导入的管理员用户名")
    parser.add_argument("--title", help="文档显示标题，默认使用文件名")
    parser.add_argument("--source", help="用户可见来源 URL 或路径")
    parser.add_argument("--private", action="store_true", help="仅导入管理员本人可检索的文档")
    asyncio.run(_run(parser.parse_args()))


if __name__ == "__main__":
    main()
