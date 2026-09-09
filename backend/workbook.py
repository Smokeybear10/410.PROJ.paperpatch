"""
backend/workbook.py

Typed Excel workbook reader and targeted patcher.

Dependencies: lxml, standard library only. Never round-trips through a
spreadsheet writer; patches only approved cell values via direct ZIP/XML
manipulation.
"""

from __future__ import annotations

import hashlib
import io
import re
import zipfile
from decimal import Decimal, InvalidOperation
from typing import Any
import lxml.etree as ET

# ---------------------------------------------------------------------------
# Bounded numeric parsing
# ---------------------------------------------------------------------------

# Caps that prevent Decimal from allocating huge coefficient arrays or
# triggering near-infinite loops inside int() on pathological inputs.
_MAX_DECIMAL_DIGITS = 28
_MAX_DECIMAL_EXP    = 100


def _bounded_decimal(raw: str, label: str = "value") -> Decimal:
    """
    Parse *raw* as a bounded finite Decimal or raise ValueError.

    Strips whitespace before parsing (numeric input only; callers that want
    to preserve text whitespace must not call this).
    """
    text = raw.strip()
    if not text:
        raise ValueError(f"Empty numeric {label}.")
    try:
        d = Decimal(text)
    except InvalidOperation:
        raise ValueError(
            f"{label} {raw!r} is not a valid number."
        )
    if not d.is_finite():
        raise ValueError(
            f"{label} {raw!r} is not a finite number (Inf/NaN not allowed)."
        )
    sign, digits, exp = d.as_tuple()
    if len(digits) > _MAX_DECIMAL_DIGITS:
        raise ValueError(
            f"{label} {raw!r} has {len(digits)} significant digits; "
            f"limit is {_MAX_DECIMAL_DIGITS}."
        )
    if abs(exp) > _MAX_DECIMAL_EXP:
        raise ValueError(
            f"{label} {raw!r} has exponent {exp}; "
            f"absolute value must be ≤ {_MAX_DECIMAL_EXP}."
        )
    return d


