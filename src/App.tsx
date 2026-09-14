import { useEffect, useRef, useState } from "react";
import type { ChangeEvent, FormEvent, ReactNode } from "react";
import {
  ArrowDownToLine,
  ArrowRight,
  Check,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  CircleAlert,
  FileCheck2,
  FileSpreadsheet,
  FileText,
  ImagePlus,
  Layers2,
  LoaderCircle,
  LockKeyhole,
  Plus,
  ScanLine,
  ShieldCheck,
  Upload,
  X,
} from "lucide-react";
import type {
  Cell,
  Decision,
  ExportPreview,
  Item,
  ManualTarget,
  ManualDraft,
  Session,
} from "./types";
import {
  exportPreviewSchema,
  readingDraftSchema,
  manualDraftSchema,
  sessionSchema,
} from "./types";
import Landing from "./Landing";
import { ExportHistory, ExportReview, ScanInbox } from "./ReviewTools";

type View = "workbook" | "paper" | "review";
type Health = { status: string; ocr: unknown };
type Filter = "all" | "pending" | "decided" | "protected";
const statusLabels: Record<Item["status"], string> = {
  ready: "Ready to review",
  conflict: "Conflict",
  paper_conflict: "Paper copies disagree",
  unchanged: "No paper change",
  already_applied: "Already matches",
  protected: "Protected",
  invalid: "Check transcription",
  replayed: "Previously exported",
};
const needsDecision = (item: Item) =>
  item.requiresReadingCheck ||
  ["ready", "conflict", "paper_conflict", "invalid"].includes(item.status);

function readDraft(sessionId: string, item: Item) {
  try {
    const stored = localStorage.getItem(
      `paperpatch:draft:${sessionId}:${item.id}`,
    );
    if (!stored) return null;
    const parsed = readingDraftSchema.safeParse(JSON.parse(stored));
    if (!parsed.success || parsed.data.text === item.text) return null;
    if (
      parsed.data.candidateId &&
      !item.candidates.some(
        (candidate) => candidate.id === parsed.data.candidateId,
      )
    )
      return null;
    return parsed.data;
  } catch {
    return null;
  }
}

function readManualDraft(session: Session) {
  try {
    const stored = localStorage.getItem(`paperpatch:manual:${session.id}`);
    if (!stored) return null;
    const parsed = manualDraftSchema.safeParse(JSON.parse(stored));
    if (!parsed.success) return null;
    const draft = parsed.data;
    if (
      !session.base?.rows.some((row) => row.id === draft.rowId) ||
      !session.config?.editableColumns.includes(draft.field)
    )
      return null;
    if (draft.scanId || draft.boxId) {
      const box = session.scans
        .find((scan) => scan.id === draft.scanId)
        ?.boxes.find((entry) => entry.boxId === draft.boxId);
      if (!box || box.rowId !== draft.rowId || box.field !== draft.field)
        return null;
    }
    return draft;
  } catch {
    return null;
  }
}
const displayValue = (cell: Pick<Cell, "value"> | null | undefined) =>
  cell?.value === null || cell?.value === undefined || cell.value === ""
    ? "Empty"
    : String(cell.value);

function rowTitle(session: Session, item: Pick<Item, "rowId" | "rowLabel">) {
  const table = session.current ?? session.base;
  const row = table?.rows.find((entry) => entry.id === item.rowId);
  const label = table?.headers
    .filter(
      (header) =>
        header !== session.config?.idColumn &&
        !session.config?.editableColumns.includes(header),
    )
    .map((header) => row?.cells[header])
    .find((cell) => cell?.kind === "text" && cell.value);
  return label ? displayValue(label) : item.rowLabel;
}

async function getSession(
  url: string,
  options?: RequestInit,
): Promise<Session> {
  const response = await fetch(url, options);
  const body: unknown = await response.json();
  if (!response.ok) {
    const detail =
      typeof body === "object" && body !== null && "detail" in body
        ? body.detail
        : null;
    throw new Error(
      typeof detail === "string"
        ? detail
        : `Request failed (${response.status}). Please try again.`,
    );
  }
  const parsed = sessionSchema.safeParse(body);
  if (!parsed.success)
    throw new Error(
      "The local service returned an incomplete session. Refresh to restore the latest saved version.",
    );
  return parsed.data;
}

function UploadButton({
  children,
  accept,
  onFile,
  disabled,
  className = "",
}: {
  children: ReactNode;
  accept: string;
  onFile: (file: File) => void;
  disabled?: boolean;
  className?: string;
}) {
  const input = useRef<HTMLInputElement>(null);
  return (
    <>
      <input
        ref={input}
        type="file"
        accept={accept}
        hidden
        tabIndex={-1}
        onChange={(event) => {
          const file = event.target.files?.[0];
          if (file) onFile(file);
          event.target.value = "";
        }}
      />
      <button
        type="button"
        className={className}
        disabled={disabled}
        onClick={() => input.current?.click()}
      >
        {children}
      </button>
    </>
  );
}

function Status({ item, decided }: { item: Item; decided?: Decision }) {
  if (decided)
    return (
      <span className="status chosen">
        <Check size={12} />
        {decided === "paper" ? "Use paper" : "Keep Excel"}
      </span>
    );
  return (
    <span className={`status status-${item.status}`}>
      {item.status === "protected" ? (
        <LockKeyhole size={12} />
      ) : ["conflict", "paper_conflict", "invalid"].includes(item.status) ? (
        <CircleAlert size={12} />
      ) : null}
      {item.requiresReadingCheck &&
      !["paper_conflict", "invalid"].includes(item.status)
        ? "Check handwriting"
        : statusLabels[item.status]}
    </span>
  );
}

