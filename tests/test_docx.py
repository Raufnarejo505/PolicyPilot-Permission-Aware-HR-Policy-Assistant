"""Synthetic OOXML only: no real documents, extraction, or evaluation data."""

import builtins
import io
import logging
import socket
import stat
import struct
import traceback
import urllib.request
import warnings
import zipfile
from xml.sax.saxutils import escape

import pytest

from policypilot.app.services import docx
from policypilot.app.services.docx import DocxError, extract_docx

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
REL = "http://schemas.openxmlformats.org/package/2006/relationships"
CT = "http://schemas.openxmlformats.org/package/2006/content-types"
MAIN = "application/vnd.openxmlformats-officedocument.wordprocessingml."


def paragraph(text="Synthetic policy", properties=""):
    return f'<w:p>{properties}<w:r><w:t xml:space="preserve">{escape(text)}</w:t></w:r></w:p>'


def style(key="Heading1", parent=None, outline=None, name=None, default=False):
    based = f'<w:basedOn w:val="{parent}"/>' if parent is not None else ""
    properties = f'<w:pPr><w:outlineLvl w:val="{outline}"/></w:pPr>' if outline is not None else ""
    display = f'<w:name w:val="{name}"/>' if name else ""
    return (
        f'<w:style w:type="paragraph" w:styleId="{key}" w:default="{int(default)}">'
        f"{based}{display}{properties}</w:style>"
    )


def parts(body=None, styles=None, relationships="", numbering=None):
    if body is None:
        body = paragraph()
    overrides = f'<Override PartName="/word/document.xml" ContentType="{MAIN}document.main+xml"/>'
    document_relationships = relationships
    if styles is not None:
        overrides += f'<Override PartName="/word/styles.xml" ContentType="{MAIN}styles+xml"/>'
        document_relationships += (
            f'<Relationship Id="styles" Type="{R}/styles" Target="styles.xml"/>'
        )
    if numbering is not None:
        overrides += f'<Override PartName="/word/numbering.xml" ContentType="{MAIN}numbering+xml"/>'
        document_relationships += (
            f'<Relationship Id="numbering" Type="{R}/numbering" Target="numbering.xml"/>'
        )
    result = {
        "[Content_Types].xml": (
            f'<Types xmlns="{CT}">'
            '<Default Extension="rels" '
            'ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            f'<Default Extension="xml" ContentType="application/xml"/>{overrides}</Types>'
        ).encode(),
        "_rels/.rels": (
            f'<Relationships xmlns="{REL}"><Relationship Id="main" '
            f'Type="{R}/officeDocument" Target="word/document.xml"/></Relationships>'
        ).encode(),
        "word/document.xml": (
            f'<w:document xmlns:w="{W}" xmlns:r="{R}"><w:body>{body}</w:body></w:document>'
        ).encode(),
    }
    if styles is not None:
        result["word/styles.xml"] = f'<w:styles xmlns:w="{W}">{styles}</w:styles>'.encode()
    if numbering is not None:
        result["word/numbering.xml"] = (
            f'<w:numbering xmlns:w="{W}">{numbering}</w:numbering>'
        ).encode()
    if document_relationships:
        result["word/_rels/document.xml.rels"] = (
            f'<Relationships xmlns="{REL}">{document_relationships}</Relationships>'
        ).encode()
    return result


def package(members=None, *, compression=zipfile.ZIP_STORED):
    output = io.BytesIO()
    if members is None:
        members = parts()
    if isinstance(members, dict):
        members = members.items()
    with zipfile.ZipFile(output, "w", compression=compression) as archive:
        for name, data in members:
            archive.writestr(name, data)
    return output.getvalue()


def reject(content):
    with pytest.raises(DocxError, match="^Invalid DOCX document$") as error:
        extract_docx(content)
    assert error.value.args == ("Invalid DOCX document",)
    assert error.value.__cause__ is None
    assert error.value.__context__ is None
    assert repr(error.value) == "DocxError('Invalid DOCX document')"
    return error.value


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("DOCX conversion attempted IO, extraction, or logging")

    for name in ("connect", "connect_ex", "sendto"):
        monkeypatch.setattr(socket.socket, name, forbidden)
    for name in ("create_connection", "getaddrinfo"):
        monkeypatch.setattr(socket, name, forbidden)
    monkeypatch.setattr(urllib.request, "urlopen", forbidden)
    monkeypatch.setattr(zipfile.ZipFile, "extract", forbidden)
    monkeypatch.setattr(zipfile.ZipFile, "extractall", forbidden)
    # Block logging from this module without interfering with pytest's own reporting.
    monkeypatch.setattr(logging.Logger, "_log", forbidden)


