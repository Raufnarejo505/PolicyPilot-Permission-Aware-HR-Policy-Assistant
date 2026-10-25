"""Authorized retrieval and extractive answer composition.

The client never supplies permissions. Each query resolves the current server-store
identity, filters retrieval by effective grants, independently authorizes candidates,
then re-reads selected chunks before exposure. Source reads resolve identity again.
Errors are generic and never include question or evidence text.
"""

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, Protocol

from policypilot.app.models.content import Chunk
from policypilot.app.models.domain import Permission
from policypilot.app.services.authorization import (
    AuthorizationError,
    AuthorizationService,
    effective_permissions,
)
from policypilot.app.services.content_store import ContentStore, ContentStoreError

_MESSAGE = "Query unavailable"
_SOURCE_MESSAGE = "Source not found"
_ABSTAINED = "I couldn't find this in the documents available to you."
_MAX_QUESTION = 2000
_MAX_SNIPPET = 500
_ESCAPE = re.compile(r"\\(.)")


class QueryError(Exception):
    """Generic query/store failure; never exposes evidence or identity details."""


class SourceNotFound(Exception):
    """Missing and unauthorized sources are deliberately indistinguishable."""


@dataclass(frozen=True)
class Citation:
    chunk_id: str = field(repr=False)
    document_id: str = field(repr=False)
    title: str = field(repr=False)
    section: str = field(repr=False)
    source_url: str = field(repr=False)
    snippet: str = field(repr=False)


@dataclass(frozen=True)
class Answer:
    status: Literal["answered", "abstained"]
    text: str = field(repr=False)
    citations: tuple[Citation, ...] = field(repr=False)


@dataclass(frozen=True)
class Source:
    chunk: Chunk = field(repr=False)


@dataclass(frozen=True)
class PolicySummary:
    document_id: str = field(repr=False)
    title: str = field(repr=False)
    source_url: str = field(repr=False)
    version: str = field(repr=False)
    updated_at: datetime = field(repr=False)
    sections: tuple[str, ...] = field(repr=False)
    chunks: int
    visible_via: tuple[Permission, ...]

    def __post_init__(self) -> None:
        if not (
            type(self.document_id) is str
            and bool(self.document_id.strip())
            and type(self.title) is str
            and bool(self.title.strip())
            and type(self.source_url) is str
            and bool(self.source_url.strip())
            and type(self.version) is str
            and bool(self.version.strip())
            and type(self.updated_at) is datetime
            and self.updated_at.tzinfo is not None
            and type(self.sections) is tuple
            and bool(self.sections)
            and all(type(section) is str and bool(section.strip()) for section in self.sections)
            and type(self.chunks) is int
            and self.chunks >= 1
            and type(self.visible_via) is tuple
            and bool(self.visible_via)
            and all(type(label) is Permission for label in self.visible_via)
        ):
            raise ValueError(_MESSAGE)


class AnswerGenerator(Protocol):
    def generate(self, chunks: tuple[Chunk, ...], top_n: int) -> tuple[str, tuple[Chunk, ...]]:
        """Return answer text and the exact chunks cited, in order."""
        ...


def _plain(text: str) -> str:
    """Presentation-only cleanup: hide extraction escapes and leading heading lines."""
    lines = [
        line for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")
    ]
    collapsed = " ".join(" ".join(lines).split())
    collapsed = _ESCAPE.sub(r"\1", collapsed).replace("**", "")
    return re.sub(r"^(?:[-*]|\d+[.)])\s+", "", collapsed)


def _first_sentence(text: str) -> str:
    collapsed = _plain(text)
    match = re.search(r"(?<=[.!?])\s", collapsed)
    sentence = collapsed if match is None else collapsed[: match.start() + 1]
    if len(sentence) < 20:
        sentence = collapsed
    return sentence[:400].strip()


class ExtractiveAnswerGenerator:
    """Deterministic local baseline; no model, no paid call, no invented text."""

    def generate(self, chunks: tuple[Chunk, ...], top_n: int) -> tuple[str, tuple[Chunk, ...]]:
        selected = chunks[:top_n]
        if not selected:
            return "", ()
        parts = [
            f"{_first_sentence(chunk.text)} [{index}]"
            for index, chunk in enumerate(selected, start=1)
        ]
        answer = " ".join(parts).strip()
        return (answer, selected) if answer else ("", ())


