"""Corpus-independent content contracts; all examples are synthetic."""

from dataclasses import FrozenInstanceError, replace
from datetime import UTC, date, datetime
from enum import StrEnum
from itertools import combinations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from policypilot.app.models.content import (
    Chunk,
    ContentValidationError,
    DocumentMetadata,
    valid_chunk,
    valid_document_metadata,
)
from policypilot.app.models.domain import Permission, Principal, SystemRole
from policypilot.app.services.authorization import can_read


class LookalikePermission(StrEnum):
    HR = "HR"


def document_fields():
    return {
        "doc_id": "synthetic-policy",
        "title": "Synthetic policy title",
        "source_url": "https://example.invalid/policies/example#section",
        "retrieved_on": date(2026, 10, 19),
        "licence_note": "Synthetic test fixture only; not corpus approval",
        "version": "test-v1",
        "updated_at": datetime(2026, 10, 19, tzinfo=UTC),
        "allowed_permissions": frozenset({Permission.HR}),
    }


def chunk_fields():
    return {
        "chunk_id": "synthetic-policy-section-1",
        "document": DocumentMetadata(**document_fields()),
        "section": "Policy / Example",
        "text": "Synthetic restricted passage.\nSecond line with\ttab.",
    }


STRING_FIELDS = ("doc_id", "title", "source_url", "licence_note", "version")
BAD_TEXT = (None, 1, True, b"text", [], "", " \t\n", "bad\x00value", "bad\ud800value")
BAD_PERMISSIONS = (
    None,
    [],
    {},
    "HR",
    Permission.HR,
    [Permission.HR],
    (Permission.HR,),
    {Permission.HR},
    frozenset(),
    frozenset({"HR"}),
    frozenset({"Unknown"}),
    frozenset({None}),
    frozenset({LookalikePermission.HR}),
    frozenset({SystemRole.ADMIN}),
    frozenset({Permission.HR, "Finance"}),
)
BAD_DOCUMENT_FIELDS = (
    *((field, value) for field in STRING_FIELDS for value in BAD_TEXT),
    *(("allowed_permissions", value) for value in BAD_PERMISSIONS),
    *(
        ("retrieved_on", value)
        for value in (None, "2026-10-19", datetime(2026, 10, 19, tzinfo=UTC), 0)
    ),
    *(
        ("updated_at", value)
        for value in (None, "2026-10-19", date(2026, 10, 19), datetime(2026, 10, 19))
    ),
    *(
        ("source_url", value)
        for value in (
            "file:///etc/passwd",
            "javascript:alert(1)",
            "https:///no-host",
            "relative/path",
            "https://user:password@example.invalid/",
            "https://example.invalid:bad/",
            "https://example.invalid:65536/",
            "https://example.invalid:0/",
            "https://%zz.example.invalid/",
            "https://example.invalid/path%",
            "https://example.invalid/path?x=%xy",
            "https://example.invalid/path#%0",
            "https://[broken/",
            "https://example.invalid/\npath",
            "https://example.invalid/a b",
            "https://example.invalid/\x7fpath",
        )
    ),
)


@pytest.mark.parametrize(("field", "value"), BAD_DOCUMENT_FIELDS)
def test_invalid_document_rejected_on_construction_and_revalidation(field, value):
    fields = document_fields() | {field: value}
    with pytest.raises(ContentValidationError, match="^Invalid content metadata$") as error:
        DocumentMetadata(**fields)
    assert error.value.__cause__ is None
    assert error.value.__context__ is None
    forged = DocumentMetadata(**document_fields())
    object.__setattr__(forged, field, value)
    assert not valid_document_metadata(forged)
    with pytest.raises(ContentValidationError, match="^Invalid content metadata$"):
        Chunk(**(chunk_fields() | {"document": forged}))


@pytest.mark.parametrize("value", (None, {}, "document", Principal("alice")))
def test_non_document_objects_deny(value):
    assert not valid_document_metadata(value)
    assert not valid_chunk(value)
    with pytest.raises(ContentValidationError):
        Chunk(**(chunk_fields() | {"document": value}))


