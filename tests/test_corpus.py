"""Offline corpus manifest and ingestion tests; synthetic packages only."""

import hashlib
import io
import json
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from xml.sax.saxutils import escape

import pytest

from policypilot import manage
from policypilot.app.models.domain import Permission
from policypilot.app.services.content_store import ContentStore
from policypilot.app.services.corpus import (
    CorpusError,
    CorpusManifest,
    ingest_manifest,
    normalize_literal_headings,
    prepare_manifest,
    read_manifest,
    require_approved,
)
from policypilot.app.services.docx import EXTRACTOR_VERSION, extract_docx

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
REL = "http://schemas.openxmlformats.org/package/2006/relationships"
CT = "http://schemas.openxmlformats.org/package/2006/content-types"
MAIN = "application/vnd.openxmlformats-officedocument.wordprocessingml."
NOW = datetime(2026, 10, 25, 12, 0, tzinfo=UTC)


def docx_bytes(text: str) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_STORED) as archive:
        archive.writestr(
            "[Content_Types].xml",
            f'<Types xmlns="{CT}">'
            '<Default Extension="rels" '
            'ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            f'<Override PartName="/word/document.xml" ContentType="{MAIN}document.main+xml"/>'
            "</Types>",
        )
        archive.writestr(
            "_rels/.rels",
            f'<Relationships xmlns="{REL}"><Relationship Id="main" '
            f'Type="{R}/officeDocument" Target="word/document.xml"/></Relationships>',
        )
        archive.writestr(
            "word/document.xml",
            f'<w:document xmlns:w="{W}"><w:body><w:p><w:r>'
            f"<w:t>{escape(text)}</w:t></w:r></w:p></w:body></w:document>",
        )
    return output.getvalue()


def build_archive(path: Path, files: dict[str, bytes]) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_STORED) as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    data = output.getvalue()
    path.write_bytes(data)
    return data


def document(file: str, *, permissions: list[str], text: str) -> tuple[str, dict[str, object]]:
    raw = docx_bytes(text)
    extracted = extract_docx(raw)
    member = f"company_bronze/data/content/policies/{file}"
    return member, {
        "file": file,
        "archive_path": member,
        "sha256": hashlib.sha256(raw).hexdigest(),
        "extracted_sha256": hashlib.sha256(extracted).hexdigest(),
        "title": text,
        "source_url": f"https://example.invalid/{file.removesuffix('.docx')}",
        "retrieved_on": "2026-10-25",
        "licence_note": "Synthetic test corpus; CC BY 4.0.",
        "version": "2025-01/v1.0",
        "allowed_permissions": permissions,
        "rationale": "Synthetic review fixture.",
    }


def manifest_data(archive_sha: str, documents: list[dict[str, object]], **overrides):
    data = {
        "dataset": {
            "title": "Synthetic corpus",
            "publisher": "Test publisher",
            "source_page": "https://example.invalid/dataset",
            "release": "test",
            "archive": "archive.zip",
            "archive_sha256": archive_sha,
            "retrieved_on": "2026-10-25",
            "license": "CC-BY-4.0",
            "license_url": "https://creativecommons.org/licenses/by/4.0/",
            "approved_scope": "synthetic English policies",
            "extractor": EXTRACTOR_VERSION,
            "english_document_count": len(documents),
            "notes": [],
        },
        "status": "approved",
        "approved": True,
        "approved_by": "test-reviewer",
        "approved_on": "2026-10-25",
        "approval_note": "Synthetic approval for tests.",
        "documents": documents,
    }
    data.update(overrides)
    return data


def write_manifest(path: Path, data: dict[str, object]) -> CorpusManifest:
    path.write_text(json.dumps(data), encoding="utf-8")
    return read_manifest(path)


@pytest.fixture
def corpus(tmp_path: Path):
    first_member, first = document("alpha_policy_en.docx", permissions=["HR"], text="Alpha")
    second_member, second = document("beta_policy_en.docx", permissions=["Public"], text="Beta")
    archive_path = tmp_path / "archive.zip"
    archive = build_archive(
        archive_path, {first_member: docx_bytes("Alpha"), second_member: docx_bytes("Beta")}
    )
    # Recompute hashes from the exact archive members used above.
    with zipfile.ZipFile(archive_path) as zipped:
        first_raw = zipped.read(first_member)
        second_raw = zipped.read(second_member)
    first["sha256"] = hashlib.sha256(first_raw).hexdigest()
    first["extracted_sha256"] = hashlib.sha256(extract_docx(first_raw)).hexdigest()
    second["sha256"] = hashlib.sha256(second_raw).hexdigest()
    second["extracted_sha256"] = hashlib.sha256(extract_docx(second_raw)).hexdigest()
    data = manifest_data(hashlib.sha256(archive).hexdigest(), [first, second])
    manifest = write_manifest(tmp_path / "manifest.json", data)
    return archive_path, manifest, data


