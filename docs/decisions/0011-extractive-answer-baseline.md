# 0011 — Extractive answer baseline; LLM generation deferred

Date: 2026-10-25
Status: approved by project owner for the current milestone

## Decision

The project owner chose to keep the deterministic extractive answer baseline for now
and defer any LLM provider, model ID, and paid budget. No paid API call is made by the
application, tests, or CI. This avoids inventing a provider/model or spending money
without explicit approval.

`ExtractiveAnswerGenerator` selects the top authorized chunks, emits their first
sentence with numbered citation markers, and never invents text. Empty evidence
returns one fixed abstention message without calling any model. Every query and source
read still resolves the current server identity and re-authorizes current content.

## Boundaries

- This is not the cited LLM generation stage in the SRS/SDD. It is a working,
  offline baseline that exercises the same authorization and citation paths.
- Any future LLM adapter must be added behind the `AnswerGenerator` protocol, require
  explicit provider/model/budget configuration, never receive unauthorized text, and
  keep the verifier outside the authorization boundary.
- Response caching remains disabled. No model-callable tools are added.
- The UI labels the baseline clearly and does not claim that answers are
  LLM-generated.

## Validation

Unit and ASGI tests cover authorized/denied queries, abstention, citation re-reads,
permission changes, source authorization, no-store responses, malformed payloads, and
secret-free errors. Passing tests establishes only the tested behavior.
