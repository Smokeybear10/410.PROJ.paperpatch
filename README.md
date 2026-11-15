# PaperPatch | Paper-to-Excel review

Bring handwritten corrections back to Excel while preserving changes made after the sheet was printed.

## Quick Start

Requires Python 3.12, Node.js 22+, npm, and uv. Run from this directory:

```sh
uv sync
npm ci
npm run setup:ocr
npm start
```

Open http://localhost:7171.

Initial setup downloads dependencies and the TrOCR model. Recognition runs locally afterward. Workbooks, photographs, and review history are stored in `.data/sessions/`.

## What It Does

- Print a workbook snapshot with stable row IDs, correction boxes, and a page QR code.
- Upload page photographs and the latest workbook. Inspect each reading against its crop, resolve competing paper values, and check every page for missed writing.
- Preview approved cell changes, then inspect an export receipt with the exact input, output, and JSON audit record. Rows are matched by ID even when Excel has been sorted.
- Verify each exported file before saving it: approved target values must match, other cells and formulas must remain intact, and unrelated workbook archive parts must be byte-identical. Failed checks stop the export without changing the current workbook.

**Open example** loads prepared readings to demonstrate conflict review and export. It does not demonstrate handwriting recognition.

**Try with your own handwriting** starts with blank correction boxes and no readings. Print the supplied PDF, write your corrections, transfer the photographs to this computer, and upload them with the supplied latest workbook. Follow the two-copy instructions to create a paper disagreement and an independent Excel edit.

## Scope

- Ordinary `.xlsx` files up to 10 MB, headers in row 1, at most 500 data rows, and unique text or numeric IDs. Baseline and latest workbooks must contain the same IDs.
- One or two editable plain-text or number columns. Blank paper boxes mean no change. Formulas, merged cells, dates, currency formats, and other unsupported targets are protected.
- JPEG, PNG, or WebP photographs up to 20 MB and 25 megapixels. All four corner markers and the correct print-run QR must be readable; rotated and mirrored captures are supported.
- Formula expressions and unrelated workbook content are preserved. The app does not calculate formulas; exports request recalculation when opened in Excel.
- Local computer use only. The server binds to loopback; phone photographs must be transferred to that computer.

OCR can misread or miss writing. A physically printed, pen-written, phone-photographed round trip has not yet been validated; generated-page and isolated handwritten-digit checks are not a substitute.

## Tech Stack

| Layer | Tools |
|-------|-------|
| Interface | React, TypeScript, Vite, Tailwind |
| Local API and workbook edits | FastAPI, lxml, targeted OOXML patches |
| Paper and recognition | ReportLab, PyMuPDF, OpenCV, local TrOCR |

## Kiro

Kiro implemented the workbook parsing, preservation-oriented patching, and three-way merge modules from the [spec](.kiro/specs/paperpatch/design.md), then addressed independent correctness findings. The [Kiro session record](.kiro/evidence/workbook-merge-session.md) documents that work. Codex implemented capture, OCR integration, the API, and the interface.

Kiro also authored the first export verifier. Its [initial implementation record](.kiro/evidence/export-verifier-session.txt) describes that draft and its limitations; it is not a record of the final hardened source. Independent review replaced permissive XML comparisons with exact structure and text checks, then checked tampered files and failure-before-save behavior.

Built by Thomas Ou
