"""Verify approved cell updates and preserve the rest of an XLSX package."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import zipfile
from decimal import InvalidOperation

import lxml.etree as ET

from backend.workbook import (
    _SS,
    _R,
    _safe_parse,
    _open_zip,
    _zip_read,
    _find_workbook_part,
    _parse_rels,
    _load_shared_strings,
    _build_style_lookup,
    _address_to_rc,
    _bounded_decimal,
    _cell_kind_value,
)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _normalise_name(name: str) -> str:
    return name.lstrip("/")


def _read_normalised(zf: zipfile.ZipFile, name: str) -> bytes:
    return _zip_read(zf, name)


def _parse_xml(xml_bytes: bytes, label: str) -> ET._Element:
    return _safe_parse(xml_bytes, label)


def _elem_text_eq(a: ET._Element, b: ET._Element) -> bool:
    at = (a.text or "")
    bt = (b.text or "")
    if at != bt:
        return False
    at2 = (a.tail or "")
    bt2 = (b.tail or "")
    return at2 == bt2


_VALUE_CHILD_TAGS = {
    f"{_SS}v",
    f"{_SS}is",
}


def _cell_address(c_elem: ET._Element) -> str:
    return (c_elem.get("r") or "").upper()


def _assert_no_formula_changed(
    cur_c: ET._Element | None,
    out_c: ET._Element,
    addr: str,
) -> None:
    cur_f = None if cur_c is None else cur_c.find(f"{_SS}f")
    out_f = out_c.find(f"{_SS}f")

    if cur_f is None and out_f is not None:
        raise ValueError(
            f"Formula introduced at non-approved cell {addr!r} in output."
        )
    if cur_f is not None and out_f is None:
        raise ValueError(
            f"Formula deleted at cell {addr!r} in output."
        )
    if cur_f is not None and out_f is not None:
        if (cur_f.text or "") != (out_f.text or ""):
            raise ValueError(
                f"Formula expression changed at cell {addr!r}."
            )
        if dict(cur_f.attrib) != dict(out_f.attrib):
            raise ValueError(
                f"Formula attributes changed at cell {addr!r}."
            )


def _compare_cell_target(
    cur_c: ET._Element | None,
    out_c: ET._Element,
    addr: str,
    patch: dict,
    shared_strings: list[str],
    style_lookup: dict[int, str],
) -> None:
    if cur_c is not None and not _elem_text_eq(cur_c, out_c):
        raise ValueError(f"Non-value text changed at target cell {addr!r}.")
    if cur_c is None and (out_c.text or out_c.tail):
        raise ValueError(f"Unexpected non-value text on inserted target {addr!r}.")
    if cur_c is not None and cur_c.get("r") != out_c.get("r"):
        raise ValueError(f"Target cell coordinate changed at {addr!r}.")
    _assert_no_formula_changed(cur_c, out_c, addr)
    values = [child for child in out_c if child.tag in _VALUE_CHILD_TAGS]
    if len(values) != 1:
        raise ValueError(f"Target cell {addr!r} has ambiguous value elements.")
    value = values[0]
    if value.attrib or value.tail:
        raise ValueError(f"Target cell {addr!r} has unexpected value metadata.")
    if patch['value']['kind'] == 'number':
        if value.tag != f'{_SS}v' or len(value) or out_c.get('t') not in (None, 'n'):
            raise ValueError(f"Target cell {addr!r} has an invalid numeric representation.")
    else:
        if value.tag != f'{_SS}is' or out_c.get('t') != 'inlineStr' or len(value) != 1 or value.text:
            raise ValueError(f"Target cell {addr!r} has an invalid text representation.")
        text = value[0]
        space = '{http://www.w3.org/XML/1998/namespace}space'
        if text.tag != f'{_SS}t' or len(text) or text.tail or set(text.attrib) - {space}:
            raise ValueError(f"Target cell {addr!r} has unsupported text metadata.")
        if text.get(space) not in (None, 'preserve'):
            raise ValueError(f"Target cell {addr!r} has unsupported whitespace handling.")
        raw = patch['value']['value']
        if raw != raw.strip() and text.get(space) != 'preserve':
            raise ValueError(f"Target cell {addr!r} does not preserve its text whitespace.")

    if out_c.get("r", "").upper() != addr.upper():
        raise ValueError(
            f"Target cell {addr!r} coordinate changed in output "
            f"(got r={out_c.get('r')!r})."
        )

    cur_s = None if cur_c is None else cur_c.get("s")
    out_s = out_c.get("s")
    if cur_s != out_s:
        raise ValueError(
            f"Style attribute changed on target cell {addr!r}: "
            f"current s={cur_s!r}, output s={out_s!r}."
        )

    cur_non_vt = {
        k: v
        for k, v in (cur_c.attrib if cur_c is not None else {}).items()
        if k not in ("t", "r")
    }
    out_non_vt = {
        k: v for k, v in out_c.attrib.items() if k not in ("t", "r")
    }
    if cur_non_vt != out_non_vt:
        raise ValueError(
            f"Non-value/non-type attributes changed on target cell {addr!r}: "
            f"current={cur_non_vt!r}, output={out_non_vt!r}."
        )

    def _non_value_children(elem: ET._Element | None) -> list[ET._Element]:
        if elem is None:
            return []
        return [
            ch for ch in elem
            if ch.tag not in _VALUE_CHILD_TAGS and ch.tag != f"{_SS}f"
        ]

    cur_nvc = _non_value_children(cur_c)
    out_nvc = _non_value_children(out_c)
    if len(cur_nvc) != len(out_nvc):
        raise ValueError(
            f"Non-value children count changed on target cell {addr!r}: "
            f"current={len(cur_nvc)}, output={len(out_nvc)}."
        )
    for i, (cc, oc) in enumerate(zip(cur_nvc, out_nvc)):
        _compare_elements_strict(cc, oc, f"target cell {addr!r} non-value child[{i}]")

    read_kind, read_val = _cell_kind_value(
        out_c, shared_strings, style_lookup, is_protected=False
    )
    exp_kind = patch["value"]["kind"]
    exp_raw  = patch["value"]["value"]

    if exp_kind == "number":
        if read_kind != "number":
            raise ValueError(
                f"Output cell {addr!r} has kind {read_kind!r}; "
                f"expected 'number' per approved patch."
            )
        try:
            d_exp  = _bounded_decimal(exp_raw,  f"patch at {addr}")
            d_read = _bounded_decimal(read_val, f"output at {addr}")
        except (ValueError, InvalidOperation) as exc:
            raise ValueError(
                f"Value comparison failed for target cell {addr!r}: {exc}"
            ) from exc
        if d_exp != d_read:
            raise ValueError(
                f"Output value mismatch at {addr!r}: "
                f"patch requested {exp_raw!r}, output contains {read_val!r}."
            )
    else:  # text
        if read_kind != "text":
            raise ValueError(
                f"Output cell {addr!r} has kind {read_kind!r}; "
                f"expected 'text' per approved patch."
            )
        if read_val != exp_raw:
            raise ValueError(
                f"Output value mismatch at {addr!r}: "
                f"patch requested {exp_raw!r}, output contains {read_val!r}."
            )


def _compare_elements_strict(
    a: ET._Element,
    b: ET._Element,
    context: str,
) -> None:
    if a.tag != b.tag:
        raise ValueError(
            f"XML tag mismatch at {context}: {a.tag!r} vs {b.tag!r}."
        )
    if dict(a.attrib) != dict(b.attrib):
        raise ValueError(
            f"XML attribute mismatch at {context} <{a.tag}>: "
            f"{dict(a.attrib)!r} vs {dict(b.attrib)!r}."
        )
    at = (a.text or "")
    bt = (b.text or "")
    if at != bt:
        raise ValueError(
            f"XML text mismatch at {context} <{a.tag}>: "
            f"{at!r} vs {bt!r}."
        )
    if (a.tail or "") != (b.tail or ""):
        raise ValueError(f"XML tail text changed at {context}.")
    if len(a) != len(b):
        raise ValueError(
            f"XML child count mismatch at {context} <{a.tag}>: "
            f"{len(a)} vs {len(b)}."
        )
    for i, (ca, cb) in enumerate(zip(a, b)):
        _compare_elements_strict(ca, cb, f"{context}/{a.tag}[{i}]")


def _verify_worksheet(
    cur_ws_bytes: bytes, out_ws_bytes: bytes, ws_path: str,
    addr_to_patch: dict[str, dict], shared_strings: list[str], style_lookup: dict[int, str],
) -> tuple[int, int]:
    current = _parse_xml(cur_ws_bytes, ws_path + " (current)")
    output = _parse_xml(out_ws_bytes, ws_path + " (output)")

    def cells(root: ET._Element) -> dict[str, ET._Element]:
        found = {}
        for cell in root.findall(f"{_SS}sheetData/{_SS}row/{_SS}c"):
            address = _cell_address(cell)
            if not address or address in found:
                raise ValueError("Worksheet has missing or duplicate cell coordinates.")
            row_number, _ = _address_to_rc(address)
            if cell.getparent().get("r") != str(row_number):
                raise ValueError(f"Cell {address!r} is in the wrong worksheet row.")
            found[address] = cell
        return found

    before_cells, after_cells = cells(current), cells(output)
    for address, patch in addr_to_patch.items():
        after = after_cells.get(address)
        if after is None:
            raise ValueError(f"Approved target {address!r} is missing from output.")
        before = before_cells.get(address)
        _compare_cell_target(before, after, address, patch, shared_strings, style_lookup)
        if before is None:
            after.getparent().remove(after)
        else:
            after.getparent().replace(after, deepcopy(before))

    _compare_elements_strict(current, output, "worksheet outside approved cell values")
    return len(addr_to_patch), sum(1 for _ in current.iter(f"{_SS}f"))


_CALC_PR_TAG = f"{_SS}calcPr"
_REQUIRED_CALC_PR_NEW_VALUES = {
    "calcMode": "auto",
    "forceFullCalc": "1",
    "fullCalcOnLoad": "1",
}


def _verify_workbook_xml(cur_wb_bytes: bytes, out_wb_bytes: bytes, wb_path: str) -> None:
    current = _parse_xml(cur_wb_bytes, wb_path + " (current)")
    output = _parse_xml(out_wb_bytes, wb_path + " (output)")
    before = current.findall(_CALC_PR_TAG)
    after = output.findall(_CALC_PR_TAG)
    if len(before) > 1 or len(after) != 1:
        raise ValueError("Expected exactly one output calculation-settings element.")
    settings = after[0]
    for key, value in _REQUIRED_CALC_PR_NEW_VALUES.items():
        if settings.get(key) != value:
            raise ValueError(f"Expected calculation setting {key}={value!r}.")
        del settings.attrib[key]
    if before:
        if current.index(before[0]) != output.index(settings):
            raise ValueError("Workbook calculation-settings element moved.")
        for key in _REQUIRED_CALC_PR_NEW_VALUES:
            before[0].attrib.pop(key, None)
    else:
        if settings.attrib or len(settings) or settings.text or settings.tail:
            raise ValueError("Unexpected content in new workbook calculation settings.")
        later_tags = {f"{_SS}{name}" for name in (
            "oleSize", "customWorkbookViews", "pivotCaches", "smartTagPr", "smartTagTypes",
            "webPublishing", "fileRecoveryPr", "webPublishObjects", "extLst",
        )}
        expected = next((index for index, child in enumerate(current) if child.tag in later_tags), len(current))
        if output.index(settings) != expected:
            raise ValueError("New calculation settings are outside their schema position.")
        output.remove(settings)
    _compare_elements_strict(current, output, "workbook outside calculation flags")


def _verify_zip_membership(
    cur_zf: zipfile.ZipFile, out_zf: zipfile.ZipFile, ws_path: str, wb_path: str,
) -> tuple[int, int]:
    before = cur_zf.namelist()
    after = out_zf.namelist()
    if len(before) != len(set(before)) or len(after) != len(set(after)):
        raise ValueError("Workbook ZIP contains duplicate entry names.")
    if set(before) != set(after):
        raise ValueError("Export added, removed, or renamed workbook ZIP entries.")
    unchanged = 0
    for name in before:
        old = _read_normalised(cur_zf, name)
        new = _read_normalised(out_zf, name)
        if old == new:
            unchanged += 1
        elif _normalise_name(name) not in (ws_path, wb_path):
            raise ValueError(f"Unrelated workbook part {name!r} changed.")
    return unchanged, len(before)


def _validate_patches(
    patches: list[dict],
    sheet: str,
    id_column: str,
    editable_columns: list[str],
    cur_data: bytes,
) -> dict[str, dict]:
    from backend.workbook import read_table

    table = read_table(cur_data, sheet, id_column, editable_columns)

    target_map: dict[tuple[str, str], dict] = {}
    id_set: set[str] = set()
    for row in table["rows"]:
        rid = row["id"]
        id_set.add(rid)
        for h, cell in row["cells"].items():
            target_map[(rid, h)] = cell

    if not patches:
        raise ValueError("Patches list is empty; nothing to verify.")

    seen: set[tuple[str, str]] = set()
    addr_to_patch: dict[str, dict] = {}

    for p in patches:
        rid   = p.get("rowId", "")
        field = p.get("field", "")
        pval  = p.get("value") or {}
        kind  = pval.get("kind", "")
        raw   = pval.get("value", "")

        key = (rid, field)
        if key in seen:
            raise ValueError(
                f"Duplicate patch target rowId={rid!r} field={field!r}."
            )
        seen.add(key)

        if rid not in id_set:
            raise ValueError(f"Unknown rowId {rid!r} in patches.")
        if field not in editable_columns:
            raise ValueError(
                f"Field {field!r} is not an editable column "
                f"(rowId={rid!r})."
            )
        cell = target_map.get(key)
        if cell is None:
            raise ValueError(
                f"No cell found for rowId={rid!r} field={field!r}."
            )
        if cell.get("protected"):
            raise ValueError(
                f"Cell for rowId={rid!r} field={field!r} is protected."
            )
        if kind not in ("number", "text"):
            raise ValueError(
                f"Patch kind must be 'number' or 'text'; "
                f"got {kind!r} for rowId={rid!r} field={field!r}."
            )
        if not raw and raw != "0":
            raise ValueError(
                f"Patch value is empty for rowId={rid!r} field={field!r}."
            )
        if kind == "number":
            _bounded_decimal(raw, f"patch for rowId={rid!r} field={field!r}")

        addr = cell["address"].upper()
        if addr in addr_to_patch:
            raise ValueError(
                f"Two patches map to the same cell address {addr!r}."
            )
        addr_to_patch[addr] = p

    return addr_to_patch


def verify_export(
    current: bytes,
    output: bytes,
    sheet: str,
    id_column: str,
    editable_columns: list[str],
    patches: list[dict],
) -> dict:
    current_hash = _sha256(current)
    output_hash  = _sha256(output)

    cur_zf = _open_zip(current)
    out_zf = _open_zip(output)

    cur_wb_path = _find_workbook_part(cur_zf)
    out_wb_path = _find_workbook_part(out_zf)

    if _normalise_name(cur_wb_path) != _normalise_name(out_wb_path):
        raise ValueError(
            f"Workbook part path changed: "
            f"{cur_wb_path!r} → {out_wb_path!r}."
        )
    wb_path = _normalise_name(cur_wb_path)
    wb_dir  = wb_path.rsplit("/", 1)[0] if "/" in wb_path else ""

    cur_wb_bytes = _read_normalised(cur_zf, wb_path)
    cur_wb_root  = _parse_xml(cur_wb_bytes, wb_path)
    cur_wb_rels  = _parse_rels(cur_zf, wb_path)

    sheets_elem = cur_wb_root.find(f"{_SS}sheets")
    if sheets_elem is None:
        raise ValueError("Current workbook contains no <sheets> element.")

    ws_path: str | None = None
    for sh_elem in sheets_elem.iter(f"{_SS}sheet"):
        if sh_elem.get("name", "") == sheet:
            rid     = sh_elem.get(f"{_R}id", "")
            ws_path = cur_wb_rels.get(rid)
            break
    if ws_path is None:
        raise ValueError(f"Sheet {sheet!r} not found in current workbook.")
    ws_path = _normalise_name(ws_path)

    addr_to_patch = _validate_patches(
        patches, sheet, id_column, editable_columns, current
    )

    unchanged_part_count, total_part_count = _verify_zip_membership(
        cur_zf, out_zf, ws_path, wb_path
    )

    out_wb_bytes = _read_normalised(out_zf, wb_path)
    _verify_workbook_xml(cur_wb_bytes, out_wb_bytes, wb_path)

    shared_strings = _load_shared_strings(cur_zf, wb_dir)
    style_lookup   = _build_style_lookup(cur_zf, wb_dir)

    cur_ws_bytes = _read_normalised(cur_zf, ws_path)
    out_ws_bytes = _read_normalised(out_zf, ws_path)
    changed_cell_count, formula_count_in_target_ws = _verify_worksheet(
        cur_ws_bytes,
        out_ws_bytes,
        ws_path,
        addr_to_patch,
        shared_strings,
        style_lookup,
    )

    if changed_cell_count != len(addr_to_patch):
        raise ValueError(
            f"Expected {len(addr_to_patch)} changed cells; "
            f"verifier found {changed_cell_count}."
        )

    preserved_formula_count = formula_count_in_target_ws

    cur_wb_root_all = _parse_xml(cur_wb_bytes, wb_path)
    cur_wb_rels_all = _parse_rels(cur_zf, wb_path)
    sheets_elem_all = cur_wb_root_all.find(f"{_SS}sheets")
    if sheets_elem_all is not None:
        for sh_elem in sheets_elem_all.iter(f"{_SS}sheet"):
            sh_name = sh_elem.get("name", "")
            if sh_name == sheet:
                continue  # already counted
            rid         = sh_elem.get(f"{_R}id", "")
            other_path  = cur_wb_rels_all.get(rid)
            if not other_path:
                raise ValueError(f"Worksheet {sh_name!r} has no package relationship.")
            other_path = _normalise_name(other_path)
            other_bytes = _read_normalised(cur_zf, other_path)
            other_root = _parse_xml(other_bytes, other_path)
            for f_elem in other_root.iter(f"{_SS}f"):
                preserved_formula_count += 1

    return {
        "status":                "passed",
        "changedCellCount":      changed_cell_count,
        "preservedFormulaCount": preserved_formula_count,
        "unchangedPartCount":    unchanged_part_count,
        "totalPartCount":        total_part_count,
        "currentHash":           current_hash,
        "outputHash":            output_hash,
    }
