"""Offline ingestion of an explicitly approved corpus manifest.

Permissions come only from the human-reviewed manifest. Nothing is inferred from
filenames, document text, retrieval scores, or system roles. The adapter verifies the
archive and per-file hashes before extraction, and publishes all prepared documents in
one content-store transaction.
"""

import hashlib
import json
import re
import zipfile
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from policypilot.app.models.content import DocumentMetadata
from policypilot.app.models.domain import Permission
from policypilot.app.services.content_store import ContentStore
from policypilot.app.services.docx import EXTRACTOR_VERSION, extract_docx
from policypilot.app.services.ingestion import (
    ChunkingConfig,
    IngestionService,
    PreparedDocument,
    SourceDocument,
)

_MESSAGE = "Corpus ingestion failed"
_NOT_APPROVED = "Corpus manifest is not approved"
_MAX_MANIFEST_BYTES = 1_048_576
_MAX_DOCX_BYTES = 1_048_576
_ARCHIVE_PREFIX = "company_bronze/data/content/policies/"
_ESCAPED_HEADING = re.compile(r"^((?:\\#){1,6})[ \t]+(.*)$")
_HEADING = re.compile(r"^(#{1,6})[ \t]+(.*)$")
_ESCAPE = re.compile(r"\\(.)")


class CorpusError(ValueError):
    """Content-free failure for manifests, archives, and ingestion boundaries."""


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def normalize_literal_headings(markdown: bytes) -> bytes:
    """Turn the approved corpus's escaped ATX headings into real section markers.

    The DOCX extractor deliberately escapes Markdown punctuation in body text. This
    corpus stores its section headings as literal "## Heading" paragraphs, so the
    approved-corpus adapter restores them as data-only section labels. Permissions are
    never derived from headings, and headings remain untrusted text.
    """
    lines: list[str] = []
    for line in markdown.decode("utf-8").splitlines(keepends=True):
        newline = "\n" if line.endswith("\n") else ""
        body = line[: -len(newline)] if newline else line
        escaped = _ESCAPED_HEADING.match(body)
        if escaped is not None:
            level = len(escaped.group(1)) // 2
            lines.append("#" * level + " " + _ESCAPE.sub(r"\1", escaped.group(2)) + newline)
            continue
        actual = _HEADING.match(body)
        if actual is not None:
            lines.append(actual.group(1) + " " + _ESCAPE.sub(r"\1", actual.group(2)) + newline)
            continue
        lines.append(line)
    return "".join(lines).encode("utf-8")


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ManifestDataset(_Model):
    title: str = Field(min_length=1, max_length=4096)
    publisher: str = Field(min_length=1, max_length=4096)
    source_page: str = Field(min_length=1, max_length=4096)
    release: str = Field(min_length=1, max_length=4096)
    archive: str = Field(min_length=1, max_length=4096)
    archive_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    retrieved_on: date
    license: str = Field(min_length=1, max_length=4096)
    license_url: str = Field(min_length=1, max_length=4096)
    approved_scope: str = Field(min_length=1, max_length=4096)
    extractor: str = Field(min_length=1, max_length=128)
    english_document_count: int = Field(ge=1, le=100, strict=True)
    notes: list[str] = Field(default_factory=list, max_length=32)


class ManifestDocument(_Model):
    file: str = Field(pattern=r"^[a-z0-9_]+_en\.docx$")
    archive_path: str = Field(pattern=r"^company_bronze/data/content/policies/[a-z0-9_]+_en\.docx$")
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    extracted_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    title: str = Field(min_length=1, max_length=4096)
    source_url: str = Field(min_length=1, max_length=4096)
    retrieved_on: date
    licence_note: str = Field(min_length=1, max_length=4096)
    version: str = Field(min_length=1, max_length=4096)
    allowed_permissions: list[Permission] = Field(min_length=1, max_length=len(Permission))
    rationale: str = Field(min_length=1, max_length=4096)

    @field_validator("allowed_permissions")
    @classmethod
    def unique_permissions(cls, value: list[Permission]) -> list[Permission]:
        if len(set(value)) != len(value):
            raise ValueError("duplicate permission")
        return value

    @field_validator("source_url")
    @classmethod
    def valid_source_url(cls, value: str) -> str:
        if any(ord(char) < 32 or char == "\x7f" for char in value):
            raise ValueError("invalid source URL")
        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
        ):
            raise ValueError("invalid source URL")
        return value


class CorpusManifest(_Model):
    dataset: ManifestDataset
    status: Literal["proposed", "approved"]
    approved: bool
    approved_by: str | None = Field(default=None, max_length=256)
    approved_on: date | None = None
    approval_note: str = Field(min_length=1, max_length=4096)
    documents: list[ManifestDocument] = Field(min_length=1, max_length=100)