def test_basic_deterministic_bytes_and_version(monkeypatch):
    content = package(parts(paragraph("English café policy") + paragraph("Next")))

    def forbidden(*args, **kwargs):
        pytest.fail("Unexpected file read")

    monkeypatch.setattr(builtins, "open", forbidden)
    monkeypatch.setattr(io, "open", forbidden)
    first = extract_docx(content)
    assert first == "English café policy\n\nNext\n".encode()
    assert extract_docx(content) == first
    assert docx.EXTRACTOR_VERSION == "docx-text-markdown-v2"


def test_preserves_paragraphs_tabs_breaks_and_whitespace():
    body = (
        '<w:p><w:r><w:t xml:space="preserve">  Before </w:t><w:tab/>'
        "<w:t>after</w:t><w:br/><w:t>line</w:t><w:cr/><w:t>end</w:t></w:r></w:p>"
        "<w:p/>" + paragraph("last")
    )
    assert extract_docx(package(parts(body))) == b"  Before \tafter\nline\nend\n\n\n\nlast\n"


@pytest.mark.parametrize("level", range(1, 7))
def test_builtin_heading_styles(level):
    body = paragraph("Policy", f'<w:pPr><w:pStyle w:val="Heading{level}"/></w:pPr>')
    assert (
        extract_docx(package(parts(body, style(f"Heading{level}"))))
        == ("#" * level + " Policy\n").encode()
    )


def test_style_inheritance_display_name_default_and_direct_override():
    styles = (
        style("Base", outline=2)
        + style("Child", parent="Base")
        + style("Normal", default=True)
        + style("Localized", name="heading 2")
    )
    body = (
        paragraph("Body")
        + paragraph("Inherited", '<w:pPr><w:pStyle w:val="Child"/></w:pPr>')
        + paragraph("Named", '<w:pPr><w:pStyle w:val="Localized"/></w:pPr>')
        + paragraph("Direct", '<w:pPr><w:pStyle w:val="Child"/><w:outlineLvl w:val="0"/></w:pPr>')
        + paragraph(
            "Override body", '<w:pPr><w:pStyle w:val="Child"/><w:outlineLvl w:val="9"/></w:pPr>'
        )
    )
    assert extract_docx(package(parts(body, styles))) == (
        b"Body\n\n### Inherited\n\n## Named\n\n# Direct\n\nOverride body\n"
    )
    assert extract_docx(package(parts(paragraph("Default"), style(default=True)))) == b"# Default\n"


def test_literal_markdown_cannot_spoof_headings_or_fences():
    body = paragraph("# Literal\n ```\n# Still literal\n~~~\n[link](https://example.invalid)\n---")
    result = extract_docx(package(parts(body)))
    assert result == (
        b"\\# Literal\n \\`\\`\\`\n\\# Still literal\n\\~\\~\\~\n"
        b"\\[link\\]\\(https\\:\\/\\/example\\.invalid\\)\n\\-\\-\\-\n"
    )
    # Use the actual downstream section parser, without creating metadata or ingesting anything.
    from policypilot.app.services.ingestion import _sections

    assert tuple(label for label, _ in _sections(result.decode(), True)) == ("Document",)


def test_heading_text_is_escaped_without_spoofed_nested_structure():
    body = paragraph("Policy #\nFake", '<w:pPr><w:outlineLvl w:val="0"/></w:pPr>')
    reject(package(parts(body)))
    body = paragraph("Policy #", '<w:pPr><w:outlineLvl w:val="0"/></w:pPr>')
    assert extract_docx(package(parts(body))) == b"# Policy \\#\n"


def test_internal_hyperlink_labels_are_plain_escaped_text():
    link = f'<Relationship Id="link" Type="{R}/hyperlink" Target="#section"/>'
    body = (
        '<w:p><w:bookmarkStart w:id="0" w:name="section"/>'
        '<w:hyperlink r:id="link"><w:r><w:t>[Visible]</w:t></w:r></w:hyperlink>'
        '<w:hyperlink w:anchor="section"><w:r><w:t> anchor</w:t></w:r></w:hyperlink>'
        '<w:bookmarkEnd w:id="0"/></w:p>'
    )
    assert extract_docx(package(parts(body, relationships=link))) == b"\\[Visible\\] anchor\n"


