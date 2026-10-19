# PolicyPilot — Understanding Report

**Author:** coding agent (lead engineer), from read-only review  
**Date:** 17 October 2026  
**Sources read in full:**

| Requested name | Found as | Readable |
| --- | --- | --- |
| `docs/PolicyPilot_Revised_Project_Proposal.pdf` | `Documentation/PolicyPilot_Revised_Project_Proposal.pdf` | Yes |
| `docs/PolicyPilot_SRS_v0_1.pdf` | `Documentation/PolicyPilot_SRS_v0.1.pdf` | Yes |
| `docs/PolicyPilot_Software_Design_Document.pdf` | `Documentation/PolicyPilot_Software_Design_Document.pdf` | Yes |
| `docs/PolicyPilot_Evaluation_Plan_and_Labeling_Guidelines` | `Documentation/PolicyPilot — Evaluation Plan and Labeling Guidelines.pdf` | Yes |
| `docs/PolicyPilot_Agent_Implementation_Plan` | `Documentation/PolicyPilot — Agent Implementation Plan.pdf` | Yes |

The five specified documents exist under `Documentation/`, not `docs/`. Contents below are taken from those PDFs only. Nothing was guessed for a missing file.

**This session:** read-and-understand only. No application code, no installs, no paid APIs.

---

## a) Project in my own words (123 words)

PolicyPilot is a demo RAG assistant over a public handbook given synthetic visibility labels. An employee logs in, asks a policy question, and receives a cited answer, a partial answer, or a refusal. It is for a reviewer who needs evidence that unauthorized text never reaches the model, citations, logs, or cache, and that quality claims are measured.

Done means: server-side deny-by-default authorization with automated negative tests (SEC-01..10); a frozen labelled benchmark with a held-out split the agent never tunes on; recorded comparisons of chunking, BM25, vector, hybrid, and reranking; a verifier measured on cached drafts; calibrated abstention; injection tests with counts; Docker/CI another engineer can reproduce; and a report that states n, intervals, failures, and that passing tests is not production certification.

---

## b) Non-negotiable invariants (as I will enforce them)

Copied from the Agent Plan §1.1 / SDD §1.5, restated as enforcement rules I will not weaken:

1. **Authorization is server-side only.** Client-supplied roles, permissions, or “I am HR” fields are ignored. Effective grants are loaded from the server store on every request. A token may carry `user_id` and `permission_version` only; grants are not trusted from the token payload.
2. **No unauthorized passage may leave the authorization boundary.** Unauthorized text must not reach the generation model, the verification model, logs, traces, errors, citations, the source endpoint, or any cache entry visible to another permission set.
3. **Retrieved text is untrusted data.** It is evidence, not instructions. It cannot change system policy, grant tools, fetch URLs, or authorize actions. The MVP exposes no model-callable tools.
4. **Deny by default.** Missing identity, lookup failure, empty grants after a failed resolution, malformed grants, or an unknown permission label is deny. Fail closed before retrieval and before any LLM call.
5. **The verifier is not a security boundary.** It may drop or qualify claims; it must never be the mechanism that decides whether a chunk was readable. Authorization runs independently, earlier, and again on citations/sources.
6. **System roles grant no content access.** Admin and Evaluator unlock admin/eval APIs and UI pages only. Restricted documents remain inaccessible unless the same user also holds the matching content permission.

Supporting SRS IDs: FR-AUTH-01..09, FR-RET-01/02, FR-GEN-02/03, FR-AUTH-06, FR-OBS-02.

---

## c) Architecture summary

**Trust boundary:** FastAPI backend. Streamlit is not trusted. Weaviate filters are not trusted alone.

### Query path (stage by stage)

