import { useEffect, useRef, useState } from "react";
import {
  ArrowDownToLine,
  ArrowRight,
  Check,
  ChevronLeft,
  ChevronRight,
  FileImage,
  LoaderCircle,
  RotateCcw,
  Upload,
  X,
  ZoomIn,
  ZoomOut,
} from "lucide-react";
import {
  exportReceiptSchema,
  type Cell,
  type ExportPreview,
  type ExportReceipt,
  type ManualTarget,
  type Scan,
  type Session,
} from "./types";
import "./review-tools.css";

type ScanInboxProps = {
  session: Session;
  busy: boolean;
  onUpload: (files: File[]) => void;
  onRetry: (scanId: string) => void;
  onReview: (rowId: string, field: string) => void;
  onTranscribe: (target: ManualTarget) => void;
  onCheckPage: (scanId: string) => void;
  initialScanId?: string | null;
};

const valueText = (value: Pick<Cell, "value">) =>
  value.value === null || value.value === "" ? "Empty" : String(value.value);

function rowName(session: Session, rowId: string) {
  const table = session.current ?? session.base;
  const row = table?.rows.find((entry) => entry.id === rowId);
  const field = table?.headers.find(
    (header) =>
      header !== session.config?.idColumn &&
      !session.config?.editableColumns.includes(header) &&
      row?.cells[header]?.kind === "text" &&
      row.cells[header].value,
  );
  return field && row ? valueText(row.cells[field]) : (row?.label ?? rowId);
}

function timestamp(value: string | null) {
  if (!value) return "Earlier export";
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? value
    : date.toLocaleString(undefined, {
        dateStyle: "medium",
        timeStyle: "short",
      });
}

function scanState(scan: Scan, readingCount: number) {
  if (scan.status === "ready")
    return `${readingCount} reading${readingCount === 1 ? "" : "s"}`;
  if (scan.status === "failed") return "Needs attention";
  return scan.stage || (scan.status === "queued" ? "Queued" : "Processing");
}

