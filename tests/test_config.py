import pytest

from app.config import Settings


def test_default_database_url_is_fail_closed() -> None:
    settings = Settings(_env_file=None)

    assert settings.database_url == (
        "postgresql://invalid:invalid@127.0.0.1:1/configure_database_url"
    )


def test_openai_model_requires_real_key() -> None:
    settings = Settings(
        _env_file=None,
        DATABASE_URL="postgresql://localhost/crm",
        MODEL_NAME="openai:gpt-5.4-mini",
        OPENAI_API_KEY="replace-me",
    )
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        settings.model_api_key()


def test_openai_model_returns_configured_key() -> None:
    settings = Settings(
        _env_file=None,
        DATABASE_URL="postgresql://localhost/crm",
        MODEL_NAME="openai:gpt-5.4-mini",
        OPENAI_API_KEY="test-key",
    )
    assert settings.model_api_key() == "test-key"


def test_other_provider_does_not_require_openai_key() -> None:
    settings = Settings(
        _env_file=None,
        DATABASE_URL="postgresql://localhost/crm",
        MODEL_NAME="anthropic:claude-sonnet-4-6",
    )
    assert settings.model_api_key() is None


def test_subagent_execution_mode_is_validated() -> None:
    settings = Settings(_env_file=None, SUBAGENT_EXECUTION="async")
    assert settings.subagent_execution_mode() == "async"

    invalid = Settings(_env_file=None, SUBAGENT_EXECUTION="background-thread")
    with pytest.raises(ValueError, match="sync 或 async"):
        invalid.subagent_execution_mode()


def test_async_subagents_require_agent_protocol_url_in_fastapi() -> None:
    settings = Settings(_env_file=None, SUBAGENT_EXECUTION="async", SUBAGENT_SERVER_URL=None)
    with pytest.raises(RuntimeError, match="Agent Protocol"):
        settings.validate_subagent_runtime()


def test_first_admin_is_loopback_only_by_default() -> None:
    settings = Settings(_env_file=None)
    assert settings.first_user_is_admin is True
    assert settings.first_admin_local_only is True


def test_remote_postgres_requires_tls_when_env_omits_sslmode() -> None:
    settings = Settings(
        _env_file=None,
        DATABASE_URL="postgresql://user:pass@db.example.com:5432/deepagents_crm",
    )

    assert settings.database_url.endswith("/deepagents_crm?sslmode=require")


def test_explicit_postgres_sslmode_is_preserved() -> None:
    settings = Settings(
        _env_file=None,
        DATABASE_URL=(
            "postgresql://user:pass@db.example.com:5432/"
            "deepagents_crm?sslmode=verify-full"
        ),
    )

    assert settings.database_url.endswith("?sslmode=verify-full")


def test_database_pool_timeout_is_bounded() -> None:
    settings = Settings(_env_file=None, DB_POOL_TIMEOUT_SECONDS=5)

    assert settings.db_pool_timeout_seconds == 5

    with pytest.raises(ValueError):
        Settings(_env_file=None, DB_POOL_TIMEOUT_SECONDS=0)


def test_knowledge_chunk_overlap_must_be_smaller_than_chunk_size() -> None:
    settings = Settings(
        _env_file=None,
        KNOWLEDGE_CHUNK_SIZE=1000,
        KNOWLEDGE_CHUNK_OVERLAP=1000,
    )

    with pytest.raises(ValueError, match="CHUNK_OVERLAP"):
        settings.validate_subagent_runtime()
