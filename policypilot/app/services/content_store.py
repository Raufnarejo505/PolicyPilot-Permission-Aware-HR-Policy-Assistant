"""Local SQLite STAGING lexical adapter, not the planned Weaviate hybrid stack.

Grants must be resolved by the caller from current server identity. Returned chunks
remain untrusted candidates: independently authorize before reranking/model input,
and resolve identity and authorize again for citations and source reads.
"""

import json
import os
import re
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from functools import wraps
from pathlib import Path

from policypilot.app.models.content import (
    Chunk,
    DocumentMetadata,
    valid_chunk,
    valid_document_metadata,
)
from policypilot.app.models.domain import Permission
from policypilot.app.services.ingestion import PreparedDocument

_MESSAGE = "Content store unavailable"
_MAX_OUTPUT = 2_097_152
_FIELDS = {
    "doc_id",
    "title",
    "source_url",
    "retrieved_on",
    "licence_note",
    "version",
    "updated_at",
    "allowed_permissions",
}
_SCHEMA = (
    """CREATE TABLE documents (
        doc_id TEXT PRIMARY KEY NOT NULL,
        metadata TEXT NOT NULL,
        provenance TEXT NOT NULL
    )""",
    """CREATE TABLE chunks (
        chunk_id TEXT PRIMARY KEY NOT NULL,
        doc_id TEXT NOT NULL REFERENCES documents(doc_id) ON DELETE CASCADE,
        section TEXT NOT NULL,
        text TEXT NOT NULL,
        text_fold TEXT NOT NULL
    )""",
    """CREATE TABLE labels (
        doc_id TEXT NOT NULL REFERENCES documents(doc_id) ON DELETE CASCADE,
        permission TEXT NOT NULL,
        PRIMARY KEY (doc_id, permission)
    )""",
    "CREATE INDEX chunks_doc_id ON chunks(doc_id)",
    "CREATE INDEX labels_permission ON labels(permission, doc_id)",
)
_APPLICATION = 0x50504353
_STOPWORDS = frozenset(
    {
        "a",
        "about",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "can",
        "could",
        "did",
        "do",
        "does",
        "for",
        "from",
        "get",
        "has",
        "have",
        "how",
        "i",
        "if",
        "in",
        "into",
        "is",
        "it",
        "many",
        "me",
        "much",
        "my",
        "no",
        "not",
        "of",
        "on",
        "or",
        "our",
        "please",
        "should",
        "tell",
        "than",
        "that",
        "the",
        "their",
        "them",
        "then",
        "there",
        "this",
        "to",
        "was",
        "we",
        "were",
        "what",
        "when",
        "where",
        "which",
        "who",
        "why",
        "will",
        "with",
        "would",
        "yes",
        "you",
        "your",
    }
)


class ContentStoreError(ValueError):
    """Content-free failure, including invalid input and unavailable/corrupt stores."""


@dataclass(frozen=True)
class DocumentSummary:
    """Authorized document metadata without passage text."""

    document: DocumentMetadata = field(repr=False)
    chunk_count: int
    sections: tuple[str, ...] = field(repr=False)
    matched_permissions: frozenset[Permission]

    def __post_init__(self) -> None:
        if not (
            valid_document_metadata(self.document)
            and type(self.chunk_count) is int
            and self.chunk_count >= 1
            and type(self.sections) is tuple
            and bool(self.sections)
            and len(self.sections) <= 4096
            and all(_text(section, 4096) for section in self.sections)
            and type(self.matched_permissions) is frozenset
            and bool(self.matched_permissions)
            and all(type(label) is Permission for label in self.matched_permissions)
            and self.matched_permissions <= self.document.allowed_permissions
        ):
            raise ValueError(_MESSAGE)


def _safe[**P, T](function: Callable[P, T]) -> Callable[P, T]:
    @wraps(function)
    def guarded(*args: P.args, **kwargs: P.kwargs) -> T:
        try:
            return function(*args, **kwargs)
        except Exception:  # noqa: S110 -- deliberately never log restricted exception data.
            pass
        # Outside the handler: even __context__ must not retain restricted values.
        raise ContentStoreError(_MESSAGE)

    return guarded


def _require(condition: bool) -> None:
    if not condition:
        raise ContentStoreError(_MESSAGE)


def _text(value: object, maximum: int) -> bool:
    return (
        type(value) is str
        and 0 < len(value) <= maximum
        and bool(value.strip())
        and "\x00" not in value
        and bool(value.encode("utf-8"))
    )


def _grants(value: object) -> None:
    _require(
        type(value) is frozenset
        and bool(value)
        and all(type(label) is Permission for label in value)
    )


