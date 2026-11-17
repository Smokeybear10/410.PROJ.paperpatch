# PaperPatch requirements

PaperPatch brings reviewed paper corrections into a newer Word document without overwriting unrelated digital edits. This is the current implementation contract, updated by Codex. Historical Kiro execution is recorded separately in `../../evidence/`; this spec is not proof of new Kiro work.

## Word review

- Treat the printed document as BASE, the latest uploaded Word draft as CURRENT, and each reviewed correction as PAPER. CURRENT is the only export base.
- Accept ordinary `.docx` input up to 10 MB. Generate at most eight review pages from at most 60 body paragraphs. The PDF reflows text and must explain that it does not reproduce original Word layout or all embedded content.
- Limit edits to ordinary top-level text paragraphs. Protect tables, fields, hyperlinks, tracked changes, hidden text, tabs, and unsupported structures. Preserve them in the Word file; expose relevant omissions and protection reasons.
- Print stable page geometry, four corner markers, and a review/page QR code. Accept JPEG, PNG, and WebP captures up to 20 MB and 25 megapixels. Reject captures that cannot be reliably aligned or bound to this printed review.
- Propose red/blue strike-through replacements and deletions. Preserve unexplained or ambiguous ink for explicit resolution. Recognition is a suggestion, never approval.
- Show BASE, CURRENT, PAPER, source crop, and the proposed sentence together. Allow manual corrections and readings. Require confirmation for every OCR reading, a keep-paper or keep-current decision for each correction, resolution of every detected mark, and inspection of each source page.
- Match corrections to CURRENT conservatively. Block missing, protected, ambiguous, or overlapping targets. Updating CURRENT must rematch targets and invalidate affected approvals and previous exports.
- Bound every mutation to the session revision. Bind export to the hash of CURRENT and reject export while capture is running.
- Export native Word insertions/deletions with reviewer attribution, a clean copy accepting only this export's revisions, the exact input document, and an audit record. Verify approved text, rejection restoration, unaffected content, and package preservation before saving an export.
- Keep documents and review history on the local computer. The default runtime must bind to loopback; this spec does not authorize a shared hosted service.

## Evidence and limits

The prepared example must be labeled as illustrative marks and prepared readings. The real-pen exercise starts without readings and reports its observed results separately. Synthetic or transformed handwriting tests must not be described as a physical print-and-photo validation. No arbitrary-handwriting accuracy claim is supported until measured against real captures.

## Workbook compatibility

Keep the existing Excel mode available separately. Match exact typed row IDs across BASE and CURRENT, preserve digital edits and formulas, and apply only explicitly approved PAPER values. Workbook input remains limited to ordinary `.xlsx` files with one or two supported editable columns. The original workbook implementation and verification sessions remain historical evidence.
