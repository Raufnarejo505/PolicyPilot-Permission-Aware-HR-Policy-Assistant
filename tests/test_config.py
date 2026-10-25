"""Configuration validation must fail before serving requests."""

import pytest
from pydantic import ValidationError

from policypilot.app.core.config import Settings


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch):
    for key in (
        "ENVIRONMENT",
        "LOG_LEVEL",
        "CACHE_ENABLED",
        "IDENTITY_DATABASE",
        "CONTENT_DATABASE",
        "SESSION_TTL_SECONDS",
        "RETRIEVAL_LIMIT",
        "ANSWER_TOP_N",
    ):
        monkeypatch.delenv(f"POLICYPILOT_{key}", raising=False)


def test_safe_defaults(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    settings = Settings()
    assert settings.environment == "development"
    assert settings.log_level == "INFO"
    assert settings.cache_enabled is False


def test_environment_overrides(monkeypatch):
    monkeypatch.setenv("POLICYPILOT_ENVIRONMENT", "test")
    monkeypatch.setenv("POLICYPILOT_LOG_LEVEL", "WARNING")
    assert Settings(_env_file=None).environment == "test"
    assert Settings(_env_file=None).log_level == "WARNING"


@pytest.mark.parametrize(
    ("key", "value"),
    [("ENVIRONMENT", "typo"), ("LOG_LEVEL", "TRACE"), ("CACHE_ENABLED", "true")],
)
def test_invalid_environment_fails_startup(key, value, monkeypatch):
    monkeypatch.setenv(f"POLICYPILOT_{key}", value)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_dotenv_loading(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("POLICYPILOT_ENVIRONMENT=test\n", encoding="utf-8")
    assert Settings().environment == "test"


def test_unknown_dotenv_key_is_rejected(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("POLICYPILOT_ENVIRONMNET=test\n", encoding="utf-8")
    with pytest.raises(ValidationError):
        Settings()


def test_identity_settings_from_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("POLICYPILOT_IDENTITY_DATABASE", str(tmp_path / "identity.db"))
    monkeypatch.setenv("POLICYPILOT_CONTENT_DATABASE", str(tmp_path / "content.db"))
    monkeypatch.setenv("POLICYPILOT_SESSION_TTL_SECONDS", "600")
    monkeypatch.setenv("POLICYPILOT_RETRIEVAL_LIMIT", "30")
    monkeypatch.setenv("POLICYPILOT_ANSWER_TOP_N", "7")
    settings = Settings(_env_file=None)
    assert settings.identity_database == tmp_path / "identity.db"
    assert settings.content_database == tmp_path / "content.db"
    assert settings.session_ttl_seconds == 600
    assert settings.retrieval_limit == 30
    assert settings.answer_top_n == 7


@pytest.mark.parametrize("value", [0, 101, True, 20.0, "invalid"])
def test_invalid_retrieval_limit(value):
    with pytest.raises(ValidationError):
        Settings(retrieval_limit=value, _env_file=None)


@pytest.mark.parametrize("value", [0, 21, True, 5.0, "invalid"])
def test_invalid_answer_top_n(value):
    with pytest.raises(ValidationError):
        Settings(answer_top_n=value, _env_file=None)


@pytest.mark.parametrize("value", [0, 59, 86401, True, 600.0, "invalid"])
def test_invalid_session_lifetime(value):
    with pytest.raises(ValidationError):
        Settings(session_ttl_seconds=value, _env_file=None)


def test_settings_are_immutable():
    settings = Settings(_env_file=None)
    with pytest.raises(ValidationError):
        settings.cache_enabled = True
