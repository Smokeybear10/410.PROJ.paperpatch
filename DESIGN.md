# PaperPatch

Turn handwritten corrections into reviewed Excel updates without losing changes made since printing.

## Premises

- Audience: people maintaining small equipment and inventory registers.
- A stable unique ID identifies each row across sorting and editing.
- Paper contains numeric or short text corrections in dedicated boxes.
- The original workbook is the printed baseline; the latest workbook is the export base.
- Recognition proposes values. A person approves every write.
- Blank boxes mean no instruction, never zero or deletion.
- Formula cells cannot be edited through this application.
- First prove a complete local round trip; actual handwriting reliability remains unmeasured.
- Kiro must implement substantive production logic, with authentic session evidence.

## Hot-path budgets

Targets, not measured results.

| Path | Target | Bound |
|---|---|---|
| Open workbook | 2 seconds | 10 MB compressed; 100 MB expanded; 500 rows |
| Generate paper | 2 seconds | Two editable fields |
| Process photograph | 60 seconds warm | One page; serialized inference |
| Reconcile | 500 ms | One print run and current workbook |
| Export | 2 seconds | Targeted ZIP/XML changes |

## Degradation matrix

| Condition | Behavior |
|---|---|
| No JavaScript | Explain that the review application requires JavaScript |
| No authentication | Normal local operation |
| No network | All functions work after the OCR model is downloaded |
| OCR unavailable | Preserve crops and offer explicitly labeled manual transcription |
| Missing anchors or wrong copy | Reject alignment with a retake instruction |
| Missing/duplicate/changed IDs | Block reconciliation/export |
| Formula introduced after print | Protect current cell |
| Server restart | Restore sessions; interrupted OCR may be retried |

## State map

| State | Owner | Persistence |
|---|---|---|
| Baseline, current and output workbooks | Service | Local session directory |
| Table configuration and immutable manifest | Service | Session JSON |
| Scans, crops, proposal receipts | Service | Session directory |
| Reviewer decisions | Browser until apply | Invalidated by workbook change |
| Active session | Browser URL | Session ID query parameter |

## Journey

1. Upload `.xlsx` or start the labeled demo.
2. Select worksheet, stable ID column and one or two editable fields.
3. Download the fixed-geometry PDF, print and write corrections.
4. Photograph the entire page and upload it.
5. Upload the latest workbook, including independent digital changes.
6. Review crops beside printed, current and proposed values.
7. Resolve conflicts explicitly; approve corrections.
8. Download the patched current workbook and see the change receipt.

## Table contract

- One selected sheet with unique nonempty headers in row 1.
- At most 500 populated rows and 100 columns; stable unique scalar IDs.
- ID types and values remain exact; do not collapse text `001` into numeric `1`.
- Rows can be reordered but not inserted/deleted in this version.
- One or two editable numeric/plain-text fields, distinct from ID.
- Formula, array/spill and merged cells are protected.
- Unsupported date/currency/error cell types cannot be edited.
- Reject `.xls`, `.xlsm`, encrypted and signed workbooks.
- Never modify the source file in place.

## Reconciliation

| Condition | Outcome |
|---|---|
| Blank paper | Keep current |
| Paper equals printed | No paper change; keep current |
| Paper equals current | Already matches; no write |
| Current equals printed | Eligible for explicit approval |
| All three differ | Conflict; explicitly choose current or paper |
| Current formula/protected target | Never write |
| Accepted proposal seen again | Do not overwrite a subsequent digital edit |

Compare numbers with Decimal and text exactly. Successful exports become the local working workbook so later pages retain earlier approved edits. The immutable printed baseline stays separate. Bind review/export to current workbook SHA-256. New uploads invalidate decisions. Repeat all validation at export. Clearing cells is out of scope.

## Interface

- Quiet warm paper, charcoal type and restrained green/orange accent.
- Workflow: workbook, paper, reconcile.
- Current filename and revision always visible during review.
- Counts for pending, conflicts and protected values.
- Change list with row ID, field and status.
- Selected item shows its source crop at useful size.
- Printed / Current Excel / Paper values appear together.
- Transcription is editable independently of approval.
- Conflicts have text and amber treatment; never rely only on color.
- Formula protection has an explanation and no override.
- Export reports how many approved changes it contains.
- No confidence percentages presented as accuracy probabilities.
- No fabricated statistics, activity or recognition evidence.
- Keyboard access, visible focus, responsive stacked mobile layout.
- No Halloween decoration; visual identity follows the function.
- Eight HTML studies explore layout without changing the safety model.

## Technical shape

- React/TypeScript/Vite/Tailwind frontend.
- FastAPI serves API and built UI at localhost:7171.
- Python ZIP/XML patching preserves untouched workbook package parts.
- ReportLab fixed PDF; OpenCV alignment/cropping; local TrOCR handwriting model.
- Sessions persist under ignored `.data`; no cloud accounts or paid OCR.
- Source image uploads bounded by bytes/pixels; OCR serialized.

## Preservation contract

Use CURRENT bytes as the export base. Resolve sheet relationships. Patch only approved cell values and workbook recalculation flags. Preserve styles, formula definitions and unrelated worksheet elements. Other ZIP entries retain identical uncompressed contents. Inline strings avoid changing shared strings; `=foo` remains literal text. Excel recalculates on opening; the app does not claim to evaluate formulas. Actual Excel compatibility needs an Excel open check.

## Acceptance evidence

- Independent digital changes survive.
- Conflicts require explicit choice.
- Sorting preserves identity mapping.
- Formulas introduced after print stay protected.
- Duplicate scans and accepted proposals cannot repeat stale writes.
- Inspect downloaded ZIP parts and values independently.
- Separate generated demo input from actual handwritten photographs.
- Measure a real print/write/photo cycle before claiming recognition accuracy.
- Record genuine Kiro execution and inspect its production changes.

## Scope reconciliation

This retains the approved paper-to-current-Excel correction concept. The current deliverable is a local proof. Public hosting/submission require later authorization. No demand validation or handwriting accuracy has been asserted.

## Out of scope

Arbitrary Excel print layouts, freeform/multiline notes, row insertion/deletion, ID edits, formulas/macros, cloud sync, multi-user accounts, automatic approvals, deployment, purchases and GitHub publishing.
