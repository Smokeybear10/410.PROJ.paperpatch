## User

Read DESIGN.md and .kiro/specs/paperpatch/design.md, then implement backend/workbook.py and backend/merge.py completely according to that contract. This is an authorized new build. Write production source now. Do not stop at a plan. Do not add tests or modify other modules. Root is independently implementing HTTP and UI. Use only repository files.

## Kiro

Let me read both design documents and understand the existing codebase first.

Now let me explore the existing repository structure:

Good. The backend directory is empty. Let me now write the two modules.

Let me do a quick self-review pass to verify correctness of a few tricky details:

The files look good. Let me verify the merge module's key classification flow and check for one edge — the `_stdlib_ET` import in workbook.py is unused (I removed the entity check approach but kept the import):

Also, verify that `_apply_patch_to_cell` sets `t="inlineStr"` correctly — there's a redundant conditional:

Also fix the dead `if False` in `_apply_patch_to_cell` for the numeric path:

Good. Let me do a final check on the key parts of both files to confirm they're clean:

There's already a `backend/paper.py`. Let me verify it's untouched and check if `__init__.py` already existed:

Good — `paper.py` was pre-existing and I haven't touched it. Here's what was implemented:

---

## Implemented

### `backend/workbook.py`

**`inspect_workbook(data: bytes) -> dict`**
- Opens the ZIP with size limits (10 MB compressed, 100 MB expanded, 2000 entries)
- Rejects `.xlsm`/macros, digital signatures, encryption
- Resolves workbook part via `[Content_Types].xml`, then sheet parts via `.rels` files
- Loads shared-string table and style-based format lookup (date, currency detection)
- Parses each worksheet: row-1 headers, data row count (trailing blank rows stripped)
- Skips sheets with duplicate/missing headers; raises if no usable sheet remains
- Returns `{sheets, sha256}`

**`read_table(data, sheet, id_column, editable_columns) -> dict`**
- Re-runs full workbook resolution on CURRENT bytes
- Builds typed row IDs: `text:001` or `number:1` with canonical Decimal normalisation
- Detects and marks protected cells: formulas, merged non-origin cells, array/shared formula ranges
- Validates: 1–2 editable columns distinct from ID, ≤500 rows, unique IDs, no blank IDs
- Returns per-cell `{kind, value, address, protected}` for every header column

**`patch_workbook(data, sheet, id_column, editable_columns, patches) -> bytes`**
- Re-resolves targets from CURRENT (no stale address assumptions)
- Validates all patches before touching bytes: no duplicates, only editable fields, not protected, finite numbers
- Rewrites `<c>` elements in-place: numbers use default numeric type with `<v>`; text uses `t="inlineStr"` with `<is><t>` (leading `=` stays literal, whitespace preserved)
- Sets `calcPr fullCalcOnLoad="1"` in workbook.xml
- Copies all other ZIP entries byte-for-byte unchanged
- Re-opens generated package and parses the patched worksheet XML to validate

### `backend/merge.py`

**`reconcile(base, current, proposals, receipts) -> list[dict]`**
- Validates structural compatibility (sheet, idColumn, editableColumns must match)
- Requires exact ID set equality between base and current; rejects mismatches
- Rejects duplicate proposals targeting the same (rowId, field)
- Classifies each proposal in spec order: missing/protected → invalid/protected; blank → unchanged; receipt replay; P==B → unchanged; P==C → already\_applied; C==B → ready; otherwise conflict
- Number equality via `Decimal`; text exact; blank baseline treated as text `""`

**`approved_patches(items, decisions) -> list[dict]`**
- Rejects unknown item IDs and invalid decision values
- Paper decision on non-ready/non-conflict raises `ValueError`
- Only `"paper"` on `"ready"` or `"conflict"` generates a patch dict
- `"current"` decisions produce no output (keep current)

---

## Limitations

