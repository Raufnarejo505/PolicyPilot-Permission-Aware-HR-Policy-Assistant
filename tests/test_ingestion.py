"""Offline synthetic ingestion contracts; no corpus or evaluation data is used."""

import hashlib
import json
import socket
import traceback
import urllib.request
from dataclasses import FrozenInstanceError, replace
from datetime import UTC, date, datetime, timedelta, timezone
from enum import StrEnum
from itertools import combinations

import httpx
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from policypilot.app.models.content import DocumentMetadata, valid_chunk, valid_document_metadata
from policypilot.app.models.domain import Permission, SystemRole
from policypilot.app.services.ingestion import (
    ChunkingConfig,
    IngestionError,
    IngestionService,
    PreparedDocument,
    SourceDocument,
)


@pytest.fixture(autouse=True)
def prohibit_network(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Ingestion must not perform network operations")

    for name in ("connect", "connect_ex", "sendto"):
        monkeypatch.setattr(socket.socket, name, forbidden)
    for name in ("create_connection", "getaddrinfo"):
        monkeypatch.setattr(socket, name, forbidden)
    monkeypatch.setattr(urllib.request, "urlopen", forbidden)
    monkeypatch.setattr(httpx.Client, "send", forbidden)
    monkeypatch.setattr(httpx.AsyncClient, "send", forbidden)


def metadata(**changes):
    fields = {
        "doc_id": "operator-supplied-id",
        "title": "Synthetic policy",
        "source_url": "https://example.invalid/synthetic-policy?revision=1#policy",
        "retrieved_on": date(2026, 10, 20),
        "licence_note": "Synthetic fixture only; not corpus approval",
        "version": "operator-supplied-version",
        "updated_at": datetime(2026, 10, 20, tzinfo=UTC),
        "allowed_permissions": frozenset({Permission.HR}),
    }
    return DocumentMetadata(**(fields | changes))


def source(content=b"Synthetic policy text.\n", media_type="text/plain", **changes):
    return SourceDocument(metadata(**changes), content, media_type)


def identities(result):
    return (
        result.document.doc_id,
        result.document.version,
        tuple(chunk.chunk_id for chunk in result.chunks),
    )


def digest(value):
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=True, separators=(",", ":")).encode("ascii")
    ).hexdigest()


def reject(call):
    with pytest.raises(IngestionError, match="^Invalid ingestion input$") as error:
        call()
    assert error.value.__cause__ is None
    assert error.value.__context__ is None
    return error.value


def forge(value, field, replacement):
    object.__setattr__(value, field, replacement)
    return value


PERMISSION_SETS = tuple(
    frozenset(labels)
    for size in range(1, len(Permission) + 1)
    for labels in combinations(Permission, size)
)


class LookalikePermission(StrEnum):
    HR = "HR"


class IntegerSubclass(int):
    pass


class BytesSubclass(bytes):
    pass


class StringSubclass(str):
    pass


class ConfigSubclass(ChunkingConfig):
    pass


class SourceSubclass(SourceDocument):
    pass


def test_deterministic_source_and_window_identities():
    config = ChunkingConfig(64, 13)
    original = source(b"synthetic-window-" * 12)
    result = IngestionService(config).prepare(original)
    assert result == IngestionService(config).prepare(original)
    expected_doc_id = "doc-" + digest(["source-v1", original.metadata.source_url])
    expected_version = "sha256-" + digest(
        [
            "utf8-atx-characters-v1",
            original.content.decode(),
            "text/plain",
            original.metadata.title,
            original.metadata.licence_note,
            original.metadata.version,
            ["HR"],
            64,
            13,
        ]
    )
    assert result.document.doc_id == expected_doc_id
    assert result.document.version == expected_version
    for index, chunk in enumerate(result.chunks):
        start = index * 51
        end = min(start + 64, len(original.content))
        assert chunk.chunk_id == "chunk-" + digest(
            [expected_doc_id, expected_version, 0, start, end]
        )
    assert len({chunk.chunk_id for chunk in result.chunks}) == len(result.chunks)
    assert original.metadata.doc_id == "operator-supplied-id"
    assert original.metadata.version == "operator-supplied-version"
    assert result.provenance is original.metadata
    assert result.provenance.doc_id == "operator-supplied-id"
    assert result.provenance.version == "operator-supplied-version"
    assert result.document.doc_id != result.provenance.doc_id
    assert result.document.version != result.provenance.version


@pytest.mark.parametrize(
    "url",
    (
        "http://example.invalid/synthetic-policy?revision=1#policy",
        "https://EXAMPLE.invalid/synthetic-policy?revision=1#policy",
        "https://example.invalid/synthetic-policy?revision=2#policy",
        "https://example.invalid/synthetic-policy?revision=1#other",
        "https://example.invalid/synthetic-policy/?revision=1#policy",
        "https://example.invalid/%73ynthetic-policy?revision=1#policy",
    ),
)
def test_source_url_is_an_exact_lineage_key_not_canonicalized_or_fetched(url):
    service = IngestionService()
    first = service.prepare(source())
    other = service.prepare(source(source_url=url))
    assert first.document.doc_id != other.document.doc_id
    assert first.document.version == other.document.version
    assert first.chunks[0].chunk_id != other.chunks[0].chunk_id
    assert other.document.source_url == url