def read_manifest(path: Path) -> CorpusManifest:
    """Read a schema-valid manifest without requiring human approval."""
    try:
        if not isinstance(path, Path):
            raise CorpusError(_MESSAGE)
        data = path.read_bytes()
        if not 0 < len(data) <= _MAX_MANIFEST_BYTES:
            raise CorpusError(_MESSAGE)
        manifest = CorpusManifest.model_validate(json.loads(data))
        if manifest.status == "approved" and (
            manifest.approved is not True
            or not (manifest.approved_by or "").strip()
            or manifest.approved_on is None
        ):
            raise CorpusError(_MESSAGE)
        if manifest.status == "proposed" and manifest.approved:
            raise CorpusError(_MESSAGE)
        if manifest.dataset.extractor != EXTRACTOR_VERSION:
            raise CorpusError(_MESSAGE)
        if len(manifest.documents) != manifest.dataset.english_document_count:
            raise CorpusError(_MESSAGE)
        if len({document.file for document in manifest.documents}) != len(manifest.documents):
            raise CorpusError(_MESSAGE)
        if len({document.archive_path for document in manifest.documents}) != len(
            manifest.documents
        ):
            raise CorpusError(_MESSAGE)
        if len({document.source_url for document in manifest.documents}) != len(manifest.documents):
            raise CorpusError(_MESSAGE)
        return manifest
    except (CorpusError, OSError, ValidationError, ValueError, TypeError):
        raise CorpusError(_MESSAGE) from None


def require_approved(manifest: CorpusManifest) -> None:
    if manifest.status != "approved" or manifest.approved is not True:
        raise CorpusError(_NOT_APPROVED)
    if not (manifest.approved_by or "").strip() or manifest.approved_on is None:
        raise CorpusError(_NOT_APPROVED)


def _member(archive: zipfile.ZipFile, document: ManifestDocument) -> bytes:
    info = archive.getinfo(document.archive_path)
    if info.is_dir() or info.file_size > _MAX_DOCX_BYTES:
        raise CorpusError(_MESSAGE)
    with archive.open(document.archive_path) as member:
        data = member.read(_MAX_DOCX_BYTES + 1)
    if len(data) != info.file_size or _digest(data) != document.sha256:
        raise CorpusError(_MESSAGE)
    return data


def prepare_manifest(
    manifest: CorpusManifest,
    archive_path: Path,
    *,
    chunking: ChunkingConfig | None = None,
    now: datetime | None = None,
) -> tuple[PreparedDocument, ...]:
    """Verify and prepare every manifest document without writing to a store."""
    try:
        if not isinstance(archive_path, Path) or not archive_path.is_file():
            raise CorpusError(_MESSAGE)
        if _digest(archive_path.read_bytes()) != manifest.dataset.archive_sha256:
            raise CorpusError(_MESSAGE)
        timestamp = now if now is not None else datetime.now(UTC)
        if timestamp.utcoffset() is None:
            raise CorpusError(_MESSAGE)
        sources: list[SourceDocument] = []
        with zipfile.ZipFile(archive_path) as archive:
            for document in manifest.documents:
                raw = _member(archive, document)
                extracted = extract_docx(raw)
                if _digest(extracted) != document.extracted_sha256:
                    raise CorpusError(_MESSAGE)
                normalized = normalize_literal_headings(extracted)
                metadata = DocumentMetadata(
                    doc_id="doc-" + _digest(document.source_url.encode("utf-8")),
                    title=document.title,
                    source_url=document.source_url,
                    retrieved_on=document.retrieved_on,
                    licence_note=document.licence_note,
                    version=document.version,
                    updated_at=timestamp,
                    allowed_permissions=frozenset(document.allowed_permissions),
                )
                sources.append(SourceDocument(metadata, normalized, media_type="text/markdown"))
        prepared = IngestionService(chunking or ChunkingConfig()).prepare_batch(tuple(sources))
        if len(prepared) != len(manifest.documents):
            raise CorpusError(_MESSAGE)
        return prepared
    except (CorpusError, OSError, ValueError, TypeError, zipfile.BadZipFile):
        raise CorpusError(_MESSAGE) from None


def ingest_manifest(
    manifest: CorpusManifest,
    archive_path: Path,
    store: ContentStore,
    *,
    chunking: ChunkingConfig | None = None,
    now: datetime | None = None,
) -> int:
    """Publish an approved manifest atomically; return the document count."""
    try:
        require_approved(manifest)
        prepared = prepare_manifest(manifest, archive_path, chunking=chunking, now=now)
        store.replace_many(prepared)
        return len(prepared)
    except CorpusError:
        raise
    except (OSError, ValueError, TypeError):
        raise CorpusError(_MESSAGE) from None
