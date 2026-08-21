from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import logging
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response, status
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from langchain.messages import AIMessage, HumanMessage
from langchain_core.messages import BaseMessage
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.checkpoint.serde.encrypted import EncryptedSerializer
from psycopg import OperationalError
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool, PoolTimeout

from app.agents import AgentRegistry, build_agent_registry, build_main_agent
from app.agents.service import AgentService, extract_ai_text
from app.config import Settings, get_settings
from app.database import (
    CRMRepository,
    DuplicateUserError,
    EntityAccessError,
    FirstAdminRegistrationError,
    InvalidActionError,
)
from app.dependencies import get_current_user, get_repo
from app.knowledge import (
    DuplicateKnowledgeDocumentError,
    KnowledgeDocumentNotFoundError,
    KnowledgeService,
)
from app.permissions import CurrentUser, Role
from app.schemas import (
    AccountChainTransferOut,
    AccountChainTransferRequest,
    AccountCreate,
    AccountOut,
    AccountOverviewOut,
    AccountUpdate,
    ActivityCreate,
    ActivityOut,
    ActivityUpdate,
    ChatRequest,
    ChatResponse,
    ContactCreate,
    ContactOut,
    ContactUpdate,
    ConversationCreate,
    ConversationMemoryOut,
    ConversationMessageOut,
    ConversationOut,
    ConversationUpdate,
    CustomerCreate,
    CustomerOut,
    CustomerUpdate,
    DashboardOut,
    LeadConversionOut,
    LeadConversionRequest,
    LeadCreate,
    LeadOut,
    LeadUpdate,
    KnowledgeDocumentCreate,
    KnowledgeDocumentOut,
    LoginRequest,
    OpportunityCreate,
    OpportunityOut,
    OpportunityUpdate,
    PendingActionOut,
    RegisterRequest,
    UserOut,
    UserRoleUpdate,
)
from app.security import (
    digest_session_token,
    generate_session_token,
    hash_password,
    verify_password,
)
from app.skill_loader import load_conversation_memory_file, load_skill_files

LOGGER = logging.getLogger(__name__)
STATIC_DIR = Path(__file__).parent / "static"
DUMMY_PASSWORD_HASH = (
    "$argon2id$v=19$m=65536,t=3,p=4$ag4gBuFClwx6pJIW0yBXIg$"
    "4xXM5Vg9kRFGNU6hQTW8TIq+/mwtzIuR0c5FGnHjAfQ"
)


def safe_pending_failure_detail(error_message: str | None) -> str:
    """Expose domain validation feedback while hiding SQL, traces and credentials."""
    fallback = "待确认操作执行失败，请刷新数据后重新发起"
    if not error_message:
        return fallback
    normalized = " ".join(error_message.split())
    unsafe_markers = (
        "traceback",
        "postgresql://",
        "psycopg",
        "sqlstate",
        "select ",
        "insert ",
        "update ",
        "delete ",
        "constraint ",
        'relation "',
    )
    if any(marker in normalized.lower() for marker in unsafe_markers):
        return fallback
    normalized = re.sub(
        r"(?i)(password|passwd|token|api[_-]?key|authorization)\s*[:=]\s*\S+",
        r"\1=[已隐藏]",
        normalized,
    )
    return f"待确认操作执行失败：{normalized[:200]}"


def checkpoint_thread_id(user_id: str | UUID, public_thread_id: str) -> str:
    """Scope a public conversation ID to one authenticated user."""
    value = f"{user_id}\0{public_thread_id}".encode()
    return hashlib.sha256(value).hexdigest()


def connection_pool_runtime_options(settings: Settings) -> dict[str, Any]:
    """Validate borrowed connections and recycle remote PostgreSQL sessions promptly."""
    return {
        "check": AsyncConnectionPool.check_connection,
        "timeout": settings.db_pool_timeout_seconds,
        "max_idle": 300.0,
        "max_lifetime": 1800.0,
        "reconnect_timeout": 30.0,
    }


def is_loopback_request(request: Request) -> bool:
    """Trust the ASGI peer address, never a client-supplied identity header."""
    if request.client is None:
        return False
    try:
        return ipaddress.ip_address(request.client.host).is_loopback
    except ValueError:
        return request.client.host == "localhost"


def extract_message_text(message: AIMessage) -> str:
    return extract_ai_text(message)


def serialize_conversation_messages(
    messages: list[BaseMessage],
) -> list[ConversationMessageOut]:
    """Return only user-visible turns from persisted LangGraph state."""
    visible: list[ConversationMessageOut] = []
    for index, message in enumerate(messages):
        if isinstance(message, HumanMessage):
            if message.additional_kwargs.get("lc_source") == "summarization":
                continue
            role = "user"
        elif isinstance(message, AIMessage):
            role = "assistant"
        else:
            continue
        content = extract_message_text(message)
        if not content:
            continue
        visible.append(
            ConversationMessageOut(
                id=str(message.id or f"message-{index}"),
                role=role,
                content=content,
            )
        )
    return visible