@pytest.mark.parametrize("bom", (b"", b"\xef\xbb\xbf"))
@pytest.mark.parametrize("newline", ("\n", "\r\n", "\r"))
@pytest.mark.parametrize("media_type", ("text/plain", "text/markdown"))
def test_bom_and_newline_normalization_produce_identical_versions_and_chunks(
    bom, newline, media_type
):
    text = "# Synthetic\nCafé\tpolicy.\n\nEnd.\n"
    service = IngestionService(ChunkingConfig(64, 0))
    expected = service.prepare(source(text.encode(), media_type))
    result = service.prepare(source(bom + text.replace("\n", newline).encode(), media_type))
    assert result == expected
    assert "".join(chunk.text for chunk in result.chunks) == text


@pytest.mark.parametrize(
    "changes",
    (
        {"content": b"Changed synthetic policy."},
        {"version": "another-operator-version"},
        {"title": "Changed synthetic title"},
        {"licence_note": "Changed synthetic provenance"},
        {"allowed_permissions": frozenset({Permission.PUBLIC})},
        {"allowed_permissions": frozenset({Permission.HR, Permission.EMPLOYEE})},
        {"media_type": "text/markdown"},
    ),
)
def test_semantic_source_changes_replace_version_and_every_chunk_id(changes):
    service = IngestionService()
    original = service.prepare(source())
    changed = service.prepare(source(**changes))
    assert changed.document.doc_id == original.document.doc_id
    assert changed.document.version != original.document.version
    assert {c.chunk_id for c in changed.chunks}.isdisjoint(c.chunk_id for c in original.chunks)


@pytest.mark.parametrize("config", (ChunkingConfig(1201, 150), ChunkingConfig(1200, 151)))
def test_configuration_is_versioned_even_when_passage_boundaries_are_unchanged(config):
    original = IngestionService().prepare(source())
    changed = IngestionService(config).prepare(source())
    assert changed.chunks[0].text == original.chunks[0].text
    assert changed.document.doc_id == original.document.doc_id
    assert changed.document.version != original.document.version
    assert changed.chunks[0].chunk_id != original.chunks[0].chunk_id


@pytest.mark.parametrize(
    "changes",
    (
        {"doc_id": "another-operator-id"},
        {"retrieved_on": date(2020, 1, 1)},
        {"updated_at": datetime(2030, 1, 1, tzinfo=UTC)},
        {"updated_at": datetime(2026, 10, 20, tzinfo=UTC).astimezone(timezone(timedelta(hours=5)))},
    ),
)
def test_operator_doc_id_and_timestamps_do_not_change_generated_ids(changes):
    service = IngestionService()
    result = service.prepare(source(**changes))
    assert identities(result) == identities(service.prepare(source()))
    assert result.provenance == metadata(**changes)
    for field in ("retrieved_on", "updated_at"):
        if field in changes:
            assert getattr(result.document, field) == changes[field]


@pytest.mark.parametrize("permissions", PERMISSION_SETS)
def test_exact_permission_inheritance_for_every_nonempty_combination(permissions):
    original = source(
        b"# One\n" + b"synthetic " * 30 + b"\n## Two\nEnd.",
        "text/markdown",
        allowed_permissions=permissions,
    )
    result = IngestionService(ChunkingConfig(64, 7)).prepare(original)
    assert valid_document_metadata(result.document)
    assert result.document.allowed_permissions is permissions
    assert result.provenance is original.metadata
    assert result.provenance.allowed_permissions is permissions
    assert len(result.chunks) > 2
    for chunk in result.chunks:
        assert valid_chunk(chunk)
        assert chunk.document is result.document
        assert chunk.allowed_permissions is permissions
    assert result.document == replace(
        original.metadata, doc_id=result.document.doc_id, version=result.document.version
    )


BAD_CONFIG = (
    (63, 0),
    (16001, 0),
    (64, -1),
    (64, 64),
    (64, 65),
    (True, 0),
    (False, 0),
    (64, True),
    (64, False),
    (64.0, 0),
    (64, 0.0),
    ("64", 0),
    (64, "0"),
    (None, 0),
    (64, None),
    (IntegerSubclass(64), 0),
    (64, IntegerSubclass(0)),
)


@pytest.mark.parametrize(("maximum", "overlap"), BAD_CONFIG)
def test_invalid_config_constructor(maximum, overlap):
    reject(lambda: ChunkingConfig(maximum, overlap))


@pytest.mark.parametrize("maximum", (64, 16000))
@pytest.mark.parametrize("near_maximum_overlap", (False, True))
def test_config_inclusive_boundaries(maximum, near_maximum_overlap):
    overlap = maximum - 1 if near_maximum_overlap else 0
    config = ChunkingConfig(maximum, overlap)
    assert IngestionService(config).prepare(source()).chunks


@pytest.mark.parametrize("value", (None, {}, (), 64, True, "config"))
def test_non_config_objects_rejected(value):
    reject(lambda: IngestionService(value))


@pytest.mark.parametrize(("maximum", "overlap"), BAD_CONFIG)
def test_forged_config_revalidated_at_service_construction_and_each_prepare(maximum, overlap):
    config = ChunkingConfig()
    service = IngestionService(config)
    forge(config, "max_characters", maximum)
    forge(config, "overlap_characters", overlap)
    reject(lambda: IngestionService(config))
    reject(lambda: service.prepare(source()))
    reject(lambda: service.prepare_batch((source(),)))


