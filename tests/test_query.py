"""Authorized query and citation re-read tests; synthetic fixtures only."""

from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from policypilot.app.models.content import DocumentMetadata
from policypilot.app.models.domain import Permission, Principal
from policypilot.app.services.authorization import AuthorizationService
from policypilot.app.services.content_store import ContentStore, ContentStoreError
from policypilot.app.services.ingestion import IngestionService, SourceDocument
from policypilot.app.services.query import (
    Answer,
    ExtractiveAnswerGenerator,
    QueryError,
    QueryService,
    SourceNotFound,
)

PUBLIC = frozenset({Permission.PUBLIC})
EMPLOYEE = frozenset({Permission.EMPLOYEE})
HR = frozenset({Permission.HR})


class Users:
    def __init__(self, principal: Principal | None) -> None:
        self.principal = principal

    def get_user(self, user_id: str) -> Principal | None:
        if self.principal is not None and self.principal.user_id == user_id:
            return self.principal
        return None


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
            prepared("hr", HR, "Secret HR leave policy for executives."),
            prepared("public", PUBLIC, "Public leave policy for everyone."),
        )
    )
    return result


def service(store, principal: Principal | None = None) -> tuple[QueryService, Users]:
    users = Users(principal or Principal("alice"))
    auth = AuthorizationService(users)
    return QueryService(store, auth, retrieval_limit=10, top_n=3), users


def test_employee_gets_public_evidence_but_never_hr_text(store):
    query, _ = service(store)
    answer = query.answer("alice", "leave")
    assert answer.status == "answered"
    assert answer.citations
    assert all("Secret HR" not in citation.snippet for citation in answer.citations)
    assert "Secret HR" not in answer.text
    assert "Public leave" in answer.text
    assert "[1]" in answer.text


def test_hr_identity_receives_hr_evidence(store):
    query, _ = service(store, Principal("alice", frozenset({Permission.HR})))
    answer = query.answer("alice", "leave")
    assert answer.status == "answered"
    assert any("Secret HR" in citation.snippet for citation in answer.citations)


def test_no_authorized_evidence_abstains_without_denied_text(store):
    query, _ = service(store)
    answer = query.answer("alice", "executives")
    assert answer == Answer(
        "abstained", "I couldn't find this in the documents available to you.", ()
    )
    assert "Secret HR" not in repr(answer)


def test_permission_changes_apply_to_query_and_source(store):
    query, users = service(store, Principal("alice", frozenset({Permission.HR})))
    chunk = store.search("executives", frozenset({Permission.HR}))[0]
    assert query.source("alice", chunk.chunk_id).chunk.text == chunk.text
    users.principal = Principal("alice")
    assert query.answer("alice", "executives").status == "abstained"
    with pytest.raises(SourceNotFound):
        query.source("alice", chunk.chunk_id)


def test_source_requires_current_authorization_and_missing_is_indistinguishable(store):
    query, _ = service(store)
    hr_chunk = store.search("executives", frozenset({Permission.HR}))[0]
    with pytest.raises(SourceNotFound) as unauthorized:
        query.source("alice", hr_chunk.chunk_id)
    with pytest.raises(SourceNotFound) as missing:
        query.source("alice", "does-not-exist")
    assert str(unauthorized.value) == str(missing.value) == "Source not found"
    public_chunk = store.search("everyone", frozenset({Permission.PUBLIC}))[0]
    assert query.source("alice", public_chunk.chunk_id).chunk.text


@pytest.mark.parametrize("chunk_id", ["", " ", "x" * 4097, None, 1, "bad\x00", "bad\x7f"])
def test_invalid_source_identifier_is_not_found(store, chunk_id):
    query, _ = service(store)
    with pytest.raises(SourceNotFound):
        query.source("alice", chunk_id)


@pytest.mark.parametrize("question", ["", " ", "x" * 2001, None, 1, "bad\x00", "bad\x7f"])
def test_invalid_question_is_generic(store, question):
    query, _ = service(store)
    with pytest.raises(QueryError, match="^Query unavailable$"):
        query.answer("alice", question)


