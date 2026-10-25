# 0010 — Approved-corpus pipeline and proposed permission manifest

Date: 2026-10-25
Status: approved and ingested on 2026-10-25

## Corpus verification

- Archive: `data/archive.zip`, SHA-256
  `c7242e1c6bc981f0f418aa68fabfef11e7d0cc370ae32bd168c376a341450d7c`.
- Packaged `README.md` and `dataset_card.json` both declare synthetic content and
  CC BY 4.0. The source dataset is "Bilingual HR Bronze (EN/PL) — Synthetic Corporate
  Data Lake Snapshot (2025-01)" by Beata Faron.
- The packaged README mentions `MANIFEST.csv`, but the archive does not contain it.
  Per-file SHA-256 values were computed directly from the archive instead.
- The archive contains 18 English policy DOCX files (the README describes 17 per
  language) plus Polish and variant files. Only the 18 English policy files are in
  scope; chats, emails, tickets, HRIS tables, PDFs, and other files are excluded.
- All 18 files extract with `docx-text-markdown-v2`. Extracted and normalized outputs
  are section-aware and produce roughly 600 chunks with the default 1200/150 chunking.

## Manifest and approval

`docs/corpus/english-policy-manifest.proposed.json` contains the proposed document
metadata and `allowed_permissions` for every document. It was approved by the
project owner on 2026-10-25 and is now `status: approved`. `docs/corpus/english-policy-inventory.json`
records the factual inventory and checksums. The CLI still refuses an unapproved manifest.

The proposed mapping assigns Public or Employee access to general policies and
restricts compensation, termination, whistleblowing, grievance, performance, and
recruitment material to the relevant Manager/HR/Finance categories. This mapping is a
review proposal, not an inference from filenames or document text.

## Pipeline

- `services/corpus.py` validates the manifest schema, requires explicit approval,
  verifies the archive and per-member SHA-256, verifies the extracted SHA-256, builds
  `DocumentMetadata` only from manifest fields, and calls `IngestionService`.
- `normalize_literal_headings` restores the approved corpus's escaped literal ATX
  headings as ingestion section markers. Sections are data-only labels; they never
  establish or change permissions. The extractor contract and its escaped-output
  behavior remain unchanged in `0009`.
- `ContentStore.replace_many` publishes every prepared document in one transaction.
  Hash, schema, validation, or insertion failure leaves the previous corpus intact.
- Operator commands:
  `init-content-db`, `inventory-corpus --manifest ... --archive ...`, and
  `ingest-corpus --manifest ... --archive ...`. Inventory works on a proposed
  manifest; ingestion requires approval. Commands never accept passwords or infer
  permissions.

## Remaining gates

The English permission manifest is approved and the corpus is ingested. Model/provider/
budget approval is still required before any paid generation call, and evaluation
precedence before dataset work. Response caching remains disabled. Passing tests
establishes only the tested behavior, not universal security.
