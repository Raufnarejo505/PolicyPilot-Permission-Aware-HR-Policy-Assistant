# 0008 — Approved external policy corpus

Date: 2026-10-21
Status: approved; files supplied and ingested (see 0010)

## Approval

The user explicitly confirmed that the selected dataset and its CC BY 4.0 attribution requirements are acceptable for this project. This records the human corpus/licensing approval required by AGENTS.md; do not ask for the same approval again.

- Dataset: **HR Corporate Data (Bilingual EN/PL, Bronze Layer)**.
- Publisher shown on the dataset page: **Beata Faron**.
- Source: https://www.kaggle.com/datasets/beatafaron/hr-corporate-data-bilingual-enpl-bronze-layer
- Dataset page reports synthetic corporate data and a **CC BY 4.0** license.
- Approved initial scope: **English policy documents only**. The listing describes 17 HR policies per language in DOCX format; actual contents/counts remain unverified until files are available.
- Exclude Polish policies, employee tables, emails, chats, helpdesk/compliance tickets, and other non-policy files from this initial corpus. Do not inspect potential evaluation questions or labels while inventorying source policies.

## Handling and attribution

Verify the downloaded license/provenance against the approved listing before processing. If packaged terms conflict or individual files carry different restrictions, stop and request clarification rather than treating this approval as a blanket override.

Retain the dataset title, publisher attribution, source URL, license link (https://creativecommons.org/licenses/by/4.0/), downloaded release/version, retrieval date, and archive/file checksums. Note extraction/chunking and other modifications. Preserve applicable upstream notices; do not imply publisher endorsement. Approval is not a warranty of upstream ownership or document quality.

Store downloaded source files under ignored `data/`; do not commit archives or extracted content by default. Do not request or commit Kaggle credentials/API tokens.

## Remaining work and boundaries

At approval time, `data/` was empty: no dataset archive was downloaded, opened, extracted, or ingested. No actual file checksums, packaged license checks, or content-quality findings can yet be recorded.

After files are supplied: inventory approved policy files, validate provenance, implement/test bounded DOCX extraction, and propose an explicit document-to-permission manifest for human review before indexing. Never infer grants from file names, document text, or system roles; missing/unapproved permissions deny. This approval does not approve a permission mapping, index/model choice, paid API call, or evaluation precedence. Frozen final evaluation items remain isolated, and response caching stays disabled.

Decision 0007 remains the historical synthetic-preparation milestone. This decision advances the corpus-selection approval gate only; it does not claim DOCX support, ingestion/index persistence, search, or generation has been implemented.
