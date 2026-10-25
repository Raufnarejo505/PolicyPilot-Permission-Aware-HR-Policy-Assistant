"""Small offline retrieval-evaluation harness with no frozen labels.

Real evaluation items and labels remain human-authored and gated. This module only
accepts explicitly supplied items, computes authorized retrieval metrics, and never
writes or reads a frozen split manifest.
"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from policypilot.app.models.domain import Permission, Principal, valid_principal
from policypilot.app.services.authorization import can_read, effective_permissions
from policypilot.app.services.content_store import ContentStore, ContentStoreError

_MESSAGE = "Evaluation failed"
_MAX_ITEMS = 10_000
_MAX_FILE = 16_777_216


class EvaluationError(ValueError):
    """Content-free evaluation failure."""


@dataclass(frozen=True)
class EvaluationItem:
    question: str
    permissions: frozenset[Permission]
    relevant_chunk_ids: tuple[str, ...] = ()
    forbidden_doc_ids: tuple[str, ...] = ()
    expect_abstain: bool = False

    def __post_init__(self) -> None:
        if not _valid_item(self):
            raise EvaluationError(_MESSAGE)


def _strings(value: object, *, allow_empty: bool = True) -> bool:
    return (
        type(value) is tuple
        and (allow_empty or bool(value))
        and all(type(item) is str and bool(item.strip()) for item in value)
        and len(set(value)) == len(value)
    )


def _valid_item(item: object) -> bool:
    try:
        return (
            type(item) is EvaluationItem
            and type(item.question) is str
            and 0 < len(item.question) <= 2000
            and bool(item.question.strip())
            and "\x00" not in item.question
            and type(item.permissions) is frozenset
            and bool(item.permissions)
            and all(type(label) is Permission for label in item.permissions)
            and _strings(item.relevant_chunk_ids)
            and _strings(item.forbidden_doc_ids)
            and type(item.expect_abstain) is bool
        )
    except AttributeError:
        return False


@dataclass(frozen=True)
class RetrievalReport:
    items: int
    answerable_items: int
    recall_at_k: float
    precision_at_k: float
    mrr_at_10: float
    abstention_items: int
    abstention_accuracy: float | None
    unauthorized_or_forbidden_hits: int


def _principal(item: EvaluationItem) -> Principal:
    principal = Principal("evaluation", item.permissions)
    if not valid_principal(principal):
        raise EvaluationError(_MESSAGE)
    return principal


def evaluate_retrieval(
    store: ContentStore, items: tuple[EvaluationItem, ...], *, k: int = 5
) -> RetrievalReport:
    """Compute authorized metrics; never include question or passage text in errors."""
    try:
        if (
            type(items) is not tuple
            or not items
            or len(items) > _MAX_ITEMS
            or type(k) is not int
            or not 1 <= k <= 100
            or not all(_valid_item(item) for item in items)
        ):
            raise EvaluationError(_MESSAGE)
        recall = precision = reciprocal = 0.0
        answerable = abstention_items = abstention_correct = leaks = 0
        for item in items:
            principal = _principal(item)
            candidates = store.search(
                item.question,
                effective_permissions(principal),
                limit=max(k, 10),
            )
            permitted = tuple(
                chunk for chunk in candidates if can_read(principal, chunk.allowed_permissions)
            )
            leaks += len(candidates) - len(permitted)
            forbidden = set(item.forbidden_doc_ids)
            leaks += sum(chunk.document.doc_id in forbidden for chunk in permitted)
            if item.expect_abstain:
                abstention_items += 1
                abstention_correct += not permitted
                continue
            if not item.relevant_chunk_ids:
                continue
            answerable += 1
            relevant = set(item.relevant_chunk_ids)
            top_k = {chunk.chunk_id for chunk in permitted[:k]}
            hits = relevant & top_k
            recall += len(hits) / len(relevant)
            precision += len(hits) / k
            for rank, chunk in enumerate(permitted[:10], start=1):
                if chunk.chunk_id in relevant:
                    reciprocal += 1 / rank
                    break
        if not answerable and not abstention_items:
            raise EvaluationError(_MESSAGE)
        return RetrievalReport(
            items=len(items),
            answerable_items=answerable,
            recall_at_k=recall / answerable if answerable else 0.0,
            precision_at_k=precision / answerable if answerable else 0.0,
            mrr_at_10=reciprocal / answerable if answerable else 0.0,
            abstention_items=abstention_items,
            abstention_accuracy=(
                abstention_correct / abstention_items if abstention_items else None
            ),
            unauthorized_or_forbidden_hits=leaks,
        )
    except EvaluationError:
        raise
    except (ContentStoreError, ValueError, TypeError, KeyError):
        raise EvaluationError(_MESSAGE) from None


def _load_line(line: str) -> EvaluationItem:
    data: Any = json.loads(line)
    if type(data) is not dict:
        raise EvaluationError(_MESSAGE)
    required = {"question", "permissions"}
    optional = {"relevant_chunk_ids", "forbidden_doc_ids", "expect_abstain"}
    if not required.issubset(data) or not set(data).issubset(required | optional):
        raise EvaluationError(_MESSAGE)
    question = data["question"]
    labels = data["permissions"]
    relevant = data.get("relevant_chunk_ids", [])
    forbidden = data.get("forbidden_doc_ids", [])
    abstain = data.get("expect_abstain", False)
    if (
        type(question) is not str
        or type(labels) is not list
        or type(relevant) is not list
        or type(forbidden) is not list
        or type(abstain) is not bool
        or any(type(label) is not str for label in labels)
        or len(set(labels)) != len(labels)
        or any(type(value) is not str for value in (*relevant, *forbidden))
    ):
        raise EvaluationError(_MESSAGE)
    return EvaluationItem(
        question=question,
        permissions=frozenset(Permission(label) for label in labels),
        relevant_chunk_ids=tuple(relevant),
        forbidden_doc_ids=tuple(forbidden),
        expect_abstain=abstain,
    )


def load_items(path: Path) -> tuple[EvaluationItem, ...]:
    """Load an explicit JSONL item file; no paths or labels are discovered implicitly."""
    try:
        if not isinstance(path, Path) or not path.is_file():
            raise EvaluationError(_MESSAGE)
        data = path.read_bytes()
        if not 0 < len(data) <= _MAX_FILE:
            raise EvaluationError(_MESSAGE)
        items = tuple(
            _load_line(line) for line in data.decode("utf-8").splitlines() if line.strip()
        )
        if not 1 <= len(items) <= _MAX_ITEMS:
            raise EvaluationError(_MESSAGE)
        return items
    except EvaluationError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError, TypeError):
        raise EvaluationError(_MESSAGE) from None
