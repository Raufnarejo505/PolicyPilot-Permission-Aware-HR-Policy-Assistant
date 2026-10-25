"""Synthetic SQLite staging tests; no real corpus, providers or evaluation data."""

import json
import os
import sqlite3
import stat
from collections.abc import Callable, Iterator
from contextlib import closing, contextmanager
from dataclasses import FrozenInstanceError, replace
from datetime import UTC, date, datetime
from itertools import combinations
from pathlib import Path
from typing import Any

import pytest

from policypilot.app.models.content import Chunk, DocumentMetadata
from policypilot.app.models.domain import Permission, SystemRole
from policypilot.app.services.content_store import ContentStore, ContentStoreError
from policypilot.app.services.ingestion import IngestionService, PreparedDocument, SourceDocument

PUBLIC = frozenset({Permission.PUBLIC})
ALL = frozenset(Permission)


def prepared(
    name: str = "one",
    labels: frozenset[Permission] = PUBLIC,
    text: str = "synthetic leave policy",
    *,
    url: str | None = None,
) -> PreparedDocument:
    metadata = DocumentMetadata(
        name,
        "Synthetic policy",
        url or f"https://example.invalid/{name}",
        date(2026, 10, 21),
        "Synthetic test fixture only",
        "upstream-v1",
        datetime(2026, 10, 21, tzinfo=UTC),
        labels,
    )
    return IngestionService().prepare(SourceDocument(metadata, text.encode()))


@pytest.fixture
def path(tmp_path: Path) -> Path:
    return tmp_path / "staging ?#.sqlite3"


@pytest.fixture
def store(path: Path) -> ContentStore:
    result = ContentStore(path)
    result.initialize()
    return result


def execute(path: Path, sql: str, values: tuple[object, ...] = ()) -> None:
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute(sql, values)


def rows(path: Path, sql: str) -> list[Any]:
    with closing(sqlite3.connect(path)) as connection:
        return connection.execute(sql).fetchall()


@contextmanager
def unavailable() -> Iterator[None]:
    with pytest.raises(ContentStoreError) as caught:
        yield
    error = caught.value
    assert str(error) == "Content store unavailable"
    assert repr(error) == "ContentStoreError('Content store unavailable')"
    assert error.args == ("Content store unavailable",)
    assert error.__context__ is None
    assert error.__cause__ is None


def test_roundtrip_idempotency_provenance_immutability(store: ContentStore, path: Path) -> None:
    value = prepared()
    store.replace(value)
    store.replace(value)
    store.initialize()
    reopened = ContentStore(path)
    assert reopened.search("LEAVE", PUBLIC) == value.chunks
    chunk = reopened.get_chunk(value.chunks[0].chunk_id, PUBLIC)
    assert chunk == value.chunks[0]
    assert chunk is not value.chunks[0]
    assert chunk is not None
    assert type(chunk.document.retrieved_on) is date
    assert type(chunk.document.updated_at) is datetime
    with pytest.raises(FrozenInstanceError):
        chunk.text = "changed"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        chunk.document.title = "changed"  # type: ignore[misc]
    metadata, provenance = rows(path, "SELECT metadata, provenance FROM documents")[0]
    assert json.loads(provenance)["doc_id"] == value.provenance.doc_id
    assert json.loads(provenance)["version"] == "upstream-v1"
    assert json.loads(metadata)["doc_id"] == value.document.doc_id
    assert "synthetic" not in repr(chunk)
    assert "example.invalid" not in repr(store)
    assert len(rows(path, "SELECT * FROM chunks")) == 1


def permission_sets() -> list[frozenset[Permission]]:
    return [frozenset(group) for size in range(1, 6) for group in combinations(Permission, size)]


def test_all_permission_combinations_union_not_role_or_baseline(store: ContentStore) -> None:
    values = [prepared(str(index), labels) for index, labels in enumerate(permission_sets())]
    for value in values:
        store.replace(value)
    for grants in permission_sets():
        expected = tuple(
            sorted(
                (
                    value.chunks[0]
                    for value in values
                    if value.document.allowed_permissions & grants
                ),
                key=lambda chunk: chunk.chunk_id,
            )
        )
        assert store.search("synthetic", grants, limit=100) == expected
        for value in values:
            found = store.get_chunk(value.chunks[0].chunk_id, grants)
            assert found == (
                value.chunks[0] if value.document.allowed_permissions & grants else None
            )