@pytest.mark.parametrize(
    "target",
    [
        "https://example.invalid/private",
        "http://127.0.0.1/",
        "mailto:private@example.invalid",
    ],
)
def test_external_hyperlink_targets_are_validated_and_discarded(target):
    rel = (
        f'<Relationship Id="secret" Type="{R}/hyperlink" Target="{target}" TargetMode="External"/>'
    )
    body = '<w:p><w:hyperlink r:id="secret"><w:r><w:t>Visible</w:t></w:r></w:hyperlink></w:p>'
    output = extract_docx(package(parts(body, relationships=rel)))
    assert output == b"Visible\n"
    assert target.encode() not in output


@pytest.mark.parametrize(
    "relationship",
    [
        f'<Relationship Id="x" Type="{R}/hyperlink" Target="https://example.invalid"/>',
        f'<Relationship Id="x" Type="{R}/image" Target="image.png"/>',
        f'<Relationship Id="x" Type="{R}/oleObject" Target="object.bin"/>',
        f'<Relationship Id="x" Type="{R}/header" Target="header1.xml"/>',
        f'<Relationship Id="x" Type="{R}/attachedTemplate" Target="template.dotx"/>',
        f'<Relationship Id="x" Type="{R}/settings" Target="../settings.xml"/>',
        f'<Relationship Id="x" Type="{R}/settings" Target="/word/settings.xml"/>',
        f'<Relationship Id="x" Type="{R}/settings" Target="missing.xml"/>',
        f'<Relationship Id="x" Type="{R}/hyperlink" Target="#ok" TargetMode="Unknown"/>',
        f'<Relationship Id="x" Type="{R}/hyperlink" Target="#ok"/>' * 2,
        f'<Relationship Id="x" Type="{R}/hyperlink" Target="file:///etc/passwd" '
        'TargetMode="External"/>',
        f'<Relationship Id="x" Type="{R}/hyperlink" Target="javascript:alert(1)" '
        'TargetMode="External"/>',
    ],
)
def test_unsafe_unknown_or_malformed_relationships(relationship):
    reject(package(parts(relationships=relationship)))


@pytest.mark.parametrize(
    "markup",
    [
        "<w:tbl/>",
        "<w:sdt/>",
        "<w:altChunk/>",
        "<w:ins/>",
        "<w:del/>",
        "<w:customXml/>",
        "<w:unknown/>",
        "<w:p><w:fldSimple/></w:p>",
        "<w:p><w:moveFrom/></w:p>",
        "<w:p><w:r><w:instrText>secret</w:instrText></w:r></w:p>",
        "<w:p><w:r><w:fldChar/></w:r></w:p>",
        "<w:p><w:r><w:drawing/></w:r></w:p>",
        "<w:p><w:r><w:pict/></w:r></w:p>",
        "<w:p><w:r><w:object/></w:r></w:p>",
        "<w:p><w:r><w:footnoteReference/></w:r></w:p>",
        "<w:p><w:r><w:commentReference/></w:r></w:p>",
        "<w:p><w:pPr><w:numPr/></w:pPr></w:p>",
        "<w:p><w:pPr><w:pPrChange/></w:pPr></w:p>",
        "<w:p><w:r><w:rPr><w:vanish/></w:rPr><w:t>hidden</w:t></w:r></w:p>",
        '<w:sectPr><w:headerReference r:id="header"/></w:sectPr>',
        '<w:sectPr><w:footerReference r:id="footer"/></w:sectPr>',
        '<w:p><w:r><w:br w:type="page"/></w:r></w:p>',
        '<w:p><w:r><w:br w:type="column"/></w:r></w:p>',
        '<w:p><w:hyperlink r:id="missing"><w:r><w:t>label</w:t></w:r></w:hyperlink></w:p>',
        "<w:p><w:hyperlink><w:r><w:t>label</w:t></w:r></w:hyperlink></w:p>",
        "<w:p><w:r><w:t>good<w:r/></w:t></w:r></w:p>",
        "<w:p><w:r><w:t>good</w:t>lost</w:r></w:p>",
        "<w:p><w:r><w:t>good</w:t></w:r><w:pPr/></w:p>",
        "<w:p><w:pPr/><w:pPr/></w:p>",
        "<w:sectPr/>" + paragraph(),
        '<w:p xmlns:mc="urn:compat"><mc:AlternateContent/></w:p>',
    ],
)
def test_unsupported_structure_is_not_silently_dropped(markup):
    reject(package(parts(markup)))


@pytest.mark.parametrize(
    "styles",
    [
        style("A", parent="A"),
        style("A", parent="B") + style("B", parent="A"),
        style("A", parent="Missing"),
        style("A", outline=6),
        style("A", outline=7),
        style("A", outline=8),
        style("A", outline=-1),
        style("A", outline="invalid"),
        "".join(style(str(i), parent=str(i + 1)) for i in range(33)) + style("33"),
    ],
)
def test_invalid_styles_reject_when_applied(styles):
    body = paragraph("Text", '<w:pPr><w:pStyle w:val="A"/></w:pPr>')
    reject(package(parts(body, styles)))