export default function App() {
  const [session, setSession] = useState<Session | null>(null);
  const [view, setView] = useState<View>("workbook");
  const [busy, setBusy] = useState<string | null>(null);
  const [restoring, setRestoring] = useState(() =>
    Boolean(new URLSearchParams(location.search).get("session")),
  );
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [lastReviewedId, setLastReviewedId] = useState<string | null>(null);
  const [health, setHealth] = useState<Health | null>(null);
  const decisions = session?.reviewDecisions ?? {};
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [filter, setFilter] = useState<Filter>("all");
  const [showManual, setShowManual] = useState(false);
  const [hasUnsavedReading, setHasUnsavedReading] = useState(false);
  const [reviewTab, setReviewTab] = useState<"changes" | "sources" | "history">(
    "changes",
  );
  const [manualTarget, setManualTarget] = useState<ManualTarget | null>(null);
  const [manualInitial, setManualInitial] = useState<ManualDraft | null>(null);
  const [initialScanId, setInitialScanId] = useState<string | null>(null);
  const [showExport, setShowExport] = useState(false);
  const [preview, setPreview] = useState<ExportPreview | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const previewRequest = useRef<AbortController | null>(null);
  const activeScans =
    session?.scans.some((scan) =>
      ["queued", "processing"].includes(scan.status),
    ) ?? false;

  useEffect(() => {
    const abort = new AbortController();
    fetch("/api/health", { signal: abort.signal })
      .then((response) => (response.ok ? response.json() : null))
      .then((result: Health | null) => setHealth(result))
      .catch(() => {});
    const id = new URLSearchParams(location.search).get("session");
    if (id)
      getSession(`/api/sessions/${encodeURIComponent(id)}`, {
        signal: abort.signal,
      })
        .then((saved) => {
          setSession(saved);
          setSelectedId(
            saved.items.find((item) => readDraft(saved.id, item))?.id ?? null,
          );
          setView(
            saved.items.length ? "review" : saved.config ? "paper" : "workbook",
          );
          const manual = readManualDraft(saved);
          if (manual) {
            setManualTarget(manual);
            setManualInitial(manual);
            setShowManual(true);
            setView("review");
          }
        })
        .catch((cause: unknown) => {
          if (!abort.signal.aborted)
            setError(
              cause instanceof Error
                ? cause.message
                : "Could not restore this session.",
            );
        })
        .finally(() => {
          if (!abort.signal.aborted) setRestoring(false);
        });
    return () => abort.abort();
  }, []);
  useEffect(() => {
    if (!session || !activeScans || busy) return;
    const id = session.id;
    const abort = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const next = await getSession(`/api/sessions/${id}`, {
          signal: abort.signal,
        });
        if (!abort.signal.aborted)
          setSession((previous) =>
            previous?.id === id && next.revision >= previous.revision
              ? next
              : previous,
          );
      } catch (cause) {
        if (!abort.signal.aborted)
          setError(
            cause instanceof Error
              ? cause.message
              : "Could not refresh scan progress. Retrying shortly.",
          );
      }
      if (!abort.signal.aborted) timer = setTimeout(poll, 1500);
    };
    timer = setTimeout(poll, 800);
    return () => {
      abort.abort();
      clearTimeout(timer);
    };
  }, [session?.id, activeScans, busy]);

  useEffect(() => () => previewRequest.current?.abort(), []);

  const acceptSession = (next: Session) => {
    setSession((previous) =>
      previous?.id === next.id && previous.revision > next.revision
        ? previous
        : next,
    );
    const url = new URL(location.href);
    url.searchParams.set("session", next.id);
    history.replaceState(null, "", url);
  };
  async function mutate(
    label: string,
    path: string,
    options?: RequestInit,
    nextView?: View,
  ) {
    if (busy) return null;
    setBusy(label);
    setError(null);
    setNotice(null);
    setLastReviewedId(null);
    try {
      const next = await getSession(path, options ?? { method: "POST" });
      acceptSession(next);
      if (nextView) setView(nextView);
      if (label === "Adding scans")
        setNotice("Scans saved. Processing continues while you review.");
      if (label === "Updating the latest workbook")
        setNotice(
          "Latest workbook updated. Decisions are retained where the cell and its evidence are unchanged.",
        );
      if (label === "Saving transcription")
        setNotice(
          "Transcription saved with its original evidence. Review this reading before export.",
        );
      return next;
    } catch (cause) {
      setError(
        cause instanceof Error
          ? cause.message
          : "Could not reach the local service. Please try again.",
      );
      return null;
    } finally {
      setBusy(null);
    }
  }
  const uploadWorkbook = (file: File) => {
    const body = new FormData();
    body.append("file", file);
    void mutate(
      "Opening your workbook",
      "/api/sessions",
      { method: "POST", body },
      "workbook",
    );
  };
  const startPaperTrial = async () => {
    const next = await mutate(
      "Preparing your handwriting trial",
      "/api/paper-trial",
      undefined,
      "paper",
    );
    if (!next) return;
    setSelectedId(null);
    setFilter("all");
    setReviewTab("changes");
    setInitialScanId(null);
    setHasUnsavedReading(false);
  };
  const uploadScans = (files: File[]) => {
    if (!session || !files.length) return;
    const body = new FormData();
    files.forEach((file) => body.append("files", file));
    setReviewTab("sources");
    void mutate(
      "Adding scans",
      `/api/sessions/${session.id}/scans`,
      { method: "POST", body },
      "review",
    );
  };
  const uploadToSession = (file: File, kind: "scan" | "current") => {
    if (!session) return;
    if (kind === "scan") return uploadScans([file]);
    const body = new FormData();
    body.append("file", file);
    void mutate(
      "Updating the latest workbook",
      `/api/sessions/${session.id}/${kind}`,
      { method: "POST", body },
    );
  };
  const reset = () => {
    setSession(null);
    setView("workbook");
    setError(null);
    setNotice(null);
    setSelectedId(null);
    setShowManual(false);
    setFilter("all");
    setReviewTab("changes");
    setShowExport(false);
    setPreview(null);
    setManualTarget(null);
    setManualInitial(null);
    setHasUnsavedReading(false);
    setInitialScanId(null);
    previewRequest.current?.abort();
    const url = new URL(location.href);
    url.searchParams.delete("session");
    history.replaceState(null, "", url);
  };
  const closeManual = () => {
    if (session) {
      try {
        localStorage.removeItem(`paperpatch:manual:${session.id}`);
      } catch {
        /* Storage may be unavailable in this browser. */
      }
    }
    setShowManual(false);
    setManualTarget(null);
    setManualInitial(null);
  };
  const items = session?.items ?? [];
  const multipleReviewFields =
    new Set(items.map((item) => item.field)).size > 1;
  const pending = items.filter(
    (item) => needsDecision(item) && !decisions[item.id],
  );
  const visibleItems = items.filter(
    (item) =>
      filter === "all" ||
      (filter === "pending" && needsDecision(item) && !decisions[item.id]) ||
      (filter === "decided" && decisions[item.id]) ||
      (filter === "protected" && item.status === "protected"),
  );
  const selected =
    visibleItems.find((item) => item.id === selectedId) ?? visibleItems[0];
  const uncheckedPages =
    session?.scans.filter((scan) => scan.status === "ready" && !scan.reviewedAt)
      .length ?? 0;
  const chosenPaper = items.filter(
    (item) => decisions[item.id] === "paper",
  ).length;
  const conflictCount = items.filter(
    (item) =>
      ["conflict", "paper_conflict"].includes(item.status) &&
      !decisions[item.id],
  ).length;
  const advanceReview = (item: Item) => {
    const index = pending.findIndex((entry) => entry.id === item.id);
    const next =
      pending.slice(index + 1).find((entry) => entry.id !== item.id) ??
      pending.find((entry) => entry.id !== item.id);
    if (next) setSelectedId(next.id);
    setLastReviewedId(item.id);
    const remaining = pending.filter((entry) => entry.id !== item.id).length;
    setNotice(
      `Saved for ${session ? rowTitle(session, item) : item.rowLabel}. ${remaining} reading${remaining === 1 ? "" : "s"} left to review.`,
    );
  };
  const choose = async (item: Item, decision: Decision) => {
    if (!session) return;
    const saved = await mutate(
      "Saving decision",
      `/api/sessions/${session.id}/decisions`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          proposalId: item.id,
          choice: decision,
          revision: session.revision,
        }),
      },
    );
    if (saved) advanceReview(item);
  };
  const verifyReading = async (item: Item, candidateId: string) => {
    if (!session) return;
    const saved = await mutate(
      "Checking reading",
      `/api/sessions/${session.id}/proposals/${item.id}/verify`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ candidateId, revision: session.revision }),
      },
    );
    if (saved) advanceReview(item);
  };
  const fetchPreview = async () => {
    if (!session) return;
    previewRequest.current?.abort();
    const abort = new AbortController();
    previewRequest.current = abort;
    setPreviewLoading(true);
    setPreviewError(null);
    setError(null);
    try {
      const latest = await getSession(`/api/sessions/${session.id}`, {
        signal: abort.signal,
      });
      if (abort.signal.aborted) return;
      setSession((previous) =>
        previous?.id === latest.id && latest.revision >= previous.revision
          ? latest
          : previous,
      );
      const response = await fetch(`/api/sessions/${session.id}/preview`, {
        signal: abort.signal,
      });
      const body: unknown = await response.json();
      if (!response.ok)
        throw new Error(
          typeof body === "object" &&
            body !== null &&
            "detail" in body &&
            typeof body.detail === "string"
            ? body.detail
            : "Could not prepare the export preview.",
        );
      const parsed = exportPreviewSchema.safeParse(body);
      if (!parsed.success)
        throw new Error(
          "The export preview was incomplete. Refresh it before exporting.",
        );
      if (!abort.signal.aborted) setPreview(parsed.data);
    } catch (cause) {
      if (!abort.signal.aborted)
        setPreviewError(
          cause instanceof Error
            ? cause.message
            : "Could not prepare the export preview.",
        );
    } finally {
      if (!abort.signal.aborted) setPreviewLoading(false);
    }
  };
  const exportWorkbook = async () => {
    if (
      !session?.current ||
      !preview?.canExport ||
      preview.revision !== session.revision ||
      preview.currentHash !== session.current.sha256
    )
      return;
    const exported = await mutate(
      "Preparing your workbook",
      `/api/sessions/${session.id}/export`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          currentHash: preview.currentHash,
          revision: preview.revision,
          decisions,
        }),
      },
    );
    if (exported) {
      setShowExport(false);
      setReviewTab("history");
    }
  };

  return (
    <div
      className={`app-shell ${session && view === "review" ? "editor-shell" : ""}`}
    >
      <header className="topbar">
        <button
          className="brand"
          onClick={reset}
          disabled={Boolean(busy) || hasUnsavedReading}
          aria-label="PaperPatch, start a new workbook"
        >
          <svg className="patch-emblem" viewBox="0 0 34 42" aria-hidden="true">
            <path d="M7 3h15l6 7v27l-5-3-5 4-5-4-6 3V3Z" fill="currentColor" />
            <path
              d="M22 3v8h6M11 27l13-3M13 23l1 7m5-8 1 7"
              fill="none"
              stroke="#20211e"
              strokeWidth="1.4"
            />
            <path d="M11 16v4m11-4v4" stroke="#20211e" strokeWidth="2.4" />
          </svg>
          <span className="brand-type">PaperPatch</span>
        </button>
        {session && (
          <nav className="steps" aria-label="Workflow">
            {(["workbook", "paper", "review"] as const).map((step) => (
              <button
                key={step}
                className={`step ${view === step ? "active" : ""}`}
                aria-current={view === step ? "step" : undefined}
                disabled={
                  Boolean(busy) ||
                  hasUnsavedReading ||
                  (step !== "workbook" && !session.config)
                }
                onClick={() => setView(step)}
              >
                {step === "workbook"
                  ? "Workbook"
                  : step === "paper"
                    ? "Print & scan"
                    : "Review"}
              </button>
            ))}
          </nav>
        )}
        <div className="topbar-end">
          <a
            className="text-button"
            href="/?mode=documents"
            aria-disabled={Boolean(busy) || hasUnsavedReading || undefined}
            tabIndex={busy || hasUnsavedReading ? -1 : undefined}
            onClick={(event) => {
              if (busy || hasUnsavedReading) event.preventDefault();
            }}
          >
            Word documents <ArrowRight size={14} />
          </a>
          <span className="local-badge">
            <span
              className={health ? "connection-dot" : "connection-dot waiting"}
            />
            On this device
          </span>
          {session && (
            <button
              className="text-button new-workbook"
              disabled={Boolean(busy) || hasUnsavedReading}
              onClick={reset}
            >
              <Plus size={15} />
              New workbook
            </button>
          )}
        </div>
      </header>
      <div className="announcements" aria-live="polite">
        {error && (
          <div className="message error" role="alert">
            <CircleAlert size={19} />
            <span>{error}</span>
            <button aria-label="Dismiss error" onClick={() => setError(null)}>
              <X size={16} />
            </button>
          </div>
        )}
        {notice && (
          <div className="message success">
            <Check size={18} />
            <span>{notice}</span>
            {lastReviewedId && (
              <button
                className="review-last"
                disabled={Boolean(busy) || hasUnsavedReading}
                onClick={() => {
                  setFilter("all");
                  setSelectedId(lastReviewedId);
                  setReviewTab("changes");
                }}
              >
                Change decision
              </button>
            )}
            <button aria-label="Dismiss notice" onClick={() => setNotice(null)}>
              <X size={16} />
            </button>
          </div>
        )}
        {busy && (
          <div className="message processing" role="status">
            <LoaderCircle size={18} className="animate-spin" />
            <span>
              <strong>{busy}…</strong>
              {busy === "Reading your scan" &&
                " Handwriting recognition can take about a minute. Keep this tab open."}
            </span>
          </div>
        )}
      </div>
      {restoring ? (
        <main className="restoring">
          <LoaderCircle className="animate-spin" />
          <h1>Opening your workspace</h1>
          <p>Restoring the saved workbook and review.</p>
        </main>
      ) : !session ? (
        <Landing
          onUpload={uploadWorkbook}
          onTrial={() => void startPaperTrial()}
          onDemo={() =>
            void mutate("Opening the example", "/api/demo", undefined, "review")
          }
          busy={Boolean(busy)}
        />
      ) : (
        <main
          className={`workspace-main ${view === "review" ? "review-main" : ""}`}
        >
          {session.demo && (
            <div className="demo-banner">
              <Layers2 size={15} />
              <span>
                <strong>Demo workspace</strong>
                <span className="demo-separator">/</span>Prepared example data.
                Handwriting recognition is not being shown.
              </span>
            </div>
          )}
          {session.warnings.length > 0 && (
            <div className="warning-list">
              <CircleAlert size={18} />
              <div>
                {session.warnings.map((warning) => (
                  <p key={warning}>{warning}</p>
                ))}
              </div>
            </div>
          )}
          {view === "workbook" && (
            <WorkbookSetup
              session={session}
              busy={Boolean(busy)}
              onContinue={() => setView("paper")}
              onSave={(config) =>
                void mutate(
                  "Preparing your paper copy",
                  `/api/sessions/${session.id}/config`,
                  {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify(config),
                  },
                  "paper",
                )
              }
            />
          )}
          {view === "paper" && (
            <PaperStage
              session={session}
              busy={Boolean(busy)}
              onUpload={uploadToSession}
              onReview={() => setView("review")}
              onManual={() => {
                setView("review");
                setManualTarget(null);
                setShowManual(true);
              }}
            />
          )}
          {view === "review" && (
            <>
              {session.demo && (
                <ExampleGuide
                  session={session}
                  busy={Boolean(busy) || hasUnsavedReading}
                  onReading={(id) => {
                    setFilter("all");
                    setSelectedId(id);
                    setReviewTab("changes");
                    requestAnimationFrame(() => {
                      const inspector = document.querySelector<HTMLElement>(
                        ".correction-inspector",
                      );
                      inspector?.focus({ preventScroll: true });
                      inspector?.scrollIntoView({ block: "start" });
                    });
                  }}
                  onPages={() => {
                    setReviewTab("sources");
                    setInitialScanId(
                      session.scans.find(
                        (scan) => scan.status === "ready" && !scan.reviewedAt,
                      )?.id ?? null,
                    );
                    requestAnimationFrame(() =>
                      document
                        .querySelector(".scan-inspection")
                        ?.scrollIntoView({ block: "start" }),
                    );
                  }}
                  onExport={() => {
                    setShowExport(true);
                    void fetchPreview();
                  }}
                  onReceipt={() => setReviewTab("history")}
                  onTrial={() => void startPaperTrial()}
                />
              )}
              <section className="document-toolbar">
                <div className="document-title">
                  <h1>{session.config?.sheet ?? "Workbook"}</h1>
                  <span title={session.currentFilename || session.filename}>
                    {session.currentFilename || session.filename}
                  </span>
                </div>
                <div className="source-actions">
                  <UploadButton
                    className="button small"
                    accept=".xlsx"
                    onFile={(file) => uploadToSession(file, "current")}
                    disabled={Boolean(busy) || hasUnsavedReading}
                  >
                    <Upload size={15} />
                    Update Excel
                  </UploadButton>
                  <button
                    className="button small"
                    onClick={() => setReviewTab("sources")}
                    disabled={Boolean(busy) || hasUnsavedReading}
                  >
                    <ScanLine size={16} />
                    Add scans
                  </button>
                  <button
                    className="button small manual-entry-button"
                    disabled={Boolean(busy) || hasUnsavedReading}
                    onClick={() => {
                      setManualTarget(null);
                      setShowManual(true);
                    }}
                  >
                    <Plus size={16} />
                    Manual entry
                  </button>
                </div>
              </section>
              <nav className="review-tabs" aria-label="Review workspace">
                {(
                  [
                    ["changes", "Changes", pending.length],
                    ["sources", "Paper inbox", session.scans.length],
                    ["history", "Exports", session.history.length],
                  ] as const
                ).map(([tab, label, count]) => (
                  <button
                    key={tab}
                    aria-current={reviewTab === tab ? "page" : undefined}
                    className={reviewTab === tab ? "active" : ""}
                    disabled={hasUnsavedReading}
                    onClick={() => setReviewTab(tab)}
                  >
                    {label}
                    <span>{count}</span>
                  </button>
                ))}
                {activeScans && (
                  <span className="scan-activity">
                    <LoaderCircle size={13} className="animate-spin" />
                    Reading scans
                  </span>
                )}
              </nav>
              {reviewTab === "sources" && (
                <ScanInbox
                  session={session}
                  busy={Boolean(busy)}
                  initialScanId={initialScanId}
                  onUpload={uploadScans}
                  onRetry={(scanId) =>
                    void mutate(
                      "Retrying scan",
                      `/api/sessions/${session.id}/scans/${scanId}/retry`,
                    )
                  }
                  onCheckPage={(scanId) =>
                    void mutate(
                      "Saving page check",
                      `/api/sessions/${session.id}/scans/${scanId}/review`,
                      {
                        method: "POST",
                        headers: { "Content-Type": "application/json" },
                        body: JSON.stringify({ revision: session.revision }),
                      },
                    )
                  }
                  onReview={(rowId, field) => {
                    const item = items.find(
                      (entry) => entry.rowId === rowId && entry.field === field,
                    );
                    if (item) {
                      setFilter("all");
                      setSelectedId(item.id);
                      setReviewTab("changes");
                    }
                  }}
                  onTranscribe={(target) => {
                    setManualTarget(target);
                    setShowManual(true);
                  }}
                />
              )}
              {reviewTab === "history" && <ExportHistory session={session} />}
              {reviewTab === "changes" && (
                <>
                  {items.length > 0 ? (
                    <div className="review-workspace">
                      <aside className="queue-panel">
                        <div className="queue-title">
                          <h2>
                            Changes <span>{items.length}</span>
                          </h2>
                          {conflictCount > 0 && (
                            <span className="queue-conflict-count">
                              {conflictCount} conflict
                              {conflictCount === 1 ? "" : "s"}
                            </span>
                          )}
                        </div>
                        <div className="queue-filter">
                          <select
                            aria-label="Filter changes"
                            value={filter}
                            disabled={hasUnsavedReading}
                            onChange={(event) => {
                              setFilter(event.target.value as Filter);
                              setSelectedId(null);
                            }}
                          >
                            <option value="all">All changes</option>
                            <option value="pending">
                              Needs review ({pending.length})
                            </option>
                            <option value="decided">
                              Decided ({Object.keys(decisions).length})
                            </option>
                            <option value="protected">Protected cells</option>
                          </select>
                          <ChevronDown size={14} />
                        </div>
                        <div className="queue">
                          {visibleItems.length ? (
                            visibleItems.map((item) => (
                              <button
                                key={item.id}
                                className={`queue-item ${selected?.id === item.id ? "selected" : ""}`}
                                disabled={hasUnsavedReading}
                                onClick={() => setSelectedId(item.id)}
                                aria-current={
                                  selected?.id === item.id ? "true" : undefined
                                }
                              >
                                <strong>{rowTitle(session, item)}</strong>
                                {multipleReviewFields && (
                                  <span className="queue-field">
                                    {item.field}
                                  </span>
                                )}
                                <span className="queue-bottom">
                                  <span>
                                    {displayValue(item.current)}{" "}
                                    <ArrowRight size={12} />{" "}
                                    {item.text || "Blank"}
                                  </span>
                                  <span
                                    className={`queue-status ${item.status}`}
                                  >
                                    {decisions[item.id]
                                      ? "Decided"
                                      : item.status === "paper_conflict"
                                        ? "Paper conflict"
                                        : item.requiresReadingCheck
                                          ? "Check reading"
                                          : item.status === "conflict"
                                            ? "Conflict"
                                            : item.status === "protected"
                                              ? "Locked"
                                              : item.status === "ready"
                                                ? "Review"
                                                : item.status === "replayed"
                                                  ? "Exported"
                                                  : item.status === "invalid"
                                                    ? "Check reading"
                                                    : "No change"}
                                  </span>
                                </span>
                              </button>
                            ))
                          ) : (
                            <p className="queue-empty">
                              No changes in this view.
                            </p>
                          )}
                        </div>
                        <div className="queue-footer">
                          <ShieldCheck size={15} />
                          <span>Review each paper reading.</span>
                        </div>
                      </aside>
                      {selected ? (
                        <ReviewDetail
                          key={selected.id}
                          item={selected}
                          session={session}
                          decision={decisions[selected.id]}
                          busy={Boolean(busy)}
                          onChoose={(decision) => choose(selected, decision)}
                          onVerify={(candidateId) =>
                            void verifyReading(selected, candidateId)
                          }
                          onDirtyChange={setHasUnsavedReading}
                          onSelectCandidate={(candidateId) =>
                            void mutate(
                              "Selecting paper reading",
                              `/api/sessions/${session.id}/proposals/${selected.id}/candidate`,
                              {
                                method: "POST",
                                headers: { "Content-Type": "application/json" },
                                body: JSON.stringify({
                                  candidateId,
                                  revision: session.revision,
                                }),
                              },
                            )
                          }
                          onViewSource={(scanId) => {
                            if (hasUnsavedReading) return;
                            setInitialScanId(scanId);
                            setReviewTab("sources");
                          }}
                          onSave={(text, candidateId) =>
                            void mutate(
                              "Saving transcription",
                              `/api/sessions/${session.id}/proposals/${selected.id}`,
                              {
                                method: "PATCH",
                                headers: { "Content-Type": "application/json" },
                                body: JSON.stringify({
                                  text,
                                  candidateId,
                                  revision: session.revision,
                                }),
                              },
                            )
                          }
                          position={visibleItems.findIndex(
                            (item) => item.id === selected.id,
                          )}
                          total={visibleItems.length}
                          onNavigate={(offset) => {
                            if (hasUnsavedReading) return;
                            const index = visibleItems.findIndex(
                              (item) => item.id === selected.id,
                            );
                            const next = visibleItems[index + offset];
                            if (next) setSelectedId(next.id);
                          }}
                        />
                      ) : (
                        <div className="review-filter-empty">
                          <Check size={25} />
                          <h2>
                            {filter === "pending"
                              ? "Every reading is reviewed"
                              : "No readings in this view"}
                          </h2>
                          <p>
                            {uncheckedPages
                              ? `${uncheckedPages} paper page${uncheckedPages === 1 ? " still needs" : "s still need"} a check for missed writing.`
                              : "You can inspect all readings or review the export."}
                          </p>
                          <button
                            className="button small"
                            onClick={() => {
                              setFilter("all");
                              setSelectedId(null);
                            }}
                          >
                            Show all readings
                          </button>
                          {uncheckedPages > 0 && (
                            <button
                              className="button small"
                              onClick={() => setReviewTab("sources")}
                            >
                              Check paper pages
                            </button>
                          )}
                        </div>
                      )}
                    </div>
                  ) : (
                    <div className="review-empty">
                      <span className="empty-icon">
                        <ScanLine size={35} strokeWidth={1.4} />
                      </span>
                      <h2>Add a completed paper sheet</h2>
                      <p>
                        Upload a photograph of your completed PaperPatch sheet.
                        <br />
                        You can also transcribe a correction yourself.
                      </p>
                      <div className="actions">
                        <UploadButton
                          className="button primary"
                          accept="image/png,image/jpeg,image/webp"
                          onFile={(file) => uploadToSession(file, "scan")}
                          disabled={Boolean(busy) || hasUnsavedReading}
                        >
                          <ImagePlus size={17} />
                          Upload a scan
                        </UploadButton>
                        <button
                          className="button"
                          onClick={() => {
                            setManualTarget(null);
                            setShowManual(true);
                          }}
                        >
                          Add manually
                        </button>
                        <button
                          className="text-button"
                          onClick={() => setView("paper")}
                        >
                          Back to paper copy <ArrowRight size={15} />
                        </button>
                      </div>
                    </div>
                  )}
                  <div className="export-bar">
                    <div className="export-note">
                      <LockKeyhole size={18} />
                      <p>
                        <strong>
                          {pending.length
                            ? `${pending.length} decision${pending.length === 1 ? "" : "s"} remaining`
                            : uncheckedPages
                              ? `${uncheckedPages} paper page${uncheckedPages === 1 ? "" : "s"} to check`
                              : chosenPaper
                                ? `${chosenPaper} ${chosenPaper === 1 ? "cell" : "cells"} selected for export`
                                : "Review complete"}
                        </strong>
                        <span>
                          Export updates a copy. Other cells and formulas stay
                          intact.
                        </span>
                      </p>
                    </div>
                    <button
                      className="button primary export-button"
                      disabled={
                        Boolean(busy) ||
                        !items.length ||
                        pending.length > 0 ||
                        hasUnsavedReading ||
                        !session.current ||
                        (chosenPaper === 0 && uncheckedPages === 0)
                      }
                      onClick={() => {
                        if (uncheckedPages) {
                          setReviewTab("sources");
                          setInitialScanId(
                            session.scans.find(
                              (scan) =>
                                scan.status === "ready" && !scan.reviewedAt,
                            )?.id ?? null,
                          );
                          return;
                        }
                        setShowExport(true);
                        void fetchPreview();
                      }}
                    >
                      <FileCheck2 size={18} />
                      {pending.length
                        ? "Review export"
                        : hasUnsavedReading
                          ? "Save the reading first"
                          : uncheckedPages
                            ? "Check paper pages"
                            : !chosenPaper
                              ? "No new changes to export"
                              : `Review export · ${chosenPaper} ${chosenPaper === 1 ? "change" : "changes"}`}
                      <ArrowRight size={16} />
                    </button>
                  </div>
                  {session.export && (
                    <div className="export-receipt">
                      <div className="receipt-check">
                        <Check size={24} />
                      </div>
                      <div>
                        <div className="eyebrow">Workbook ready</div>
                        <h2>
                          {session.export.count}{" "}
                          {session.export.count === 1 ? "change" : "changes"}{" "}
                          applied.
                        </h2>
                        <p>
                          Your source files are unchanged. Download the reviewed
                          copy below.
                        </p>
                        <span className="mono receipt-filename">
                          {session.export.filename}
                        </span>
                      </div>
                      <a
                        className="button primary"
                        href={session.export.url}
                        download={session.export.filename}
                      >
                        <ArrowDownToLine size={17} />
                        Download Excel
                      </a>
                    </div>
                  )}
                </>
              )}
            </>
          )}
        </main>
      )}
      <footer className="site-footer">
        <span>
          PaperPatch <span className="footer-divider">/</span> Kiroween 2026 ·
          Built with Kiro
        </span>
        <span>
          <LockKeyhole size={12} />
          Processed on this device
        </span>
      </footer>
      {showExport && session && (
        <ExportReview
          session={session}
          preview={preview}
          loading={previewLoading}
          error={previewError ?? error}
          busy={Boolean(busy)}
          onRefresh={() => void fetchPreview()}
          onReviewSources={() => {
            setShowExport(false);
            setReviewTab("sources");
            setInitialScanId(
              session.scans.find(
                (scan) => scan.status === "ready" && !scan.reviewedAt,
              )?.id ?? null,
            );
          }}
          onExport={() => void exportWorkbook()}
          onClose={() => {
            setShowExport(false);
            previewRequest.current?.abort();
          }}
        />
      )}
      {showManual && session?.base && (
        <ManualDialog
          session={session}
          target={manualTarget}
          initialText={manualInitial?.text ?? ""}
          busy={Boolean(busy)}
          error={error}
          onClose={closeManual}
          onSubmit={async (values) => {
            const saved = await mutate(
              "Adding manual transcription",
              `/api/sessions/${session.id}/proposals`,
              {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ ...values, revision: session.revision }),
              },
              "review",
            );
            if (saved) {
              closeManual();
              setSelectedId(
                saved.items.find(
                  (item) =>
                    item.rowId === values.rowId && item.field === values.field,
                )?.id ?? null,
              );
              setFilter("all");
              setReviewTab("changes");
            }
          }}
        />
      )}
    </div>
  );
}