def test_read_prepare_and_ingest_approved_corpus(tmp_path, corpus):
    archive_path, manifest, _ = corpus
    prepared = prepare_manifest(manifest, archive_path, now=NOW)
    assert len(prepared) == 2
    store = ContentStore(tmp_path / "content.sqlite3")
    store.initialize()
    assert ingest_manifest(manifest, archive_path, store, now=NOW) == 2
    assert len(store.search("Alpha", frozenset({Permission.HR}))) == 1
    assert store.search("Alpha", frozenset({Permission.EMPLOYEE})) == ()
    assert len(store.search("Beta", frozenset({Permission.PUBLIC, Permission.EMPLOYEE}))) == 1


def test_proposed_or_unapproved_manifest_cannot_ingest(tmp_path, corpus):
    archive_path, _, data = corpus
    data.update({"status": "proposed", "approved": False, "approved_by": None, "approved_on": None})
    manifest = write_manifest(tmp_path / "proposed.json", data)
    with pytest.raises(CorpusError, match="Corpus manifest is not approved"):
        require_approved(manifest)
    store = ContentStore(tmp_path / "content.sqlite3")
    store.initialize()
    with pytest.raises(CorpusError, match="Corpus manifest is not approved"):
        ingest_manifest(manifest, archive_path, store, now=NOW)
    assert store.search("Alpha", frozenset({Permission.HR})) == ()


def test_archive_and_member_hash_mismatches_reject(tmp_path, corpus):
    archive_path, manifest, data = corpus
    data["dataset"]["archive_sha256"] = "0" * 64
    bad_archive = write_manifest(tmp_path / "bad-archive.json", data)
    with pytest.raises(CorpusError, match="Corpus ingestion failed"):
        prepare_manifest(bad_archive, archive_path, now=NOW)

    _, manifest, data = corpus
    data["documents"][0]["sha256"] = "0" * 64
    bad_member = write_manifest(tmp_path / "bad-member.json", data)
    with pytest.raises(CorpusError, match="Corpus ingestion failed"):
        prepare_manifest(bad_member, archive_path, now=NOW)

    _, manifest, data = corpus
    data["documents"][0]["extracted_sha256"] = "0" * 64
    bad_extract = write_manifest(tmp_path / "bad-extract.json", data)
    with pytest.raises(CorpusError, match="Corpus ingestion failed"):
        prepare_manifest(bad_extract, archive_path, now=NOW)


@pytest.mark.parametrize(
    "change",
    [
        {"status": "approved", "approved": False},
        {"status": "approved", "approved": True, "approved_by": ""},
        {"status": "approved", "approved": True, "approved_on": None},
        {"status": "proposed", "approved": True},
    ],
)
def test_inconsistent_approval_fields_reject(tmp_path, corpus, change):
    _, _, data = corpus
    data.update(change)
    with pytest.raises(CorpusError, match="Corpus ingestion failed"):
        write_manifest(tmp_path / "bad.json", data)


@pytest.mark.parametrize(
    "change",
    [
        {"documents": []},
        {"documents": [{"file": "missing"}]},
        {"unknown": True},
    ],
)
def test_schema_invalid_manifest_rejects(tmp_path, corpus, change):
    _, _, data = corpus
    data.update(change)
    with pytest.raises(CorpusError, match="Corpus ingestion failed"):
        write_manifest(tmp_path / "bad.json", data)


def test_duplicate_permissions_and_duplicate_documents_reject(tmp_path, corpus):
    _, _, data = corpus
    data["documents"][0]["allowed_permissions"] = ["HR", "HR"]
    with pytest.raises(CorpusError, match="Corpus ingestion failed"):
        write_manifest(tmp_path / "dupe-perms.json", data)

    _, _, data = corpus
    data["documents"][1] = dict(data["documents"][0])
    with pytest.raises(CorpusError, match="Corpus ingestion failed"):
        write_manifest(tmp_path / "dupe-docs.json", data)


@pytest.mark.parametrize(
    "field,value",
    [
        ("file", "alpha_policy_pl.docx"),
        ("archive_path", "company_bronze/data/content/policies/alpha_policy_pl.docx"),
        ("archive_path", "../alpha_policy_en.docx"),
        ("source_url", "not-a-url"),
        ("retrieved_on", "not-a-date"),
    ],
)
def test_unsafe_document_metadata_rejects(tmp_path, corpus, field, value):
    _, _, data = corpus
    data["documents"][0][field] = value
    with pytest.raises(CorpusError, match="Corpus ingestion failed"):
        write_manifest(tmp_path / "bad.json", data)