@pytest.mark.parametrize(
    "styles",
    [style("A") * 2, style("A", default=True) + style("B", default=True)],
)
def test_duplicate_or_ambiguous_styles_reject(styles):
    reject(package(parts(styles=styles)))


def test_unused_invalid_template_styles_are_ignored():
    styles = style("A", parent="Missing") + style("B", outline=8)
    assert extract_docx(package(parts(styles=styles))) == b"Synthetic policy\n"


def test_missing_style_and_body_outline_limits():
    reject(package(parts(paragraph("Text", '<w:pPr><w:pStyle w:val="Missing"/></w:pPr>'))))
    reject(package(parts(paragraph("Text", '<w:pPr><w:outlineLvl w:val="8"/></w:pPr>'))))
    reject(package(parts(paragraph(" ", '<w:pPr><w:outlineLvl w:val="0"/></w:pPr>'))))


@pytest.mark.parametrize(
    "name",
    [
        "word/document.xml",
        "word/styles.xml",
        "_rels/.rels",
        "[Content_Types].xml",
        "word/_rels/document.xml.rels",
    ],
)
@pytest.mark.parametrize(
    "declaration",
    [
        '<!DOCTYPE secret [<!ENTITY payload "PRIVATE_SENTINEL">]>',
        '<!DOCTYPE secret SYSTEM "file:///etc/passwd">',
        '<!DOCTYPE secret [<!ENTITY payload SYSTEM "https://example.invalid/private">]>',
        '<!ENTITY payload "PRIVATE_SENTINEL">',
    ],
)
def test_dtd_and_entities_rejected_in_every_read_part(name, declaration):
    members = parts(styles=style())
    members[name] = declaration.encode() + members[name]
    error = reject(package(members))
    assert "PRIVATE_SENTINEL" not in "".join(traceback.format_exception(error))


@pytest.mark.parametrize(
    "payload",
    [
        b"\xffPRIVATE_SENTINEL",
        b"<unclosed",
        b"",
        b"<root/>",
        b"<?xml version='1.0'?><x>\x00</x>",
        b"<x>PRIVATE_SENTINEL &missing;</x>",
        b"<x/><x/>",
        b"<x>\x7f</x>",
    ],
)
@pytest.mark.parametrize(
    "name", ["word/document.xml", "word/styles.xml", "_rels/.rels", "[Content_Types].xml"]
)
def test_malformed_xml_errors_have_no_payload_or_context(name, payload):
    members = parts(styles=style())
    members[name] = payload
    error = reject(package(members))
    assert "PRIVATE_SENTINEL" not in str(error) + repr(error)


def test_utf16_dtd_is_not_a_scanner_bypass():
    members = parts()
    members["word/document.xml"] = (
        '<?xml version="1.0" encoding="utf-16"?><!DOCTYPE x [<!ENTITY y "secret">]><x/>'
    ).encode("utf-16")
    reject(package(members))


@pytest.mark.parametrize(
    "name",
    [
        "../secret",
        "/secret",
        "word/../secret",
        "word//secret",
        "word/./secret",
        "word\\secret",
        "C:/secret",
        "word/%2e%2e/secret",
        "word/secret?x",
        "word/secret#x",
        "word/\x00secret",
        "word/\x01secret",
        "word/é.xml",
        "word/vbaProject.bin",
        "word/embeddings/object.bin",
        "word/media/image.png",
        "word/header1.xml",
        "word/footnotes.xml",
    ],
)
def test_unsafe_paths_and_unsupported_parts(name):
    members = list(parts().items()) + [(name, b"private")]
    reject(package(members))


@pytest.mark.parametrize(
    "relationships",
    [
        "",
        f'<Relationship Id="custom" Type="{R}/customXml" Target="../customXml/item1.xml"/>',
    ],
)
def test_custom_xml_is_accepted_but_never_read(monkeypatch, relationships):
    members = dict(parts(relationships=relationships))
    members["customXml/item1.xml"] = b"PRIVATE_SENTINEL"
    content = package(members)
    original = zipfile.ZipFile.open
    reads: list[str] = []

    def recording(self, name, *args, **kwargs):
        reads.append(name)
        return original(self, name, *args, **kwargs)

    monkeypatch.setattr(zipfile.ZipFile, "open", recording)
    output = extract_docx(content)
    assert output == b"Synthetic policy\n"
    assert "customXml/item1.xml" not in reads
    assert "PRIVATE_SENTINEL" not in output.decode()


