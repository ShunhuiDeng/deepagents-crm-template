from functools import lru_cache
from string import hexdigits
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", ".env.local"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = Field(
        # A fail-closed endpoint keeps tests importable without risking writes to a
        # developer's conventional local PostgreSQL instance. Real deployments must
        # set DATABASE_URL in .env.local or the secret manager.
        default="postgresql://invalid:invalid@127.0.0.1:1/configure_database_url",
        validation_alias="DATABASE_URL",
    )

    @field_validator("database_url")
    @classmethod
    def require_tls_for_remote_postgres(cls, value: str) -> str:
        """Refuse silent plaintext fallback when PostgreSQL is not on this machine."""
        parsed = urlsplit(value)
        if parsed.scheme not in {"postgresql", "postgres"}:
            return value
        if parsed.hostname in {None, "localhost", "127.0.0.1", "::1"}:
            return value
        query = dict(parse_qsl(parsed.query, keep_blank_values=True))
        if "sslmode" not in query:
            query["sslmode"] = "require"
        return urlunsplit(parsed._replace(query=urlencode(query)))
    model_name: str = Field(default="openai:gpt-5.4-mini", validation_alias="MODEL_NAME")
    openai_api_key: SecretStr | None = Field(default=None, validation_alias="OPENAI_API_KEY")
    langgraph_aes_key: SecretStr | None = Field(
        default=None, validation_alias="LANGGRAPH_AES_KEY"
    )

    app_host: str = Field(default="0.0.0.0", validation_alias="APP_HOST")
    app_port: int = Field(default=8000, validation_alias="APP_PORT")
    db_pool_min_size: int = Field(default=1, validation_alias="DB_POOL_MIN_SIZE")
    db_pool_max_size: int = Field(default=10, validation_alias="DB_POOL_MAX_SIZE")
    db_pool_timeout_seconds: float = Field(
        default=8.0,
        gt=0,
        le=60,
        validation_alias="DB_POOL_TIMEOUT_SECONDS",
    )
    checkpoint_pool_max_size: int = Field(
        default=5, validation_alias="CHECKPOINT_POOL_MAX_SIZE"
    )

    session_cookie_name: str = Field(
        default="crm_session", validation_alias="SESSION_COOKIE_NAME"
    )
    session_ttl_hours: int = Field(default=168, validation_alias="SESSION_TTL_HOURS")
    cookie_secure: bool = Field(default=False, validation_alias="COOKIE_SECURE")
    registration_enabled: bool = Field(
        default=True, validation_alias="REGISTRATION_ENABLED"
    )
    first_user_is_admin: bool = Field(
        default=True, validation_alias="FIRST_USER_IS_ADMIN"
    )
    first_admin_local_only: bool = Field(
        default=True, validation_alias="FIRST_ADMIN_LOCAL_ONLY"
    )

    enabled_subagents: str = Field(
        default="crud-agent",
        validation_alias="ENABLED_SUBAGENTS",
    )
    subagent_execution: str = Field(default="sync", validation_alias="SUBAGENT_EXECUTION")
    subagent_server_url: str | None = Field(
        default=None, validation_alias="SUBAGENT_SERVER_URL"
    )
    agent_timeout_seconds: float = Field(
        default=120.0, validation_alias="AGENT_TIMEOUT_SECONDS"
    )

    def model_api_key(self) -> str | None:
        """Return the configured provider key and fail clearly for an OpenAI model."""
        if not self.model_name.startswith("openai:"):
            return None
        value = self.openai_api_key.get_secret_value().strip() if self.openai_api_key else ""
        if not value or value == "replace-me":
            raise RuntimeError(
                "MODEL_NAME 使用 OpenAI 模型，但 .env 中没有有效的 OPENAI_API_KEY"
            )
        return value

    def checkpoint_encryption_key(self) -> bytes | None:
        """Return an optional 256-bit AES key used for persisted Agent checkpoints."""
        value = (
            self.langgraph_aes_key.get_secret_value().strip()
            if self.langgraph_aes_key
            else ""
        )
        if not value or value.startswith("replace-"):
            return None
        if len(value) != 64 or any(character not in hexdigits for character in value):
            raise RuntimeError("LANGGRAPH_AES_KEY 必须是 64 位十六进制字符串")
        return bytes.fromhex(value)

    def enabled_subagent_names(self) -> set[str]:
        return {name.strip() for name in self.enabled_subagents.split(",") if name.strip()}

    def subagent_execution_mode(self) -> str:
        value = self.subagent_execution.strip().lower()
        if value not in {"sync", "async"}:
            raise ValueError("SUBAGENT_EXECUTION 必须是 sync 或 async")
        return value

    def validate_subagent_runtime(self) -> None:
        if self.subagent_execution_mode() == "async" and not (
            self.subagent_server_url and self.subagent_server_url.strip()
        ):
            raise RuntimeError(
                "FastAPI 模式使用异步子 Agent 时必须配置 SUBAGENT_SERVER_URL，"
                "指向 Agent Protocol 服务"
            )


@lru_cache
def get_settings() -> Settings:
    return Settings()
