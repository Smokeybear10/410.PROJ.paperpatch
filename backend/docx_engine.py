"""Rebase paper corrections and export verified Word revisions."""

from __future__ import annotations

import hashlib
import io
import zipfile
import unicodedata
from copy import deepcopy
from datetime import datetime, timezone
from difflib import SequenceMatcher

import lxml.etree as ET


_W   = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_CT  = "http://schemas.openxmlformats.org/package/2006/content-types"

_WNS  = f"{{{_W}}}"

_PROTECT_TAGS: frozenset[str] = frozenset({
    f"{_WNS}fldChar",
    f"{_WNS}fldSimple",
    f"{_WNS}hyperlink",
    f"{_WNS}bookmarkStart",
    f"{_WNS}bookmarkEnd",
    f"{_WNS}drawing",
    f"{_WNS}pict",
    f"{_WNS}object",
    f"{_WNS}ins",
    f"{_WNS}del",
    f"{_WNS}moveFrom",
    f"{_WNS}moveTo",
    f"{_WNS}sdt",
    f"{_WNS}smartTag",
    f"{_WNS}customXml",
})


_REJECT_CT_FRAGMENTS: tuple[str, ...] = (
    "macroEnabled",
    "macro-enabled",
    "ms-word.document.macroEnabled",
    "ms-word.template.macroEnabled",
    "vnd.ms-office.activeX",
)


_MAX_COMPRESSED   = 10 * 1024 * 1024
_MAX_EXPANDED     = 100 * 1024 * 1024
_MAX_ZIP_MEMBERS  = 2_000
_MAX_PARAGRAPHS   = 5_000
_MAX_PARA_CHARS   = 50_000


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _safe_parse(xml_bytes: bytes, label: str) -> ET._Element:
    parser = ET.XMLParser(
        resolve_entities=False,
        no_network=True,
        load_dtd=False,
        huge_tree=False,
    )
    try:
        root = ET.fromstring(xml_bytes, parser=parser)
        if root.getroottree().docinfo.doctype or any(isinstance(node, ET._Entity) for node in root.iter()):
            raise ValueError(f"DTD and entity declarations are not supported in {label}.")
        return root
    except ET.XMLSyntaxError as exc:
        raise ValueError(f"Malformed XML in {label}: {exc}") from exc


def _open_docx(data: bytes) -> zipfile.ZipFile:
    if len(data) > _MAX_COMPRESSED:
        raise ValueError(
            f"Document exceeds {_MAX_COMPRESSED // 1024 // 1024} MB compressed limit."
        )
    try:
        zf = zipfile.ZipFile(io.BytesIO(data), "r")
    except zipfile.BadZipFile as exc:
        raise ValueError("Not a valid .docx ZIP package.") from exc

    entries = zf.infolist()
    if len(entries) > _MAX_ZIP_MEMBERS:
        raise ValueError(
            f"ZIP has {len(entries)} entries; limit is {_MAX_ZIP_MEMBERS}."
        )

    total_expanded = sum(e.file_size for e in entries)
    if total_expanded > _MAX_EXPANDED:
        raise ValueError(
            f"Expanded ZIP size {total_expanded} bytes exceeds "
            f"{_MAX_EXPANDED // 1024 // 1024} MB limit."
        )

    for entry in entries:
        name = entry.filename
        if name.startswith("/") or "\\" in name or ":" in name or any(part in (".", "..") for part in name.split("/")):
            raise ValueError(
                f"ZIP entry {name!r} contains a path-traversal component."
            )

    lower_names = [e.filename.lower() for e in entries]
    if len(lower_names) != len(set(lower_names)):
        raise ValueError("ZIP contains duplicate or case-ambiguous entries.")

    if any(e.flag_bits & 1 for e in entries):
        raise ValueError("Encrypted ZIP entries are not supported.")

    return zf


