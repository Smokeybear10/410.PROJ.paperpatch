# PaperPatch | Paper edits back in Word

Review handwritten corrections against the latest Word draft, then export the changes you approve as native Word revisions.

## Quick Start

Requires Python 3.12, Node.js 22+, npm, and [uv](https://docs.astral.sh/uv/getting-started/installation/). Run from this directory:

```sh
uv sync --frozen
npm ci
npm run setup:document-ocr
npm start
```

Open http://localhost:7171.

The OCR setup downloads two pinned PP-OCRv6 models and checks their hashes. Recognition runs locally afterward. You can skip that step to try the prepared example or enter corrections manually. No API key is required. Word documents, photographs, decisions, and exports stay in `.data/documents/`.

## Downloaded Judge Build

A release ZIP includes the compiled interface and runs without Node.js. On macOS or Linux, install [uv](https://docs.astral.sh/uv/getting-started/installation/), extract the archive, and run:

```sh
cd paperpatch
./run-paperpatch.sh
```

The launcher installs the locked Python dependencies and local Word recognition models, then serves http://localhost:7171. Internet access is needed for the initial downloads. For the prepared example or manually entered corrections, use `./run-paperpatch.sh --skip-ocr` to skip model setup. Windows launch has not been validated.

To build that ZIP from committed source:

```sh
npm run package -- --output /path/to/paperpatch.zip
```

The package excludes private review sessions, photographs, credentials, Git history, and development dependencies. It is a local test build; a download link must be published separately for judge access.

## What It Does

**Bring paper edits into a newer draft**

- Open a `.docx` file and print its review PDF. Cross out words with a red or blue pen and write short replacements directly above them.
- Upload photographs of the marked pages and, if it has changed, the latest Word file.
- Compare the printed text, the latest Word text, and the paper reading together. Correct the reading, choose which version to keep, and inspect the sentence preview.
- Resolve competing reviewers' corrections and every detected unexplained pen mark. Add missed corrections manually and check each source page before export.

**Return editable Word files**

- Download a document with native insertions and deletions attributed to each reviewer, a clean copy with this export's changes accepted, the exact input file, and a JSON audit record.
- Preserve unrelated edits in the latest draft. Export checks confirm the approved text, restored text and formatting when revisions are rejected, untouched paragraphs, and unchanged package parts.
- Stop export when a reading is unconfirmed, a source page is unchecked, a mark is unresolved, or corrections overlap. Updating the Word draft rematches corrections and clears approvals whose targets changed.

**Try the working example**

Choose **Open working example**. The printed page says **24 guests**, the newer Word draft says **30**, and a paper reviewer asks for **18**. Review the competing edits, keep the intended version, and choose **Return to Word → Create Word files**. The newer draft's unrelated changes, including the 9:30 last-entry time, remain in the export.

The example uses illustrative pen marks and prepared readings. It demonstrates review, merging, and export; it does not run handwriting recognition. **Try a real-pen check** starts a separate six-correction exercise with no prepared readings and reports detection and reading results for the uploaded page.

## Scope

- Ordinary `.docx` files up to 10 MB, no more than 60 body paragraphs, and at most eight generated review pages. Changes stay within one paragraph; each replacement is limited to 2,000 characters.
- Editable passages are ordinary top-level body paragraphs containing text runs. Tables, fields, hyperlinks, existing tracked changes, hidden text, tabs, and other complex structures are protected. Protected passages can appear for context; the UI reports what cannot be changed.
- The review PDF reflows body text. It does not reproduce Word pagination, headers, footers, images, or every kind of embedded content. The original structures remain in the Word file.
- JPEG, PNG, or WebP photos up to 20 MB and 25 megapixels. Keep all four corner markers and the print-run QR code visible. Automatic proposals target red or blue strike-throughs with short replacements above them; other marks need manual review.
- Each review supports up to 200 corrections. Capture proposes at most 18 crossed-out spans per photo and flags additional ink for inspection.
- Local use only. The server binds to this computer's loopback interface; transfer phone photographs to this computer before uploading. This is not a hosted multi-user service.

Handwriting recognition can miss or misread writing. Every automatic reading requires confirmation. A physically printed, pen-written, phone-photographed round trip has not yet been validated. Prepared examples and transformed handwriting fixtures are not evidence of arbitrary phone-photo accuracy. Export verification checks document preservation, not whether a handwritten instruction was understood correctly.

## Excel Mode

The earlier workbook workflow remains available at http://localhost:7171/?mode=workbooks. It reconciles corrections by typed row ID while preserving formulas and unrelated cells. To use its local TrOCR reader, also run:

```sh
npm run setup:ocr
npm run start:workbooks
```

Stop the Word server before starting Excel mode on the same port. The extra setup installs the separate PyTorch/TrOCR dependencies; Word mode does not need them. Workbook sessions are stored separately in `.data/sessions/`. Excel mode supports ordinary `.xlsx` tables with headers in row 1, unique row IDs, at most 500 data rows, and one or two editable text or number columns.

## Tech Stack

| Layer | Tools |
|-------|-------|
| Interface | React, TypeScript, Vite |
| Local API and review state | FastAPI, Pydantic, local files |
| Word and workbook edits | lxml, targeted OOXML changes |
| Review PDF and photo alignment | ReportLab, PyMuPDF, OpenCV |
| Word handwriting | PP-OCRv6 medium and small, ONNX Runtime |
| Workbook handwriting | TrOCR, PyTorch |

## Kiro

Kiro implemented the initial Word inspection, three-way text matching, and tracked-revision export engine in `backend/docx_engine.py`. The [Word session record](.kiro/evidence/word-revisions-session.txt) records that implementation and its original limitations. Codex subsequently hardened matching, protected-content handling, revision verification, and the clean-copy export. The historical session record describes the initial engine, not the final source.

Kiro also implemented the original workbook parsing, preservation-oriented patching, and merge modules, then addressed independent findings. See the [workbook session](.kiro/evidence/workbook-merge-session.md) and [initial export-verifier session](.kiro/evidence/export-verifier-session.txt). Codex implemented photo capture, local OCR integration, the API, the review interface, and independent verification.

The current requirements and design are in [`.kiro/specs/paperpatch/`](.kiro/specs/paperpatch/). Specs describe the implementation contract; the session records document actual Kiro work.

## License

PaperPatch is licensed under [AGPL-3.0](LICENSE). Copyright 2026 Thomas Ou.

PyMuPDF, used to render review pages, is available under the [AGPL or a commercial license](https://pymupdf.io/licensing); this project uses its open-source distribution. The downloaded [PP-OCRv6 models](https://huggingface.co/PaddlePaddle/PP-OCRv6_medium_rec_onnx) are Apache-2.0 licensed. The optional TrOCR reader uses Microsoft’s upstream model; its terms are separate from this project’s license. Bundled fonts retain their SIL Open Font License notices in `public/fonts/`. Other dependencies retain their own licenses. The release archive includes source for PaperPatch; model weights and Python dependencies are installed separately.

Built by Thomas Ou