def _citation(chunk: Chunk) -> Citation:
    return Citation(
        chunk_id=chunk.chunk_id,
        document_id=chunk.document.doc_id,
        title=chunk.document.title,
        section=chunk.section,
        source_url=chunk.document.source_url,
        snippet=_plain(chunk.text)[:_MAX_SNIPPET],
    )


def _valid_question(question: object) -> bool:
    return (
        type(question) is str
        and 0 < len(question) <= _MAX_QUESTION
        and bool(question.strip())
        and "\x00" not in question
        and "\x7f" not in question
    )


class QueryService:
    def __init__(
        self,
        store: ContentStore,
        authorization: AuthorizationService,
        *,
        retrieval_limit: int = 20,
        top_n: int = 5,
        generator: AnswerGenerator | None = None,
    ) -> None:
        if (
            type(retrieval_limit) is not int
            or not 1 <= retrieval_limit <= 100
            or type(top_n) is not int
            or not 1 <= top_n <= 20
        ):
            raise ValueError(_MESSAGE)
        self._store = store
        self._authorization = authorization
        self._retrieval_limit = retrieval_limit
        self._top_n = top_n
        self._generator = generator if generator is not None else ExtractiveAnswerGenerator()

    @staticmethod
    def _abstained() -> Answer:
        return Answer("abstained", _ABSTAINED, ())

    def answer(self, user_id: str, question: str) -> Answer:
        """Resolve, filter, independently authorize, re-read, and answer."""
        try:
            if not _valid_question(question):
                raise QueryError(_MESSAGE)
            principal = self._authorization.resolve(user_id)
            permissions = effective_permissions(principal)
            candidates = self._store.search(question, permissions, limit=self._retrieval_limit)
            bundle = self._authorization.authorize(user_id, candidates)
            if not bundle.chunks:
                return self._abstained()
            # Re-read current chunks and re-authorize before composing an answer or
            # returning citations. This is the citation/source authorization step.
            current = tuple(
                stored
                for chunk in bundle.chunks
                if (stored := self._store.get_chunk(chunk.chunk_id, permissions)) is not None
            )
            checked = self._authorization.authorize(user_id, current)
            if not checked.chunks:
                return self._abstained()
            text, cited = self._generator.generate(checked.chunks, self._top_n)
            if not text or not cited:
                return self._abstained()
            return Answer("answered", text, tuple(_citation(chunk) for chunk in cited))
        except (AuthorizationError, ContentStoreError, QueryError, ValueError, TypeError):
            raise QueryError(_MESSAGE) from None

    def policies(self, user_id: str) -> tuple[PolicySummary, ...]:
        """List document metadata readable through the current server-side identity."""
        try:
            principal = self._authorization.resolve(user_id)
            permissions = effective_permissions(principal)
            return tuple(
                PolicySummary(
                    document_id=summary.document.doc_id,
                    title=summary.document.title,
                    source_url=summary.document.source_url,
                    version=summary.document.version,
                    updated_at=summary.document.updated_at,
                    sections=summary.sections,
                    chunks=summary.chunk_count,
                    visible_via=tuple(sorted(summary.matched_permissions)),
                )
                for summary in self._store.list_documents(permissions)
            )
        except (AuthorizationError, ContentStoreError, ValueError, TypeError):
            raise QueryError(_MESSAGE) from None

    def source(self, user_id: str, chunk_id: str) -> Source:
        """Resolve current identity and current content; never trust a prior bundle."""
        try:
            if (
                type(chunk_id) is not str
                or not 0 < len(chunk_id) <= 4096
                or not chunk_id.strip()
                or "\x00" in chunk_id
                or "\x7f" in chunk_id
            ):
                raise SourceNotFound(_SOURCE_MESSAGE)
            principal = self._authorization.resolve(user_id)
            permissions = effective_permissions(principal)
            chunk = self._store.get_chunk(chunk_id, permissions)
            if chunk is None:
                raise SourceNotFound(_SOURCE_MESSAGE)
            return Source(chunk)
        except SourceNotFound:
            raise
        except (AuthorizationError, ContentStoreError, ValueError, TypeError):
            raise QueryError(_MESSAGE) from None