BAD_SOURCE_FIELDS = (
    *(
        ("content", value)
        for value in (
            None,
            "text",
            bytearray(b"text"),
            memoryview(b"text"),
            BytesSubclass(b"text"),
            b"",
            b"a" * (1_048_576 + 1),
        )
    ),
    *(
        ("media_type", value)
        for value in (
            None,
            b"text/plain",
            "text/html",
            "application/pdf",
            "text/plain; charset=utf-8",
            "TEXT/PLAIN",
            "",
            StringSubclass("text/plain"),
        )
    ),
    *(("metadata", value) for value in (None, {}, "metadata", object())),
)


@pytest.mark.parametrize(("field", "value"), BAD_SOURCE_FIELDS)
def test_invalid_source_fields_rejected_on_construction_and_revalidation(field, value):
    original = source()
    reject(lambda: replace(original, **{field: value}))
    forged = forge(original, field, value)
    service = IngestionService()
    reject(lambda: service.prepare(forged))
    reject(lambda: service.prepare_batch((forged,)))


@pytest.mark.parametrize(
    "permissions",
    (
        frozenset(),
        {Permission.HR},
        (Permission.HR,),
        frozenset({"HR"}),
        frozenset({LookalikePermission.HR}),
        frozenset({SystemRole.ADMIN}),
        frozenset({Permission.HR, "Unknown"}),
        frozenset({None}),
        None,
    ),
)
def test_forged_metadata_permissions_never_normalized_or_dropped(permissions):
    original = source()
    forge(original.metadata, "allowed_permissions", permissions)
    reject(lambda: SourceDocument(original.metadata, original.content))
    reject(lambda: IngestionService().prepare(original))
    reject(lambda: IngestionService().prepare_batch((source(), original)))


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("title", ""),
        ("licence_note", "\x00"),
        ("doc_id", None),
        ("version", "\ud800"),
        ("source_url", "file:///synthetic"),
        ("retrieved_on", datetime(2026, 1, 1, tzinfo=UTC)),
        ("updated_at", datetime(2026, 1, 1)),
    ),
)
def test_forged_nonpermission_metadata_is_revalidated(field, value):
    original = source()
    forge(original.metadata, field, value)
    reject(lambda: IngestionService().prepare(original))
    reject(lambda: IngestionService().prepare_batch((original,)))


@pytest.mark.parametrize("value", (None, {}, (), b"text", "source", metadata()))
def test_non_source_objects_rejected_by_prepare_and_batch(value):
    service = IngestionService()
    reject(lambda: service.prepare(value))
    reject(lambda: service.prepare_batch((value,)))


def test_subclasses_cannot_bypass_exact_type_validation():
    reject(lambda: ConfigSubclass())
    reject(lambda: SourceSubclass(metadata(), b"synthetic"))
    config = object.__new__(ConfigSubclass)
    forge(config, "max_characters", 64)
    forge(config, "overlap_characters", 0)
    reject(lambda: IngestionService(config))
    forged = object.__new__(SourceSubclass)
    for field in ("metadata", "content", "media_type"):
        forge(forged, field, getattr(source(), field))
    reject(lambda: IngestionService().prepare(forged))


@pytest.mark.parametrize("missing", ("metadata", "content", "all"))
@pytest.mark.parametrize("batch", (False, True), ids=("prepare", "batch"))
def test_incomplete_forged_source_rejects_with_content_free_error(missing, batch):
    forged = source() if missing != "all" else object.__new__(SourceDocument)
    if missing != "all":
        object.__delattr__(forged, missing)
    service = IngestionService()
    reject(lambda: service.prepare_batch((forged,)) if batch else service.prepare(forged))


@pytest.mark.parametrize("missing", ("max_characters", "overlap_characters", "all"))
@pytest.mark.parametrize("boundary", ("constructor", "prepare", "batch"))
def test_incomplete_forged_config_rejects_with_content_free_error(missing, boundary, monkeypatch):
    config = ChunkingConfig()
    service = IngestionService(config)
    fields = ("max_characters", "overlap_characters") if missing == "all" else (missing,)
    for field in fields:
        object.__delattr__(config, field)
        # Class defaults otherwise mask missing instance attributes in these dataclasses.
        monkeypatch.delattr(ChunkingConfig, field)
    if boundary == "constructor":
        reject(lambda: IngestionService(config))
    elif boundary == "prepare":
        reject(lambda: service.prepare(source()))
    else:
        reject(lambda: service.prepare_batch((source(),)))


@pytest.mark.parametrize("field", ("doc_id", "version", "title", "source_url", "licence_note"))
def test_incomplete_forged_metadata_is_rejected_at_ingestion_boundaries(field):
    original = source()
    object.__delattr__(original.metadata, field)
    reject(lambda: SourceDocument(original.metadata, original.content))
    reject(lambda: IngestionService().prepare(original))
    reject(lambda: IngestionService().prepare_batch((original,)))


@pytest.mark.parametrize("field", ("doc_id", "version", "title", "source_url", "licence_note"))
@pytest.mark.parametrize("length", (4095, 4096, 4097))
def test_metadata_character_budget_for_each_field_on_construction_and_revalidation(field, length):
    prefix = "https://example.invalid/" if field == "source_url" else ""
    value = prefix + "é" * (length - len(prefix))
    upstream = metadata(**{field: value})
    assert len(getattr(upstream, field)) == length
    assert len(getattr(upstream, field).encode()) > length
    assert valid_document_metadata(upstream)
    service = IngestionService()
    if length <= 4096:
        original = SourceDocument(upstream, b"Synthetic body.")
        result = service.prepare(original)
        assert result.provenance is upstream
        assert getattr(result.provenance, field) == value
        assert service.prepare_batch((original,)) == (result,)
    else:
        reject(lambda: SourceDocument(upstream, b"Synthetic body."))
        original = forge(source(), "metadata", upstream)
        reject(lambda: service.prepare(original))
        reject(lambda: service.prepare_batch((source(), original)))


