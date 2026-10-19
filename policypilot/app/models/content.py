"""Validated internal content values, not ingestion approval or authorized evidence."""

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from urllib.parse import urlsplit

from policypilot.app.models.domain import Permission

_INVALID = "Invalid content metadata"


class ContentValidationError(ValueError):
    """A content-free failure suitable for translation at an ingestion boundary."""


def _valid_text(value: object) -> bool:
    if type(value) is not str or not value.strip() or "\x00" in value:
        return False
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def _valid_source_url(value: str) -> bool:
    # Syntax only: no fetch, DNS lookup, licence approval, or SSRF protection.
    if any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in value):
        return False
    if re.search(r"%(?![0-9a-fA-F]{2})", value):
        return False
    parts = urlsplit(value)
    return (
        parts.scheme in ("https", "http")
        and bool(parts.hostname)
        and parts.username is None
        and parts.password is None
        and (parts.port is None or 1 <= parts.port <= 65535)
    )


@dataclass(frozen=True)
class DocumentMetadata:
    """Explicit provenance and labels supplied by a future trusted ingestion boundary.

    A licence note records provenance; its presence does not establish legal approval.
    IDs and versions are supplied, never inferred from text or permissions here.
    """

    doc_id: str = field(repr=False)
    title: str = field(repr=False)
    source_url: str = field(repr=False)
    retrieved_on: date = field(repr=False)
    licence_note: str = field(repr=False)
    version: str = field(repr=False)
    updated_at: datetime = field(repr=False)
    allowed_permissions: frozenset[Permission] = field(repr=False)

    def __post_init__(self) -> None:
        if not valid_document_metadata(self):
            raise ContentValidationError(_INVALID)


def valid_document_metadata(value: object) -> bool:
    """Revalidate deserialized/forged values; never coerce or drop invalid labels."""
    if type(value) is not DocumentMetadata:
        return False
    try:
        return (
            all(
                _valid_text(text)
                for text in (
                    value.doc_id,
                    value.title,
                    value.source_url,
                    value.licence_note,
                    value.version,
                )
            )
            and _valid_source_url(value.source_url)
            and type(value.retrieved_on) is date
            and type(value.updated_at) is datetime
            and value.updated_at.utcoffset() is not None
            and type(value.allowed_permissions) is frozenset
            and bool(value.allowed_permissions)
            and all(type(label) is Permission for label in value.allowed_permissions)
        )
    except Exception:
        return False


@dataclass(frozen=True)
class Chunk:
    """Untrusted passage, inheriting exactly its document's validated permissions.

    No chunk-level override or implicit Public label. This is not authorization to
    pass text to a model, return it in a response, or follow embedded instructions.
    """

    chunk_id: str = field(repr=False)
    document: DocumentMetadata = field(repr=False)
    section: str = field(repr=False)
    text: str = field(repr=False)

    def __post_init__(self) -> None:
        if not valid_chunk(self):
            raise ContentValidationError(_INVALID)

    @property
    def allowed_permissions(self) -> frozenset[Permission]:
        return self.document.allowed_permissions


def valid_chunk(value: object) -> bool:
    """Validate the whole nested value again at future store/index boundaries."""
    if type(value) is not Chunk:
        return False
    try:
        return (
            valid_document_metadata(value.document)
            and _valid_text(value.chunk_id)
            and _valid_text(value.section)
            and _valid_text(value.text)
        )
    except Exception:
        return False