@pytest.mark.parametrize(
    "grants",
    [
        frozenset(),
        set(PUBLIC),
        [Permission.PUBLIC],
        None,
        "Public",
        frozenset({"Public"}),
        frozenset({SystemRole.ADMIN}),
        frozenset({Permission.PUBLIC, SystemRole.EVALUATOR}),
        frozenset({Permission.PUBLIC, "unknown"}),
    ],
)
def test_invalid_grants_deny(store: ContentStore, grants: Any) -> None:
    store.replace(prepared())
    with unavailable():
        store.search("synthetic", grants)
    with unavailable():
        store.get_chunk("absent", grants)


@pytest.mark.parametrize(
    "query",
    [
        None,
        1,
        "",
        "  ",
        "___ !-- ' %",
        "a" * 1025,
        "a" * 65,
        "\ud800",
        "a\x00b",
        " ".join(f"term{number}" for number in range(17)),
    ],
)
def test_invalid_queries(store: ContentStore, query: Any) -> None:
    with unavailable():
        store.search(query, PUBLIC)


@pytest.mark.parametrize("limit", [0, 101, True, 1.0, "1", None])
def test_invalid_limits(store: ContentStore, limit: Any) -> None:
    with unavailable():
        store.search("word", PUBLIC, limit=limit)


def test_unicode_distinct_ranking_limits_and_restricted_invariance(store: ContentStore) -> None:
    value = prepared(text="unused")
    document = value.document
    chunks = (
        Chunk("b", document, "Section", "Straße CAFÉ leave leave leave"),
        Chunk("a", document, "Section", "CAFÉ leave"),
        Chunk("c", document, "Section", "CAFÉ"),
    )
    store.replace(replace(value, chunks=chunks))
    expected = (chunks[1], chunks[0], chunks[2])
    assert store.search("café LEAVE leave", PUBLIC) == expected
    assert store.search("STRASSE", PUBLIC) == (chunks[0],)
    assert store.search("café leave", PUBLIC, limit=1) == expected[:1]
    assert store.search("CAFÉ", PUBLIC, limit=100) == expected
    for number in range(8):
        store.replace(
            prepared(f"restricted-{number}", frozenset({Permission.HR}), "café leave " * 100)
        )
    assert store.search("café LEAVE leave", PUBLIC) == expected
    assert store.search("no_matching_words", PUBLIC) == ()


def test_parameterized_sql_and_no_wildcard_semantics(store: ContentStore) -> None:
    value = prepared(text="leave policy")
    odd = "x'; DROP TABLE documents; --"
    document = replace(value.document, doc_id=odd)
    chunk = replace(value.chunks[0], chunk_id=odd, document=document)
    store.replace(replace(value, document=document, chunks=(chunk,)))
    assert store.get_chunk(odd, PUBLIC) == chunk
    assert store.get_chunk("' OR 1=1 --", PUBLIC) is None
    assert store.search("' OR 1=1 --", PUBLIC) == ()
    assert store.search("%leave%", PUBLIC) == (chunk,)
    store.delete("' OR 1=1 --")
    assert store.get_chunk(odd, PUBLIC) == chunk
    store.delete(odd)
    assert store.search("leave", PUBLIC) == ()


def test_replacement_narrows_labels_source_and_deletion_removes_stale(
    store: ContentStore,
    path: Path,
) -> None:
    old = prepared(labels=frozenset({Permission.PUBLIC, Permission.HR}))
    store.replace(old)
    new = prepared(labels=frozenset({Permission.HR}), text="new policy")
    assert new.document.doc_id == old.document.doc_id
    store.replace(new)
    assert store.get_chunk(old.chunks[0].chunk_id, ALL) is None
    assert store.search("policy", PUBLIC) == ()
    assert store.search("policy", ALL) == new.chunks
    assert rows(path, "SELECT permission FROM labels") == [("HR",)]
    assert len(rows(path, "SELECT * FROM chunks")) == 1
    # Changed URL is distinct lineage; replacing it does not silently delete another source.
    other_source = prepared(url="https://example.invalid/new", text="other policy")
    store.replace(other_source)
    store.delete(new.document.doc_id)
    store.delete(new.document.doc_id)
    assert store.search("policy", ALL) == other_source.chunks
    store.delete(other_source.document.doc_id)
    for statement in ("SELECT * FROM documents", "SELECT * FROM chunks", "SELECT * FROM labels"):
        assert rows(path, statement) == []


