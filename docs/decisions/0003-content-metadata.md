# 0003 — Corpus-independent content metadata

Date: 2026-10-19
Status: implemented foundation; corpus ingestion remains gated

## Scope and rationale

Continue with the metadata prerequisite of SRS FR-ING-01/03/05 and SDD §7.1 without selecting, fetching, parsing, or indexing a corpus. This is not completion of those ingestion requirements. Corpus selection/licensing and chunking defaults remain unapproved; no synthetic classification manifest or evaluation dataset is created.

## Choices

- Add immutable internal `DocumentMetadata` and `Chunk` dataclasses, consistent with the existing domain layer; no new dependency, endpoint, store, model, or configuration is required.
- Require explicit document ID, title, HTTP(S) source URL, retrieval date, licence note, version, timezone-aware update timestamp, and nonempty canonical `frozenset[Permission]`. Missing/invalid/mixed labels reject the entire value without normalization. There is no implicit Public grant. Internal typed constructors reject raw label strings; a future external-input adapter must validate before constructing them.
- Chunks reference their document and supply an explicit chunk ID, section, and nonblank UTF-8 text. They inherit exactly the document permissions, with no override mechanism in this milestone. IDs/versioning are supplied; stable-ID generation, uniqueness, source updates/deletes, and persistence belong to the future ingestion implementation.
- Text is untrusted and preserved without trimming or interpreting embedded instructions. NUL and invalid UTF-8 surrogate values reject. Metadata validity is not content authorization or prompt-injection protection.
- Redact all document/chunk fields from representations; validation failures contain no field values or nested error causes. This does not make explicit serialization, traceback-local capture, or logging individual fields safe; future adapters must not log raw payloads or restricted content.
- Provide nested revalidation helpers for future persistence/index boundaries. Immutable dataclasses alone are not a trust boundary. Current authorization semantics and original security tests remain unchanged.
- Source URLs receive structural checks, including rejection of credentials, whitespace/control characters, malformed percent escapes, and invalid ports. No DNS/link validation or fetching occurs. These checks are not SSRF protection or approval to fetch a URL; future fetchers require separate source allowlisting and network policy.

## Validation and remaining gates

Synthetic tests cover malformed metadata, all nonempty permission combinations, inheritance, immutability, forged/nested values, redacted representations/errors, and unchanged role/content separation. Full `make check` remains required. Passing tests establishes only the tested behavior, not universal security.

A licence note is a provenance record, not human approval of legal terms. Confirm the selected corpus and licensing/redistribution terms before ingestion. Parser/chunking parameters and stable-ID rules need a separate recorded decision. Provider/model/budget approval remains required before paid calls, and evaluation precedence before dataset work. No frozen evaluation material is accessed and response caching remains disabled.