export function ScanInbox({
  session,
  busy,
  onUpload,
  onRetry,
  onReview,
  onTranscribe,
  onCheckPage,
  initialScanId,
}: ScanInboxProps) {
  const input = useRef<HTMLInputElement>(null);
  const pageArea = useRef<HTMLDivElement>(null);
  const [selectedId, setSelectedId] = useState<string | null>(
    initialScanId ?? null,
  );
  const [selectedBoxId, setSelectedBoxId] = useState<string | null>(null);
  const [showOriginal, setShowOriginal] = useState(false);
  const [zoomed, setZoomed] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  useEffect(() => {
    if (initialScanId) {
      setSelectedId(initialScanId);
      setSelectedBoxId(null);
      setShowOriginal(false);
      setZoomed(false);
    }
  }, [initialScanId]);
  const scan =
    session.scans.find((entry) => entry.id === selectedId) ??
    session.scans.at(-1);
  const box = scan?.boxes.find((entry) => entry.boxId === selectedBoxId);
  const boxIndex =
    scan?.boxes.findIndex((entry) => entry.boxId === selectedBoxId) ?? -1;
  const linkedItem =
    box &&
    session.items.find(
      (item) => item.rowId === box.rowId && item.field === box.field,
    );
  const savedReading = linkedItem?.candidates
    .filter(
      (candidate) =>
        candidate.scanId === scan?.id &&
        (!candidate.boxId || candidate.boxId === box?.boxId),
    )
    .at(-1);
  const manuallyTranscribed = savedReading?.transcriptionState === "corrected";
  const boxRow =
    box &&
    (session.current ?? session.base)?.rows.find((row) => row.id === box.rowId);
  const cellProtected = Boolean(box && boxRow?.cells[box.field]?.protected);
  const completedPages = new Set(
    session.scans
      .filter((entry) => entry.status === "ready" && entry.page !== null)
      .map((entry) => entry.page),
  );
  const totalPages = session.manifest?.pages.length ?? 0;
  const activeCount = session.scans.filter(
    (entry) => entry.status === "processing" || entry.status === "queued",
  ).length;
  const uncheckedCount = session.scans.filter(
    (entry) => entry.status === "ready" && !entry.reviewedAt,
  ).length;
  const imageUrl =
    scan &&
    (showOriginal
      ? (scan.originalUrl ?? scan.alignedUrl)
      : (scan.alignedUrl ?? scan.originalUrl));
  const canOverlay = scan?.alignedUrl && !showOriginal;
  const warnings = [
    ...new Set([...(scan?.warnings ?? []), ...(scan?.quality?.warnings ?? [])]),
  ].filter(
    (warning) =>
      !session.demo || !warning.startsWith("Prepared example values."),
  );

  useEffect(() => {
    const area = pageArea.current;
    const selected =
      area?.querySelector<HTMLButtonElement>(".scan-box.selected");
    if (!area || !selected) return;
    const bounds = selected.getBoundingClientRect();
    const frame = area.getBoundingClientRect();
    area.scrollTo({
      left:
        area.scrollLeft +
        bounds.left -
        frame.left -
        (area.clientWidth - bounds.width) / 2,
      top:
        area.scrollTop +
        bounds.top -
        frame.top -
        (area.clientHeight - bounds.height) / 2,
      behavior: "smooth",
    });
  }, [selectedBoxId, zoomed, showOriginal]);

  const upload = (files: File[]) => {
    if (busy || !session.manifest || !files.length) return;
    if (files.length > 12)
      return setUploadError("Choose up to 12 photographs at a time.");
    const invalid = files.find(
      (file) =>
        !/\.(png|jpe?g|webp)$/i.test(file.name) ||
        !file.size ||
        file.size > 20 * 1024 * 1024,
    );
    if (invalid)
      return setUploadError(
        `${invalid.name}: choose a nonempty PNG, JPEG, or WebP image under 20 MB.`,
      );
    setUploadError(null);
    onUpload(files);
  };

  const selectScan = (id: string) => {
    setSelectedId(id);
    setSelectedBoxId(null);
    setShowOriginal(false);
    setZoomed(false);
  };

  return (
    <section className="scan-inbox" aria-labelledby="scan-inbox-title">
      <div className="scan-inbox-heading">
        <div>
          <h2 id="scan-inbox-title">Returned paper</h2>
          <p>
            Check each page for missed writing, then review its corrections.
          </p>
        </div>
        <span className="scan-coverage">
          {completedPages.size} of {totalPages} printed pages received
          {activeCount > 0 && ` · ${activeCount} processing`}
          {uncheckedCount > 0 && ` · ${uncheckedCount} awaiting page check`}
        </span>
      </div>
      <div
        className={`scan-upload-strip ${dragging ? "is-dragging" : ""}`}
        onDragOver={(event) => {
          event.preventDefault();
          if (!busy) setDragging(true);
        }}
        onDragLeave={(event) => {
          if (!event.currentTarget.contains(event.relatedTarget as Node | null))
            setDragging(false);
        }}
        onDrop={(event) => {
          event.preventDefault();
          setDragging(false);
          upload(Array.from(event.dataTransfer.files));
        }}
      >
        <input
          ref={input}
          type="file"
          accept="image/png,image/jpeg,image/webp"
          multiple
          hidden
          onChange={(event) => {
            upload(Array.from(event.target.files ?? []));
            event.target.value = "";
          }}
        />
        <button
          className="button"
          type="button"
          disabled={busy || !session.manifest}
          onClick={() => input.current?.click()}
        >
          <Upload size={16} /> Add photographs
        </button>
        <div className="scan-capture-guidance">
          <p>
            One page per photo. Include all four corner markers and a sharp QR
            code.
          </p>
          <span>
            Keep the paper flat and evenly lit. Up to 12 PNG, JPEG or WebP
            images · 20 MB and 25 megapixels each
          </span>
        </div>
        {uploadError && (
          <p className="scan-upload-error" role="alert">
            {uploadError}
          </p>
        )}
      </div>
      {totalPages > 1 && (
        <div className="scan-page-coverage" aria-label="Printed page coverage">
          {session.manifest?.pages.map((page) => (
            <span
              key={page.number}
              className={completedPages.has(page.number) ? "is-received" : ""}
            >
              {completedPages.has(page.number) && <Check size={12} />} Page{" "}
              {page.number}
              {completedPages.has(page.number) ? " received" : " not received"}
            </span>
          ))}
        </div>
      )}
      {session.scans.length === 0 ? (
        <div className="scan-empty">
          <FileImage size={28} strokeWidth={1.3} />
          <h3>No photographs yet</h3>
          <p>
            Upload complete pages with all four corner markers visible. You can
            inspect every correction box, including boxes detected as blank.
          </p>
          {session.items.length > 0 && (
            <p>
              Existing readings remain available in Review. They have no
              full-page photograph attached.
            </p>
          )}
        </div>
      ) : (
        <div className="scan-workspace">
          <aside className="scan-list" aria-label="Uploaded photographs">
            {session.scans.map((entry, index) => (
              <div
                key={entry.id}
                className={`scan-list-row ${scan?.id === entry.id ? "selected" : ""}`}
              >
                <button
                  type="button"
                  className="scan-select"
                  aria-current={scan?.id === entry.id ? "true" : undefined}
                  onClick={() => selectScan(entry.id)}
                >
                  <span className="scan-number">
                    {String(index + 1).padStart(2, "0")}
                  </span>
                  <span className="scan-list-copy">
                    <strong title={entry.filename}>{entry.filename}</strong>
                    <span>
                      {entry.page !== null ? `Page ${entry.page} · ` : ""}
                      {scanState(
                        entry,
                        Math.max(
                          entry.readingCount,
                          session.items.filter((item) =>
                            item.candidates.some(
                              (candidate) => candidate.scanId === entry.id,
                            ),
                          ).length,
                        ),
                      )}
                    </span>
                    {entry.status === "ready" && (
                      <span
                        className={`scan-check-status ${entry.reviewedAt ? "is-checked" : ""}`}
                      >
                        {entry.reviewedAt && <Check size={12} />}
                        {entry.reviewedAt ? "Page checked" : "Needs page check"}
                      </span>
                    )}
                  </span>
                  {(entry.status === "processing" ||
                    entry.status === "queued") && (
                    <LoaderCircle
                      size={15}
                      className={
                        entry.status === "processing" ? "animate-spin" : ""
                      }
                    />
                  )}
                </button>
                {entry.status === "processing" && entry.progress.total > 0 && (
                  <progress
                    aria-label={`Reading ${entry.filename}`}
                    value={entry.progress.done}
                    max={entry.progress.total}
                  />
                )}
                {entry.status === "failed" && (
                  <button
                    type="button"
                    className="scan-retry"
                    disabled={busy}
                    onClick={() => onRetry(entry.id)}
                  >
                    <RotateCcw size={13} /> Retry saved photo
                  </button>
                )}
              </div>
            ))}
          </aside>
          {scan && (
            <div className="scan-inspection">
              <div className="scan-inspection-heading">
                <div>
                  <h3>{scan.filename}</h3>
                  <p>
                    {scan.page !== null ? `Printed page ${scan.page} · ` : ""}
                    {timestamp(scan.createdAt)}
                  </p>
                </div>
                {scan.alignedUrl && scan.originalUrl && (
                  <button
                    type="button"
                    className="text-button"
                    onClick={() => setShowOriginal(!showOriginal)}
                  >
                    {showOriginal ? "Show aligned page" : "Show original"}
                  </button>
                )}
              </div>
              {(scan.status === "processing" || scan.status === "queued") && (
                <p className="scan-job-message" role="status">
                  <LoaderCircle size={16} className="animate-spin" />
                  {scan.stage || "Queued"}
                  {scan.progress.total > 0 &&
                    ` · ${scan.progress.done} of ${scan.progress.total} boxes`}
                  . You can keep reviewing other readings.
                </p>
              )}
              {scan.error && (
                <div className="scan-job-error" role="alert">
                  <div>
                    <p>{scan.error}</p>
                    <p className="scan-recovery-help">
                      If the photograph needs fixing, retake the page and add
                      the new image. Retry uses this same saved photo.
                    </p>
                  </div>
                  <div className="scan-recovery-actions">
                    <button
                      className="button small"
                      type="button"
                      disabled={busy}
                      onClick={() => input.current?.click()}
                    >
                      <Upload size={14} /> Add a new photo
                    </button>
                    <button
                      className="text-button"
                      type="button"
                      disabled={busy}
                      onClick={() => onRetry(scan.id)}
                    >
                      <RotateCcw size={14} /> Retry saved photo
                    </button>
                  </div>
                </div>
              )}
              {warnings.length > 0 && (
                <ul className="scan-warnings">
                  {warnings.map((warning) => (
                    <li key={warning}>{warning}</li>
                  ))}
                </ul>
              )}
              {imageUrl ? (
                <>
                  <div className="scan-inspection-toolbar">
                    {scan.boxes.length > 0 && (
                      <label className="scan-box-select">
                        <span>Correction box</span>
                        <select
                          value={selectedBoxId ?? ""}
                          onChange={(event) =>
                            setSelectedBoxId(event.target.value || null)
                          }
                        >
                          <option value="">Choose a box to inspect</option>
                          {scan.boxes.map((entry, index) => (
                            <option key={entry.boxId} value={entry.boxId}>
                              {index + 1}. {rowName(session, entry.rowId)} /{" "}
                              {entry.field}
                              {entry.inkStatus === "uncertain" ||
                              entry.recognitionState === "needs_review"
                                ? " — check reading"
                                : ""}
                            </option>
                          ))}
                        </select>
                      </label>
                    )}
                    <div className="scan-box-navigation">
                      <button
                        type="button"
                        className="button small"
                        aria-label="Previous correction box"
                        disabled={boxIndex <= 0}
                        onClick={() =>
                          setSelectedBoxId(scan.boxes[boxIndex - 1].boxId)
                        }
                      >
                        <ChevronLeft size={16} />
                      </button>
                      <span>
                        {boxIndex < 0 ? "—" : boxIndex + 1} /{" "}
                        {scan.boxes.length}
                      </span>
                      <button
                        type="button"
                        className="button small"
                        aria-label="Next correction box"
                        disabled={boxIndex >= scan.boxes.length - 1}
                        onClick={() =>
                          setSelectedBoxId(scan.boxes[boxIndex + 1].boxId)
                        }
                      >
                        <ChevronRight size={16} />
                      </button>
                    </div>
                    <button
                      type="button"
                      className="text-button scan-zoom"
                      aria-pressed={zoomed}
                      onClick={() => setZoomed(!zoomed)}
                    >
                      {zoomed ? <ZoomOut size={15} /> : <ZoomIn size={15} />}
                      {zoomed ? "Fit page" : "Zoom page"}
                    </button>
                  </div>
                  <div className="scan-page-desk">
                    <div className="scan-page-scroll" ref={pageArea}>
                      <div
                        className={`scan-page-image ${zoomed ? "is-zoomed" : ""}`}
                      >
                        <img
                          src={imageUrl}
                          alt={`${showOriginal ? "Original photograph" : scan.alignedUrl ? "Aligned paper page" : "Uploaded photograph"}: ${scan.filename}`}
                        />
                        {canOverlay &&
                          scan.boxes.map((entry) => (
                            <button
                              type="button"
                              key={entry.boxId}
                              className={`scan-box scan-box-${entry.inkStatus} ${selectedBoxId === entry.boxId ? "selected" : ""}`}
                              aria-label={`${session.base?.rows.find((row) => row.id === entry.rowId)?.label ?? entry.rowId}, ${entry.field}: ${entry.text || (entry.inkStatus === "blank" ? "detected as blank" : "reading needed")}`}
                              aria-pressed={selectedBoxId === entry.boxId}
                              style={{
                                left: `${entry.rect[0] * 100}%`,
                                top: `${entry.rect[1] * 100}%`,
                                width: `${entry.rect[2] * 100}%`,
                                height: `${entry.rect[3] * 100}%`,
                              }}
                              onClick={() => setSelectedBoxId(entry.boxId)}
                            />
                          ))}
                      </div>
                    </div>
                    <div className="scan-box-inspector">
                      {box ? (
                        <>
                          <div className="scan-box-heading">
                            <h4>{rowName(session, box.rowId)}</h4>
                            <span>
                              {boxRow?.label ?? box.rowId} / {box.field}
                            </span>
                          </div>
                          {box.cropUrl && (
                            <a
                              className="scan-crop-link"
                              href={box.cropUrl}
                              target="_blank"
                              rel="noreferrer"
                              aria-label={`Open correction crop for ${boxRow?.label ?? box.rowId}, ${box.field}`}
                            >
                              <img
                                className="scan-box-crop"
                                src={box.cropUrl}
                                alt={`Correction box for ${boxRow?.label ?? box.rowId}, ${box.field}`}
                              />
                            </a>
                          )}
                          <dl>
                            <dt>
                              {manuallyTranscribed
                                ? "Saved manual reading"
                                : box.recognitionState === "illustrative"
                                  ? "Example reading"
                                  : "Captured reading"}
                            </dt>
                            <dd>
                              {(savedReading ? savedReading.text : box.text) ||
                                "No reading"}
                            </dd>
                            {manuallyTranscribed &&
                              savedReading &&
                              savedReading.text !== box.text && (
                                <>
                                  <dt>Original capture</dt>
                                  <dd className="scan-original-reading">
                                    {box.text || "No reading"}
                                  </dd>
                                </>
                              )}
                          </dl>
                          <p>
                            {manuallyTranscribed
                              ? "Your transcription is saved with this photograph. Review this cell to choose what reaches Excel."
                              : box.recognitionState === "illustrative"
                                ? "Prepared example reading. This page illustrates review; it is not a handwriting recognition result."
                                : box.inkStatus === "blank"
                                  ? "Detected as blank. Check the photograph for faint or missed writing."
                                  : box.recognitionState === "needs_review"
                                    ? "This box needs a manual reading. Check it against the photograph."
                                    : "Check this reading against the photograph before approval."}
                          </p>
                          {linkedItem && (
                            <button
                              className="button"
                              type="button"
                              onClick={() => onReview(box.rowId, box.field)}
                            >
                              Review this cell <ArrowRight size={15} />
                            </button>
                          )}
                          {cellProtected ? (
                            <p>
                              This cell is protected and cannot be overwritten.
                            </p>
                          ) : (
                            <button
                              className="button"
                              type="button"
                              disabled={busy || scan.status !== "ready"}
                              onClick={() =>
                                onTranscribe({
                                  rowId: box.rowId,
                                  field: box.field,
                                  scanId: scan.id,
                                  boxId: box.boxId,
                                })
                              }
                            >
                              {linkedItem
                                ? "Transcribe this box"
                                : "Enter a reading"}{" "}
                              <ArrowRight size={15} />
                            </button>
                          )}
                        </>
                      ) : (
                        <>
                          <h4>
                            {showOriginal
                              ? "Original evidence"
                              : "Inspect the page"}
                          </h4>
                          <p>
                            {canOverlay && scan.boxes.length
                              ? "Choose a correction box on the page to check its reading or enter missed writing."
                              : "The full photograph is retained alongside its readings."}
                          </p>
                          <p>
                            Box detection can miss writing. Review the complete
                            page before exporting.
                          </p>
                        </>
                      )}
                      {scan.originalUrl && (
                        <a
                          className="scan-original-link"
                          href={scan.originalUrl}
                          target="_blank"
                          rel="noreferrer"
                        >
                          Open original photograph <ArrowRight size={13} />
                        </a>
                      )}
                    </div>
                  </div>
                </>
              ) : (
                <div className="scan-evidence-empty">
                  <FileImage size={24} />
                  <p>No full-page image is attached to this source.</p>
                </div>
              )}
              {scan.status === "ready" && (
                <div
                  className={`scan-page-check ${scan.reviewedAt ? "is-checked" : ""}`}
                >
                  <div>
                    <strong>
                      {scan.reviewedAt
                        ? "This page has been checked"
                        : "Check the whole page"}
                    </strong>
                    <p>
                      {scan.reviewedAt
                        ? `Page check saved ${timestamp(scan.reviewedAt)}. Corrections still need their own decisions.`
                        : "Inspect all writing and boxes detected as blank for missed marks. This page check does not approve its corrections."}
                    </p>
                  </div>
                  <button
                    type="button"
                    className={`button ${scan.reviewedAt ? "" : "primary"}`}
                    disabled={busy || Boolean(scan.reviewedAt)}
                    onClick={() => onCheckPage(scan.id)}
                  >
                    <Check size={16} />
                    {scan.reviewedAt ? "Page checked" : "Mark page checked"}
                  </button>
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </section>
  );
}

type ExportReviewProps = {
  session: Session;
  preview: ExportPreview | null;
  loading: boolean;
  error: string | null;
  busy: boolean;
  onRefresh: () => void;
  onExport: () => void;
  onReviewSources: () => void;
  onClose: () => void;
};

export function ExportReview({
  session,
  preview,
  loading,
  error,
  busy,
  onRefresh,
  onExport,
  onReviewSources,
  onClose,
}: ExportReviewProps) {
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    if (!dialog.current?.open) dialog.current?.showModal();
  }, []);
  const stale = Boolean(
    preview &&
    (preview.revision !== session.revision ||
      preview.currentHash !== session.current?.sha256),
  );
  const canExport = Boolean(
    preview?.canExport && !stale && !loading && !busy && !error,
  );
  const processingCount = session.scans.filter((scan) =>
    ["queued", "processing"].includes(scan.status),
  ).length;
  return (
    <dialog
      ref={dialog}
      className="export-review-dialog"
      onCancel={(event) => {
        event.preventDefault();
        onClose();
      }}
      onClose={onClose}
      aria-labelledby="export-review-title"
    >
      <div className="export-review-heading">
        <div>
          <h2 id="export-review-title">Review the export</h2>
          <p>{session.currentFilename || session.filename}</p>
        </div>
        <button
          className="icon-button"
          type="button"
          aria-label="Close export review"
          onClick={onClose}
        >
          <X size={20} />
        </button>
      </div>
      <div className="export-review-body">
        {loading && (
          <p className="export-load-message" role="status">
            <LoaderCircle size={17} className="animate-spin" /> Checking the
            selected changes…
          </p>
        )}
        {(error || (!preview && !loading)) && (
          <div className="export-review-warning" role="alert">
            <p>{error ?? "The export preview is unavailable. Try again."}</p>
            <button
              className="button small"
              type="button"
              disabled={loading || busy}
              onClick={onRefresh}
            >
              Try again
            </button>
          </div>
        )}
        {stale && (
          <div className="export-review-warning" role="alert">
            <p>
              The workbook or readings changed after this preview. Refresh it
              before exporting.
            </p>
            <button
              className="button small"
              type="button"
              disabled={loading || busy}
              onClick={onRefresh}
            >
              <RotateCcw size={14} /> Refresh preview
            </button>
          </div>
        )}
        {preview && (
          <>
            <dl className="export-summary">
              <div>
                <dd>{preview.paperChanges.length}</dd>
                <dt>paper changes</dt>
              </div>
              <div>
                <dd>{preview.pendingCount}</dd>
                <dt>unresolved readings</dt>
              </div>
              <div>
                <dd>{preview.uncheckedPageCount}</dd>
                <dt>pages awaiting check</dt>
              </div>
              <div>
                <dd>{preview.preservedDigitalCount}</dd>
                <dt>digital edits retained</dt>
              </div>
              <div>
                <dd>{preview.protectedCount}</dd>
                <dt>protected cells</dt>
              </div>
              <div>
                <dd>{preview.sourceCount}</dd>
                <dt>paper sources</dt>
              </div>
            </dl>
            {preview.uncheckedPageCount > 0 && (
              <div className="export-page-check">
                <p>
                  {preview.uncheckedPageCount} returned page
                  {preview.uncheckedPageCount === 1
                    ? " still needs"
                    : "s still need"}{" "}
                  a check for missed writing before export.
                </p>
                <button
                  className="button small"
                  type="button"
                  onClick={onReviewSources}
                  disabled={busy}
                >
                  Check paper pages <ArrowRight size={15} />
                </button>
              </div>
            )}
            {preview.pendingCount > 0 && (
              <p className="export-unresolved">
                {preview.pendingCount} correction
                {preview.pendingCount === 1 ? " still needs" : "s still need"} a
                decision. Return to review before exporting.
              </p>
            )}
            {processingCount > 0 && (
              <p className="export-unresolved" role="status">
                {processingCount} photograph
                {processingCount === 1 ? " is" : "s are"} still processing.
                Review the new readings before exporting.
              </p>
            )}
            {preview.failedSourceCount > 0 && (
              <div className="export-page-check" role="status">
                <p>
                  {preview.failedSourceCount} photograph
                  {preview.failedSourceCount === 1 ? " could" : "s could"} not
                  be read. This export does not include readings from failed
                  photographs. You can retry them in the paper inbox.
                </p>
                <button
                  className="button small"
                  type="button"
                  onClick={onReviewSources}
                  disabled={busy}
                >
                  Open paper inbox <ArrowRight size={15} />
                </button>
              </div>
            )}
            <section
              className="export-change-section"
              aria-labelledby="export-paper-title"
            >
              <h3 id="export-paper-title">Apply from paper</h3>
              {preview.paperChanges.length ? (
                <div
                  className="export-table-scroll"
                  tabIndex={0}
                  role="region"
                  aria-label="Approved paper changes"
                >
                  <table className="export-change-table">
                    <thead>
                      <tr>
                        <th>Item / field</th>
                        <th>Excel now</th>
                        <th>Export value</th>
                        <th>Source</th>
                      </tr>
                    </thead>
                    <tbody>
                      {preview.paperChanges.map((change) => (
                        <tr key={change.proposalId}>
                          <th scope="row">
                            <strong>{rowName(session, change.rowId)}</strong>
                            <span>
                              {change.rowLabel} · {change.field} ·{" "}
                              {change.address}
                            </span>
                          </th>
                          <td>{valueText(change.before)}</td>
                          <td className="export-new-value">
                            {valueText(change.after)}
                          </td>
                          <td className="export-source">{change.source}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                <p className="export-section-empty">
                  No paper changes are selected.
                </p>
              )}
            </section>
            <section
              className="export-change-section"
              aria-labelledby="export-digital-title"
            >
              <h3 id="export-digital-title">Keep from the latest workbook</h3>
              <p className="export-section-description">
                These edits remain in the exported copy. Formula expressions are
                preserved; this preview does not recalculate them.
              </p>
              {preview.digitalChanges.length ? (
                <div
                  className="export-table-scroll"
                  tabIndex={0}
                  role="region"
                  aria-label="Digital edits retained"
                >
                  <table className="export-change-table export-digital-table">
                    <thead>
                      <tr>
                        <th>Item / field</th>
                        <th>When printed</th>
                        <th>Keep in export</th>
                      </tr>
                    </thead>
                    <tbody>
                      {preview.digitalChanges.map((change) => (
                        <tr key={`${change.rowId}:${change.field}`}>
                          <th scope="row">
                            <strong>{rowName(session, change.rowId)}</strong>
                            <span>
                              {change.rowLabel} · {change.field} ·{" "}
                              {change.address}
                            </span>
                          </th>
                          <td>{valueText(change.before)}</td>
                          <td>{valueText(change.after)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                <p className="export-section-empty">
                  No other changed cell values were found in this worksheet.
                </p>
              )}
              {preview.preservedDigitalCount >
                preview.digitalChanges.length && (
                <p className="export-section-description">
                  Showing {preview.digitalChanges.length} of{" "}
                  {preview.preservedDigitalCount} retained digital edits.
                </p>
              )}
              {preview.retainedFormulaChangeCount > 0 && (
                <details className="receipt-disclosure">
                  <summary>
                    {preview.retainedFormulaChangeCount} formula expression
                    changes retained from Excel
                  </summary>
                  <p>
                    These expressions changed after printing and will remain as
                    they are in the latest workbook.
                  </p>
                  <ReceiptChanges changes={preview.formulaChanges} />
                  {preview.retainedFormulaChangeCount >
                    preview.formulaChanges.length && (
                    <p>
                      Showing {preview.formulaChanges.length} of{" "}
                      {preview.retainedFormulaChangeCount} formula expression
                      changes.
                    </p>
                  )}
                </details>
              )}
            </section>
          </>
        )}
      </div>
      <div className="export-review-actions">
        <p>
          The export includes a downloadable change record with decisions and
          source evidence.
        </p>
        <div>
          <button className="button" type="button" onClick={onClose}>
            Back to review
          </button>
          <button
            className="button primary"
            type="button"
            disabled={!canExport}
            onClick={onExport}
          >
            {busy ? (
              <LoaderCircle size={16} className="animate-spin" />
            ) : (
              <ArrowDownToLine size={16} />
            )}
            Export reviewed workbook
          </button>
        </div>
      </div>
    </dialog>
  );
}

export function ExportHistory({ session }: { session: Session }) {
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const selected =
    session.history.find((entry) => entry.id === selectedId) ??
    session.history.at(-1);
  return (
    <section className="export-history" aria-labelledby="export-history-title">
      <div className="export-history-heading">
        <h2 id="export-history-title">Export history</h2>
        <p>
          See what changed, what stayed in Excel, and the files used for each
          export.
        </p>
      </div>
      {session.history.length === 0 ? (
        <div className="history-empty">
          <h3>No exports yet</h3>
          <p>
            Review the paper readings, then inspect the export preview.
            Completed exports will appear here.
          </p>
        </div>
      ) : (
        <ol>
          {[...session.history].reverse().map((entry) => (
            <li
              key={entry.id}
              className={selected?.id === entry.id ? "is-expanded" : ""}
            >
              <div className="history-entry-heading">
                <div className="history-entry-copy">
                  <strong>{entry.filename}</strong>
                  <span>
                    {entry.count} change{entry.count === 1 ? "" : "s"} ·{" "}
                    {timestamp(entry.createdAt)}
                    {entry.verification && " · File content verified"}
                  </span>
                </div>
                <div className="history-entry-actions">
                  {selected?.id !== entry.id && (
                    <button
                      className="text-button"
                      type="button"
                      onClick={() => setSelectedId(entry.id)}
                    >
                      View export receipt <ArrowRight size={14} />
                    </button>
                  )}
                  <a
                    className="button small"
                    href={entry.url}
                    download={entry.filename}
                  >
                    <ArrowDownToLine size={15} /> Workbook
                  </a>
                </div>
              </div>
              {selected?.id === entry.id && (
                <ExportReceiptPanel
                  key={`${session.id}:${entry.id}`}
                  sessionId={session.id}
                  entry={entry}
                />
              )}
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}

function ExportReceiptPanel({
  sessionId,
  entry,
}: {
  sessionId: string;
  entry: Session["history"][number];
}) {
  const [receipt, setReceipt] = useState<ExportReceipt | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(Boolean(entry.auditUrl));
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    if (!entry.auditUrl) return;
    const controller = new AbortController();
    setLoading(true);
    setError(null);
    void (async () => {
      try {
        const response = await fetch(
          `/api/sessions/${encodeURIComponent(sessionId)}/exports/${encodeURIComponent(entry.id)}/receipt`,
          { signal: controller.signal },
        );
        if (!response.ok)
          throw new Error(
            "This export receipt could not be loaded. The saved files are still available below.",
          );
        const parsed = exportReceiptSchema.safeParse(await response.json());
        if (
          !parsed.success ||
          parsed.data.id !== entry.id ||
          parsed.data.sessionId !== sessionId
        )
          throw new Error(
            "This receipt could not be verified as the selected export. Download the change record below to inspect it.",
          );
        setReceipt(parsed.data);
      } catch (cause) {
        if (!controller.signal.aborted)
          setError(
            cause instanceof Error
              ? cause.message
              : "The export receipt could not be loaded.",
          );
      } finally {
        if (!controller.signal.aborted) setLoading(false);
      }
    })();
    return () => controller.abort();
  }, [sessionId, entry.id, entry.auditUrl, attempt]);
  const verification = receipt?.verification;
  const inputUrl = receipt?.inputWorkbookUrl ?? entry.inputWorkbookUrl;
  return (
    <div className="history-receipt">
      {loading && (
        <p className="export-load-message" role="status">
          <LoaderCircle size={16} className="animate-spin" /> Loading the saved
          export receipt…
        </p>
      )}
      {error && (
        <div className="export-review-warning" role="alert">
          <p>{error}</p>
          <button
            className="button small"
            type="button"
            onClick={() => setAttempt((value) => value + 1)}
          >
            Try again
          </button>
        </div>
      )}
      {!entry.auditUrl && (
        <p className="history-legacy-note">
          No change record or file verification was recorded for this earlier
          export.
        </p>
      )}
      {receipt && (
        <>
          <div
            className={`receipt-verification ${verification ? "is-verified" : ""}`}
          >
            <h3>
              {verification ? (
                <>
                  <Check size={18} /> Export file checks passed
                </>
              ) : (
                "No file verification recorded"
              )}
            </h3>
            <p>
              {verification
                ? "The exported workbook was compared with the exact input file. Only approved cells changed; formula expressions were preserved."
                : "This earlier export has a saved change record, but no file comparison was recorded."}
            </p>
          </div>
          <dl className="receipt-summary">
            <div>
              <dd>{receipt.paperChanges.length}</dd>
              <dt>paper changes applied</dt>
            </div>
            <div>
              <dd>{receipt.preservedDigitalCount}</dd>
              <dt>Excel edits retained</dt>
            </div>
            {verification && (
              <div>
                <dd>{verification.preservedFormulaCount}</dd>
                <dt>formula expressions preserved</dt>
              </div>
            )}
          </dl>
          <section className="export-change-section">
            <h3>Applied from paper</h3>
            <ReceiptChanges changes={receipt.paperChanges} paper />
          </section>
          <section className="export-change-section">
            <h3>Retained from the latest Excel file</h3>
            <p className="export-section-description">
              Changes made in Excel since printing remain in the exported file.
            </p>
            {receipt.digitalChanges.length ? (
              <ReceiptChanges changes={receipt.digitalChanges} />
            ) : (
              <p className="export-section-empty">
                No other changed cell values were recorded in this worksheet.
              </p>
            )}
            {receipt.preservedDigitalCount > receipt.digitalChanges.length && (
              <p className="export-section-description">
                Showing {receipt.digitalChanges.length} of{" "}
                {receipt.preservedDigitalCount} retained Excel edits. Download
                the input and output workbooks to inspect all retained edits.
              </p>
            )}
          </section>
          {receipt.retainedFormulaChangeCount > 0 && (
            <details className="receipt-disclosure">
              <summary>
                {receipt.retainedFormulaChangeCount} formula expression change
                {receipt.retainedFormulaChangeCount === 1 ? "" : "s"} retained
                from Excel
              </summary>
              <p>
                These expressions changed in Excel after printing. The export
                preserves them without recalculating their results.
              </p>
              <ReceiptChanges changes={receipt.formulaChanges} />
              {receipt.retainedFormulaChangeCount >
                receipt.formulaChanges.length && (
                <p>
                  Showing {receipt.formulaChanges.length} of{" "}
                  {receipt.retainedFormulaChangeCount} formula expression
                  changes.
                </p>
              )}
            </details>
          )}
        </>
      )}
      <div className="receipt-files">
        <h3>Files from this export</h3>
        <div>
          {inputUrl && (
            <a className="button small" href={inputUrl} download>
              <ArrowDownToLine size={15} /> Input Excel file
            </a>
          )}
          <a
            className="button small"
            href={entry.url}
            download={entry.filename}
          >
            <ArrowDownToLine size={15} /> Exported workbook
          </a>
          {entry.auditUrl && (
            <a className="text-button" href={entry.auditUrl} download>
              Change record <ArrowRight size={14} />
            </a>
          )}
        </div>
        {inputUrl && (
          <p>
            The input is the exact Excel file used for this export, including
            edits made after printing.
          </p>
        )}
      </div>
      {receipt && (
        <details className="receipt-disclosure receipt-hashes">
          <summary>File fingerprints and verification details</summary>
          {verification && (
            <p>
              {verification.changedCellCount} approved cells changed.{" "}
              {verification.unchangedPartCount} of {verification.totalPartCount}{" "}
              workbook archive parts are byte-for-byte unchanged. Verification
              covers file contents; it does not check handwriting accuracy or
              recalculate formulas.
            </p>
          )}
          <dl>
            <div>
              <dt>Input SHA-256</dt>
              <dd>{receipt.currentHash}</dd>
            </div>
            <div>
              <dt>Output SHA-256</dt>
              <dd>{receipt.outputHash}</dd>
            </div>
          </dl>
        </details>
      )}
    </div>
  );
}

function ReceiptChanges({
  changes,
  paper = false,
}: {
  changes: ExportReceipt["paperChanges"] | ExportReceipt["digitalChanges"];
  paper?: boolean;
}) {
  return (
    <div
      className="export-table-scroll"
      tabIndex={0}
      role="region"
      aria-label={paper ? "Applied paper changes" : "Retained Excel edits"}
    >
      <table className="export-change-table">
        <thead>
          <tr>
            <th>Row ID / field</th>
            <th>{paper ? "Excel before export" : "When printed"}</th>
            <th>{paper ? "Exported value" : "Retained in export"}</th>
            {paper && <th>Paper source</th>}
          </tr>
        </thead>
        <tbody>
          {changes.map((change) => (
            <tr key={`${change.rowId}:${change.field}`}>
              <th scope="row">
                <strong>{change.rowLabel}</strong>
                <span>
                  {change.field} · {change.address}
                </span>
              </th>
              <td>{valueText(change.before)}</td>
              <td className={paper ? "export-new-value" : undefined}>
                {valueText(change.after)}
              </td>
              {paper && (
                <td className="export-source">
                  {"source" in change ? change.source : ""}
                </td>
              )}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
