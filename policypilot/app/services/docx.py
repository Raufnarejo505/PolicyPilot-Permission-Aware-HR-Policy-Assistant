"""Offline, text-only OOXML -> UTF-8 Markdown (``docx-text-markdown-v2``).

Only callers can establish English-policy approval/licensing; no language detection,
permission inference, persistence, logging, filesystem reads, or fetching occurs here.
No ZIP member is extracted. Input <= 1 MiB; <= 256 entries; declared expansion <=
8 MiB total, <= 2 MiB/member, <= 200:1/member. XML is UTF-8 only, DTD/entity-free,
<= 50,000 nodes/part and depth 64. Output <= 1 MiB (ingestion's source limit).

This is deliberately NOT a general Word renderer. Reject rendered tables, nested
or exotic numbering, tracked changes, fields, drawings/images, objects/macros,
altChunk, content controls, notes/comments, text boxes, header/footer references,
hidden text and unknown structural markup. Unused template styles (including table
styles and Heading7..9) do not render and are not applied. Ancillary layout/metadata
and unbound custom-XML parts are not read. Content controls/bindings still reject.
Inline font/emphasis and page layout are not represented. Single-level bullet and
decimal lists retain generated markers/counters, including start overrides. Link
labels are plain text, never clickable Markdown. Valid http/https/mailto hyperlink
targets are discarded, never fetched; all non-hyperlink external relations reject.
Internal bookmarks are non-text markers.
Paragraphs are separated by two LF, tabs and text line breaks survive, page/column
breaks reject. Headings use paragraph styles (including bounded inheritance) or
outline levels 0..5; outline 9 means body, 6..8 reject. Multiline headings reject.
ASCII Markdown punctuation is escaped in source text, so literal headings/fences,
links, HTML and setext syntax cannot manufacture downstream section structure.

Deterministic output appends a terminal LF (retaining empty paragraphs). This
module's version is the extraction contract, not a document identity. A parent
adapter must retain original-byte hash, provenance and EXTRACTOR_VERSION in its
version recipe, then pass output explicitly
as text/markdown to ingestion. Do not change ingestion or infer authorization here.
"""

import io
import posixpath
import re
import stat
import struct
import zipfile
import zlib
from contextlib import suppress
from dataclasses import dataclass
from urllib.parse import urlsplit
from xml.etree import ElementTree as ET

