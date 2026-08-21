"""Permission-scoped pgvector knowledge-base operations."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
from typing import Any, Protocol
from uuid import UUID

from langchain_openai import OpenAIEmbeddings
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from app.permissions import CurrentUser, Permission, require_permission
from app.schemas import (
    KnowledgeDocumentCreate,
    KnowledgeDocumentOut,
    KnowledgeSearchResult,
)

EMBEDDING_DIMENSIONS = 1536


class EmbeddingsClient(Protocol):
    async def aembed_documents(self, texts: list[str]) -> list[list[float]]: ...

    async def aembed_query(self, text: str) -> list[float]: ...


class DuplicateKnowledgeDocumentError(ValueError):
    pass


class KnowledgeDocumentNotFoundError(ValueError):
    pass


def split_knowledge_text(content: str, *, chunk_size: int, overlap: int) -> list[str]:
    """Split text predictably while preferring paragraph boundaries."""
    normalized = re.sub(r"\r\n?", "\n", content).strip()
    if not normalized:
        raise ValueError("知识库文档正文不能为空")
    if len(normalized) <= chunk_size:
        return [normalized]

    chunks: list[str] = []
    start = 0
    length = len(normalized)
    while start < length:
        end = min(start + chunk_size, length)
        if end < length:
            boundary = normalized.rfind("\n", start + chunk_size // 2, end)
            if boundary <= start:
                boundary = normalized.rfind(" ", start + chunk_size // 2, end)
            if boundary > start:
                end = boundary
        chunk = normalized[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= length:
            break
        start = max(end - overlap, start + 1)
    return chunks


def _vector_literal(vector: Sequence[float]) -> str:
    if len(vector) != EMBEDDING_DIMENSIONS:
        raise ValueError(
            f"Embedding 维度必须为 {EMBEDDING_DIMENSIONS}，实际为 {len(vector)}"
        )
    return "[" + ",".join(format(float(value), ".10g") for value in vector) + "]"


class KnowledgeService:
    """Keep embeddings and retrieval behind an authenticated service boundary."""

    def __init__(
        self,
        pool: AsyncConnectionPool,
        *,
        api_key: str,
        embedding_model: str,
        chunk_size: int,
        chunk_overlap: int,
        embeddings: EmbeddingsClient | None = None,
    ) -> None:
        self.pool = pool
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.embeddings: EmbeddingsClient = embeddings or OpenAIEmbeddings(
            model=embedding_model,
            api_key=api_key,
        )

    async def ingest_document(
        self,
        current_user: CurrentUser,
        data: KnowledgeDocumentCreate,
    ) -> KnowledgeDocumentOut:
        """Create one immutable version of an administrator-provided text document."""
        require_permission(current_user, Permission.USERS_MANAGE)
        content = data.content.strip()
        chunks = split_knowledge_text(
            content,
            chunk_size=self.chunk_size,
            overlap=self.chunk_overlap,
        )
        vectors = await self.embeddings.aembed_documents(chunks)
        if len(vectors) != len(chunks):
            raise RuntimeError("Embedding 服务返回的向量数量与文本分块不一致")
        vector_literals = [_vector_literal(vector) for vector in vectors]
        checksum = hashlib.sha256(content.encode("utf-8")).hexdigest()

        async with self.pool.connection() as conn, conn.transaction():
            existing = await conn.execute(
                """
                SELECT id FROM knowledge_documents
                WHERE created_by_user_id = %s AND content_sha256 = %s
                """,
                (current_user.id, checksum),
            )
            if await existing.fetchone():
                raise DuplicateKnowledgeDocumentError("相同内容的知识库文档已存在")
            inserted = await conn.execute(
                """
                INSERT INTO knowledge_documents
                    (created_by_user_id, title, source, visibility, content_sha256, metadata)
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING id, title, source, visibility, chunk_count, metadata,
                          created_by_user_id, created_at, updated_at
                """,
                (
                    current_user.id,
                    data.title.strip(),
                    data.source.strip() if data.source else None,
                    data.visibility,
                    checksum,
                    Jsonb(data.metadata),
                ),
            )
            record = await inserted.fetchone()
            document_id = record["id"]
            async with conn.cursor() as cursor:
                await cursor.executemany(
                    """
                    INSERT INTO knowledge_chunks
                        (document_id, chunk_index, content, embedding, metadata)
                    VALUES (%s, %s, %s, %s::vector, %s)
                    """,
                    [
                        (document_id, index, chunk, vector, Jsonb(data.metadata))
                        for index, (chunk, vector) in enumerate(zip(chunks, vector_literals))
                    ],
                )
            updated = await conn.execute(
                """
                UPDATE knowledge_documents
                SET chunk_count = %s, updated_at = NOW()
                WHERE id = %s
                RETURNING id, title, source, visibility, chunk_count, metadata,
                          created_by_user_id, created_at, updated_at
                """,
                (len(chunks), document_id),
            )
            record = await updated.fetchone()
        return KnowledgeDocumentOut.model_validate(record)

    async def list_documents(
        self, current_user: CurrentUser, *, limit: int = 100
    ) -> list[KnowledgeDocumentOut]:
        require_permission(current_user, Permission.USERS_MANAGE)
        async with self.pool.connection() as conn:
            result = await conn.execute(
                """
                SELECT id, title, source, visibility, chunk_count, metadata,
                       created_by_user_id, created_at, updated_at
                FROM knowledge_documents
                ORDER BY updated_at DESC
                LIMIT %s
                """,
                (min(max(limit, 1), 100),),
            )
            records = await result.fetchall()
        return [KnowledgeDocumentOut.model_validate(record) for record in records]

    async def delete_document(self, current_user: CurrentUser, document_id: UUID) -> None:
        require_permission(current_user, Permission.USERS_MANAGE)
        async with self.pool.connection() as conn, conn.transaction():
            result = await conn.execute(
                "DELETE FROM knowledge_documents WHERE id = %s RETURNING id",
                (document_id,),
            )
            if not await result.fetchone():
                raise KnowledgeDocumentNotFoundError("知识库文档不存在")

    async def search(
        self,
        current_user: CurrentUser,
        query: str,
        *,
        limit: int = 5,
    ) -> list[KnowledgeSearchResult]:
        if not current_user.is_active:
            return []
        normalized_query = " ".join(query.split())
        if not normalized_query:
            raise ValueError("检索问题不能为空")
        vector = _vector_literal(await self.embeddings.aembed_query(normalized_query))
        async with self.pool.connection() as conn:
            result = await conn.execute(
                """
                SELECT d.id AS document_id, c.id AS chunk_id, d.title, d.source,
                       c.content, c.chunk_index, c.metadata,
                       1 - (c.embedding <=> %s::vector) AS score
                FROM knowledge_chunks c
                JOIN knowledge_documents d ON d.id = c.document_id
                WHERE d.visibility = 'shared' OR d.created_by_user_id = %s
                ORDER BY c.embedding <=> %s::vector, c.chunk_index
                LIMIT %s
                """,
                (vector, current_user.id, vector, min(max(limit, 1), 10)),
            )
            records = await result.fetchall()
        return [KnowledgeSearchResult.model_validate(record) for record in records]