def _zip_read(zf: zipfile.ZipFile, name: str) -> bytes:
    for candidate in (name, name.lstrip("/")):
        if candidate in zf.namelist():
            try:
                return zf.read(candidate)
            except (zipfile.BadZipFile, RuntimeError, NotImplementedError) as exc:
                raise ValueError(f"Cannot read ZIP entry {candidate!r}: {exc}") from exc
    raise ValueError(f"Required ZIP entry not found: {name!r}")


def _check_package_constraints(zf: zipfile.ZipFile) -> None:
    try:
        ct_raw = _zip_read(zf, "[Content_Types].xml")
    except ValueError as exc:
        raise ValueError(f"Missing [Content_Types].xml: {exc}") from exc

    ct_root = _safe_parse(ct_raw, "[Content_Types].xml")
    ns = f"{{{_CT}}}"
    if ct_root.tag != ns + "Types":
        raise ValueError("Invalid OOXML content type manifest.")

    for elem in ct_root.iter():
        ct = elem.get("ContentType", "")
        for fragment in _REJECT_CT_FRAGMENTS:
            if fragment in ct:
                raise ValueError(
                    f"Unsupported document type ({ct!r}): macros are not allowed."
                )

    all_names_lower = {n.lower() for n in zf.namelist()}
    for n in all_names_lower:
        if "_xmlsignatures" in n or "digitalsignature" in n:
            raise ValueError("Document contains digital signatures; not supported.")

    if any(name.endswith("/vbaproject.bin") or name.startswith("word/activex/") for name in all_names_lower):
        raise ValueError("Document contains VBA macros (word/vbaProject.bin).")

    if any("encryptioninfo" in n for n in all_names_lower):
        raise ValueError("Document appears to be encrypted.")


def _find_main_document_part(zf: zipfile.ZipFile) -> str:
    ct_root = _safe_parse(_zip_read(zf, "[Content_Types].xml"), "content types")
    declared = [node.get("PartName", "").lstrip("/") for node in ct_root
                if node.get("ContentType") == "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"]
    if declared != ["word/document.xml"]:
        raise ValueError("Only standard Word document packages are supported.")
    rels = _safe_parse(_zip_read(zf, "_rels/.rels"), "package relationships")
    if rels.tag != "{http://schemas.openxmlformats.org/package/2006/relationships}Relationships":
        raise ValueError("Invalid OOXML package relationships.")
    main = [node for node in rels if node.get("Type", "").endswith("/officeDocument")]
    if len(main) != 1 or main[0].get("TargetMode") == "External" or main[0].get("Target", "").lstrip("/") != declared[0]:
        raise ValueError("Main document relationship is missing or inconsistent.")
    return declared[0]


def _para_text(para: ET._Element) -> str:
    parts: list[str] = []
    for child in para.iter():
        if child.tag == f"{_WNS}delText":
            continue
        if child.tag == f"{_WNS}t":
            text = child.text or ""
            parts.append(text)
    return "".join(parts)


def _para_style(para: ET._Element) -> str:
    ppr = para.find(f"{_WNS}pPr")
    if ppr is None:
        return "Normal"
    pstyle = ppr.find(f"{_WNS}pStyle")
    if pstyle is None:
        return "Normal"
    return pstyle.get(f"{_WNS}val") or "Normal"


def _classify_paragraph(para: ET._Element, is_top_level: bool) -> tuple[bool, str | None]:
    if not is_top_level:
        return False, "not a top-level body paragraph"
    for node in para.iter():
        if not isinstance(node.tag, str):
            return False, "contains unsupported XML nodes"
        if node.tag in _PROTECT_TAGS or node.tag.endswith("Change"):
            return False, f"contains {ET.QName(node).localname}"
    if any(child.tag not in (_WNS + "pPr", _WNS + "r") for child in para):
        return False, "contains unsupported paragraph content"
    runs = para.findall(_WNS + "r")
    if not runs:
        return False, "empty paragraph"
    for run in runs:
        if any(child.tag not in (_WNS + "rPr", _WNS + "t") for child in run):
            return False, "contains non-text runs (such as tabs or fields)"
        texts = run.findall(_WNS + "t")
        if len(texts) != 1 or not texts[0].text or len(run.findall(_WNS + "rPr")) > 1:
            return False, "contains empty or compound text runs"
    if len(_para_text(para)) > _MAX_PARA_CHARS:
        return False, "paragraph exceeds supported text length"
    return True, None