def test_dataclass_defaults_remain_valid_when_instance_overrides_are_absent():
    # Defaulted fields remain available on the class, unlike required source fields.
    config = object.__new__(ChunkingConfig)
    assert config.max_characters == 1200
    assert config.overlap_characters == 150
    original = source()
    object.__delattr__(original, "media_type")
    assert original.media_type == "text/plain"
    assert IngestionService(config).prepare(original) == IngestionService().prepare(source())


BAD_DECODE = (
    b"\xff",
    b"\xc0\xaf",
    b"\xed\xa0\x80",
    b"\xe2\x82",
    b"\x80",
    b"synthetic\xf4\x90\x80\x80",
    b"\xef\xbb\xbf",
    b" \t\r\n",
    "\u2003\u00a0".encode(),
    *(b"synthetic" + bytes([control]) for control in range(32) if control not in (9, 10, 13)),
    b"synthetic\x7f",
    b"\x89PNG\r\n\x1a\n",
    b"PK\x03\x04synthetic",
)


@pytest.mark.parametrize("content", BAD_DECODE)
def test_utf8_blank_binary_and_control_data_fail_without_codec_context(content):
    original = source(content)
    reject(lambda: IngestionService().prepare(original))
    reject(lambda: IngestionService().prepare_batch((source(), original)))


def test_markdown_atx_hierarchy_keeps_headings_and_section_boundaries():
    parts = (
        ("Document", "Synthetic preamble.\n\n"),
        ("Root", "# Root ###  \nRoot body.\n"),
        ("Root / Child", "   ### Child\nChild body.\n"),
        ("Root / Child / Deep", "###### Deep\nDeep body.\n"),
        ("Root / Sibling", "## Sibling\nSibling body.\n"),
        ("Root / Sibling / Tabbed", "###\tTabbed\t##\t\nTabbed body.\n"),
        ("Next", "# Next\nLast body."),
    )
    text = "".join(body for _, body in parts)
    result = IngestionService(ChunkingConfig(1000, 99)).prepare(
        source(text.encode(), "text/markdown")
    )
    assert [(chunk.section, chunk.text) for chunk in result.chunks] == list(parts)
    for index, chunk in enumerate(result.chunks):
        assert chunk.chunk_id == "chunk-" + digest(
            [result.document.doc_id, result.document.version, index, 0, len(chunk.text)]
        )


@pytest.mark.parametrize(
    "body",
    (
        "#No space\n####### Seven\n    # Indented\nSetext\n======\n",
        "```python\n# Not a heading\n```\n",
        "~~~\n## Not a heading\n~~~\n",
        "````\n```\n# Still fenced\n~~~\n## Still fenced\n```` trailing\n# Still fenced\n`````\n",
        "~~~python\n```\n# Still fenced\n~~\n## Still fenced\n~~~~\n",
        "    ```\nordinary indented text\n",
    ),
)
def test_markdown_nonheadings_and_fenced_code_preserved(body):
    text = "# Outer\n" + body + "## Real\nSynthetic end.\n"
    result = IngestionService().prepare(source(text.encode(), "text/markdown"))
    assert [(c.section, c.text) for c in result.chunks] == [
        ("Outer", "# Outer\n" + body),
        ("Outer / Real", "## Real\nSynthetic end.\n"),
    ]


@pytest.mark.parametrize(
    "heading",
    (
        "## ",
        "##\t",
        "## \t ",
        "## \u00a0",
        "## \u00a0\t\u00a0",
        "## ###",
        "## \t### \t",
        "## \u00a0 ###",
    ),
)
def test_empty_whitespace_and_nbsp_atx_headings_use_untitled_section_in_hierarchy(heading):
    empty_body = heading + "\nSynthetic empty-heading body.\n"
    child_body = "### Child\nSynthetic child body.\n"
    text = "# Root\n" + empty_body + child_body + "# Next\nEnd."
    result = IngestionService().prepare(source(text.encode(), "text/markdown"))
    assert [(chunk.section, chunk.text) for chunk in result.chunks] == [
        ("Root", "# Root\n"),
        ("Root / Untitled section", empty_body),
        ("Root / Untitled section / Child", child_body),
        ("Next", "# Next\nEnd."),
    ]
    assert all(valid_chunk(chunk) for chunk in result.chunks)
    assert "".join(chunk.text for chunk in result.chunks) == text


@pytest.mark.parametrize(
    ("heading", "section"),
    (
        ("# Name ### \t", "Name"),
        ("# Name\t#\t", "Name"),
        ("# Name###", "Name###"),
        ("# Name # inside", "Name # inside"),
        ("# Name ### suffix", "Name ### suffix"),
        ("# ###", "Untitled section"),
        ("# Name\u00a0###", "Name\u00a0###"),
    ),
)
def test_optional_closing_hashes_require_ascii_whitespace_or_an_empty_title(heading, section):
    text = heading + "\nSynthetic body.\n"
    result = IngestionService().prepare(source(text.encode(), "text/markdown"))
    assert [(chunk.section, chunk.text) for chunk in result.chunks] == [(section, text)]


