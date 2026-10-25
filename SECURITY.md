# Security policy

PolicyPilot is a **local/demo** permission-aware RAG application. It is not certified for
production HR data. This document states the trust boundary and how to report a problem;
it is not a claim of universal security.

## Supported scope

| Scope | Status |
| --- | --- |
| Local development and demo use | Supported |
| Public production deployment with real employee data | Not supported without a deployment review |
| Third-party model/provider calls | Disabled in this milestone |
| Admin write APIs or self-service registration | Not implemented |

## Security properties we test

- Identity and permissions are resolved from the server-side store on every request.
- Client-supplied roles, grants, and token claims are never trusted.
- Retrieval is filtered by effective permissions before ranking.
- Every candidate is independently authorized before it can become evidence.
- Citations and source reads are re-authorized against current identity and content.
- Unknown, malformed, or empty permission labels deny rather than normalize.
- Public + Employee is an authenticated baseline, not a document default.
- Admin and Evaluator are feature roles; they never grant restricted content.
- Authentication and query failures return generic messages without restricted text.
- Private responses use `no-store`; no response cache exists.
- Login attempts have persisted per-username, per-caller, and global budgets.
- Passwords use Argon2id; session tokens are opaque and stored only as digests.

## Important limitations

- No HTTPS termination, trusted proxy configuration, distributed rate limiting,
  memory-aware Argon2 concurrency limit, or backup strategy is included. These are
  required before remote deployment.
- Shared caller addresses share login budgets; a distributed attacker can exhaust the
  global budget. This is a denial-of-service tradeoff, not an authentication bypass.
- The local Argon2 defaults are intentionally simple for a demo. Review memory and
  concurrency limits for the target host.
- The policy corpus is approved synthetic/CC BY 4.0 HR content. Do not ingest real
  employee data without a privacy and licensing review.
- The extractive answer path limits invention but does not prove that every policy is
  current, complete, or legally correct.
- Streamlit and FastAPI are local-demo components here; a production deployment needs an
  independent threat model, dependency review, and monitoring.
- Tests demonstrate covered behavior; passing tests do not prove the absence of defects.

## Reporting a vulnerability

Do **not** open a public issue containing a suspected vulnerability, credentials, policy
text, or personal data.

Use the repository's private security advisory / maintainer contact channel. Include:

- affected version or commit;
- a minimal reproduction using synthetic data;
- impact and whether restricted content, identity, or session state is involved;
- any suggested fix.

Do not include real employee data, access tokens, passwords, or restricted policy text in
the report. If a secret was exposed, rotate it before sharing details.

## Operator hygiene

- Never commit `.env`, databases, credentials, archives, or demo credential files.
- Keep the demo credentials file owner-only (`0600`) and delete it when finished.
- Use a separate identity database for demos when possible.
- Run `make check` and `make git-check` before publishing changes.
- Review the architecture decisions in `docs/decisions/` before changing authorization,
  caching, ingestion, or provider behavior.