def inspect_docx(data: bytes) -> dict:
    """Enumerate body paragraphs with codepoint offsets and editability guards."""
    zf = _open_docx(data)
    _check_package_constraints(zf)

    main_part = _find_main_document_part(zf)
    xml_bytes = _zip_read(zf, main_part)
    root      = _safe_parse(xml_bytes, main_part)
    if root.tag != _WNS + "document":
        raise ValueError("Main part is not a Word document.")

    body = root.find(f"{_WNS}body")
    if body is None:
        raise ValueError("Document XML has no <w:body> element.")

    warnings: list[str] = []

    title: str | None = None
    try:
        core_raw  = _zip_read(zf, "docProps/core.xml")
        core_root = _safe_parse(core_raw, "docProps/core.xml")
        DC = "http://purl.org/dc/elements/1.1/"
        title_elem = core_root.find(f"{{{DC}}}title")
        if title_elem is not None and title_elem.text:
            title = title_elem.text.strip() or None
    except ValueError:
        pass

    paragraphs: list[dict] = []
    para_index  = 0


    for elem in body.iter(f"{_WNS}p"):
        if para_index >= _MAX_PARAGRAPHS:
            raise ValueError(f"Document exceeds {_MAX_PARAGRAPHS} paragraphs.")

        is_top_level = id(elem.getparent()) == id(body)
        text         = _para_text(elem)

        if len(text) > _MAX_PARA_CHARS:
            warnings.append(
                f"Paragraph p{para_index} is protected above "
                f"{_MAX_PARA_CHARS} characters."
            )

        style    = _para_style(elem)
        editable, reason = _classify_paragraph(elem, is_top_level)

        entry = {
            "id":       f"p{para_index}",
            "index":    para_index,
            "text":     text,
            "style":    style,
            "editable": editable,
        }
        if not editable:
            entry["reason"] = reason
        paragraphs.append(entry)
        para_index += 1

    return {
        "title":      title,
        "paragraphs": paragraphs,
        "warnings":   warnings,
    }


def _valid_replacement(value: object) -> bool:
    return isinstance(value, str) and len(value) <= _MAX_PARA_CHARS and all(
        ord(c) >= 32 and not 0xD800 <= ord(c) <= 0xDFFF and ord(c) not in (0xFFFE, 0xFFFF)
        for c in value
    )


def _mapped_span(opcodes: list[tuple], start: int, end: int) -> tuple[int | None, int | None, bool]:
    def boundary(offset: int, right: bool) -> int | None:
        candidates = []
        for tag, a, b, c, d in opcodes:
            if tag == "equal" and a <= offset <= b:
                candidates.append(c + offset - a)
            elif a == offset:
                candidates.append(c)
                if a == b:
                    candidates.append(d)
            elif b == offset:
                candidates.append(d)
        return (max(candidates) if right else min(candidates)) if candidates else None

    overlap = any(
        tag != "equal" and (
            (a == b and start <= a <= end) or
            (start == end and a <= start <= b) or
            (a < end and b > start)
        )
        for tag, a, b, c, d in opcodes
    )
    return boundary(start, False), boundary(end, True), overlap