def test_collision_rolls_back_complete_replacement(store: ContentStore, path: Path) -> None:
    first, second = prepared("first"), prepared("second")
    store.replace(first)
    store.replace(second)
    statements = ("SELECT * FROM documents", "SELECT * FROM labels", "SELECT * FROM chunks")
    before = [rows(path, statement) for statement in statements]
    collision = replace(first.chunks[0], document=second.document)
    with unavailable():
        store.replace(
            replace(second, chunks=(replace(second.chunks[0], chunk_id="new"), collision))
        )
    assert before == [rows(path, statement) for statement in statements]
    assert store.get_chunk(first.chunks[0].chunk_id, ALL) == first.chunks[0]
    assert store.get_chunk(second.chunks[0].chunk_id, ALL) == second.chunks[0]


def forge(value_type: type[Any], **fields: Any) -> Any:
    value = object.__new__(value_type)
    for name, field in fields.items():
        object.__setattr__(value, name, field)
    return value


@pytest.mark.parametrize(
    "mutate",
    [
        lambda p: None,
        lambda p: forge(PreparedDocument),
        lambda p: replace(p, provenance=None),
        lambda p: replace(p, document=forge(DocumentMetadata)),
        lambda p: replace(p, chunks=[]),
        lambda p: replace(p, chunks=()),
        lambda p: replace(p, chunks=(p.chunks[0], p.chunks[0])),
        lambda p: replace(p, chunks=(forge(Chunk),)),
        lambda p: replace(p, chunks=(replace(p.chunks[0], document=prepared("other").document),)),
        lambda p: replace(p, provenance=replace(p.provenance, source_url="https://other.invalid")),
        lambda p: replace(p, provenance=replace(p.provenance, allowed_permissions=ALL)),
        lambda p: replace(p, provenance=replace(p.provenance, title="different title")),
        lambda p: replace(p, document=replace(p.document, title="x" * 4097)),
        lambda p: replace(p, chunks=(replace(p.chunks[0], text="x" * 2_097_153),)),
        lambda p: replace(p, chunks=(replace(p.chunks[0], section="x" * 2_097_153),)),
        lambda p: replace(
            p, chunks=tuple(replace(p.chunks[0], chunk_id=str(i)) for i in range(4097))
        ),
        lambda p: replace(
            p,
            chunks=tuple(
                replace(p.chunks[0], chunk_id=str(i), text="x" * 1_048_577) for i in range(2)
            ),
        ),
        lambda p: replace(
            p,
            chunks=tuple(
                replace(p.chunks[0], chunk_id=str(i), section="x" * 1_048_577) for i in range(2)
            ),
        ),
    ],
)
def test_forged_and_over_budget_prepared_is_atomic(
    store: ContentStore,
    mutate: Callable[[PreparedDocument], Any],
) -> None:
    original = prepared()
    store.replace(original)
    with unavailable():
        store.replace(mutate(original))
    assert store.search("synthetic", ALL) == original.chunks


@pytest.mark.parametrize(
    "field,value",
    [
        ("allowed_permissions", frozenset({"Public"})),
        ("allowed_permissions", frozenset({Permission.PUBLIC, "unknown"})),
        ("retrieved_on", datetime(2026, 1, 1, tzinfo=UTC)),
        ("updated_at", datetime(2026, 1, 1)),
        ("title", "\ud800"),
        ("source_url", "https://user:password@example.invalid"),  # pragma: allowlist secret
    ],
)
def test_forged_nested_metadata(store: ContentStore, field: str, value: Any) -> None:
    item = prepared()
    metadata = replace(item.document)
    object.__setattr__(metadata, field, value)
    with unavailable():
        store.replace(replace(item, document=metadata))
    with unavailable():
        store.replace(replace(item, provenance=metadata))


@pytest.mark.parametrize(
    "field,value",
    [
        ("title", None),
        ("title", []),
        ("title", 5),
        ("title", ""),
        ("title", "x" * 4097),
        ("title", "\ud800"),
        ("retrieved_on", "not a date"),
        ("retrieved_on", "2026-01-01T00:00:00Z"),
        ("updated_at", "2026-01-01T00:00:00"),
        ("source_url", "javascript:invalid"),
        ("doc_id", ""),
        ("allowed_permissions", []),
        ("allowed_permissions", ["Public", "Public"]),
        ("allowed_permissions", ["Public", "unknown"]),
        ("allowed_permissions", ["Public", 1]),
        ("allowed_permissions", "Public"),
        ("allowed_permissions", ["Admin"]),
    ],
)
@pytest.mark.parametrize("column", ["metadata", "provenance"])
def test_corrupt_nested_json_never_escapes(
    store: ContentStore,
    path: Path,
    field: str,
    value: Any,
    column: str,
) -> None:
    item = prepared()
    store.replace(item)
    select, update = {
        "metadata": ("SELECT metadata FROM documents", "UPDATE documents SET metadata = ?"),
        "provenance": ("SELECT provenance FROM documents", "UPDATE documents SET provenance = ?"),
    }[column]
    data = json.loads(rows(path, select)[0][0])
    data[field] = value
    execute(path, update, (json.dumps(data),))
    with unavailable():
        store.search("synthetic", PUBLIC)
    with unavailable():
        store.get_chunk(item.chunks[0].chunk_id, PUBLIC)