def test_missing_archive_and_naive_timestamp_reject(tmp_path, corpus):
    _, manifest, _ = corpus
    with pytest.raises(CorpusError, match="Corpus ingestion failed"):
        prepare_manifest(manifest, tmp_path / "missing.zip", now=NOW)
    with pytest.raises(CorpusError, match="Corpus ingestion failed"):
        prepare_manifest(manifest, tmp_path / "archive.zip", now=datetime(2026, 10, 25))


def test_extractor_version_must_match(tmp_path, corpus):
    _, _, data = corpus
    data["dataset"]["extractor"] = "docx-text-markdown-v1"
    with pytest.raises(CorpusError, match="Corpus ingestion failed"):
        write_manifest(tmp_path / "bad.json", data)


def test_document_count_must_match(tmp_path, corpus):
    _, _, data = corpus
    data["dataset"]["english_document_count"] = 3
    with pytest.raises(CorpusError, match="Corpus ingestion failed"):
        write_manifest(tmp_path / "bad.json", data)


def test_cli_init_inventory_and_ingest(tmp_path, corpus, capsys):
    archive_path, _, _ = corpus
    manifest_path = tmp_path / "manifest.json"
    content_path = tmp_path / "content.sqlite3"
    base = ["--content-database", str(content_path)]
    assert manage.main([*base, "init-content-db"]) == 0
    assert content_path.exists()
    assert manage.main([*base, "init-content-db"]) == 0
    assert (
        manage.main(
            [
                *base,
                "inventory-corpus",
                "--manifest",
                str(manifest_path),
                "--archive",
                str(archive_path),
            ]
        )
        == 0
    )
    assert "Verified 2 documents" in capsys.readouterr().out
    assert (
        manage.main(
            [
                *base,
                "ingest-corpus",
                "--manifest",
                str(manifest_path),
                "--archive",
                str(archive_path),
            ]
        )
        == 0
    )
    assert "Ingested 2 documents." in capsys.readouterr().out
    store = ContentStore(content_path)
    assert len(store.search("Alpha", frozenset({Permission.HR}))) == 1


def test_cli_refuses_unapproved_manifest_and_missing_files(tmp_path, corpus, capsys):
    archive_path, _, data = corpus
    data.update({"status": "proposed", "approved": False, "approved_by": None, "approved_on": None})
    proposed = tmp_path / "proposed.json"
    proposed.write_text(json.dumps(data), encoding="utf-8")
    content_path = tmp_path / "content.sqlite3"
    base = ["--content-database", str(content_path)]
    assert (
        manage.main(
            [
                *base,
                "ingest-corpus",
                "--manifest",
                str(proposed),
                "--archive",
                str(archive_path),
            ]
        )
        == 1
    )
    assert "Operation failed" in capsys.readouterr().err
    assert not content_path.exists()
    assert (
        manage.main(
            [
                *base,
                "inventory-corpus",
                "--manifest",
                str(tmp_path / "missing.json"),
                "--archive",
                str(archive_path),
            ]
        )
        == 1
    )
    assert "Operation failed" in capsys.readouterr().err


def test_literal_heading_normalization_is_data_only():
    assert normalize_literal_headings(b"\\#\\# Eligibility \\(EN\\)\n") == (
        b"## Eligibility (EN)\n"
    )
    assert normalize_literal_headings(b"plain body\n") == b"plain body\n"
    assert normalize_literal_headings(b"# Title \\(v1\\)\n") == b"# Title (v1)\n"


def test_approved_corpus_headings_become_sections(tmp_path):
    text = "# Annual Leave\n\n## Eligibility\n\nFull-time employees get 26 days."
    raw = docx_bytes(text)
    member = "company_bronze/data/content/policies/sections_policy_en.docx"
    archive_bytes = build_archive(tmp_path / "archive.zip", {member: raw})

    def make_doc():
        return {
            "file": "sections_policy_en.docx",
            "archive_path": member,
            "sha256": hashlib.sha256(raw).hexdigest(),
            "extracted_sha256": hashlib.sha256(extract_docx(raw)).hexdigest(),
            "title": "Sections policy",
            "source_url": "https://example.invalid/sections_policy_en",
            "retrieved_on": "2026-10-25",
            "licence_note": "Synthetic test corpus; CC BY 4.0.",
            "version": "2025-01/v1.0",
            "allowed_permissions": ["Employee"],
            "rationale": "Synthetic review fixture.",
        }

    data = manifest_data(hashlib.sha256(archive_bytes).hexdigest(), [make_doc()])
    manifest = write_manifest(tmp_path / "manifest.json", data)
    prepared = prepare_manifest(manifest, tmp_path / "archive.zip", now=NOW)
    sections = {chunk.section for chunk in prepared[0].chunks}
    assert "Annual Leave / Eligibility" in sections
    assert not any("\\" in section for section in sections)