def test_duplicate_members_reject():
    members = list(parts().items())
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        content = package(members + [members[-1]])
    reject(content)


def test_symlink_and_other_special_files_reject():
    for mode in (stat.S_IFLNK, stat.S_IFIFO, stat.S_IFCHR):
        item = zipfile.ZipInfo("word/settings.xml")
        item.create_system = 3
        item.external_attr = (mode | 0o777) << 16
        reject(package(list(parts().items()) + [(item, b"target")]))


def patch_headers(content, *, flags=None, expanded=None, compressed=None):
    """Mutate synthetic local/central headers, never real archive bytes."""
    changed = bytearray(content)
    for signature, flag_offset, compressed_offset, expanded_offset in (
        (b"PK\x03\x04", 6, 18, 22),
        (b"PK\x01\x02", 8, 20, 24),
    ):
        start = 0
        while (offset := changed.find(signature, start)) >= 0:
            if flags is not None:
                struct.pack_into("<H", changed, offset + flag_offset, flags)
            if expanded is not None:
                struct.pack_into("<I", changed, offset + expanded_offset, expanded)
            if compressed is not None:
                struct.pack_into("<I", changed, offset + compressed_offset, compressed)
            start = offset + 4
    return bytes(changed)


@pytest.mark.parametrize("flags", [1, 64, 65])
def test_encrypted_members_reject(flags):
    reject(patch_headers(package(), flags=flags))


def test_caps_and_compression_bomb():
    reject(b"x" * (1_048_576 + 1))
    reject(package(list(parts().items()) + [(f"dir{i}/", b"") for i in range(254)]))
    reject(patch_headers(package(), expanded=2_097_153))
    reject(patch_headers(package(), expanded=201, compressed=1))
    reject(package(parts(paragraph("x" * 500_000)), compression=zipfile.ZIP_DEFLATED))
    members = list(parts().items()) + [
        (name, b"") for name in ("word/settings.xml", "word/fontTable.xml", "word/numbering.xml")
    ]
    reject(patch_headers(package(members), expanded=1_500_000, compressed=10_000))


def test_resource_boundaries_with_small_test_limits(monkeypatch):
    content = package()
    entries = len(parts())
    expanded = sum(map(len, parts().values()))
    biggest = max(map(len, parts().values()))
    monkeypatch.setattr(docx, "_MAX_INPUT", len(content))
    monkeypatch.setattr(docx, "_MAX_TOTAL", expanded)
    monkeypatch.setattr(docx, "_MAX_MEMBER", biggest)
    monkeypatch.setattr(docx, "_MAX_ENTRIES", entries)
    assert extract_docx(content) == b"Synthetic policy\n"
    for name, limit in (
        ("_MAX_INPUT", len(content)),
        ("_MAX_TOTAL", expanded),
        ("_MAX_MEMBER", biggest),
        ("_MAX_ENTRIES", entries),
    ):
        monkeypatch.setattr(docx, name, limit - 1)
        reject(content)
        monkeypatch.setattr(docx, name, limit)


def test_ratio_boundary_and_actual_crc_and_size_checks():
    # Exact ratio is accepted by the metadata guard; corrupt sizes still fail at read time.
    reject(patch_headers(package(), expanded=200, compressed=1))
    content = bytearray(package())
    at = content.index(b"Synthetic policy")
    content[at] = ord("X")
    reject(bytes(content))
    reject(patch_headers(package(), expanded=1))


def test_xml_depth_and_node_budgets(monkeypatch):
    members = parts()
    members["word/document.xml"] = ("<x>" * 65 + "</x>" * 65).encode()
    reject(package(members))
    content = package(parts(paragraph() * 10))
    monkeypatch.setattr(docx, "_MAX_NODES", 20)
    reject(content)


def test_output_cap_after_markdown_amplification(monkeypatch):
    members = parts(paragraph("#" * 1200))
    content = package(members, compression=zipfile.ZIP_DEFLATED)
    assert len(content) < 2000
    monkeypatch.setattr(docx, "_MAX_INPUT", 2000)
    reject(content)


@pytest.mark.parametrize("missing", ["[Content_Types].xml", "_rels/.rels", "word/document.xml"])
def test_missing_required_part(missing):
    members = parts()
    del members[missing]
    reject(package(members))


def test_macro_content_type_and_invalid_relationship_types():
    members = parts()
    members["[Content_Types].xml"] = members["[Content_Types].xml"].replace(
        (MAIN + "document.main+xml").encode(),
        b"application/vnd.ms-word.document.macroEnabled.main+xml",
    )
    reject(package(members))
    members = parts()
    members["_rels/.rels"] = f'<Relationships xmlns="{REL}"/>'.encode()
    reject(package(members))