def _ambiguous_repeated_anchor(original: str, current: str, opcodes: list[tuple], start: int, end: int) -> bool:
    def word_character(char: str) -> bool:
        return char.isalnum() or char == "_" or unicodedata.category(char).startswith("M")

    if start == end:
        anchor = start if start < len(original) and word_character(original[start]) else start - 1
        if anchor < 0 or not word_character(original[anchor]):
            return False
        start, end = anchor, anchor + 1
        while start > 0 and word_character(original[start - 1]):
            start -= 1
        while end < len(original) and word_character(original[end]):
            end += 1
    fragment = original[start:end]
    if not fragment:
        return False
    left_word = start > 0 and word_character(original[start - 1])
    right_word = end < len(original) and word_character(original[end])

    def occurrences(text: str) -> list[int]:
        positions = []
        offset = text.find(fragment)
        while offset != -1:
            finish = offset + len(fragment)
            left_matches = not word_character(fragment[0]) or (offset > 0 and word_character(text[offset - 1])) == left_word
            right_matches = not word_character(fragment[-1]) or (finish < len(text) and word_character(text[finish])) == right_word
            if left_matches and right_matches:
                positions.append(offset)
            offset = text.find(fragment, offset + 1)
        return positions

    before = occurrences(original)
    if len(before) < 2:
        return False
    after = occurrences(current)
    if len(before) != len(after):
        return True
    equal_blocks = [(a, b, c) for tag, a, b, c, _ in opcodes if tag == "equal"]
    block_index = 0
    mapped = []
    for position in before:
        finish = position + len(fragment)
        while block_index < len(equal_blocks) and equal_blocks[block_index][1] < finish:
            block_index += 1
        if block_index == len(equal_blocks):
            return True
        a, b, c = equal_blocks[block_index]
        if not a <= position < finish <= b:
            return True
        mapped.append(c + position - a)
    return mapped != after