def test_unknown_identity_and_store_failure_are_generic(tmp_path):
    query, users = service(ContentStore(tmp_path / "missing.sqlite3"), Principal("alice"))
    with pytest.raises(QueryError, match="^Query unavailable$"):
        query.answer("alice", "leave")
    users.principal = None
    with pytest.raises(QueryError, match="^Query unavailable$"):
        query.answer("alice", "leave")


def test_store_failure_does_not_leak_evidence(tmp_path):
    class FailingStore:
        def search(self, *args, **kwargs):
            raise ContentStoreError("sensitive store detail")

        def get_chunk(self, *args, **kwargs):
            raise ContentStoreError("sensitive store detail")

    query = QueryService(FailingStore(), AuthorizationService(Users(Principal("alice"))))
    with pytest.raises(QueryError) as caught:
        query.answer("alice", "leave")
    assert "sensitive" not in str(caught.value)
    with pytest.raises(QueryError):
        query.source("alice", "chunk")


def test_custom_generator_controls_answer_and_citations(store):
    class Generator:
        def generate(self, chunks, top_n):
            return "Custom answer", chunks[:1]

    query, _ = service(store)
    query = QueryService(
        store,
        AuthorizationService(Users(Principal("alice"))),
        generator=Generator(),
    )
    answer = query.answer("alice", "leave")
    assert answer.text == "Custom answer"
    assert len(answer.citations) == 1


@pytest.mark.parametrize(
    "generator_result",
    [("", ()), ("No citations", ()), ("", None)],
)
def test_generator_without_evidence_abstains(store, generator_result):
    class Generator:
        def generate(self, chunks, top_n):
            return generator_result

    query = QueryService(
        store,
        AuthorizationService(Users(Principal("alice"))),
        generator=Generator(),
    )
    assert query.answer("alice", "leave").status == "abstained"


def test_repr_redacts_answer_citation_and_source(store):
    query, _ = service(store)
    answer = query.answer("alice", "leave")
    assert repr(answer) == "Answer(status='answered')"
    assert repr(answer.citations[0]) == "Citation()"
    chunk = store.search("everyone", frozenset({Permission.PUBLIC}))[0]
    assert repr(query.source("alice", chunk.chunk_id)) == "Source()"


def test_extractive_generator_limits_sentences_and_citations(store):
    generator = ExtractiveAnswerGenerator()
    chunks = store.search("leave", frozenset({Permission.PUBLIC, Permission.EMPLOYEE}))
    text, cited = generator.generate(chunks, 1)
    assert cited == chunks[:1]
    assert text.endswith("[1]")


@pytest.mark.parametrize(
    ("retrieval_limit", "top_n"),
    [(0, 1), (101, 1), (True, 1), (20, 0), (20, 21), (20, True), (20.0, 1), (20, 5.0)],
)
def test_invalid_service_limits_reject(retrieval_limit, top_n):
    with pytest.raises(ValueError, match="^Query unavailable$"):
        QueryService(None, None, retrieval_limit=retrieval_limit, top_n=top_n)


def test_policies_list_only_currently_authorized_metadata(store):
    query, users = service(store)
    policies = query.policies("alice")
    assert [policy.title for policy in policies] == ["Public policy"]
    assert policies[0].sections
    assert policies[0].chunks >= 1
    assert policies[0].visible_via == (Permission.PUBLIC,)
    assert "Public leave policy" not in repr(policies[0])
    users.principal = Principal("alice", frozenset({Permission.HR}))
    assert {policy.title for policy in query.policies("alice")} == {
        "Hr policy",
        "Public policy",
    }


def test_policies_fail_generically_for_unknown_identity_and_store_errors(tmp_path):
    query, users = service(store=None)  # type: ignore[arg-type]
    users.principal = None
    with pytest.raises(QueryError, match="^Query unavailable$"):
        query.policies("alice")

    class FailingStore:
        def list_documents(self, permissions):
            raise ContentStoreError("sensitive store detail")

    failing = QueryService(FailingStore(), AuthorizationService(Users(Principal("alice"))))
    with pytest.raises(QueryError) as caught:
        failing.policies("alice")
    assert "sensitive" not in str(caught.value)