1. Browser ? Streamlit ? `POST /query` with question only (no client role).
2. Validate request shape and length; authenticate session; **deny** on invalid/expired session.
3. Resolve principal from the user store; load `content_permissions` and `system_roles`; **deny** if resolution fails (SEC-08).
4. Normalize **effective grants** = Public + Employee (authenticated baseline) + explicitly assigned categories (union). System roles are not added to grants.
5. Embed the query; **hybrid search with a permission metadata filter inside the retrieval query** (authorization place 1; FR-RET-01).
6. Fuse BM25 + vector candidates; **re-check every candidate with `can_read` before context assembly** (authorization place 2; FR-RET-02). Construct `AuthorizedEvidenceBundle` only here.
7. Cross-encoder rerank of permitted candidates; select frozen top-N.
8. If the bundle is empty: return the fixed abstention/escalation template **without calling the LLM**.
9. Otherwise generate a cited answer from the bundle only; retrieved passages are delimited as untrusted; citation IDs must come from the bundle.
10. Verifier (quality only): label claims supported / contradicted / insufficient; keep, qualify, remove, or abstain. Never used to admit extra chunks.
11. **Re-check before returning a citation or `GET /sources/{chunk_id}`** (authorization place 3). Unauthorized or missing ? same not-found body (FR-AUTH-09 family).
12. Sanitize trace/metrics (no restricted text). Optional cache, if ever enabled, keyed by permission set **and** `permission_version`, query, index version, prompt/model (authorization place 4 / cache isolation; FR-GEN-03).

### Ingestion path (stage by stage)

1. Admin starts a run (`POST /admin/ingest`) — **system-role gate**, not content access.
2. Load a **frozen local snapshot** (no live scrape at query time); record URL, retrieval date, licence note, version, SHA-256.
3. Parse markdown; keep heading tree and section paths; record failures.
4. Heading-aware chunk; stable IDs; character offsets in source (needed for span?chunk mapping).
5. Assign synthetic visibility (document-level, seeded); plant canaries in a recorded separate step.
6. **Reject chunks with missing/invalid permission labels** (FR-ING-05); do not index them.
7. Embed (cache by text hash + model ID); upsert to Weaviate with BM25 text + `allowed_permissions`.
8. On update/delete, replace/remove all prior chunks (FR-ING-06).
9. Mark run successful only after permission metadata and citation-link checks (FR-ING-07).

Authorization is also enforced in log redaction (FR-OBS-02) and in evaluation retrieval metrics (authorized evidence only).

---

## d) Phase map

Hours are the plan’s planning figures, not a promise.

| Phase | Goal | Exit gate | [HUMAN] tasks |
| --- | --- | --- | --- |
| **0 Bootstrap (~4h)** | Repo, pinned deps, settings, Compose, CI, decision-log template, `AGENTS.md` | New clone starts with documented commands (NFR-REP-01); `make check` on empty suite | Review scaffold; no labelled HUMAN task IDs, but you merge T0.5 |
| **1 Authorization core (~5h)** | Domain + `AuthorizationService` + exhaustive tests | Auth tests green; **you review line by line** | Review of T1.1–T1.3 (not optional) |
| **2 Identity and API skeleton (~6h)** | Users, argon2, JWT, `/login` `/me` `/health`, personas seed | Login-then-`/me` for every persona | Seed passwords via env; review T2.5 forged-role tests |
| **3 Corpus and ingestion (~14h)** | Snapshot loader, parser, chunker, labels, canaries, Weaviate adapter, admin ingest | Full ingest; counts match visibility manifest | **T3.0** pick corpus, verify licence, approve label rule |
| **4 Retrieval (~10h)** | Filtered hybrid search, re-check, reranker, metrics, span mapping | Baseline retrieval metrics from one command | **T4.6** dataset authoring/labelling (pilot 10 items, ?) |
| **5 Generation and query API (~10h)** | LLM client + budget guard, prompts, `AuthorizedEvidenceBundle` answer path, `/query` `/sources` | E2E cited RAG; spy proves only authorized text hits the LLM | Confirm model string/budget before any paid call |
| **6 Verification and abstention (~10h)** | Claim verifier, response policy, thresholds, shared template, cached drafts | Dev-set verifier before/after table | **T6.3** calibrate thresholds on **dev only** |
| **7 Security and injection (~10h)** | SEC-01..10 harness (?100 cases), R2 pair test, spy, injection YAML, optional cache, log scan | One-command suite; any leak blocks merge and forces full re-run | Approve injection catalogue goals; do not weaken tests |
| **8 Eval, observability, UI, delivery (~14h)** | Metrics+stats, `--final` guard, OTel, Streamlit, README, release gates | SDD Appendix B checklist ticked | Final-split labelling/adjudication; OD-05 targets after baseline; you reproduce from README |