def rebase_edit(base: bytes, current: bytes, edit: dict) -> dict:
    replacement = edit.get("replacement", "")
    result = dict(status="missing", reason="", currentParagraphId=None,
                  currentStart=None, currentEnd=None, baseText="", currentText="",
                  replacement=replacement, paragraphText="")
    pid, start, end = edit.get("paragraphId"), edit.get("start"), edit.get("end")
    if not isinstance(pid, str) or type(start) is not int or type(end) is not int or not _valid_replacement(replacement):
        return {**result, "reason": "Invalid paragraph, offsets, or replacement text."}
    base_paras = inspect_docx(base)["paragraphs"]
    current_paras = inspect_docx(current)["paragraphs"]
    original = next((p for p in base_paras if p["id"] == pid), None)
    if original is None or start < 0 or end < start or end > len(original["text"]):
        return {**result, "reason": "Original paragraph or text span is missing."}
    text = original["text"]
    result["baseText"] = text[start:end]
    if not original["editable"]:
        return {**result, "status": "protected", "reason": original["reason"]}
    base_texts = [paragraph["text"] for paragraph in base_paras]
    current_texts = [paragraph["text"] for paragraph in current_paras]
    exact = [paragraph for paragraph in current_paras if paragraph["text"] == text]
    if base_texts == current_texts:
        target = current_paras[original["index"]]
        c_start, c_end, overlap = start, end, False
    elif base_texts.count(text) > 1:
        return {**result, "status": "conflict", "reason": "The original paragraph is repeated; its identity is ambiguous after the draft changed."}
    elif len(exact) > 1:
        return {**result, "status": "conflict", "reason": "More than one current paragraph matches the original."}
    elif exact:
        target = exact[0]
        c_start, c_end, overlap = start, end, False
    else:
        anchored_texts = set(base_texts) & set(current_texts)
        unmatched_base = [paragraph for paragraph in base_paras if paragraph["text"] not in anchored_texts]
        unmatched_current = [paragraph for paragraph in current_paras if paragraph["text"] not in anchored_texts]
        comparison_budget = 20_000_000

        def rank(reference: str, paragraphs: list[dict]) -> list[tuple] | None:
            nonlocal comparison_budget
            scored = []
            for paragraph in paragraphs:
                candidate = paragraph["text"]
                if not candidate or len(reference) * len(candidate) > 4_000_000:
                    continue
                matcher = SequenceMatcher(None, reference, candidate, autojunk=False)
                if matcher.quick_ratio() < 0.58:
                    continue
                comparison_budget -= len(reference) * len(candidate)
                if comparison_budget < 0:
                    return None
                ratio = matcher.ratio()
                if ratio >= 0.58:
                    scored.append((ratio, paragraph))
            return sorted(scored, key=lambda item: item[0], reverse=True)

        scored = rank(text, unmatched_current)
        if scored is None:
            return {**result, "status": "conflict", "reason": "Too many similar paragraphs to match reliably; use a shorter source document."}
        if not scored or scored[0][0] < 0.70:
            return {**result, "reason": "The original paragraph could not be located without reusing another unchanged passage."}
        if len(scored) > 1 and scored[0][0] - scored[1][0] < 0.12:
            return {**result, "status": "conflict", "reason": "Several current paragraphs resemble the original; choose a fresh source document."}
        target = scored[0][1]
        reverse_matches = rank(target["text"], unmatched_base)
        if reverse_matches is None:
            return {**result, "status": "conflict", "reason": "Too many similar paragraphs to match reliably; use a shorter source document."}
        if (not reverse_matches or reverse_matches[0][1]["id"] != original["id"]
                or len(reverse_matches) > 1 and reverse_matches[0][0] - reverse_matches[1][0] < 0.12):
            return {**result, "status": "conflict", "reason": "This current passage could belong to another original paragraph; its identity is ambiguous."}
        current_text = target["text"]
        opcodes = SequenceMatcher(None, text, current_text, autojunk=False).get_opcodes()
        if target["editable"] and _ambiguous_repeated_anchor(text, current_text, opcodes, start, end):
            return {**result, "status": "conflict", "currentParagraphId": target["id"],
                    "paragraphText": current_text,
                    "reason": "A repeated word or passage was added, removed, or reassigned in the latest draft. Its original occurrence cannot be identified safely; use a fresh source for this correction."}
        c_start, c_end, overlap = _mapped_span(opcodes, start, end)
        overlap = overlap or any(
            tag == "insert" and (
                (a <= start and not text[a:start].strip()) or
                (a >= end and not text[end:a].strip())
            )
            for tag, a, b, c, d in opcodes
        )
        reverse = SequenceMatcher(None, text[::-1], current_text[::-1], autojunk=False).get_opcodes()
        reverse_start, reverse_end, _ = _mapped_span(reverse, len(text) - end, len(text) - start)
        if c_start is not None and c_end is not None and reverse_start is not None and reverse_end is not None:
            if (c_start, c_end) != (len(current_text) - reverse_end, len(current_text) - reverse_start):
                c_start = c_end = None
                overlap = True
    result.update(currentParagraphId=target["id"], paragraphText=target["text"],
                  currentStart=c_start, currentEnd=c_end)
    if not target["editable"]:
        return {**result, "status": "protected", "reason": target["reason"]}
    if c_start is None or c_end is None:
        return {**result, "status": "conflict", "reason": "The changed text has no unambiguous boundaries; this correction needs a fresh source."}
    current_span = target["text"][c_start:c_end]
    result["currentText"] = current_span
    if current_span == replacement:
        return {**result, "status": "already_applied", "reason": "The requested text is already present."}
    if overlap or current_span != text[start:end]:
        return {**result, "status": "conflict", "reason": "The latest document also changed this text. Keep it or explicitly replace the shown span."}
    return {**result, "status": "ready", "reason": "The original text is intact in the latest document."}


def _next_revision_id(root: ET._Element, base_id: int = 1) -> int:
    used: set[int] = set()
    for elem in root.iter():
        for attr in ("w:id", f"{_WNS}id"):
            val = elem.get(attr) or elem.get(f"{{{_W}}}id")
            if val is not None:
                try:
                    used.add(int(val))
                except ValueError:
                    pass
    counter = max(used, default=0) + 1
    return max(counter, base_id)


class _RevisionIdAllocator:

    def __init__(self, root: ET._Element) -> None:
        self._next = _next_revision_id(root)

    def next(self) -> int:
        val = self._next
        self._next += 1
        return val


def _run_text_spans(para: ET._Element) -> list[tuple[int, int, ET._Element]]:
    spans: list[tuple[int, int, ET._Element]] = []
    pos = 0
    for child in para:
        if child.tag != f"{_WNS}r":
            continue
        text = "".join(
            (t.text or "") for t in child.findall(f"{_WNS}t")
        )
        spans.append((pos, pos + len(text), child))
        pos += len(text)
    return spans