def _metadata(value: DocumentMetadata) -> None:
    _require(valid_document_metadata(value))
    _require(
        all(
            _text(text, 4096)
            for text in (
                value.doc_id,
                value.title,
                value.source_url,
                value.licence_note,
                value.version,
            )
        )
    )


def _lineage(document: DocumentMetadata, provenance: DocumentMetadata) -> None:
    _metadata(document)
    _metadata(provenance)
    _require(replace(provenance, doc_id=document.doc_id, version=document.version) == document)


def _encode(value: DocumentMetadata) -> str:
    _metadata(value)
    return json.dumps(
        {
            "doc_id": value.doc_id,
            "title": value.title,
            "source_url": value.source_url,
            "retrieved_on": value.retrieved_on.isoformat(),
            "licence_note": value.licence_note,
            "version": value.version,
            "updated_at": value.updated_at.isoformat(),
            "allowed_permissions": sorted(label.value for label in value.allowed_permissions),
        },
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
    )


def _pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        _require(key not in result)
        result[key] = value
    return result


def _constant(value: str) -> None:
    raise ContentStoreError(_MESSAGE)


def _decode(encoded: object) -> DocumentMetadata:
    if type(encoded) is not str:
        raise ContentStoreError(_MESSAGE)
    # Bound before parsing; ensure_ascii can expand each character to 12 bytes.
    _require(len(encoded) <= 12 * 4096 * 8)
    data = json.loads(encoded, object_pairs_hook=_pairs, parse_constant=_constant)
    _require(type(data) is dict and data.keys() == _FIELDS)
    _require(all(type(data[key]) is str for key in _FIELDS - {"allowed_permissions"}))
    labels = data["allowed_permissions"]
    _require(type(labels) is list and 1 <= len(labels) <= len(Permission))
    _require(all(type(label) is str for label in labels))
    _require(len(set(labels)) == len(labels))
    value = DocumentMetadata(
        doc_id=data["doc_id"],
        title=data["title"],
        source_url=data["source_url"],
        retrieved_on=date.fromisoformat(data["retrieved_on"]),
        licence_note=data["licence_note"],
        version=data["version"],
        updated_at=datetime.fromisoformat(data["updated_at"]),
        allowed_permissions=frozenset(Permission(label) for label in labels),
    )
    _metadata(value)
    return value


def _prepared(value: PreparedDocument) -> None:
    _require(type(value) is PreparedDocument)
    _lineage(value.document, value.provenance)
    _require(type(value.chunks) is tuple and 1 <= len(value.chunks) <= 4096)
    ids: set[str] = set()
    text_size = section_size = 0
    for chunk in value.chunks:
        _require(valid_chunk(chunk))
        _metadata(chunk.document)
        _require(chunk.document == value.document and _text(chunk.chunk_id, 4096))
        _require(chunk.chunk_id not in ids)
        ids.add(chunk.chunk_id)
        text_size += len(chunk.text)
        section_size += len(chunk.section)
        _require(text_size <= _MAX_OUTPUT and section_size <= _MAX_OUTPUT)