EXTRACTOR_VERSION = "docx-text-markdown-v2"
_INVALID = "Invalid DOCX document"
_MAX_INPUT = 1_048_576
_MAX_MEMBER = 2_097_152
_MAX_TOTAL = 8_388_608
_MAX_ENTRIES = 256
_MAX_RATIO = 200
_MAX_NODES = 50_000
_MAX_DEPTH = 64
_MAX_STYLE_CHAIN = 32
_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
_REL = "{http://schemas.openxmlformats.org/package/2006/relationships}"
_CT = "{http://schemas.openxmlformats.org/package/2006/content-types}"
_MAIN_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml."
_PART_TYPES = {
    "word/document.xml": _MAIN_TYPE + "document.main+xml",
    "word/styles.xml": _MAIN_TYPE + "styles+xml",
    "word/stylesWithEffects.xml": "application/vnd.ms-word.stylesWithEffects+xml",
    "word/settings.xml": _MAIN_TYPE + "settings+xml",
    "word/webSettings.xml": _MAIN_TYPE + "webSettings+xml",
    "word/fontTable.xml": _MAIN_TYPE + "fontTable+xml",
    "word/numbering.xml": _MAIN_TYPE + "numbering+xml",
    "word/theme/theme1.xml": "application/vnd.openxmlformats-officedocument.theme+xml",
    "docProps/core.xml": "application/vnd.openxmlformats-package.core-properties+xml",
    "docProps/app.xml": "application/vnd.openxmlformats-officedocument.extended-properties+xml",
    "docProps/custom.xml": "application/vnd.openxmlformats-officedocument.custom-properties+xml",
    "docProps/thumbnail.jpeg": "image/jpeg",
}
_REL_TYPES = {
    _R[1:-1] + "/" + name
    for name in (
        "officeDocument",
        "styles",
        "settings",
        "webSettings",
        "fontTable",
        "numbering",
        "theme",
        "extended-properties",
        "custom-properties",
        "thumbnail",
        "hyperlink",
        "customXml",
        "customXmlProps",
    )
} | {
    "http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties",
    "http://schemas.openxmlformats.org/package/2006/relationships/metadata/thumbnail",
    "http://schemas.microsoft.com/office/2007/relationships/stylesWithEffects",
}
_P_FORMAT = frozenset(
    [
        "keepNext",
        "keepLines",
        "pageBreakBefore",
        "widowControl",
        "suppressLineNumbers",
        "pBdr",
        "shd",
        "tabs",
        "suppressAutoHyphens",
        "kinsoku",
        "wordWrap",
        "overflowPunct",
        "topLinePunct",
        "autoSpaceDE",
        "autoSpaceDN",
        "bidi",
        "adjustRightInd",
        "snapToGrid",
        "spacing",
        "ind",
        "contextualSpacing",
        "mirrorIndents",
        "jc",
        "textDirection",
        "textAlignment",
        "textboxTightWrap",
        "suppressOverlap",
    ]
)
_R_FORMAT = frozenset(
    [
        "rStyle",
        "rFonts",
        "b",
        "bCs",
        "i",
        "iCs",
        "caps",
        "smallCaps",
        "strike",
        "dstrike",
        "outline",
        "shadow",
        "emboss",
        "imprint",
        "noProof",
        "snapToGrid",
        "color",
        "spacing",
        "w",
        "kern",
        "position",
        "sz",
        "szCs",
        "highlight",
        "u",
        "effect",
        "bdr",
        "shd",
        "fitText",
        "vertAlign",
        "rtl",
        "cs",
        "em",
        "lang",
        "eastAsianLayout",
    ]
)
_SECT_FORMAT = frozenset(
    [
        "type",
        "pgSz",
        "pgMar",
        "paperSrc",
        "pgBorders",
        "lnNumType",
        "pgNumType",
        "cols",
        "formProt",
        "vAlign",
        "noEndnote",
        "titlePg",
        "textDirection",
        "bidi",
        "rtlGutter",
        "docGrid",
    ]
)
_MARKDOWN = re.compile(r"([!\"#$%&'()*+,\-./:;<=>?@\[\\\]^_`{|}~])")
_DECLARATION = re.compile(r"<!\s*(?:DOCTYPE|ENTITY)\b", re.IGNORECASE)


class DocxError(ValueError):
    """Content-free public failure; underlying parser exceptions are not chained."""

    def __init__(self) -> None:
        super().__init__(_INVALID)


class _Rejected(Exception):
    pass


def _require(condition: bool) -> None:
    if not condition:
        raise _Rejected


def _safe_path(name: str, *, directory: bool = False) -> None:
    _require(bool(name) and name.isascii())
    _require(not any(ord(char) < 32 or char in "\\:%?#\x7f" for char in name))
    path = name[:-1] if directory and name.endswith("/") else name
    _require(all(part not in ("", ".", "..") for part in path.split("/")))


def _read(archive: zipfile.ZipFile, name: str) -> bytes:
    with archive.open(name) as member:
        data = member.read(_MAX_MEMBER + 1)
    _require(len(data) == archive.getinfo(name).file_size and len(data) <= _MAX_MEMBER)
    return data


class _BoundedTreeBuilder(ET.TreeBuilder):
    def __init__(self) -> None:
        super().__init__()
        self.depth = 0
        self.nodes = 0

    def start(self, tag: str, attrs: dict[str, str]) -> ET.Element:
        self.depth += 1
        self.nodes += 1
        _require(self.depth <= _MAX_DEPTH and self.nodes <= _MAX_NODES)
        return super().start(tag, attrs)

    def end(self, tag: str) -> ET.Element:
        element = super().end(tag)
        self.depth -= 1
        return element