def test_only_core_and_relationship_parts_are_read(monkeypatch):
    members = parts(styles=style())
    members["word/settings.xml"] = b"not parsed ancillary bytes"
    members["[Content_Types].xml"] = members["[Content_Types].xml"].replace(
        b"</Types>",
        f'<Override PartName="/word/settings.xml" ContentType="{MAIN}settings+xml"/>'
        "</Types>".encode(),
    )
    content = package(members)
    original = zipfile.ZipFile.open
    reads = []

    def recording(self, name, *args, **kwargs):
        reads.append(name)
        return original(self, name, *args, **kwargs)

    monkeypatch.setattr(zipfile.ZipFile, "open", recording)
    assert extract_docx(content) == b"Synthetic policy\n"
    assert set(reads) == set(members) - {"word/settings.xml"}


def test_supported_nontext_formatting_and_section_layout():
    body = (
        '<w:p><w:pPr><w:spacing w:after="20"/><w:tabs><w:tab w:pos="720"/></w:tabs>'
        '</w:pPr><w:r><w:rPr><w:b/><w:lang w:val="en-US"/></w:rPr>'
        '<w:t>Policy</w:t></w:r></w:p><w:sectPr><w:pgSz w:w="12240"/>'
        '<w:cols><w:col w:w="720"/></w:cols></w:sectPr>'
    )
    assert extract_docx(package(parts(body))) == b"Policy\n"


@pytest.mark.parametrize(
    "value",
    [
        None,
        "bytes",
        bytearray(b"bytes"),
        0,
        b"",
        b"not zip",
        b"PK\x03\x04",
        b"PK\x05\x06" + bytes(18),
    ],
)
def test_invalid_or_incomplete_input(value):
    reject(value)


def test_every_truncated_prefix_fails_without_context():
    content = package()
    for length in range(len(content)):
        reject(content[:length])


def test_unused_template_numbering_style_is_safe_but_cannot_be_applied():
    styles = (
        style("Normal", default=True)
        + '<w:style w:type="paragraph" w:styleId="ListBullet"><w:pPr><w:numPr>'
        '<w:numId w:val="1"/></w:numPr></w:pPr></w:style>'
        + style("InheritedList", parent="ListBullet")
    )
    assert extract_docx(package(parts(styles=styles))) == b"Synthetic policy\n"
    for key in ("ListBullet", "InheritedList"):
        body = paragraph("Not a plain paragraph", f'<w:pPr><w:pStyle w:val="{key}"/></w:pPr>')
        reject(package(parts(body, styles)))


def test_unused_hidden_style_is_ignored_but_applied_hidden_text_rejects():
    hidden = '<w:style w:type="character" w:styleId="Hidden"><w:rPr><w:vanish/></w:rPr></w:style>'
    assert extract_docx(package(parts(styles=hidden))) == b"Synthetic policy\n"
    body = '<w:p><w:r><w:rPr><w:rStyle w:val="Hidden"/></w:rPr><w:t>hidden secret</w:t></w:r></w:p>'
    reject(package(parts(body, hidden)))
    defaults = "<w:docDefaults><w:rPrDefault><w:rPr><w:webHidden/></w:rPr>"
    reject(package(parts(styles=defaults + "</w:rPrDefault></w:docDefaults>")))


def test_real_input_entry_and_member_boundaries():
    members = parts()
    members["docProps/thumbnail.jpeg"] = b""
    members["[Content_Types].xml"] = members["[Content_Types].xml"].replace(
        b"</Types>", b'<Default Extension="jpeg" ContentType="image/jpeg"/></Types>'
    )
    members["docProps/thumbnail.jpeg"] = b"x" * (1_048_576 - len(package(members)))
    content = package(members)
    assert len(content) == 1_048_576
    assert extract_docx(content) == b"Synthetic policy\n"
    reject(content + b"x")
    entries = list(parts().items()) + [(f"dir{i}/", b"") for i in range(253)]
    assert extract_docx(package(entries)) == b"Synthetic policy\n"
    reject(package(entries + [("too-many/", b"")]))


def test_declared_resource_limits_reject_before_member_read(monkeypatch):
    def forbidden(*args):
        pytest.fail("Resource guard must run before decompressing any member")

    inputs = [
        patch_headers(package(), expanded=2_097_153, compressed=20_000),
        patch_headers(package(), expanded=201, compressed=1),
        patch_headers(
            package(
                list(parts().items())
                + [
                    (name, b"")
                    for name in ("word/settings.xml", "word/fontTable.xml", "word/numbering.xml")
                ]
            ),
            expanded=1_500_000,
            compressed=10_000,
        ),
    ]
    monkeypatch.setattr(docx, "_read", forbidden)
    for content in inputs:
        reject(content)


