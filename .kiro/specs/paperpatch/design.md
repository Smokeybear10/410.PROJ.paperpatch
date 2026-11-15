# Backend contract

Implement Python 3.12 `backend/workbook.py` and `backend/merge.py`, using standard library and lxml. Never round-trip with a spreadsheet writer. Raise ValueError with useful messages. Root independently verifies; do not add tests/docs.

## workbook.py

`inspect_workbook(data: bytes) -> dict`: `{"sheets": [{"name": str, "headers": [str], "rows": int}], "sha256": str}`. Headers in row 1. Ignore wholly blank trailing rows. ZIP limits 10MB input, 100MB expanded, 2000 entries. Reject macros/signatures/encrypted/unsupported files. XML entities/network disabled. Resolve workbook/sheet relationship paths, shared/inline strings, numbers, booleans, blanks and formulas. Unique nonempty headers for selected table. Skip unsupported sheets if other valid sheets exist; clear error if none.

`read_table(data: bytes, sheet: str, id_column: str, editable_columns: list[str]) -> dict`: `{"sheet": str, "idColumn": str, "editableColumns": [str], "headers": [str], "rows": [{"id": str, "label": str, "cells": {header: {"kind": "number"|"text"|"blank"|"formula"|"boolean"|"unsupported", "value": str, "address": str, "protected": bool}}}], "sha256": str}`. ID uses type prefix (`text:001`, `number:1`), canonical numeric normalization; label is human-readable ID. Exact string IDs. Reject duplicate/missing IDs, >500 data rows, invalid editable fields. Require 1-2 fields distinct from ID. Dates/currency/complex editable types unsupported; formula cells visible but protected. Merged targets and array/shared formula ranges protected. Infer ranges for formula ref attributes; do not patch protected ranges. Header name mapping and row ID mapping tolerate sorting. Formula `value` is expression, not cached value.

`patch_workbook(data: bytes, sheet: str, id_column: str, editable_columns: list[str], patches: list[dict]) -> bytes`: each patch `{"rowId": str, "field": str, "value": {"kind": "number"|"text", "value": str}}`. Re-resolve targets from CURRENT. Reject noneditable/protected/missing/duplicate targets and empty values. Preserve styles, unrelated attributes and worksheet elements. Text uses inlineStr and significant whitespace; leading `=` stays literal. Numbers finite Decimal. Copy other ZIP entries' contents unchanged; only selected worksheet and workbook recalculation flags may change. Never extract ZIP to disk. Reopen generated package to validate.

## merge.py

`reconcile(base: dict, current: dict, proposals: list[dict], receipts: list[dict]) -> list[dict]`: validate matching sheet/ID/fields and exact ID sets even when proposals empty. Proposal: `{"id": str, "rowId": str, "field": str, "text": str, "cropUrl": str|null, "source": "ocr"|"manual"|"demo"}`. Whitespace-only proposal means no instruction. Numeric target parses finite Decimal; text exact. Baseline blank targets treated as text. Output each proposal plus `baseline`, `current` cell dicts, `proposed` typed value or null, `status`, `reason`, `rowLabel`.

Status: `ready`, `conflict`, `unchanged`, `already_applied`, `protected`, `invalid`, `replayed`. Duplicate target proposals are invalid, never last-wins. Order: missing/protected => blocked; blank => unchanged; accepted receipt match => replayed; P==B => unchanged; P==C => already_applied; C==B => ready; otherwise conflict. Receipt: `{ "rowId": str, "field": str, "value": {"kind": str,"value": str} }` prevents already accepted paper text overwriting newer digital edits. Typed number equality Decimal; text exact.

`approved_patches(items: list[dict], decisions: dict[str,str]) -> list[dict]`: only explicit `paper` for ready/conflict generates patch. `current` keeps current. Reject invalid decisions/unknown IDs or paper decisions on other statuses. HTTP handles all-items-resolution and current hash binding.

Straightforward functions and type hints. May create backend/__init__.py. Work only inside this repository.