def _xml(data: bytes) -> ET.Element:
    text = data.decode("utf-8-sig")
    _require(not _DECLARATION.search(text))
    _require(not any(ord(char) < 32 and char not in "\t\r\n" for char in text))
    _require("\x7f" not in text)
    declaration = re.match(r"<\?xml\b[^?]*\bencoding\s*=\s*(['\"])([^'\"]+)\1", text)
    _require(declaration is None or declaration[2].lower() in {"utf-8", "utf8"})
    parser = ET.XMLParser(target=_BoundedTreeBuilder())  # noqa: S314 -- DTD rejected above
    return ET.fromstring(text, parser=parser)  # noqa: S314 -- DTD/entity rejected before parse


def _children(node: ET.Element, allowed: frozenset[str]) -> None:
    _require(not (node.text or "").strip())
    for child in node:
        _require(child.tag in {_W + name for name in allowed})
        _require(not (child.tail or "").strip())


def _format(node: ET.Element, allowed: frozenset[str]) -> None:
    # Only explicitly listed non-text layout properties; no extension subtrees.
    _require(not (node.text or "").strip())
    for child in node:
        _require(child.tag in {_W + name for name in allowed})
        _require(not (child.tail or "").strip())
        _format(
            child, frozenset({"top", "left", "bottom", "right", "between", "bar", "tab", "col"})
        )


def _one(node: ET.Element, name: str) -> ET.Element | None:
    found = node.findall(_W + name)
    _require(len(found) <= 1)
    return found[0] if found else None


def _outline(properties: ET.Element | None) -> int | None:
    if properties is None:
        return None
    item = _one(properties, "outlineLvl")
    if item is None:
        return None
    _require(not len(item))
    value = item.get(_W + "val", "")
    _require(value in {"0", "1", "2", "3", "4", "5", "9"})
    return 0 if value == "9" else int(value) + 1


def _properties(node: ET.Element) -> None:
    _children(node, _P_FORMAT | {"pStyle", "outlineLvl", "sectPr", "rPr", "numPr"})
    for child in node:
        if child.tag == _W + "sectPr":
            _format(child, _SECT_FORMAT)
        elif child.tag == _W + "rPr":
            _format(child, _R_FORMAT)
        elif child.tag == _W + "numPr":
            _number_properties(node)
        elif child.tag in {_W + "pStyle", _W + "outlineLvl"}:
            _require(not len(child) and not (child.text or "").strip())
        else:
            _format(child, frozenset({"top", "left", "bottom", "right", "between", "bar", "tab"}))
    _one(node, "pStyle")
    _outline(node)


def _integer(value: str) -> int:
    _require(bool(re.fullmatch(r"[0-9]{1,10}", value)))
    result = int(value)
    _require(result <= 2_147_483_647)
    return result


def _leaf_value(node: ET.Element) -> str:
    _require(not len(node) and not (node.text or "").strip())
    value = node.get(_W + "val")
    _require(value is not None)
    return value if value is not None else ""


def _number_properties(properties: ET.Element | None) -> tuple[str | None, int | None]:
    numbering = _one(properties, "numPr") if properties is not None else None
    if numbering is None:
        return None, None
    _children(numbering, frozenset({"numId", "ilvl"}))
    identifier, level = _one(numbering, "numId"), _one(numbering, "ilvl")
    _require(identifier is not None or level is not None)
    key = str(_integer(_leaf_value(identifier))) if identifier is not None else None
    depth = _integer(_leaf_value(level)) if level is not None else None
    _require(depth in (None, 0))
    return key, depth


