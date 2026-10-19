# Run and test PolicyPilot locally

## Current capabilities

The backend supports authentication, current server-side permissions, and validated content metadata. You can test the available API through FastAPI's interactive browser UI.

The chat UI, corpus ingestion, search, and cited answer generation are not implemented yet. Content metadata currently has automated tests, not browser endpoints.

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
```

Choose a local demo password when prompted twice: 12–1024 UTF-8 bytes. There are no default accounts or passwords. Skip creating `alice` if that account already exists.

The identity database defaults to `data/identity.sqlite3`. Keep it and its backups private; do not commit them. For a custom location, set `POLICYPILOT_IDENTITY_DATABASE` consistently for both operator commands and the API.

## 3. Start the application

```sh
make run
```

Keep this terminal open. The development server binds only to localhost.

- Interactive API UI: <http://127.0.0.1:8000/docs>
- Liveness endpoint: <http://127.0.0.1:8000/api/v1/health>

The liveness endpoint returns `{"status":"ok"}`. It does not verify database or model readiness. Interactive docs are disabled in production mode.

Stop the server with **Ctrl+C**. On subsequent runs, use `make run`; you do not need to repeat account creation.

## 4. Test login through the browser

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

Keep the server running and open a second terminal at the project root.

### Add HR access

```sh
uv run --frozen python -m policypilot.manage set-access alice --permission HR
```

Execute `/api/v1/me` again using the existing token, without logging in again. HR should now appear alongside Public + Employee.

**Important:** `set-access` replaces all explicit grants and roles. Omitted flags clear those assignments. The authenticated Public + Employee baseline remains.

### Disable the account

```sh
uv run --frozen python -m policypilot.manage disable-user alice
```

Execute `/api/v1/me` again with the same token. Expect **401**: disabling the account revokes its sessions.

### Re-enable the account

```sh
uv run --frozen python -m policypilot.manage enable-user alice
```

Log in again and authorize with the new token. Re-enabling does not restore revoked sessions.

### Test logout

With a newly issued token authorized:

1. Execute **POST `/api/v1/auth/logout`**. Expect **204**.
2. Execute **GET `/api/v1/me`** with that same token. Expect **401**.
3. Clear the token using the **Authorize** dialog when finished.

## 6. Run automated validation

```sh
make check
```

This runs Ruff lint/format checks, strict application typing, and the full test suite with branch coverage. Read the terminal output for the current result; a previous passing run is not a guarantee that later changes pass.

For focused content metadata tests:

```sh
uv run --frozen pytest tests/test_content.py -q
```

For focused authorization tests:

```sh
uv run --frozen pytest tests/authorization -q
```

Tests use temporary databases and synthetic content. Passing tests demonstrates only the tested behavior, not universal security or production readiness.

## 7. Troubleshooting

| Symptom | Check |
| --- | --- |
| Browser cannot connect | Keep `make run` running and use port 8000. Check the server terminal for startup errors or a port conflict. |
| Health works but login fails | Health is liveness-only. Confirm the database was initialized, the account exists and is enabled, and operator commands/API use the same database path. |
| `/me` returns 401 | Log in and authorize with a current token. Tokens expire, and logout, account enable/disable changes, or password resets revoke sessions. |
| Login returns 429 | Wait at least 60 seconds before retrying. Login attempts, including successes, have username/caller/global budgets. |
| Login returns 422 | Submit only `username` and `password` using the documented JSON shape. |
| `/docs` is unavailable | Check whether production mode is enabled; production disables interactive docs. |

## 8. Monitor Git progress

```sh
git --no-optional-locks status --short --branch
git --no-pager log --oneline --graph --decorate --all
```

Install this clone's Git safeguards with `make hooks` and run them on tracked files with `make git-check`. See [CONTRIBUTING.md](CONTRIBUTING.md) for branch, commit, review, and secret-handling guidance. No remote is configured automatically.

## 9. Scope and deployment limits

This guide is for a local development/demo setup, not public hosting. External deployment still needs HTTPS, trusted ingress configuration, concurrency/abuse controls, and database/backup protection.

Corpus selection and licensing approval are required before ingestion. Model/provider/budget approval is required before paid calls. No end-to-end RAG or chat testing is available at this milestone.
