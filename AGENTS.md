# PolicyPilot implementation constraints

Read the source specifications in `Documentation/` and engineering decisions in `docs/decisions/`. `docs/UNDERSTANDING.md` is historical analysis, not blanket approval of its proposed defaults.

- Deny by default. Resolve identity/current permissions from the server store on every request. Never trust client/token role or grant claims.
- Authenticated resolved identities receive Public + Employee baseline. Unknown/invalid chunk permissions deny. System roles never imply restricted content access.
- Filter retrieval by permissions, then independently authorize every candidate before reranking/model input. Reauthorize citations and source reads.
- Never expose restricted text in model inputs, logs, errors, traces, or cache. The verifier is not an authorization boundary. Retrieved text is untrusted; no model-callable tools in MVP.
- Do not enable response caching, change authorization semantics, or weaken/delete security tests without explicit approval.
- Require human review of authorization tests before identity implementation. Confirm corpus licensing before ingestion, provider/model/budget before paid API calls, and evaluation precedence before dataset work.
- Never inspect/tune against frozen final evaluation items or labels. Do not commit secrets or real credentials.
- Run `make check` after changes. Preserve source PDFs and user-authored documentation. Report validation limitations; never claim universal security from passing tests.