- **No `paper.py` integration**: `inspect_workbook`/`read_table`/`patch_workbook` and `reconcile`/`approved_patches` are pure functions; session management, HTTP binding, and receipts persistence are deferred to `main.py` (Root's responsibility).
- **Date/currency detection**: relies on built-in format ID ranges and format code heuristics; exotic custom formats may misclassify.
- **Array formula range inference**: only handles explicit `ref=` attributes on `<f t="array">` and `<f t="shared">`; older-style implicit shared formula refs are not fully expanded.
- **Style preservation on patch**: the `s=` attribute on `<c>` is preserved, but if the original cell had no `s`, numeric patches may lose any default style the host applied.
- **No formula evaluation**: `calcPr fullCalcOnLoad="1"` instructs Excel to recalculate; the app never evaluates formulas itself, as designed.
- **`.xls` (BIFF) files**: these are not valid ZIPs and will fail at `_open_zip` with a `BadZipFile` error rather than a tailored message; the calling HTTP layer should distinguish this.

## User

Fix these correctness bugs in your modules now: (1) _typed_equal must require identical kinds, except numbers compare via Decimal; text '001' must never equal numeric 1. _cell_to_comparable must preserve actual kinds, not coerce to text. (2) Parse PAPER against the BASELINE cell kind per spec, not current kind, so digital type changes are conflicts. (3) Keep significant whitespace in nonblank text proposals, only use strip to detect blanks and parse numbers. (4) Replay protection must compare against ALL historical receipts for the row+field, not only the last receipt. Say previously exported, not applied, since download does not prove external application. (5) patch_workbook currently walks existing XML cells only; a blank cell omitted from sheet XML can be approved but never written. Insert missing target c elements in the proper row/column order, preserving other XML. Verify every approved target was patched and output values match. (6) Avoid unbounded Decimal int conversion: limit input digits/exponents and use bounded canonical formatting; return ValueError for out-of-range numeric inputs. Do not touch other modules or add tests/docs. Implement these fixes directly; root is doing independent behavioral verification.

## Kiro

Let me read both files carefully before making any changes.

Good — I have both files in full. Now I'll implement all six fixes precisely. Let me work through each one methodically:

**merge.py changes:**
1. `_typed_equal` — require identical kinds; no cross-kind numeric equality
2. `_proposal_to_typed` / `_classify` — parse against BASELINE kind, not current; keep raw whitespace, only strip to detect blank/parse numbers
3. `_cell_to_comparable` — preserve actual kinds, no coercion to text
4. Receipt index — collect ALL receipts per key as a list, check any match
5. Wording: "previously exported" not "applied"
6. Bounded Decimal parsing

**workbook.py changes:**
5. Insert missing `<c>` elements for blank cells; verify every target was patched and values match
6. Bounded Decimal in `_cell_kind_value`, `_apply_patch_to_cell`, and patch validation

Now fix workbook.py — bounded Decimal, blank-cell insertion with proper ordering, and patch verification:

Now fix `_cell_kind_value` to use `_bounded_decimal` instead of raw `Decimal()`:

Now fix patch validation to use `_bounded_decimal`, and then rewrite the patching loop to insert missing cells and verify every target was written. First fix the validation block:

Now read the patching loop section to replace it:

Now replace everything from "Build address → patch map" through the end of the file with the corrected implementation:

Now verify the final files are clean — check for any remaining raw `Decimal(` calls that bypass bounds, and confirm all six fixes are present:

Both remaining `Decimal(` calls are inside the bounded parsing helpers themselves (`_bounded_decimal` and `_parse_finite_decimal`), which enforce the digit/exponent limits before returning — that's correct. Let me also confirm the `int()` path is gone from `_apply_patch_to_cell` and that `_canonical_number` is used everywhere in workbook.py:

The grep is using the pipe as literal, not regex alternation. Let me check each separately:

All helpers are present and wired correctly. Let me do one final check confirming the merge.py fixes — receipt list, `_any_receipt_matches`, baseline-kind parsing, and `_cell_to_comparable`:

All six fixes are in place. Here's a precise accounting of every change made:

---

## Changes made

### merge.py — rewritten in full

**Fix 1 — `_typed_equal` kind identity requirement**
Removed the mixed-kind Decimal comparison branch entirely. Cross-kind pairs (`number` vs `text`, etc.) now return `False` unconditionally. Only `kind=="number"` on both sides uses Decimal. `text:"001"` can never equal `number:1`.

**Fix 1 — `_cell_to_comparable` preserves actual kinds**
The function no longer coerces non-numeric cells to `{"kind":"text"}`. It returns `{"kind": kind, "value": value}` as-is (blank cells return `{"kind":"blank","value":""}` so `_typed_equal` can distinguish blank from empty-string text).  `_classify` calls it without a `target_kind` argument — the kind comes from the cell itself.

**Fix 2 — `_proposal_to_typed` and `_classify` use BASELINE kind**
`_proposal_to_typed` now takes `baseline_kind` (not `current_kind`). `_classify` reads `base_cell.get("kind")` to determine parsing mode. Baselines with non-constraining kinds (`blank`, `formula`, `unsupported`, `boolean`) default to `"text"`. A digital type change (e.g. numeric baseline → text current) is now surfaced as a conflict rather than silently parsed as text.

**Fix 3 — Whitespace preserved in text proposals**
`_proposal_to_typed` only calls `.strip()` to detect blank proposals and to parse numeric candidates. The `"text"` branch returns `{"kind":"text","value":proposal_text}` with the full original text including significant whitespace.

**Fix 4 — Replay uses ALL historical receipts**
Replaced `receipt_index: dict[key, dict]` (last-wins) with `receipt_index: dict[key, list[dict]]`. New helper `_any_receipt_matches(receipts: list[dict], proposed)` iterates every receipt for the key and returns `True` if any matches. `_classify` receives `receipts: list[dict]`. Wording changed from "applied" to "previously exported".

**Fix 6 — `_parse_finite_decimal` bounds check**
After parsing, checks `len(digits) > _MAX_DECIMAL_DIGITS` (28) or `abs(exp) > _MAX_DECIMAL_EXP` (100); returns `None` if exceeded. `_canonical_number` uses `d.to_integral_value(rounding=None)` via Decimal arithmetic — never `int()`.

---

### workbook.py — targeted changes

**Fix 6 — `_bounded_decimal` and `_canonical_number` added**
`_bounded_decimal(raw, label)` enforces 28 significant digits and exponent ≤100; raises `ValueError` with a useful message. `_canonical_number(d)` uses `d.to_integral_value(rounding=None)` for integers, `d.normalize()` otherwise — no `int()` on unbounded values. All three sites that previously called `Decimal()` directly — `_cell_kind_value`, patch validation, and `_apply_patch_to_cell` — now go through these helpers.

**Fix 5 — Insert missing `<c>` elements; verify every target**
The patching loop is now two passes. First pass: patch existing `<c>` elements, record which addresses were hit. Second pass: for addresses not found, call `_insert_row_element` (inserts `<row>` in ascending `r` order) and `_insert_cell_element` (inserts `<c>` in ascending column order within the row), then apply the patch. After both passes, asserts `patched_addrs == set(addr_to_patch)`. Then `_verify_patches` re-reads every patched cell through `_cell_kind_value` and confirms kind and value match the patch exactly (Decimal equality for numbers, exact string equality for text).

## User

Additional independent review found these workbook correctness bugs; fix after current edits: read_table must validate duplicate/nonempty headers itself (current uploads and patching bypass inspect_workbook), otherwise ID,Qty,Qty writes wrong column. Unsupported date/currency/boolean protection is calculated but discarded near old line811; protect/reject all unsupported editable targets on reading and patching. Merged ranges must protect origin as well as subordinate cells. Reject formula/boolean/blank IDs, allow only text or finite numeric ID and canonicalize numeric IDs so 1E+3 and 1000 have same identity. Catch BadZipFile and report ValueError for ordinary invalid uploads; reject duplicate ZIP entry names to avoid ambiguous packages. Recognize OOXML signature part names _xmlsignatures. Keep patch preserving unrelated XML and styles. Do not edit other modules.

## Kiro

Let me read the current workbook.py in full before touching anything.

## User

One final preservation boundary for the same workbook module: do not bypass worksheet protection; reject a selected sheet with active sheetProtection for this first version. Protect cells with cm/vm value metadata and every formula ref range including dataTable refs. If dynamic-array/rich-value metadata cannot be mapped reliably, reject unsupported xl/metadata.xml workbooks explicitly rather than claiming support. When adding missing calcPr, insert it in valid OOXML workbook child order (before oleSize/customWorkbookViews/pivotCaches/smartTag*/web*/fileRecoveryPr/webPublishObjects/extLst), and set calcMode auto, forceFullCalc, fullCalcOnLoad so a manually calculated workbook updates on open. Keep work limited to current modules; finish with a concise summary.

## Kiro

Let me read the remaining unread sections of workbook.py before making any changes.

Good — I have the complete file. Now I'll implement every fix as a set of precise targeted replacements. Let me work through them one by one.

**Fix 1: `_open_zip` — catch `BadZipFile`, reject duplicate entry names**