@pytest.mark.parametrize(
    "encoded",
    [
        "{",
        "null",
        "[]",
        "{}",
        "NaN",
        b"not-json",
        '{"doc_id":"restricted-secret","doc_id":"duplicate"}',
        "[" * 2000,
    ],
)
def test_malformed_json_safe_errors(store: ContentStore, path: Path, encoded: Any) -> None:
    item = prepared()
    store.replace(item)
    execute(path, "UPDATE documents SET metadata = ?", (encoded,))
    with unavailable():
        store.search("synthetic", PUBLIC)
    with unavailable():
        store.get_chunk(item.chunks[0].chunk_id, PUBLIC)


@pytest.mark.parametrize(
    "sql,parameters",
    [
        ("UPDATE chunks SET text = ?", (b"restricted-secret",)),
        ("UPDATE chunks SET text = ?", ("",)),
        ("UPDATE chunks SET section = ?", (b"invalid-section-type",)),
        ("UPDATE chunks SET section = ?", ("\x00",)),
        ("UPDATE chunks SET chunk_id = ?", ("",)),
        ("UPDATE chunks SET text_fold = ?", ("forged",)),
        ("UPDATE chunks SET text = ?, text_fold = ?", ("x" * 2_097_153, "x" * 2_097_153)),
        ("INSERT INTO labels SELECT doc_id, 'unknown' FROM documents", ()),
        ("INSERT INTO labels SELECT doc_id, 'HR' FROM documents", ()),
    ],
)
def test_matching_corrupt_rows_deny(
    store: ContentStore,
    path: Path,
    sql: str,
    parameters: tuple[object, ...],
) -> None:
    item = prepared()
    store.replace(item)
    execute(path, sql, parameters)
    with unavailable():
        store.get_chunk(item.chunks[0].chunk_id if "chunk_id" not in sql else "", ALL)
    # Corruption may make the lexical term nonmatching; neither result may expose text.
    try:
        result = store.search("synthetic", ALL)
    except ContentStoreError as error:
        assert error.__context__ is None
    else:
        assert result == ()


def test_missing_labels_and_forged_prefilter_cannot_grant(store: ContentStore, path: Path) -> None:
    item = prepared(labels=frozenset({Permission.HR}))
    store.replace(item)
    assert store.get_chunk(item.chunks[0].chunk_id, PUBLIC) is None
    execute(path, "INSERT INTO labels SELECT doc_id, 'Public' FROM documents")
    with unavailable():
        store.search("synthetic", PUBLIC)
    with unavailable():
        store.get_chunk(item.chunks[0].chunk_id, PUBLIC)
    execute(path, "DELETE FROM labels")
    assert store.search("synthetic", ALL) == ()
    assert store.get_chunk(item.chunks[0].chunk_id, ALL) is None


def test_missing_files_never_implicitly_created(path: Path) -> None:
    store = ContentStore(path)
    for operation in (
        lambda: store.search("word", PUBLIC),
        lambda: store.get_chunk("id", PUBLIC),
        lambda: store.replace(prepared()),
        lambda: store.delete("id"),
    ):
        with unavailable():
            operation()
        assert not path.exists()
    store.initialize()
    assert store.search("word", PUBLIC) == ()
    assert store.get_chunk("absent", PUBLIC) is None


@pytest.mark.parametrize(
    "setup",
    [
        "PRAGMA user_version = 99",
        "CREATE TABLE unrelated (value TEXT)",
        "PRAGMA user_version = 1",
        "PRAGMA application_id = 123",
    ],
)
def test_schema_mismatch_is_not_adopted(path: Path, setup: str) -> None:
    execute(path, setup)
    before = path.read_bytes()
    store = ContentStore(path)
    for operation in (
        store.initialize,
        lambda: store.search("word", PUBLIC),
        lambda: store.get_chunk("id", PUBLIC),
        lambda: store.replace(prepared()),
        lambda: store.delete("id"),
    ):
        with unavailable():
            operation()
    assert path.read_bytes() == before


