# 0007 — Synthetic document preparation

Date: 2026-10-21
Status: historical synthetic-preparation milestone; the approved corpus pipeline is in 0010

## Scope

The user approved proceeding with synthetic documents. Implement the pure preparation prerequisite of SRS FR-ING-01/02/03/05 and SDD §§4.2, 7.1–7.3, not a completed ingestion/index service. No real documents, source PDFs, evaluation items, URLs, or paid services are ingested. Existing authorization semantics/tests are unchanged.

## Interface and parsing

`services/ingestion.py` provides immutable, representation-redacted `SourceDocument`, `PreparedDocument`, and `ChunkingConfig`, plus stateless `IngestionService.prepare` / `prepare_batch`. Callers supply explicit validated provenance and permission labels. There is no browser/API ingestion route, filesystem reader, source fetcher, database, index, cache, model call, or automatic licensing approval.

- Accept bytes with exactly `text/plain` or `text/markdown`; strict UTF-8 decoding, optional leading UTF-8 BOM removal, CRLF/CR normalization to LF. Reject blank text, unsupported formats, invalid encodings, ASCII controls other than LF/tab, and DEL. Errors are generic and raised outside codec exception contexts; no raw payload logging.
- Plain text is one section. Markdown supports ATX headings of levels 1–6 and backtick/tilde fenced code; headings inside fences remain data. Preserve heading lines in passage text and build hierarchical section paths. Empty/whitespace-only headings use `Untitled section`. Heading parsing uses linear scans, not ambiguous trailing-whitespace regular expressions.
- This is deliberately not a complete CommonMark, HTML, PDF, table, or scanned-document parser. No markup is rendered or embedded instructions executed.
- Fixed Unicode-character windows default to **1200 characters, 150 overlap**. These are documented provisional preparation settings, not tokenizer/model settings or evaluation-tuned production defaults. Configurable maximum 64–16000; overlap 0 through maximum minus one. Windows never cross section boundaries. Preserve nonblank windows verbatim after decoding normalization; whitespace-only windows/preambles are omitted. No sentence/word-boundary guarantee.

## Identity and provenance

- Generated `doc_id` is SHA-256 of a versioned, unambiguous JSON representation of the exact source URL. URL spelling, query, and fragment changes create distinct lineage keys. No normalization, fetching, DNS, SSRF safety, or live citation validation is implied.
- Generated `document.version` hashes normalized source text, parser algorithm identifier, media type, title, licence note, upstream version, sorted canonical permissions, and chunking parameters. Permission changes regenerate all chunk IDs. Retrieval/update timestamps and supplied upstream document ID do not alter generated IDs.
- Each `chunk_id` hashes generated document ID/version, section ordinal, and character-window offsets. Identical preparation inputs reproduce IDs; different source lineage cannot share chunk IDs.
- `PreparedDocument.provenance` retains original metadata, including upstream document ID/version, independently of generated identities. The generated document keeps current provenance dates. All chunks reference exactly their generated document and inherit its permissions without overrides or inferred Public access.
- Hashes identify content; they are neither authorization nor secrecy. Do not expose manifests/identifiers without appropriate access control.

## Batch and resource behavior

- Bound each source to 1 MiB; each batch to 100 entries and 4 MiB source bytes. Each provenance string is limited to 4096 characters.
- Cap output at 4096 chunks/document and 8192 chunks/batch. Separately cap emitted passage characters and cumulative section-label characters at 2,097,152 each/document and 8,388,608 each/batch. This bounds overlap amplification and repeated validation of large headings, even with near-total overlap.
- Revalidate nested metadata and configuration, including forged/incomplete objects. Invalid/mixed/unknown permissions reject, never downgrade or silently normalize.
- `prepare_batch` accepts a nonempty tuple. Exact prepared duplicates collapse in first-source order; differing versions or provenance for one source URL reject the whole call. Even a timestamp-only difference is a provenance conflict within a batch. Duplicates do not consume output budgets twice, but still count against input budgets.
- Preparation is pure: rejected calls cannot partially persist anything. Results are ordinary internal values, not a security capability. Future stores must revalidate and enforce transactional replace/delete; there is no cross-run deduplication, stale-content removal, ingestion run tracking, or restart persistence yet.

## Validation and remaining gates

Synthetic tests cover identities/provenance, normalization, all nonempty permission combinations, malformed/forged inputs, headings/fences, Unicode preservation/overlap properties, output/input limits, duplicates/conflicts, redacted errors/repr, and offline execution. Run `make check` and explicit pre-commit checks for new files. Passing tests does not prove universal security.

Human approval of a selected corpus and its licensing/redistribution terms remains required before real-document ingestion. Persistence/index implementation must address replacement, deletion, atomic publication, permissions, and reauthorization. Paid provider/model/budget and evaluation-precedence gates remain unchanged; response caching stays disabled. No frozen evaluation material is accessed. No ingestion UI or search/answer capability is implied by this internal milestone.