def _styles(root: ET.Element | None) -> tuple[dict[str, ET.Element], str | None]:
    if root is None:
        return {}, None
    _require(root.tag == _W + "styles")
    _children(root, frozenset({"style", "docDefaults", "latentStyles"}))
    styles: dict[str, ET.Element] = {}
    default: str | None = None
    for item in root:
        if item.tag == _W + "docDefaults":
            _children(item, frozenset({"rPrDefault", "pPrDefault"}))
            for group in item:
                _children(group, frozenset({"rPr", "pPr"}))
                for defaults in group:
                    if defaults.tag == _W + "pPr":
                        _properties(defaults)
                        _require(_outline(defaults) in (None, 0))
                        _require(_one(defaults, "pStyle") is None)
                        _require(_number_properties(defaults) == (None, None))
                    else:
                        _format(defaults, _R_FORMAT - {"rStyle"})
            continue
        if item.tag == _W + "latentStyles":
            _children(item, frozenset({"lsdException"}))
            for child in item:
                _require(not len(child) and not (child.text or "").strip())
            continue
        key = item.get(_W + "styleId", "")
        _require(bool(key) and key not in styles)
        _require(item.get(_W + "type") in {"paragraph", "character", "table", "numbering"})
        styles[key] = item
        if item.get(_W + "type") == "paragraph" and item.get(_W + "default") in {"1", "true", "on"}:
            _require(default is None)
            default = key
    return styles, default


def _style_chain(styles: dict[str, ET.Element], key: str, kind: str) -> tuple[ET.Element, ...]:
    chain: list[ET.Element] = []
    visited: set[str] = set()
    while key:
        _require(key in styles and key not in visited and len(chain) < _MAX_STYLE_CHAIN)
        visited.add(key)
        item = styles[key]
        _require(item.get(_W + "type") == kind)
        metadata = frozenset(
            {
                "name",
                "aliases",
                "basedOn",
                "next",
                "link",
                "autoRedefine",
                "hidden",
                "uiPriority",
                "semiHidden",
                "unhideWhenUsed",
                "qFormat",
                "locked",
                "personal",
                "personalCompose",
                "personalReply",
                "rsid",
            }
        )
        _children(item, metadata | {"pPr", "rPr"})
        for child in item:
            if child.tag.removeprefix(_W) in metadata:
                _require(not len(child) and not (child.text or "").strip())
        chain.append(item)
        based = _one(item, "basedOn")
        key = _leaf_value(based) if based is not None else ""
        _require(based is None or bool(key))
    return tuple(chain)


def _run_format(node: ET.Element, styles: dict[str, ET.Element]) -> None:
    _format(node, _R_FORMAT)
    reference = _one(node, "rStyle")
    if reference is not None:
        for item in _style_chain(styles, _leaf_value(reference), "character"):
            _require(_one(item, "pPr") is None)
            run = _one(item, "rPr")
            if run is not None:
                _format(run, _R_FORMAT - {"rStyle"})


@dataclass(frozen=True)
class _ParagraphStyle:
    heading: int = 0
    num_id: str | None = None
    list_level: int | None = None


def _paragraph_style(styles: dict[str, ET.Element], key: str | None) -> _ParagraphStyle:
    heading: int | None = None
    num_id: str | None = None
    list_level: int | None = None
    for item in _style_chain(styles, key, "paragraph") if key is not None else ():
        properties = _one(item, "pPr")
        if properties is not None:
            _properties(properties)
            _require(_one(properties, "pStyle") is None)
            paragraph_run = _one(properties, "rPr")
            if paragraph_run is not None:
                _run_format(paragraph_run, styles)
        run = _one(item, "rPr")
        if run is not None:
            _run_format(run, styles)
        level = _outline(properties)
        if level is None:
            name = _one(item, "name")
            candidates = [
                item.get(_W + "styleId", ""),
                _leaf_value(name) if name is not None else "",
            ]
            for candidate in candidates:
                match = re.fullmatch(r"heading ?([1-9])", candidate, re.IGNORECASE)
                if match:
                    _require(int(match[1]) <= 6)
                    level = int(match[1])
                    break
        if heading is None:
            heading = level
        number, depth = _number_properties(properties)
        if num_id is None:
            num_id = number
        if list_level is None:
            list_level = depth
    return _ParagraphStyle(heading or 0, num_id, list_level)