If time is short, cut in this order: extra UI polish, cache (T7.5), concurrency measurements, extra chunking variants. **Never cut Phases 1, 5 (source authorization), 7, or final-split labelling.**

---

## e) Conflicts, ambiguities, and gaps

I treated the Eval Plan’s §2 “Corrections” (C1–C7) as a **known dispute log**, not as already-approved SRS/SDD text. Each item below: confirm or reject the candidate, then other issues.

### Candidate 1 — Relevance labels: chunk IDs vs evidence spans — **CONFIRMED conflict**

- **SRS** FR-EVAL-01 and logical DB: `relevant_chunk_ids[]`.
- **SDD** §7.1 Evaluation item: same field.
- **Eval Plan C1 / §5.1 / Appendix A:** gold unit is `doc_id + section path + exact quote + offsets`; chunk hit = ?50% of unit characters after whitespace normalisation. `relevant_chunk_ids` is **derived per configuration**.
- **Agent Plan T3.3, T4.5:** already implements span?chunk mapping.

**Why it matters:** chunking is an experimental factor (Week 2 / E1). Gold chunk IDs from one chunker invalidate Recall@5 for every other chunker. That would make RQ1/RQ2 uninterpretable.

**Recommendation:** Eval Plan C1 is the measurement spec. Do not store human labels as chunk IDs. Keep `relevant_chunk_ids` only as a run artifact. Update SRS/SDD schema when you freeze v0.2. I will not invent chunk-ID gold labels.

### Candidate 2 — Benchmark size 60 vs 80 and the split — **CONFIRMED conflict (numbers), split policy aligned**

- **Proposal §7, SRS FR-EVAL-01, SDD §11.1:** “approximately 60”.
- **Eval Plan C3 / §4.3:** **target 80 (50 dev / 30 final)**; **floor 60 (40 / 20)**; final results “indicative only”.
- **Agent Plan:** follows the Eval Plan (schema validation, `--final` guard).
- Split itself is not in conflict: Proposal already wants a held-out set; SRS FR-EVAL-06 (Should); SDD OD-12; Eval Plan §4.4 stratified ~2/3–1/3 with a hashed manifest.

**Why it matters:** 20 final items produce intervals too wide to support strong claims (Eval Plan’s 18/20 example). Headline n must be stated once.

**Recommendation:** Treat **80 as the authoring target and 60 as the floor** you will disclose if time runs out. I will not pick a number. You update FR-EVAL-01 when you freeze the SRS, or explicitly accept “about 60” as the floor only.

### Candidate 3 — Employee/Public baseline vs `can_read` — **CONFIRMED implementation hazard; documents are compatible if normalisation is mandatory**

- **SRS FR-AUTH-03:** Public applies to **all**; Employee applies to **all authenticated** users; effective permissions = union of assigned content permissions **plus** those baselines.
- **Proposal / SRS / SDD matrices:** Employee/Manager/HR/Finance rows all Allow Public and Employee.
- **Eval personas:** every persona lists Public and Employee explicitly, including P-ADMIN / P-EVAL with **no extra** content permission.
- **SDD §6.2 `can_read`:** empty grants **or** empty chunk permissions ? False; then intersection. Comment: “normalize the baseline grants consistently in one authorization service.”
- **Agent T1.2:** `effective_grants()` = Public + Employee baseline + assigned; `can_read` on the **effective** set.