def test_incomplete_forged_instances_deny():
    assert not valid_document_metadata(object.__new__(DocumentMetadata))
    assert not valid_chunk(object.__new__(Chunk))


@pytest.mark.parametrize("field", ("chunk_id", "section", "text"))
@pytest.mark.parametrize("value", BAD_TEXT)
def test_invalid_chunk_rejected_on_construction_and_revalidation(field, value):
    with pytest.raises(ContentValidationError, match="^Invalid content metadata$"):
        Chunk(**(chunk_fields() | {field: value}))
    forged = Chunk(**chunk_fields())
    object.__setattr__(forged, field, value)
    assert not valid_chunk(forged)


def test_forged_nested_metadata_does_not_remain_valid():
    chunk = Chunk(**chunk_fields())
    object.__setattr__(chunk.document, "allowed_permissions", frozenset({"HR"}))
    assert not valid_chunk(chunk)


def test_valid_models_are_immutable_and_inherit_exact_document_labels():
    chunk = Chunk(**chunk_fields())
    assert valid_document_metadata(chunk.document)
    assert valid_chunk(chunk)
    assert chunk.allowed_permissions is chunk.document.allowed_permissions
    with pytest.raises(FrozenInstanceError):
        chunk.text = "changed"
    with pytest.raises(FrozenInstanceError):
        chunk.document.allowed_permissions = frozenset({Permission.PUBLIC})
    with pytest.raises(TypeError):
        Chunk(**chunk_fields(), allowed_permissions=frozenset({Permission.PUBLIC}))


def test_no_permission_default_or_model_implies_authorization():
    fields = document_fields()
    del fields["allowed_permissions"]
    with pytest.raises(TypeError):
        DocumentMetadata(**fields)
    chunk = Chunk(**chunk_fields())
    assert not can_read(
        Principal("admin", system_roles=frozenset(SystemRole)), chunk.allowed_permissions
    )
    assert not can_read(None, chunk.allowed_permissions)
    assert can_read(Principal("hr", frozenset({Permission.HR})), chunk.allowed_permissions)


def test_source_url_is_only_metadata_not_proof_of_licensing_or_reachability():
    for url in (
        "https://example.invalid/",
        "http://example.invalid:8080/policy",
        "https://example.invalid/policy%20section?version=1#heading",
    ):
        assert valid_document_metadata(
            DocumentMetadata(**(document_fields() | {"source_url": url}))
        )


def test_repr_and_validation_errors_never_include_content(caplog, capsys):
    chunk = Chunk(**chunk_fields())
    assert repr(chunk) == "Chunk()"
    assert repr(chunk.document) == "DocumentMetadata()"
    with pytest.raises(ContentValidationError) as error:
        replace(chunk, text="SECRET_SENTINEL\x00")
    assert str(error.value) == "Invalid content metadata"
    assert "SECRET_SENTINEL" not in repr(error.value)
    assert not caplog.records
    captured = capsys.readouterr()
    assert captured.out == captured.err == ""


PERMISSION_SETS = tuple(
    frozenset(subset)
    for size in range(1, len(Permission) + 1)
    for subset in combinations(Permission, size)
)


@pytest.mark.parametrize("permissions", PERMISSION_SETS)
def test_all_nonempty_permission_sets_are_preserved_without_normalization(permissions):
    metadata = DocumentMetadata(**(document_fields() | {"allowed_permissions": permissions}))
    assert metadata.allowed_permissions == permissions
    assert valid_chunk(Chunk(**(chunk_fields() | {"document": metadata})))


@given(st.text())
def test_arbitrary_passage_text_is_preserved_without_normalization(text):
    fields = chunk_fields() | {"text": text}
    try:
        encodable = bool(text.encode("utf-8"))
    except UnicodeEncodeError:
        encodable = False
    if text.strip() and "\x00" not in text and encodable:
        chunk = Chunk(**fields)
        assert chunk.text == text
        assert valid_chunk(chunk)
    else:
        with pytest.raises(ContentValidationError):
            Chunk(**fields)