class ContentStore:
    @_safe
    def __init__(self, path: Path) -> None:
        _require(isinstance(path, Path))
        self._path = path.absolute()

    @staticmethod
    def _check_schema(connection: sqlite3.Connection) -> None:
        _require(connection.execute("PRAGMA user_version").fetchone()[0] == 1)
        _require(connection.execute("PRAGMA application_id").fetchone()[0] == _APPLICATION)
        actual = {
            row[0]
            for row in connection.execute("SELECT sql FROM sqlite_master WHERE sql IS NOT NULL")
        }
        _require(actual == set(_SCHEMA))

    @contextmanager
    def _connection(
        self, *, write: bool = False, initialize: bool = False
    ) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(f"{self._path.as_uri()}?mode=rw", uri=True)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            _require(connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1)
            with connection:
                connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
                if not initialize:
                    self._check_schema(connection)
                yield connection
        finally:
            connection.close()

    @_safe
    def initialize(self) -> None:
        """Explicitly create a private DB; never adopt an unrelated existing schema."""
        try:
            descriptor = os.open(self._path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            pass
        else:
            os.close(descriptor)
        with self._connection(write=True, initialize=True) as connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version == 0:
                count = connection.execute("SELECT count(*) FROM sqlite_master").fetchone()[0]
                _require(count == 0)
                _require(connection.execute("PRAGMA application_id").fetchone()[0] == 0)
                for statement in _SCHEMA:
                    connection.execute(statement)
                connection.execute("PRAGMA user_version = 1")
                connection.execute("PRAGMA application_id = 1347437395")
            self._check_schema(connection)

    @staticmethod
    def _write(connection: sqlite3.Connection, prepared: PreparedDocument) -> None:
        document = prepared.document
        metadata, provenance = _encode(document), _encode(prepared.provenance)
        connection.execute("DELETE FROM documents WHERE doc_id = ?", (document.doc_id,))
        connection.execute(
            "INSERT INTO documents VALUES (?, ?, ?)",
            (
                document.doc_id,
                metadata,
                provenance,
            ),
        )
        connection.executemany(
            "INSERT INTO labels VALUES (?, ?)",
            ((document.doc_id, label.value) for label in sorted(document.allowed_permissions)),
        )
        # Global chunk primary key rejects cross-document collisions; rollback
        # restores even the previous version deleted above.
        connection.executemany(
            "INSERT INTO chunks VALUES (?, ?, ?, ?, ?)",
            (
                (
                    chunk.chunk_id,
                    document.doc_id,
                    chunk.section,
                    chunk.text,
                    chunk.text.casefold(),
                )
                for chunk in prepared.chunks
            ),
        )

    @_safe
    def replace(self, prepared: PreparedDocument) -> None:
        """Atomically replace one document, all provenance, labels and chunks."""
        _prepared(prepared)
        with self._connection(write=True) as connection:
            self._write(connection, prepared)

    @_safe
    def replace_many(self, prepared_documents: tuple[PreparedDocument, ...]) -> None:
        """Atomically publish a nonempty batch; no document is visible on failure."""
        _require(type(prepared_documents) is tuple and 1 <= len(prepared_documents) <= 100)
        document_ids: set[str] = set()
        chunk_ids: set[str] = set()
        for prepared in prepared_documents:
            _prepared(prepared)
            _require(prepared.document.doc_id not in document_ids)
            document_ids.add(prepared.document.doc_id)
            for chunk in prepared.chunks:
                _require(chunk.chunk_id not in chunk_ids)
                chunk_ids.add(chunk.chunk_id)
        with self._connection(write=True) as connection:
            for prepared in prepared_documents:
                self._write(connection, prepared)

    @_safe
    def counts(self) -> tuple[int, int]:
        """Return document and chunk counts without exposing any content."""
        with self._connection() as connection:
            documents = connection.execute("SELECT count(*) FROM documents").fetchone()[0]
            chunks = connection.execute("SELECT count(*) FROM chunks").fetchone()[0]
            _require(type(documents) is int and documents >= 0)
            _require(type(chunks) is int and chunks >= 0)
            return documents, chunks

    @_safe
    def list_documents(self, permissions: frozenset[Permission]) -> tuple[DocumentSummary, ...]:
        """List only documents readable through the supplied current grants.

        Returns metadata, section labels, and counts. It never returns passage text.
        """
        _grants(permissions)
        with self._connection() as connection:
            rows = connection.execute(
                """SELECT d.doc_id, d.metadata, d.provenance,
                    (SELECT count(*) FROM chunks c WHERE c.doc_id = d.doc_id) AS chunk_count
                FROM documents d
                WHERE EXISTS (
                    SELECT 1 FROM labels l JOIN json_each(?) g ON l.permission = g.value
                    WHERE l.doc_id = d.doc_id
                )
                ORDER BY lower(json_extract(d.metadata, '$.title')) COLLATE BINARY,
                    d.doc_id COLLATE BINARY
                LIMIT 1001""",
                (json.dumps(sorted(permissions)),),
            ).fetchall()
            _require(len(rows) <= 1000)
            summaries: list[DocumentSummary] = []
            for row in rows:
                document = _decode(row["metadata"])
                provenance = _decode(row["provenance"])
                _lineage(document, provenance)
                _require(document.doc_id == row["doc_id"])
                labels = {
                    item[0]
                    for item in connection.execute(
                        "SELECT permission FROM labels WHERE doc_id = ?", (document.doc_id,)
                    )
                }
                _require(
                    all(type(label) is str for label in labels)
                    and len(labels) == len(document.allowed_permissions)
                    and labels == {label.value for label in document.allowed_permissions}
                )
                matched = document.allowed_permissions & permissions
                _require(bool(matched))
                sections = tuple(
                    row[0]
                    for row in connection.execute(
                        """SELECT DISTINCT section FROM chunks WHERE doc_id = ?
                        ORDER BY section COLLATE BINARY""",
                        (document.doc_id,),
                    )
                )
                _require(
                    0 < len(sections) <= 4096
                    and all(_text(section, 4096) for section in sections)
                    and sum(len(section) for section in sections) <= _MAX_OUTPUT
                )
                summaries.append(
                    DocumentSummary(
                        document=document,
                        chunk_count=row["chunk_count"],
                        sections=sections,
                        matched_permissions=frozenset(matched),
                    )
                )
            return tuple(summaries)

    @_safe
    def delete(self, doc_id: str) -> None:
        _require(_text(doc_id, 4096))
        with self._connection(write=True) as connection:
            connection.execute("DELETE FROM documents WHERE doc_id = ?", (doc_id,))

    @staticmethod
    def _chunk(
        connection: sqlite3.Connection, row: sqlite3.Row, permissions: frozenset[Permission]
    ) -> Chunk:
        document, provenance = _decode(row["metadata"]), _decode(row["provenance"])
        _lineage(document, provenance)
        _require(document.doc_id == row["doc_id"])
        labels = tuple(
            item[0]
            for item in connection.execute(
                "SELECT permission FROM labels WHERE doc_id = ?", (document.doc_id,)
            )
        )
        _require(all(type(label) is str for label in labels))
        _require(len(labels) == len(set(labels)))
        _require(set(labels) == {label.value for label in document.allowed_permissions})
        _require(bool(document.allowed_permissions & permissions))
        chunk = Chunk(row["chunk_id"], document, row["section"], row["text"])
        _require(valid_chunk(chunk) and _text(chunk.chunk_id, 4096))
        _require(len(chunk.text) <= _MAX_OUTPUT and len(chunk.section) <= _MAX_OUTPUT)
        _require(type(row["text_fold"]) is str and row["text_fold"] == chunk.text.casefold())
        return chunk

    @_safe
    def search(
        self, query: str, permissions: frozenset[Permission], *, limit: int = 20
    ) -> tuple[Chunk, ...]:
        """Permission-filtered lexical OR search; title and section matches rank higher."""
        _grants(permissions)
        _require(_text(query, 1024) and type(limit) is int and 1 <= limit <= 100)
        words = {word.casefold() for word in re.findall(r"[^\W_]+", query)}
        filtered = words - _STOPWORDS
        terms = sorted(filtered or words)
        _require(1 <= len(terms) <= 16 and all(len(term) <= 64 for term in terms))
        encoded_terms = json.dumps(terms)
        with self._connection() as connection:
            rows = connection.execute(
                """SELECT c.*, d.metadata, d.provenance,
                    (SELECT count(*) FROM json_each(?) t
                     WHERE instr(c.text_fold, t.value) > 0)
                    + 3 * (SELECT count(*) FROM json_each(?) t
                        WHERE instr(lower(json_extract(d.metadata, '$.title')), t.value) > 0)
                    + 2 * (SELECT count(*) FROM json_each(?) t
                        WHERE instr(lower(c.section), t.value) > 0) AS matches
                FROM chunks c JOIN documents d ON d.doc_id = c.doc_id
                WHERE EXISTS (
                    SELECT 1 FROM labels l JOIN json_each(?) g ON l.permission = g.value
                    WHERE l.doc_id = c.doc_id
                ) AND EXISTS (
                    SELECT 1 FROM json_each(?) t
                    WHERE instr(c.text_fold, t.value) > 0
                       OR instr(lower(json_extract(d.metadata, '$.title')), t.value) > 0
                       OR instr(lower(c.section), t.value) > 0
                )
                ORDER BY matches DESC, c.chunk_id COLLATE BINARY ASC LIMIT ?""",
                (
                    encoded_terms,
                    encoded_terms,
                    encoded_terms,
                    json.dumps(sorted(permissions)),
                    encoded_terms,
                    limit,
                ),
            ).fetchall()
            return tuple(self._chunk(connection, row, permissions) for row in rows)

    @_safe
    def get_chunk(self, chunk_id: str, permissions: frozenset[Permission]) -> Chunk | None:
        _grants(permissions)
        _require(_text(chunk_id, 4096))
        with self._connection() as connection:
            row = connection.execute(
                """SELECT c.*, d.metadata, d.provenance
                FROM chunks c JOIN documents d ON d.doc_id = c.doc_id
                WHERE c.chunk_id = ? AND EXISTS (
                    SELECT 1 FROM labels l JOIN json_each(?) g ON l.permission = g.value
                    WHERE l.doc_id = c.doc_id
                )""",
                (chunk_id, json.dumps(sorted(permissions))),
            ).fetchone()
            return None if row is None else self._chunk(connection, row, permissions)