def test_exact_declared_ratio_and_member_limits_reach_bounded_reader(monkeypatch):
    reads = []

    def stop(*args):
        reads.append(True)
        raise docx._Rejected

    monkeypatch.setattr(docx, "_read", stop)
    reject(patch_headers(package(), expanded=200, compressed=1))
    assert reads == [True]
    reads.clear()
    reject(patch_headers(package(), expanded=2_097_152, compressed=20_000))
    assert reads == [True]


def test_xml_node_and_depth_exact_boundaries(monkeypatch):
    content = package()
    # Types has four nodes, root relationships two, document five.
    monkeypatch.setattr(docx, "_MAX_NODES", 5)
    monkeypatch.setattr(docx, "_MAX_DEPTH", 5)
    assert extract_docx(content) == b"Synthetic policy\n"
    monkeypatch.setattr(docx, "_MAX_DEPTH", 4)
    reject(content)
    monkeypatch.setattr(docx, "_MAX_DEPTH", 5)
    monkeypatch.setattr(docx, "_MAX_NODES", 4)
    reject(content)


def test_malformed_body_and_non_utf8_xml_declaration():
    members = parts()
    members["word/document.xml"] = (
        b'<?xml version="1.0" encoding="iso-8859-1"?>' + members["word/document.xml"]
    )
    reject(package(members))
    members = parts()
    members["word/document.xml"] = members["word/document.xml"].replace(
        b"</w:document>", b"<w:body/></w:document>"
    )
    reject(package(members))


def test_empty_or_incomplete_document_and_unsupported_compression():
    reject(package(parts("")))
    reject(package(parts("<w:p/>")))
    members = parts()
    members["word/document.xml"] = f'<w:document xmlns:w="{W}"/>'.encode()
    reject(package(members))
    reject(package(compression=zipfile.ZIP_BZIP2))


def numbered_paragraph(text="Item", *, identifier="1", level="0"):
    return (
        f'<w:p><w:pPr><w:numPr><w:ilvl w:val="{level}"/>'
        f'<w:numId w:val="{identifier}"/></w:numPr></w:pPr>'
        f"<w:r><w:t>{escape(text)}</w:t></w:r></w:p>"
    )


def abstract_numbering(
    *, fmt="decimal", label="%1.", start="1", ilvl="0", multi="singleLevel", level_extra=""
):
    multi_tag = "" if multi is None else f'<w:multiLevelType w:val="{multi}"/>'
    return (
        f'<w:abstractNum w:abstractNumId="1">{multi_tag}<w:lvl w:ilvl="{ilvl}">'
        f'<w:start w:val="{start}"/><w:numFmt w:val="{fmt}"/>'
        f'<w:lvlText w:val="{label}"/><w:suff w:val="tab"/>{level_extra}'
        "</w:lvl></w:abstractNum>"
    )


def number_instance(*, identifier="1", abstract="1", override=""):
    return f'<w:num w:numId="{identifier}"><w:abstractNumId w:val="{abstract}"/>{override}</w:num>'


def test_numbered_and_bulleted_paragraphs_render():
    numbering = abstract_numbering() + number_instance()
    body = numbered_paragraph("First") + numbered_paragraph("Second")
    assert extract_docx(package(parts(body, numbering=numbering))) == (b"1. First\n\n2. Second\n")
    bullet = abstract_numbering(fmt="bullet", label="•") + number_instance()
    assert extract_docx(package(parts(numbered_paragraph("Only"), numbering=bullet))) == (
        b"- Only\n"
    )


def test_numbering_start_override_and_space_suffix():
    override = '<w:lvlOverride w:ilvl="0"><w:startOverride w:val="5"/></w:lvlOverride>'
    numbering = abstract_numbering(level_extra="").replace(
        '<w:suff w:val="tab"/>', '<w:suff w:val="space"/>'
    ) + number_instance(override=override)
    assert extract_docx(package(parts(numbered_paragraph(), numbering=numbering))) == (b"5. Item\n")


