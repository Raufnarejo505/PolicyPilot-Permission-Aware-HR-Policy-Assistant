"""Pure, bounded source preparation; no fetching, persistence, or authorization.

Only synthetic sources are approved for use at this milestone. Callers must obtain
corpus approval before supplying real documents. No approval flag can establish
licensing or grant content access.
"""

import hashlib
import json
import re
from dataclasses import dataclass, field, replace

from policypilot.app.models.content import (
    Chunk,
    DocumentMetadata,
    valid_document_metadata,
)

_INVALID = "Invalid ingestion input"
_MAX_BYTES = 1_048_576
_MAX_BATCH_BYTES = 4_194_304
_MAX_DOCUMENTS = 100
_MAX_CHUNKS = 4096
_MAX_BATCH_CHUNKS = 8192
_MAX_OUTPUT_CHARACTERS = 2_097_152
_MAX_BATCH_OUTPUT_CHARACTERS = 8_388_608
_MAX_METADATA_CHARACTERS = 4096
_ALGORITHM = "utf8-atx-characters-v1"
_HEADING = re.compile(r"^ {0,3}(#{1,6})[ \t]+(.*)$")
_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")


class IngestionError(ValueError):
    """Content-free validation failure; never include source fields or parser errors."""


@dataclass(frozen=True)
class ChunkingConfig:
    """Character windows, not model tokens; overlap never crosses sections."""

    max_characters: int = 1200
    overlap_characters: int = 150

    def __post_init__(self) -> None:
        if not _valid_config(self):
            raise IngestionError(_INVALID)


def _valid_config(value: object) -> bool:
    try:
        return (
            type(value) is ChunkingConfig
            and type(value.max_characters) is int
            and 64 <= value.max_characters <= 16000
            and type(value.overlap_characters) is int
            and 0 <= value.overlap_characters < value.max_characters
        )
    except AttributeError:
        return False


@dataclass(frozen=True)
class SourceDocument:
    """Trusted operator-supplied provenance plus untrusted UTF-8 source bytes.

    Original metadata is retained in PreparedDocument.provenance, separately from
    generated identities. Source URLs are exact lineage keys, never fetched.
    """

    metadata: DocumentMetadata = field(repr=False)
    content: bytes = field(repr=False)
    media_type: str = field(default="text/plain", repr=False)

    def __post_init__(self) -> None:
        if not _valid_source(self):
            raise IngestionError(_INVALID)


def _valid_source(value: object) -> bool:
    try:
        return (
            type(value) is SourceDocument
            and type(value.metadata) is DocumentMetadata
            and all(
                type(text) is str and len(text) <= _MAX_METADATA_CHARACTERS
                for text in (
                    value.metadata.doc_id,
                    value.metadata.version,
                    value.metadata.title,
                    value.metadata.source_url,
                    value.metadata.licence_note,
                )
            )
            and valid_document_metadata(value.metadata)
            and type(value.content) is bytes
            and 0 < len(value.content) <= _MAX_BYTES
            and type(value.media_type) is str
            and value.media_type in ("text/plain", "text/markdown")
        )
    except AttributeError:
        return False


@dataclass(frozen=True)
class PreparedDocument:
    """Untrusted, nonpersistent output; not indexed content or authorized evidence."""

    document: DocumentMetadata = field(repr=False)
    chunks: tuple[Chunk, ...] = field(repr=False)
    provenance: DocumentMetadata = field(repr=False)


def _digest(parts: object) -> str:
    encoded = json.dumps(parts, ensure_ascii=True, separators=(",", ":")).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


def _decode(content: bytes) -> str | None:
    # Return a sentinel so callers raise outside the codec exception context.
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        return None
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if not text.strip() or any(ord(char) < 32 and char not in "\n\t" for char in text):
        return None
    if "\x7f" in text:
        return None
    return text