**Why it matters:** If `can_read` is called on **raw assigned** permissions, a user stored as `[]` or as `{Manager}` would fail FR-AUTH-03 (no Public/Employee). Conversely, putting Public/Employee on every *document* instead of on the *user* would break deny-by-default for restricted-only docs.

**Recommendation:** One function, `effective_grants(principal)`, is the only place baselines are added. `can_read` never adds them. Empty **assigned** list after successful auth still yields `{Public, Employee}`. Empty **chunk** `allowed_permissions` stays invalid (FR-ING-05) and is never readable. Unauthenticated callers never reach `can_read` on content APIs.

**Residual ambiguity (not a matrix bug):** “Public applies to all” vs every content API requiring login. I will treat Public as a **content label + authenticated baseline grant**, not as anonymous handbook browsing, unless you say otherwise.

### Candidate 4 — How FR-AUTH-09 is tested — **CONFIRMED underspecified in SRS/SDD; specified in Eval Plan**

- **SRS FR-AUTH-09:** response whose only relevant evidence is restricted shall be **indistinguishable** from no evidence. No procedure.
- **SDD §6.3:** “paired tests… avoid leaking titles/counts/snippets.” Still not mechanical.
- **Eval Plan C6, C7, §4.3, §8.2, §11.10, Example 2:** each R2 item has a matched no-evidence control; pair passes iff **same status** and, after masking echoed question text, **equivalent structure** — preferably the **same fixed template**, **no citations**, no counts/titles. Latency gap is **measured and disclosed** (side channel), not a hidden pass. T5.5: no LLM call on no-evidence. T5.6: identical not-found body for missing vs unauthorized sources. T7.2: R2 pair + latency recorder.

**Why it matters:** Without a fixed template, “indistinguishable” is a vibe. Title/count leaks are L3/L4. Timing can still distinguish even if text matches.

**Recommendation:** Eval Plan §8.2 is the test I will write. Status for both arms is ABSTAINED or ESCALATED using **one shared template** (OD-09 / T6.4). I will not treat a latency gap as a suite fail unless you later promote it to a fail.

### Candidate 5 — Caching, escalation wording, token/session — **CONFIRMED underspecified (three separate gaps)**

**Caching**

- SRS FR-GEN-03: *if* a response cache exists, key on permission set.
- SDD §7.4: optional; key must also include permission **version**, query normalisation, index version, prompt/model; invalidate on permission change.
- Proposal success criteria and SEC-04 still require cache tests.
- Agent OD-10 / T7.5: **disabled by default**; add only after Phase 7 tests; run SEC-04 with cache off and, if enabled, on.
- NFR-COST-01 / T3.7: **embedding** cache (text hash + model ID) is a different cache.

**Gap:** no TTL, store (memory vs Redis vs SQLite), or whether a shared embedding cache could ever hold restricted *text* (it must not: cache embeddings/vectors or hashes, not passages, in any shared store).

**Recommendation:** Phase 0 sets `cache_enabled=false`. Embedding cache stores vectors keyed by hash+model, never passage text in logs. Response cache is T7.5 only with your approval.

**Escalation wording**

- SRS TBD #8 and FR-VER-05 (Should): ESCALATED with a suggested next step; no case-management.
- SDD: empty evidence ? ABSTAINED **or** ESCALATED; OD-09 open.
- Agent OD-09 default: one template for abstention **and** no-evidence: *“I couldn't find this in the documents available to you. You may want to contact the relevant team.”*
- Eval Plan: ABSTAINED and ESCALATED both count as abstention; R2 must match no-evidence **status** as well as wording.

**Gap:** when is the status `ESCALATED` vs `ABSTAINED` if the user-visible text is identical? A status-code difference would **fail** the §8.2 pair test if R2 and U1 returned different statuses.

**Recommendation:** Until you decide, I will use **one status and one template** for no-evidence, R2, and calibrated abstention (likely `ESCALATED` if we keep the “contact the team” sentence, or `ABSTAINED` if you want no next-step). Mixing statuses will break FR-AUTH-09 tests.