def _part_type(name: str) -> str | None:
    if re.fullmatch(r"customXml/item[1-9][0-9]*\.xml", name):
        return "application/xml"
    if re.fullmatch(r"customXml/itemProps[1-9][0-9]*\.xml", name):
        return "application/vnd.openxmlformats-officedocument.customXmlProperties+xml"
    return _PART_TYPES.get(name)


def _types(archive: zipfile.ZipFile, names: set[str]) -> None:
    root = _xml(_read(archive, "[Content_Types].xml"))
    _require(root.tag == _CT + "Types" and not (root.text or "").strip())
    defaults: dict[str, str] = {}
    overrides: dict[str, str] = {}
    permitted = set(_PART_TYPES.values()) | {
        "application/xml",
        "application/vnd.openxmlformats-officedocument.customXmlProperties+xml",
    }
    rel_type = "application/vnd.openxmlformats-package.relationships+xml"
    permitted.add(rel_type)
    for child in root:
        _require(not len(child) and not (child.text or "").strip())
        _require(not (child.tail or "").strip())
        content_type = child.get("ContentType", "")
        _require(content_type in permitted)
        if child.tag == _CT + "Default":
            extension = child.get("Extension", "")
            _require(extension in {"xml", "rels", "jpeg"} and extension not in defaults)
            defaults[extension] = content_type
        else:
            _require(child.tag == _CT + "Override")
            part = child.get("PartName", "")
            _require(part.startswith("/"))
            part = part[1:]
            _require(part in names and part not in overrides)
            overrides[part] = content_type
    for name in names - {"[Content_Types].xml"}:
        expected = rel_type if name.endswith(".rels") else _part_type(name)
        _require(expected is not None)
        _require(overrides.get(name, defaults.get(name.rsplit(".", 1)[-1])) == expected)


def _valid_hyperlink(target: str, external: bool) -> None:
    _require(0 < len(target) <= 4096 and target.isascii())
    _require(not any(ord(char) <= 32 or ord(char) == 127 or char == "\\" for char in target))
    _require(re.search(r"%(?![0-9a-fA-F]{2})", target) is None)
    if not external:
        _require(bool(re.fullmatch(r"#[A-Za-z_][A-Za-z0-9_.:-]*", target)))
        return
    parsed = urlsplit(target)
    _require(parsed.scheme in {"http", "https", "mailto"})
    if parsed.scheme == "mailto":
        _require(not parsed.netloc and bool(re.fullmatch(r"[^@/?#]+@[^@/?#]+", parsed.path)))
    else:
        _require(bool(parsed.netloc) and bool(parsed.hostname))
        _require(parsed.username is None and parsed.password is None)
        _require(parsed.port is None or 1 <= parsed.port <= 65535)


