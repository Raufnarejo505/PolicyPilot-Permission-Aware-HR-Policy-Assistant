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
        "SESSION_TTL_SECONDS",
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
    monkeypatch.setenv("POLICYPILOT_SESSION_TTL_SECONDS", "600")
    settings = Settings(_env_file=None)
    assert settings.identity_database == tmp_path / "identity.db"
    assert settings.session_ttl_seconds == 600


@pytest.mark.parametrize("value", [0, 59, 86401, True, 600.0, "invalid"])
def test_invalid_session_lifetime(value):
    with pytest.raises(ValidationError):
        Settings(session_ttl_seconds=value, _env_file=None)


def test_settings_are_immutable():
    settings = Settings(_env_file=None)
    with pytest.raises(ValidationError):
        settings.cache_enabled = True
