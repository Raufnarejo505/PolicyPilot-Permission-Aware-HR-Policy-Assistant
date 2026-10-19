# PolicyPilot

Permission-aware policy RAG assistant. **Current milestone: foundation, authorization, authentication, and content metadata**, not a working RAG product.

## Quick start

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/) (validated with 0.11.25).

```sh
make setup
make check
make run
```

`GET http://127.0.0.1:8000/api/v1/health` returns `{"status":"ok"}`. This is **liveness**, not database/model readiness. Development API docs: `/docs`. Production disables docs. `make run` binds only to localhost.

Settings load from environment or optional `.env`. Tests create temporary SQLite databases; no real credentials, external database, models, Docker, or network access are required after dependency installation. First setup downloads Python packages. `uv.lock` freezes transitive dependencies; commit it with the code when initializing a repository.

## Authentication setup

```sh
uv run --frozen python -m policypilot.manage init-db
uv run --frozen python -m policypilot.manage create-user alice --user-id alice
make run
```

The password is prompted twice (12–1024 UTF-8 bytes); no default accounts or credentials exist. The database defaults to `data/identity.sqlite3`. Override with `POLICYPILOT_IDENTITY_DATABASE` or the operator command's `--database PATH` option before its subcommand. `POLICYPILOT_SESSION_TTL_SECONDS` defaults to `3600` (range 60–86400).

| Endpoint | Contract |
| --- | --- |
| `POST /api/v1/auth/login` | JSON `username` and `password`; returns `access_token`, `token_type`, `expires_in` |
| `GET /api/v1/me` | `Authorization: Bearer <access_token>`; current identity/grants/roles |
| `POST /api/v1/auth/logout` | Bearer token; revokes it, returns 204 |

Use development `/docs` to exercise the flow. Never put passwords/tokens in URLs, source files, shell history, or logs. Tokens are opaque, expire absolutely, and are stored only as digests on the server. Auth responses are `no-store`; no cookies are issued. Up to ten sessions per user are retained; an eleventh login evicts the oldest. Expired sessions are pruned during login attempts.

Trusted local access management:

```sh
uv run --frozen python -m policypilot.manage set-access alice --permission HR --role Admin
uv run --frozen python -m policypilot.manage disable-user alice
uv run --frozen python -m policypilot.manage enable-user alice
uv run --frozen python -m policypilot.manage reset-password alice
```

`set-access` **replaces** all explicit permissions and roles; omitted flags clear them. Public + Employee remain the authenticated baseline. Changes apply on the next authenticated request; disabling/enabling or resetting a password revokes existing sessions. These commands are trusted local administration, not public API routes.

Login has persisted fixed-minute budgets: five attempts per username, ten per caller address, thirty globally, including successes. Caller identity uses connection metadata, not application-trusted forwarding headers. `make run` disables Uvicorn proxy-header handling. Body limit: 4 KiB. Invalid credentials, disabled/deleted users, and store failures deny generically. A missing/uninitialized database denies authentication; health remains liveness-only.

**Local/demo scope:** external deployment still requires HTTPS, trusted ingress/proxy configuration, deadlines, memory-aware Argon2 concurrency limits, and database/backup protection. Shared addresses share rate limits, and distributed callers can exhaust the global budget. See [identity decisions](docs/decisions/0002-identity-and-sessions.md).

## Structure

```text
policypilot/app/
  main.py                   Application factory and liveness
  auth_api.py               Login, current identity, logout dependencies
  core/config.py            Validated, immutable settings
  core/security.py          Argon2id and opaque-token primitives
  core/http_security.py     Bounded auth bodies and no-store responses
  models/domain.py          Principal, content permissions, feature roles
  models/content.py         Validated immutable document/chunk metadata
  services/authorization.py Fail-closed checks and current-identity lookup
  services/authentication.py Credential verification and session lifecycle
  services/identity_store.py Transactional SQLite users/sessions/throttles
policypilot/manage.py        Trusted interactive operator commands
 tests/
  authorization/            Exhaustive matrix, negative and property tests
  test_api.py               ASGI endpoint tests
  test_config.py            Startup/settings validation
Documentation/              Original project specifications (unchanged)
docs/decisions/             Scope and deferred decisions
```

## Security contract

- Only successfully resolved server-side identities receive Public + Employee baseline grants. Valid empty assignments are not lookup failures.
- Manager, HR, and Finance permissions combine by union. Admin/Evaluator roles grant features only, never content.
- Empty, unknown, or malformed chunk labels deny access, even if other labels are valid.
- `AuthorizationService.resolve` reloads the store on every call. Missing identities, lookup errors, and identity mismatches return the same generic error.
- `Principal` is an internal value object, **not authentication**. Future routes must authenticate first, resolve current identity, and independently authorize retrieval, citations, and sources. Runtime validation protects malformed objects, not malicious code already executing in the server process.
- No query/source/admin placeholder routes, response cache, model tools, or paid API calls exist.

## Content metadata foundation

`DocumentMetadata` records explicit IDs, title, source URL, retrieval date, licence note, version, timezone-aware update time, and nonempty permissions. `Chunk` holds a document reference, explicit chunk ID, section, and untrusted text. Chunks inherit exactly the document's labels; no implicit Public label or chunk-level override exists. These internal constructors require canonical `Permission` members, not raw strings; malformed labels are never silently dropped.

Both models are immutable, redact their representations, and fail with content-free validation errors. `valid_document_metadata` and `valid_chunk` revalidate nested values at future store/index boundaries. A valid chunk is **not authorized evidence**: existing authorization checks remain mandatory before model input, citations, or source responses.

This adds no ingestion pipeline or content store. URL checks are structural only—not reachability validation, a safe-fetch/SSRF boundary, or licence approval. IDs are supplied, not generated. Corpus licensing, stable-ID generation, parser/chunking configuration, indexing, and model choices remain pending; tests use synthetic fixtures only.

## Validation

`make check` runs Ruff lint/format, strict application typing, and all tests with branch coverage (minimum 95%). Warnings fail tests. CI config repeats checks on Python 3.12/3.14 and builds distributions; remote CI requires publishing to GitHub.

```sh
uv run --frozen pytest tests/authorization -q
uv build --no-sources
```

## Next gate

The user confirmed authorization-test review and approved the identity design; implementation is recorded in `docs/decisions/0002-identity-and-sessions.md`. Corpus licensing must be approved before ingestion, model/provider/budget before paid calls, and evaluation precedence before dataset work. See `docs/decisions/0001-foundation.md` for deferred decisions. Passing these tests does not establish production security or completion of the full security evaluation.
