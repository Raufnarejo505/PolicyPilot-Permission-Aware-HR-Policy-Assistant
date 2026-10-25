"""Synthetic retrieval-evaluation harness tests; no frozen labels or dataset."""

import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from policypilot import manage
from policypilot.app.models.content import DocumentMetadata
from policypilot.app.models.domain import Permission
from policypilot.app.services.content_store import ContentStore
from policypilot.app.services.evaluation import (
    EvaluationError,
    EvaluationItem,
    evaluate_retrieval,
    load_items,
)
from policypilot.app.services.ingestion import IngestionService, SourceDocument

PUBLIC = frozenset({Permission.PUBLIC})
EMPLOYEE = frozenset({Permission.EMPLOYEE})
HR = frozenset({Permission.HR})


def prepared(name: str, labels: frozenset[Permission], text: str):
    metadata = DocumentMetadata(
        name,
        f"{name.title()} policy",
        f"https://example.invalid/{name}",
        date(2026, 10, 25),
        "Synthetic fixture; CC BY 4.0.",
        "v1",
        datetime(2026, 10, 25, tzinfo=UTC),
        labels,
    )
    return IngestionService().prepare(SourceDocument(metadata, text.encode()))


@pytest.fixture
def store(tmp_path: Path) -> ContentStore:
    result = ContentStore(tmp_path / "content.sqlite3")
    result.initialize()
    result.replace_many(
        (
            prepared("leave", PUBLIC, "Annual leave policy for employees."),
            prepared("remote", EMPLOYEE, "Remote work policy."),
            prepared("salary", HR, "Salary and compensation bands."),
        )
    )
    return result


def all_ids(store, text, permissions):
    return tuple(chunk.chunk_id for chunk in store.search(text, permissions))


def test_authorized_metrics_and_abstention(store):
    leave_id = all_ids(store, "annual leave", PUBLIC | EMPLOYEE)[0]
    item = EvaluationItem("annual leave", PUBLIC | EMPLOYEE, (leave_id,), ("salary",))
    report = evaluate_retrieval(store, (item,), k=5)
    assert report.items == 1
    assert report.answerable_items == 1
    assert report.recall_at_k == 1.0
    assert report.precision_at_k == pytest.approx(0.2)
    assert report.mrr_at_10 == 1.0
    assert report.unauthorized_or_forbidden_hits == 0
    abstain = EvaluationItem("salary", EMPLOYEE, expect_abstain=True, forbidden_doc_ids=("salary",))
    report = evaluate_retrieval(store, (item, abstain), k=5)
    assert report.abstention_items == 1
    assert report.abstention_accuracy == 1.0


def test_forbidden_document_and_unauthorized_candidate_are_counted(store):
    leave_chunk = store.search("annual leave", PUBLIC | EMPLOYEE)[0]
    forbidden = EvaluationItem(
        "annual leave",
        PUBLIC | EMPLOYEE,
        (leave_chunk.chunk_id,),
        (leave_chunk.document.doc_id,),
    )
    assert evaluate_retrieval(store, (forbidden,), k=5).unauthorized_or_forbidden_hits == 1
    hr_chunk = store.search("salary", HR)[0]

    class FakeStore:
        def search(self, *args, **kwargs):
            return (hr_chunk,)

    leak = EvaluationItem("salary", EMPLOYEE, expect_abstain=True)
    report = evaluate_retrieval(FakeStore(), (leak,), k=5)
    assert report.unauthorized_or_forbidden_hits == 1


def test_mrr_uses_rank_of_first_relevant(store):
    ids = all_ids(store, "policy", PUBLIC | EMPLOYEE)
    assert len(ids) >= 2
    item = EvaluationItem("policy", PUBLIC | EMPLOYEE, (ids[1],))
    report = evaluate_retrieval(store, (item,), k=5)
    assert report.mrr_at_10 == 0.5


def test_invalid_items_and_limits_reject(store):
    with pytest.raises(EvaluationError, match="^Evaluation failed$"):
        EvaluationItem("", PUBLIC)
    with pytest.raises(EvaluationError):
        EvaluationItem("question", frozenset())
    with pytest.raises(EvaluationError):
        EvaluationItem("question", PUBLIC, ("a", "a"))
    with pytest.raises(EvaluationError):
        evaluate_retrieval(store, (), k=5)
    with pytest.raises(EvaluationError):
        evaluate_retrieval(store, (EvaluationItem("q", PUBLIC),), k=0)
    with pytest.raises(EvaluationError):
        evaluate_retrieval(
            store,
            (EvaluationItem("q", PUBLIC, expect_abstain=False),),
            k=5,
        )


def test_jsonl_loader_accepts_only_explicit_items(tmp_path):
    path = tmp_path / "items.jsonl"
    path.write_text(
        json.dumps(
            {
                "question": "annual leave",
                "permissions": ["Public", "Employee"],
                "relevant_chunk_ids": ["chunk-1"],
                "forbidden_doc_ids": ["salary"],
                "expect_abstain": False,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    items = load_items(path)
    assert len(items) == 1
    assert items[0].permissions == PUBLIC | EMPLOYEE
    assert items[0].relevant_chunk_ids == ("chunk-1",)


@pytest.mark.parametrize(
    "line",
    [
        "",
        "not-json",
        "{}",
        '{"question":"q"}',
        '{"question":"q","permissions":["Unknown"]}',
        '{"question":"q","permissions":["Public","Public"]}',
        '{"question":"q","permissions":["Public"],"extra":1}',
        '{"question":"q","permissions":"Public"}',
        '{"question":"q","permissions":["Public"],"expect_abstain":"no"}',
        '{"question":"q","permissions":["Public"],"relevant_chunk_ids":"x"}',
    ],
)
def test_invalid_jsonl_rejects(tmp_path, line):
    path = tmp_path / "items.jsonl"
    path.write_text(line + "\n", encoding="utf-8")
    with pytest.raises(EvaluationError, match="^Evaluation failed$"):
        load_items(path)


def test_loader_rejects_missing_and_non_utf8_files(tmp_path):
    with pytest.raises(EvaluationError):
        load_items(tmp_path / "missing.jsonl")
    path = tmp_path / "bad.jsonl"
    path.write_bytes(b"\xff")
    with pytest.raises(EvaluationError):
        load_items(path)


def test_evaluation_cli_reports_only_aggregates(tmp_path, store, capsys):
    leave_id = all_ids(store, "annual leave", PUBLIC | EMPLOYEE)[0]
    items = tmp_path / "items.jsonl"
    items.write_text(
        json.dumps(
            {
                "question": "annual leave",
                "permissions": ["Public", "Employee"],
                "relevant_chunk_ids": [leave_id],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    assert (
        manage.main(
            [
                "--content-database",
                str(tmp_path / "content.sqlite3"),
                "evaluate-retrieval",
                "--items",
                str(items),
            ]
        )
        == 0
    )
    report = json.loads(capsys.readouterr().out)
    assert report["answerable_items"] == 1
    assert report["recall_at_k"] == 1.0
    assert "annual leave" not in json.dumps(report)
    assert (
        manage.main(
            [
                "--content-database",
                str(tmp_path / "content.sqlite3"),
                "evaluate-retrieval",
                "--items",
                str(tmp_path / "missing.jsonl"),
            ]
        )
        == 1
    )
    assert "Operation failed" in capsys.readouterr().err