def _run_slice(run: ET._Element, start: int, end: int) -> ET._Element:
    copied = deepcopy(run)
    text = copied.find(_WNS + "t")
    text.text = (text.text or "")[start:end]
    text.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
    return copied


def _apply_paragraph_edits(para: ET._Element, edits: list[dict], allocator: _RevisionIdAllocator) -> list[str]:
    spans = _run_text_spans(para)
    revisions = []
    children = [deepcopy(ch) for ch in para if ch.tag == _WNS + "pPr"]

    def slices(start: int, end: int) -> list[ET._Element]:
        return [_run_slice(run, max(start - a, 0), min(end, b) - a)
                for a, b, run in spans if a < end and b > start]

    def revision(kind: str, author: str) -> ET._Element:
        node = ET.Element(_WNS + kind)
        rev_id = str(allocator.next())
        node.set(_WNS + "id", rev_id)
        node.set(_WNS + "author", author)
        node.set(_WNS + "date", datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
        revisions.append(rev_id)
        return node

    cursor = 0
    for edit in sorted(edits, key=lambda item: (item["start"], item["end"])):
        start, end = edit["start"], edit["end"]
        children.extend(slices(cursor, start))
        deleted = slices(start, end)
        if deleted:
            node = revision("del", edit["author"])
            for run in deleted:
                run.find(_WNS + "t").tag = _WNS + "delText"
                node.append(run)
            children.append(node)
        if edit["replacement"]:
            node = revision("ins", edit["author"])
            reference = next((r for a, b, r in spans if a <= start < b), spans[-1][2])
            inserted = _run_slice(reference, 0, 0)
            inserted.find(_WNS + "t").text = edit["replacement"]
            node.append(inserted)
            children.append(node)
        cursor = end
    children.extend(slices(cursor, spans[-1][1]))
    para[:] = children
    return revisions


def _rewrite_package(zf: zipfile.ZipFile, main_part: str, xml: bytes) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as out:
        out.comment = zf.comment
        for info in zf.infolist():
            out.writestr(deepcopy(info), xml if info.filename == main_part else _zip_read(zf, info.filename))
    output = buffer.getvalue()
    with _open_docx(output) as check:
        if check.namelist() != zf.namelist():
            raise ValueError("Verification: package membership changed.")
        for info in zf.infolist():
            if info.filename != main_part and check.read(info.filename) != _zip_read(zf, info.filename):
                raise ValueError("Verification: an unrelated package part changed.")
    return output


def export_docx(current: bytes, edits: list[dict]) -> tuple[bytes, dict]:
    if not edits or len(edits) > 500:
        raise ValueError("Supply between 1 and 500 approved edits.")
    meta = {p["id"]: p for p in inspect_docx(current)["paragraphs"]}
    grouped = {}
    for edit in edits:
        pid = edit.get("paragraphId")
        if pid not in meta or not meta[pid]["editable"]:
            raise ValueError(f"Paragraph {pid!r} is missing or protected.")
        start, end = edit.get("start"), edit.get("end")
        if type(start) is not int or type(end) is not int or not 0 <= start <= end <= len(meta[pid]["text"]):
            raise ValueError("Edit offsets are outside the current paragraph.")
        if not _valid_replacement(edit.get("replacement")):
            raise ValueError("Replacement must be plain text without tabs, newlines, or invalid XML characters.")
        author = edit.get("author")
        if not _valid_replacement(author) or not author.strip() or len(author) > 255:
            raise ValueError("A valid revision author is required.")
        if meta[pid]["text"][start:end] == edit["replacement"]:
            raise ValueError("An approved edit must change the selected text.")
        grouped.setdefault(pid, []).append(edit)
    for pid, group in grouped.items():
        ordered = sorted(group, key=lambda e: (e["start"], e["end"]))
        for a, b in zip(ordered, ordered[1:]):
            if a["end"] > b["start"] or (a["end"] == b["start"] and (a["start"] == a["end"] or b["start"] == b["end"])):
                raise ValueError(f"Edits overlap or share an insertion boundary in {pid}.")
    with _open_docx(current) as zf:
        _check_package_constraints(zf)
        main_part = _find_main_document_part(zf)
        original_xml = _zip_read(zf, main_part)
        root = _safe_parse(original_xml, main_part)
        body = root.find(_WNS + "body")
        paragraphs = list(body.iter(_WNS + "p"))
        allocator = _RevisionIdAllocator(root)
        revision_ids = []
        for pid, group in grouped.items():
            revision_ids.extend(_apply_paragraph_edits(paragraphs[meta[pid]["index"]], group, allocator))
        output_xml = ET.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)
        _verify_export(original_xml, output_xml, edits, meta)
        output = _rewrite_package(zf, main_part, output_xml)
        parts = len(zf.infolist())
    return output, dict(status="passed", currentHash=_sha256(current), outputHash=_sha256(output),
                        changedParagraphCount=len(grouped), revisionCount=len(revision_ids),
                        revisionIds=revision_ids, unchangedPartCount=parts - 1, totalPartCount=parts)


