# Run and test PolicyPilot locally

## Current capabilities

The backend supports authentication, current server-side permissions, offline DOCX ingestion from the approved English-policy manifest, permission-filtered lexical retrieval, authorized query/citation/source endpoints, an authorized policy library, and a local Streamlit Q&A dashboard. The UI starts with a public landing page with an optional one-click demo role switcher, then separate employee and administrator entry points. The administrator console is read-only: an access matrix, account metadata, and corpus counts, never password hashes or restricted text. FastAPI's interactive `/docs` remains an alternative. Answers are a deterministic extractive baseline; no LLM or paid API is used.

The corpus is the approved CC BY 4.0 **HR Corporate Data** English policy set. It is stored under ignored `data/`; the approved manifest and checksums are committed under `docs/corpus/`. See [decision 0010](docs/decisions/0010-approved-corpus-pipeline.md) and [decision 0011](docs/decisions/0011-extractive-answer-baseline.md).

### Test the implemented milestones

After `make setup`, run:

```sh
uv run --frozen pytest tests/test_docx.py tests/test_ingestion.py tests/test_corpus.py -q
uv run --frozen pytest tests/test_query.py tests/test_query_api.py -q
make check
```

No external service or model key is needed. Tests cover extraction, heading-aware chunking, manifest/hash verification, atomic ingestion, authorization, citations, source re-reads, and the dashboard. The corpus archive and local databases are not committed.

## 1. Requirements

