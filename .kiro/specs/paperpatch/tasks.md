# Build tasks

Historical Kiro sessions and subsequent Codex work are distinguished below. Checked items describe implemented work, not a claim that every supported input has been validated.

- [x] Kiro: initial workbook inspection, typed-ID reading, preservation-oriented patching, and three-way merge.
- [x] Kiro: initial workbook export verifier; retain the session record and its limitations.
- [x] Kiro: initial Word inspection, paragraph matching, and native tracked-revision engine; retain the original implementation session.
- [x] Codex: harden Word matching, protected-content handling, revision preservation, and clean-copy export.
- [x] Codex: review PDF geometry, photo alignment, local OCR, and ambiguous/unexplained-ink handling.
- [x] Codex: session persistence, revision-bound decisions, current-file hash binding, and export gates.
- [x] Codex: document-centered review UI with printed/latest/paper comparison and contextual Word preview.
- [x] Recorded verification: November 16 browser checks passed 28 scenarios; prepared Word export contained three native insertions and four deletions while preserving unrelated package parts. These results demonstrate the tested review/export cases, not physical capture accuracy.
- [x] Align the README and current specs with the Word workflow and actual Kiro contributions.
- [x] Recorded verification: November 17 checks covered 32 browser scenarios. A continuous 119-second recording exercised real UI decisions and Word downloads; four corrections were exported while preserving the newer schedule and accessibility paragraph.
- [x] Audit current recognition on 48 known, digitally composited handwriting examples: 48 correct targets, 23 exact primary readings, and 27 with the correct text in either suggestion. A fixed padding change failed its improvement gate and was not shipped. These results are development evidence, not physical-capture accuracy.
- [x] Publish the public repository, compiled v0.1.1 test build, current demonstration MP4, and captions. Verify the archive in a clean local installation and its public download URL.
- [ ] Validate a physically printed, pen-written, phone-photographed round trip before making capture-accuracy claims.
- [ ] Verify the final judge build on a separate clean machine; current launcher verification covers macOS on the development computer.
- [ ] Publish the current video to public YouTube, then complete and verify the Devpost submission.
