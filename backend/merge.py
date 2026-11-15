"""
backend/merge.py

Three-way reconciliation between baseline (printed), current (latest workbook)
and paper proposals (OCR or manual).  Pure functions; no I/O.

Dependencies: standard library only.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

# ---------------------------------------------------------------------------
# Bounded numeric parsing
# ---------------------------------------------------------------------------

# Guard against pathological inputs that cause Decimal to allocate huge
# coefficient arrays or trigger near-infinite loops in int().
_MAX_DECIMAL_DIGITS = 28   # fits in a standard 128-bit decimal context
_MAX_DECIMAL_EXP    = 100  # absolute value of exponent


def _parse_finite_decimal(s: str) -> Decimal | None:
    """
    Return a Decimal if *s* is a valid finite number string, else None.

    Rejects values whose coefficient digit count or absolute exponent exceed
    the module bounds, preventing unbounded memory allocation.
    """
    text = s.strip()
    if not text:
        return None
    try:
        d = Decimal(text)
    except InvalidOperation:
        return None
    if not d.is_finite():
        return None
    sign, digits, exp = d.as_tuple()
    if len(digits) > _MAX_DECIMAL_DIGITS:
        return None
    if abs(exp) > _MAX_DECIMAL_EXP:
        return None
    return d


def _bounded_decimal(raw: str, label: str) -> Decimal:
    """
    Parse *raw* as a bounded finite Decimal or raise ValueError.

    Used for patch validation and proposal parsing wherever the value will
    eventually be written to the workbook or compared against cell values.
    """
    d = _parse_finite_decimal(raw)
    if d is None:
        raise ValueError(
            f"Value {raw!r} for {label} is not a valid finite number, "
            f"or exceeds the allowed digit/exponent range "
            f"({_MAX_DECIMAL_DIGITS} digits, exponent ±{_MAX_DECIMAL_EXP})."
        )
    return d


def _canonical_number(d: Decimal) -> str:
    """
    Return a canonical string for a finite Decimal, without calling int()
    on arbitrarily large values.

    For values whose exponent is non-negative (integer-valued), use
    Decimal.to_integral_value() expressed via Decimal string arithmetic
    rather than Python int(), which could be unbounded.  Since we enforce
    _MAX_DECIMAL_DIGITS and _MAX_DECIMAL_EXP at parse time the coefficient
    is at most 28 digits, so the resulting string is bounded.
    """
    if d == d.to_integral_value():
        # Remove the fractional part and any trailing exponent notation.
        # quantize to 1 (i.e. Decimal('1')) keeps it as an integer Decimal.
        return str(d.to_integral_value(rounding=None))
    return str(d.normalize())


# ---------------------------------------------------------------------------
# Equality helpers
# ---------------------------------------------------------------------------

def _typed_equal(a: dict, b: dict) -> bool:
    """
    Compare two typed value dicts {kind, value} for semantic equality.

    Rules:
    - blank == blank → True
    - blank vs non-blank → False
    - Both kind=="number" → Decimal comparison (1.0 == 1)
    - Any other combination → kinds must be identical AND values equal as strings
      (text "001" is never equal to number 1)
    """
    ka, va = a.get("kind", "blank"), a.get("value", "")
    kb, vb = b.get("kind", "blank"), b.get("value", "")

    if ka == "blank" and kb == "blank":
        return True
    if ka == "blank" or kb == "blank":
        return False

    # Both are explicitly numeric → compare via Decimal
    if ka == "number" and kb == "number":
        da = _parse_finite_decimal(va)
        db = _parse_finite_decimal(vb)
        if da is not None and db is not None:
            return da == db
        # Unparseable numeric cell values: fall back to string
        return va == vb

    # Mixed kinds (number vs text, text vs boolean, etc.) → never equal
    if ka != kb:
        return False

    # Same non-numeric kind → exact string equality
    return va == vb


# ---------------------------------------------------------------------------
# Proposal parsing
# ---------------------------------------------------------------------------

def _proposal_to_typed(proposal_text: str, baseline_kind: str) -> dict | None:
    """
    Convert OCR/manual proposal text to a typed value dict.

    *baseline_kind* is the kind of the **baseline** (printed) cell, not the
    current cell, so that a digital type change is surfaced as a conflict
    rather than silently adopting the new type.

    Returns {"kind": ..., "value": ...} or None when the proposal is
    blank/whitespace-only (no instruction).

    Whitespace is preserved in the returned value; only *strip()* is used
    to detect blank proposals and to parse numeric candidates.

    Raises ValueError for out-of-range numeric inputs.
    """
    # Blank detection uses strip(); the raw text is kept for text values.
    if not proposal_text.strip():
        return None  # blank → no instruction

    if baseline_kind == "number":
        # Strip only for numeric parsing; non-numeric text in a numeric field
        # is returned as text so the caller can classify it as invalid.
        d = _parse_finite_decimal(proposal_text.strip())
        if d is None:
            # Could not parse as number → return text kind; caller marks invalid
            return {"kind": "text", "value": proposal_text}
        # Validate bounds (parse already checked, but be explicit)
        return {"kind": "number", "value": _canonical_number(d)}

    # text, blank-baseline, boolean, formula, unsupported → treat as text,
    # preserving all significant whitespace.
    return {"kind": "text", "value": proposal_text}


# ---------------------------------------------------------------------------
# Receipt helpers
# ---------------------------------------------------------------------------

def _receipt_key(r: dict) -> tuple[str, str]:
    return (r.get("rowId", ""), r.get("field", ""))


def _any_receipt_matches(receipts: list[dict], proposed: dict | None) -> bool:
    """
    Return True if *any* receipt in the list for a given (rowId, field) has
    a value that matches *proposed*.

    Checks ALL historical receipts, not only the most recent one, so that a
    paper value cannot be re-applied after it was previously exported even if
    further exports have occurred since.
    """
    if proposed is None or not receipts:
        return False
    for receipt in receipts:
        rv   = receipt.get("value", {})
        rk   = rv.get("kind", "")
        rval = rv.get("value", "")
        if _typed_equal({"kind": rk, "value": rval}, proposed):
            return True
    return False


# ---------------------------------------------------------------------------
# Public API – reconcile
# ---------------------------------------------------------------------------

def reconcile(
    base: dict,
    current: dict,
    proposals: list[dict],
    receipts: list[dict],
) -> list[dict]:
    """
    Three-way merge of baseline, current workbook and paper proposals.

    Parameters
    ----------
    base, current
        Return values of ``workbook.read_table`` for the baseline and current
        workbooks.  Must represent the same sheet/ID/field configuration.
    proposals
        List of proposal dicts::

            {
                "id": str,              # unique proposal id
                "rowId": str,           # typed row ID ("text:..." or "number:...")
                "field": str,
                "text": str,            # raw OCR/manual text
                "cropUrl": str | null,
                "source": "ocr" | "manual" | "demo",
            }

    receipts
        Previously exported patches::

            {"rowId": str, "field": str, "value": {"kind": str, "value": str}}

    Returns
    -------
    list of item dicts, one per proposal, each containing:

        {
            "id": str,
            "rowId": str,
            "field": str,
            "text": str,
            "cropUrl": str | null,
            "source": str,
            "rowLabel": str,
            "baseline": cell dict,
            "current": cell dict,
            "proposed": {"kind": str, "value": str} | null,
            "status": str,
            "reason": str,
        }

    Raises ValueError for structural mismatches or duplicate proposals.
    """
    # --- Structural validation -----------------------------------------------
    _validate_table_compat(base, current)

    # Build lookup: rowId → {field → cell} for base and current
    base_map    = _build_cell_map(base)
    current_map = _build_cell_map(current)
    id_to_label = {row["id"]: row["label"] for row in current["rows"]}

    base_ids    = set(base_map)
    current_ids = set(current_map)
    if base_ids != current_ids:
        missing = base_ids - current_ids
        extra   = current_ids - base_ids
        parts: list[str] = []
        if missing:
            parts.append(f"IDs in baseline but not current: {sorted(missing)}")
        if extra:
            parts.append(f"IDs in current but not baseline: {sorted(extra)}")
        raise ValueError(
            "Baseline and current workbooks have different row ID sets. "
            + "; ".join(parts)
        )

    editable_fields = set(current["editableColumns"])

    # --- Validate proposals: no duplicate targets ----------------------------
    seen_proposal_targets: dict[tuple[str, str], str] = {}  # key → proposal id
    for prop in proposals:
        key = (prop.get("rowId", ""), prop.get("field", ""))
        pid = prop.get("id", "")
        if key in seen_proposal_targets:
            raise ValueError(
                f"Duplicate proposals for target rowId={key[0]!r} "
                f"field={key[1]!r}: ids {seen_proposal_targets[key]!r} "
                f"and {pid!r}."
            )
        seen_proposal_targets[key] = pid

    # --- Build receipt index: (rowId, field) → list[receipt] -----------------
    # Collect ALL receipts per key; replay check examines the entire history.
    receipt_index: dict[tuple[str, str], list[dict]] = {}
    for r in receipts:
        rk = _receipt_key(r)
        receipt_index.setdefault(rk, []).append(r)

    # --- Process each proposal -----------------------------------------------
    items: list[dict] = []
    for prop in proposals:
        row_id  = prop.get("rowId", "")
        field   = prop.get("field", "")
        text    = prop.get("text", "")
        prop_id = prop.get("id", "")

        base_cell    = base_map.get(row_id, {}).get(field)
        current_cell = current_map.get(row_id, {}).get(field)
        row_label    = id_to_label.get(row_id, row_id)

        status, reason, proposed = _classify(
            row_id          = row_id,
            field           = field,
            text            = text,
            base_cell       = base_cell,
            current_cell    = current_cell,
            editable_fields = editable_fields,
            receipts        = receipt_index.get((row_id, field), []),
        )

        items.append({
            "id":       prop_id,
            "rowId":    row_id,
            "field":    field,
            "text":     text,
            "cropUrl":  prop.get("cropUrl"),
            "source":   prop.get("source", "manual"),
            "rowLabel": row_label,
            "baseline": (
                base_cell
                or {"kind": "blank", "value": "", "address": "", "protected": False}
            ),
            "current": (
                current_cell
                or {"kind": "blank", "value": "", "address": "", "protected": False}
            ),
            "proposed": proposed,
            "status":   status,
            "reason":   reason,
        })

    return items


def _validate_table_compat(base: dict, current: dict) -> None:
    """Raise ValueError if the two tables have incompatible configuration."""
    if base["sheet"] != current["sheet"]:
        raise ValueError(
            f"Sheet mismatch: baseline={base['sheet']!r}, "
            f"current={current['sheet']!r}."
        )
    if base["idColumn"] != current["idColumn"]:
        raise ValueError(
            f"ID column mismatch: baseline={base['idColumn']!r}, "
            f"current={current['idColumn']!r}."
        )
    if sorted(base["editableColumns"]) != sorted(current["editableColumns"]):
        raise ValueError(
            f"Editable column mismatch: "
            f"baseline={sorted(base['editableColumns'])}, "
            f"current={sorted(current['editableColumns'])}."
        )


def _build_cell_map(table: dict) -> dict[str, dict[str, dict]]:
    """Return {rowId: {field: cell_dict}} from a read_table result."""
    m: dict[str, dict[str, dict]] = {}
    for row in table["rows"]:
        m[row["id"]] = dict(row["cells"])
    return m


def _classify(
    *,
    row_id: str,
    field: str,
    text: str,
    base_cell: dict | None,
    current_cell: dict | None,
    editable_fields: set[str],
    receipts: list[dict],
) -> tuple[str, str, dict | None]:
    """
    Classify a single proposal.

    Returns (status, reason, proposed_value_or_None).

    Decision order (matches spec):
        1. Missing row/field → invalid
        2. Field not editable → invalid
        3. Protected current cell → protected
        4. Blank proposal text → unchanged
        5. Any historical receipt matches proposal → replayed
        6. P == B (paper == baseline) → unchanged
        7. P == C (paper == current) → already_applied
        8. C == B (current == baseline) → ready
        9. All three differ → conflict
    """
    if current_cell is None or base_cell is None:
        return (
            "invalid",
            f"Row {row_id!r} field {field!r} not found in table.",
            None,
        )

    if field not in editable_fields:
        return (
            "invalid",
            f"Field {field!r} is not an editable column.",
            None,
        )

    if current_cell.get("protected", False):
        return (
            "protected",
            "Target cell is protected (formula, merged, or array formula).",
            None,
        )

    # Blank detection: strip only to check emptiness.
    if not text.strip():
        return (
            "unchanged",
            "Proposal is blank; no correction indicated.",
            None,
        )

    # Parse the proposal against the BASELINE cell kind, not the current kind.
    # If the baseline was numeric and someone digitally changed it to text,
    # the paper correction should be classified as a conflict, not silently
    # adopt the new type.
    baseline_kind = base_cell.get("kind", "blank")
    if baseline_kind in ("blank", "formula", "unsupported", "boolean"):
        # Baseline did not constrain the type; treat proposal as text.
        baseline_kind = "text"

    try:
        proposed = _proposal_to_typed(text, baseline_kind)
    except ValueError as exc:
        return (
            "invalid",
            str(exc),
            None,
        )

    if proposed is None:
        # Whitespace-only after strip (defensive; already caught above).
        return (
            "unchanged",
            "Proposal is blank; no correction indicated.",
            None,
        )

    # Numeric baseline field but proposal did not parse as a number → invalid.
    if baseline_kind == "number" and proposed["kind"] != "number":
        return (
            "invalid",
            f"Cannot parse {text!r} as a number for numeric field {field!r}.",
            proposed,
        )

    # Replay check: compare against ALL historical receipts for this target.
    if _any_receipt_matches(receipts, proposed):
        return (
            "replayed",
            "This paper value was previously exported; "
            "re-applying it would overwrite any subsequent digital edits.",
            proposed,
        )

    # Build comparable forms for P==B and P==C tests.
    # _cell_to_comparable preserves the actual kind; no coercion to text.
    base_cmp    = _cell_to_comparable(base_cell)
    current_cmp = _cell_to_comparable(current_cell)

    # P == B
    if _typed_equal(proposed, base_cmp):
        return (
            "unchanged",
            "Paper value matches the baseline (printed) value; no change needed.",
            proposed,
        )

    # P == C
    if _typed_equal(proposed, current_cmp):
        return (
            "already_applied",
            "Current workbook already has the proposed value.",
            proposed,
        )

    # C == B
    if _typed_equal(current_cmp, base_cmp):
        return (
            "ready",
            "No independent digital change; paper correction can be applied.",
            proposed,
        )

    # All three differ
    return (
        "conflict",
        "Current workbook has an independent digital change that differs "
        "from both the baseline and the paper value.",
        proposed,
    )


def _cell_to_comparable(cell: dict) -> dict:
    """
    Return a typed value dict for comparison.

    Preserves the actual kind from the cell dict rather than coercing to text.
    Blank cells are returned as {"kind": "blank", "value": ""} so that
    _typed_equal can correctly distinguish blank from empty-string text.
    """
    kind  = cell.get("kind", "blank")
    value = cell.get("value", "")
    if kind == "blank":
        return {"kind": "blank", "value": ""}
    # Return the kind and value as-is; _typed_equal handles the comparison rules.
    return {"kind": kind, "value": value}


# ---------------------------------------------------------------------------
# Public API – approved_patches
# ---------------------------------------------------------------------------

def approved_patches(
    items: list[dict],
    decisions: dict[str, str],
) -> list[dict]:
    """
    Convert reviewer decisions into a list of patch dicts suitable for
    ``workbook.patch_workbook``.

    Parameters
    ----------
    items
        Output of ``reconcile``.
    decisions
        {item_id: "paper"|"current"} — only explicit decisions are accepted.

    Returns
    -------
    list of patch dicts::

        {"rowId": str, "field": str, "value": {"kind": str, "value": str}}

    Only items with decision ``"paper"`` and status ``"ready"`` or
    ``"conflict"`` generate a patch.  ``"current"`` decisions produce no
    patch (keep current workbook value as-is).

    Raises ValueError for:
    - Unknown item IDs in decisions
    - Paper decision on a non-ready/non-conflict status
    - Decision values other than "paper" or "current"
    """
    item_index: dict[str, dict] = {it["id"]: it for it in items}

    unknown = set(decisions) - set(item_index)
    if unknown:
        raise ValueError(
            f"Decisions reference unknown item IDs: {sorted(unknown)}."
        )

    patches: list[dict] = []

    for item_id, decision in decisions.items():
        if decision not in ("paper", "current"):
            raise ValueError(
                f"Invalid decision {decision!r} for item {item_id!r}. "
                "Must be 'paper' or 'current'."
            )

        item   = item_index[item_id]
        status = item["status"]

        if decision == "paper":
            if status not in ("ready", "conflict"):
                raise ValueError(
                    f"Cannot apply paper decision to item {item_id!r} "
                    f"with status {status!r}. "
                    "Paper decisions are only valid for 'ready' or 'conflict' items."
                )
            proposed = item.get("proposed")
            if proposed is None:
                raise ValueError(
                    f"Item {item_id!r} has no proposed value; "
                    "cannot generate patch."
                )
            patches.append({
                "rowId": item["rowId"],
                "field": item["field"],
                "value": {
                    "kind":  proposed["kind"],
                    "value": proposed["value"],
                },
            })
        # decision == "current": no patch generated

    return patches