def _canonical_number(d: Decimal) -> str:
    """
    Return a canonical string for a bounded finite Decimal without calling
    Python int() on arbitrarily large values.  Since _bounded_decimal already
    enforces _MAX_DECIMAL_DIGITS the coefficient is at most 28 digits, so
    the resulting string is bounded regardless.
    """
    if d == 0:
        return "0"
    text = format(d, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


# ---------------------------------------------------------------------------
# Namespace constants
# ---------------------------------------------------------------------------

NS = {
    "ss":  "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "r":   "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "pr":  "http://schemas.openxmlformats.org/package/2006/relationships",
    "ct":  "http://schemas.openxmlformats.org/package/2006/content-types",
}

_SS  = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_R   = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
_PR  = "{http://schemas.openxmlformats.org/package/2006/relationships}"
_CT  = "{http://schemas.openxmlformats.org/package/2006/content-types}"

# Content-type fragments that indicate unsupported workbook types
_REJECT_CONTENT_TYPES = {
    "application/vnd.ms-excel.sheet.macroEnabled",        # xlsm
    "application/vnd.ms-excel.template.macroEnabled",
    "application/vnd.ms-excel.addin.macroEnabled",
    "application/vnd.ms-excel.sheet.binary",              # xlsb
}

_SIGNATURE_PART = "/xmlsignatures"
_ENCRYPTION_CONTENT_TYPE = (
    "application/vnd.ms-office.activeX+xml",
    "application/vnd.ms-office.encrypt",
)

# Column letter cache
_COL_CACHE: dict[str, int] = {}


# ---------------------------------------------------------------------------
# ZIP / XML helpers
# ---------------------------------------------------------------------------

def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _safe_parse(xml_bytes: bytes, source_label: str = "") -> ET._Element:
    """Parse XML with network access and entity expansion disabled."""
    parser = ET.XMLParser(
        resolve_entities=False,
        no_network=True,
        load_dtd=False,
        huge_tree=False,
    )
    try:
        return ET.fromstring(xml_bytes, parser=parser)
    except ET.XMLSyntaxError as exc:
        raise ValueError(f"Malformed XML in {source_label}: {exc}") from exc


def _open_zip(data: bytes) -> zipfile.ZipFile:
    """Open bytes as ZIP; enforce size limits."""
    if len(data) > 10 * 1024 * 1024:
        raise ValueError("Workbook exceeds 10 MB compressed limit.")
    try:
        zf = zipfile.ZipFile(io.BytesIO(data), "r")
    except zipfile.BadZipFile as exc:
        raise ValueError("This is not a valid ordinary .xlsx ZIP package.") from exc
    entries = zf.infolist()
    if len(entries) > 2000:
        raise ValueError(f"ZIP has {len(entries)} entries; limit is 2000.")
    total = sum(e.file_size for e in entries)
    if total > 100 * 1024 * 1024:
        raise ValueError(
            f"ZIP expanded size {total} bytes exceeds 100 MB limit."
        )
    names = [entry.filename.lstrip("/").lower() for entry in entries]
    if len(names) != len(set(names)):
        raise ValueError("Workbook ZIP contains duplicate or ambiguous entries.")
    if any(entry.flag_bits & 1 for entry in entries):
        raise ValueError("Encrypted workbook ZIP entries are not supported.")
    if "xl/metadata.xml" in names:
        raise ValueError("Workbooks with dynamic-array or rich-value metadata are not supported.")
    return zf


def _zip_read(zf: zipfile.ZipFile, name: str) -> bytes:
    """Read a ZIP member; normalise leading slash differences."""
    names = zf.namelist()
    for candidate in (name, name.lstrip("/")):
        if candidate in names:
            try:
                return zf.read(candidate)
            except (zipfile.BadZipFile, RuntimeError, NotImplementedError) as exc:
                raise ValueError(f"Cannot read workbook ZIP entry {candidate!r}.") from exc
    raise ValueError(f"Required ZIP entry not found: {name!r}")


def _zip_names_ci(zf: zipfile.ZipFile) -> dict[str, str]:
    """Return lower-case-name -> actual-name mapping for the ZIP."""
    return {n.lower(): n for n in zf.namelist()}


# ---------------------------------------------------------------------------
# Relationship resolution
# ---------------------------------------------------------------------------

def _parse_rels(zf: zipfile.ZipFile, part_path: str) -> dict[str, str]:
    """
    Parse a .rels file for *part_path* and return {Id: target_path}.
    part_path is like 'xl/workbook.xml'; rels live at
    'xl/_rels/workbook.xml.rels'.
    """
    dir_part  = part_path.rsplit("/", 1)[0] if "/" in part_path else ""
    base_name = part_path.rsplit("/", 1)[-1]
    rels_path = (
        f"{dir_part}/_rels/{base_name}.rels"
        if dir_part
        else f"_rels/{base_name}.rels"
    )
    try:
        raw = _zip_read(zf, rels_path)
    except ValueError:
        return {}
    root = _safe_parse(raw, rels_path)
    result: dict[str, str] = {}
    for rel in root:
        rid    = rel.get("Id", "")
        target = rel.get("Target", "")
        tmode  = rel.get("TargetMode", "Internal")
        if tmode == "External" or not target:
            continue
        # Resolve relative target against the directory of part_path
        if not target.startswith("/"):
            target = (f"{dir_part}/{target}" if dir_part else target)
        else:
            target = target.lstrip("/")
        # Normalise "xl/worksheets/../sharedStrings.xml" etc.
        parts: list[str] = []
        for seg in target.split("/"):
            if seg == "..":
                if parts:
                    parts.pop()
            elif seg and seg != ".":
                parts.append(seg)
        result[rid] = "/".join(parts)
    return result


def _find_workbook_part(zf: zipfile.ZipFile) -> str:
    """Return the path of the workbook part from [Content_Types].xml."""
    ct_raw  = _zip_read(zf, "[Content_Types].xml")
    ct_root = _safe_parse(ct_raw, "[Content_Types].xml")
    for override in ct_root.iter(f"{_CT}Override"):
        ct = override.get("ContentType", "")
        for bad in _REJECT_CONTENT_TYPES:
            if bad in ct:
                raise ValueError(
                    f"Unsupported workbook type ({ct}). "
                    "Only .xlsx (macro-free) is supported."
                )
        if "spreadsheetml.sheet.main" in ct:
            return override.get("PartName", "").lstrip("/")
    # Fallback: try _rels/.rels
    try:
        root_rels = _parse_rels(zf, "_rels/.rels")
    except Exception:
        root_rels = {}
    # Also check _rels/.rels via direct read
    try:
        rr = _zip_read(zf, "_rels/.rels")
        rr_root = _safe_parse(rr, "_rels/.rels")
        for rel in rr_root:
            if "officeDocument" in rel.get("Type", ""):
                target = rel.get("Target", "").lstrip("/")
                if target:
                    return target
    except ValueError:
        pass
    raise ValueError("Cannot locate workbook part in [Content_Types].xml.")


def _check_signatures(zf: zipfile.ZipFile) -> None:
    names_lc = {n.lower() for n in zf.namelist()}
    for n in names_lc:
        if "_xmlsignatures" in n or "digitalsignature" in n:
            raise ValueError(
                "Workbook contains digital signatures and cannot be processed."
            )


def _check_encryption(zf: zipfile.ZipFile) -> None:
    # Encrypted xlsx is not a valid ZIP (EncryptedPackage); we won't reach
    # here, but guard against EncryptionInfo parts.
    for n in zf.namelist():
        if "encryptioninfo" in n.lower():
            raise ValueError("Workbook appears to be encrypted.")


# ---------------------------------------------------------------------------
# Shared-string table
# ---------------------------------------------------------------------------

def _load_shared_strings(zf: zipfile.ZipFile, wb_dir: str) -> list[str]:
    """Return the shared-string table as a list of plain strings."""
    try:
        raw = _zip_read(zf, f"{wb_dir}/sharedStrings.xml")
    except ValueError:
        return []
    root = _safe_parse(raw, "sharedStrings.xml")
    result: list[str] = []
    for si in root.iter(f"{_SS}si"):
        # Concatenate all <t> children, preserving rich-text runs
        text = "".join(
            t.text or ""
            for t in si.iter(f"{_SS}t")
        )
        result.append(text)
    return result


# ---------------------------------------------------------------------------
# Cell address utilities
# ---------------------------------------------------------------------------

_COL_RE = re.compile(r"^([A-Z]{1,3})(\d{1,7})$")


def _col_index(letters: str) -> int:
    """Convert column letters (A=1, Z=26, AA=27, …) to 1-based int."""
    if letters in _COL_CACHE:
        return _COL_CACHE[letters]
    n = 0
    for ch in letters:
        n = n * 26 + (ord(ch) - ord("A") + 1)
    _COL_CACHE[letters] = n
    return n


def _address_to_rc(addr: str) -> tuple[int, int]:
    """Return (row_1based, col_1based) from an A1-style address like 'B3'."""
    m = _COL_RE.match(addr.upper())
    if not m:
        raise ValueError(f"Cannot parse cell address: {addr!r}")
    row, col = int(m.group(2)), _col_index(m.group(1))
    if not (1 <= row <= 1_048_576 and 1 <= col <= 16_384):
        raise ValueError(f"Cell address is outside the worksheet bounds: {addr!r}")
    return row, col


def _col_letters(col: int) -> str:
    """Convert 1-based column index to letters."""
    s = ""
    while col > 0:
        col, rem = divmod(col - 1, 26)
        s = chr(ord("A") + rem) + s
    return s


def _rc_to_address(row: int, col: int) -> str:
    return f"{_col_letters(col)}{row}"


# ---------------------------------------------------------------------------
# Merged-cell and array/shared-formula range detection
# ---------------------------------------------------------------------------

def _parse_ref_range(ref: str) -> tuple[tuple[int, int], tuple[int, int]] | None:
    """
    Parse a range like 'A1:C3' or a single cell 'A1'.
    Returns ((r1,c1),(r2,c2)) or None on parse failure.
    """
    parts = ref.replace("$", "").split(":")
    if len(parts) == 1:
        try:
            r, c = _address_to_rc(parts[0])
            return (r, c), (r, c)
        except ValueError:
            return None
    if len(parts) == 2:
        try:
            r1, c1 = _address_to_rc(parts[0])
            r2, c2 = _address_to_rc(parts[1])
            if r1 > r2 or c1 > c2:
                return None
            return (r1, c1), (r2, c2)
        except ValueError:
            return None
    return None


def _build_protected_ranges(
    ws_root: ET._Element, max_row: int, max_col: int
) -> set[tuple[int, int]]:
    """
    Return the set of (row, col) cells that must be protected:
    - every merged cell, including its origin
    - every formula reference range, clipped to the supported table
    """
    protected: set[tuple[int, int]] = set()

    # Merged cells
    for mc_elem in ws_root.iter(f"{_SS}mergeCell"):
        ref = mc_elem.get("ref", "")
        parsed = _parse_ref_range(ref)
        if parsed is None:
            raise ValueError(f"Unsupported merged-cell range: {ref!r}.")
        (r1, c1), (r2, c2) = parsed
        for r in range(r1, min(r2, max_row) + 1):
            for c in range(c1, min(c2, max_col) + 1):
                protected.add((r, c))

    # Array formulas and shared formulas (all cells in range are protected)
    for f_elem in ws_root.iter(f"{_SS}f"):
        ref   = f_elem.get("ref", "")
        if ref:
            parsed = _parse_ref_range(ref)
            if parsed is None:
                raise ValueError(f"Unsupported formula reference range: {ref!r}.")
            (r1, c1), (r2, c2) = parsed
            for r in range(r1, min(r2, max_row) + 1):
                for c in range(c1, min(c2, max_col) + 1):
                    protected.add((r, c))

    return protected


# ---------------------------------------------------------------------------
# Style detection (number formats indicating date/currency/etc.)
# ---------------------------------------------------------------------------

def _build_style_lookup(zf: zipfile.ZipFile, wb_dir: str) -> dict[int, str]:
    """
    Return {xfIndex: kind} where kind is 'date', 'currency', or 'number'.
    Built-in date/time and currency/accounting formats remain read-only.
    """
    try:
        raw = _zip_read(zf, f"{wb_dir}/styles.xml")
    except ValueError:
        return {}
    root = _safe_parse(raw, "styles.xml")

    # 1. Collect custom numFmt id → formatCode
    custom_fmts: dict[int, str] = {}
    for nf in root.iter(f"{_SS}numFmt"):
        try:
            fid  = int(nf.get("numFmtId", "-1"))
            code = nf.get("formatCode", "")
            custom_fmts[fid] = code
        except ValueError:
            pass

    # 2. Walk cellXfs
    xfs = root.find(f".//{_SS}cellXfs")
    if xfs is None:
        return {}

    result: dict[int, str] = {}
    date_builtin = set(range(14, 23)) | set(range(27, 37)) | {45, 46, 47} | set(range(50, 59))
    money_builtin = set(range(5, 9)) | set(range(41, 45))

    for idx, xf in enumerate(xfs.iter(f"{_SS}xf")):
        try:
            fid = int(xf.get("numFmtId", "0"))
        except ValueError:
            fid = 0
        if fid in date_builtin:
            result[idx] = "date"
        elif fid in money_builtin:
            result[idx] = "currency"
        elif fid in custom_fmts:
            code = custom_fmts[fid].lower()
            tokens = re.sub(r'"[^"]*"|\\.|_.|\*.|\[(?![hms]+\])[^]]*\]', "", code)
            if re.search(r"[ymdhs]", tokens):
                result[idx] = "date"
            elif any(c in code for c in ("$", "€", "£", "¥", "usd", "eur")):
                result[idx] = "currency"
    return result


# ---------------------------------------------------------------------------
# Cell value extraction
# ---------------------------------------------------------------------------

def _cell_kind_value(
    c_elem: ET._Element,
    shared_strings: list[str],
    style_lookup: dict[int, str],
    is_protected: bool,
) -> tuple[str, str]:
    """
    Return (kind, value) for a <c> element.

    kind: "number" | "text" | "blank" | "formula" | "boolean" | "unsupported"
    value: string representation suitable for display and comparison
    """
    t  = c_elem.get("t", "")           # type attribute
    s  = c_elem.get("s")               # style index

    f_elem = c_elem.find(f"{_SS}f")
    v_elem = c_elem.find(f"{_SS}v")
    is_elem = c_elem.find(f"{_SS}is")  # inlineStr

    # Formula cell
    if f_elem is not None:
        # Value is the formula expression, not the cached numeric result
        formula = f_elem.text or ""
        return "formula", ("=" + formula if not formula.startswith("=") else formula)

    # Inline string
    if t == "inlineStr" and is_elem is not None:
        texts = "".join(
            tt.text or "" for tt in is_elem.iter(f"{_SS}t")
        )
        return "text", texts

    # Shared string
    if t == "s":
        if v_elem is None or v_elem.text is None:
            return "blank", ""
        try:
            idx = int(v_elem.text)
        except ValueError:
            return "unsupported", ""
        if idx < 0 or idx >= len(shared_strings):
            return "unsupported", ""
        return "text", shared_strings[idx]

    # Boolean
    if t == "b":
        raw = (v_elem.text or "").strip() if v_elem is not None else ""
        return "boolean", ("TRUE" if raw == "1" else "FALSE")

    # Error
    if t == "e":
        raw = (v_elem.text or "").strip() if v_elem is not None else ""
        return "unsupported", raw

    if t == "d":
        return "unsupported", (v_elem.text or "") if v_elem is not None else ""

    # Style-based classification: date or currency → unsupported for editing
    if s is not None:
        try:
            xf_idx = int(s)
            stype  = style_lookup.get(xf_idx, "")
            if stype in ("date", "currency"):
                return "unsupported", (v_elem.text or "") if v_elem is not None else ""
        except ValueError:
            pass

    # Blank (no value element)
    if v_elem is None or v_elem.text is None:
        return "blank", ""

    # Numeric (default when no type attribute)
    raw = v_elem.text.strip()
    try:
        d = _bounded_decimal(raw, "cell numeric value")
        return "number", _canonical_number(d)
    except ValueError:
        return "unsupported", raw


# ---------------------------------------------------------------------------
# Sheet parsing
# ---------------------------------------------------------------------------

def _parse_sheet(
    ws_bytes: bytes,
    shared_strings: list[str],
    style_lookup: dict[int, str],
    ws_name: str,
) -> dict[str, Any]:
    """
    Parse a worksheet XML and return:
      {
        "headers":   [str, ...],          # row-1 header values
        "rows":      int,                 # count of non-blank data rows
        "row_data":  [                    # for read_table
            {col_index_1based: (kind, value, address), ...}, ...
          ]
        "protected_rc": set[(row,col)],   # merged/array/shared formula ranges
      }
    Entirely blank trailing rows are excluded from count.
    """
    root = _safe_parse(ws_bytes, ws_name)
    protection = root.find(f"{_SS}sheetProtection")
    if protection is not None and protection.get("sheet", "0").lower() not in ("0", "false"):
        raise ValueError(f"Sheet {ws_name!r} is protected. Use an unprotected worksheet.")
    protected_rc: set[tuple[int, int]] = set()
    sheet_data = root.find(f".//{_SS}sheetData")
    if sheet_data is None:
        return {
            "headers": [], "rows": 0, "row_data": [], "protected_rc": protected_rc
        }

    # Collect rows
    all_rows: dict[int, dict[int, tuple[str, str, str]]] = {}
    for row_elem in sheet_data.iter(f"{_SS}row"):
        try:
            rn = int(row_elem.get("r", "0"))
        except ValueError:
            raise ValueError(f"Invalid row number in sheet {ws_name!r}.")
        if not 1 <= rn <= 1_048_576:
            raise ValueError(f"Row {rn} is outside the worksheet bounds.")
        cols: dict[int, tuple[str, str, str]] = {}
        for c_elem in row_elem.iter(f"{_SS}c"):
            addr = c_elem.get("r", "")
            if not addr:
                continue
            try:
                cell_row, cc = _address_to_rc(addr)
            except ValueError:
                raise ValueError(f"Invalid cell address {addr!r} in sheet {ws_name!r}.")
            if cell_row != rn or cc in cols:
                raise ValueError(f"Duplicate or misplaced cell address {addr!r}.")
            is_prot = "cm" in c_elem.attrib or "vm" in c_elem.attrib
            if is_prot:
                protected_rc.add((rn, cc))
            kind, val = _cell_kind_value(
                c_elem, shared_strings, style_lookup, is_prot
            )
            cols[cc] = (kind, val, addr)
        if cols:
            if rn in all_rows:
                raise ValueError(f"Duplicate row {rn} in sheet {ws_name!r}.")
            all_rows[rn] = cols

    if not all_rows:
        return {
            "headers": [], "rows": 0, "row_data": [], "protected_rc": protected_rc
        }

    max_row = max((rn for rn, cols in all_rows.items()
                   if any(kind != "blank" and value != "" for kind, value, _ in cols.values())), default=1)
    if max_row > 501:
        raise ValueError(f"Sheet has data beyond row 501; limit is 500 data rows.")
    max_col = max(
        (ci for rn, cols in all_rows.items() if rn <= max_row
         for ci, (kind, value, _) in cols.items() if kind != "blank" and value != ""),
        default=0,
    )
    if max_col > 100:
        raise ValueError("The selected table exceeds the 100-column limit.")
    protected_rc.update(_build_protected_ranges(root, max_row, max_col))

    # Row 1 → headers
    headers: list[str] = []
    if 1 in all_rows:
        for ci in range(1, max_col + 1):
            cell = all_rows[1].get(ci)
            if cell:
                headers.append(cell[1])
            else:
                headers.append("")
    else:
        headers = [""] * max_col

    # Data rows (2..max_row), strip trailing blank rows
    row_data: list[dict[int, tuple[str, str, str]]] = []
    last_data_row = 1
    for rn in range(2, max_row + 1):
        cols = all_rows.get(rn, {})
        # Check if row has any non-blank value
        has_data = any(
            kind != "blank" and value != ""
            for kind, value, _ in cols.values()
        )
        if has_data:
            last_data_row = rn
        row_data.append(cols)

    # Trim trailing blank rows
    row_data = row_data[: last_data_row - 1]  # index 0 = row 2

    return {
        "headers":    headers,
        "rows":       len(row_data),
        "row_data":   row_data,
        "protected_rc": protected_rc,
    }


# ---------------------------------------------------------------------------
# Public API – inspect_workbook
# ---------------------------------------------------------------------------

def inspect_workbook(data: bytes) -> dict:
    """
    Returns::

        {
            "sheets": [{"name": str, "headers": [str], "rows": int}],
            "sha256": str,
        }

    Raises ValueError for any structural, format or safety problem.
    """
    sha = _sha256(data)
    zf  = _open_zip(data)
    _check_signatures(zf)
    _check_encryption(zf)

    wb_path  = _find_workbook_part(zf)
    wb_dir   = wb_path.rsplit("/", 1)[0] if "/" in wb_path else ""
    wb_bytes = _zip_read(zf, wb_path)
    wb_root  = _safe_parse(wb_bytes, wb_path)

    # Shared strings and styles
    shared_strings = _load_shared_strings(zf, wb_dir)
    style_lookup   = _build_style_lookup(zf, wb_dir)

    # Workbook relationships → sheet part paths
    wb_rels = _parse_rels(zf, wb_path)

    # Sheet name → rid from workbook.xml
    sheets_elem = wb_root.find(f"{_SS}sheets")
    if sheets_elem is None:
        raise ValueError("Workbook contains no <sheets> element.")

    sheet_infos: list[dict] = []
    errors: list[str] = []

    for sh_elem in sheets_elem.iter(f"{_SS}sheet"):
        sh_name = sh_elem.get("name", "")
        rid     = sh_elem.get(f"{_R}id", "")
        if not sh_name or not rid:
            continue
        ws_path = wb_rels.get(rid)
        if not ws_path:
            errors.append(f"No relationship target for sheet {sh_name!r}.")
            continue
        try:
            ws_bytes = _zip_read(zf, ws_path)
        except ValueError as exc:
            errors.append(str(exc))
            continue
        try:
            parsed = _parse_sheet(ws_bytes, shared_strings, style_lookup, sh_name)
        except ValueError as exc:
            errors.append(f"Sheet {sh_name!r}: {exc}")
            continue
        # Validate unique non-empty headers (required for use as a table)
        headers = parsed["headers"]
        non_empty = [h for h in headers if h.strip()]
        if len(non_empty) != len(headers):
            errors.append(f"Sheet {sh_name!r} skipped: blank headers in the table.")
            continue
        if len(non_empty) != len(set(non_empty)):
            errors.append(
                f"Sheet {sh_name!r} skipped: duplicate headers."
            )
            continue
        if not non_empty:
            errors.append(f"Sheet {sh_name!r} skipped: no headers in row 1.")
            continue
        sheet_infos.append({
            "name":    sh_name,
            "headers": [h for h in headers if h.strip()],
            "rows":    parsed["rows"],
        })

    if not sheet_infos:
        combined = "; ".join(errors) if errors else "No usable sheets found."
        raise ValueError(f"Workbook contains no usable sheets: {combined}")

    return {"sheets": sheet_infos, "sha256": sha}


# ---------------------------------------------------------------------------
# Public API – read_table
# ---------------------------------------------------------------------------

def read_table(
    data: bytes,
    sheet: str,
    id_column: str,
    editable_columns: list[str],
) -> dict:
    """
    Returns::

        {
            "sheet": str,
            "idColumn": str,
            "editableColumns": [str],
            "headers": [str],
            "rows": [
                {
                    "id": str,          # "text:foo" or "number:1"
                    "label": str,
                    "cells": {
                        header: {
                            "kind": str,
                            "value": str,
                            "address": str,
                            "protected": bool,
                        }
                    }
                }
            ],
            "sha256": str,
        }

    Raises ValueError for any validation failure.
    """
    sha = _sha256(data)
    zf  = _open_zip(data)
    _check_signatures(zf)
    _check_encryption(zf)

    wb_path  = _find_workbook_part(zf)
    wb_dir   = wb_path.rsplit("/", 1)[0] if "/" in wb_path else ""
    wb_bytes = _zip_read(zf, wb_path)
    wb_root  = _safe_parse(wb_bytes, wb_path)

    shared_strings = _load_shared_strings(zf, wb_dir)
    style_lookup   = _build_style_lookup(zf, wb_dir)
    wb_rels        = _parse_rels(zf, wb_path)

    sheets_elem = wb_root.find(f"{_SS}sheets")
    if sheets_elem is None:
        raise ValueError("Workbook contains no <sheets> element.")

    ws_bytes: bytes | None = None
    ws_path:  str | None   = None

    for sh_elem in sheets_elem.iter(f"{_SS}sheet"):
        if sh_elem.get("name", "") == sheet:
            rid     = sh_elem.get(f"{_R}id", "")
            ws_path = wb_rels.get(rid)
            if ws_path:
                ws_bytes = _zip_read(zf, ws_path)
            break

    if ws_bytes is None:
        raise ValueError(f"Sheet {sheet!r} not found in workbook.")

    parsed       = _parse_sheet(ws_bytes, shared_strings, style_lookup, sheet)
    all_headers  = parsed["headers"]
    protected_rc = parsed["protected_rc"]
    row_data     = parsed["row_data"]

    if not all_headers or any(not header.strip() for header in all_headers):
        raise ValueError("Every column in the selected table must have a nonempty header.")
    if len(all_headers) != len(set(all_headers)):
        raise ValueError("The selected table contains duplicate headers.")

    # Build header → col_index_1based map (only non-empty headers)
    # Row 1 is headers, so col indices are aligned with parsed data.
    header_to_col: dict[str, int] = {}
    for ci, h in enumerate(all_headers, start=1):
        if h.strip():
            header_to_col[h] = ci

    # Validate id_column
    if id_column not in header_to_col:
        raise ValueError(
            f"ID column {id_column!r} not found in sheet headers."
        )

    # Validate editable_columns
    if not (1 <= len(editable_columns) <= 2):
        raise ValueError(
            f"Require 1 or 2 editable columns; got {len(editable_columns)}."
        )
    for ec in editable_columns:
        if ec == id_column:
            raise ValueError(
                f"Editable column {ec!r} must be distinct from ID column."
            )
        if ec not in header_to_col:
            raise ValueError(
                f"Editable column {ec!r} not found in sheet headers."
            )
    if len(set(editable_columns)) != len(editable_columns):
        raise ValueError("Duplicate editable columns specified.")

    if len(row_data) > 500:
        raise ValueError(
            f"Sheet has {len(row_data)} data rows; limit is 500."
        )

    # Build rows
    id_col_idx = header_to_col[id_column]
    rows: list[dict] = []
    seen_ids: set[str] = set()

    for row_idx, cols in enumerate(row_data):
        row_number = row_idx + 2  # row 1 = headers

        # Resolve ID cell
        id_cell = cols.get(id_col_idx)
        if id_cell is None:
            raise ValueError(
                f"Row {row_number}: missing ID in column {id_column!r}."
            )
        id_kind, id_val, id_addr = id_cell
        if id_kind == "blank" or id_val == "":
            raise ValueError(
                f"Row {row_number}: blank ID in column {id_column!r}."
            )
        if id_kind == "formula":
            raise ValueError(f"Row {row_number}: formula IDs are not supported. Use stable text or numeric IDs.")
        elif id_kind == "number":
            # Canonical: strip trailing .0 etc. already done in _cell_kind_value
            typed_id = f"number:{id_val}"
            label    = id_val
        elif id_kind == "text":
            typed_id = f"text:{id_val}"
            label    = id_val
        else:
            raise ValueError(
                f"Row {row_number}: unsupported ID type {id_kind!r} "
                f"in column {id_column!r}."
            )

        if (row_number, id_col_idx) in protected_rc:
            raise ValueError(f"Row {row_number}: ID belongs to a protected range or metadata cell.")

        if typed_id in seen_ids:
            raise ValueError(
                f"Duplicate row ID {typed_id!r} at row {row_number}."
            )
        seen_ids.add(typed_id)

        # Build cell dict for each non-empty header
        cells: dict[str, dict] = {}
        for h, ci in header_to_col.items():
            cell_tuple = cols.get(ci)
            addr       = _rc_to_address(row_number, ci)
            if cell_tuple:
                kind, val, addr = cell_tuple
            else:
                kind, val = "blank", ""

            # Determine protection
            is_formula   = kind == "formula"
            is_merged    = (row_number, ci) in protected_rc
            is_unsupported_edit = (
                h in editable_columns
                and kind in ("unsupported", "boolean", "date", "currency")
            )
            protected = is_formula or is_merged or is_unsupported_edit

            cells[h] = {
                "kind":      kind,
                "value":     val,
                "address":   addr,
                "protected": protected,
            }

        rows.append({
            "id":    typed_id,
            "label": label,
            "cells": cells,
        })

    return {
        "sheet":           sheet,
        "idColumn":        id_column,
        "editableColumns": list(editable_columns),
        "headers":         [h for h in all_headers if h.strip()],
        "rows":            rows,
        "sha256":          sha,
    }


# ---------------------------------------------------------------------------
# Public API – patch_workbook
# ---------------------------------------------------------------------------

def patch_workbook(
    data: bytes,
    sheet: str,
    id_column: str,
    editable_columns: list[str],
    patches: list[dict],
) -> bytes:
    """
    Apply approved patches to the CURRENT workbook bytes.

    Each patch::

        {
            "rowId": str,
            "field": str,
            "value": {"kind": "number"|"text", "value": str},
        }

    Returns new workbook bytes.  Never extracts ZIP to disk.
    Re-validates all targets from CURRENT before writing.
    Raises ValueError if any patch is invalid.
    """
    # Re-resolve the full table to get authoritative addresses
    table = read_table(data, sheet, id_column, editable_columns)

    # Build lookup: (rowId, field) → cell dict
    target_map: dict[tuple[str, str], dict] = {}
    id_to_label: dict[str, str] = {}
    for row in table["rows"]:
        rid   = row["id"]
        label = row["label"]
        id_to_label[rid] = label
        for h, cell in row["cells"].items():
            target_map[(rid, h)] = cell

    # Validate patches before touching bytes
    if not patches:
        raise ValueError("No patches provided.")

    seen_targets: set[tuple[str, str]] = set()
    for p in patches:
        rid   = p.get("rowId", "")
        field = p.get("field", "")
        pval  = p.get("value", {})
        kind  = pval.get("kind", "")
        raw   = pval.get("value", "")

        key = (rid, field)
        if key in seen_targets:
            raise ValueError(
                f"Duplicate patch target rowId={rid!r} field={field!r}."
            )
        seen_targets.add(key)

        if rid not in id_to_label:
            raise ValueError(f"Unknown rowId {rid!r} in patch.")
        if field not in editable_columns:
            raise ValueError(
                f"Field {field!r} is not in editable_columns for rowId {rid!r}."
            )
        cell = target_map.get(key)
        if cell is None:
            raise ValueError(
                f"Target cell not found for rowId={rid!r} field={field!r}."
            )
        if cell["protected"]:
            raise ValueError(
                f"Target cell for rowId={rid!r} field={field!r} is protected."
            )
        if kind not in ("number", "text"):
            raise ValueError(
                f"Patch kind must be 'number' or 'text'; got {kind!r}."
            )
        if not raw and raw != "0":
            raise ValueError(
                f"Patch value is empty for rowId={rid!r} field={field!r}."
            )
        if kind == "number":
            # Validates finiteness and digit/exponent bounds; raises on failure.
            _bounded_decimal(
                raw, f"patch value for rowId={rid!r} field={field!r}"
            )

    # Open ZIP; plan to rewrite only the target worksheet
    zf      = _open_zip(data)
    wb_path = _find_workbook_part(zf)
    wb_dir  = wb_path.rsplit("/", 1)[0] if "/" in wb_path else ""
    wb_root = _safe_parse(_zip_read(zf, wb_path), wb_path)
    wb_rels = _parse_rels(zf, wb_path)
    shared_strings = _load_shared_strings(zf, wb_dir)
    style_lookup   = _build_style_lookup(zf, wb_dir)

    sheets_elem = wb_root.find(f"{_SS}sheets")
    if sheets_elem is None:
        raise ValueError("Workbook contains no <sheets> element.")

    ws_path: str | None = None
    for sh_elem in sheets_elem.iter(f"{_SS}sheet"):
        if sh_elem.get("name", "") == sheet:
            rid     = sh_elem.get(f"{_R}id", "")
            ws_path = wb_rels.get(rid)
            break
    if ws_path is None:
        raise ValueError(f"Sheet {sheet!r} not found when patching.")

    ws_bytes = _zip_read(zf, ws_path)
    ws_root  = _safe_parse(ws_bytes, ws_path)

    # Build address → patch map
    addr_to_patch: dict[str, dict] = {}
    for p in patches:
        rid   = p["rowId"]
        field = p["field"]
        cell  = target_map[(rid, field)]
        addr  = cell["address"]
        addr_to_patch[addr] = p

    # Apply patches, inserting <c> elements for blank cells that are absent
    # from the XML (Excel omits blank cells entirely from sheetData).
    sheet_data = ws_root.find(f".//{_SS}sheetData")
    if sheet_data is None:
        raise ValueError("Worksheet has no <sheetData> element.")

    patched_addrs: set[str] = set()

    # First pass: patch cells that already exist in the XML.
    for row_elem in sheet_data.iter(f"{_SS}row"):
        for c_elem in row_elem.iter(f"{_SS}c"):
            addr = c_elem.get("r", "")
            if addr not in addr_to_patch:
                continue
            p    = addr_to_patch[addr]
            kind = p["value"]["kind"]
            raw  = p["value"]["value"]
            _apply_patch_to_cell(c_elem, kind, raw)
            patched_addrs.add(addr)

    # Second pass: insert <c> elements for addresses not found in the XML.
    missing_addrs = set(addr_to_patch) - patched_addrs
    if missing_addrs:
        # Build a dict of existing row elements keyed by row number.
        existing_rows: dict[int, ET._Element] = {}
        for row_elem in sheet_data:
            try:
                rn = int(row_elem.get("r", "0"))
                existing_rows[rn] = row_elem
            except ValueError:
                pass

        for addr in missing_addrs:
            p    = addr_to_patch[addr]
            kind = p["value"]["kind"]
            raw  = p["value"]["value"]
            row_num, col_num = _address_to_rc(addr)

            # Find or create the <row> element for this row number.
            if row_num in existing_rows:
                row_elem = existing_rows[row_num]
            else:
                row_elem = _insert_row_element(sheet_data, row_num)
                existing_rows[row_num] = row_elem

            # Insert a new <c> element in column order within the row.
            c_elem = _insert_cell_element(row_elem, addr, col_num)
            _apply_patch_to_cell(c_elem, kind, raw)
            patched_addrs.add(addr)

    # Verify every approved target was actually patched.
    if patched_addrs != set(addr_to_patch):
        missed = set(addr_to_patch) - patched_addrs
        raise ValueError(
            f"Internal error: failed to patch cells at addresses: {sorted(missed)}."
        )

    # Verify output values match the requested patch values by re-reading the
    # modified XML through the same cell parser used for read_table.
    _verify_patches(ws_root, addr_to_patch, shared_strings, style_lookup)

    # Serialise patched worksheet
    new_ws_bytes = ET.tostring(
        ws_root,
        xml_declaration=True,
        encoding="UTF-8",
        standalone=True,
    )

    # Patch workbook.xml recalculation flag: set fullCalcOnLoad to 1
    _set_full_calc_on_load(wb_root)
    new_wb_bytes = ET.tostring(
        wb_root,
        xml_declaration=True,
        encoding="UTF-8",
        standalone=True,
    )

    # Reconstruct ZIP in memory
    out_buf = io.BytesIO()
    with zipfile.ZipFile(out_buf, "w", compression=zipfile.ZIP_DEFLATED) as zout:
        for entry in zf.infolist():
            name = entry.filename
            if name == ws_path:
                zout.writestr(entry, new_ws_bytes)
            elif name == wb_path:
                zout.writestr(entry, new_wb_bytes)
            else:
                zout.writestr(entry, _zip_read(zf, name))

    result = out_buf.getvalue()

    # Re-open generated package to validate structure and parse the worksheet.
    _validate_output_zip(result, ws_path)

    return result


def _insert_row_element(sheet_data: ET._Element, row_num: int) -> ET._Element:
    """
    Insert a new <row r="row_num"> into sheet_data in ascending row order.
    Returns the new element.
    """
    new_row = ET.Element(f"{_SS}row")
    new_row.set("r", str(row_num))

    # Find the insertion position: first existing row with r > row_num.
    insert_before: ET._Element | None = None
    for existing in sheet_data:
        try:
            er = int(existing.get("r", "0"))
        except ValueError:
            continue
        if er > row_num:
            insert_before = existing
            break

    if insert_before is not None:
        idx = list(sheet_data).index(insert_before)
        sheet_data.insert(idx, new_row)
    else:
        sheet_data.append(new_row)

    return new_row


def _insert_cell_element(
    row_elem: ET._Element, addr: str, col_num: int
) -> ET._Element:
    """
    Insert a new <c r="addr"> into row_elem in ascending column order.
    Returns the new element.
    """
    new_c = ET.Element(f"{_SS}c")
    new_c.set("r", addr)

    # Find insertion position: first existing <c> with column index > col_num.
    insert_before: ET._Element | None = None
    for existing_c in row_elem.iter(f"{_SS}c"):
        ec_addr = existing_c.get("r", "")
        try:
            _, ec_col = _address_to_rc(ec_addr)
        except ValueError:
            continue
        if ec_col > col_num:
            insert_before = existing_c
            break

    if insert_before is not None:
        idx = list(row_elem).index(insert_before)
        row_elem.insert(idx, new_c)
    else:
        extension = row_elem.find(f"{_SS}extLst")
        if extension is not None:
            row_elem.insert(list(row_elem).index(extension), new_c)
        else:
            row_elem.append(new_c)

    return new_c


def _verify_patches(
    ws_root: ET._Element,
    addr_to_patch: dict[str, dict],
    shared_strings: list[str],
    style_lookup: dict[int, str],
) -> None:
    """
    Re-read every patched cell from the modified XML and confirm the stored
    value matches the patch value.  Raises ValueError on any mismatch.
    """
    # Collect all <c> elements by address from the modified tree.
    found: dict[str, ET._Element] = {}
    sheet_data = ws_root.find(f".//{_SS}sheetData")
    if sheet_data is None:
        raise ValueError("Cannot verify: worksheet has no <sheetData>.")
    for c_elem in sheet_data.iter(f"{_SS}c"):
        addr = c_elem.get("r", "")
        if addr in addr_to_patch:
            found[addr] = c_elem

    for addr, p in addr_to_patch.items():
        if addr not in found:
            raise ValueError(
                f"Patch verification failed: cell {addr!r} not found in "
                "output XML."
            )
        c_elem = found[addr]
        read_kind, read_val = _cell_kind_value(
            c_elem, shared_strings, style_lookup, is_protected=False
        )
        exp_kind = p["value"]["kind"]
        exp_val  = p["value"]["value"]

        if exp_kind == "number":
            if read_kind != "number":
                raise ValueError(
                    f"Patch verification failed for {addr!r}: "
                    f"expected kind 'number', got {read_kind!r}."
                )
            # Compare via bounded Decimal
            try:
                d_exp  = _bounded_decimal(exp_val,  f"expected value at {addr}")
                d_read = _bounded_decimal(read_val, f"written value at {addr}")
            except ValueError as exc:
                raise ValueError(
                    f"Patch verification failed for {addr!r}: {exc}"
                ) from exc
            if d_exp != d_read:
                raise ValueError(
                    f"Patch verification failed for {addr!r}: "
                    f"expected {exp_val!r}, got {read_val!r}."
                )
        else:  # text
            if read_kind != "text":
                raise ValueError(
                    f"Patch verification failed for {addr!r}: "
                    f"expected kind 'text', got {read_kind!r}."
                )
            if read_val != exp_val:
                raise ValueError(
                    f"Patch verification failed for {addr!r}: "
                    f"expected {exp_val!r}, got {read_val!r}."
                )


def _apply_patch_to_cell(c_elem: ET._Element, kind: str, raw: str) -> None:
    """
    Rewrite <c> element in-place for a patched value.

    - Numbers: remove 't' attribute (default numeric), write <v>.
      Uses _bounded_decimal / _canonical_number; never calls int() on the
      raw string directly.
    - Texts: set t="inlineStr", write <is><t>.  Leading '=' stays literal.
      Significant whitespace is preserved; xml:space="preserve" set when
      the value contains leading or trailing whitespace.
    - Removes <v>, <f>, and <is> children before writing.
    - Preserves style attribute (s=) and all other attributes.
    """
    for tag in (f"{_SS}v", f"{_SS}f", f"{_SS}is"):
        child = c_elem.find(tag)
        if child is not None:
            c_elem.remove(child)

    if kind == "number":
        d = _bounded_decimal(raw, "patch numeric value")
        if "t" in c_elem.attrib:
            del c_elem.attrib["t"]
        v_elem      = ET.Element(f"{_SS}v")
        v_elem.text = _canonical_number(d)
        value_element = v_elem

    elif kind == "text":
        c_elem.set("t", "inlineStr")
        is_elem = ET.Element(f"{_SS}is")
        t_elem  = ET.SubElement(is_elem, f"{_SS}t")
        if raw != raw.strip():
            t_elem.set(
                "{http://www.w3.org/XML/1998/namespace}space", "preserve"
            )
        t_elem.text = raw
        value_element = is_elem

    extension = c_elem.find(f"{_SS}extLst")
    if extension is not None:
        c_elem.insert(list(c_elem).index(extension), value_element)
    else:
        c_elem.append(value_element)


def _set_full_calc_on_load(wb_root: ET._Element) -> None:
    """Enable a full automatic recalculation when Excel opens the workbook."""
    calc_pr = wb_root.find(f"{_SS}calcPr")
    if calc_pr is None:
        calc_pr = ET.Element(f"{_SS}calcPr")
        later_tags = {f"{_SS}{name}" for name in (
            "oleSize", "customWorkbookViews", "pivotCaches", "smartTagPr", "smartTagTypes",
            "webPublishing", "fileRecoveryPr", "webPublishObjects", "extLst",
        )}
        position = next((index for index, child in enumerate(wb_root) if child.tag in later_tags), len(wb_root))
        wb_root.insert(position, calc_pr)
    calc_pr.set("calcMode", "auto")
    calc_pr.set("forceFullCalc", "1")
    calc_pr.set("fullCalcOnLoad", "1")


def _validate_output_zip(data: bytes, ws_path: str) -> None:
    """
    Reopen generated bytes as a ZIP and parse the patched worksheet XML.
    Raises ValueError if the package is structurally invalid.
    """
    try:
        zf = zipfile.ZipFile(io.BytesIO(data), "r")
    except zipfile.BadZipFile as exc:
        raise ValueError(f"Generated package is not a valid ZIP: {exc}") from exc
    try:
        ws_bytes = _zip_read(zf, ws_path)
    except ValueError as exc:
        raise ValueError(
            f"Generated package missing worksheet {ws_path!r}: {exc}"
        ) from exc
    _safe_parse(ws_bytes, ws_path)  # will raise on malformed XML