function WorkbookSetup({
  session,
  busy,
  onSave,
  onContinue,
}: {
  session: Session;
  busy: boolean;
  onSave: (config: NonNullable<Session["config"]>) => void;
  onContinue: () => void;
}) {
  const [sheet, setSheet] = useState(
    session.config?.sheet ?? session.inspection.sheets[0]?.name ?? "",
  );
  const selectedSheet = session.inspection.sheets.find(
    (entry) => entry.name === sheet,
  );
  const headers = selectedSheet?.headers ?? [];
  const [idColumn, setIdColumn] = useState(
    session.config?.idColumn ?? headers[0] ?? "",
  );
  const [editableColumns, setEditableColumns] = useState<string[]>(
    session.config?.editableColumns ?? [],
  );
  const configured = Boolean(session.config);
  const changeSheet = (event: ChangeEvent<HTMLSelectElement>) => {
    const next = session.inspection.sheets.find(
      (entry) => entry.name === event.target.value,
    );
    setSheet(event.target.value);
    setIdColumn(next?.headers[0] ?? "");
    setEditableColumns([]);
  };
  return (
    <>
      <section className="workspace-heading">
        <div>
          <div className="eyebrow">
            <span className="short-rule" />
            01 / Workbook setup
          </div>
          <h1>Set up your paper sheet</h1>
          <p>Choose the worksheet, row identifier, and fields to print.</p>
        </div>
        <span className="status chosen">
          <FileSpreadsheet size={14} />
          Workbook received
        </span>
      </section>
      <div className="setup-layout">
        <section className="panel setup-panel">
          <div className="panel-heading">
            <FileSpreadsheet size={21} />
            <div>
              <h2>{session.filename}</h2>
              <p>Excel workbook</p>
            </div>
          </div>
          <form
            onSubmit={(event) => {
              event.preventDefault();
              onSave({ sheet, idColumn, editableColumns });
            }}
          >
            <label className="field-label" htmlFor="sheet">
              Worksheet
            </label>
            <div className="select-wrap">
              <select
                id="sheet"
                value={sheet}
                onChange={changeSheet}
                disabled={busy || configured}
              >
                {session.inspection.sheets.map((entry) => (
                  <option value={entry.name} key={entry.name}>
                    {entry.name} · {entry.rows} rows
                  </option>
                ))}
              </select>
              <ChevronDown size={16} />
            </div>
            <label className="field-label" htmlFor="id-column">
              Stable row ID
            </label>
            <p className="field-help">
              A unique, permanent value for each row. IDs keep corrections
              matched even when Excel rows move.
            </p>
            <div className="select-wrap">
              <select
                id="id-column"
                value={idColumn}
                disabled={busy || configured}
                onChange={(event) => {
                  setIdColumn(event.target.value);
                  setEditableColumns((previous) =>
                    previous.filter((value) => value !== event.target.value),
                  );
                }}
              >
                {headers.map((header) => (
                  <option key={header}>{header}</option>
                ))}
              </select>
              <ChevronDown size={16} />
            </div>
            <div className="field-label">
              Editable fields <span className="muted">Choose 1–2</span>
            </div>
            <p className="field-help">
              Choose short text or number columns. Formula cells are protected.
            </p>
            <div className="field-options">
              {headers
                .filter((header) => header !== idColumn)
                .map((header) => (
                  <label
                    key={header}
                    className={`field-option ${editableColumns.includes(header) ? "checked" : ""}`}
                  >
                    <input
                      type="checkbox"
                      checked={editableColumns.includes(header)}
                      disabled={
                        busy ||
                        configured ||
                        (!editableColumns.includes(header) &&
                          editableColumns.length >= 2)
                      }
                      onChange={(event) =>
                        setEditableColumns((previous) =>
                          event.target.checked
                            ? [...previous, header]
                            : previous.filter((value) => value !== header),
                        )
                      }
                    />
                    <span>{header}</span>
                    {editableColumns.includes(header) && <Check size={16} />}
                  </label>
                ))}
            </div>
            {configured ? (
              <button
                type="button"
                className="button primary full-width"
                onClick={onContinue}
              >
                Open paper copy
                <ArrowRight size={17} />
              </button>
            ) : (
              <button
                className="button primary full-width"
                type="submit"
                disabled={
                  busy || !sheet || !idColumn || !editableColumns.length
                }
              >
                Prepare paper copy
                <ArrowRight size={17} />
              </button>
            )}
          </form>
        </section>
        <aside className="setup-aside">
          <span className="section-number">Row matching</span>
          <h2>
            Match by ID.
            <br />
            Even after sorting.
          </h2>
          <p>
            PaperPatch remembers the workbook as it was when you printed. When
            it comes back, each correction is compared with your latest Excel
            file.
          </p>
          <div className="setup-principle">
            <ShieldCheck size={20} />
            <div>
              <strong>Every correction requires approval.</strong>
              <p>Review each proposed edit before exporting a new workbook.</p>
            </div>
          </div>
          <div className="column-preview">
            <div className="eyebrow">
              {sheet} / {selectedSheet?.rows ?? 0} rows
            </div>
            <h3>Columns in this sheet</h3>
            <div>
              {headers.map((header) => (
                <span
                  key={header}
                  className={
                    header === idColumn
                      ? "id-column"
                      : editableColumns.includes(header)
                        ? "editable-column"
                        : ""
                  }
                >
                  {header}
                  {header === idColumn && <LockKeyhole size={11} />}
                </span>
              ))}
            </div>
          </div>
        </aside>
      </div>
      {session.base && <TablePreview session={session} />}
    </>
  );
}