**Token / session**

- SRS TBD #6: proposed username/password + signed token.
- SDD §6.5: salted hashes; signed short-lived session; **“Exact token mechanism and expiry remain TBD.”**
- Agent OD-04: argon2; JWT 60 minutes; claims = `user_id` + `permission_version`; reload grants from DB every request; expired / deleted user / bumped version rejected (T2.2).

**Gap:** JWT vs opaque server session; clock skew; logout/revocation list; whether `permission_version` mismatch is 401 vs silent reload. SDD wants grants **out of** long-lived client claims — Agent’s design matches that if we reload every request.

**Recommendation:** Adopt Agent OD-04 as the Phase 2 default unless you pick opaque sessions. I will not put permission lists in the JWT.

### Other conflicts and gaps (not on your candidate list)

| Issue | Documents | Why it matters | Recommendation |
| --- | --- | --- | --- |
| **Eval Plan C2: persona vs `permitted_roles[]`** | SRS/SDD eval item vs Eval §4.2 / Appendix A `persona` | Recall must be against **authorized** evidence for a named permission set. Role names are not personas (Admin ? extra content). | Store `persona` as in Eval Appendix A. Derived `permitted_roles` optional. |
| **Precision@k / N not fixed (C4)** | Proposal/SRS/SDD “Precision@k”; Eval: Precision@5, MRR@10, Recall@5; N is OD-03 | Incomparable tables. | Report Eval Plan metrics; freeze N before the final run. |
| **Verifier before/after confound (C5)** | Proposal/SRS measure verifier effect; Eval §6.3 cached drafts; Agent T6.6 | Regenerating drafts attributes LLM noise to the verifier. | Cached drafts only. |
| **Search DB** | Proposal: Weaviate **or** Qdrant; SRS/SDD/Agent: Weaviate | Already decided in SRS. | Weaviate. I will not silently switch. |
| **UI** | Proposal: Streamlit or lightweight web; SRS/SDD: Streamlit | Already decided. | Streamlit MVP. |
| **OD numbering in Agent Plan §3** | Three rows labelled **OD-02** (embed, rerank, LLM); **OD-05** and **OD-12** omitted from that table (they exist in SDD) | Decision log will collide. | Keep SDD IDs: OD-02 = all three model IDs; log embed/rerank/LLM as OD-02a/b/c sub-rows. Include OD-05 and OD-12. |
| **Week 1 load** | Proposal Week 1: corpus + baseline RAG + initial benchmark. Agent: Phases 0–3 + vertical slice in Week 1 (~29h of phase estimates) | Schedule fiction if you have 15–20h/week. | Phase 1 before ingestion. Vertical slice after T3, not in the same sitting as T0. |
| **Tests directory** | SDD §5.2 nests `tests/` under `policypilot/`; Agent implies protected `tests/authorization/` | CI path and “protected tests” rule. | Prefer repo-root `tests/` with `authorization/` and `security/` as protected. Confirm at T0.1. |
| **Public demo vs restricted corpus** | NFR-SEC-02 hosted demo = public content only; local demo uses synthetic restricted labels | A public host must not serve Finance canaries. | Local Compose may use full synthetic labels; hosted demo config strips or relabels to Public. |
| **SEC-01 vs L3** | SDD SEC-01: no restricted **title** disclosure; Eval L3: title/section/URL/chunk ID is a leak | Implementation might “helpfully” say the doc exists. | Titles of restricted docs are leaks. Tests must catch them. |
| **SRS Appendix A vs SDD OD list** | SRS TBD 1 (university metadata) has no OD; tracing retention TBD | Not an engineering block. | Leave academic metadata to you; OD-08 for retention. |
| **Who may read the final split** | Eval: nobody tuning may read it; Agent: agent never reads final labels; human labels final in Week 4 | I must not open those files. | You keep final labels outside the agent’s working set or behind the `--final` guard. |

