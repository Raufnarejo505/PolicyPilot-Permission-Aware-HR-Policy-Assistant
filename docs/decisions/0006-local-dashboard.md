# 0006 — Local Streamlit dashboard

Date: 2026-10-19
Status: implemented local/demo dashboard; question flow and landing/admin portals added by 0011/0013; actual browser validation pending

## Scope and rationale

Follow the source specifications' Streamlit-to-FastAPI architecture, exposing only real implemented services. `policypilot/ui` provides login, current identity/permissions, refresh after trusted operator changes, logout, and API liveness. This is not the complete question/answer UI described in the specifications: the question area is disabled/read-only and the roadmap explicitly leaves RAG, ingestion, search, chat, generated answers, citations, and evaluation views unimplemented.

## Decisions

- Use Streamlit, as specified by the architecture, pinned to the resolved stable `1.66.0` release. Move existing `httpx==0.28.1` from development to runtime dependencies for the stateless API adapter. No authorization semantics, database setup, or trusted `init-db`/`create-user` workflow change.
- Run `make run` for the backend at `http://127.0.0.1:8000`, then `make ui` in a second terminal for Streamlit at `http://127.0.0.1:8501`. Keep development Swagger `/docs` as an alternative. The health badge is liveness, not database, corpus, or model readiness.
- Authenticate against the real login API. On every authenticated rerun, fetch `/api/v1/me`; never treat UI state or token claims as authority. **Refresh permissions** checks trusted CLI `set-access` changes without relogin. Display content grants separately from system roles; roles do not imply restricted content access. Backend authorization remains authoritative.
- Keep the opaque token only in Python per-WebSocket session state, not localStorage, URLs, or caches. Clear the password field on submit, including failure. Do not cache responses or identities, display credentials/tokens, or add model-callable tools.
- An unauthorized session response (401/403) clears the local token and returns to login. Non-authentication failures, including non-401 outages, hide identity rather than show stale grants; retain the token for explicit retry or sign-out.
- Logout always clears the local token and attempts server revocation. Warn when revocation is unconfirmed: the server session may remain active until absolute expiry. Closing/reloading the tab or losing WebSocket state is not server revocation. The existing default server TTL is 3600 seconds, without sliding renewal.
- Use a fresh HTTP client per operation, loopback-only HTTP API bases, a five-second timeout, no retries, no redirect following, no proxy-environment use (`trust_env=False`), and no cookie persistence between operations. Validate response shapes and map failures to generic allowlisted messages; do not expose raw response bodies, configuration values, or transport exceptions.
- Default the API base to `http://127.0.0.1:8000`. `POLICYPILOT_UI_API_BASE_URL` is an operator shell environment override for the UI only, **not a `.env` key**: backend dotenv validation rejects unknown keys. Accepted hosts are `127.0.0.1`, `localhost`, and `[::1]`; no remote API base is supported.
- Add three individually reviewed hashed false positives to the secret baseline for `tests/test_ui_client.py`: the synthetic expected JSON password (line 113), a deliberately rejected localhost URL credential (line 166), and a randomly generated integration-password prefix (line 505). Preserve all security tests and existing baseline entries; no detector is disabled. Human branch review must include these additions.
- `.streamlit/config.toml` disables usage telemetry and file watching (`fileWatcherType = "none"`), keeps CORS and XSRF protection enabled, and sets `showErrorDetails = "none"`. These are local hardening measures, not production certification.

## Validation and operational limits

`make check` includes lint/format, strict application typing, and the full branch-coverage suite, including frontend Streamlit AppTest and API-client tests. Focused command:

```sh
uv run --frozen pytest tests/test_dashboard.py tests/test_ui_client.py -q
```

Tests cover UI state transitions, fresh identity display, grant/role separation, password clearing, invalid sessions, outages, logout failures, and adapter request/validation behavior. AppTest uses fake API responses; client tests also exercise the real FastAPI app in-process. No actual browser validation has been performed yet. Consult current command output rather than a fixed test total; passing tests establish only tested behavior, not universal security or production readiness.

This dashboard is local/demo only. All browser clients call the backend through the UI server and share its backend caller-address login throttle; do not trust forwarded browser IPs to split that budget. Public deployment needs separate HTTPS, trusted ingress, concurrency/abuse controls, and credential/database/backup protection review.

Work is on `feat/frontend-dashboard`, based on prior unmerged work (including the authorized-evidence milestone); no automatic commits or merges. Preserve source PDFs, existing user documentation, environment files, and frozen evaluation material. Corpus/licensing approval remains required before ingestion, provider/model/budget approval before paid calls, and evaluation precedence before dataset work. Response caching remains disabled.