@pytest.mark.parametrize(
    ("heading", "section"),
    (
        ("# " + " " * 20_000 + "Synthetic ###  ", "Synthetic"),
        ("# Synthetic" + " " * 20_000 + "###\t", "Synthetic"),
        ("# " + " " * 20_000, "Untitled section"),
        ("# " + "\u00a0" * 20_000, "Untitled section"),
    ),
    ids=("leading-spaces", "closing-hashes", "empty-spaces", "empty-nbsp"),
)
def test_long_atx_heading_regression_without_timing_assumptions(heading, section):
    text = heading + "\nSynthetic body.\n"
    assert len(heading) > 16_000
    result = IngestionService(ChunkingConfig(16000, 0)).prepare(
        source(text.encode(), "text/markdown")
    )
    assert len(result.chunks) == 2
    assert all(chunk.section == section for chunk in result.chunks)
    assert all(valid_chunk(chunk) for chunk in result.chunks)
    assert "".join(chunk.text for chunk in result.chunks) == text


def test_unclosed_fence_keeps_all_following_headings_as_text():
    text = "# Outer\n```\n# Not a section\n## Nor this\n"
    result = IngestionService().prepare(source(text.encode(), "text/markdown"))
    assert [(c.section, c.text) for c in result.chunks] == [("Outer", text)]


def test_markdown_window_overlap_resets_at_each_section_and_round_trips():
    parts = (
        ("Root", "# Root\n" + "Synthetic root body. " * 15 + "\n"),
        ("Root / Child", "## Child\n" + "Synthetic child body. " * 15 + "\n"),
    )
    result = IngestionService(ChunkingConfig(64, 9)).prepare(
        source("".join(body for _, body in parts).encode(), "text/markdown")
    )
    assert {chunk.section for chunk in result.chunks} == {section for section, _ in parts}
    for section, body in parts:
        chunks = [chunk for chunk in result.chunks if chunk.section == section]
        assert len(chunks) > 1
        assert chunks[0].text.startswith(body.splitlines()[0] + "\n")
        assert [chunk.text for chunk in chunks] == [
            body[start : start + 64] for start in range(0, len(body) - 9, 55)
        ]
        assert chunks[0].text + "".join(chunk.text[9:] for chunk in chunks[1:]) == body


@pytest.mark.parametrize("length", (1, 63, 64, 65, 127, 128, 129))
@pytest.mark.parametrize("overlap", (0, 1, 63))
def test_window_boundaries_do_not_add_redundant_trailing_chunks(length, overlap):
    text = "a" * length
    result = IngestionService(ChunkingConfig(64, overlap)).prepare(source(text.encode()))
    expected_count = 1 + (max(0, length - 64) + 63 - overlap) // (64 - overlap)
    assert len(result.chunks) == expected_count
    assert result.chunks[0].text + "".join(c.text[overlap:] for c in result.chunks[1:]) == text


def test_plain_text_does_not_interpret_markdown_headings_or_fences():
    text = "Preamble\n# Root\n```\n## Inner\n```\n# Next\n"
    plain = IngestionService().prepare(source(text.encode()))
    markdown = IngestionService().prepare(source(text.encode(), "text/markdown"))
    assert [(c.section, c.text) for c in plain.chunks] == [("Document", text)]
    assert len(markdown.chunks) == 3
    assert plain.document.version != markdown.document.version


def test_adjacent_repeated_headings_have_distinct_position_based_ids():
    result = IngestionService().prepare(source(b"# Same\n# Same\n", "text/markdown"))
    assert [(c.section, c.text) for c in result.chunks] == [("Same", "# Same\n")] * 2
    assert result.chunks[0].chunk_id != result.chunks[1].chunk_id


@pytest.mark.parametrize("text", ("\n\n# Heading\n", " \t\n# Heading\n", "Synthetic body\n\n"))
def test_markdown_blank_preamble_is_omitted_but_body_whitespace_is_kept(text):
    result = IngestionService().prepare(source(text.encode(), "text/markdown"))
    expected = text[text.index("#") :] if "#" in text else text
    assert "".join(c.text for c in result.chunks) == expected


def test_whitespace_only_windows_are_omitted_without_changing_other_offsets():
    text = "A" * 64 + " " * 64 + "B" * 64
    result = IngestionService(ChunkingConfig(64, 0)).prepare(source(text.encode()))
    assert [c.text for c in result.chunks] == ["A" * 64, "B" * 64]
    assert result.chunks[1].chunk_id == "chunk-" + digest(
        [result.document.doc_id, result.document.version, 0, 128, 192]
    )


UNICODE_CHARACTER = st.characters(
    min_codepoint=32, exclude_categories=("Cs",), exclude_characters="\x7f\ufeff"
)
NONSPACE_CHARACTER = UNICODE_CHARACTER.filter(lambda char: not char.isspace())


@settings(max_examples=100, deadline=None)
@given(
    text=st.text(NONSPACE_CHARACTER, min_size=1, max_size=1800),
    maximum=st.integers(64, 200),
    overlap_fraction=st.integers(0, 99),
)
def test_unicode_character_windows_preserve_text_size_overlap_and_determinism(
    text, maximum, overlap_fraction
):
    overlap = maximum * overlap_fraction // 100
    config = ChunkingConfig(maximum, overlap)
    original = source(text.encode())
    result = IngestionService(config).prepare(original)
    assert result == IngestionService(config).prepare(original)
    chunks = result.chunks
    assert chunks
    for index, chunk in enumerate(chunks):
        start = index * (maximum - overlap)
        assert chunk.text == text[start : start + maximum]
        assert 1 <= len(chunk.text) <= maximum
        assert chunk.section == "Document"
        assert chunk.allowed_permissions == original.metadata.allowed_permissions
        if index and overlap:
            assert chunks[index - 1].text[-overlap:] == chunk.text[:overlap]
    reconstructed = chunks[0].text + "".join(chunk.text[overlap:] for chunk in chunks[1:])
    assert reconstructed == text
    assert len({c.chunk_id for c in chunks}) == len(chunks)