def _xml_signature(node: ET._Element) -> tuple:
    return (node.tag, tuple(sorted(node.attrib.items())), node.text if node.text and node.text.strip() else None,
            tuple(_xml_signature(child) for child in node))


def _paragraph_semantics(para: ET._Element) -> tuple:
    styled = []
    for run in para.findall(_WNS + "r"):
        text = run.find(_WNS + "t")
        if text is None:
            raise ValueError("Verification: unsupported run in restored paragraph.")
        style = (tuple(sorted(run.attrib.items())),
                 tuple(_xml_signature(ch) for ch in run if ch.tag == _WNS + "rPr"),
                 tuple(sorted((key, value) for key, value in text.attrib.items()
                              if key != "{http://www.w3.org/XML/1998/namespace}space")))
        value = text.text or ""
        if styled and styled[-1][0] == style:
            styled[-1] = (style, styled[-1][1] + value)
        else:
            styled.append((style, value))
    return (tuple(sorted(para.attrib.items())),
            tuple(_xml_signature(ch) for ch in para if ch.tag == _WNS + "pPr"), tuple(styled))


def _reject_paragraph_revisions(para: ET._Element) -> ET._Element:
    restored = deepcopy(para)
    for node in list(restored):
        if node.tag == _WNS + "ins":
            restored.remove(node)
        elif node.tag == _WNS + "del":
            index = restored.index(node)
            for run in list(node):
                for text in run.findall(_WNS + "delText"):
                    text.tag = _WNS + "t"
                restored.insert(index, run)
                index += 1
            restored.remove(node)
    return restored


def _verify_export(current_xml_bytes: bytes, output_xml_bytes: bytes, edits: list[dict], para_meta: dict[str, dict]) -> None:
    current = _safe_parse(current_xml_bytes, "source verification")
    output = _safe_parse(output_xml_bytes, "output verification")
    source_paras = list(current.find(_WNS + "body").iter(_WNS + "p"))
    result_paras = list(output.find(_WNS + "body").iter(_WNS + "p"))
    if len(source_paras) != len(result_paras):
        raise ValueError("Verification: paragraph count changed.")
    touched = {e["paragraphId"] for e in edits}
    for index, (source, result) in enumerate(zip(source_paras, result_paras)):
        pid = f"p{index}"
        if pid not in touched:
            if ET.tostring(source) != ET.tostring(result):
                raise ValueError(f"Verification: untouched paragraph {pid} changed.")
            continue
        if _paragraph_semantics(source) != _paragraph_semantics(_reject_paragraph_revisions(result)):
            raise ValueError(f"Verification: rejecting revisions changed text or formatting in {pid}.")
        expected = _apply_edits_to_text(para_meta[pid]["text"], [e for e in edits if e["paragraphId"] == pid])
        if _para_text_with_revisions(result, "accept_all") != expected:
            raise ValueError(f"Verification: accepted text does not match the approved edits in {pid}.")
        source.getparent().replace(source, ET.Element("verified-paragraph", id=pid))
        result.getparent().replace(result, ET.Element("verified-paragraph", id=pid))
    if _xml_signature(current) != _xml_signature(output):
        raise ValueError("Verification: document structure outside edited paragraphs changed.")