- Python 3.12 or newer.
- [uv](https://docs.astral.sh/uv/).
- `make`.

Run all commands below from the project root. Initial setup downloads dependencies; the current application needs no model API keys or paid services.

## 2. First-time setup

```sh
make setup
uv run --frozen python -m policypilot.manage init-db
uv run --frozen python -m policypilot.manage create-user alice --user-id alice
uv run --frozen python -m policypilot.manage init-content-db
uv run --frozen python -m policypilot.manage ingest-corpus \
  --manifest docs/corpus/english-policy-manifest.json --archive data/archive.zip
```

Choose a local demo password when prompted twice: 12–1024 UTF-8 bytes. There are no default accounts or passwords. Skip creating `alice` if that account already exists. The ingest step requires `data/archive.zip` and refuses an unapproved or hash-mismatched manifest; skip it only if you do not need query data.

The identity database defaults to `data/identity.sqlite3`; the content database defaults to `data/content.sqlite3`. Keep them and their backups private; do not commit them. For custom locations, set `POLICYPILOT_IDENTITY_DATABASE` / `POLICYPILOT_CONTENT_DATABASE` consistently for both operator commands and the API.

### Optional one-click demo roles

To evaluate the permission difference without creating accounts by hand:

```sh
make demo-seed
```

This one-time command creates `demo.employee`, `demo.manager`, `demo.hr`,
`demo.finance`, and `demo.admin`, then writes owner-only credentials to
`.cache/demo-accounts.json` (ignored by Git). Reload the landing page after seeding; it detects the file and
offers one-click role buttons. Each button uses the normal login API; the UI does not
change grants. `seed-demo` refuses to overwrite existing demo credentials.

For a separate demo identity database:

```sh
uv run --frozen python -m policypilot.manage --database .cache/demo-identity.sqlite3 init-db
uv run --frozen python -m policypilot.manage --database .cache/demo-identity.sqlite3 seed-demo
POLICYPILOT_IDENTITY_DATABASE=.cache/demo-identity.sqlite3 make run
```

Demo credentials are local test material. Do not use them with real employee data. To hide
the role switcher, remove or rename `.cache/demo-accounts.json`; the normal sign-in forms
remain available.

## 3. Start the application

```sh
make run
```

Keep this first terminal open for the backend on localhost port 8000. In a **second terminal** at the project root:

```sh
make ui
```

- Dashboard: <http://127.0.0.1:8501>
- Alternative interactive API UI: <http://127.0.0.1:8000/docs>
- Liveness endpoint: <http://127.0.0.1:8000/api/v1/health>

The liveness endpoint returns `{"status":"ok"}`. It does not verify database or model readiness. Interactive docs are disabled in production mode.

Stop each process with **Ctrl+C** in its terminal. On subsequent runs, use `make run` and `make ui`; database initialization and account creation are unchanged and do not need repeating.

The dashboard defaults to API base `http://127.0.0.1:8000`. If needed, override it in the operator shell running the UI (use the backend's actual loopback port):

```sh
POLICYPILOT_UI_API_BASE_URL=http://127.0.0.1:8000 make ui
```

Only loopback HTTP bases are accepted (`127.0.0.1`, `localhost`, or `[::1]`). **Do not put this UI setting in `.env`**: the backend rejects unknown dotenv keys. Each operation uses a fresh HTTP client, a five-second timeout, no retry, no redirects, no proxy-environment use, and no persisted cookies. Errors use generic allowlisted messages rather than raw backend responses.

## 4. Use the dashboard

1. Open <http://127.0.0.1:8501>. The public landing page explains the flow. If demo credentials exist, choose **Enter as Employee**, **Manager**, **HR**, **Finance**, or **Administrator**; otherwise use **Sign in as employee** with `alice`.
2. On the employee workspace, check **Your access**: initially Public + Employee content permissions and no system roles. Roles control features, not restricted content grants.
3. Use **Ask a question** or one of the sample prompts. The API returns an extractive answer with up to three numbered authorized citations, or a fixed abstention message. Each citation can be expanded; **View authorized source** re-reads and re-authorizes it.
4. Open **Policy library** to browse the titles, section labels, and update dates available to the account. Passage text is not listed there. The library request is permission-filtered by the API.
5. Every authenticated Streamlit rerun fetches `/api/v1/me` from the server. After a trusted CLI access change, click **Refresh permissions**; no relogin is needed.
6. The **Administrator** role opens a read-only access matrix and account directory. It never adds restricted content. Use **Sign out** before leaving; a warning means server revocation could not be confirmed.

The token stays only in Python per-WebSocket session state, not localStorage, URLs, or caches. Closing or reloading the tab is **not server revocation**: the server token can live until its absolute TTL (default 3600 seconds). An invalid/expired/revoked session clears the local token and returns to login. A non-401 outage or other non-authentication failure hides the old identity and clears the previous answer while keeping the token so you can retry or sign out.

The sidebar connection badge is liveness only, not database/corpus/model readiness. Answers are deterministic extracts, not LLM output. The Admin console is read-only; it shows an access matrix, account metadata, and corpus counts, never password hashes, restricted passages, or a public write route. `.streamlit/config.toml` disables telemetry and file watching, enables CORS/XSRF protection, and sets `showErrorDetails = "none"`. Restart the UI after code changes because file watching is intentionally disabled.

### Alternative: Swagger `/docs` login

1. Open <http://127.0.0.1:8000/docs>.
2. Expand **POST `/api/v1/auth/login`** and click **Try it out**.
3. Enter `alice` as the username and the demo password you chose, then click **Execute**.
4. A successful response contains `access_token`, `token_type`, and `expires_in`.
5. Copy the `access_token` value.
6. Click **Authorize** near the top of the page and paste **only the token**, without a `Bearer ` prefix.
7. Confirm authorization and close the dialog.
8. Expand **GET `/api/v1/me`**, click **Try it out**, then **Execute**.

Expected initial result: user `alice`, Public + Employee content permissions, and no system roles.

Use local demo credentials only. Do not save passwords or tokens in this guide, screenshots, URLs, source files, shell commands/history, or logs. The interactive UI displays credentials/tokens during testing; do not share its request or response output.

## 5. Verify current permissions and revocation

Keep both servers running and open another terminal at the project root for trusted operator commands.

### Add HR access

```sh
uv run --frozen python -m policypilot.manage set-access alice --permission HR
```

To let `rauf` or another local account open the administrator console:

```sh
uv run --frozen python -m policypilot.manage set-access rauf --role Admin
```

The Admin role unlocks the read-only console only. Add content grants separately; roles never grant restricted content.

Click **Refresh permissions** in the dashboard (or execute `/api/v1/me` again in Swagger using the existing token), without logging in again. HR should now appear alongside Public + Employee.

To also demonstrate separate system roles:

```sh
uv run --frozen python -m policypilot.manage set-access alice --permission HR --role Admin
```

Refresh again: HR remains a content grant, while Admin appears separately as a system role and grants no additional restricted content.

**Important:** `set-access` replaces all explicit grants and roles. Omitted flags clear those assignments. The authenticated Public + Employee baseline remains.

### Disable the account

```sh
uv run --frozen python -m policypilot.manage disable-user alice
```

Refresh the dashboard: it should clear the token and return to login. In Swagger, execute `/api/v1/me` with the same token and expect **401**: disabling the account revokes its sessions.

### Re-enable the account

```sh
uv run --frozen python -m policypilot.manage enable-user alice
```

Sign in again in the dashboard (or log in and authorize with the new token in Swagger). Re-enabling does not restore revoked sessions.

### Test logout

In the dashboard, click **Sign out** and check the confirmation or unconfirmed-revocation warning. To verify server revocation directly in Swagger, with a newly issued token authorized:

1. Execute **POST `/api/v1/auth/logout`**. Expect **204**.
2. Execute **GET `/api/v1/me`** with that same token. Expect **401**.
3. Clear the token using the **Authorize** dialog when finished.

## 6. Run automated validation

```sh
make check
```

This runs Ruff lint/format checks, strict application typing, and the full test suite with branch coverage, including Streamlit AppTest and API-client tests. Read the terminal output for the current result; a previous passing run is not a guarantee that later changes pass. No actual browser validation has been performed yet; AppTest is not an end-to-end browser test.

For focused frontend validation:

```sh
uv run --frozen pytest tests/test_dashboard.py tests/test_ui_client.py -q
```

For focused corpus/extraction validation:

```sh
uv run --frozen pytest tests/test_docx.py tests/test_corpus.py -q
```

For focused query/source authorization validation:

```sh
uv run --frozen pytest tests/test_query.py tests/test_query_api.py -q
```

For focused content metadata tests:

```sh
uv run --frozen pytest tests/test_content.py -q
```

For focused evidence-boundary tests (including mixed allowed/denied candidates and permission changes):

```sh
uv run --frozen pytest tests/test_evidence.py -q
```

For focused authorization tests:

```sh
uv run --frozen pytest tests/authorization -q
```

Tests use temporary databases and synthetic content. Passing tests demonstrates only the tested behavior, not universal security or production readiness.

## 7. Troubleshooting

| Symptom | Check |
| --- | --- |
| Browser cannot connect | Keep `make run` (API port 8000) and `make ui` (dashboard port 8501) running. Check both terminals for startup errors or port conflicts. |
| Dashboard hides identity during an outage | Restore the API, then refresh; the token is retained for retry or sign-out, not used to display stale grants. |
| Dashboard rejects API configuration | Use a loopback HTTP base via the UI operator shell, not `.env`; check `POLICYPILOT_UI_API_BASE_URL`. |
| Logout warns revocation is unconfirmed | The local token is cleared, but the server session may remain valid until TTL; closing/reloading the tab does not revoke it. |
| Health works but login fails | Health is liveness-only. Confirm the database was initialized, the account exists and is enabled, and operator commands/API use the same database path. |
| `/me` returns 401 | Log in and authorize with a current token. Tokens expire, and logout, account enable/disable changes, or password resets revoke sessions. |
| Login returns 429 | Wait at least 60 seconds before retrying. Login attempts, including successes, have username/caller/global budgets. All UI clients share the UI server's backend caller-address budget; do not trust forwarded browser IPs. |
| Login returns 422 | Submit only `username` and `password` using the documented JSON shape. |
| Query returns 503 | The content database is missing/uninitialized or unavailable. Run `init-content-db` and `ingest-corpus` with the approved manifest. |
| Query always abstains | Check the signed-in account's effective permissions and that the corpus was ingested. Abstention is expected when no authorized evidence matches. |
| Administrator portal says access required | The account lacks the `Admin` system role. Grant it with `set-access <user> --role Admin`; re-check permissions or sign in again. The Admin role does not add content permissions. |
| Question returns 422 | Keep the body to JSON `{"question": "..."}` with 1–2000 characters; private bodies are capped at 4 KiB. |
| `/docs` is unavailable | Check whether production mode is enabled; production disables interactive docs. |

## 8. Monitor Git progress

```sh
git --no-optional-locks status --short --branch
git --no-pager log --oneline --graph --decorate --all
```

Dashboard work is on `feat/frontend-dashboard`, based on prior unmerged work; no automatic commits or merges. See [decision 0006](docs/decisions/0006-local-dashboard.md).

Install this clone's Git safeguards with `make hooks` and run them on tracked files with `make git-check`. See [CONTRIBUTING.md](CONTRIBUTING.md) for branch, commit, review, and secret-handling guidance. No remote is configured automatically.

## 9. Scope and deployment limits

This guide is for a local development/demo setup, not public hosting. External deployment still needs HTTPS, trusted ingress configuration, concurrency/abuse controls, and database/backup protection.

The approved English corpus is ingested into a local SQLite lexical store. Answers are deterministic extracts with authorized citations. The landing page, one-click demo mode, policy library, and read-only Admin access matrix are implemented; write-side account administration and ingestion remain CLI-only. LLM generation, cross-encoder reranking, the evidence verifier, vector/hybrid retrieval, and a committed retrieval benchmark remain incomplete. Model/provider/budget approval is required before any paid call, and evaluation labels remain human-gated. A headless-browser smoke test of the landing page, employee login, query, policy library, and admin console was performed for the screenshots in `docs/assets/`; it is not a substitute for an accessibility audit or production pen-test.