---

## f) Open decisions table

Suggested defaults are the Agent Plan’s / SDD’s starting points, **not facts**. “You decide by” is the latest phase I can start without a frozen choice (I can still record “open, using default”).

| ID | Decision | Suggested default (from docs) | My view | You must decide before |
| --- | --- | --- | --- | --- |
| **OD-01** | Corpus and licence | GitLab Handbook snapshot; **[HUMAN]** verifies licence/redistribution | Do not ingest or publish until you write the licence note. Snapshot locally; no live scrape. | **Phase 3 (T3.0)** |
| **OD-02** | Embedding, reranker, hosted LLM IDs | Small English embedder (e.g. BGE-small-class); MiniLM/BGE-class reranker; one current Claude for generation; different model/tier for any LLM-assisted **labelling**; pin exact strings and revision hashes | Names in the plan are examples. I will not invent a model ID. Settings may hold empty placeholders in Phase 0. Shortlist from model cards + a tiny **dev** comparison. Labelling judge ? generator ? verifier prompt. | Placeholders: Phase 0. Frozen IDs: **before Phase 4/5 paid or local-model download**; freeze again before final run |
| **OD-03** | Chunking and N | Heading-aware ~400 tokens, 15% overlap, N=5 | Fine as **C0 baseline**, not as the reported winner. Tune only on dev (E1). | Baseline values: Phase 3. Frozen N: **before final run** (Week 4) |
| **OD-04** | Auth | Username/password, argon2, signed JWT 60 min; token = `user_id` + `permission_version`; reload grants every request | Matches FR-AUTH-02/07 better than stuffing grants into JWT. Opaque server sessions are equally valid; pick one. | **Phase 2** |
| **OD-05** | Latency/cost/quality numeric targets | Set **after baseline**, **before** frozen final; SRS starting point p95 ? 10s with verification is TBD | I will not choose targets after seeing the final numbers. | After C0, **before Phase 8 final run** |
| **OD-06** | Synthetic visibility | Document-level, seeded random, fixed share per category; manifest; labelled synthetic; each of Manager/HR/Finance supports ?6 questions | Document-level is auditable. Share-% still unspecified. | **Phase 3 (T3.0 with you)** |
| **OD-07** | Admin edits | MVP: Admin edits **user** permissions only; document labels change via ingestion config | Agree. Document-permission editing is a second write path into AuthZ. | **Phase 2–3** (before admin APIs) |
| **OD-08** | Tracing and retention | OpenTelemetry + JSON logs; optional local Phoenix/Langfuse; never capture restricted text; retention TBD | Ship OTel + JSON first. Add Phoenix/Langfuse only if it cannot log passages by default. You still owe a retention period. | Tool: Phase 8 (T8.3). Retention: before any hosted demo |
| **OD-09** | Escalation | Fixed template (see Agent Plan); one template for abstention and no-evidence | Required for FR-AUTH-09 mechanical tests. You must choose **one status code** to pair with it. | Wording can stay default through Phase 5. **Status rule before T5.5/T6.4** |
| **OD-10** | Cache | Disabled by default; add only after Phase 7 tests | Agree. SEC-04 still has a cache-off case. | Phase 0 flag=false. Enable: **only with your approval after T7.5 tests exist** |
| **OD-11** | API prefix and limits | `/api/v1`, 2,000-character question cap, per-user rate limit; timeouts from baseline | Sensible. Timeouts stay TBD until C0. | Prefix/cap: **Phase 2**. Timeouts: after baseline |
| **OD-12** | Held-out split | Predefine split; no tuning on final; disclose if n is small | Eval Plan 50/30 (or 40/20 floor), hashed manifest. I never read final labels or the manifest for tuning. | Split **when T4.6 dataset is authored**, before any threshold/prompt sweep |
| **New** | Relational store | SQLite via SQLAlchemy for users, runs, request logs | Appropriate for a single-node demo. Keep Weaviate for search only. File-backed SQLite + Compose volume is enough. | **Phase 2 (T2.1)**; can note the default in Phase 0 decision log |

