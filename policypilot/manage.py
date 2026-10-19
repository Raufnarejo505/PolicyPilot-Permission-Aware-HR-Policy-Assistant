"""Trusted local operator commands. Never accept passwords on the command line."""

import argparse
import getpass
import sqlite3
import sys
from collections.abc import Sequence
from pathlib import Path

from policypilot.app.core.config import Settings
from policypilot.app.core.security import PasswordSecurity
from policypilot.app.models.domain import Permission, SystemRole
from policypilot.app.services.identity_store import IdentityStore


def _password_hash() -> str:
    password = getpass.getpass("Password (12–1024 UTF-8 bytes): ")
    confirmation = getpass.getpass("Confirm password: ")
    if password != confirmation:
        raise ValueError("Password confirmation differs")
    return PasswordSecurity().hash(password)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, help="Defaults to configured identity database")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init-db", help="Explicitly create an empty private identity database")
    create = commands.add_parser("create-user", help="Create a user with an interactive password")
    create.add_argument("username")
    create.add_argument("--user-id", required=True)
    access = commands.add_parser("set-access", help="Replace all explicit grants and roles")
    access.add_argument("user_id")
    for command in (create, access):
        command.add_argument("--permission", action="append", default=[], choices=list(Permission))
        command.add_argument("--role", action="append", default=[], choices=list(SystemRole))
    for name in ("disable-user", "enable-user", "reset-password"):
        commands.add_parser(name).add_argument("user_id")
    args = parser.parse_args(argv)
    try:
        path = args.database if args.database is not None else Settings().identity_database
        store = IdentityStore(path)
        if args.command == "init-db":
            path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            store.initialize()
        elif args.command == "create-user":
            store.create_user(
                user_id=args.user_id,
                username=args.username,
                password_hash=_password_hash(),
                assigned_permissions=frozenset(Permission(label) for label in args.permission),
                system_roles=frozenset(SystemRole(role) for role in args.role),
            )
        elif args.command == "set-access":
            store.set_access(
                args.user_id,
                assigned_permissions=frozenset(Permission(label) for label in args.permission),
                system_roles=frozenset(SystemRole(role) for role in args.role),
            )
        elif args.command == "reset-password":
            store.set_password_hash(args.user_id, _password_hash())
        else:
            store.set_enabled(args.user_id, args.command == "enable-user")
    except (OSError, sqlite3.Error, ValueError, EOFError):
        # Do not print exceptions: they can contain SQL values or filesystem details.
        print("Operation failed; check configuration and supplied values.", file=sys.stderr)
        return 1
    print("Operation completed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