def accept_export_revisions(data: bytes, revision_ids: list[str]) -> bytes:
    if not revision_ids or any(not isinstance(value, str) for value in revision_ids) or len(set(revision_ids)) != len(revision_ids):
        raise ValueError("Supply unique revision IDs from this export.")
    wanted = set(revision_ids)
    with _open_docx(data) as zf:
        _check_package_constraints(zf)
        part = _find_main_document_part(zf)
        root = _safe_parse(_zip_read(zf, part), part)
        before_paragraphs = list(root.find(_WNS + "body").iter(_WNS + "p"))
        before_text = [_para_text(p) for p in before_paragraphs]
        revisions = [n for n in root.iter() if n.tag in (_WNS + "ins", _WNS + "del")]
        selected = [n for n in revisions if n.get(_WNS + "id") in wanted]
        if len(selected) != len(wanted) or {n.get(_WNS + "id") for n in selected} != wanted:
            raise ValueError("One or more export revisions are missing or ambiguous.")
        untouched = [_xml_signature(n) for n in revisions if n.get(_WNS + "id") not in wanted]
        affected = {n.getparent() for n in selected}
        unchanged_paragraphs = [(i, ET.tostring(p)) for i, p in enumerate(before_paragraphs) if p not in affected]
        for node in selected:
            parent = node.getparent()
            if parent.tag != _WNS + "p" or any(ch.tag != _WNS + "r" for ch in node):
                raise ValueError("Only simple paragraph revisions from a PaperPatch export can be accepted.")
            if node.tag == _WNS + "ins":
                index = parent.index(node)
                for child in list(node):
                    parent.insert(index, child)
                    index += 1
            parent.remove(node)
        xml = ET.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)
        verified = _safe_parse(xml, "accepted output verification")
        after_paragraphs = list(verified.find(_WNS + "body").iter(_WNS + "p"))
        after_revisions = [n for n in verified.iter() if n.tag in (_WNS + "ins", _WNS + "del")]
        if before_text != [_para_text(p) for p in after_paragraphs]:
            raise ValueError("Verification: accepting revisions changed visible text.")
        if untouched != [_xml_signature(n) for n in after_revisions]:
            raise ValueError("Verification: unrelated existing revisions changed.")
        if any(ET.tostring(after_paragraphs[i]) != original for i, original in unchanged_paragraphs):
            raise ValueError("Verification: an unrelated paragraph changed during acceptance.")
        return _rewrite_package(zf, part, xml)


def _para_text_with_revisions(para: ET._Element, mode: str) -> str:
    parts: list[str] = []
    for child in para:
        tag = child.tag
        if tag == f"{_WNS}r":
            for t in child.findall(f"{_WNS}t"):
                parts.append(t.text or "")
        elif tag == f"{_WNS}ins":
            if mode == "accept_all":
                for r in child.findall(f"{_WNS}r"):
                    for t in r.findall(f"{_WNS}t"):
                        parts.append(t.text or "")
        elif tag == f"{_WNS}del":
            if mode == "reject_all":
                for r in child.findall(f"{_WNS}r"):
                    for dt in r.findall(f"{_WNS}delText"):
                        parts.append(dt.text or "")
    return "".join(parts)


def _apply_edits_to_text(text: str, edits: list[dict]) -> str:
    sorted_edits = sorted(edits, key=lambda e: e["start"])
    result = []
    pos = 0
    for edit in sorted_edits:
        s   = edit["start"]
        e   = edit["end"]
        rep = edit.get("replacement", "")
        result.append(text[pos:s])
        result.append(rep)
        pos = e
    result.append(text[pos:])
    return "".join(result)
