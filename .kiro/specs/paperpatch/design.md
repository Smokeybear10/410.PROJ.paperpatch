# PaperPatch design

The default application is a local Word review workspace. This document describes the current implementation. Kiro's original implementation sessions are retained in `../../evidence/`; later hardening and interface work were completed by Codex.

## Data flow

1. `src/main.tsx` selects `DocumentApp` by default. `?mode=workbooks` retains the earlier workbook interface.
2. `backend/documents.py` inspects the uploaded Word draft, stores BASE and CURRENT independently, and creates a review session under `.data/documents/<id>/`.
3. `backend/document_capture.py` generates a reflowed review PDF and a manifest of paragraph/word spans, page geometry, markers, and QR identity. A photo is aligned to that manifest before ink is matched to text.
4. Capture identifies crossed-out spans, associates replacement ink conservatively, and retains unexplained marks. Shared ink is not assigned to multiple corrections as independent evidence. Ambiguous regions require manual interpretation.
5. `backend/document_ocr.py` runs pinned PP-OCRv6 medium and small ONNX recognizers locally. The medium reading is primary; at most one distinct small-model alternative is offered for human review. Missing local models do not trigger an unannounced cloud reader.
6. The editor presents printed text, latest Word text, the paper reading, source crop, and a contextual result preview. The server owns saved decisions; local reading drafts do not override a previously saved decision in the preview.
7. Export applies selected corrections to CURRENT, validates preservation, and writes tracked Word, clean Word, exact input, and JSON audit files.

## Document engine

`backend/docx_engine.py` reads and patches the OOXML archive directly. It does not rebuild a document through a high-level Word writer.

- Validate ZIP paths, package type, member count, compressed/expanded sizes, and XML. Reject macros, signatures, encryption, duplicate paths, inconsistent main-document relationships, and malformed input.
- Resolve the main document part. Enumerate body paragraphs with Unicode code-point offsets and explicit editability guards.
- Match BASE passages to CURRENT through exact text and conservative bidirectional similarity checks. Repeated or ambiguous passages, uncertain boundaries, and unsupported content remain blocked rather than guessed.
- Keep a digital/paper conflict visible when CURRENT also changed the target span. Competing paper corrections cannot be exported over the same text.
- Split text runs only where required, retaining run formatting. Write native `w:ins` and `w:del` with unique revision IDs and reviewer attribution. Leave other archive parts unchanged.
- Verify that rejecting the new revisions restores original text and formatting, accepting them produces the approved text, untouched paragraphs match, and structure outside edited paragraphs is preserved.
- Build the clean copy by accepting only revision IDs created by this export. Existing protected revisions remain intact. Reopen and verify the resulting text and preserved structure.

## Review state and export gates

Session mutations require the latest revision number. CURRENT updates retain BASE, rematch paper edits, clear affected approvals, and invalidate prior export state. Hash binding prevents exporting against a different current file.

Each capture has its own reviewer, source image, page identity, proposed edits, detected marks, and page-inspection state. Each mark must be linked to a correction or explicitly ignored. A page cannot be marked checked while unresolved marks remain.

Export requires capture to be idle, all source pages inspected, all marks resolved, every correction decided, and every selected reading confirmed. Missing/protected targets, unresolved matching conflicts, and overlapping corrections stop export. No file is returned as verified solely because OCR succeeded.

## Prepared and real-pen examples

`backend/document_example.py` creates two prepared paper reviewers and a newer Word draft. It exercises the 24/30/18 conflict and preserves unrelated newer text. Its marks and readings are explicitly labeled as prepared.

The real-pen exercise generates six requested corrections without prepared proposals, records raw capture outcomes, and keeps user-reported physical provenance distinct from independently verified provenance. Manual corrections do not retroactively become OCR successes.

## Workbook mode

`backend/workbook.py` reads supported tables using exact typed row IDs and patches selected worksheet cells without a spreadsheet-writer round trip. `backend/merge.py` compares BASE, CURRENT, and PAPER, exposes conflicts, and prevents accepted paper values from being replayed over newer digital edits. `backend/verification.py` checks the resulting archive against the approved cell changes. Workbook sessions and exports remain under `.data/sessions/`.

## Runtime boundary

React/Vite serves the interface; FastAPI manages local sessions and files. OCR model downloads are an explicit setup step, pinned and hash-checked for Word recognition. The normal server binds to `127.0.0.1:7171`. There is no account isolation or hosted multi-user deployment contract. Fonts and model weights retain their own licenses.
