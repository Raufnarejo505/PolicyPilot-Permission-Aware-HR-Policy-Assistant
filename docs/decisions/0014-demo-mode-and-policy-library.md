# 0014 — Local demo mode, policy library, and product-first landing page

Date: 2026-10-25
Status: implemented; local/demo only

## Context

The first landing page explained the security model well but made the differentiator
hard to experience. A reviewer had to invent accounts, remember which grants to apply,
and ask several questions before seeing that permissions change retrieval. The employee
workspace also had no way to browse the policies already available to an account.

This decision adds a **local-only demo path** and an authorized policy library. It does
not change the authorization model, add a model provider, or expose a public write route.

## Demo mode

`python -m policypilot.manage seed-demo` creates five identities in the configured
identity database:

| Label | Username | Explicit access |
| --- | --- | --- |
| Employee | `demo.employee` | none (Public + Employee baseline) |
| Manager | `demo.manager` | Manager |
| HR | `demo.hr` | HR |
| Finance | `demo.finance` | Finance |
| Administrator | `demo.admin` | Admin role only |

The command generates one random password, hashes it once with Argon2id, and creates all
five users in one transaction. It writes `.cache/demo-accounts.json` with mode `0600`;
the file is gitignored and contains the demo password so the local dashboard can offer
one-click login. The command refuses to overwrite an existing credentials file or
partially existing demo accounts.

The dashboard reads the file only when `POLICYPILOT_UI_DEMO_CREDENTIALS` points to it
(default `.cache/demo-accounts.json`). Each button calls the normal login API; the UI
stores the returned opaque token and never constructs grants or roles. The admin demo
account has the Admin role but no restricted content, which is intentional.

**Boundary:** this is an operator-controlled local demo convenience, not an
authentication bypass. A remote deployment must not ship seeded demo credentials or a
world-readable credentials file.

## Authorized policy library

`GET /api/v1/policies` resolves the current identity, computes effective permissions, and
lists only documents readable through those grants. For each document it returns:

- document ID, title, source URL, version, and update time;
- section labels and indexed passage count;
- the matched permission labels that made the document visible.

It never returns passage text. Section labels are exposed only after the document passes
the same permission filter used by retrieval. This endpoint is private (`no-store`) and
returns `503` generically when the store is unavailable or corrupt.

## Landing page changes

The public page now leads with the product outcome, includes a safe public-policy answer
preview, and adds a three-role contrast section using illustrative behavior only. It does
not display restricted policy text. The page also removes implementation-focused metrics
such as "paid model calls" in favor of policy count, access levels, and citation behavior.

The hero and feature copy are still explicitly local/demo. The page does not claim
production certification or universal leakage prevention.

## Validation

Tests cover:

- demo credential parsing, one-time provisioning, owner-only permissions, and atomic
  account creation;
- one-click login through the normal API and admin destination routing;
- malformed credentials hiding the demo panel;
- policy-list permission filtering, no-store responses, metadata-only output, and
  corrupt/missing store failures;
- the employee library UI, answer card, sample prompts, and failure states.

`make check` remains the gate.