def get_agent_registry(request: Request) -> AgentRegistry:
    return request.app.state.agent_registry


def get_knowledge_service(request: Request) -> KnowledgeService:
    service = getattr(request.app.state, "knowledge_service", None)
    if service is None:
        raise HTTPException(status_code=503, detail="知识库 RAG 尚未启用")
    return service


def _client_metadata(request: Request) -> tuple[str | None, str | None]:
    user_agent = request.headers.get("user-agent")
    ip_address = request.client.host if request.client else None
    return user_agent, ip_address


def _set_session_cookie(response: Response, settings: Settings, token: str) -> None:
    response.set_cookie(
        key=settings.session_cookie_name,
        value=token,
        max_age=settings.session_ttl_hours * 3600,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="strict",
        path="/",
    )


def _clear_session_cookie(response: Response, settings: Settings) -> None:
    response.delete_cookie(
        key=settings.session_cookie_name,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="strict",
        path="/",
    )


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        model_api_key = settings.model_api_key()
        settings.validate_subagent_runtime()
        encryption_key = settings.checkpoint_encryption_key()
        pool_kwargs = {
            "autocommit": True,
            "options": "-c timezone=UTC",
            "prepare_threshold": 0,
            "row_factory": dict_row,
        }
        pool_runtime_options = connection_pool_runtime_options(settings)
        pool = AsyncConnectionPool(
            conninfo=settings.database_url,
            min_size=settings.db_pool_min_size,
            max_size=settings.db_pool_max_size,
            open=False,
            kwargs=pool_kwargs,
            **pool_runtime_options,
        )
        checkpoint_pool = AsyncConnectionPool(
            conninfo=settings.database_url,
            min_size=1,
            max_size=settings.checkpoint_pool_max_size,
            open=False,
            kwargs=pool_kwargs,
            **pool_runtime_options,
        )
        await asyncio.gather(pool.open(), checkpoint_pool.open())
        await asyncio.gather(pool.wait(), checkpoint_pool.wait())
        try:
            repository = CRMRepository(pool)
            await repository.setup()
            serializer = (
                EncryptedSerializer.from_pycryptodome_aes(key=encryption_key)
                if encryption_key
                else None
            )
            checkpointer = AsyncPostgresSaver(checkpoint_pool, serde=serializer)
            await checkpointer.setup()
            knowledge_service = None
            if settings.knowledge_agent_enabled():
                knowledge_service = KnowledgeService(
                    pool,
                    api_key=settings.embedding_api_key(),
                    embedding_model=settings.knowledge_embedding_model,
                    chunk_size=settings.knowledge_chunk_size,
                    chunk_overlap=settings.knowledge_chunk_overlap,
                )
            registry = build_agent_registry(
                repository,
                settings.enabled_subagent_names(),
                knowledge_service=knowledge_service,
                execution=settings.subagent_execution_mode(),
                async_url=settings.subagent_server_url,
            )
            agent = build_main_agent(
                repository,
                checkpointer,
                settings.model_name,
                model_api_key=model_api_key,
                registry=registry,
            )

            app.state.settings = settings
            app.state.pool = pool
            app.state.checkpoint_pool = checkpoint_pool
            app.state.repository = repository
            app.state.knowledge_service = knowledge_service
            app.state.agent_registry = registry
            app.state.checkpointer = checkpointer
            app.state.agent = agent
            app.state.agent_service = AgentService(
                agent, timeout_seconds=settings.agent_timeout_seconds
            )
            yield
        finally:
            await asyncio.gather(pool.close(), checkpoint_pool.close())

    app = FastAPI(
        title="智能 CRM",
        version="1.0.0",
        description="智能 CRM：五实体业务闭环、角色权限与持久化 AI 助手。",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.exception_handler(EntityAccessError)
    async def entity_access_error(_request: Request, exc: EntityAccessError) -> JSONResponse:
        return JSONResponse(status_code=403, content={"detail": str(exc)})

    @app.exception_handler(InvalidActionError)
    async def invalid_entity_action(_request: Request, exc: InvalidActionError) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.exception_handler(PoolTimeout)
    @app.exception_handler(OperationalError)
    async def database_unavailable(request: Request, exc: OperationalError) -> JSONResponse:
        LOGGER.error(
            "PostgreSQL connection unavailable; request_id=%s; error=%s",
            getattr(request.state, "request_id", "unknown"),
            type(exc).__name__,
        )
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"detail": "数据库暂时不可用，请稍后重试"},
            headers={"Retry-After": "3"},
        )

    @app.middleware("http")
    async def request_context(request: Request, call_next: Any) -> Response:
        request.state.request_id = request.headers.get("X-Request-ID") or str(uuid4())
        response: Response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' data:; style-src 'self'; "
            "script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'"
        )
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        elif request.url.path == "/" or request.url.path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-cache"
        return response

    @app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/health")
    async def health(request: Request) -> dict[str, Any]:
        async with request.app.state.pool.connection() as conn:
            await conn.execute("SELECT 1")
        return {
            "status": "ok",
            "agent": "ready",
            "subagents": len(request.app.state.agent_registry.build_specs()),
        }

    @app.post(
        "/api/auth/register",
        response_model=UserOut,
        status_code=status.HTTP_201_CREATED,
    )
    async def register(
        payload: RegisterRequest,
        request: Request,
        response: Response,
        repo: CRMRepository = Depends(get_repo),
    ) -> UserOut:
        if not settings.registration_enabled:
            raise HTTPException(status_code=403, detail="当前未开放账号注册")
        password_hash = await hash_password(payload.password)
        try:
            user = await repo.register_user(
                payload,
                password_hash,
                first_user_is_admin=settings.first_user_is_admin,
                allow_first_admin=(
                    not settings.first_admin_local_only or is_loopback_request(request)
                ),
            )
        except FirstAdminRegistrationError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except DuplicateUserError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        token = generate_session_token()
        user_agent, ip_address = _client_metadata(request)
        await repo.create_session(
            user.id,
            digest_session_token(token),
            ttl_hours=settings.session_ttl_hours,
            user_agent=user_agent,
            ip_address=ip_address,
        )
        _set_session_cookie(response, settings, token)
        return user

    @app.post("/api/auth/login", response_model=UserOut)
    async def login(
        payload: LoginRequest,
        request: Request,
        response: Response,
        repo: CRMRepository = Depends(get_repo),
    ) -> UserOut:
        record = await repo.get_auth_record(payload.username)
        password_hash = record.get("password_hash") if record else DUMMY_PASSWORD_HASH
        password_valid = await verify_password(payload.password, password_hash)
        if not record or not password_valid or not record["is_active"]:
            raise HTTPException(status_code=401, detail="用户名、邮箱或密码不正确")
        await repo.mark_login(record["id"])
        user = await repo.get_user(record["id"])
        if not user:
            raise HTTPException(status_code=401, detail="账号不可用")
        token = generate_session_token()
        user_agent, ip_address = _client_metadata(request)
        await repo.create_session(
            user.id,
            digest_session_token(token),
            ttl_hours=settings.session_ttl_hours,
            user_agent=user_agent,
            ip_address=ip_address,
        )
        _set_session_cookie(response, settings, token)
        return user

    @app.post("/api/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
    async def logout(
        request: Request,
        response: Response,
        repo: CRMRepository = Depends(get_repo),
    ) -> None:
        token = request.cookies.get(settings.session_cookie_name)
        if token:
            await repo.delete_session(digest_session_token(token))
        _clear_session_cookie(response, settings)

    @app.get("/api/auth/me", response_model=UserOut)
    async def me(
        current_user: CurrentUser = Depends(get_current_user),
        repo: CRMRepository = Depends(get_repo),
    ) -> UserOut:
        user = await repo.get_user(current_user.id)
        if not user:
            raise HTTPException(status_code=401, detail="账号不可用")
        return user

    @app.get("/api/users", response_model=list[UserOut])
    async def list_users(
        current_user: CurrentUser = Depends(get_current_user),
        repo: CRMRepository = Depends(get_repo),
    ) -> list[UserOut]:
        return await repo.list_users(current_user)

    @app.get("/api/dashboard", response_model=DashboardOut)
    async def dashboard(
        current_user: CurrentUser = Depends(get_current_user),
        repo: CRMRepository = Depends(get_repo),
    ) -> DashboardOut:
        return await repo.get_dashboard(current_user)

    @app.patch("/api/users/{user_id}/role", response_model=UserOut)
    async def update_user_role(
        user_id: UUID,
        payload: UserRoleUpdate,
        current_user: CurrentUser = Depends(get_current_user),
        repo: CRMRepository = Depends(get_repo),
    ) -> UserOut:
        try:
            user = await repo.update_user_role(current_user, user_id, Role(payload.role))
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if not user:
            raise HTTPException(status_code=404, detail="账号不存在")
        return user

    @app.post("/api/chat", response_model=ChatResponse)
    async def chat(
        payload: ChatRequest,
        request: Request,
        current_user: CurrentUser = Depends(get_current_user),
        repo: CRMRepository = Depends(get_repo),
    ) -> ChatResponse:
        conversation_id = payload.thread_id or uuid4()
        conversation = await repo.get_conversation(current_user.id, conversation_id)
        if payload.thread_id and not conversation:
            raise HTTPException(status_code=404, detail="会话不存在")
        if not conversation:
            await repo.create_conversation(current_user.id, conversation_id=conversation_id)
        memories = await repo.recall_conversation_memories(
            current_user.id, conversation_id, limit=50
        )
        runtime_files = {
            **load_skill_files(),
            **load_conversation_memory_file(memories),
        }
        storage_thread_id = checkpoint_thread_id(current_user.id, str(conversation_id))
        try:
            answer = await request.app.state.agent_service.invoke(
                current_user=current_user,
                conversation_id=conversation_id,
                storage_thread_id=storage_thread_id,
                request_id=request.state.request_id,
                message=payload.message,
                runtime_files=runtime_files,
            )
        except TimeoutError as exc:
            raise HTTPException(status_code=504, detail="Agent 处理超时，请稍后重试") from exc
        except Exception as exc:
            LOGGER.exception("Agent invocation failed; request_id=%s", request.state.request_id)
            raise HTTPException(
                status_code=502,
                detail=f"Agent 调用失败，请联系管理员并提供请求 ID：{request.state.request_id}",
            ) from exc
        await repo.record_conversation_turn(current_user.id, conversation_id, payload.message)
        pending_actions = await repo.list_pending_actions(
            current_user, conversation_id=conversation_id
        )
        return ChatResponse(
            thread_id=conversation_id,
            answer=answer,
            pending_actions=pending_actions,
        )

    @app.get("/api/knowledge/documents", response_model=list[KnowledgeDocumentOut])
    async def list_knowledge_documents(
        request: Request,
        current_user: CurrentUser = Depends(get_current_user),
    ) -> list[KnowledgeDocumentOut]:
        return await get_knowledge_service(request).list_documents(current_user)

    @app.post(
        "/api/knowledge/documents",
        response_model=KnowledgeDocumentOut,
        status_code=status.HTTP_201_CREATED,
    )
    async def create_knowledge_document(
        payload: KnowledgeDocumentCreate,
        request: Request,
        current_user: CurrentUser = Depends(get_current_user),
    ) -> KnowledgeDocumentOut:
        try:
            return await get_knowledge_service(request).ingest_document(current_user, payload)
        except DuplicateKnowledgeDocumentError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.delete("/api/knowledge/documents/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_knowledge_document(
        document_id: UUID,
        request: Request,
        current_user: CurrentUser = Depends(get_current_user),
    ) -> Response:
        try:
            await get_knowledge_service(request).delete_document(current_user, document_id)
        except KnowledgeDocumentNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.get("/api/conversations", response_model=list[ConversationOut])
    async def list_conversations(
        archived: bool = False,
        limit: int = Query(default=50, ge=1, le=100),
        offset: int = Query(default=0, ge=0),
        current_user: CurrentUser = Depends(get_current_user),
        repo: CRMRepository = Depends(get_repo),
    ) -> list[ConversationOut]:
        return await repo.list_conversations(
            current_user.id, archived=archived, limit=limit, offset=offset
        )

    @app.post(
        "/api/conversations",
        response_model=ConversationOut,
        status_code=status.HTTP_201_CREATED,
    )
    async def create_conversation(
        payload: ConversationCreate,
        current_user: CurrentUser = Depends(get_current_user),
        repo: CRMRepository = Depends(get_repo),
    ) -> ConversationOut:
        return await repo.create_conversation(current_user.id, payload.title)

    @app.get(
        "/api/conversations/{conversation_id}/messages",
        response_model=list[ConversationMessageOut],
    )
    async def get_conversation_messages(
        conversation_id: UUID,
        request: Request,
        current_user: CurrentUser = Depends(get_current_user),
        repo: CRMRepository = Depends(get_repo),
    ) -> list[ConversationMessageOut]:
        if not await repo.get_conversation(current_user.id, conversation_id):
            raise HTTPException(status_code=404, detail="会话不存在")
        config = {
            "configurable": {
                "thread_id": checkpoint_thread_id(current_user.id, str(conversation_id))
            }
        }
        snapshot = await request.app.state.agent.aget_state(config)
        return serialize_conversation_messages(snapshot.values.get("messages", []))

    @app.patch("/api/conversations/{conversation_id}", response_model=ConversationOut)
    async def update_conversation(
        conversation_id: UUID,
        payload: ConversationUpdate,
        current_user: CurrentUser = Depends(get_current_user),
        repo: CRMRepository = Depends(get_repo),
    ) -> ConversationOut:
        conversation = await repo.update_conversation(
            current_user.id,
            conversation_id,
            title=payload.title,
            is_archived=payload.is_archived,
        )
        if not conversation:
            raise HTTPException(status_code=404, detail="会话不存在")
        return conversation

    @app.delete(
        "/api/conversations/{conversation_id}",
        status_code=status.HTTP_204_NO_CONTENT,
    )
    async def delete_conversation(
        conversation_id: UUID,
        request: Request,
        current_user: CurrentUser = Depends(get_current_user),
        repo: CRMRepository = Depends(get_repo),
    ) -> None:
        if not await repo.get_conversation(current_user.id, conversation_id):
            raise HTTPException(status_code=404, detail="会话不存在")
        storage_thread_id = checkpoint_thread_id(current_user.id, str(conversation_id))
        await request.app.state.checkpointer.adelete_thread(storage_thread_id)
        if not await repo.delete_conversation(current_user.id, conversation_id):
            raise HTTPException(status_code=404, detail="会话不存在")
        request.app.state.agent_service.release_conversation(current_user.id, conversation_id)

    @app.get(
        "/api/conversations/{conversation_id}/memories",
        response_model=list[ConversationMemoryOut],
    )
    async def list_conversation_memories(
        conversation_id: UUID,
        query: str | None = None,
        limit: int = Query(default=20, ge=1, le=100),
        current_user: CurrentUser = Depends(get_current_user),
        repo: CRMRepository = Depends(get_repo),
    ) -> list[ConversationMemoryOut]:
        if not await repo.get_conversation(current_user.id, conversation_id):
            raise HTTPException(status_code=404, detail="会话不存在")
        return await repo.recall_conversation_memories(
            current_user.id, conversation_id, query=query, limit=limit
        )

    @app.get("/api/agents")
    async def list_agents(
        _current_user: CurrentUser = Depends(get_current_user),
        registry: AgentRegistry = Depends(get_agent_registry),
    ) -> list[dict[str, Any]]:
        return registry.describe()

    @app.get("/api/customers", response_model=list[CustomerOut])
    async def list_customers(
        query: str | None = None,
        customer_status: str | None = Query(default=None, alias="status"),
        limit: int = Query(default=50, ge=1, le=100),
        offset: int = Query(default=0, ge=0),
        current_user: CurrentUser = Depends(get_current_user),
        repo: CRMRepository = Depends(get_repo),
    ) -> list[CustomerOut]:
        return await repo.list_customers(
            current_user,
            query=query,
            status=customer_status,
            limit=limit,
            offset=offset,
        )

    @app.post(
        "/api/customers",
        response_model=CustomerOut,
        status_code=status.HTTP_201_CREATED,
    )
    async def create_customer(
        payload: CustomerCreate,
        request: Request,
        current_user: CurrentUser = Depends(get_current_user),
        repo: CRMRepository = Depends(get_repo),
    ) -> CustomerOut:
        return await repo.create_customer(
            current_user, payload, request_id=request.state.request_id
        )

    @app.get("/api/customers/{customer_id}", response_model=CustomerOut)
    async def get_customer(
        customer_id: UUID,
        current_user: CurrentUser = Depends(get_current_user),
        repo: CRMRepository = Depends(get_repo),
    ) -> CustomerOut:
        customer = await repo.get_customer(current_user, customer_id)
        if not customer:
            raise HTTPException(status_code=404, detail="客户不存在")
        return customer

    @app.patch("/api/customers/{customer_id}", response_model=CustomerOut)
    async def update_customer(
        customer_id: UUID,
        payload: CustomerUpdate,
        request: Request,
        current_user: CurrentUser = Depends(get_current_user),
        repo: CRMRepository = Depends(get_repo),
    ) -> CustomerOut:
        customer = await repo.update_customer(
            current_user,
            customer_id,
            payload,
            request_id=request.state.request_id,
        )
        if not customer:
            raise HTTPException(status_code=404, detail="客户不存在")
        return customer

    @app.delete("/api/customers/{customer_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_customer(
        customer_id: UUID,
        request: Request,
        current_user: CurrentUser = Depends(get_current_user),
        repo: CRMRepository = Depends(get_repo),
    ) -> None:
        if not await repo.delete_customer(
            current_user, customer_id, request_id=request.state.request_id
        ):
            raise HTTPException(status_code=404, detail="客户不存在")

    @app.get("/api/leads", response_model=list[LeadOut])
    async def list_leads(
        query: str | None = None,
        lead_status: str | None = Query(default=None, alias="status"),
        limit: int = Query(default=50, ge=1, le=100),
        offset: int = Query(default=0, ge=0),
        current_user: CurrentUser = Depends(get_current_user),
        repo: CRMRepository = Depends(get_repo),
    ) -> list[LeadOut]:
        return await repo.list_leads(
            current_user, query=query, status=lead_status, limit=limit, offset=offset
        )

    @app.post("/api/leads", response_model=LeadOut, status_code=status.HTTP_201_CREATED)
    async def create_lead(
        payload: LeadCreate,
        request: Request,
        current_user: CurrentUser = Depends(get_current_user),
        repo: CRMRepository = Depends(get_repo),
    ) -> LeadOut:
        return await repo.create_lead(current_user, payload, request_id=request.state.request_id)

    @app.get("/api/leads/{entity_id}", response_model=LeadOut)
    async def get_lead(
        entity_id: UUID,
        current_user: CurrentUser = Depends(get_current_user),
        repo: CRMRepository = Depends(get_repo),
    ) -> LeadOut:
        entity = await repo.get_lead(current_user, entity_id)
        if not entity:
            raise HTTPException(status_code=404, detail="线索不存在")
        return entity

    @app.patch("/api/leads/{entity_id}", response_model=LeadOut)
    async def update_lead(
        entity_id: UUID,
        payload: LeadUpdate,
        request: Request,
        current_user: CurrentUser = Depends(get_current_user),
        repo: CRMRepository = Depends(get_repo),
    ) -> LeadOut:
        entity = await repo.update_lead(
            current_user, entity_id, payload, request_id=request.state.request_id
        )
        if not entity:
            raise HTTPException(status_code=404, detail="线索不存在")
        return entity

    @app.delete("/api/leads/{entity_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_lead(
        entity_id: UUID,
        request: Request,
        current_user: CurrentUser = Depends(get_current_user),
        repo: CRMRepository = Depends(get_repo),
    ) -> None:
        if not await repo.delete_lead(current_user, entity_id, request_id=request.state.request_id):
            raise HTTPException(status_code=404, detail="线索不存在")

    @app.post("/api/leads/{entity_id}/convert", response_model=LeadConversionOut)
    async def convert_lead(
        entity_id: UUID,
        payload: LeadConversionRequest,
        request: Request,
        current_user: CurrentUser = Depends(get_current_user),
        repo: CRMRepository = Depends(get_repo),
    ) -> LeadConversionOut:
        return await repo.convert_lead(
            current_user, entity_id, payload, request_id=request.state.request_id
        )

    @app.get("/api/accounts", response_model=list[AccountOut])
    async def list_accounts(
        query: str | None = None,
        account_status: str | None = Query(default=None, alias="status"),
        owner_id: UUID | None = None,
        limit: int = Query(default=50, ge=1, le=100),
        offset: int = Query(default=0, ge=0),
        current_user: CurrentUser = Depends(get_current_user),
        repo: CRMRepository = Depends(get_repo),
    ) -> list[AccountOut]:
        return await repo.list_accounts(
            current_user,
            query=query,
            status=account_status,
            owner_id=owner_id,
            limit=limit,
            offset=offset,
        )

    @app.post("/api/accounts", response_model=AccountOut, status_code=status.HTTP_201_CREATED)
    async def create_account(
        payload: AccountCreate,
        request: Request,
        current_user: CurrentUser = Depends(get_current_user),
        repo: CRMRepository = Depends(get_repo),
    ) -> AccountOut:
        return await repo.create_account(current_user, payload, request_id=request.state.request_id)

    @app.get("/api/accounts/{entity_id}", response_model=AccountOut)
    async def get_account(
        entity_id: UUID,
        current_user: CurrentUser = Depends(get_current_user),
        repo: CRMRepository = Depends(get_repo),
    ) -> AccountOut:
        entity = await repo.get_account(current_user, entity_id)
        if not entity:
            raise HTTPException(status_code=404, detail="公司客户不存在")
        return entity

    @app.get("/api/accounts/{entity_id}/overview", response_model=AccountOverviewOut)
    async def get_account_overview(
        entity_id: UUID,
        current_user: CurrentUser = Depends(get_current_user),
        repo: CRMRepository = Depends(get_repo),
    ) -> AccountOverviewOut:
        overview = await repo.get_account_overview(current_user, entity_id)
        if not overview:
            raise HTTPException(status_code=404, detail="公司客户不存在")
        return overview

    @app.post("/api/accounts/{entity_id}/transfer", response_model=AccountChainTransferOut)
    async def transfer_account_chain(
        entity_id: UUID,
        payload: AccountChainTransferRequest,
        request: Request,
        current_user: CurrentUser = Depends(get_current_user),
        repo: CRMRepository = Depends(get_repo),
    ) -> AccountChainTransferOut:
        outcome = await repo.transfer_account_chain(
            current_user,
            entity_id,
            payload.new_owner_id,
            request_id=request.state.request_id,
        )
        if not outcome:
            raise HTTPException(status_code=404, detail="公司客户不存在")
        return outcome

    @app.patch("/api/accounts/{entity_id}", response_model=AccountOut)
    async def update_account(
        entity_id: UUID,
        payload: AccountUpdate,
        request: Request,
        current_user: CurrentUser = Depends(get_current_user),
        repo: CRMRepository = Depends(get_repo),
    ) -> AccountOut:
        entity = await repo.update_account(
            current_user, entity_id, payload, request_id=request.state.request_id
        )
        if not entity:
            raise HTTPException(status_code=404, detail="公司客户不存在")
        return entity

    @app.delete("/api/accounts/{entity_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_account(
        entity_id: UUID,
        request: Request,
        current_user: CurrentUser = Depends(get_current_user),
        repo: CRMRepository = Depends(get_repo),
    ) -> None:
        if not await repo.delete_account(
            current_user, entity_id, request_id=request.state.request_id
        ):
            raise HTTPException(status_code=404, detail="公司客户不存在")

    @app.get("/api/contacts", response_model=list[ContactOut])
    async def list_contacts(
        query: str | None = None,
        account_id: UUID | None = None,
        limit: int = Query(default=50, ge=1, le=100),
        offset: int = Query(default=0, ge=0),
        current_user: CurrentUser = Depends(get_current_user),
        repo: CRMRepository = Depends(get_repo),
    ) -> list[ContactOut]:
        return await repo.list_contacts(
            current_user, query=query, account_id=account_id, limit=limit, offset=offset
        )

    @app.post("/api/contacts", response_model=ContactOut, status_code=status.HTTP_201_CREATED)
    async def create_contact(
        payload: ContactCreate,
        request: Request,
        current_user: CurrentUser = Depends(get_current_user),
        repo: CRMRepository = Depends(get_repo),
    ) -> ContactOut:
        return await repo.create_contact(current_user, payload, request_id=request.state.request_id)

    @app.get("/api/contacts/{entity_id}", response_model=ContactOut)
    async def get_contact(
        entity_id: UUID,
        current_user: CurrentUser = Depends(get_current_user),
        repo: CRMRepository = Depends(get_repo),
    ) -> ContactOut:
        entity = await repo.get_contact(current_user, entity_id)
        if not entity:
            raise HTTPException(status_code=404, detail="联系人不存在")
        return entity

    @app.patch("/api/contacts/{entity_id}", response_model=ContactOut)
    async def update_contact(
        entity_id: UUID,
        payload: ContactUpdate,
        request: Request,
        current_user: CurrentUser = Depends(get_current_user),
        repo: CRMRepository = Depends(get_repo),
    ) -> ContactOut:
        entity = await repo.update_contact(
            current_user, entity_id, payload, request_id=request.state.request_id
        )
        if not entity:
            raise HTTPException(status_code=404, detail="联系人不存在")
        return entity

    @app.delete("/api/contacts/{entity_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_contact(
        entity_id: UUID,
        request: Request,
        current_user: CurrentUser = Depends(get_current_user),
        repo: CRMRepository = Depends(get_repo),
    ) -> None:
        if not await repo.delete_contact(
            current_user, entity_id, request_id=request.state.request_id
        ):
            raise HTTPException(status_code=404, detail="联系人不存在")

    @app.get("/api/opportunities", response_model=list[OpportunityOut])
    async def list_opportunities(
        query: str | None = None,
        stage: str | None = None,
        account_id: UUID | None = None,
        limit: int = Query(default=50, ge=1, le=100),
        offset: int = Query(default=0, ge=0),
        current_user: CurrentUser = Depends(get_current_user),
        repo: CRMRepository = Depends(get_repo),
    ) -> list[OpportunityOut]:
        return await repo.list_opportunities(
            current_user,
            query=query,
            stage=stage,
            account_id=account_id,
            limit=limit,
            offset=offset,
        )

    @app.post(
        "/api/opportunities",
        response_model=OpportunityOut,
        status_code=status.HTTP_201_CREATED,
    )
    async def create_opportunity(
        payload: OpportunityCreate,
        request: Request,
        current_user: CurrentUser = Depends(get_current_user),
        repo: CRMRepository = Depends(get_repo),
    ) -> OpportunityOut:
        return await repo.create_opportunity(
            current_user, payload, request_id=request.state.request_id
        )

    @app.get("/api/opportunities/{entity_id}", response_model=OpportunityOut)
    async def get_opportunity(
        entity_id: UUID,
        current_user: CurrentUser = Depends(get_current_user),
        repo: CRMRepository = Depends(get_repo),
    ) -> OpportunityOut:
        entity = await repo.get_opportunity(current_user, entity_id)
        if not entity:
            raise HTTPException(status_code=404, detail="商机不存在")
        return entity

    @app.patch("/api/opportunities/{entity_id}", response_model=OpportunityOut)
    async def update_opportunity(
        entity_id: UUID,
        payload: OpportunityUpdate,
        request: Request,
        current_user: CurrentUser = Depends(get_current_user),
        repo: CRMRepository = Depends(get_repo),
    ) -> OpportunityOut:
        entity = await repo.update_opportunity(
            current_user, entity_id, payload, request_id=request.state.request_id
        )
        if not entity:
            raise HTTPException(status_code=404, detail="商机不存在")
        return entity

    @app.delete("/api/opportunities/{entity_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_opportunity(
        entity_id: UUID,
        request: Request,
        current_user: CurrentUser = Depends(get_current_user),
        repo: CRMRepository = Depends(get_repo),
    ) -> None:
        if not await repo.delete_opportunity(
            current_user, entity_id, request_id=request.state.request_id
        ):
            raise HTTPException(status_code=404, detail="商机不存在")

    @app.get("/api/activities", response_model=list[ActivityOut])
    async def list_activities(
        query: str | None = None,
        activity_status: str | None = Query(default=None, alias="status"),
        assigned_user_id: UUID | None = None,
        account_id: UUID | None = None,
        contact_id: UUID | None = None,
        lead_id: UUID | None = None,
        opportunity_id: UUID | None = None,
        limit: int = Query(default=50, ge=1, le=100),
        offset: int = Query(default=0, ge=0),
        current_user: CurrentUser = Depends(get_current_user),
        repo: CRMRepository = Depends(get_repo),
    ) -> list[ActivityOut]:
        return await repo.list_activities(
            current_user,
            query=query,
            status=activity_status,
            assigned_user_id=assigned_user_id,
            account_id=account_id,
            contact_id=contact_id,
            lead_id=lead_id,
            opportunity_id=opportunity_id,
            limit=limit,
            offset=offset,
        )

    @app.post("/api/activities", response_model=ActivityOut, status_code=status.HTTP_201_CREATED)
    async def create_activity(
        payload: ActivityCreate,
        request: Request,
        current_user: CurrentUser = Depends(get_current_user),
        repo: CRMRepository = Depends(get_repo),
    ) -> ActivityOut:
        return await repo.create_activity(
            current_user, payload, request_id=request.state.request_id
        )

    @app.get("/api/activities/{entity_id}", response_model=ActivityOut)
    async def get_activity(
        entity_id: UUID,
        current_user: CurrentUser = Depends(get_current_user),
        repo: CRMRepository = Depends(get_repo),
    ) -> ActivityOut:
        entity = await repo.get_activity(current_user, entity_id)
        if not entity:
            raise HTTPException(status_code=404, detail="活动不存在")
        return entity

    @app.patch("/api/activities/{entity_id}", response_model=ActivityOut)
    async def update_activity(
        entity_id: UUID,
        payload: ActivityUpdate,
        request: Request,
        current_user: CurrentUser = Depends(get_current_user),
        repo: CRMRepository = Depends(get_repo),
    ) -> ActivityOut:
        entity = await repo.update_activity(
            current_user, entity_id, payload, request_id=request.state.request_id
        )
        if not entity:
            raise HTTPException(status_code=404, detail="活动不存在")
        return entity

    @app.delete("/api/activities/{entity_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_activity(
        entity_id: UUID,
        request: Request,
        current_user: CurrentUser = Depends(get_current_user),
        repo: CRMRepository = Depends(get_repo),
    ) -> None:
        if not await repo.delete_activity(
            current_user, entity_id, request_id=request.state.request_id
        ):
            raise HTTPException(status_code=404, detail="活动不存在")

    @app.get("/api/pending-actions", response_model=list[PendingActionOut])
    async def list_pending_actions(
        conversation_id: UUID | None = None,
        current_user: CurrentUser = Depends(get_current_user),
        repo: CRMRepository = Depends(get_repo),
    ) -> list[PendingActionOut]:
        return await repo.list_pending_actions(current_user, conversation_id=conversation_id)

    @app.post("/api/pending-actions/{action_id}/approve", response_model=PendingActionOut)
    async def approve_pending_action(
        action_id: UUID,
        request: Request,
        current_user: CurrentUser = Depends(get_current_user),
        repo: CRMRepository = Depends(get_repo),
    ) -> PendingActionOut:
        try:
            action = await repo.decide_pending_action(
                current_user,
                action_id,
                approve=True,
                request_id=request.state.request_id,
            )
        except InvalidActionError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if not action:
            raise HTTPException(status_code=404, detail="待确认操作不存在")
        if action.status == "failed":
            raise HTTPException(
                status_code=409,
                detail=safe_pending_failure_detail(action.error_message),
            )
        if action.status == "expired":
            raise HTTPException(status_code=409, detail="待确认操作已过期，请重新发起")
        if action.status != "approved":
            raise HTTPException(status_code=409, detail="该操作已不处于可确认状态")
        return action

    @app.post("/api/pending-actions/{action_id}/reject", response_model=PendingActionOut)
    async def reject_pending_action(
        action_id: UUID,
        request: Request,
        current_user: CurrentUser = Depends(get_current_user),
        repo: CRMRepository = Depends(get_repo),
    ) -> PendingActionOut:
        action = await repo.decide_pending_action(
            current_user,
            action_id,
            approve=False,
            request_id=request.state.request_id,
        )
        if not action:
            raise HTTPException(status_code=404, detail="待确认操作不存在")
        if action.status != "rejected":
            raise HTTPException(status_code=409, detail="该操作已不处于可拒绝状态")
        return action

    return app


app = create_app()
