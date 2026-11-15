import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { FormEvent } from "react";
import { z } from "zod";
import {
  ArrowDownToLine,
  ArrowLeft,
  ArrowRight,
  Check,
  CheckCheck,
  ChevronLeft,
  ChevronRight,
  CircleAlert,
  FileCheck2,
  FileText,
  ImagePlus,
  LoaderCircle,
  Minus,
  PenLine,
  Plus,
  Printer,
  RotateCcw,
  Upload,
  X,
} from "lucide-react";
import { documentError, documentRequest, DocumentRequestError } from "./document-types";
import { DocumentCropViewer } from "./DocumentCropViewer";
import { RecentDocumentReviews } from "./RecentDocumentReviews";
import type {
  DocumentEdit,
  DocumentParagraph,
  DocumentScan,
  DocumentSession,
} from "./document-types";
import "./documents.css";

type Stage = "prepare" | "review" | "export";
type Mutation = (
  path: string,
  body: object,
  method?: "POST" | "PATCH",
) => Promise<boolean>;

const unsavedReadingNotice = "This browser could not save your reading. Save and confirm it, or discard the reading edit before leaving this correction.";

const statusNames: Record<DocumentEdit["status"], string> = {
  ready: "Ready to review",
  conflict: "Draft changed here",
  missing: "Find the passage",
  protected: "Protected passage",
  already_applied: "Already in the draft",
  overlap: "Overlapping corrections",
};

function editLabel(edit: DocumentEdit): string {
  if (edit.decision === "paper" && edit.status === "already_applied") return "Already in draft";
  if (edit.decision === "paper") return "Use paper";
  if (edit.decision === "current") return "Keep draft";
  if (edit.status === "already_applied") return "Already applied";
  if (!edit.confirmed && edit.status === "ready") return "Check reading";
  return statusNames[edit.status];
}

function isExportableEdit(edit: DocumentEdit): boolean {
  return edit.decision === "paper" && edit.confirmed &&
    (edit.status === "ready" || edit.status === "conflict") &&
    edit.currentParagraphId !== null && edit.currentStart !== null &&
    edit.currentEnd !== null && edit.currentStart >= 0 && edit.currentEnd >= edit.currentStart;
}

function editAction(edit: DocumentEdit): string {
  return edit.kind === "replace"
    ? "Replace"
    : edit.kind === "insert" || edit.start === edit.end
      ? "Insert"
      : edit.replacement
        ? "Replace"
        : "Delete";
}

function readDraft(key: string, fallback: string): string {
  try {
    return localStorage.getItem(key) ?? fallback;
  } catch {
    return fallback;
  }
}

function writeDraft(key: string, value: string | null): void {
  try {
    if (value === null) localStorage.removeItem(key);
    else localStorage.setItem(key, value);
  } catch {
    /* The review still works when browser storage is unavailable. */
  }
}

const readingDraftSchema = z.object({
  version: z.literal(2),
  sessionId: z.string(),
  editId: z.string(),
  sourceIdentity: z.string(),
  currentText: z.string(),
  serverReplacement: z.string(),
  text: z.string().max(2000),
});

function Mark({ small = false }: { small?: boolean }) {
  return (
    <span
      className={`doc-brand-mark ${small ? "small" : ""}`}
      aria-hidden="true"
    >
      <FileText size={small ? 20 : 25} strokeWidth={1.4} />
      <span />
    </span>
  );
}

function SourceBadge({ edit }: { edit: DocumentEdit }) {
  const readingChanged =
    edit.ocrText !== undefined && edit.replacement !== edit.ocrText;
  return (
    <span className="doc-source-badge">
      {edit.source === "demo"
        ? "Prepared reading"
        : edit.source === "manual"
          ? "Entered manually"
          : readingChanged
            ? "Reading edited during review"
            : edit.kind === "delete"
              ? "Detected strike-through"
              : edit.ocrText === ""
                ? "No readable OCR text"
                : "Local OCR suggestion"}
    </span>
  );
}

