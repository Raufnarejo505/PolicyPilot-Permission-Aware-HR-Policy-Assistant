# 0001 — Initial foundation

Date: 2026-10-19
Status: implemented foundation; authorization review subsequently approved (see 0002)

## Scope

The user requested basic implementation, a strong structure, and testing. Implement package/configuration, a liveness-only API, independently testable authorization, locked dependencies, quality commands, and CI configuration. Preserve the source PDFs and existing understanding report as historical records.

## Implemented choices

- Python >=3.12; setuptools package; uv lockfile and project-local virtual environment/cache.
- FastAPI application factory and provisional `/api/v1/health` route.
- Typed immutable server-side principal; strict content labels; baseline Public + Employee only for validated resolved identities. Role/content separation and per-call store resolution.
- Environment validation, caching permanently disabled in this milestone, production docs disabled.
- Exhaustive role/grant/label matrix, malformed-object cases, Hypothesis properties, store revocation/failure checks, configuration and ASGI tests.
- Ruff, strict mypy on application code, warnings-as-errors tests, branch coverage threshold, and GitHub Actions configuration.

## Explicit deferrals

No authentication scheme/session lifetime, database, corpus, model IDs, deployment stack, generation parameters, abstention template/status, evaluation override C1–C7, or numeric quality/cost targets are decided here. They do not block this milestone. No fake retrieval or answers, empty UI, or nonfunctional Compose services are added. Compose and Streamlit follow real services, rather than pretending the full stack is runnable.

The complete secret-scanning/pre-commit toolchain and full security/injection harness remain future work; Ruff security rules are not substitutes. No claims of full Phase 0 or product completion.

## Required next gate

Human review of authorization tests was required before Phase 2 identity work and has now been confirmed by the user; see `0002-identity-and-sessions.md` for the approved implementation. Subsequent API handlers must not accept client-provided grants, and must resolve current server-store permissions for each request. Full SEC-01…10 evaluation remains outstanding. Frozen final evaluation data must remain isolated.