def test_damaged_schema_even_with_right_version(store: ContentStore, path: Path) -> None:
    execute(path, "DROP INDEX labels_permission")
    with unavailable():
        store.search("word", PUBLIC)
    with unavailable():
        store.initialize()


def test_non_database_and_missing_parent(tmp_path: Path, path: Path) -> None:
    path.write_bytes(b"restricted-secret-not-sqlite")
    with unavailable():
        ContentStore(path).initialize()
    assert path.read_bytes() == b"restricted-secret-not-sqlite"
    with unavailable():
        ContentStore(tmp_path / "missing" / "db").initialize()
    assert not (tmp_path / "missing").exists()
    with unavailable():
        ContentStore("not a Path")  # type: ignore[arg-type]


@pytest.mark.skipif(os.name != "posix", reason="POSIX permissions")
def test_private_creation_preserves_existing_modes(path: Path) -> None:
    previous = os.umask(0)
    try:
        store = ContentStore(path)
        store.initialize()
    finally:
        os.umask(previous)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    path.chmod(0o640)
    store.initialize()
    assert stat.S_IMODE(path.stat().st_mode) == 0o640


def test_connection_failures_are_generic(path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*args: Any, **kwargs: Any) -> Any:
        raise sqlite3.OperationalError("restricted-secret database error")

    monkeypatch.setattr("policypilot.app.services.content_store.sqlite3.connect", fail)
    with unavailable():
        ContentStore(path).initialize()
    assert path.read_bytes() == b""


