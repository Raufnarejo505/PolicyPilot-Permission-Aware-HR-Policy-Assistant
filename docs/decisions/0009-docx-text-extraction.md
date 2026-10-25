# 0009 — Offline DOCX text extraction contract

Date: 2026-10-25
Status: implemented and tested; corpus permission mapping still pending

## Approval and scope

The user approved updating the stale v1 DOCX tests to the implemented v2 contract after
reviewing the 15 failures. This decision records the contract; it does not approve a
document-to-permission mapping, indexing, model calls, or real-corpus ingestion.

`policypilot/app/services/docx.py` converts bounded, untrusted OOXML bytes into UTF-8
Markdown without filesystem reads, network access, extraction to disk, logging, language
detection, or permission inference. Callers remain responsible for corpus approval,
provenance, permission labels, and downstream ingestion validation.

## v2 contract

- Accept only a bounded ZIP package: input <= 1 MiB, <= 256 entries, declared expansion
  <= 8 MiB total and <= 2 MiB/member, <= 200:1/member, UTF-8 XML only, no DTD/entities,
  bounded XML nodes and depth. Reject macros, encrypted members, symlinks/special files,
  duplicate members, unknown parts, unsafe paths, and unsupported compression.
- Read only the core parts needed for text: main document, styles, numbering, and the
  package/document relationship parts. Ancillary layout, metadata, custom-XML content,
  images, headers/footers, comments, notes, and embedded objects are never read.
- Unused template styles are not applied and are tolerated, including Heading7..9 and
  table styles. Any style that is actually applied is fully resolved and validated:
  missing parents, cycles, over-deep chains, invalid outline levels, and unsupported
  formatting reject the whole document.
- Valid `http`/`https`/`mailto` external hyperlink targets are validated and then
  discarded; only the visible label is emitted. Targets are never fetched, logged, or
  returned. Internal anchors are syntax-checked. Non-hyperlink external relationships
  reject.
- Unbound custom-XML parts are accepted but never read. Bound custom-XML relationships
  are path-checked, and custom-XML property parts are type/path-checked; their bytes are
  not parsed or reflected.
- Hidden/vanish text and unsupported structural markup reject when applied. Literal
  Markdown punctuation in source text is escaped, so document text cannot manufacture
  downstream headings, fences, links, or HTML.
- Output is deterministic UTF-8 Markdown, <= 1 MiB, with a terminal LF. The extractor
  version is `docx-text-markdown-v2`.

## Validation

`tests/test_docx.py` covers deterministic output, formatting, styles, numbering,
hyperlinks, custom XML, malformed inputs, resource limits, and error redaction. The v2
contract was also smoke-tested offline against all 18 English policy DOCX files in the
approved corpus archive; all extracted successfully. Passing tests is not a claim of
universal security, and the extractor does not establish licensing or access rights.

## Remaining gates

The corpus permission manifest must be approved by a human before indexing. A parent
adapter must retain original-byte hashes and `EXTRACTOR_VERSION` in its version recipe,
then pass the extracted Markdown explicitly to ingestion. No retrieval, model, or
evaluation work is approved by this decision.
