"""Trusted local operator commands. Never accept passwords on the command line."""

import argparse
import getpass
import json
import os
import secrets
import sqlite3
import sys
from collections.abc import Sequence
from dataclasses import asdict
from pathlib import Path

from policypilot.app.core.config import Settings
from policypilot.app.core.security import PasswordSecurity
from policypilot.app.models.domain import Permission, SystemRole
from policypilot.app.services.content_store import ContentStore
from policypilot.app.services.corpus import (
    ingest_manifest,
    prepare_manifest,
    read_manifest,
    require_approved,
)
from policypilot.app.services.evaluation import evaluate_retrieval, load_items
from policypilot.app.services.identity_store import IdentityStore, NewUser

_DEMO_ACCOUNTS: tuple[
    tuple[str, str, str, str, frozenset[Permission], frozenset[SystemRole], str], ...
] = (
    (
        "Employee",
        "demo.employee",
        "demo-employee",
        "Ask policies available to every employee.",
        frozenset(),
        frozenset(),
        "workspace",
    ),
    (
        "Manager",
        "demo.manager",
        "demo-manager",
        "Manager procedures and responsibilities.",
        frozenset({Permission.MANAGER}),
        frozenset(),
        "workspace",
    ),
    (
        "HR",
        "demo.hr",
        "demo-hr",
        "HR-only case handling and guidance.",
        frozenset({Permission.HR}),
        frozenset(),
        "workspace",
    ),
    (
        "Finance",
        "demo.finance",
        "demo-finance",
        "Finance-only compensation content.",
        frozenset({Permission.FINANCE}),
        frozenset(),
        "workspace",
    ),
    (
        "Administrator",
        "demo.admin",
        "demo-admin",
        "Read-only access matrix and corpus health.",
        frozenset(),
        frozenset({SystemRole.ADMIN}),
        "admin",
    ),
)
_DEMO_CREDENTIALS = Path(".cache/demo-accounts.json")


def _stage_demo_credentials(path: Path, password: str) -> Path:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.exists() or path.is_symlink():
        raise ValueError("Demo credentials already exist")
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(
                {
                    "version": 1,
                    "password": password,
                    "accounts": [
                        {
                            "label": label,
                            "description": description,
                            "username": username,
                            "destination": destination,
                        }
                        for label, username, _, description, _, _, destination in _DEMO_ACCOUNTS
                    ],
                },
                stream,
                ensure_ascii=True,
                separators=(",", ":"),
            )
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return temporary


def _seed_demo(store: IdentityStore, path: Path) -> None:
    password = secrets.token_urlsafe(24)
    password_hash = PasswordSecurity().hash(password)
    staged = _stage_demo_credentials(path, password)
    try:
        store.create_users(
            tuple(
                NewUser(
                    user_id=user_id,
                    username=username,
                    password_hash=password_hash,
                    assigned_permissions=permissions,
                    system_roles=roles,
                )
                for _, username, user_id, _, permissions, roles, _ in _DEMO_ACCOUNTS
            )
        )
        os.replace(staged, path)
    except Exception:
        staged.unlink(missing_ok=True)
        raise


def _password_hash() -> str:
    password = getpass.getpass("Password (12–1024 UTF-8 bytes): ")
    confirmation = getpass.getpass("Confirm password: ")
    if password != confirmation:
        raise ValueError("Password confirmation differs")
    return PasswordSecurity().hash(password)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, help="Defaults to configured identity database")
    parser.add_argument(
        "--content-database", type=Path, help="Defaults to configured content database"
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init-db", help="Explicitly create an empty private identity database")
    commands.add_parser(
        "init-content-db", help="Explicitly create an empty private content database"
    )
    inventory = commands.add_parser(
        "inventory-corpus", help="Verify an approved-corpus manifest without writing"
    )
    ingest = commands.add_parser(
        "ingest-corpus", help="Atomically ingest a human-approved corpus manifest"
    )
    for command in (inventory, ingest):
        command.add_argument("--manifest", type=Path, required=True)
        command.add_argument("--archive", type=Path, required=True)
    evaluate = commands.add_parser(
        "evaluate-retrieval", help="Report retrieval metrics for an explicit JSONL item file"
    )
    evaluate.add_argument("--items", type=Path, required=True)
    evaluate.add_argument("--k", type=int, default=5)
    create = commands.add_parser("create-user", help="Create a user with an interactive password")
    create.add_argument("username")
    create.add_argument("--user-id", required=True)
    demo = commands.add_parser(
        "seed-demo", help="Provision local-only demo accounts and a one-click role switcher"
    )
    demo.add_argument(
        "--credentials",
        type=Path,
        default=_DEMO_CREDENTIALS,
        help="Private credentials file read only by the local dashboard",
    )
    access = commands.add_parser("set-access", help="Replace all explicit grants and roles")
    access.add_argument("user_id")
    for command in (create, access):
        command.add_argument("--permission", action="append", default=[], choices=list(Permission))
        command.add_argument("--role", action="append", default=[], choices=list(SystemRole))
    for name in ("disable-user", "enable-user", "reset-password"):
        commands.add_parser(name).add_argument("user_id")
    args = parser.parse_args(argv)
    try:
        if args.command in {
            "init-content-db",
            "inventory-corpus",
            "ingest-corpus",
            "evaluate-retrieval",
        }:
            content_path = (
                args.content_database
                if args.content_database is not None
                else Settings().content_database
            )
            content = ContentStore(content_path)
            if args.command == "init-content-db":
                content_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                content.initialize()
            elif args.command == "evaluate-retrieval":
                report = evaluate_retrieval(content, load_items(args.items), k=args.k)
                print(json.dumps(asdict(report), sort_keys=True))
                return 0
            else:
                manifest = read_manifest(args.manifest)
                if args.command == "inventory-corpus":
                    prepared = prepare_manifest(manifest, args.archive)
                    print(
                        f"Verified {len(prepared)} documents and "
                        f"{sum(len(item.chunks) for item in prepared)} chunks."
                    )
                    return 0
                require_approved(manifest)
                content_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                content.initialize()
                count = ingest_manifest(manifest, args.archive, content)
                print(f"Ingested {count} documents.")
                return 0
        else:
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
            elif args.command == "seed-demo":
                _seed_demo(store, args.credentials)
                print(
                    f"Created {len(_DEMO_ACCOUNTS)} local demo accounts. "
                    f"Credentials: {args.credentials}"
                )
                print("Local demo only. Do not use these accounts with real data.")
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