**Phase 0 can start** with these recorded as “proposed defaults, not frozen,” except I still need you to **accept that working method** (section h, Q1–Q2).

---

## g) Risks specific to me building this

| Risk | How I will avoid it |
| --- | --- |
| Inventing Weaviate/FastAPI/JWT/SQLAlchemy APIs or model IDs | Read **installed** package docs or source after pin; smoke-test each integration; record exact versions. If docs are missing, stop. |
| Weakening, skipping, or deleting tests so they pass | Stop and ask. Treat `tests/authorization/` and `tests/security/` as protected. Never “temporarily” skip SEC cases. |
| Overfitting to the eval set | No reading frozen final split, split manifest, or final labels while tuning. `--final` flag + hashed config (T8.2). Tune only on dev. |
| Quietly changing authorization semantics to make RAG “work” | Type-level `AuthorizedEvidenceBundle`; spy tests on every LLM prompt; human line-by-line review of Phases 1 and 4. |
| Implementing `relevant_chunk_ids` gold labels because the SRS still says so | Follow Eval C1 unless you reject it in writing. |
| Calling a paid API in tests or loops | `FakeLLMClient` / `SpyLLMClient` default in tests; budget guard in `LLMClient`; no paid calls until you approve provider, model string, and cap. |
| Plausible-but-wrong metrics | Hand-calculated unit tests before any real run (T4.5, T8.1). |
| Context loss across sessions | `AGENTS.md`, `docs/decisions/`, `STATUS.md` after every task. |
| Scope creep (Urdu, tools, tables, agentic retrieval) | Out until Phase 8 gate. ADR-008: no tools in MVP. |
| Committing secrets or restricted text | Env vars only; `.env.example` placeholders; secret scan in CI; log-scan test. |
| Treating 0 leaks as “secure” | Report k/n and the binomial upper bound; limitations section mandatory. |
| Using the verifier or an LLM judge as ground truth | Humans label final split and all security/injection verdicts. |

---

## h) Questions for you (max 10, blocking order)

I am not asking anything the documents already settle (Weaviate, Streamlit, deny-by-default, no tools, cache off by default, GitLab as the *candidate* corpus, 60 as a stated SRS size, span labelling as the Eval Plan’s decision, persona list, SEC-01..10 list).

1. **Normative override:** Do you adopt Eval Plan C1–C7 (span labels, named personas, 80/60 target/floor, Precision@5+MRR@10, cached-draft verifier, §8.2 indistinguishability, latency side-channel disclosure) as overriding SRS v0.1 / SDD v1.0 until those docs are patched?
2. **Phase 0 defaults:** May I record SDD/Agent suggested defaults (SQLite, `/api/v1`, 2k question cap, `cache_enabled=false`, argon2+JWT intent, OD-09 template as draft wording) in `docs/decisions/` as **proposed, not frozen**, and start T0.1–T0.5 on that basis?
3. **ABSTAINED vs ESCALATED:** For no-evidence, R2, U1, and threshold abstention, must status and body be **identical** (required for a strict §8.2 pass)? If yes, which single status?
4. **OD-01:** Will you take the GitLab Handbook (or name another source), verify licence/redistribution, and write that into the decision log before T3.0? I will not scrape or ingest until then.
5. **Paid API:** Provider, how the key is supplied (env var name only), and the hard budget cap for NFR-COST-01? I will not call a paid API in Phase 0–4.
6. **OD-07:** Confirm Admin may change **user** content permissions only in the MVP (not document labels)?
7. **OD-06:** What target share per visibility category (or approve “seeded random with a minimum document count per restricted category” without a percentage)?
8. **Annotator B:** Will a second person label the final split and ?25% of dev, or will you use the 7-day intra-annotator fallback (Eval §11.1)?
9. **Hosted demo vs local:** Is Week 4 success local Compose only, or do you also need a hosted HTTPS demo that must contain **only Public** content (NFR-SEC-02)?
10. **Repo layout:** Keep the PDFs in `Documentation/` and treat `docs/` as engineering docs (this file, decisions, AGENTS copy), or should I copy the five PDFs into `docs/` as Agent Plan §8 says?