def _relationships(archive: zipfile.ZipFile, names: set[str]) -> tuple[set[str], bool]:
    links: set[str] = set()
    main = styles = numbering = 0
    for name in sorted(names):
        if not name.endswith(".rels"):
            continue
        if name == "_rels/.rels":
            base = ""
        else:
            parent, leaf = name.rsplit("/_rels/", 1)
            owner = parent + "/" + leaf[:-5]
            _require(owner in names)
            base = parent
        root = _xml(_read(archive, name))
        _require(root.tag == _REL + "Relationships" and not (root.text or "").strip())
        ids: set[str] = set()
        for child in root:
            _require(child.tag == _REL + "Relationship" and not len(child))
            _require(not (child.text or "").strip() and not (child.tail or "").strip())
            key, kind, target = (child.get(attr, "") for attr in ("Id", "Type", "Target"))
            _require(bool(key) and key not in ids and kind in _REL_TYPES)
            ids.add(key)
            mode = child.get("TargetMode", "Internal")
            _require(mode in {"Internal", "External"})
            if kind == _R[1:-1] + "/hyperlink":
                _valid_hyperlink(target, mode == "External")
                if name == "word/_rels/document.xml.rels":
                    links.add(key)
                continue
            _require(mode == "Internal")
            # OOXML's conventional metadata edge uses one parent hop. This is NOT
            # a general traversal exemption: archive paths remain unchanged/strict.
            if kind == _R[1:-1] + "/customXml" and name == "word/_rels/document.xml.rels":
                _require(bool(re.fullmatch(r"\.\./customXml/item[1-9][0-9]*\.xml", target)))
                destination = target[3:]
                _safe_path(destination)
            else:
                _safe_path(target)
                destination = posixpath.join(base, target)
            _require(destination in names)
            if kind == _R[1:-1] + "/customXmlProps":
                _require(bool(re.fullmatch(r"customXml/itemProps[1-9][0-9]*\.xml", destination)))
            if kind == _R[1:-1] + "/numbering":
                _require(
                    name == "word/_rels/document.xml.rels" and destination == "word/numbering.xml"
                )
                numbering += 1
            if kind == _R[1:-1] + "/officeDocument":
                _require(name == "_rels/.rels" and destination == "word/document.xml")
                main += 1
            if kind == _R[1:-1] + "/styles":
                _require(
                    name == "word/_rels/document.xml.rels" and destination == "word/styles.xml"
                )
                styles += 1
    _require(main == 1 and styles <= 1 and numbering <= 1)
    return links, numbering == 1


class _Lists:
    """Lazy, bounded single-level numbering. Never resolve style links or pictures."""

    def __init__(self, archive: zipfile.ZipFile, linked: bool) -> None:
        self.archive = archive
        self.linked = linked
        self.definitions: dict[str, ET.Element] = {}
        self.instances: dict[str, ET.Element] = {}
        self.formats: dict[str, tuple[str, str, int]] = {}
        self.counters: dict[str, int] = {}
        self.loaded = False

    def _load(self) -> None:
        _require(self.linked)
        root = _xml(_read(self.archive, "word/numbering.xml"))
        _require(root.tag == _W + "numbering")
        _children(root, frozenset({"abstractNum", "num", "numPicBullet"}))
        for child in root:
            if child.tag == _W + "abstractNum":
                key = str(_integer(child.get(_W + "abstractNumId", "")))
                _require(key not in self.definitions)
                self.definitions[key] = child
            elif child.tag == _W + "num":
                key = str(_integer(child.get(_W + "numId", "")))
                _require(key != "0" and key not in self.instances)
                self.instances[key] = child
        self.loaded = True

    def _definition(self, key: str) -> tuple[str, str, int]:
        if not self.loaded:
            self._load()
        _require(key in self.instances)
        instance = self.instances[key]
        _children(instance, frozenset({"abstractNumId", "lvlOverride"}))
        abstract = _one(instance, "abstractNumId")
        _require(abstract is not None)
        identifier = str(_integer(_leaf_value(abstract))) if abstract is not None else ""
        _require(identifier in self.definitions)
        definition = self.definitions[identifier]
        _children(definition, frozenset({"nsid", "multiLevelType", "tmpl", "name", "lvl"}))
        for child in definition:
            if child.tag != _W + "lvl":
                value = _leaf_value(child)
                if child.tag == _W + "multiLevelType":
                    _require(value == "singleLevel")
        level = _one(definition, "lvl")
        _require(level is not None)
        if level is None:
            raise _Rejected
        _require(level.get(_W + "ilvl") == "0")
        _children(
            level,
            frozenset({"start", "numFmt", "lvlText", "lvlJc", "pPr", "rPr", "pStyle", "suff"}),
        )
        values: dict[str, str] = {}
        for child in level:
            name = child.tag.removeprefix(_W)
            _require(name not in values)
            if name == "pPr":
                _properties(child)
                _require(_number_properties(child) == (None, None))
                values[name] = ""
            elif name == "rPr":
                _format(child, _R_FORMAT - {"rStyle"})
                values[name] = ""
            else:
                values[name] = _leaf_value(child)
        kind, text = values.get("numFmt", ""), values.get("lvlText", "")
        _require(values.get("suff", "tab") in {"tab", "space"})
        if kind == "bullet":
            _require(text in {"•", "\uf0b7", "○", "o", "▪", "\uf0a7", "-"})
            suffix = ""
        else:
            _require(kind == "decimal" and text in {"%1.", "%1)"})
            suffix = text[-1]
        start = _integer(values.get("start", "1"))
        override = _one(instance, "lvlOverride")
        if override is not None:
            _require(override.get(_W + "ilvl") == "0")
            _children(override, frozenset({"startOverride"}))
            replacement = _one(override, "startOverride")
            _require(replacement is not None)
            if replacement is not None:
                start = _integer(_leaf_value(replacement))
        return kind, suffix, start

    def marker(self, key: str, level: int | None) -> str:
        _require(level in (None, 0))
        if key not in self.formats:
            self.formats[key] = self._definition(key)
        kind, suffix, start = self.formats[key]
        if kind == "bullet":
            return "- "
        value = self.counters.get(key, start)
        _require(value <= 2_147_483_647)
        self.counters[key] = value + 1
        return f"{value}{suffix} "