function TablePreview({ session }: { session: Session }) {
  const table = session.current ?? session.base;
  if (!table) return null;
  return (
    <section className="table-preview panel">
      <div className="table-preview-heading">
        <h2>Workbook preview</h2>
        <span>
          First {Math.min(5, table.rows.length)} of {table.rows.length} rows
        </span>
      </div>
      <div className="table-scroll">
        <table>
          <thead>
            <tr>
              {table.headers.map((header) => (
                <th key={header}>{header}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {table.rows.slice(0, 5).map((row) => (
              <tr key={row.id}>
                {table.headers.map((header) => (
                  <td
                    key={header}
                    className={
                      session.config?.editableColumns.includes(header)
                        ? "editable-cell"
                        : ""
                    }
                  >
                    {displayValue(row.cells[header])}
                    {row.cells[header]?.protected && <LockKeyhole size={11} />}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function ExampleGuide({
  session,
  busy,
  onReading,
  onPages,
  onExport,
  onReceipt,
  onTrial,
}: {
  session: Session;
  busy: boolean;
  onReading: (id: string) => void;
  onPages: () => void;
  onExport: () => void;
  onReceipt: () => void;
  onTrial: () => void;
}) {
  const pending = session.items.filter(
    (item) =>
      needsDecision(item) &&
      (item.requiresReadingCheck || !session.reviewDecisions[item.id]),
  );
  const disputed = pending.find((item) => item.paperConflict) ?? pending[0];
  const unchecked = session.scans.filter(
    (scan) => scan.status === "ready" && !scan.reviewedAt,
  );
  const hasChanges = Object.values(session.reviewDecisions).includes("paper");
  const exported = session.history.length > 0;
  const stage = pending.length
    ? 0
    : unchecked.length
      ? 1
      : hasChanges
        ? 2
        : exported
          ? 3
          : 2;
  const action =
    stage === 0 && disputed
      ? () => onReading(disputed.id)
      : stage === 1
        ? onPages
        : stage === 3
          ? onReceipt
          : hasChanges
            ? onExport
            : onTrial;
  const heading = [
    disputed?.paperConflict
      ? "Two crews disagree. Excel changed too."
      : "Check the remaining paper readings.",
    "Check the pages behind your decisions.",
    hasChanges
      ? "See exactly what will reach Excel."
      : "You kept the current workbook.",
    "The merge is ready to inspect.",
  ][stage];
  const detail = [
    disputed?.paperConflict
      ? "The sheet left with 12 drills. Excel now says 10; Crew A wrote 9 and Crew B wrote 8. Choose which source to keep. The harness move to Rig B should survive."
      : `${pending.length} ${pending.length === 1 ? "reading still needs" : "readings still need"} a decision. ${disputed ? `Next: ${rowTitle(session, disputed)}, ${disputed.field}. ` : ""}Compare the paper with the printed value and the current Excel value before choosing.`,
    "Inspect the whole returned page for missed marks. Checking a page and approving a value are separate decisions.",
    hasChanges
      ? "Rows were reordered in Excel. Corrections follow Asset IDs, while the latest locations, formatting and formulas stay in place."
      : "No replacement file is needed when every decision keeps Excel. You can change a decision or try the same workflow with your own handwriting.",
    "Open the receipt to compare the actual input and output files, see the approved changes, and check the preservation result.",
  ][stage];
  return (
    <section className="example-guide" aria-labelledby="example-guide-title">
      <div className="example-guide-copy">
        <span className="example-guide-label">
          Equipment handover / prepared example
        </span>
        <h2 id="example-guide-title">{heading}</h2>
        <p>{detail}</p>
      </div>
      <div className="example-guide-next">
        <ol aria-label="Example progress">
          {["Readings", "Pages", "Export", "Receipt"].map((label, index) => (
            <li
              key={label}
              className={
                index < stage ? "complete" : index === stage ? "active" : ""
              }
              aria-current={index === stage ? "step" : undefined}
            >
              <span>{index < stage ? <Check size={11} /> : index + 1}</span>
              {label}
            </li>
          ))}
        </ol>
        <button className="button small" disabled={busy} onClick={action}>
          {stage === 0
            ? disputed?.paperConflict
              ? "Compare the paper counts"
              : "Review remaining readings"
            : stage === 1
              ? `Check ${unchecked.length} paper ${unchecked.length === 1 ? "page" : "pages"}`
              : stage === 3
                ? "Inspect the receipt"
                : hasChanges
                  ? "Review the export"
                  : "Try your own handwriting"}
          <ArrowRight size={15} />
        </button>
      </div>
    </section>
  );
}

function PaperStage({
  session,
  busy,
  onUpload,
  onReview,
  onManual,
}: {
  session: Session;
  busy: boolean;
  onUpload: (file: File, kind: "scan" | "current") => void;
  onReview: () => void;
  onManual: () => void;
}) {
  const [pageIndex, setPageIndex] = useState(0);
  const pages = session.manifest?.pages ?? [];
  const page = pages[pageIndex] ?? pages[0];
  return (
    <>
      <section className="workspace-heading">
        <div>
          <div className="eyebrow">
            <span className="short-rule" />
            02 / Print & scan
          </div>
          <h1>
            {session.practice
              ? "Try a real paper handover"
              : "Print, mark, and scan"}
          </h1>
          <p>
            {session.practice
              ? "Use your own pen and photograph. This workspace starts with no prepared readings."
              : "Print your sheet, write the corrections, then bring back a clear photograph."}
          </p>
        </div>
        {session.items.length > 0 && (
          <button className="button primary" onClick={onReview}>
            Review {session.items.length} changes
            <ArrowRight size={16} />
          </button>
        )}
      </section>
      {session.practice && session.practiceResources && (
        <section className="paper-trial-brief" aria-labelledby="trial-title">
          <div>
            <span className="example-guide-label">
              A reproducible field test
            </span>
            <h2 id="trial-title">
              One workbook. Two paper copies. A change back at the office.
            </h2>
            <p>
              Print two copies of the PDF below. On the first, change drills to
              9 and helmets to 22. On the second, change drills to 8. Use your
              own handwriting and leave the other boxes blank.
            </p>
            <p>
              The supplied latest workbook changes drills to 10, moves the
              harnesses to Rig B, and reverses the rows. Upload it before
              exporting. You should resolve the competing counts while keeping
              Rig B and the formulas.
            </p>
          </div>
          <div className="paper-trial-files">
            <a
              className="button small"
              href={session.practiceResources.baselineUrl}
              download="Field-equipment-original.xlsx"
            >
              <ArrowDownToLine size={15} /> Original workbook
            </a>
            <a
              className="button small"
              href={session.practiceResources.latestUrl}
              download="Field-equipment-latest.xlsx"
            >
              <ArrowDownToLine size={15} /> Latest workbook
            </a>
            <span>
              Transfer phone photos to this computer, then upload them below.
              Nothing is pre-approved.
            </span>
          </div>
        </section>
      )}
      <div className="paper-layout">
        <section className="paper-preview panel">
          <div className="paper-preview-top">
            <span>
              <FileText size={16} />
              Your paper copy
            </span>
            <span>
              {pages.length} {pages.length === 1 ? "page" : "pages"}
            </span>
          </div>
          <div className="paper-image-area">
            {page ? (
              <a href={page.previewUrl} target="_blank" rel="noreferrer">
                <img
                  src={page.previewUrl}
                  alt={`Printable PaperPatch sheet, page ${page.number}`}
                />
              </a>
            ) : (
              <div className="empty-paper">
                <FileText size={45} />
                <p>Paper preview is not available.</p>
              </div>
            )}
          </div>
          <div className="paper-preview-bottom">
            <button
              className="button icon-button"
              aria-label="Previous page"
              disabled={pageIndex === 0}
              onClick={() => setPageIndex((previous) => previous - 1)}
            >
              <ChevronLeft size={17} />
            </button>
            <span>
              {page ? `Page ${page.number} of ${pages.length}` : "No preview"}
            </span>
            <button
              className="button icon-button"
              aria-label="Next page"
              disabled={pageIndex >= pages.length - 1}
              onClick={() => setPageIndex((previous) => previous + 1)}
            >
              <ChevronRight size={17} />
            </button>
          </div>
        </section>
        <div className="paper-instructions">
          <section className="instruction-step">
            <span className="instruction-number">1</span>
            <div>
              <h2>Download the paper sheet</h2>
              <p>
                Use the prepared PDF. Write only new values inside the
                correction boxes, and leave unchanged fields blank.
              </p>
              {session.manifest && (
                <a
                  className="button primary"
                  href={session.manifest.pdfUrl}
                  target="_blank"
                  rel="noreferrer"
                >
                  <ArrowDownToLine size={17} />
                  Download print PDF
                </a>
              )}
            </div>
          </section>
          <section className="instruction-step">
            <span className="instruction-number">2</span>
            <div>
              <h2>Upload the marked page</h2>
              <p>
                Photograph one page at a time in good light. Include all corner
                markers and keep the page flat.
              </p>
              <UploadButton
                className="button"
                accept="image/png,image/jpeg,image/webp"
                onFile={(file) => onUpload(file, "scan")}
                disabled={busy}
              >
                <ScanLine size={17} />
                Upload marked page
              </UploadButton>
              <button
                className="text-button manual-link"
                onClick={onManual}
                disabled={busy}
              >
                Or transcribe a correction manually
              </button>
            </div>
          </section>
          <section className="instruction-step">
            <span className="instruction-number">3</span>
            <div>
              <h2>Add the latest workbook</h2>
              <p>
                Someone updated the workbook after printing? Upload it here so
                those digital changes enter the comparison.
              </p>
              <UploadButton
                className="button"
                accept=".xlsx"
                onFile={(file) => onUpload(file, "current")}
                disabled={busy}
              >
                <FileSpreadsheet size={17} />
                Upload latest workbook
              </UploadButton>
              <div className="file-caption">
                <Check size={13} />
                <span>{session.currentFilename || session.filename}</span>
              </div>
            </div>
          </section>
          <div className="paper-note">
            <ShieldCheck size={18} />
            <p>
              Check each reading against its paper crop before choosing what to
              export.
            </p>
          </div>
        </div>
      </div>
    </>
  );
}

function WorkbookContext({
  session,
  item,
  decision,
}: {
  session: Session;
  item: Item;
  decision?: Decision;
}) {
  const viewport = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const container = viewport.current;
    if (!container) return;
    const revealCell = () => {
      const cell = container.querySelector(".target-cell");
      if (!cell) return;
      const bounds = cell.getBoundingClientRect();
      const frame = container.getBoundingClientRect();
      const headerHeight =
        container.querySelector("thead")?.getBoundingClientRect().height ?? 0;
      container.scrollLeft +=
        bounds.left - frame.left - (container.clientWidth - bounds.width) / 2;
      if (bounds.bottom > frame.bottom)
        container.scrollTop += bounds.bottom - frame.bottom;
      if (bounds.top < frame.top + headerHeight)
        container.scrollTop += bounds.top - frame.top - headerHeight;
    };
    const observer = new ResizeObserver(revealCell);
    observer.observe(container);
    revealCell();
    return () => observer.disconnect();
  }, [item.id, item.current.address]);
  const table = session.current;
  if (!table) return null;
  const rowIndex = table.rows.findIndex((row) => row.id === item.rowId);
  const start = Math.max(0, Math.min(rowIndex - 5, table.rows.length - 12));
  const rows = table.rows.slice(start, start + 12);
  const headers = table.headers;
  const previewPaper = decision === "paper" && item.proposed;
  return (
    <section
      className="workbook-context"
      aria-label="Destination workbook context"
    >
      <div className="section-heading">
        <span>Latest workbook</span>
        <span className="worksheet-range">
          {table.rows.length <= 12
            ? `${table.rows.length} rows`
            : `Rows ${start + 1}–${start + rows.length} of ${table.rows.length}`}
        </span>
      </div>
      <div className="formula-bar">
        <span className="cell-address mono">{item.current.address}</span>
        <span className="formula-symbol">fx</span>
        <span className="formula-value mono">{displayValue(item.current)}</span>
        {item.current.protected && <LockKeyhole size={14} />}
        <span className="formula-current-label">Current value</span>
      </div>
      <div
        ref={viewport}
        className="context-scroll"
        tabIndex={0}
        role="region"
        aria-label="Worksheet rows around the selected cell"
      >
        <table className="context-table">
          <caption className="sr-only">
            Current worksheet.{" "}
            {previewPaper
              ? "The selected paper value is shown as an export preview."
              : "The selected cell is highlighted."}
          </caption>
          <thead>
            <tr className="column-letters">
              <th aria-label="Row number" />
              {headers.map((header) => (
                <th
                  key={header}
                  className={header === item.field ? "target-column" : ""}
                >
                  {rows[0]?.cells[header]?.address.replace(/[0-9]/g, "")}
                </th>
              ))}
            </tr>
            <tr>
              <th className="row-number">1</th>
              {headers.map((header) => (
                <th
                  key={header}
                  className={header === item.field ? "target-column" : ""}
                >
                  {header}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr
                key={row.id}
                className={row.id === item.rowId ? "target-row" : ""}
              >
                <th className="row-number" scope="row">
                  {row.cells[headers[0]]?.address.replace(/[^0-9]/g, "")}
                </th>
                {headers.map((header) => {
                  const cell = row.cells[header];
                  const target = row.id === item.rowId && header === item.field;
                  return (
                    <td
                      key={header}
                      className={`${target ? "target-cell" : ""} ${target && previewPaper ? "patch-preview" : ""} ${cell?.protected ? "protected-cell" : ""}`}
                      title={displayValue(cell)}
                      aria-label={
                        target && previewPaper
                          ? `Current ${displayValue(cell)}; selected paper value ${displayValue(item.proposed)}`
                          : undefined
                      }
                    >
                      {target && previewPaper ? (
                        <>
                          <s>{displayValue(cell)}</s>
                          <strong>{displayValue(item.proposed)}</strong>
                        </>
                      ) : (
                        displayValue(cell)
                      )}
                      {cell?.protected && <LockKeyhole size={10} />}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="sheet-tab">
        <span>
          <FileSpreadsheet size={13} />
          {session.config?.sheet}
        </span>
        <span>
          {previewPaper
            ? "Selected cell preview · not applied"
            : `Matched by ${session.config?.idColumn}: ${item.rowLabel}`}
        </span>
      </div>
    </section>
  );
}

function ReviewDetail({
  item,
  session,
  decision,
  busy,
  onChoose,
  onSave,
  position,
  total,
  onNavigate,
  onDirtyChange,
  onSelectCandidate,
  onViewSource,
  onVerify,
}: {
  item: Item;
  session: Session;
  decision?: Decision;
  busy: boolean;
  onChoose: (decision: Decision) => void;
  onDirtyChange: (dirty: boolean) => void;
  onSave: (text: string, candidateId: string | null) => void;
  position: number;
  total: number;
  onNavigate: (offset: number) => void;
  onSelectCandidate: (candidateId: string) => void;
  onViewSource: (scanId: string) => void;
  onVerify: (candidateId: string) => void;
}) {
  const [initialDraft] = useState(() => readDraft(session.id, item));
  const [text, setText] = useState(initialDraft?.text ?? item.text);
  const [draftCandidateId, setDraftCandidateId] = useState<string | null>(
    initialDraft?.candidateId ?? null,
  );
  const [draftSaved, setDraftSaved] = useState(Boolean(initialDraft));
  const savedText = useRef(item.text);
  useEffect(() => {
    const previous = savedText.current;
    savedText.current = item.text;
    setText((draft) => (draft === previous ? item.text : draft));
  }, [item.text]);
  const dirty = text !== item.text;
  useEffect(() => {
    try {
      const key = `paperpatch:draft:${session.id}:${item.id}`;
      if (dirty)
        localStorage.setItem(
          key,
          JSON.stringify({ text, candidateId: draftCandidateId }),
        );
      else localStorage.removeItem(key);
      setDraftSaved(dirty);
    } catch {
      setDraftSaved(false);
    }
  }, [dirty, text, draftCandidateId, session.id, item.id]);
  useEffect(() => {
    if (!dirty || draftSaved) return;
    const warn = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty, draftSaved]);
  const currentCandidateId =
    item.selectedCandidateId ?? item.candidates.at(-1)?.id ?? null;
  const evidence = item.candidates.find(
    (candidate) =>
      candidate.id === (dirty ? draftCandidateId : currentCandidateId),
  );
  const cropUrl = evidence?.cropUrl ?? item.cropUrl;
  const source = evidence?.source ?? item.source;
  useEffect(() => {
    onDirtyChange(dirty);
    return () => onDirtyChange(false);
  }, [dirty, onDirtyChange]);
  const canDecide = needsDecision(item);
  const canUsePaper =
    ["ready", "conflict"].includes(item.status) && !item.current.protected;
  const matchingReading =
    item.requiresReadingCheck &&
    ["unchanged", "already_applied", "replayed"].includes(item.status);
  const explanation =
    item.status === "paper_conflict"
      ? "The paper copies have different values. Select a source reading, or keep Excel."
      : item.status === "conflict"
        ? "Both copies changed after printing. Choose the value to keep."
        : item.status === "ready"
          ? "Excel still matches the printed copy. Review the paper correction."
          : item.reason;
  return (
    <section className="review-detail">
      <WorkbookContext session={session} item={item} decision={decision} />
      <aside
        className="correction-inspector"
        tabIndex={-1}
        aria-label="Review the selected paper correction"
      >
        <div className="detail-header">
          <div>
            <div className="detail-location">
              <span className="mono">{item.rowLabel}</span>
              <span>/</span>
              {item.field}
              <span className="destination-tag mono">
                {item.current.address}
              </span>
            </div>
            <h2>{rowTitle(session, item)}</h2>
          </div>
          <div className="detail-navigation">
            <span className="mono">
              {position + 1} / {total}
            </span>
            <button
              aria-label="Previous correction"
              disabled={position === 0 || busy || dirty}
              onClick={() => onNavigate(-1)}
            >
              <ChevronLeft size={17} />
            </button>
            <button
              aria-label="Next correction"
              disabled={position === total - 1 || busy || dirty}
              onClick={() => onNavigate(1)}
            >
              <ChevronRight size={17} />
            </button>
          </div>
        </div>
        <div className="inspector-content">
          {item.candidates.length > 1 && (
            <section
              className={`paper-candidates ${item.paperConflict ? "unresolved" : ""}`}
              aria-label="Source readings"
            >
              <div className="section-heading">
                <span>
                  {item.paperConflict
                    ? "Paper copies disagree"
                    : "Source readings"}
                </span>
                <span>{item.candidates.length}</span>
              </div>
              {item.paperConflict && (
                <p>Select the reading to review against Excel.</p>
              )}
              <div className="candidate-list">
                {item.candidates.map((candidate) => (
                  <div className="candidate-row" key={candidate.id}>
                    <button
                      className="candidate-choice"
                      aria-pressed={
                        candidate.id === item.selectedCandidateId &&
                        !item.paperConflict
                      }
                      disabled={
                        busy ||
                        dirty ||
                        item.status === "protected" ||
                        item.status === "replayed"
                      }
                      onClick={() => onSelectCandidate(candidate.id)}
                    >
                      {candidate.cropUrl && (
                        <img src={candidate.cropUrl} alt="" />
                      )}
                      <span>
                        <strong>{candidate.text || "Unread"}</strong>
                        <small>
                          {candidate.label}
                          {candidate.page ? ` · page ${candidate.page}` : ""}
                        </small>
                      </span>
                      {candidate.id === item.selectedCandidateId &&
                        !item.paperConflict && <Check size={15} />}
                    </button>
                    {candidate.scanId && (
                      <button
                        className="candidate-page"
                        disabled={dirty}
                        onClick={() => onViewSource(candidate.scanId!)}
                        aria-label={`View page from ${candidate.label}`}
                      >
                        <FileText size={14} />
                      </button>
                    )}
                  </div>
                ))}
              </div>
            </section>
          )}
          <section
            className="source-evidence"
            aria-label="Paper evidence and transcription"
          >
            <div className="section-heading">
              <span>On paper</span>
              {evidence?.scanId ? (
                <button
                  className="source-page-link"
                  disabled={dirty}
                  onClick={() => {
                    if (evidence.scanId) onViewSource(evidence.scanId);
                  }}
                >
                  View full page
                </button>
              ) : (
                cropUrl && (
                  <a
                    href={cropUrl}
                    target="_blank"
                    rel="noreferrer"
                    aria-label="Open original crop"
                  >
                    View crop
                  </a>
                )
              )}
            </div>
            <div className="evidence-paper">
              {cropUrl ? (
                <a href={cropUrl} target="_blank" rel="noreferrer">
                  <img
                    className="paper-crop"
                    src={cropUrl}
                    alt={`Source crop for ${item.rowLabel}, ${item.field}`}
                  />
                </a>
              ) : (
                <div className="no-crop">
                  <FileText size={25} />
                  <span>No image attached</span>
                  <small>Check your original paper.</small>
                </div>
              )}
              <span className="source-label">
                {source === "demo"
                  ? "Illustrative crop · demo"
                  : source === "manual"
                    ? "Manually transcribed"
                    : "Cropped from your scan"}
              </span>
            </div>
            <div className="transcription">
              {item.requiresReadingCheck && (
                <p className="reading-check-note">
                  {evidence?.recognitionState === "needs_review"
                    ? "This mark needs a closer look. Check the crop and correct the reading if needed."
                    : "Machine reading. Check every character against the paper before choosing."}
                </p>
              )}
              <label className="field-label" htmlFor="transcription">
                {source === "demo" ? "Example reading" : "Transcribed value"}
              </label>
              <div className="transcription-input">
                <input
                  id="transcription"
                  value={text}
                  maxLength={250}
                  disabled={
                    busy ||
                    item.status === "protected" ||
                    (item.status === "replayed" && !item.requiresReadingCheck)
                  }
                  onChange={(event) => {
                    if (!dirty) setDraftCandidateId(currentCandidateId);
                    setText(event.target.value);
                  }}
                />
                <button
                  className="button small"
                  disabled={busy || !dirty}
                  onClick={() =>
                    onSave(text, draftCandidateId ?? currentCandidateId)
                  }
                >
                  Save
                </button>
              </div>
              {dirty && (
                <div className="transcription-draft">
                  <span>
                    {draftSaved
                      ? "Draft saved in this browser. Save to add it to the review."
                      : "Draft could not be saved in this browser. Save before leaving this cell."}
                  </span>
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => setText(item.text)}
                  >
                    Discard edit
                  </button>
                </div>
              )}
            </div>
          </section>
          <div className="inspector-review">
            <dl className="value-history">
              <div>
                <dt>When printed</dt>
                <dd>{displayValue(item.baseline)}</dd>
              </div>
              <div>
                <dt>Now in Excel</dt>
                <dd>{displayValue(item.current)}</dd>
              </div>
            </dl>
            <div
              className={`reason-box ${["conflict", "paper_conflict", "invalid"].includes(item.status) ? "attention" : ""}`}
            >
              <Status item={item} decided={decision} />
              <p>{explanation}</p>
            </div>
            {matchingReading ? (
              <div className="decision-area">
                <div className="decision-prompt">
                  <strong>Does the reading match the paper?</strong>
                  <span>
                    A machine guess that looks unchanged still needs a check.
                  </span>
                </div>
                <button
                  className="button primary"
                  disabled={busy || dirty || !item.selectedCandidateId}
                  onClick={() => {
                    if (item.selectedCandidateId)
                      onVerify(item.selectedCandidateId);
                  }}
                >
                  <Check size={16} />
                  Reading is correct
                </button>
                <button
                  className="text-button dismiss-reading"
                  disabled={busy || dirty}
                  onClick={() => onChoose("current")}
                >
                  Keep Excel without using this reading
                </button>
              </div>
            ) : canDecide ? (
              <div className="decision-area">
                <div className="decision-prompt">
                  <strong>
                    {decision ? "Decision recorded" : "Keep which value?"}
                  </strong>
                  <span>
                    {dirty
                      ? "Save the reading before choosing."
                      : decision
                        ? "You can change this before export."
                        : item.requiresReadingCheck
                          ? "Using paper confirms this exact reading."
                          : "Your file changes only on export."}
                  </span>
                </div>
                <div className="decision-buttons">
                  <button
                    className={`button decision-button ${decision === "current" ? "is-chosen" : ""}`}
                    disabled={busy || dirty}
                    aria-pressed={decision === "current"}
                    onClick={() => onChoose("current")}
                  >
                    {decision === "current" && <Check size={16} />}Keep Excel{" "}
                    <span className="mono">{displayValue(item.current)}</span>
                  </button>
                  <button
                    className={`button decision-button ${decision === "paper" ? "is-chosen" : ""}`}
                    disabled={busy || dirty || !canUsePaper}
                    aria-pressed={decision === "paper"}
                    onClick={() => onChoose("paper")}
                  >
                    {decision === "paper" && <Check size={16} />}Use paper{" "}
                    <span className="mono">
                      {item.paperConflict
                        ? "Select a source"
                        : item.proposed
                          ? displayValue(item.proposed)
                          : "Invalid"}
                    </span>
                  </button>
                </div>
              </div>
            ) : (
              <div className="no-decision">
                <LockKeyhole size={16} />
                <span>
                  {item.status === "protected"
                    ? "Formula and protected cells cannot be overwritten."
                    : "No update will be made for this correction."}
                </span>
              </div>
            )}
          </div>
        </div>
      </aside>
    </section>
  );
}

function ManualDialog({
  session,
  target,
  initialText,
  busy,
  error,
  onClose,
  onSubmit,
}: {
  session: Session;
  target: ManualTarget | null;
  initialText: string;
  busy: boolean;
  error: string | null;
  onClose: () => void;
  onSubmit: (values: ManualTarget & { text: string }) => void;
}) {
  const [rowId, setRowId] = useState(
    target?.rowId ?? session.base?.rows[0]?.id ?? "",
  );
  const [field, setField] = useState(
    target?.field ?? session.config?.editableColumns[0] ?? "",
  );
  const [text, setText] = useState(initialText);
  const [draftSaved, setDraftSaved] = useState(Boolean(initialText));
  useEffect(() => {
    try {
      const key = `paperpatch:manual:${session.id}`;
      if (text)
        localStorage.setItem(
          key,
          JSON.stringify({
            rowId,
            field,
            text,
            scanId: target?.scanId,
            boxId: target?.boxId,
          }),
        );
      else localStorage.removeItem(key);
      setDraftSaved(Boolean(text));
    } catch {
      setDraftSaved(false);
    }
  }, [rowId, field, text, target?.scanId, target?.boxId, session.id]);
  useEffect(() => {
    if (!text || draftSaved) return;
    const warn = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [text, draftSaved]);
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    dialog.current?.showModal();
  }, []);
  const submit = (event: FormEvent) => {
    event.preventDefault();
    onSubmit({ ...target, rowId, field, text });
  };
  return (
    <dialog
      ref={dialog}
      className="manual-dialog"
      onCancel={onClose}
      onClose={onClose}
    >
      <div className="dialog-header">
        <span className="eyebrow">Explicit manual entry</span>
        <button
          className="icon-button"
          aria-label="Close manual entry"
          onClick={onClose}
        >
          <X size={19} />
        </button>
      </div>
      <h2>Transcribe a correction</h2>
      <p>
        Enter one correction yourself. It will be labeled as a manual
        transcription and still needs review.
      </p>
      {error && (
        <p className="manual-error" role="alert">
          {error}
        </p>
      )}
      <form onSubmit={submit}>
        <label htmlFor="manual-row" className="field-label">
          Row
        </label>
        <select
          id="manual-row"
          value={rowId}
          disabled={Boolean(target?.scanId) || busy}
          onChange={(event) => setRowId(event.target.value)}
        >
          {session.base?.rows.map((row) => (
            <option value={row.id} key={row.id}>
              {row.label} ·{" "}
              {rowTitle(session, { rowId: row.id, rowLabel: row.label })}
            </option>
          ))}
        </select>
        <label htmlFor="manual-field" className="field-label">
          Field
        </label>
        <select
          id="manual-field"
          value={field}
          disabled={Boolean(target?.scanId) || busy}
          onChange={(event) => setField(event.target.value)}
        >
          {session.config?.editableColumns.map((column) => (
            <option key={column}>{column}</option>
          ))}
        </select>
        <label htmlFor="manual-text" className="field-label">
          Handwritten value
        </label>
        <input
          id="manual-text"
          value={text}
          maxLength={250}
          disabled={busy}
          onChange={(event) => setText(event.target.value)}
          required
          autoComplete="off"
        />
        {text && (
          <p className="manual-draft-note">
            {draftSaved
              ? "Draft saved in this browser."
              : "Draft could not be saved in this browser. Add it for review before leaving."}
          </p>
        )}
        <div className="dialog-actions">
          <button className="button" type="button" onClick={onClose}>
            Cancel
          </button>
          <button
            className="button primary"
            type="submit"
            disabled={busy || !rowId || !field || !text.trim()}
          >
            Add for review
            <ArrowRight size={16} />
          </button>
        </div>
      </form>
    </dialog>
  );
}
