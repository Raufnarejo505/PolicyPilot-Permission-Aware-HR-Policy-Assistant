# 0013 — Public landing page and read-only administrator console

Date: 2026-10-25
Status: implemented; write-side administration remains CLI-only

## User experience

`policypilot/ui/dashboard.py` now opens on a public landing page before any account
access. It presents the product promise, current corpus scale, four trust properties,
the authorization flow, and a plain security posture. Two role entry points lead to
separate sign-in experiences:

- **Employee portal** — asks policy questions, shows authorized citations, and lets the
  user open a re-authorized source passage.
- **Administrator portal** — verifies the `Admin` system role server-side, then shows a
  read-only account directory and corpus counts.

The visual system is self-contained CSS with no external fonts, scripts, images, or
network dependencies. It uses a teal/ink palette, a gradient hero, responsive cards,
accessible contrast, and explicit "local demo / not production certified" language.

## Administrator boundary

`GET /api/v1/admin/overview` is a new read-only endpoint:

- Requires an authenticated, currently resolved identity holding `SystemRole.ADMIN`.
- Returns account metadata (user ID, username, enabled state, explicit content grants,
  system roles, permission version) and document/chunk counts.
- Never returns password hashes, restricted passage text, or cached data.
- Returns `403` for non-admins and `503` for missing/corrupt stores.
- Uses `no-store` response headers.

Permission changes, account disabling, password resets, and ingestion are intentionally
**not** exposed as HTTP write routes. They remain trusted local CLI operations, in line
with `0002` and `0010`. This implements the "view users" part of SRS FR-UI-04 but not the
write/ingestion actions; those need a separate approved design.

## Validation

ASGI tests cover admin/non-admin access, corrupt stores, no-store responses, and the
Admin role not granting restricted content. Streamlit AppTest covers landing,
employee/admin login, role denial, admin overview rendering, query/citation/source
flows, logout, degraded identity, and isolated browser sessions. `make check` remains
the gate. A later headless-browser smoke test captured the landing page, employee query,
policy library, and admin console; see `docs/assets/` and decision 0014.
