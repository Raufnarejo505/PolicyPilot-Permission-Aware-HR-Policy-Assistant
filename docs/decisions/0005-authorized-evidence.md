# 0005 — Corpus-independent authorized evidence

Date: 2026-10-19
Status: implemented internal boundary; retrieval and model integration remain pending

## Scope

Implement the authorized-evidence prerequisite from the Agent Implementation Plan §2, SRS FR-RET-02/04, and SDD permission-aware pipeline. This does not complete retrieval, generation, source reads, or the end-to-end security evaluation. No corpus, model, dataset, new dependency, endpoint, or configuration is introduced.

## Choices

- Extend the existing `AuthorizationService` with `authorize(user_id, candidates)`. Always resolve current server-store identity, even with no candidates. Preserve existing authorization semantics and tests: Public + Employee baseline, permission union, strict invalid-label denial, and no content access from system roles.
- Require a tuple of internal `Chunk` values. Independently revalidate each complete nested chunk before checking its document permissions; omit malformed or denied candidates without logging text, returning denial diagnostics, or exposing rejected counts. Preserve permitted order and untrusted text unchanged. A malformed container denies generically; failed identity resolution denies even for empty evidence.
- Return a frozen `AuthorizedEvidenceBundle` containing the resolved principal and permitted chunks, with all fields excluded from representations. Only the authorization service supplies the private construction token. This is an API/type discipline, not a sandbox against hostile Python code, explicit serialization, or traceback-local capture.
- Restricted-only and absent candidate inputs both yield empty evidence. This establishes internal payload equivalence only: no query response/status or abstention template is chosen here. Future generation must skip empty evidence.
- Bundles represent a point-in-time check, not authentication, a cached grant, or authoritative index freshness. Future callers must authenticate before supplying the server-derived user ID, filter retrieval by current grants, and route every candidate through this boundary before reranking/model input. Future citation/source operations must resolve current identity and authorize current content again; never reuse a bundle as their authority.
- No retrieval adapter, reranker, LLM consumer, persistence, cache, or public raw-content response exists yet. Typed downstream integration and authoritative source/index synchronization require their own implementation and tests. The synthetic downstream spy checks bundle payloads, not a working model pipeline.

## Validation and remaining gates

Tests were written first and failed because the bundle API did not exist. Synthetic tests cover all 32 assignment sets against all 31 nonempty permission sets, role/content separation, mixed results, malformed/nested/forged candidates, current-store reloads and failures, redacted output, empty evidence, construction guards, and immutability. Run full `make check` and Git checks; passing tests is not universal security certification.

Work is isolated on `feat/authorized-evidence`, based on the unmerged Git-workflow branch; neither branch is merged automatically. Corpus selection/licensing approval is still required before ingestion, model/provider/budget before paid calls, and evaluation precedence before dataset work. No frozen evaluation items or labels are accessed.