def _inline(node: ET.Element, links: set[str], styles: dict[str, ET.Element]) -> str:
    _children(node, frozenset({"r", "hyperlink", "bookmarkStart", "bookmarkEnd", "proofErr"}))
    pieces: list[str] = []
    for child in node:
        if child.tag == _W + "hyperlink":
            reference = child.get(_R + "id")
            _require(reference is None or reference in links)
            anchor = child.get(_W + "anchor")
            _require(reference is not None or anchor is not None)
            if anchor is not None:
                _valid_hyperlink("#" + anchor, False)
            _children(child, frozenset({"r", "bookmarkStart", "bookmarkEnd", "proofErr"}))
            pieces.append(_inline(child, links, styles))
        elif child.tag == _W + "r":
            _children(child, frozenset({"rPr", "t", "tab", "br", "cr"}))
            _one(child, "rPr")
            for item in child:
                if item.tag == _W + "rPr":
                    _run_format(item, styles)
                    continue
                _require(not len(item))
                if item.tag == _W + "t":
                    text = item.text or ""
                    _require(not any(ord(c) < 32 and c not in "\n\t\r" for c in text))
                    _require("\x7f" not in text)
                    pieces.append(text.replace("\r\n", "\n").replace("\r", "\n"))
                else:
                    _require(not (item.text or "").strip())
                    if item.tag == _W + "tab":
                        pieces.append("\t")
                    else:
                        _require(item.get(_W + "type", "textWrapping") == "textWrapping")
                        pieces.append("\n")
        else:
            _require(not len(child) and not (child.text or "").strip())
    return "".join(pieces)