function Landing({
  busy,
  onExample,
  onTrial,
  onUpload,
}: {
  busy: string | null;
  onExample: () => void;
  onTrial: () => void;
  onUpload: (file: File) => void;
}) {
  const input = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  return (
    <main className="doc-landing" id="doc-main">
      <RecentDocumentReviews busy={!!busy} />
      <div className="doc-introduction">
        <span className="doc-eyebrow">PAPER → REVIEW → WORD</span>
        <h1>
          Your pen.
          <br />
          <em>Word’s</em> Track Changes.
        </h1>
        <p className="doc-lead">
          Make your edits on paper. Bring them back into the latest draft,
          without losing what changed while you were away.
        </p>
        <div className="doc-landing-actions">
          <button
            className="doc-button primary"
            disabled={!!busy}
            onClick={onExample}
          >
            {busy === "example" ? (
              <LoaderCircle className="doc-spin" size={18} />
            ) : (
              <ArrowRight size={18} />
            )}{" "}
            Open working example
          </button>
          <button
            className="doc-button"
            disabled={!!busy}
            onClick={() => input.current?.click()}
          >
            <Upload size={17} /> Choose Word file
          </button>
        </div>
        <p className="doc-fine-print">
          The example uses illustrative pen marks and prepared readings.
          <br />
          Your own documents are processed on this computer.
        </p>
        <button
          className="doc-text-link doc-trial-start"
          disabled={!!busy}
          onClick={onTrial}
        >
          <PenLine size={16} /> Try a real-pen check <ArrowRight size={14} />
        </button>
        <input
          ref={input}
          type="file"
          accept=".docx"
          hidden
          onChange={(event) => {
            const file = event.target.files?.[0];
            if (file) onUpload(file);
            event.target.value = "";
          }}
        />
        <div
          className={`doc-drop-line ${dragging ? "is-dragging" : ""}`}
          onDragOver={(event) => {
            event.preventDefault();
            setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={(event) => {
            event.preventDefault();
            setDragging(false);
            const file = event.dataTransfer.files[0];
            if (file && !busy) onUpload(file);
          }}
        >
          <FileText size={16} /> Or drop a .docx here to prepare a review copy
        </div>
      </div>
      <div
        className="doc-cover-example"
        aria-label="Illustration of handwritten corrections becoming tracked changes"
      >
        <div className="doc-cover-index">
          <span>01 / ON PAPER</span>
          <span>ILLUSTRATION</span>
        </div>
        <div className="doc-cover-sheet">
          <span className="doc-sheet-kicker">COMMUNITY OPEN HOUSE</span>
          <h2>
            A space to make
            <br />
            something together.
          </h2>
          <p>
            The workshop will welcome{" "}
            <span className="doc-pen-correction">
              <del>forty</del>
              <ins>sixty</ins>
            </span>{" "}
            guests. We’ll open the doors at 10 a.m. and finish with a shared
            lunch.
          </p>
          <p>
            <span className="doc-pen-strike">
              Registration closes on Friday.
            </span>{" "}
            There is no experience required. Bring an idea and a little
            curiosity.
          </p>
          <div className="doc-cover-margin">keep this open!</div>
          <div className="doc-cover-rule" />
          <span className="doc-sheet-footer">REVIEW COPY · 1 / 1</span>
        </div>
        <div className="doc-cover-result">
          <span className="doc-eyebrow">
            <Check size={14} /> BACK IN WORD
          </span>
          <p>
            The workshop will welcome <del>forty</del> <ins>sixty</ins> guests.
          </p>
          <span className="doc-cover-result-note">
            An editable revision. Yours to accept or reject.
          </span>
        </div>
      </div>
      <div className="doc-landing-bottom">
        <span>
          01 <b>Print & mark</b> <small>Use a red or blue pen.</small>
        </span>
        <span>
          02 <b>Review together</b>{" "}
          <small>See paper and the latest draft.</small>
        </span>
        <span>
          03 <b>Return to Word</b> <small>Download real tracked changes.</small>
        </span>
      </div>
    </main>
  );
}

function CaptureReport({ scan }: { scan: DocumentScan }) {
  const report = scan.captureReport;
  if (!report) return null;
  return (
    <section
      className="doc-capture-report"
      aria-label="First-pass capture results"
    >
      <div className="doc-capture-report-heading">
        <span className="doc-eyebrow">FIRST PASS · BEFORE REVIEW</span>
        <span>
          {report.source === "physical_reported"
            ? "Physical pen · reported by uploader"
            : report.source === "synthetic"
              ? "Synthetic image"
              : "Physical source not confirmed"}
        </span>
      </div>
      <div className="doc-capture-counts">
        <span>
          <b>
            {report.targetMatches}/{report.expectedCount}
          </b>{" "}
          targets found
        </span>
        <span>
          <b>
            {report.exactReadings}/{report.expectedCount}
          </b>{" "}
          exact results
        </span>
        <span>
          <b>{report.missedTargets}</b> missed
        </span>
        <span>
          <b>{report.unexpectedTargets}</b> unexpected
        </span>
      </div>
      <p>{report.scope}</p>
      {scan.captureReportUrl && (
        <p>
          <a href={scan.captureReportUrl} download>
            Download original capture results
          </a>
        </p>
      )}
      {!!report.failedAttemptsBeforeThis && (
        <p>
          {report.failedAttemptsBeforeThis} earlier upload
          {report.failedAttemptsBeforeThis === 1 ? "" : "s"} could not be
          processed before this result. Recognition counts above describe this
          photograph only.
        </p>
      )}
      {report.source === "physical_reported" && (
        <p>
          The uploader reports printing and handwriting these marks. This is not
          independently verified.
        </p>
      )}
      <details>
        <summary>Compare all {report.expectedCount} intended edits</summary>
        <div className="doc-capture-table-wrap">
          <table>
            <thead>
              <tr>
                <th>Printed</th>
                <th>Intended</th>
                <th>First-pass result</th>
              </tr>
            </thead>
            <tbody>
              {report.items.map((item, index) => (
                <tr key={`${item.paragraphId}:${index}`}>
                  <td>{item.before}</td>
                  <td>{item.expected || <em>Delete</em>}</td>
                  <td>
                    <span className={item.exact ? "exact" : "needs-review"}>
                      {!item.targetFound
                        ? "Not detected"
                        : item.observed === null
                          ? "No reading"
                          : item.observed === ""
                            ? item.observedKind === "delete" ||
                              (item.exact && item.expected === "")
                              ? "Deletion"
                              : "Unresolved reading"
                            : item.observed}
                    </span>
                    <small>
                      {item.exact
                        ? "Exact"
                        : item.targetFound
                          ? "Review needed"
                          : "Target missed"}
                    </small>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p>
          These initial results stay unchanged when you correct a reading or add
          a missed mark. Exact results include deletions; this is one capture,
          not a general accuracy estimate.
        </p>
      </details>
    </section>
  );
}

function ProofViewer({
  session,
  selected,
  source,
  setSource,
  selectEdit,
  busy,
  onReview,
  onManual,
  onResolveMark,
}: {
  session: DocumentSession;
  selected: DocumentEdit | null;
  source: string;
  setSource: (source: string) => void;
  selectEdit: (edit: DocumentEdit) => void;
  busy: boolean;
  onReview: (id: string) => void;
  onManual: (markId?: string) => void;
  onResolveMark: (
    scanId: string,
    markId: string,
    resolution: "ignored" | "correction" | null,
    editId?: string,
  ) => Promise<boolean>;
}) {
  const [zoom, setZoom] = useState(100);
  const [linkingMarkId, setLinkingMarkId] = useState<string | null>(null);
  const [linkedEditId, setLinkedEditId] = useState("");
  const proofScroll = useRef<HTMLDivElement>(null);
  const scan = session.scans.find((item) => item.id === source);
  useEffect(() => {
    setLinkingMarkId(null);
    setLinkedEditId("");
  }, [source]);
  useEffect(() => {
    const viewport = proofScroll.current;
    const region = viewport?.querySelector(".doc-ink-region.selected");
    if (!viewport || !region) return;
    const frame = viewport.getBoundingClientRect();
    const mark = region.getBoundingClientRect();
    if (mark.top < frame.top || mark.bottom > frame.bottom || mark.left < frame.left || mark.right > frame.right) {
      viewport.scrollBy({
        top: mark.top + mark.height / 2 - frame.top - frame.height / 2,
        left: mark.left + mark.width / 2 - frame.left - frame.width / 2,
      });
    }
  }, [selected?.id, source, zoom]);
  const pageNumber = (scan?.page ?? Number(source.replace("page:", ""))) || 1;
  const page =
    session.pages.find((item) => item.number === pageNumber) ??
    session.pages[0];
  const sourceEdits = scan
    ? session.edits.filter((edit) => edit.scanId === scan.id && edit.rect)
    : [];
  const linkableEdits = scan
    ? session.edits.filter((edit) => edit.scanId === scan.id && edit.page === scan.page)
    : [];
  const unmatchedMarks = scan?.marks ?? [];
  const unresolvedMarks = unmatchedMarks.filter((mark) => !mark.resolution);
  const pageChecked = !!scan?.reviewed && unresolvedMarks.length === 0;
  const openMark = (mark: (typeof unmatchedMarks)[number]) => {
    const edit = session.edits.find((item) => item.id === mark.editId);
    if (edit) selectEdit(edit);
    else onManual(mark.id);
  };
  const captureProblems =
    scan?.warnings.filter((warning) =>
      /unavailable|could not be read|no clear|looks soft|appears dark/i.test(
        warning,
      ),
    ) ?? [];
  return (
    <section className="doc-proof" aria-label="Document source">
      <div className="doc-proof-toolbar">
        <label className="doc-source-select">
          <span className="doc-sr-only">Source page</span>
          <select
            value={source}
            onChange={(event) => setSource(event.target.value)}
          >
            {session.pages.map((item) => (
              <option key={item.number} value={`page:${item.number}`}>
                Printed copy · page {item.number}
              </option>
            ))}
            {session.scans.map((item) => (
              <option key={item.id} value={item.id}>
                {item.reviewer} · page {item.page}
                {item.source === "demo" ? " · example" : ""}
              </option>
            ))}
          </select>
        </label>
        <div className="doc-zoom">
          <button
            aria-label="Zoom out"
            disabled={zoom <= 70}
            onClick={() => setZoom((value) => value - 15)}
          >
            <Minus size={15} />
          </button>
          <output>{zoom}%</output>
          <button
            aria-label="Zoom in"
            disabled={zoom >= 190}
            onClick={() => setZoom((value) => value + 15)}
          >
            <Plus size={15} />
          </button>
        </div>
      </div>
      {scan?.source === "demo" && (
        <div className="doc-example-strip">
          Illustrative source · prepared readings · not an OCR accuracy test
        </div>
      )}
      {scan && <CaptureReport scan={scan} />}
      {captureProblems.length > 0 && (
        <div className="doc-capture-problem">
          <CircleAlert size={15} />
          <div>
            {captureProblems.map((warning) => (
              <p key={warning}>{warning}</p>
            ))}
            <button
              className="doc-text-link"
              disabled={busy}
              onClick={() => onManual()}
            >
              Enter a missed correction <Plus size={13} />
            </button>
          </div>
        </div>
      )}
      <div className="doc-proof-scroll" ref={proofScroll}>
        {page ? (
          <div
            className="doc-proof-paper"
            style={{
              width: `${zoom}%`,
              aspectRatio: `${page.width} / ${page.height}`,
            }}
          >
            <img
              src={scan?.alignedUrl ?? page.previewUrl}
              alt={
                scan
                  ? `${scan.reviewer}’s marked review page ${scan.page}`
                  : `Printable review page ${page.number}`
              }
              draggable={false}
            />
            {sourceEdits.map(
              (edit, index) =>
                edit.rect && (
                  <button
                    key={edit.id}
                    className={`doc-ink-region ${selected?.id === edit.id ? "selected" : ""} ${edit.decision ? "resolved" : ""}`}
                    style={{
                      left: `${edit.rect[0] * 100}%`,
                      top: `${edit.rect[1] * 100}%`,
                      width: `${edit.rect[2] * 100}%`,
                      height: `${edit.rect[3] * 100}%`,
                    }}
                    aria-label={`Review ${editAction(edit).toLowerCase()}: ${edit.baseText || "inserted text"}`}
                    aria-pressed={selected?.id === edit.id}
                    onClick={() => selectEdit(edit)}
                  >
                    <span>{index + 1}</span>
                  </button>
                ),
            )}
            {unmatchedMarks.map(
              (mark, index) =>
                mark.resolution !== "ignored" && (
                  <button
                    key={mark.id}
                    className={`doc-unmatched-region ${mark.resolution ? "resolved" : ""}`}
                    style={{
                      left: `${mark.rect[0] * 100}%`,
                      top: `${mark.rect[1] * 100}%`,
                      width: `${mark.rect[2] * 100}%`,
                      height: `${mark.rect[3] * 100}%`,
                    }}
                    title={mark.reason}
                    disabled={busy}
                    aria-label={`${mark.resolution ? "Open correction for" : "Resolve"} pen mark ${index + 1}`}
                    onClick={() => openMark(mark)}
                  >
                    <span>{mark.resolution ? "✓" : "?"}</span>
                  </button>
                ),
            )}
          </div>
        ) : (
          <p className="doc-empty">No review page is available.</p>
        )}
      </div>
      {scan && unmatchedMarks.length > 0 && (
        <section
          className="doc-mark-queue"
          aria-label="Pen marks to account for"
        >
          <div className="doc-mark-queue-heading">
            <b>
              {unresolvedMarks.length
                ? `${unresolvedMarks.length} pen mark${unresolvedMarks.length === 1 ? " needs" : "s need"} attention`
                : "Every detected pen mark is accounted for"}
            </b>
            <span>
              {unmatchedMarks.length - unresolvedMarks.length}/
              {unmatchedMarks.length} resolved
            </span>
          </div>
          <p>
            Enter a correction, connect the mark to an existing correction, or
            mark it as not changing the text.
          </p>
          <div className="doc-mark-queue-items">
            {unmatchedMarks.map((mark, index) => (
              <div
                className={`doc-mark-queue-item ${mark.resolution ? "resolved" : ""}`}
                key={mark.id}
              >
                <button
                  className="doc-mark-crop-button"
                  disabled={busy || mark.resolution === "ignored"}
                  onClick={() => openMark(mark)}
                  aria-label={`Inspect pen mark ${index + 1}`}
                >
                  <img src={mark.cropUrl} alt={`Pen mark ${index + 1}`} />
                </button>
                <div>
                  <b>Mark {index + 1}</b>
                  <p>
                    {mark.resolution === "ignored"
                      ? "Not an edit"
                      : mark.resolution === "correction"
                        ? "Linked to a correction"
                        : mark.reason}
                  </p>
                  <div className="doc-mark-actions">
                    {mark.resolution === "ignored" ? (
                      <button
                        className="doc-text-link"
                        disabled={busy}
                        onClick={() => onResolveMark(scan.id, mark.id, null)}
                      >
                        Undo dismissal
                      </button>
                    ) : mark.resolution === "correction" ? (
                      <>
                      <button
                        className="doc-text-link"
                        disabled={busy}
                        onClick={() => openMark(mark)}
                      >
                        Open correction <ArrowRight size={13} />
                      </button>
                      {!linkableEdits.some((edit) => edit.sourceMarkId === mark.id) && (
                        <button
                          className="doc-text-link"
                          disabled={busy}
                          onClick={() => onResolveMark(scan.id, mark.id, null)}
                        >
                          Unlink mark
                        </button>
                      )}
                      </>
                    ) : (
                      <>
                        <button
                          className="doc-text-link"
                          disabled={busy}
                          onClick={() => onManual(mark.id)}
                        >
                          Enter correction <Plus size={13} />
                        </button>
                        {linkableEdits.length > 0 && linkingMarkId !== mark.id && (
                          <button
                            className="doc-text-link"
                            disabled={busy}
                            onClick={() => {
                              setLinkingMarkId(mark.id);
                              setLinkedEditId("");
                            }}
                          >
                            Part of an existing correction
                          </button>
                        )}
                        <button
                          className="doc-text-link"
                          disabled={busy}
                          onClick={() =>
                            onResolveMark(scan.id, mark.id, "ignored")
                          }
                        >
                          Not an edit
                        </button>
                      </>
                    )}
                  </div>
                  {!mark.resolution && linkingMarkId === mark.id && (
                    <div className="doc-mark-linker">
                      <label>
                        Correction for mark {index + 1}
                        <select
                          value={linkedEditId}
                          disabled={busy}
                          onChange={(event) => setLinkedEditId(event.target.value)}
                        >
                          <option value="">Choose from this source page</option>
                          {linkableEdits.map((edit) => (
                            <option key={edit.id} value={edit.id}>
                              {editAction(edit)}: {edit.baseText || "insertion"}
                              {edit.replacement ? ` → ${edit.replacement}` : ""}
                            </option>
                          ))}
                        </select>
                      </label>
                      <p>This keeps the ink as evidence for the same change.</p>
                      <div className="doc-mark-actions">
                        <button
                          className="doc-button compact"
                          disabled={busy || !linkedEditId}
                          onClick={async () => {
                            if (await onResolveMark(scan.id, mark.id, "correction", linkedEditId)) {
                              setLinkingMarkId(null);
                              setLinkedEditId("");
                            }
                          }}
                        >
                          Link mark
                        </button>
                        <button
                          className="doc-text-link"
                          disabled={busy}
                          onClick={() => setLinkingMarkId(null)}
                        >
                          Cancel
                        </button>
                      </div>
                    </div>
                  )}
                </div>
              </div>
            ))}
          </div>
        </section>
      )}
      {scan ? (
        <div className="doc-proof-footer">
          <div>
            <b>
              {pageChecked ? (
                <>
                  <CheckCheck size={16} /> Page checked
                </>
              ) : (
                <>
                  <CircleAlert size={16} /> Check the whole page
                </>
              )}
            </b>
            <p>
              {unresolvedMarks.length
                ? `Account for ${unresolvedMarks.length} unresolved pen mark${unresolvedMarks.length === 1 ? "" : "s"} before checking this page.`
                : pageChecked
                  ? "You confirmed the page was inspected for missed marks."
                  : "Recognition can miss writing. Look beyond the highlighted regions."}
            </p>
          </div>
          <button
            className="doc-button compact"
            disabled={busy || pageChecked || unresolvedMarks.length > 0}
            onClick={() => onReview(scan.id)}
          >
            {pageChecked ? "Checked" : "I checked this page"}
          </button>
          <details className="doc-scan-details">
            <summary>Source & capture notes</summary>
            <a href={scan.originalUrl} target="_blank" rel="noreferrer">
              View source image <ArrowRight size={13} />
            </a>
            {scan.uploadUrl && (
              <a href={scan.uploadUrl} download>
                Download exact uploaded file <ArrowDownToLine size={13} />
              </a>
            )}
            {scan.warnings.map((warning, index) => (
              <p key={index}>{warning}</p>
            ))}
            {!scan.warnings.length && <p>No capture warnings recorded.</p>}
          </details>
        </div>
      ) : (
        <div className="doc-proof-footer">
          <p>
            This review copy keeps your text, with more room for handwriting. It
            does not reproduce the original Word pagination.
          </p>
          <a
            className="doc-text-link"
            href={session.printUrl}
            target="_blank"
            rel="noreferrer"
          >
            <Printer size={15} /> Open printable PDF
          </a>
        </div>
      )}
    </section>
  );
}

function EditInspector({
  session,
  edit,
  busy,
  mutate,
  next,
  previous,
  hasPrevious,
  hasNext,
  onManual,
  onDraftProtectionChange,
}: {
  session: DocumentSession;
  edit: DocumentEdit;
  busy: boolean;
  mutate: Mutation;
  next: () => void;
  previous: () => void;
  hasPrevious: boolean;
  hasNext: boolean;
  onManual: () => void;
  onDraftProtectionChange: (blocked: boolean) => void;
}) {
  const draftKey = `paperpatch-word-draft:${session.id}:${edit.id}`;
  const legacyDraftKey = `${draftKey}:${JSON.stringify([edit.baseText, edit.currentText, edit.replacement])}`;
  const initialLegacyKey = useRef(legacyDraftKey);
  const sourceIdentity = JSON.stringify([edit.paragraphId, edit.start, edit.end, edit.baseText, edit.scanId, edit.sourceMarkId ?? null]);
  const [draft, setDraft] = useState<z.infer<typeof readingDraftSchema> | null>(() => {
    try {
      const parsed = readingDraftSchema.safeParse(JSON.parse(readDraft(draftKey, "null")));
      if (parsed.success && parsed.data.sessionId === session.id && parsed.data.editId === edit.id && parsed.data.sourceIdentity === sourceIdentity) return parsed.data;
    } catch { /* Older drafts were stored as plain text. */ }
    const legacy = readDraft(legacyDraftKey, "\u0000");
    return legacy === "\u0000" ? null : {
      version: 2, sessionId: session.id, editId: edit.id, sourceIdentity,
      currentText: edit.currentText, serverReplacement: edit.replacement, text: legacy,
    };
  });
  const replacement = draft?.text ?? edit.replacement;
  const [draftSaved, setDraftSaved] = useState(Boolean(draft));
  const changed = replacement !== edit.replacement;
  const contextChanged = !!draft && (draft.currentText !== edit.currentText || draft.serverReplacement !== edit.replacement);
  const clearDraft = () => {
    writeDraft(draftKey, null);
    writeDraft(initialLegacyKey.current, null);
    setDraft(null);
    setDraftSaved(false);
    onDraftProtectionChange(false);
  };
  const updateReplacement = (value: string) => {
    const next = {
      ...(draft ?? { version: 2 as const, sessionId: session.id, editId: edit.id, sourceIdentity,
        currentText: edit.currentText, serverReplacement: edit.replacement }),
      text: value,
    };
    setDraft(next);
    const serialized = JSON.stringify(next);
    writeDraft(draftKey, serialized);
    const saved = readDraft(draftKey, "") === serialized;
    setDraftSaved(saved);
    onDraftProtectionChange((value !== edit.replacement || contextChanged) && !saved);
  };
  useEffect(() => {
    if (!draft) return;
    const serialized = JSON.stringify(draft);
    writeDraft(draftKey, serialized);
    const saved = readDraft(draftKey, "") === serialized;
    setDraftSaved(saved);
    if (saved) writeDraft(initialLegacyKey.current, null);
  }, [draftKey, draft]);
  useEffect(() => {
    onDraftProtectionChange((changed || contextChanged) && !draftSaved);
    return () => onDraftProtectionChange(false);
  }, [changed, contextChanged, draftSaved, onDraftProtectionChange]);
  useEffect(() => {
    if ((!changed && !contextChanged) || draftSaved) return;
    const warn = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [changed, contextChanged, draftSaved]);
  const missingReading = edit.kind === "replace" && !replacement.trim();
  const canApply =
    ["ready", "conflict"].includes(edit.status) &&
    edit.currentParagraphId !== null &&
    edit.currentStart !== null &&
    edit.currentEnd !== null;
  const confirmed = edit.confirmed && !changed && !contextChanged;
  const index = session.edits.findIndex((item) => item.id === edit.id);
  const patch = async (body: object) =>
    mutate(`/edits/${edit.id}`, body, "PATCH");
  const confirm = async () => {
    if (await patch({ replacement, confirmed: true }))
      clearDraft();
  };
  const confirmAndApply = async () => {
    if (await patch({ replacement, confirmed: true, decision: "paper" }))
      clearDraft();
  };
  const alternatives = [...new Set(edit.ocrAlternatives ?? [])]
    .filter((value) => value.trim() && value !== edit.ocrText)
    .slice(0, 2);
  return (
    <aside
      className="doc-inspector"
      aria-label="Correction review"
      tabIndex={-1}
    >
      <div className="doc-inspector-top">
        <span className="doc-eyebrow">
          CORRECTION {String(index + 1).padStart(2, "0")} /{" "}
          {String(session.edits.length).padStart(2, "0")}
        </span>
        <div>
          <button
            aria-label="Previous correction"
            disabled={!hasPrevious || changed || busy}
            onClick={previous}
          >
            <ChevronLeft size={18} />
          </button>
          <button
            aria-label="Next correction"
            disabled={!hasNext || changed || busy}
            onClick={next}
          >
            <ChevronRight size={18} />
          </button>
        </div>
      </div>
      <h2>
        {editAction(edit)}{" "}
        {edit.start === edit.end ? "at this passage" : "this passage"}
      </h2>
      <div className="doc-edit-author">
        <span className="doc-avatar">
          {edit.reviewer.slice(0, 1).toUpperCase()}
        </span>
        <b>{edit.reviewer}</b>
        <SourceBadge edit={edit} />
      </div>
      <div
        className={`doc-state-note ${edit.status === "conflict" || edit.status === "overlap" ? "conflict" : ""}`}
      >
        <CircleAlert size={16} />
        <div>
          <b>{statusNames[edit.status]}</b>
          <p>
            {edit.reason ||
              "Check the reading against the source, then choose what belongs in the draft."}
          </p>
        </div>
      </div>
      {edit.cropUrl && (
        <div className="doc-crop">
          <span className="doc-eyebrow">SOURCE MARK</span>
          <DocumentCropViewer
            src={edit.cropUrl}
            alt={`Source handwriting for ${edit.reviewer}’s correction`}
          />
        </div>
      )}
      <div className="doc-comparison">
        <div>
          <span className="doc-eyebrow">PRINTED</span>
          <p className="doc-original-text">
            {edit.baseText || <em>Insertion point</em>}
          </p>
        </div>
        <div className={edit.currentText !== edit.baseText ? "changed" : ""}>
          <span className="doc-eyebrow">
            LATEST DRAFT{" "}
            {edit.currentText !== edit.baseText && <span>CHANGED</span>}
          </span>
          <p>
            {edit.currentText || (
              <em>
                {edit.start === edit.end ? "Insertion point" : "Passage absent"}
              </em>
            )}
          </p>
        </div>
      </div>
      <label className="doc-replacement-label">
        <span className="doc-eyebrow">
          {edit.confirmed ? "REVIEWED TEXT" : "ON PAPER"}{" "}
          <span>{confirmed ? "READING CONFIRMED" : "CHECK THE READING"}</span>
        </span>
        <textarea
          value={replacement}
          maxLength={2000}
          rows={2}
          disabled={busy || edit.decision !== null}
          placeholder={
            edit.kind === "replace"
              ? "Type the replacement written above the crossed-out words"
              : edit.kind === "insert" || edit.start === edit.end
                ? "Type the words to insert"
                : "Leave empty for a deletion"
          }
          onChange={(event) => updateReplacement(event.target.value)}
        />
      </label>
      {edit.source === "ocr" && (
        <div className="doc-initial-reading">
          <span className="doc-eyebrow">INITIAL MACHINE RESULT</span>
          <p>
            {edit.ocrText === undefined
              ? "Original reading was not recorded for this older capture."
              : edit.kind === "delete" && edit.ocrText === ""
                ? "Strike-through detected; no replacement reading."
                : edit.ocrText ||
                  "Replacement ink detected, but no readable text."}
          </p>
          {edit.ocrText !== undefined && replacement !== edit.ocrText && (
            <small>
              You changed the reading. The initial result remains in the record.
            </small>
          )}
        </div>
      )}
      {!replacement && (
        <p className="doc-delete-note">
          <Minus size={14} />
          {missingReading
            ? "Replacement ink was found, but the reading is empty. Enter the words you see, or keep the latest draft. For a deletion, add a manual correction."
            : "This correction removes the selected text."}
        </p>
      )}
      {alternatives.length > 0 && !edit.decision && (
        <div className="doc-reading-alternatives">
          <span className="doc-eyebrow">OTHER MACHINE READINGS</span>
          <p>Compare these with the image. Choosing one only fills the reading; it does not approve it.</p>
          <div>{alternatives.map((value) => (
            <button key={value} type="button" disabled={busy} aria-pressed={replacement === value} onClick={() => updateReplacement(value)}>{value}</button>
          ))}</div>
        </div>
      )}
      {(changed || contextChanged) && (
        <div className="doc-reading-draft" role="status">
          {contextChanged && <p>The latest passage or saved reading changed. Your draft is retained below; compare it with the latest text before confirming.</p>}
          <p>{draftSaved
            ? "Reading saved in this browser. Confirm it to add it to the review."
            : "This browser could not save your draft. Confirm the reading before leaving this correction."}</p>
          <button type="button" className="doc-text-link" disabled={busy} onClick={clearDraft}>Discard reading edit</button>
        </div>
      )}
      {!edit.decision && (
        <button
          className="doc-button doc-confirm"
          disabled={busy || confirmed || missingReading}
          onClick={confirm}
        >
          {confirmed ? <CheckCheck size={16} /> : <Check size={16} />}
          {confirmed
            ? "Reading confirmed"
            : changed
              ? "Save & confirm reading"
              : "This reading is correct"}
        </button>
      )}
      {edit.decision ? (
        <div className="doc-decision-saved">
          {edit.decision === "paper" && edit.status !== "already_applied" && !isExportableEdit(edit)
            ? <CircleAlert size={20} /> : <CheckCheck size={20} />}
          <div>
            <b>
              {edit.decision === "paper"
                ? edit.status === "already_applied"
                  ? "Already in the latest draft"
                  : isExportableEdit(edit)
                    ? "Paper correction selected"
                    : "Selected correction needs attention"
                : "Latest draft kept"}
            </b>
            <p>
              {edit.decision === "paper"
                ? edit.status === "already_applied"
                  ? "This reading is already present. It will not create a tracked change."
                  : isExportableEdit(edit)
                    ? "It will be included as a tracked change."
                    : "Resolve its reading or target before exporting."
                : "This paper suggestion will not change the draft."}
            </p>
          </div>
          <button
            aria-label="Change this decision"
            disabled={busy}
            onClick={() => patch({ decision: null })}
          >
            <RotateCcw size={16} />
          </button>
        </div>
      ) : (
        <div className="doc-decision-actions">
          <button
            className="doc-button primary"
            disabled={busy || missingReading || !canApply}
            onClick={() => confirmed ? patch({ decision: "paper" }) : confirmAndApply()}
          >
            <PenLine size={16} />
            {!confirmed ? "Confirm & use paper" : edit.status === "conflict"
              ? "Replace latest with paper"
              : "Use paper correction"}
          </button>
          <button
            className="doc-button"
            disabled={busy || changed}
            onClick={() => patch({ decision: "current" })}
          >
            Keep latest draft
          </button>
        </div>
      )}
      {!edit.decision && edit.status === "overlap" && (
        <p className="doc-fine-print">
          To use this version, select the other correction for this passage and
          choose “Keep latest draft” to dismiss it. Then return here.
        </p>
      )}
      {!canApply && !["already_applied", "overlap"].includes(edit.status) && (
        <p className="doc-fine-print">
          This target needs attention. Keep the latest draft, then{" "}
          <button className="doc-inline-link" onClick={onManual}>
            enter a correction at the right passage
          </button>
          .
        </p>
      )}
      <details className="doc-context">
        <summary>Read the passage in context</summary>
        <p>{edit.paragraphText}</p>
      </details>
    </aside>
  );
}

function ManualDialog({
  session,
  busy,
  close,
  mutate,
  initialScanId,
  initialReviewer,
  initialMarkId,
  requestError,
  reloadNeeded,
  reloadReview,
}: {
  session: DocumentSession;
  busy: boolean;
  close: () => void;
  mutate: Mutation;
  initialScanId: string;
  initialReviewer: string;
  initialMarkId?: string | null;
  requestError: string | null;
  reloadNeeded: boolean;
  reloadReview: () => void;
}) {
  const draftSchema = z.object({
    version: z.literal(1),
    sessionId: z.literal(session.id),
    originScanId: z.literal(initialScanId),
    sourceMarkId: z.literal(initialMarkId ?? null),
    passageKey: z.string(),
    target: z.string(),
    occurrence: z.number().int().nonnegative(),
    operation: z.enum(["replace", "delete", "before", "after"]),
    replacement: z.string().max(2000),
    reviewer: z.string().max(80),
    scanId: z.string(),
  });
  const [draftKey] = useState(() => `paperpatch-word-manual:${session.id}:${initialScanId || "no-source"}:${initialMarkId ?? "unbound"}`);
  const [storedDraft] = useState(() => {
    try {
      const parsed = draftSchema.safeParse(JSON.parse(readDraft(draftKey, "null")));
      if (!parsed.success) return null;
      if (parsed.data.scanId && !session.scans.some((scan) => scan.id === parsed.data.scanId)) return null;
      return parsed.data;
    } catch {
      return null;
    }
  });
  const dialog = useRef<HTMLDialogElement>(null);
  const submitting = useRef(false);
  const [passageKey, setPassageKey] = useState(storedDraft?.passageKey ?? "");
  const [target, setTarget] = useState(storedDraft?.target ?? "");
  const [occurrence, setOccurrence] = useState(storedDraft?.occurrence ?? 0);
  const [operation, setOperation] = useState<string>(storedDraft?.operation ?? "replace");
  const [replacement, setReplacement] = useState(storedDraft?.replacement ?? "");
  const [reviewer, setReviewer] = useState(storedDraft?.reviewer ?? initialReviewer);
  const [scanId, setScanId] = useState(storedDraft?.scanId ?? initialScanId);
  const [draftSaved, setDraftSaved] = useState(Boolean(storedDraft));
  const [closeBlocked, setCloseBlocked] = useState(false);
  const [saveFailed, setSaveFailed] = useState(false);
  useEffect(() => { setSaveFailed(false); }, [session.revision]);
  const sourceScan = session.scans.find((item) => item.id === scanId);
  const sourceMark = sourceScan?.marks?.find(
    (item) => item.id === initialMarkId,
  );
  const passages = useMemo(() => {
    const ranges = sourceScan
      ? (session.pages.find((page) => page.number === sourceScan.page)
          ?.textRanges ?? [])
      : session.paragraphs.map((paragraph) => ({
          paragraphId: paragraph.id,
          start: 0,
          end: Array.from(paragraph.text).length,
        }));
    return ranges.flatMap((range) => {
      const paragraph = session.paragraphs.find(
        (item) => item.id === range.paragraphId,
      );
      if (!paragraph?.editable || range.start >= range.end) return [];
      return [
        {
          ...range,
          paragraph,
          key: `${range.paragraphId}:${range.start}:${range.end}`,
          text: Array.from(paragraph.text)
            .slice(range.start, range.end)
            .join(""),
        },
      ];
    });
  }, [session, sourceScan]);
  const passage =
    passages.find((item) => item.key === passageKey) ??
    passages.find(
      (item) => item.paragraphId === sourceMark?.suggestedParagraphId,
    ) ??
    passages[0];
  const paragraph = passage?.paragraph;
  const matches = useMemo(() => {
    if (!target || !passage) return [];
    const found: number[] = [];
    let offset = passage.text.indexOf(target);
    while (offset !== -1) {
      found.push(
        passage.start + Array.from(passage.text.slice(0, offset)).length,
      );
      offset = passage.text.indexOf(target, offset + 1);
    }
    return found;
  }, [passage, target]);
  const targetStart = matches[occurrence];
  const passageMatchesDraft = !passageKey || passage?.key === passageKey;
  const hasDraft = Boolean(target || replacement || passageKey || operation !== "replace" || reviewer !== initialReviewer || scanId !== initialScanId);
  const serializedDraft = JSON.stringify({
    version: 1, sessionId: session.id, originScanId: initialScanId,
    sourceMarkId: initialMarkId ?? null, passageKey: passage?.key ?? passageKey,
    target, occurrence, operation, replacement, reviewer, scanId,
  });
  useEffect(() => {
    if (submitting.current) return;
    if (!hasDraft) {
      writeDraft(draftKey, null);
      setDraftSaved(false);
      return;
    }
    writeDraft(draftKey, serializedDraft);
    const saved = readDraft(draftKey, "") === serializedDraft;
    setDraftSaved(saved);
    if (saved) setCloseBlocked(false);
  }, [draftKey, serializedDraft, hasDraft]);
  useEffect(() => {
    if (!hasDraft || draftSaved) return;
    const warn = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [hasDraft, draftSaved]);
  const requestClose = () => {
    if (busy) return;
    if (hasDraft) {
      writeDraft(draftKey, serializedDraft);
      if (readDraft(draftKey, "") !== serializedDraft) {
        setDraftSaved(false);
        setCloseBlocked(true);
        return;
      }
    }
    close();
  };
  const discard = () => {
    submitting.current = true;
    writeDraft(draftKey, null);
    close();
  };
  useEffect(() => {
    dialog.current?.showModal();
  }, []);
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (submitting.current || busy) return;
    const start = matches[occurrence];
    if (start === undefined || !paragraph || !passageMatchesDraft) return;
    const end = start + Array.from(target).length;
    const body = {
      paragraphId: paragraph.id,
      start: operation === "after" ? end : start,
      end: operation === "before" ? start : end,
      replacement: operation === "delete" ? "" : replacement,
      reviewer: reviewer.trim() || "Reviewer",
      ...(scanId ? { scanId } : {}),
      ...(initialMarkId ? { sourceMarkId: initialMarkId } : {}),
    };
    submitting.current = true;
    setSaveFailed(false);
    if (await mutate("/edits", body)) {
      writeDraft(draftKey, null);
      close();
    } else {
      submitting.current = false;
      setSaveFailed(true);
    }
  };
  return (
    <dialog
      ref={dialog}
      className="doc-manual-dialog"
      onCancel={(event) => {
        event.preventDefault();
        requestClose();
      }}
    >
      <form onSubmit={submit}>
        <header>
          <div>
            <span className="doc-eyebrow">MANUAL CORRECTION</span>
            <h2>Point to the exact passage.</h2>
          </div>
          <button
            type="button"
            aria-label="Close manual correction"
            disabled={busy}
            onClick={requestClose}
          >
            <X size={20} />
          </button>
        </header>
        <p className="doc-fine-print">
          Add a missed mark or a correction you transcribed yourself. It is
          recorded as manual, not handwriting recognition.
        </p>
        {sourceMark && sourceScan && (
          <div className="doc-manual-mark-source">
            <span className="doc-eyebrow">
              SOURCE MARK · PAGE {sourceScan.page}
            </span>
            <DocumentCropViewer
              src={sourceMark.cropUrl}
              alt="Pen mark being entered as a manual correction"
            />
            <a href={sourceScan.alignedUrl} target="_blank" rel="noreferrer">
              View the whole source page <ArrowRight size={13} />
            </a>
          </div>
        )}
        <label>
          Associated source page
          <select
            value={scanId}
            disabled={!!initialMarkId || busy}
            onChange={(event) => {
              setScanId(event.target.value);
              setPassageKey("");
              setTarget("");
              setOccurrence(0);
            }}
          >
            <option value="">No photo — manual instruction</option>
            {session.scans.map((scan) => (
              <option key={scan.id} value={scan.id}>
                {scan.reviewer} · page {scan.page}
              </option>
            ))}
          </select>
        </label>
        <label>
          {sourceScan
            ? `Printed passage on page ${sourceScan.page}`
            : "Printed paragraph"}
          <select
            value={passage?.key ?? ""}
            disabled={!passages.length || busy}
            onChange={(event) => {
              setPassageKey(event.target.value);
              setTarget("");
              setOccurrence(0);
            }}
          >
            {!passages.length && (
              <option value="">No editable passage on this page</option>
            )}
            {passages.map((item) => (
              <option key={item.key} value={item.key}>
                {item.paragraph.index + 1}. {item.text.slice(0, 90)}
              </option>
            ))}
          </select>
        </label>
        {!passages.length && (
          <p className="doc-field-error">
            This source page has no editable text range. Reload if this is an
            older review; protected Word content cannot be changed here.
          </p>
        )}
        <label htmlFor="doc-selectable-passage" className="doc-selection-label">
          Select the exact words in the printed passage
        </label>
        <p id="doc-selection-help" className="doc-selection-help">Drag over the words, or use the arrow keys with Shift. You can also type the exact words below.</p>
        <textarea
          id="doc-selectable-passage"
          className="doc-manual-passage"
          rows={4}
          readOnly
          disabled={busy}
          value={passage?.text ?? ""}
          aria-describedby="doc-selection-help"
          onSelect={(event) => {
            if (!passage || busy) return;
            const { selectionStart, selectionEnd } = event.currentTarget;
            if (selectionStart === selectionEnd) return;
            const text = passage.text.slice(selectionStart, selectionEnd);
            let selectedOccurrence = 0;
            let offset = passage.text.indexOf(text);
            while (offset !== -1 && offset < selectionStart) {
              selectedOccurrence += 1;
              offset = passage.text.indexOf(text, offset + 1);
            }
            setPassageKey(passage.key);
            setTarget(text);
            setOccurrence(selectedOccurrence);
          }}
        />
        {!passageMatchesDraft && <p className="doc-field-error">The saved draft refers to a passage that is no longer available. Choose the correct passage before adding it.</p>}
        <label>
          Exact words to change or insert beside
          <input
            value={target}
            disabled={busy}
            onChange={(event) => {
              if (passage) setPassageKey(passage.key);
              setTarget(event.target.value);
              setOccurrence(0);
            }}
            placeholder="Select words above, or type them here"
            required
            autoFocus
          />
        </label>
        {target && !matches.length && (
          <p className="doc-field-error">
            These words do not match the displayed passage exactly.
          </p>
        )}
        {matches.length > 1 && (
          <label>
            Which occurrence?
            <select
              value={occurrence}
              disabled={busy}
              onChange={(event) => setOccurrence(Number(event.target.value))}
            >
              {matches.map((start, index) => (
                <option key={start} value={index}>
                  Occurrence {index + 1} · character {start + 1}
                </option>
              ))}
            </select>
          </label>
        )}
        <div className="doc-form-pair">
          <label>
            Action
            <select
              value={operation}
              disabled={busy}
              onChange={(event) => setOperation(event.target.value)}
            >
              <option value="replace">Replace these words</option>
              <option value="delete">Delete these words</option>
              <option value="before">Insert before these words</option>
              <option value="after">Insert after these words</option>
            </select>
          </label>
          <label>
            Reviewer
            <input
              value={reviewer}
              disabled={busy}
              onChange={(event) => setReviewer(event.target.value)}
              maxLength={80}
              required
            />
          </label>
        </div>
        {operation !== "delete" && (
          <label>
            New text
            <textarea
              rows={3}
              disabled={busy}
              value={replacement}
              maxLength={2000}
              onChange={(event) => setReplacement(event.target.value)}
              required
              placeholder={
                operation === "before" || operation === "after"
                  ? "Include any space the insertion needs"
                  : "The replacement, exactly as it should read"
              }
            />
          </label>
        )}
        {passage && targetStart !== undefined && passageMatchesDraft && (
          <div className="doc-target-preview" aria-label="Exact correction preview" aria-live="polite">
            <div>
              <span className="doc-eyebrow">PRINTED · SELECTED WORDS</span>
              <p>{Array.from(passage.text).slice(0, targetStart - passage.start).join("")}<mark>{target}</mark>{Array.from(passage.text).slice(targetStart - passage.start + Array.from(target).length).join("")}</p>
            </div>
            <div>
              <span className="doc-eyebrow">AFTER THIS CORRECTION</span>
              {operation === "delete" || replacement ? (
                <p>{Array.from(passage.text).slice(0, targetStart - passage.start).join("")}{operation === "after" && target}{operation !== "delete" && <ins>{replacement}</ins>}{operation === "before" && target}{Array.from(passage.text).slice(targetStart - passage.start + Array.from(target).length).join("")}</p>
              ) : <p className="doc-preview-prompt">Enter the new text to preview the revised passage.</p>}
            </div>
          </div>
        )}
        {hasDraft && (
          <div className={`doc-manual-draft ${draftSaved ? "" : "not-saved"}`} role="status">
            <p>{draftSaved
              ? `${storedDraft ? "Draft restored. " : ""}Saved in this browser for this source. Closing keeps it; add it for review when ready.`
              : closeBlocked
                ? "This draft could not be saved in your browser. Keep this dialog open, add the correction, or explicitly discard the draft before closing."
                : "This browser could not save your draft. Keep this dialog open until you add the correction or discard it."}</p>
            <button type="button" className="doc-text-link" disabled={busy} onClick={discard}>Discard draft</button>
          </div>
        )}
        {(saveFailed || reloadNeeded) && <div className="doc-field-error" role="alert">
          <p>{requestError ?? "The correction could not be saved."} Your draft is still here.</p>
          {reloadNeeded && <button type="button" className="doc-text-link" disabled={busy} onClick={reloadReview}>Reload latest review</button>}
        </div>}
        <footer>
          <button
            type="button"
            className="doc-button"
            disabled={busy}
            onClick={requestClose}
          >
            {hasDraft ? "Close and keep draft" : "Close"}
          </button>
          <button
            className="doc-button primary"
            disabled={
              busy || matches[occurrence] === undefined || !paragraph?.editable || !passageMatchesDraft
            }
          >
            <Plus size={16} /> Add for review
          </button>
        </footer>
      </form>
    </dialog>
  );
}

function DraftPreview({ session }: { session: DocumentSession }) {
  const applicable = session.edits.filter(isExportableEdit);
  const hasBlockedSelection = session.edits.some((edit) => edit.decision === "paper" && edit.status !== "already_applied" && !isExportableEdit(edit));
  function paragraphContent(paragraph: DocumentParagraph) {
    const edits = applicable
      .filter(
        (edit) =>
          edit.currentParagraphId === paragraph.id &&
          edit.currentStart !== null &&
          edit.currentEnd !== null,
      )
      .sort((a, b) => (a.currentStart ?? 0) - (b.currentStart ?? 0));
    const characters = Array.from(paragraph.text);
    let cursor = 0;
    const parts = [];
    for (const edit of edits) {
      const start = edit.currentStart ?? 0;
      const end = edit.currentEnd ?? 0;
      parts.push(
        <span key={`${edit.id}-before`}>
          {characters.slice(cursor, start).join("")}
        </span>,
      );
      if (end > start)
        parts.push(
          <del key={`${edit.id}-delete`}>
            {characters.slice(start, end).join("")}
          </del>,
        );
      if (edit.replacement)
        parts.push(<ins key={`${edit.id}-insert`}>{edit.replacement}</ins>);
      cursor = end;
    }
    parts.push(<span key="rest">{characters.slice(cursor).join("")}</span>);
    return parts;
  }
  return (
    <section className="doc-draft-preview">
      <div className="doc-preview-heading">
        <span className="doc-eyebrow">LATEST DRAFT + YOUR DECISIONS</span>
        <span>
          <i /> Deleted <i /> Inserted
        </span>
      </div>
      <div className="doc-preview-paper">
        {session.currentParagraphs
          .filter((paragraph) => paragraph.text)
          .map((paragraph) =>
            /^subtitle$/i.test(paragraph.style) ? (
              <p className="doc-preview-subtitle" key={paragraph.id}>
                {paragraphContent(paragraph)}
              </p>
            ) : /title|heading/i.test(paragraph.style) ? (
              <h3 key={paragraph.id}>{paragraphContent(paragraph)}</h3>
            ) : (
              <p key={paragraph.id}>{paragraphContent(paragraph)}</p>
            ),
          )}
      </div>
      <p className="doc-fine-print">
        {hasBlockedSelection && "Selected corrections that still need attention are not shown as changes. "}
        Text preview. The Word export retains the current document’s supported
        formatting.
      </p>
    </section>
  );
}

function ExportStage({
  session,
  busy,
  exportDocument,
  review,
  refresh,
  refreshRequired,
}: {
  session: DocumentSession;
  busy: boolean;
  exportDocument: () => void;
  review: () => void;
  refresh: () => void;
  refreshRequired: boolean;
}) {
  const accepted = session.edits.filter(isExportableEdit);
  const alreadyPresent = session.edits.filter((edit) => edit.decision === "paper" && edit.status === "already_applied");
  const blocked = session.edits.filter((edit) => edit.decision === "paper" && edit.status !== "already_applied" && !isExportableEdit(edit));
  const pending = session.edits.filter((edit) => edit.decision === null);
  const unchecked = session.scans.filter((scan) => !scan.reviewed);
  const unresolvedMarks = session.scans.reduce(
    (count, scan) =>
      count + (scan.marks?.filter((mark) => !mark.resolution).length ?? 0),
    0,
  );
  const result = session.pendingCaptures > 0 || blocked.length > 0 || pending.length > 0 || unchecked.length > 0 || unresolvedMarks > 0 ? null : session.export;
  return (
    <div className="doc-export-layout">
      <div>
        <span className="doc-eyebrow">03 / RETURN TO WORD</span>
        <h1>
          {result
            ? "Your changes,\nback in the draft."
            : "One draft.\nEvery decision accounted for."}
        </h1>
        <p className="doc-section-intro">
          Download an editable Word file with genuine tracked changes. Accept or
          reject them in your word processor.
        </p>
        <div className={`doc-export-counts ${alreadyPresent.length ? "has-already-present" : ""}`}>
          <span>
            <b>{accepted.length}</b> paper change{accepted.length === 1 ? "" : "s"}
          </span>
          {alreadyPresent.length > 0 && <span><b>{alreadyPresent.length}</b> already in the latest draft</span>}
          <span>
            <b>
              {
                session.edits.filter((edit) => edit.decision === "current")
                  .length
              }
            </b>{" "}
            draft passages kept
          </span>
          <span>
            <b>
              {session.scans.filter((scan) => scan.reviewed).length}/
              {session.scans.length}
            </b>{" "}
            pages checked
          </span>
        </div>
        {result ? (
          <div className="doc-export-receipt">
            <div className="doc-receipt-top">
              <FileCheck2 size={26} />
              <div>
                <b>Export preservation checks passed</b>
                <p>
                  {result.changeCount} corrections ·{" "}
                  {result.verification.changedParagraphCount} paragraph
                  {result.verification.changedParagraphCount === 1 ? "" : "s"}
                </p>
              </div>
            </div>
            <a href={result.url} className="doc-button primary" download>
              <ArrowDownToLine size={17} /> Download tracked Word file
            </a>
            <a href={result.cleanUrl} className="doc-button" download>
              <FileText size={17} /> Download clean accepted copy
            </a>
            <div className="doc-receipt-links">
              <a href={result.inputUrl} download>
                Exact input file
              </a>
              <a href={result.auditUrl} download>
                Change receipt · JSON
              </a>
            </div>
            <p className="doc-fine-print">
              File checks verify the recorded changes and preserved package
              parts. They do not establish that a handwriting reading is
              correct.
            </p>
            <details>
              <summary>Inspect the verification</summary>
              <dl>
                <dt>Tracked revisions</dt>
                <dd>{result.verification.revisionCount}</dd>
                <dt>Unchanged package parts</dt>
                <dd>
                  {result.verification.unchangedPartCount} /{" "}
                  {result.verification.totalPartCount}
                </dd>
                <dt>Input SHA-256</dt>
                <dd className="doc-hash">{result.verification.currentHash}</dd>
                <dt>Output SHA-256</dt>
                <dd className="doc-hash">{result.verification.outputHash}</dd>
              </dl>
            </details>
          </div>
        ) : (
          <div className="doc-export-gate">
            {session.pendingCaptures > 0 ? (
              <>
                <LoaderCircle className="doc-spin" size={19} />
                <div>
                  <b>Wait for the photographs to finish</b>
                  <p>{session.pendingCaptures} photograph{session.pendingCaptures === 1 ? " is" : "s are"} still being processed. This review refreshes automatically. Check the resulting marks before exporting.</p>
                  <button className="doc-text-link" disabled={busy} onClick={refresh}>Refresh review <RotateCcw size={15} /></button>
                </div>
              </>
            ) : pending.length > 0 ||
            blocked.length > 0 ||
            unchecked.length > 0 ||
            unresolvedMarks > 0 ? (
              <>
                <CircleAlert size={19} />
                <div>
                  <b>Finish the review first</b>
                  <p>
                    {unresolvedMarks > 0
                      ? `${unresolvedMarks} detected pen mark${unresolvedMarks === 1 ? " still needs" : "s still need"} to be accounted for. `
                      : ""}
                    {pending.length > 0
                      ? `${pending.length} correction${pending.length === 1 ? " still needs" : "s still need"} a decision. `
                      : ""}
                    {blocked.length > 0
                      ? `${blocked.length} selected paper correction${blocked.length === 1 ? " still needs" : "s still need"} a confirmed reading or a safe target. `
                      : ""}
                    {unchecked.length > 0
                      ? `${unchecked.length} source page${unchecked.length === 1 ? " still needs" : "s still need"} checking.`
                      : ""}
                  </p>
                  <button className="doc-text-link" onClick={review}>
                    Return to review <ArrowRight size={15} />
                  </button>
                </div>
              </>
            ) : accepted.length ? (
              <>
                <CheckCheck size={20} />
                <div>
                  <b>Ready to create the Word files</b>
                  <p>
                    Only your selected corrections will be applied to the latest
                    draft.
                  </p>
                </div>
              </>
            ) : (
              <>
                <FileText size={20} />
                <div>
                  <b>{alreadyPresent.length ? "Selected text is already in the draft" : "No paper changes selected"}</b>
                  <p>
                    {alreadyPresent.length
                      ? "No new tracked changes are needed for these readings. Download the latest draft as it is."
                      : "Your latest draft can stay as it is. Add or accept a correction to create a revised file."}
                  </p>
                  <a
                    href={session.currentUrl}
                    className="doc-text-link"
                    download
                  >
                    Download latest draft
                  </a>
                </div>
              </>
            )}
          </div>
        )}
        {!result && (
          <button
            className="doc-button primary doc-export-primary"
            disabled={
              busy ||
              refreshRequired ||
              session.pendingCaptures > 0 ||
              pending.length > 0 ||
              blocked.length > 0 ||
              unchecked.length > 0 ||
              unresolvedMarks > 0 ||
              !accepted.length
            }
            onClick={exportDocument}
          >
            {busy ? (
              <LoaderCircle className="doc-spin" size={17} />
            ) : (
              <ArrowDownToLine size={17} />
            )}{" "}
            Create Word files
          </button>
        )}
      </div>
      <DraftPreview session={session} />
    </div>
  );
}

export default function DocumentApp() {
  const [session, setSession] = useState<DocumentSession | null>(null);
  const [stage, setStage] = useState<Stage>("prepare");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [reloadNeeded, setReloadNeeded] = useState(false);
  const [refreshWarning, setRefreshWarning] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [source, setSource] = useState("page:1");
  const [manual, setManual] = useState(false);
  const [manualMarkId, setManualMarkId] = useState<string | null>(null);
  const [reviewer, setReviewer] = useState("Reviewer");
  const [physicalCapture, setPhysicalCapture] = useState(false);
  const [filter, setFilter] = useState<"all" | "pending" | "conflicts">("all");
  const currentInput = useRef<HTMLInputElement>(null);
  const photoInput = useRef<HTMLInputElement>(null);
  const mounted = useRef(false);
  const requestVersion = useRef(0);
  const inFlight = useRef(false);
  const unpersistedReading = useRef(false);
  const protectReading = useCallback((blocked: boolean) => {
    unpersistedReading.current = blocked;
    if (!blocked) setNotice((value) => value === unsavedReadingNotice ? null : value);
  }, []);
  const canLeaveReading = () => {
    if (!unpersistedReading.current) return true;
    setNotice(unsavedReadingNotice);
    return false;
  };
  const changeStage = (value: Stage) => {
    if (value === stage || canLeaveReading()) setStage(value);
  };

  useEffect(() => {
    mounted.current = true;
    const controller = new AbortController();
    const id = new URLSearchParams(location.search).get("document");
    if (id) {
      const version = ++requestVersion.current;
      inFlight.current = true;
      setBusy("loading");
      documentRequest(`/api/documents/${encodeURIComponent(id)}`, { signal: controller.signal })
        .then((value) => {
          if (!mounted.current || version !== requestVersion.current) return;
          setSession(value);
          setStage(value.export ? "export" : value.edits.length || value.scans.length ? "review" : "prepare");
          const edit = value.edits.find((item) => !item.decision) ?? value.edits[0];
          setSelectedId(edit?.id ?? null);
          setSource(edit?.scanId ?? value.scans.at(-1)?.id ?? "page:1");
        })
        .catch((cause) => {
          if (mounted.current && version === requestVersion.current) setError(documentError(cause));
        })
        .finally(() => {
          if (mounted.current && version === requestVersion.current) {
            inFlight.current = false;
            setBusy(null);
          }
        });
    }
    return () => {
      mounted.current = false;
      inFlight.current = false;
      requestVersion.current += 1;
      controller.abort();
    };
  }, []);

  const adopt = (value: DocumentSession) => {
    setSession(value);
    const url = new URL(location.href);
    url.search = "";
    url.searchParams.set("document", value.id);
    history.replaceState(null, "", url);
  };
  const perform = async (
    label: string,
    request: () => Promise<DocumentSession>,
    onSuccess?: (value: DocumentSession) => void,
  ): Promise<boolean> => {
    if (inFlight.current || !mounted.current) return false;
    const version = ++requestVersion.current;
    inFlight.current = true;
    setBusy(label);
    setError(null);
    setNotice(null);
    setReloadNeeded(false);
    try {
      const value = await request();
      if (!mounted.current || version !== requestVersion.current) return false;
      adopt(value);
      setRefreshWarning(null);
      onSuccess?.(value);
      return true;
    } catch (cause) {
      if (mounted.current && version === requestVersion.current) {
        setError(documentError(cause));
        setReloadNeeded(cause instanceof DocumentRequestError && cause.status === 409);
      }
      return false;
    } finally {
      if (mounted.current && version === requestVersion.current) {
        inFlight.current = false;
        setBusy(null);
      }
    }
  };
  const start = async (file?: File, trial = false) => {
    if (!canLeaveReading()) return;
    const form = new FormData();
    if (file) form.append("file", file);
    await perform(file ? "upload" : trial ? "trial" : "example", () => documentRequest(
      file ? "/api/documents" : trial ? "/api/documents/trial" : "/api/documents/example",
      { method: "POST", ...(file ? { body: form } : {}) },
    ), (value) => {
      setStage(file || trial ? "prepare" : "review");
      setPhysicalCapture(false);
      setFilter("all");
      setManual(false);
      setManualMarkId(null);
      const edit = value.edits[0];
      setSelectedId(edit?.id ?? null);
      setSource(edit?.scanId ?? "page:1");
    });
  };
  const reloadReview = async () => {
    if (!session) return;
    await perform("reload", () => documentRequest(`/api/documents/${session.id}`), (value) => {
      if (!value.edits.some((edit) => edit.id === selectedId)) {
        const edit = value.edits.find((item) => !item.decision) ?? value.edits[0];
        setSelectedId(edit?.id ?? null);
      }
      if (!source.startsWith("page:") && !value.scans.some((scan) => scan.id === source)) setSource("page:1");
      setNotice("Latest review loaded. Your reading drafts are retained; check the updated passages before saving.");
    });
  };
  const mutate: Mutation = async (path, body, method = "POST") => {
    if (!session) return false;
    return perform(path, () => documentRequest(`/api/documents/${session.id}${path}`, {
      method,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...body, revision: session.revision }),
    }), (value) => {
      if (path === "/edits") {
        const added = value.edits.at(-1);
        if (added) {
          setSelectedId(added.id);
          setSource(added.scanId ?? `page:${added.page ?? 1}`);
        }
      }
    });
  };
  const uploadCurrent = async (file: File) => {
    if (!session || !canLeaveReading()) return;
    const form = new FormData();
    form.append("file", file);
    form.append("revision", String(session.revision));
    await perform("current", () => documentRequest(`/api/documents/${session.id}/current`, { method: "POST", body: form }), (value) => {
      const unchanged = value.revision === session.revision && value.currentHash === session.currentHash;
      setNotice(unchanged
        ? "This file already matches the latest draft. Your review decisions are unchanged."
        : "Latest draft loaded. Review the updated matches and decisions before exporting.");
      if (!unchanged) setStage("review");
    });
  };
  const uploadPhoto = async (file: File) => {
    if (!session || !canLeaveReading()) return;
    const form = new FormData();
    form.append("file", file);
    form.append("reviewer", reviewer.trim() || "Reviewer");
    form.append("captureKind", physicalCapture ? "physical" : "unspecified");
    await perform("photo", () => documentRequest(`/api/documents/${session.id}/scans`, { method: "POST", body: form }), (value) => {
      const scan = value.scans.at(-1);
      const edit = value.edits.find((item) => item.scanId === scan?.id);
      setSource(scan?.id ?? source);
      setSelectedId(edit?.id ?? null);
      setStage("review");
      const marks = scan?.marks?.filter((mark) => !mark.resolution).length ?? 0;
      setNotice(edit
        ? "Photo processed. Check every suggested correction and inspect the full page for missed writing."
        : marks
          ? `${marks} pen-mark ${marks === 1 ? "region needs" : "regions need"} your reading. Enter the intended correction, or mark an annotation as not changing the text.`
          : "No corrections were detected on this photo. Inspect the source and enter missed marks manually, or try a clearer photograph.");
    });
  };

  useEffect(() => {
    if (!session || reloadNeeded) return;
    const sessionId = session.id;
    let stopped = false;
    let polling = false;
    let controller: AbortController | null = null;
    const refresh = async () => {
      if (stopped || polling || inFlight.current || document.visibilityState === "hidden") return;
      polling = true;
      controller = new AbortController();
      const version = requestVersion.current;
      try {
        const value = await documentRequest(`/api/documents/${sessionId}`, { signal: controller.signal });
        if (stopped || !mounted.current || inFlight.current || version !== requestVersion.current) return;
        setSession((current) => current?.id === sessionId && value.revision >= current.revision ? value : current);
        setRefreshWarning(null);
      } catch {
        if (!stopped && mounted.current && version === requestVersion.current) setRefreshWarning("The latest review could not be refreshed. Reload it before exporting.");
      } finally {
        polling = false;
      }
    };
    const interval = window.setInterval(refresh, session.pendingCaptures > 0 || stage === "export" ? 2000 : 5000);
    const onVisible = () => { if (document.visibilityState === "visible") void refresh(); };
    window.addEventListener("focus", refresh);
    document.addEventListener("visibilitychange", onVisible);
    if (stage === "export") void refresh();
    return () => {
      stopped = true;
      controller?.abort();
      window.clearInterval(interval);
      window.removeEventListener("focus", refresh);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [session?.id, session?.pendingCaptures, stage, reloadNeeded]);
  const selected =
    session?.edits.find((edit) => edit.id === selectedId) ?? null;
  const activeScan = session?.scans.find((scan) => scan.id === source);
  const unresolvedMark = activeScan?.marks?.find((mark) => !mark.resolution);
  const openManual = (markId?: string) => {
    if (!canLeaveReading()) return;
    setManualMarkId(markId ?? null);
    setManual(true);
  };
  const resolveMark = (
    scanId: string,
    markId: string,
    resolution: "ignored" | "correction" | null,
    editId?: string,
  ) => mutate(`/scans/${scanId}/marks/${markId}`, { resolution, ...(editId ? { editId } : {}) }, "PATCH");
  const selectEdit = (edit: DocumentEdit) => {
    if (edit.id !== selectedId && !canLeaveReading()) return;
    setSelectedId(edit.id);
    setSource(edit.scanId ?? `page:${edit.page ?? 1}`);
  };
  const selectSource = (nextSource: string) => {
    const edits =
      session?.edits.filter((edit) => edit.scanId === nextSource) ?? [];
    const edit = edits.find((item) => item.decision === null) ?? edits[0];
    if (edit?.id !== selectedId && !canLeaveReading()) return;
    setSource(nextSource);
    setSelectedId(edit?.id ?? null);
  };
  const pendingCount =
    session?.edits.filter((edit) => !edit.decision).length ?? 0;
  const filteredEdits =
    session?.edits.filter(
      (edit) =>
        filter === "all" ||
        (filter === "pending"
          ? !edit.decision
          : ["conflict", "overlap", "missing", "protected"].includes(
              edit.status,
            ) && !edit.decision),
    ) ?? [];
  const selectedIndex = session?.edits.findIndex((edit) => edit.id === selectedId) ?? -1;
  const previousEdit = session?.edits
    .slice(0, Math.max(0, selectedIndex))
    .reverse()
    .find((edit) => filteredEdits.includes(edit));
  const nextEdit = session?.edits
    .slice(selectedIndex + 1)
    .find((edit) => filteredEdits.includes(edit));

  return (
    <div className="doc-app">
      <a className="doc-skip" href="#doc-main">
        Skip to document
      </a>
      <header className="doc-header">
        <a className="doc-brand" href="/?mode=documents" onClick={(event) => { if (!canLeaveReading()) event.preventDefault(); }}>
          <Mark />
          <span>
            PaperPatch<span className="doc-brand-period">.</span>
          </span>
        </a>
        <div className="doc-header-context">
          {session ? (
            <>
              <FileText size={15} />
              <span>{session.filename}</span>
              <span className="doc-header-mode">DOCUMENT REVIEW</span>
            </>
          ) : (
            <span>THE PAPER IS STILL PART OF THE DOCUMENT.</span>
          )}
        </div>
        <a className="doc-legacy-link" href="/?mode=workbooks" onClick={(event) => { if (!canLeaveReading()) event.preventDefault(); }}>
          Spreadsheet tool <ArrowRight size={14} />
        </a>
      </header>
      {error && (
        <div className="doc-message error" role="alert">
          <CircleAlert size={18} />
          <span>{error}</span>
          {reloadNeeded && session && <button className="doc-text-link" disabled={!!busy} onClick={reloadReview}>Reload latest review</button>}
          <button aria-label="Dismiss error" onClick={() => { setError(null); setReloadNeeded(false); }}>
            <X size={17} />
          </button>
        </div>
      )}
      {refreshWarning && (
        <div className="doc-message error" role="alert">
          <CircleAlert size={18} /><span>{refreshWarning}</span>
          <button className="doc-text-link" disabled={!!busy} onClick={reloadReview}>Reload latest review</button>
        </div>
      )}
      {!!session?.pendingCaptures && stage !== "export" && (
        <div className="doc-message notice" role="status">
          <LoaderCircle className="doc-spin" size={17} />
          <span>{session.pendingCaptures} photograph{session.pendingCaptures === 1 ? " is" : "s are"} still being processed. This review refreshes automatically.</span>
        </div>
      )}
      {notice && (
        <div className="doc-message notice" role="status">
          <Check size={17} />
          <span>{notice}</span>
          <button aria-label="Dismiss message" onClick={() => setNotice(null)}>
            <X size={17} />
          </button>
        </div>
      )}
      {busy && (
        <div className="doc-busy" role="status">
          <LoaderCircle className="doc-spin" size={16} />
          {busy === "photo"
            ? "Aligning the page and reading colored pen marks. This may take a minute…"
            : busy === "loading" || busy === "reload"
              ? "Opening your document review…"
              : busy === "current"
                ? "Matching corrections to the latest draft…"
                : busy === "/export"
                  ? "Creating Word revisions and checking preservation…"
                  : busy === "upload"
                    ? "Preparing your print copy…"
                    : busy === "example"
                      ? "Preparing the working example…"
                      : busy === "trial"
                        ? "Preparing a clean page for your real-pen check…"
                        : "Saving your review…"}
        </div>
      )}
      {!session ? (
        <div id="doc-main">
          <Landing
            busy={busy}
            onExample={() => start()}
            onTrial={() => start(undefined, true)}
            onUpload={(file) => start(file)}
          />
        </div>
      ) : (
        <main id="doc-main" className="doc-workspace">
          <div className="doc-workflow-bar">
            <nav aria-label="Document workflow">
              {(
                [
                  ["prepare", "01", "Prepare"],
                  ["review", "02", "Review"],
                  ["export", "03", "Return to Word"],
                ] as const
              ).map(([value, number, label]) => (
                <button
                  key={value}
                  aria-current={stage === value ? "step" : undefined}
                  className={stage === value ? "active" : ""}
                  onClick={() => changeStage(value)}
                >
                  <span>{number}</span>
                  {label}
                  {value === "review" && pendingCount > 0 && (
                    <b>{pendingCount}</b>
                  )}
                </button>
              ))}
            </nav>
            <div className="doc-current-file">
              <span className="doc-current-dot" /> Latest:{" "}
              <b title={session.currentFilename}>{session.currentFilename}</b>
              <button
                onClick={() => currentInput.current?.click()}
                disabled={!!busy}
              >
                Update <Upload size={13} />
              </button>
            </div>
          </div>
          <input
            ref={currentInput}
            type="file"
            accept=".docx"
            hidden
            onChange={(event) => {
              const file = event.target.files?.[0];
              if (file) uploadCurrent(file);
              event.target.value = "";
            }}
          />
          <input
            ref={photoInput}
            type="file"
            accept="image/jpeg,image/png,image/webp"
            hidden
            onChange={(event) => {
              const file = event.target.files?.[0];
              if (file) uploadPhoto(file);
              event.target.value = "";
            }}
          />
          {session.demo && (
            <div className="doc-session-example">
              <span className="doc-example-label">WORKING EXAMPLE</span>
              <p>
                Two reviewers marked the printed draft. The author also changed
                the digital copy. Choose what survives.
              </p>
              <a href={session.baselineUrl} download>
                Original Word file <ArrowDownToLine size={13} />
              </a>
            </div>
          )}
          {session.trial && (
            <section className="doc-trial-bar" aria-label="Real-pen check">
              <div>
                <span className="doc-example-label">REAL-PEN CHECK</span>
                <b>
                  {session.scans.length
                    ? "First photograph recorded"
                    : "Awaiting your photograph"}
                </b>
                <p>
                  {session.scans.length
                    ? "Initial recognition is recorded separately from your review. Use a fresh check for another attempt; this one stays saved."
                    : `Print this page, make the ${session.trial.expectedCount} edits below, then upload one photograph. This starts with no marks or prepared readings.`}
                </p>
              </div>
              {session.scans.length > 0 ? (
                <button
                  className="doc-button compact"
                  disabled={!!busy}
                  onClick={() => start(undefined, true)}
                >
                  <RotateCcw size={15} /> Start a fresh check
                </button>
              ) : (
                <div className="doc-capture-attestation">
                  <label>
                    <input
                      type="checkbox"
                      checked={physicalCapture}
                      disabled={!!busy}
                      onChange={(event) =>
                        setPhysicalCapture(event.target.checked)
                      }
                    />
                    <span>
                      I printed this page and made these marks by hand
                    </span>
                  </label>
                  <p>
                    This records your report, not independent verification.
                    Leave unchecked for any other image.
                  </p>
                </div>
              )}
            </section>
          )}
          {session.warnings.length > 0 && (
            <details className="doc-session-warnings">
              <summary>
                <CircleAlert size={15} /> {session.warnings.length} document
                note{session.warnings.length === 1 ? "" : "s"}
              </summary>
              {session.warnings.map((warning, index) => (
                <p key={index}>{warning}</p>
              ))}
            </details>
          )}
          {stage === "prepare" && (
            <div className="doc-prepare-layout">
              <section className="doc-prepare-copy">
                <span className="doc-eyebrow">01 / TAKE IT TO PAPER</span>
                <h1>
                  {session.trial ? (
                    <>
                      Try your own
                      <br />
                      handwriting.
                    </>
                  ) : (
                    <>
                      Make room
                      <br />
                      for your edits.
                    </>
                  )}
                </h1>
                <p className="doc-section-intro">
                  Print this review copy, then mark it with a red or blue pen.
                  The original Word file stays intact.
                </p>
                <a
                  className="doc-button primary"
                  href={session.printUrl}
                  target="_blank"
                  rel="noreferrer"
                >
                  <Printer size={18} /> Open printable PDF
                </a>
                {session.trial && (
                  <div className="doc-trial-instructions">
                    <span className="doc-eyebrow">
                      MAKE THESE {session.trial.expectedCount} EDITS
                    </span>
                    <ol>
                      {session.trial.instructions.map((instruction, index) => (
                        <li key={`${instruction.paragraphId}:${index}`}>
                          <span>
                            {instruction.kind === "delete"
                              ? "Cross out"
                              : "Replace"}{" "}
                            <b>{instruction.before}</b>
                          </span>
                          {instruction.kind === "replace" ? (
                            <>
                              <ArrowRight size={13} />
                              <ins>{instruction.after}</ins>
                            </>
                          ) : (
                            <em>No replacement</em>
                          )}
                        </li>
                      ))}
                    </ol>
                    <p>
                      Use a red or blue pen. Keep every other passage unchanged.
                      The expected words are used to compare results, not to
                      fill in the handwriting reading.
                    </p>
                  </div>
                )}
                <ol className="doc-instructions">
                  <li>
                    <b>Cross out words to remove them.</b>
                    <p>
                      Make a clear line through the text you want to change.
                    </p>
                  </li>
                  <li>
                    <b>Write the replacement directly above.</b>
                    <p>
                      Keep it on one line, just above the crossed-out words.
                      You’ll confirm every reading.
                    </p>
                  </li>
                  <li>
                    <b>Photograph the whole page.</b>
                    <p>
                      Keep the four corner markers and QR code visible, in focus
                      and without glare.
                    </p>
                  </li>
                </ol>
                <div className="doc-upload-block">
                  <label>
                    Whose edits are these?
                    <input
                      value={reviewer}
                      onChange={(event) => setReviewer(event.target.value)}
                      maxLength={80}
                    />
                  </label>
                  <button
                    className="doc-button"
                    disabled={
                      !!busy || (!!session.trial && session.scans.length > 0)
                    }
                    onClick={() => photoInput.current?.click()}
                  >
                    <ImagePlus size={17} /> Add a marked page
                  </button>
                  <p className="doc-fine-print">
                    JPG, PNG or WebP · one page per photo · up to 20 MB and 25
                    megapixels
                  </p>
                  <p className="doc-fine-print">
                    Take a phone photo, then transfer or export it as a JPEG to
                    this computer to upload. Keep the whole page and all four
                    markers visible.
                  </p>
                </div>
                <div className="doc-latest-note">
                  <span className="doc-eyebrow">THE DRAFT MOVED ON?</span>
                  <p>
                    Upload the newer Word file. We’ll compare each paper
                    correction with the text that is there now.
                  </p>
                  <button
                    className="doc-text-link"
                    disabled={!!busy}
                    onClick={() => currentInput.current?.click()}
                  >
                    Choose latest draft <ArrowRight size={14} />
                  </button>
                </div>
              </section>
              <ProofViewer
                session={session}
                selected={selected}
                source={source}
                setSource={selectSource}
                selectEdit={selectEdit}
                busy={!!busy}
                onReview={(id) => mutate(`/scans/${id}/review`, {})}
                onResolveMark={resolveMark}
                onManual={openManual}
              />
            </div>
          )}
          {stage === "review" && (
            <>
              <div className="doc-review-heading">
                <div>
                  <span className="doc-eyebrow">02 / EVERY MARK HAS A SAY</span>
                  <h1>Bring the drafts together.</h1>
                </div>
                <div className="doc-review-heading-actions">
                  <label>
                    <span className="doc-sr-only">Reviewer for next photo</span>
                    <input
                      aria-label="Reviewer for next photo"
                      value={reviewer}
                      onChange={(event) => setReviewer(event.target.value)}
                      maxLength={80}
                    />
                  </label>
                  <button
                    className="doc-button compact"
                    disabled={
                      !!busy || (!!session.trial && session.scans.length > 0)
                    }
                    onClick={() => photoInput.current?.click()}
                  >
                    <ImagePlus size={16} /> Add photo
                  </button>
                  <button
                    className="doc-button compact"
                    disabled={
                      !!busy ||
                      !session.paragraphs.some(
                        (paragraph) => paragraph.editable,
                      )
                    }
                    onClick={() => openManual()}
                  >
                    <Plus size={16} /> Add correction
                  </button>
                </div>
              </div>
              <div className="doc-review-layout">
                <aside className="doc-change-rail" aria-label="Corrections">
                  <div className="doc-change-rail-top">
                    <b>
                      {session.edits.length} correction
                      {session.edits.length === 1 ? "" : "s"}
                    </b>
                    <span>{pendingCount} to review</span>
                  </div>
                  <div
                    className="doc-rail-filters"
                    role="group"
                    aria-label="Filter corrections"
                  >
                    {(["all", "pending", "conflicts"] as const).map((value) => (
                      <button
                        key={value}
                        className={filter === value ? "active" : ""}
                        aria-pressed={filter === value}
                        onClick={() => setFilter(value)}
                      >
                        {value === "all"
                          ? "All"
                          : value === "pending"
                            ? "To review"
                            : "Conflicts"}
                      </button>
                    ))}
                  </div>
                  <div className="doc-change-items">
                    {filteredEdits.map((edit) => (
                      <button
                        className={`doc-change-item ${edit.id === selected?.id ? "selected" : ""} ${edit.decision ? "decided" : ""}`}
                        key={edit.id}
                        onClick={() => selectEdit(edit)}
                        aria-pressed={edit.id === selected?.id}
                      >
                        <span className="doc-change-number">
                          {edit.decision ? (
                            <Check size={13} />
                          ) : (
                            String(
                              session.edits.findIndex(
                                (item) => item.id === edit.id,
                              ) + 1,
                            ).padStart(2, "0")
                          )}
                        </span>
                        <div>
                          <div className="doc-change-meta">
                            <span>{edit.reviewer}</span>
                            <span>{editAction(edit)}</span>
                          </div>
                          <p>
                            {edit.baseText || edit.replacement || "Insertion"}
                          </p>
                          <span
                            className={`doc-edit-status ${!edit.decision && ["conflict", "overlap", "missing", "protected"].includes(edit.status) ? "conflict" : ""}`}
                          >
                            {editLabel(edit)}
                          </span>
                        </div>
                      </button>
                    ))}
                    {!filteredEdits.length && (
                      <div className="doc-rail-empty">
                        <CheckCheck size={22} />
                        <p>
                          {session.edits.length
                            ? "No corrections in this view."
                            : session.scans.length
                              ? "No text corrections yet. Check the source page and enter the changes you find."
                            : "Add a marked page to begin. You can also enter a correction manually."}
                        </p>
                      </div>
                    )}
                  </div>
                  <button
                    className="doc-rail-export"
                    onClick={() => changeStage("export")}
                  >
                    Preview Word changes <ArrowRight size={16} />
                  </button>
                </aside>
                <ProofViewer
                  session={session}
                  selected={selected}
                  source={source}
                  setSource={selectSource}
                  selectEdit={selectEdit}
                  busy={!!busy}
                  onReview={(id) => mutate(`/scans/${id}/review`, {})}
                  onResolveMark={resolveMark}
                  onManual={openManual}
                />
                {selected ? (
                  <EditInspector
                    key={`${session.id}:${selected.id}`}
                    session={session}
                    edit={selected}
                    busy={!!busy}
                    mutate={mutate}
                    hasPrevious={!!previousEdit}
                    hasNext={!!nextEdit}
                    previous={() => previousEdit && selectEdit(previousEdit)}
                    next={() => nextEdit && selectEdit(nextEdit)}
                    onManual={openManual}
                    onDraftProtectionChange={protectReading}
                  />
                ) : (
                  <aside className="doc-inspector doc-inspector-empty">
                    <PenLine size={30} strokeWidth={1.3} />
                    <h2>
                      {unresolvedMark
                        ? "This mark needs your reading."
                        : activeScan
                        ? "No correction selected."
                        : "Start with a mark."}
                    </h2>
                    <p>
                      {unresolvedMark
                        ? "The ink is preserved on the page. Choose the words it changes and enter the correction, or use “Not an edit” in the pen-mark list."
                        : activeScan
                        ? "Inspect this photograph for marks the scanner missed. Enter a correction yourself, or select an existing one from the list."
                        : "Add a photo of your printed review copy, or select a correction from the list."}
                    </p>
                    {activeScan && (
                      <button
                        className="doc-button"
                        disabled={!!busy}
                        onClick={() => openManual(unresolvedMark?.id)}
                      >
                        <Plus size={15} /> {unresolvedMark ? "Read the first pen mark" : "Enter a missed correction"}
                      </button>
                    )}
                    <button
                      className="doc-button"
                      disabled={!!busy}
                      onClick={() =>
                        activeScan && session.trial
                          ? start(undefined, true)
                          : activeScan
                            ? photoInput.current?.click()
                            : changeStage("prepare")
                      }
                    >
                      {activeScan ? (
                        <RotateCcw size={15} />
                      ) : (
                        <ArrowLeft size={15} />
                      )}
                      {activeScan
                        ? session.trial
                          ? "Start a fresh check"
                          : "Try a clearer photograph"
                        : "Get the print copy"}
                    </button>
                  </aside>
                )}
              </div>
            </>
          )}
          {stage === "export" && (
            <ExportStage
              session={session}
              busy={!!busy}
              refreshRequired={!!refreshWarning || reloadNeeded}
              review={() => changeStage("review")}
              refresh={reloadReview}
              exportDocument={() =>
                mutate("/export", { currentHash: session.currentHash })
              }
            />
          )}
          <footer className="doc-workspace-footer">
            <span>Saved on this computer</span>
            <span>Original files preserved · Every write reviewed</span>
          </footer>
          {manual && (
            <ManualDialog
              session={session}
              busy={!!busy}
              close={() => setManual(false)}
              mutate={mutate}
              initialScanId={activeScan?.id ?? ""}
              initialReviewer={activeScan?.reviewer ?? reviewer}
              initialMarkId={manualMarkId}
              requestError={error}
              reloadNeeded={reloadNeeded}
              reloadReview={reloadReview}
            />
          )}
        </main>
      )}
    </div>
  );
}