@pytest.mark.parametrize(
    "numbering",
    [
        "",
        abstract_numbering(ilvl="1") + number_instance(),
        abstract_numbering(multi="multiple") + number_instance(),
        abstract_numbering() + number_instance(abstract="9"),
        abstract_numbering(fmt="bullet", label="not-a-bullet") + number_instance(),
        abstract_numbering(fmt="roman") + number_instance(),
        abstract_numbering(level_extra='<w:start w:val="2"/>') + number_instance(),
        abstract_numbering() + number_instance(identifier="0"),
        abstract_numbering(start="2147483648") + number_instance(),
        abstract_numbering() + abstract_numbering() + number_instance(),
        abstract_numbering() + number_instance() + number_instance(),
        '<w:abstractNum w:abstractNumId="1"><w:multiLevelType w:val="singleLevel"/>'
        "</w:abstractNum>" + number_instance(),
        abstract_numbering()
        + number_instance(
            override='<w:lvlOverride w:ilvl="0"><w:startOverride w:val="bad"/></w:lvlOverride>'
        ),
        abstract_numbering()
        + number_instance(
            override='<w:lvlOverride w:ilvl="1"><w:startOverride w:val="5"/></w:lvlOverride>'
        ),
    ],
)
def test_invalid_numbering_rejects(numbering):
    reject(package(parts(numbered_paragraph(), numbering=numbering)))


def test_list_reference_without_numbering_part_rejects():
    reject(package(parts(numbered_paragraph())))


def test_doc_defaults_and_latent_styles_are_validated():
    styles = (
        "<w:docDefaults><w:rPrDefault><w:rPr><w:b/></w:rPr></w:rPrDefault>"
        '<w:pPrDefault><w:pPr><w:spacing w:after="0"/></w:pPr></w:pPrDefault>'
        "</w:docDefaults>"
        '<w:latentStyles w:defLockedState="0"><w:lsdException w:name="Heading 1"/>'
        "</w:latentStyles>"
    )
    assert extract_docx(package(parts(styles=styles))) == b"Synthetic policy\n"


@pytest.mark.parametrize(
    "defaults",
    [
        '<w:docDefaults><w:pPrDefault><w:pPr><w:outlineLvl w:val="1"/>'
        "</w:pPr></w:pPrDefault></w:docDefaults>",
        '<w:docDefaults><w:pPrDefault><w:pPr><w:pStyle w:val="A"/>'
        "</w:pPr></w:pPrDefault></w:docDefaults>",
        '<w:docDefaults><w:pPrDefault><w:pPr><w:numPr><w:numId w:val="1"/>'
        "</w:numPr></w:pPr></w:pPrDefault></w:docDefaults>",
    ],
)
def test_invalid_doc_defaults_reject(defaults):
    reject(package(parts(styles=defaults)))


def test_paragraph_run_and_section_properties_are_validated():
    body = (
        "<w:p><w:pPr><w:rPr><w:b/></w:rPr>"
        '<w:sectPr><w:pgSz w:w="12240"/></w:sectPr></w:pPr>'
        "<w:r><w:t>Text</w:t></w:r></w:p>"
    )
    assert extract_docx(package(parts(body))) == b"Text\n"
    reject(package(parts(body.replace("<w:b/>", "<w:vanish/>"))))


def test_heading_named_style_and_paragraph_run_format():
    styles = (
        '<w:style w:type="paragraph" w:styleId="Custom" w:default="0">'
        '<w:name w:val="Heading 2"/><w:rPr><w:b/></w:rPr>'
        "<w:pPr><w:rPr><w:i/></w:rPr></w:pPr></w:style>"
    )
    body = paragraph("Title", '<w:pPr><w:pStyle w:val="Custom"/></w:pPr>')
    assert extract_docx(package(parts(body, styles))) == b"## Title\n"


def test_style_property_cannot_reference_another_paragraph_style():
    styles = (
        '<w:style w:type="paragraph" w:styleId="A" w:default="0">'
        '<w:pPr><w:pStyle w:val="B"/></w:pPr></w:style>'
    )
    body = paragraph("Text", '<w:pPr><w:pStyle w:val="A"/></w:pPr>')
    reject(package(parts(body, styles)))


def test_custom_xml_properties_are_validated_but_not_read():
    members = dict(parts())
    members["customXml/item1.xml"] = b"<x/>"
    members["customXml/itemProps1.xml"] = b"<p/>"
    members["customXml/_rels/item1.xml.rels"] = (
        f'<Relationships xmlns="{REL}"><Relationship Id="props" '
        f'Type="{R}/customXmlProps" Target="itemProps1.xml"/></Relationships>'
    ).encode()
    members["[Content_Types].xml"] = members["[Content_Types].xml"].replace(
        b"</Types>",
        b'<Override PartName="/customXml/itemProps1.xml" '
        b'ContentType="application/vnd.openxmlformats-officedocument.'
        b'customXmlProperties+xml"/></Types>',
    )
    assert extract_docx(package(members)) == b"Synthetic policy\n"