def _document(
    root: ET.Element,
    styles: dict[str, ET.Element],
    default: str | None,
    links: set[str],
    lists: _Lists,
) -> bytes:
    _require(root.tag == _W + "document")
    _children(root, frozenset({"body"}))
    _require(len(root) == 1)
    body = root[0]
    _children(body, frozenset({"p", "sectPr"}))
    paragraphs: list[str] = []
    resolved: dict[str | None, _ParagraphStyle] = {}
    for index, paragraph in enumerate(body):
        if paragraph.tag == _W + "sectPr":
            _require(index == len(body) - 1)
            _format(paragraph, _SECT_FORMAT)
            continue
        _children(
            paragraph,
            frozenset({"pPr", "r", "hyperlink", "bookmarkStart", "bookmarkEnd", "proofErr"}),
        )
        properties = _one(paragraph, "pPr")
        key = default
        direct: int | None = None
        if properties is not None:
            _require(paragraph[0] is properties)
            _properties(properties)
            style = _one(properties, "pStyle")
            if style is not None:
                key = style.get(_W + "val", "")
            direct = _outline(properties)
        if key not in resolved:
            resolved[key] = _paragraph_style(styles, key)
        inherited = resolved[key]
        level = direct if direct is not None else inherited.heading
        number, depth = _number_properties(properties)
        number = number if number is not None else inherited.num_id
        depth = depth if depth is not None else inherited.list_level
        if properties is not None:
            paragraph_run = _one(properties, "rPr")
            if paragraph_run is not None:
                _run_format(paragraph_run, styles)
        # Inline parser sees no property subtree, which has already been validated.
        inline = ET.Element(_W + "p")
        inline.extend(child for child in paragraph if child is not properties)
        text = _inline(inline, links, styles)
        if level:
            _require(number in (None, "0"))
            _require(bool(text.strip()) and len(text.splitlines()) == 1)
            text = "#" * level + " " + _MARKDOWN.sub(r"\\\1", text.strip())
        else:
            text = _MARKDOWN.sub(r"\\\1", text)
            if number is not None and number != "0":
                marker = lists.marker(number, depth)
                text = marker + text.replace("\n", "\n" + " " * len(marker))
        paragraphs.append(text)
    output = ("\n\n".join(paragraphs) + "\n").encode("utf-8")
    _require(bool(output.strip()) and len(output) <= _MAX_INPUT)
    return output


def _extract(content: bytes) -> bytes:
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        entries = archive.infolist()
        _require(0 < len(entries) <= _MAX_ENTRIES)
        names: set[str] = set()
        total = 0
        for item in entries:
            _require(item.filename == item.orig_filename)
            _safe_path(item.filename, directory=item.is_dir())
            _require(item.filename not in names)
            names.add(item.filename)
            mode = stat.S_IFMT(item.external_attr >> 16)
            _require(mode in {0, stat.S_IFDIR if item.is_dir() else stat.S_IFREG})
            _require(not item.flag_bits & (1 | 64))
            _require(item.compress_type in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED})
            _require(0 <= item.file_size <= _MAX_MEMBER)
            _require(item.file_size <= _MAX_RATIO * item.compress_size)
            total += item.file_size
            _require(total <= _MAX_TOTAL)
            if item.is_dir():
                _require(item.file_size == 0)
                continue
            if item.filename.endswith(".rels"):
                _require(item.filename == "_rels/.rels" or "/_rels/" in item.filename)
            else:
                _require(
                    _part_type(item.filename) is not None or item.filename == "[Content_Types].xml"
                )
        files = {item.filename for item in entries if not item.is_dir()}
        _require({"[Content_Types].xml", "_rels/.rels", "word/document.xml"} <= files)
        _types(archive, files)
        links, numbering_linked = _relationships(archive, files)
        style_root = _xml(_read(archive, "word/styles.xml")) if "word/styles.xml" in files else None
        styles, default = _styles(style_root)
        return _document(
            _xml(_read(archive, "word/document.xml")),
            styles,
            default,
            links,
            _Lists(archive, numbering_linked),
        )


def extract_docx(content: bytes) -> bytes:
    """All-or-nothing bounded conversion; every rejected input raises DocxError."""
    if type(content) is not bytes or not 0 < len(content) <= _MAX_INPUT:
        raise DocxError()
    result: bytes | None = None
    with suppress(
        _Rejected,
        zipfile.BadZipFile,
        OSError,
        ValueError,
        RuntimeError,
        ET.ParseError,
        EOFError,
        struct.error,
        zlib.error,
    ):
        result = _extract(content)
    # Raise outside handler: even __context__ must not retain codec/XML/ZIP payloads.
    if result is None:
        raise DocxError()
    return result