def test_sql_prefilter_happens_before_python_decoding(
    store: ContentStore,
    path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    public, restricted = prepared(), prepared("restricted", frozenset({Permission.FINANCE}))
    store.replace(public)
    store.replace(restricted)
    execute(
        path,
        "UPDATE documents SET metadata = '{secret' WHERE doc_id = ?",
        (restricted.document.doc_id,),
    )
    seen: list[str] = []
    original = store._chunk

    def inspect(
        connection: sqlite3.Connection, row: sqlite3.Row, permissions: frozenset[Permission]
    ) -> Chunk:
        seen.append(row["doc_id"])
        return original(connection, row, permissions)

    monkeypatch.setattr(store, "_chunk", inspect)
    assert store.search("synthetic", PUBLIC) == public.chunks
    assert seen == [public.document.doc_id]
    assert store.get_chunk(restricted.chunks[0].chunk_id, PUBLIC) is None


def test_document_json_parent_mismatch(store: ContentStore, path: Path) -> None:
    item = prepared()
    store.replace(item)
    data = json.loads(rows(path, "SELECT metadata FROM documents")[0][0])
    data["doc_id"] = "not-the-parent-key"
    execute(path, "UPDATE documents SET metadata = ?", (json.dumps(data),))
    with unavailable():
        store.search("synthetic", ALL)
    with unavailable():
        store.get_chunk(item.chunks[0].chunk_id, ALL)


@pytest.mark.parametrize("identifier", [None, 5, "", " ", "\x00", "\ud800", "x" * 4097])
def test_invalid_identifiers(store: ContentStore, identifier: Any) -> None:
    with unavailable():
        store.get_chunk(identifier, PUBLIC)
    with unavailable():
        store.delete(identifier)


def test_boundary_budgets_and_query_terms(store: ContentStore) -> None:
    item = prepared()
    chunks = tuple(
        replace(item.chunks[0], chunk_id=f"c-{number:04d}", text="x" * 512, section="s" * 512)
        for number in range(4096)
    )
    store.replace(replace(item, chunks=chunks))
    assert len(store.search("x", PUBLIC, limit=100)) == 100
    assert store.get_chunk(chunks[-1].chunk_id, PUBLIC) == chunks[-1]
    store.replace(replace(item, chunks=(replace(item.chunks[0], text="a" * 64),)))
    assert store.search("a" * 64, PUBLIC)
    assert store.search("a " * 512, PUBLIC)
    assert store.search(" ".join(f"t{i}" for i in range(16)), PUBLIC) == ()


def test_delete_and_initialization_failures_roll_back(
    store: ContentStore,
    path: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    item = prepared()
    store.replace(item)
    connect = sqlite3.connect

    class FailingConnection(sqlite3.Connection):
        def execute(self, sql: str, parameters: Any = ()) -> sqlite3.Cursor:
            cursor = super().execute(sql, parameters)
            if sql.startswith(("DELETE FROM documents", "CREATE INDEX")):
                raise sqlite3.OperationalError("restricted-secret after mutation")
            return cursor

    def failing_connect(*args: Any, **kwargs: Any) -> sqlite3.Connection:
        return connect(*args, **kwargs, factory=FailingConnection)

    monkeypatch.setattr("policypilot.app.services.content_store.sqlite3.connect", failing_connect)
    with unavailable():
        store.delete(item.document.doc_id)
    with unavailable():
        store.replace(prepared(text="replacement"))
    assert store.search("synthetic", PUBLIC) == item.chunks
    new_path = tmp_path / "failed-initialization.db"
    with unavailable():
        ContentStore(new_path).initialize()
    assert rows(new_path, "SELECT * FROM sqlite_master") == []
    assert rows(new_path, "PRAGMA user_version") == [(0,)]


def test_replace_many_publishes_all_documents_atomically(store, path):
    first = prepared("one", text="alpha policy")
    second = prepared("two", text="beta policy")
    store.replace_many((first, second))
    assert len(store.search("alpha", ALL)) == 1
    assert len(store.search("beta", ALL)) == 1
    assert rows(path, "SELECT count(*) FROM documents") == [(2,)]


def test_replace_many_rejects_duplicates_without_partial_write(store, path):
    existing = prepared("existing", text="existing policy")
    store.replace(existing)
    with unavailable():
        store.replace_many((prepared("one"), prepared("one")))
    assert rows(path, "SELECT count(*) FROM documents") == [(1,)]
    assert len(store.search("existing", ALL)) == 1


def test_replace_many_rejects_cross_document_chunk_collision(store, path):
    first = prepared("one", text="alpha")
    second = prepared("two", text="beta")
    collided = replace(first, chunks=(replace(first.chunks[0], chunk_id="shared"),))
    also_collided = replace(second, chunks=(replace(second.chunks[0], chunk_id="shared"),))
    with unavailable():
        store.replace_many((collided, also_collided))
    assert rows(path, "SELECT count(*) FROM documents") == [(0,)]


@pytest.mark.parametrize("value", [(), [], None, (prepared("one"),) * 101])
def test_replace_many_requires_nonempty_bounded_tuple(store, value):
    with unavailable():
        store.replace_many(value)


def test_replace_many_revalidates_every_nested_prepared(store):
    first = prepared("one")
    second = replace(prepared("two"), chunks=("not-a-chunk",))
    with unavailable():
        store.replace_many((first, second))


def test_replace_single_still_works_after_batch_refactor(store):
    store.replace(prepared("one", text="single policy"))
    assert len(store.search("single", ALL)) == 1


def test_list_documents_is_permission_filtered_metadata_only(store: ContentStore) -> None:
    public = prepared("public", PUBLIC, "Public leave policy text.")
    hr = prepared("hr", frozenset({Permission.HR}), "Secret HR text.")
    store.replace_many((public, hr))
    public_summaries = store.list_documents(PUBLIC)
    assert len(public_summaries) == 1
    summary = public_summaries[0]
    assert summary.document.doc_id == public.document.doc_id
    assert summary.document.title == "Synthetic policy"
    assert summary.chunk_count == len(public.chunks)
    assert summary.sections
    assert summary.matched_permissions == PUBLIC
    assert "Public leave policy text" not in repr(summary)
    assert "Secret HR text" not in repr(summary)
    assert {item.document.doc_id for item in store.list_documents(ALL)} == {
        public.document.doc_id,
        hr.document.doc_id,
    }


@pytest.mark.parametrize(
    "grants",
    [
        frozenset(),
        set(PUBLIC),
        [Permission.PUBLIC],
        None,
        "Public",
        frozenset({"Public"}),
        frozenset({SystemRole.ADMIN}),
        frozenset({Permission.PUBLIC, "unknown"}),
    ],
)
def test_list_documents_rejects_invalid_grants(store: ContentStore, grants: Any) -> None:
    with unavailable():
        store.list_documents(grants)


def test_list_documents_survives_corrupt_labels(store: ContentStore, path: Path) -> None:
    value = prepared()
    store.replace(value)
    execute(
        path,
        "INSERT INTO labels VALUES (?, ?)",
        (value.document.doc_id, "HR"),
    )
    with unavailable():
        store.list_documents(PUBLIC)