@settings(max_examples=100, deadline=None)
@given(
    middle=st.text(
        st.one_of(UNICODE_CHARACTER, st.sampled_from(["\n", "\r", "\t"])), max_size=1200
    ),
    maximum=st.integers(64, 160),
    overlap=st.integers(0, 63),
)
def test_unicode_and_whitespace_windows_match_normalized_source_slices(middle, maximum, overlap):
    text = "Synthetic " + middle + " end"
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    result = IngestionService(ChunkingConfig(maximum, overlap)).prepare(source(text.encode()))
    expected = []
    start = 0
    while start < len(normalized):
        end = min(start + maximum, len(normalized))
        passage = normalized[start:end]
        if passage.strip():
            expected.append(passage)
        if end == len(normalized):
            break
        start = end - overlap
    assert [chunk.text for chunk in result.chunks] == expected


def test_unicode_is_not_ascii_folded_normalized_or_counted_as_bytes():
    text = "é界🙂e\u0301\ufeff" * 30
    result = IngestionService(ChunkingConfig(64, 0)).prepare(source(text.encode()))
    assert len(result.chunks[0].text) == 64
    assert len(result.chunks[0].text.encode()) > 64
    assert "".join(c.text for c in result.chunks) == text
    assert identities(IngestionService().prepare(source("é".encode()))) != identities(
        IngestionService().prepare(source("e\u0301".encode()))
    )


def test_batch_duplicates_collapse_in_first_source_order_including_normalized_bytes():
    first = source(b"Synthetic\nbody\n")
    duplicate = source(b"\xef\xbb\xbfSynthetic\r\nbody\r")
    second = source(source_url="https://example.invalid/second")
    service = IngestionService()
    assert service.prepare_batch((first, second, first, duplicate, second)) == (
        service.prepare(first),
        service.prepare(second),
    )


@pytest.mark.parametrize(
    "changes",
    (
        {"content": b"Changed synthetic body"},
        {"version": "another-upstream-version"},
        {"doc_id": "another-upstream-id"},
        {"title": "Another title"},
        {"licence_note": "Another synthetic licence"},
        {"media_type": "text/markdown"},
        {"allowed_permissions": frozenset({Permission.FINANCE})},
        {"updated_at": datetime(2027, 1, 1, tzinfo=UTC)},
        {"retrieved_on": date(2027, 1, 1)},
    ),
)
def test_batch_same_source_conflicts_reject_atomically_and_do_not_poison_service(changes):
    service = IngestionService()
    first, conflict = source(), source(**changes)
    before = service.prepare(first)
    reject(
        lambda: service.prepare_batch(
            (first, source(source_url="https://example.invalid/other"), conflict)
        )
    )
    assert service.prepare(first) == before
    assert service.prepare_batch((first,)) == (before,)


class TupleSubclass(tuple):
    pass


@pytest.mark.parametrize(
    "sources",
    (
        None,
        [],
        [source()],
        {},
        "sources",
        iter((source(),)),
        (),
        TupleSubclass((source(),)),
        (source(),) * 101,
    ),
)
def test_batch_requires_exact_nonempty_bounded_tuple(sources):
    reject(lambda: IngestionService().prepare_batch(sources))


@pytest.mark.parametrize("position", (0, 1, 2))
def test_invalid_batch_entries_are_prevalidated_before_any_prepare(position, monkeypatch):
    service = IngestionService()
    calls = []
    monkeypatch.setattr(service, "prepare", lambda item: calls.append(item))
    inputs = [source(), source(), source()]
    inputs[position] = None
    reject(lambda: service.prepare_batch(tuple(inputs)))
    assert calls == []


def test_late_decode_failure_returns_no_partial_batch_and_leaves_no_state():
    service = IngestionService()
    valid = source()
    reject(lambda: service.prepare_batch((valid, source(b"secret-synthetic\xff"))))
    assert service.prepare_batch((valid,)) == (service.prepare(valid),)


def test_batch_document_count_includes_duplicates_before_collapsing():
    service = IngestionService()
    original = source()
    assert service.prepare_batch((original,) * 100) == (service.prepare(original),)
    reject(lambda: service.prepare_batch((original,) * 101))
    distinct = tuple(source(source_url=f"https://example.invalid/{i}") for i in range(100))
    assert len(service.prepare_batch(distinct)) == 100


def test_source_and_batch_byte_limits_are_inclusive_and_count_duplicates(monkeypatch):
    service = IngestionService(ChunkingConfig(16000, 0))
    original = source(b"a" * 1_048_576)
    expected = service.prepare(original)
    assert "".join(c.text for c in expected.chunks).encode() == original.content
    assert service.prepare_batch((original,) * 4) == (expected,)
    calls = []
    monkeypatch.setattr(service, "prepare", lambda item: calls.append(item))
    reject(lambda: service.prepare_batch((original,) * 4 + (source(b"x"),)))
    reject(lambda: service.prepare_batch((original,) * 5))
    assert calls == []