---

## Proposed next work session (after your approval)

**Not started.** No code, deps, or paid APIs until you approve.

### Exact first session

**Phase 0: T0.1 ? T0.5, then Phase 1: T1.1 ? T1.3.** One task per branch. Tests first on T1.

**T0.1 — repo scaffold**

- Create (expected): `pyproject.toml` (pinned: ruff, mypy, pytest, pre-commit, a secret scanner such as detect-secrets **after I verify current package names/versions**), `Makefile` with `make check`, `.gitignore`, `.pre-commit-config.yaml`, empty `tests/` so pytest is green, package dir `policypilot/` as in SDD §5.2 (tests at repo root unless you answer Q10/layout).
- Commands: `make check` (ruff, mypy, pytest on empty suite).
- Tests: suite collects 0 failures (empty or a trivial health later — not application logic yet).

**T0.2 — settings**

- Files: `policypilot/app/core/config.py` (pydantic-settings), `.env.example` placeholders only.
- Flags: `retrieval_mode`, `rerank_enabled`, `verifier_enabled`, `cache_enabled=false`, model ID placeholders, thresholds, N.
- Test first: missing required settings fail startup with a clear error.

**T0.3 — Compose**

- Files: `compose.yaml` (api, ui, Weaviate **pinned image tag**, healthchecks). Images are not a paid LLM API; I still will not pull huge models until OD-02.
- Command: `docker compose up` ? healthy (when you approve running Compose).

**T0.4 — GitHub Actions**

- Lint, types, unit tests, secret scan; green on main.

**T0.5 — docs**

- `docs/decisions/` template; `AGENTS.md` copied from Agent Plan §1; this `UNDERSTANDING.md` already done.

**Then T1.1–T1.3 — authorization core (highest-value code)**

- Files: `policypilot/app/models/domain.py`, `policypilot/app/services/authorization.py`, `tests/authorization/` table-driven + Hypothesis properties.
- Tests **written first**:
  - Unknown permission label ? invalid / deny.
  - Matrix from SDD §2.2 / SRS §3.4 (all cells).
  - Manager+HR union without Finance (SEC-10).
  - Empty, malformed, unknown grants ? deny.
  - Admin / Evaluator with no extra grants cannot read Manager/HR/Finance (SEC-09).
  - Property: readable chunks ? grants ? chunk permissions.
  - Fail-closed on exception.
- Gate: you read the authorization tests line by line before Phase 2.

### What I need from you before that session

- Answers to **h) Q1–Q3** (override, Phase 0 defaults, abstention status). Q4–Q10 can be “later” if you say so, except Q4 is a hard stop before Phase 3.
- GitHub repo / Actions enabled if T0.4 should run on a remote (local `make check` can proceed without it).
- **No API keys required for T0–T1.** When keys exist: environment variables only; never in git.
- Corpus choice is **not** required for T0–T1.

### I will not do without your approval

- Application code or installs before you accept this report.
- Any paid LLM/API call or raising the budget cap.
- Enabling the response cache.
- Ingesting/publishing corpus text before written licence confirmation.
- Reading or editing a frozen eval set, split manifest, or final-split labels.
- Weakening, skipping, or deleting authorization/security tests.
- Changing authorization semantics (including Public/Employee baseline).
- Adding tools, Urdu, agentic retrieval, or table/OCR pipelines.
- Committing secrets, real passwords, or a filled `.env`.
- Switching Weaviate ? Qdrant, Streamlit ? another UI, or inventing model IDs.
- Marking numeric quality/latency targets after seeing final-split results.
- Hosting a public demo that includes synthetic restricted documents.

---

*End of understanding report. Waiting for approval before any further work.*