def _sections(text: str, markdown: bool) -> tuple[tuple[str, str], ...]:
    if not markdown:
        return (("Document", text),)
    sections: list[tuple[str, str]] = []
    headings: list[tuple[int, str]] = []
    lines: list[str] = []
    section = "Document"
    fence_character = ""
    fence_length = 0
    for line in text.splitlines(keepends=True):
        fence = _FENCE.match(line.rstrip("\n"))
        if fence:
            marker, remainder = fence.groups()
            if not fence_character:
                fence_character, fence_length = marker[0], len(marker)
            elif (
                marker[0] == fence_character
                and len(marker) >= fence_length
                and not remainder.strip()
            ):
                fence_character = ""
            lines.append(line)
            continue
        heading = None if fence_character else _HEADING.match(line.rstrip("\n"))
        if heading:
            if "".join(lines).strip():
                sections.append((section, "".join(lines)))
            lines = []
            level = len(heading[1])
            title = heading[2].rstrip(" \t")
            without_hashes = title.rstrip("#")
            if without_hashes != title and (not without_hashes or without_hashes[-1] in " \t"):
                title = without_hashes.rstrip(" \t")
            if not title.strip():
                title = "Untitled section"
            while headings and headings[-1][0] >= level:
                headings.pop()
            headings.append((level, title))
            section = " / ".join(title for _, title in headings)
        lines.append(line)
    if "".join(lines).strip():
        sections.append((section, "".join(lines)))
    return tuple(sections)


_DEFAULT_CONFIG = ChunkingConfig()


class IngestionService:
    """Stateless preparation with all-or-nothing batch validation.

    No disk reads, network, logging, cache, index, model calls, or source exposure.
    Identical batch entries collapse; conflicting versions of one source reject.
    """

    def __init__(self, config: ChunkingConfig = _DEFAULT_CONFIG) -> None:
        if not _valid_config(config):
            raise IngestionError(_INVALID)
        self._config = config

    def prepare(self, source: SourceDocument) -> PreparedDocument:
        if not _valid_config(self._config) or not _valid_source(source):
            raise IngestionError(_INVALID)
        text = _decode(source.content)
        if text is None:
            raise IngestionError(_INVALID)
        metadata = source.metadata
        doc_id = "doc-" + _digest(["source-v1", metadata.source_url])
        version = "sha256-" + _digest(
            [
                _ALGORITHM,
                text,
                source.media_type,
                metadata.title,
                metadata.licence_note,
                metadata.version,
                sorted(label.value for label in metadata.allowed_permissions),
                self._config.max_characters,
                self._config.overlap_characters,
            ]
        )
        document = replace(metadata, doc_id=doc_id, version=version)
        chunks: list[Chunk] = []
        output_characters = 0
        section_characters = 0
        for section_index, (section, body) in enumerate(
            _sections(text, source.media_type == "text/markdown")
        ):
            start = 0
            while start < len(body):
                end = min(start + self._config.max_characters, len(body))
                passage = body[start:end]
                if passage.strip():
                    output_characters += len(passage)
                    section_characters += len(section)
                    if (
                        len(chunks) >= _MAX_CHUNKS
                        or output_characters > _MAX_OUTPUT_CHARACTERS
                        or section_characters > _MAX_OUTPUT_CHARACTERS
                    ):
                        raise IngestionError(_INVALID)
                    chunk_id = "chunk-" + _digest([doc_id, version, section_index, start, end])
                    chunks.append(Chunk(chunk_id, document, section, passage))
                if end == len(body):
                    break
                start = end - self._config.overlap_characters
        return PreparedDocument(document, tuple(chunks), metadata)

    def prepare_batch(self, sources: tuple[SourceDocument, ...]) -> tuple[PreparedDocument, ...]:
        if (
            type(sources) is not tuple
            or not 1 <= len(sources) <= _MAX_DOCUMENTS
            or not all(_valid_source(source) for source in sources)
            or sum(len(source.content) for source in sources) > _MAX_BATCH_BYTES
        ):
            raise IngestionError(_INVALID)
        prepared: dict[str, PreparedDocument] = {}
        total_chunks = 0
        total_characters = 0
        total_section_characters = 0
        for source in sources:
            result = self.prepare(source)
            previous = prepared.get(result.document.doc_id)
            if previous is not None:
                if previous != result:
                    raise IngestionError(_INVALID)
                continue
            total_chunks += len(result.chunks)
            total_characters += sum(len(chunk.text) for chunk in result.chunks)
            total_section_characters += sum(len(chunk.section) for chunk in result.chunks)
            if (
                total_chunks > _MAX_BATCH_CHUNKS
                or total_characters > _MAX_BATCH_OUTPUT_CHARACTERS
                or total_section_characters > _MAX_BATCH_OUTPUT_CHARACTERS
            ):
                raise IngestionError(_INVALID)
            prepared[result.document.doc_id] = result
        return tuple(prepared.values())
