"""Local provisioning never installs default accounts or accepts command-line passwords."""

import json
import sqlite3
import subprocess
import sys
from contextlib import closing

import pytest

from policypilot import manage
from policypilot.app.core.security import PasswordSecurity
from policypilot.app.models.domain import Permission, SystemRole
from policypilot.app.services.identity_store import IdentityStore


@pytest.fixture
def operator(tmp_path, monkeypatch):
    path = tmp_path / "private" / "identities.db"
    args = ["--database", str(path)]
    monkeypatch.setattr(manage.getpass, "getpass", lambda prompt: "local-test-passphrase-only")
    assert manage.main([*args, "init-db"]) == 0
    return path, args, IdentityStore(path)


def test_provision_and_update_access(operator):
    path, args, store = operator
    assert (
        manage.main(
            [
                *args,
                "create-user",
                "alice",
                "--user-id",
                "u1",
                "--permission",
                "HR",
                "--role",
                "Admin",
            ]
        )
        == 0
    )
    user = store.get_user("u1")
    assert user.assigned_permissions == frozenset({Permission.HR})
    assert user.system_roles == frozenset({SystemRole.ADMIN})
    credential = store.get_credentials("alice")
    assert PasswordSecurity().verify(credential.password_hash, "local-test-passphrase-only")
    assert manage.main([*args, "set-access", "u1", "--permission", "Finance"]) == 0
    assert store.get_user("u1").assigned_permissions == frozenset({Permission.FINANCE})
    assert store.get_user("u1").system_roles == frozenset()
    assert manage.main([*args, "set-access", "u1"]) == 0
    assert store.get_user("u1").assigned_permissions == frozenset()
    assert manage.main([*args, "disable-user", "u1"]) == 0
    assert store.get_user("u1") is None
    assert manage.main([*args, "enable-user", "u1"]) == 0
    assert store.get_user("u1") is not None
    old_hash = credential.password_hash
    assert manage.main([*args, "reset-password", "u1"]) == 0
    assert store.get_credentials("alice").password_hash != old_hash
    with closing(sqlite3.connect(path)) as connection:
        assert connection.execute("SELECT count(*) FROM sessions").fetchone()[0] == 0


def test_default_path_from_environment(tmp_path, monkeypatch):
    path = tmp_path / "default.db"
    monkeypatch.setenv("POLICYPILOT_IDENTITY_DATABASE", str(path))
    monkeypatch.chdir(tmp_path)
    assert manage.main(["init-db"]) == 0
    assert IdentityStore(path).get_user("admin") is None


def test_password_confirmation_must_match(operator, monkeypatch, capsys):
    _, args, store = operator
    answers = iter(["first-sensitive-password", "second-sensitive-password"])
    monkeypatch.setattr(manage.getpass, "getpass", lambda prompt: next(answers))
    assert manage.main([*args, "create-user", "alice", "--user-id", "u1"]) == 1
    assert store.get_credentials("alice") is None
    assert "sensitive-password" not in capsys.readouterr().err


@pytest.mark.parametrize("exception", [EOFError, OSError, ValueError, sqlite3.OperationalError])
def test_operator_errors_are_generic(operator, monkeypatch, capsys, exception):
    _, args, _ = operator

    def fail(prompt):
        raise exception("private-data-must-not-appear")

    monkeypatch.setattr(manage.getpass, "getpass", fail)
    assert manage.main([*args, "create-user", "alice", "--user-id", "u1"]) == 1
    error = capsys.readouterr().err
    assert "Operation failed" in error
    assert "private-data" not in error


def test_missing_user_and_duplicate_user_fail_safely(operator):
    _, args, _ = operator
    assert manage.main([*args, "disable-user", "absent"]) == 1
    create = [*args, "create-user", "alice", "--user-id", "u1"]
    assert manage.main(create) == 0
    assert manage.main(create) == 1


@pytest.mark.parametrize(
    "arguments",
    [
        [],
        ["create-user", "alice"],
        ["create-user", "alice", "--user-id", "u1", "--password", "x"],
        ["set-access", "u1", "--permission", "Unknown"],
    ],
)
def test_invalid_command_arguments(arguments):
    with pytest.raises(SystemExit) as caught:
        manage.main(arguments)
    assert caught.value.code == 2


def test_module_entrypoint_help():
    result = subprocess.run(
        [sys.executable, "-m", "policypilot.manage", "--help"],
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    )
    assert "create-user" in result.stdout
    assert "reset-password" in result.stdout


def test_seed_demo_writes_private_one_time_credentials(operator, tmp_path, capsys):
    _, args, store = operator
    credentials = tmp_path / "demo" / "accounts.json"
    assert manage.main([*args, "seed-demo", "--credentials", str(credentials)]) == 0
    payload = json.loads(credentials.read_text(encoding="utf-8"))
    assert payload["version"] == 1
    assert len(payload["password"]) >= 12
    assert [account["username"] for account in payload["accounts"]] == [
        "demo.employee",
        "demo.manager",
        "demo.hr",
        "demo.finance",
        "demo.admin",
    ]
    assert credentials.stat().st_mode & 0o777 == 0o600
    for username in ("demo.employee", "demo.manager", "demo.hr", "demo.finance", "demo.admin"):
        credential = store.get_credentials(username)
        assert credential is not None
        assert PasswordSecurity().verify(credential.password_hash, payload["password"])
    output = capsys.readouterr()
    assert payload["password"] not in output.out
    assert payload["password"] not in output.err
    before = credentials.read_bytes()
    assert manage.main([*args, "seed-demo", "--credentials", str(credentials)]) == 1
    assert credentials.read_bytes() == before


def test_seed_demo_does_not_leave_partial_accounts_on_conflict(operator, tmp_path):
    _, args, store = operator
    assert manage.main([*args, "create-user", "demo.hr", "--user-id", "existing"]) == 0
    credentials = tmp_path / "accounts.json"
    assert manage.main([*args, "seed-demo", "--credentials", str(credentials)]) == 1
    assert not credentials.exists()
    assert store.get_credentials("demo.employee") is None
    assert store.get_credentials("demo.hr") is not None