def test_source_byte_limit_is_utf8_bytes_not_character_count():
    reject(lambda: source(("🙂" * 262_145).encode()))


@pytest.mark.parametrize("markdown", (False, True))
def test_resource_chunk_cap_allows_4096_and_rejects_4097_across_entire_document(markdown):
    if markdown:
        config = ChunkingConfig(64, 0)
        at_limit = source(b"# Synthetic\n" * 4096, "text/markdown")
        over_limit = source(b"# Synthetic\n" * 4097, "text/markdown")
    else:
        config = ChunkingConfig(64, 63)
        at_limit = source(b"a" * 4159)
        over_limit = source(b"a" * 4160)
    service = IngestionService(config)
    result = service.prepare(at_limit)
    assert len(result.chunks) == 4096
    assert len({c.chunk_id for c in result.chunks}) == 4096
    reject(lambda: service.prepare(over_limit))
    reject(lambda: service.prepare_batch((source(), over_limit)))
    assert service.prepare(at_limit) == result


@pytest.mark.parametrize("delta", (-1, 0, 1), ids=("below", "at-limit", "above"))
def test_document_emitted_character_budget_counts_overlap_and_unicode_characters(delta):
    # 1048 full windows plus a 1152-character tail total exactly 2 Mi characters.
    # One input character changes only that tail, not the number of windows.
    text = "é" + "a" * (944_352 + delta - 1)
    original = source(text.encode())
    service = IngestionService(ChunkingConfig(2000, 1100))
    assert len(original.content) <= 1_048_576
    if delta > 0:
        reject(lambda: service.prepare(original))
        reject(lambda: service.prepare_batch((original,)))
    else:
        result = service.prepare(original)
        assert len(result.chunks) == 1049 < 4096
        assert len(result.chunks[-1].text) == 1152 + delta
        assert sum(len(chunk.text) for chunk in result.chunks) == 2_097_152 + delta
        assert sum(len(chunk.text.encode()) for chunk in result.chunks) == 2_097_153 + delta
        assert service.prepare_batch((original,)) == (result,)


@pytest.mark.parametrize("delta", (-1, 0, 1), ids=("below", "at-limit", "above"))
def test_batch_emitted_character_budget_boundary_and_atomic_failure(delta):
    # Each small source expands to 1024 full 2048-character windows (2 Mi characters).
    service = IngestionService(ChunkingConfig(2048, 2047))
    originals = tuple(
        source(b"a" * 3071, source_url=f"https://example.invalid/output-budget/{index}")
        for index in range(4)
    )
    if delta < 0:
        originals = originals[:3] + (
            replace(originals[3], content=b"a" * 3070),
            source(b"a" * 2047, source_url="https://example.invalid/output-budget/tail"),
        )
    elif delta > 0:
        originals += (source(b"x", source_url="https://example.invalid/output-budget/tail"),)
    individual = tuple(service.prepare(item) for item in originals)
    assert sum(len(item.content) for item in originals) < 4_194_304
    assert sum(len(item.chunks) for item in individual) < 8192
    assert sum(len(chunk.text) for item in individual for chunk in item.chunks) == 8_388_608 + delta
    if delta > 0:
        reject(lambda: service.prepare_batch(originals))
        assert service.prepare_batch(originals[:1]) == individual[:1]
    else:
        assert service.prepare_batch(originals) == individual


def test_duplicate_batch_entries_do_not_double_count_emitted_characters():
    service = IngestionService(ChunkingConfig(2048, 2047))
    originals = tuple(
        source(b"a" * 3071, source_url=f"https://example.invalid/duplicate-output/{index}")
        for index in range(4)
    )
    expected = service.prepare_batch(originals)
    assert sum(len(chunk.text) for item in expected for chunk in item.chunks) == 8_388_608
    duplicated = (originals[0], originals[0]) + originals[1:] + originals
    assert sum(len(item.content) for item in duplicated) < 4_194_304
    assert service.prepare_batch(duplicated) == expected


@pytest.mark.parametrize("delta", (-1, 0, 1), ids=("below", "at-limit", "above"))
def test_batch_chunk_budget_boundary_and_atomic_failure(delta):
    service = IngestionService(ChunkingConfig(64, 63))
    first = source(b"a" * 4159, source_url="https://example.invalid/chunk-budget/first")
    second = source(
        b"b" * (4158 if delta < 0 else 4159),
        source_url="https://example.invalid/chunk-budget/second",
    )
    originals = (first, second)
    if delta > 0:
        originals += (source(b"x", source_url="https://example.invalid/chunk-budget/third"),)
    individual = tuple(service.prepare(item) for item in originals)
    assert sum(len(item.chunks) for item in individual) == 8192 + delta
    assert sum(len(chunk.text) for item in individual for chunk in item.chunks) < 8_388_608
    assert sum(len(item.content) for item in originals) < 4_194_304
    if delta > 0:
        reject(lambda: service.prepare_batch(originals))
        assert service.prepare_batch((first,)) == individual[:1]
    else:
        assert service.prepare_batch(originals) == individual
        duplicated = (first, first, second, second, first)
        assert service.prepare_batch(duplicated) == individual


def test_document_section_character_budget_accepts_limit_and_rejects_one_character_over():
    service = IngestionService(ChunkingConfig(64, 0))
    section = "é" * 8192
    heading = "# " + section + "\n"
    text = heading + "x" * (16_384 - len(heading) - 1) + "\n"
    original = source(text.encode(), "text/markdown")
    result = service.prepare(original)
    assert len(result.chunks) == 256 < 4096
    assert all(chunk.section == section for chunk in result.chunks)
    assert sum(len(chunk.section) for chunk in result.chunks) == 2_097_152
    assert sum(len(chunk.section.encode()) for chunk in result.chunks) > 2_097_152
    assert sum(len(chunk.text) for chunk in result.chunks) == 16_384 < 2_097_152
    assert len(original.content) < 1_048_576
    assert service.prepare_batch((original,)) == (result,)

    # A new one-character section exceeds only the label budget, not text or chunk limits.
    over_limit = replace(original, content=original.content + b"# X\n")
    reject(lambda: service.prepare(over_limit))
    reject(lambda: service.prepare_batch((over_limit,)))
    assert service.prepare(original) == result


def test_batch_section_character_budget_rejects_one_over_without_double_counting_duplicates():
    service = IngestionService(ChunkingConfig(64, 0))
    heading = "# " + "S" * 8192 + "\n"
    text = heading + "x" * (16_384 - len(heading) - 1) + "\n"
    originals = tuple(
        source(
            text.encode(),
            "text/markdown",
            source_url=f"https://example.invalid/section-budget/{index}",
        )
        for index in range(4)
    )
    individual = tuple(service.prepare(item) for item in originals)
    assert all(sum(len(chunk.section) for chunk in item.chunks) == 2_097_152 for item in individual)
    assert sum(len(chunk.section) for item in individual for chunk in item.chunks) == 8_388_608
    assert sum(len(chunk.text) for item in individual for chunk in item.chunks) < 8_388_608
    assert sum(len(item.chunks) for item in individual) == 1024 < 8192
    assert sum(len(item.content) for item in originals) < 4_194_304
    assert service.prepare_batch(originals) == individual
    assert service.prepare_batch((originals[0],) + originals + originals) == individual

    extra = source(
        b"# X\n", "text/markdown", source_url="https://example.invalid/section-budget/extra"
    )
    extra_result = service.prepare(extra)
    assert sum(len(chunk.section) for chunk in extra_result.chunks) == 1
    reject(lambda: service.prepare_batch(originals + (extra,)))
    assert service.prepare_batch(originals) == individual


def test_large_high_overlap_windows_reject_when_character_budget_precedes_chunk_cap():
    service = IngestionService(ChunkingConfig(16000, 15999))
    # 131 full windows fit; the next is over the character budget, well below 4096 chunks.
    accepted = source(b"a" * 16_130)
    result = service.prepare(accepted)
    assert len(result.chunks) == 131
    assert sum(len(chunk.text) for chunk in result.chunks) == 2_096_000
    rejected = replace(accepted, content=accepted.content + b"a")
    reject(lambda: service.prepare(rejected))
    reject(lambda: service.prepare_batch((source(), rejected)))
    assert service.prepare(accepted) == result


def test_immutable_inputs_outputs_and_nested_values():
    config = ChunkingConfig()
    original = source()
    result = IngestionService(config).prepare(original)
    mutations = (
        (config, "max_characters", 64),
        (config, "overlap_characters", 0),
        (original, "metadata", metadata()),
        (original, "content", b"changed"),
        (original, "media_type", "text/markdown"),
        (original.metadata, "title", "changed"),
        (result, "document", metadata()),
        (result, "chunks", ()),
        (result, "provenance", metadata()),
        (result.provenance, "doc_id", "changed-upstream-id"),
        (result.provenance, "version", "changed-upstream-version"),
        (result.document, "allowed_permissions", frozenset({Permission.PUBLIC})),
        (result.chunks[0], "text", "changed"),
    )
    for value, field, replacement in mutations:
        with pytest.raises(FrozenInstanceError):
            setattr(value, field, replacement)
    assert type(result.chunks) is tuple
    with pytest.raises(TypeError):
        result.chunks[0] = result.chunks[0]
    with pytest.raises(AttributeError):
        result.chunks[0].allowed_permissions.add(Permission.PUBLIC)
    assert original.content == b"Synthetic policy text.\n"
    assert result.document is not original.metadata
    assert result.provenance is original.metadata


def test_representations_errors_logs_and_output_do_not_disclose_source(caplog, capsys):
    sentinel = "SYNTHETIC_PRIVATE_SENTINEL"
    original = source(
        sentinel.encode(),
        title=sentinel,
        doc_id=sentinel,
        version=sentinel,
        source_url=f"https://example.invalid/{sentinel}",
        licence_note=sentinel,
    )
    service = IngestionService()
    result = service.prepare(original)
    assert repr(original) == "SourceDocument()"
    assert repr(result) == "PreparedDocument()"
    assert repr(result.document) == "DocumentMetadata()"
    assert repr(result.provenance) == "DocumentMetadata()"
    assert repr(result.chunks[0]) == "Chunk()"
    assert isinstance(result, PreparedDocument)
    for content in (sentinel.encode() + b"\xff", sentinel.encode() + b"\x00"):
        error = reject(lambda content=content: service.prepare(replace(original, content=content)))
        rendered = "".join(traceback.format_exception(error))
        assert sentinel not in rendered
        assert "UnicodeDecodeError" not in rendered
        assert "utf-8" not in rendered
        assert repr(error) == "IngestionError('Invalid ingestion input')"
    assert service.prepare_batch((original, original)) == (result,)
    assert not caplog.records
    captured = capsys.readouterr()
    assert captured.out == captured.err == ""
