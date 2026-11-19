import { useCallback, useEffect, useRef, useState } from "react";
import { z } from "zod";
import { ArrowDownToLine, ArrowLeft, Check, ChevronDown, FileText, LoaderCircle, Plus, RotateCcw, Upload, X } from "lucide-react";
import { ImageSelection, PhotoPreparation } from "./TranscriptionImageEditor";
import type { Preparation, Selection } from "./TranscriptionImageEditor";
import "./documents.css";
import "./transcription.css";

const fileUrl = z.string().startsWith("/api/transcriptions/");
const recognitionSchema = z.object({
  id: z.string(), rect: z.tuple([z.number(), z.number(), z.number(), z.number()]), cropUrl: fileUrl,
  text: z.string(), alternatives: z.array(z.string()), warning: z.string().nullable(), createdAt: z.string(),
});
const lineSchema = z.object({
  id: z.string(),
  rect: z.tuple([z.number(), z.number(), z.number(), z.number()]).nullable(),
  cropUrl: fileUrl.nullable(),
  ocrText: z.string(),
  ocrAlternatives: z.array(z.string()),
  text: z.string(),
  confirmed: z.boolean(),
  dismissed: z.boolean(),
  paragraphBreak: z.boolean(),
  source: z.enum(["ocr", "manual"]),
  warning: z.string().nullable(),
  recognitionHistory: z.array(recognitionSchema).default([]),
  recognitionCandidate: recognitionSchema.nullable().default(null),
  originalRect: z.tuple([z.number(), z.number(), z.number(), z.number()]).nullable().optional(),
  originalCropUrl: fileUrl.nullable().optional(),
});
const sessionSchema = z.object({
  id: z.string().regex(/^[a-f0-9]{32}$/),
  revision: z.number().int().nonnegative(),
  status: z.enum(["processing", "ready", "failed"]),
  filename: z.string(),
  sourceSha256: z.string(),
  imageWidth: z.number(),
  imageHeight: z.number(),
  previewUrl: fileUrl,
  originalUrl: fileUrl,
  createdAt: z.string(),
  lines: z.array(lineSchema),
  progress: z.object({ completed: z.number(), total: z.number() }),
  warning: z.string().nullable(),
  error: z.string().nullable(),
  pageReviewed: z.boolean(),
  export: z.object({ url: fileUrl, auditUrl: fileUrl, filename: z.string() }).nullable(),
  pendingRecognition: z.object({ id: z.string(), lineId: z.string(), startedAt: z.string() }).nullable().default(null),
  preparation: z.object({ rotation: z.number(), straighten: z.number(), crop: z.tuple([z.number(), z.number(), z.number(), z.number()]) }).optional(),
});
type Session = z.infer<typeof sessionSchema>;
type Line = z.infer<typeof lineSchema>;
type Draft = { text: string; paragraphBreak: boolean; baseText: string };
const draftsSchema = z.record(z.string(), z.object({ text: z.string(), paragraphBreak: z.boolean(), baseText: z.string() }));

class RequestError extends Error {
  constructor(message: string, readonly status: number) { super(message); }
}

async function request(url: string, init?: RequestInit): Promise<Session> {
  const response = await fetch(url, init);
  const data: unknown = await response.json();
  if (!response.ok) {
    const detail = z.object({ detail: z.string() }).safeParse(data);
    throw new RequestError(detail.success ? detail.data.detail : `Request failed (${response.status}).`, response.status);
  }
  return sessionSchema.parse(data);
}

function errorMessage(error: unknown): string {
  if (error instanceof z.ZodError) return "The saved transcription could not be read. Reload the page and try again.";
  return error instanceof Error ? error.message : "This action could not be completed. Try again.";
}

function paragraphs(lines: Line[]): string[] {
  return lines.filter((line) => !line.dismissed).reduce<string[]>((result, line) => {
    if (!result.length || line.paragraphBreak) result.push(line.text);
    else result[result.length - 1] += `\n${line.text}`;
    return result;
  }, []);
}

export default function TranscriptionApp() {
  const initialId = useRef(new URLSearchParams(window.location.search).get("transcription"));
  const [session, setSession] = useState<Session | null>(null);
  const [busy, setBusy] = useState<string | null>(initialId.current ? "Opening transcription" : null);
  const [error, setError] = useState<string | null>(null);
  const [pollFailures, setPollFailures] = useState(0);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [drafts, setDrafts] = useState<Record<string, Draft>>({});
  const [storageWarning, setStorageWarning] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [zoom, setZoom] = useState(false);
  const [preview, setPreview] = useState(false);
  const [adding, setAdding] = useState(false);
  const [manualText, setManualText] = useState("");
  const [addAfter, setAddAfter] = useState<string | null>(null);
  const [completenessChecked, setCompletenessChecked] = useState(false);
  const [pendingFile, setPendingFile] = useState<File | null>(null);
  const [selectionEditor, setSelectionEditor] = useState<{ lineId: string; rect: Selection } | null>(null);
  const input = useRef<HTMLInputElement>(null);
  const rowRefs = useRef(new Map<string, HTMLLIElement>());
  const sourceScroll = useRef<HTMLDivElement>(null);
  const sourceRegion = useRef<HTMLDivElement>(null);
  const dirty = Object.keys(drafts).length > 0 || manualText.length > 0;
  const selected = session?.lines.find((line) => line.id === selectedId);
  const unresolved = session?.lines.filter((line) => !line.confirmed && !line.dismissed).length ?? 0;
  const included = session?.lines.filter((line) => !line.dismissed).length ?? 0;
  const pendingChoices = session?.lines.filter((line) => line.recognitionCandidate).length ?? 0;
  const actionBusy = !!busy || !!session?.pendingRecognition;
  const prepared = !!session?.preparation && (session.preparation.rotation !== 0 || session.preparation.straighten !== 0 || session.preparation.crop.some((value, index) => value !== (index < 2 ? 0 : 1)));

  const adoptSession = useCallback((value: Session) => {
    setSession(value);
    setCompletenessChecked(value.pageReviewed);
    setSelectedId((id) => value.lines.some((line) => line.id === id) ? id : value.lines[0]?.id ?? null);
  }, []);

  useEffect(() => {
    const id = initialId.current;
    if (!id) return;
    let active = true;
    const abort = new AbortController();
    request(`/api/transcriptions/${encodeURIComponent(id)}`, { signal: abort.signal }).then((value) => {
      if (!active) return;
      adoptSession(value);
      try {
        const stored: unknown = JSON.parse(localStorage.getItem(`paperpatch:transcription:${id}`) ?? "{}");
        const parsed = draftsSchema.safeParse(stored);
        if (parsed.success) setDrafts(Object.fromEntries(Object.entries(parsed.data).filter(([lineId]) => value.lines.some((line) => line.id === lineId))));
      } catch { setStorageWarning(true); }
    }).catch((reason: unknown) => {
      if (active) setError(errorMessage(reason));
    }).finally(() => { if (active) setBusy(null); });
    return () => { active = false; abort.abort(); };
  }, [adoptSession]);

  useEffect(() => {
    if (!session || (session.status !== "processing" && !session.pendingRecognition)) return;
    const abort = new AbortController();
    const timeout = window.setTimeout(() => {
      request(`/api/transcriptions/${session.id}`, { signal: abort.signal }).then((value) => {
        adoptSession(value); setPollFailures(0); setError(null);
      }).catch((reason: unknown) => {
        if (!abort.signal.aborted) { setError(`${errorMessage(reason)} Retrying automatically.`); setPollFailures((count) => count + 1); }
      });
    }, Math.min(1200 * (pollFailures + 1), 10000));
    return () => { window.clearTimeout(timeout); abort.abort(); };
  }, [session, adoptSession, pollFailures]);

  useEffect(() => {
    if (!session) return;
    try {
      const key = `paperpatch:transcription:${session.id}`;
      if (Object.keys(drafts).length) localStorage.setItem(key, JSON.stringify(drafts));
      else localStorage.removeItem(key);
      setStorageWarning(false);
    } catch { setStorageWarning(true); }
  }, [drafts, session?.id]);

  useEffect(() => {
    if (!dirty) return;
    const warn = (event: BeforeUnloadEvent) => { event.preventDefault(); };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);

  useEffect(() => {
    if (!zoom || !selectedId) return;
    const frame = requestAnimationFrame(() => {
      const container = sourceScroll.current;
      const region = sourceRegion.current;
      if (!container || !region) return;
      const box = container.getBoundingClientRect();
      const mark = region.getBoundingClientRect();
      container.scrollBy({ top: mark.top - box.top - box.height / 2 + mark.height / 2, behavior: "smooth" });
    });
    return () => cancelAnimationFrame(frame);
  }, [selectedId, zoom]);

  useEffect(() => {
    if (selectionEditor && (selectedId !== selectionEditor.lineId || session?.lines.find((line) => line.id === selectionEditor.lineId)?.recognitionCandidate)) setSelectionEditor(null);
  }, [selectedId, session?.revision, selectionEditor]);

  function chooseImage(file: File) {
    if (file.size > 20 * 1024 * 1024) { setError("Choose a page image smaller than 20 MB."); return; }
    if (!/\.(jpe?g|png|webp)$/i.test(file.name) && !["image/jpeg", "image/png", "image/webp"].includes(file.type)) {
      setError("Choose a JPEG, PNG, or WebP page image."); return;
    }
    setError(null); setPendingFile(file);
  }

  async function upload(file: File, preparation: Preparation) {
    setError(null);
    setBusy("Uploading page");
    const form = new FormData();
    form.append("file", file);
    form.append("preparation", JSON.stringify(preparation));
    try {
      const value = await request("/api/transcriptions", { method: "POST", body: form });
      setDrafts({});
      adoptSession(value);
      setPendingFile(null);
      window.history.replaceState(null, "", `?transcription=${value.id}`);
    } catch (reason) { setError(errorMessage(reason)); }
    finally { setBusy(null); }
  }

  async function mutate(path: string, body: object, label: string, method = "POST"): Promise<Session | null> {
    if (!session || actionBusy) return null;
    setBusy(label);
    setError(null);
    try {
      const value = await request(`/api/transcriptions/${session.id}${path}`, {
        method, headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ...body, revision: session.revision }),
      });
      adoptSession(value);
      return value;
    } catch (reason) {
      if ((reason instanceof RequestError && reason.status === 409) || path.endsWith("/recognize")) {
        try { adoptSession(await request(`/api/transcriptions/${session.id}`)); }
        catch { /* Keep the existing page and local edits when refresh also fails. */ }
      }
      setError(errorMessage(reason));
      return null;
    } finally { setBusy(null); }
  }

  function editLine(line: Line, changes: Partial<Pick<Draft, "text" | "paragraphBreak">>) {
    setDrafts((current) => {
      const saved = current[line.id] ?? { text: line.text, paragraphBreak: line.paragraphBreak, baseText: line.text };
      const draft = { ...saved, ...changes };
      const next = { ...current, [line.id]: draft };
      if (draft.text === line.text && draft.paragraphBreak === line.paragraphBreak) delete next[line.id];
      return next;
    });
  }

  async function saveLine(line: Line, dismissed = false) {
    const draft = drafts[line.id];
    const value = await mutate(`/lines/${line.id}`, {
      text: draft?.text ?? line.text, paragraphBreak: draft?.paragraphBreak ?? line.paragraphBreak,
      confirmed: !dismissed, dismissed,
    }, dismissed ? "Removing line" : "Saving reading", "PATCH");
    if (!value) return;
    setDrafts((current) => { const next = { ...current }; delete next[line.id]; return next; });
    if (!dismissed) {
      const index = value.lines.findIndex((item) => item.id === line.id);
      const next = [...value.lines.slice(index + 1), ...value.lines.slice(0, index)].find((item) => !item.confirmed && !item.dismissed);
      if (next) { setSelectedId(next.id); rowRefs.current.get(next.id)?.scrollIntoView({ block: "nearest", behavior: "smooth" }); }
    }
  }

  async function restoreLine(line: Line) {
    await mutate(`/lines/${line.id}`, { text: line.text, paragraphBreak: line.paragraphBreak, confirmed: false, dismissed: false }, "Restoring line", "PATCH");
  }

  async function addLine() {
    if (!manualText.trim()) return;
    const value = await mutate("/lines", { text: manualText, afterId: addAfter, paragraphBreak: true }, "Adding text");
    if (value) {
      const newLine = value.lines.find((line) => !session?.lines.some((old) => old.id === line.id));
      if (newLine) setSelectedId(newLine.id);
      setManualText(""); setAdding(false);
    }
  }

  async function retrySelection() {
    if (!selectionEditor) return;
    const value = await mutate(`/lines/${selectionEditor.lineId}/recognize`, { rect: selectionEditor.rect }, "Reading selected handwriting");
    if (value) setSelectionEditor(null);
  }

  async function chooseRecognition(line: Line, useCandidate: boolean) {
    const candidate = line.recognitionCandidate;
    if (!candidate) return;
    const value = await mutate(`/lines/${line.id}/recognition-choice`, { attemptId: candidate.id, useCandidate }, useCandidate ? "Using new reading" : "Keeping current text");
    if (!value || !useCandidate) return;
    const updated = value.lines.find((item) => item.id === line.id);
    if (!updated) return;
    setDrafts((current) => {
      const next = { ...current };
      const existing = current[line.id];
      if (existing && existing.paragraphBreak !== updated.paragraphBreak) next[line.id] = { text: updated.text, baseText: updated.text, paragraphBreak: existing.paragraphBreak };
      else delete next[line.id];
      return next;
    });
  }

  return <div className="doc-app transcription-app">
    <a className="doc-skip" href="#transcription-main">Skip to transcription</a>
    <header className="transcription-header">
      <a className="transcription-brand" href="?mode=documents"><FileText size={23} strokeWidth={1.5} /> PaperPatch</a>
      <span className="transcription-breadcrumb">Handwriting to Word</span>
      <a className="transcription-header-link" href="?mode=documents">Document reviews</a>
    </header>
    {error && <div className="transcription-notice error" role="alert"><span>{error}</span><button aria-label="Dismiss error" onClick={() => setError(null)}><X size={17} /></button></div>}
    {storageWarning && dirty && <div className="transcription-notice" role="status">Your edits are only in this tab. Save them before closing it.</div>}
    {!session ? <main className="transcription-start" id="transcription-main">
      <h1>Turn a handwritten page into a Word document</h1>
      <p>Open a scan or photo. Check the recognized text against the page, then save an editable document.</p>
      {pendingFile ? <PhotoPreparation key={`${pendingFile.name}:${pendingFile.lastModified}`} file={pendingFile} busy={actionBusy} onCancel={() => { setPendingFile(null); setError(null); }} onRead={(preparation) => void upload(pendingFile, preparation)} /> : <section className={`transcription-upload ${dragging ? "dragging" : ""}`} onDragOver={(event) => { event.preventDefault(); setDragging(true); }} onDragLeave={() => setDragging(false)} onDrop={(event) => {
        event.preventDefault(); setDragging(false); const file = event.dataTransfer.files[0]; if (file && !actionBusy) chooseImage(file);
      }}>
        <Upload size={25} strokeWidth={1.5} />
        <h2>{busy ?? (dragging ? "Drop your page here" : "Open a handwritten page")}</h2>
        <p>JPEG or PNG · one page at a time</p>
        <button className="doc-button primary" disabled={actionBusy} onClick={() => input.current?.click()}>{busy ? <LoaderCircle className="transcription-spinner" size={17} /> : <Plus size={17} />} Choose page image</button>
        <input type="file" accept="image/jpeg,image/png,image/webp,.jpg,.jpeg,.png,.webp" ref={input} hidden onChange={(event) => { const file = event.target.files?.[0]; if (file) chooseImage(file); event.target.value = ""; }} />
      </section>}
      <p className="transcription-local">Recognition runs on this computer. Your original page stays attached to the transcription.</p>
    </main> : <main id="transcription-main">
      <div className="transcription-toolbar">
        <a className="transcription-back" href="?mode=handwriting" aria-label="Open another page"><ArrowLeft size={18} /></a>
        <div className="transcription-title"><h1>{session.filename}</h1><span>{session.status === "processing" ? "Reading handwriting…" : session.status === "failed" ? "Recognition could not finish" : `${included} lines · ${unresolved ? `${unresolved} to review` : "readings confirmed"}`}{dirty && " · unsaved edits"}</span></div>
        <div className="transcription-actions">
          {busy && <span className="transcription-saving" role="status"><LoaderCircle size={14} className="transcription-spinner" />{busy}</span>}
          {session.export && !dirty && !actionBusy && !pendingChoices ? <a className="doc-button primary compact" href={session.export.url} download><ArrowDownToLine size={16} /> Download Word</a> : <button className="doc-button primary compact" disabled={actionBusy || dirty || pendingChoices > 0 || !session.pageReviewed || session.status !== "ready"} onClick={() => void mutate("/export", {}, "Creating Word document")}><ArrowDownToLine size={16} /> Create Word document</button>}
        </div>
      </div>
      {session.status === "processing" && <div className="transcription-progress" role="status"><LoaderCircle size={16} className="transcription-spinner" /><span>{session.progress.total ? `Reading line ${Math.min(session.progress.completed + 1, session.progress.total)} of ${session.progress.total}` : "Finding text on the page"}. You can keep this page open while it runs.</span></div>}
      {session.pendingRecognition && <div className="transcription-progress" role="status"><LoaderCircle size={16} className="transcription-spinner" /><span>Reading the adjusted selection. Your existing text is kept while the reader works.</span></div>}
      {session.error && <div className="transcription-notice error" role="alert">{session.error} <a href="?mode=handwriting">Open another image</a></div>}
      {session.warning && <div className="transcription-notice">{session.warning}</div>}
      <div className="transcription-workspace">
        <section className="transcription-source" aria-label="Original handwritten page">
          <div className="transcription-pane-heading"><h2>{selectionEditor ? "Adjust line selection" : prepared ? "Reading copy" : "Original page"}</h2><div><button onClick={() => setZoom(!zoom)}>{zoom ? "Fit page" : "Enlarge"}</button><a href={session.originalUrl} target="_blank" rel="noreferrer">Open original</a></div></div>
          {selectionEditor && <div className="transcription-selection-help"><p>Include one complete line. Drag the corners to resize, or drag outside the box to draw a new selection.</p><span>Your current wording stays until you choose a new reading.</span></div>}
          <div className="transcription-source-scroll" ref={sourceScroll}><div className={`transcription-image ${zoom ? "enlarged" : ""}`} style={{ width: zoom ? "150%" : `min(100%, calc((100vh - 300px) * ${session.imageWidth / Math.max(1, session.imageHeight)}))` }}>
            {selectionEditor ? <ImageSelection src={session.previewUrl} alt={`Choose handwriting for line ${session.lines.findIndex((line) => line.id === selectionEditor.lineId) + 1}`} selection={selectionEditor.rect}
              disabled={actionBusy} onChange={(rect) => setSelectionEditor((current) => current ? { ...current, rect } : null)} /> : <img src={session.previewUrl} alt={`${prepared ? "Prepared" : "Original"} handwritten page: ${session.filename}`} />}
            {!selectionEditor && selected?.rect && selected.source === "ocr" && !preview && <div className="transcription-region" ref={sourceRegion} style={{ left: `${selected.rect[0] * 100}%`, top: `${selected.rect[1] * 100}%`, width: `${selected.rect[2] * 100}%`, height: `${selected.rect[3] * 100}%` }} />}
          </div></div>
          {selectionEditor && <div className="transcription-selection-actions"><button className="doc-button compact primary" disabled={actionBusy} onClick={() => void retrySelection()}>{actionBusy ? <LoaderCircle size={14} className="transcription-spinner" /> : <RotateCcw size={14} />}Read this selection again</button><button className="transcription-quiet" disabled={actionBusy} onClick={() => setSelectionEditor(null)}>Cancel</button></div>}
          <div className="transcription-source-caption">{session.imageWidth} × {session.imageHeight} pixels · uploaded original retained</div>
        </section>
        <section className="transcription-review" aria-label="Recognized text">
          <div className="transcription-pane-heading"><h2>{preview ? "Document text" : "Recognized text"}</h2><button disabled={session.status !== "ready"} onClick={() => setPreview(!preview)}>{preview ? "Review readings" : "Preview document"}</button></div>
          {preview ? <div className="transcription-document">{dirty && <p className="transcription-preview-note">Preview shows saved text. Save your edits to include them.</p>}{paragraphs(session.lines).map((text, index) => <p key={index}>{text}</p>)}{!included && <p>No text has been included yet.</p>}</div> : <>
            <div className="transcription-review-intro"><p>Compare each reading with the handwriting. Select a line to correct it.</p><span>Nothing is confirmed automatically.</span></div>
            {session.status === "ready" && !session.lines.length && <p className="transcription-empty">No text lines were detected. Add the missing text below while checking the original page.</p>}
            <ol className="transcription-lines">
              {session.lines.map((line, index) => {
                const active = selectedId === line.id;
                const draft = drafts[line.id];
                return <li key={line.id} ref={(element) => { if (element) rowRefs.current.set(line.id, element); else rowRefs.current.delete(line.id); }} className={`transcription-line ${active ? "selected" : ""} ${line.dismissed ? "dismissed" : ""}`}>
                  <button className="transcription-line-select" onClick={() => setSelectedId(active ? null : line.id)} aria-expanded={active}>
                    <span className="transcription-line-number">{index + 1}</span>
                    <span className="transcription-line-text">{(draft?.text ?? line.text) || "No readable text"}{line.source === "manual" && <small>Added manually</small>}</span>
                    <span className="transcription-line-state">{draft ? "Unsaved" : line.dismissed ? "Omitted" : line.confirmed ? <Check size={16} aria-label="Confirmed" /> : <span className="transcription-unchecked" aria-label="Needs review" />}</span><ChevronDown size={13} />
                  </button>
                  {active && <div className="transcription-line-editor">
                    {line.cropUrl && <img className="transcription-crop" src={line.cropUrl} alt={`Handwriting for line ${index + 1}`} />}
                    {line.source === "ocr" && line.rect && !line.dismissed && <button className="transcription-quiet transcription-adjust-selection" disabled={actionBusy || session.status !== "ready" || !!line.recognitionCandidate} onClick={() => {
                      setSelectionEditor({ lineId: line.id, rect: [...line.rect!] }); setPreview(false);
                      sourceScroll.current?.scrollIntoView({ block: "start", behavior: "smooth" });
                    }}>Adjust selection and read again</button>}
                    {line.recognitionCandidate && <div className="transcription-candidate" aria-label={`New reading for line ${index + 1}`}>
                      <h3>New reading from your selection</h3>
                      <img className="transcription-crop" src={line.recognitionCandidate.cropUrl} alt={`Adjusted handwriting selection for line ${index + 1}`} />
                      <p className="transcription-candidate-text">{line.recognitionCandidate.text || "No readable text returned."}</p>
                      {line.recognitionCandidate.warning && <p className="transcription-line-warning">{line.recognitionCandidate.warning}</p>}
                      {line.recognitionCandidate.alternatives.length > 0 && <details className="transcription-original-reading"><summary>Other suggestions</summary>{line.recognitionCandidate.alternatives.map((text) => <p key={text}>{text}</p>)}</details>}
                      <p>Your current text has not changed. Choose what to keep, then confirm the line.</p>
                      <div className="transcription-line-buttons"><button className="doc-button compact" disabled={actionBusy || !line.recognitionCandidate.text.trim()} onClick={() => void chooseRecognition(line, true)}>{draft && draft.text !== line.text ? "Replace unsaved text with this reading" : "Use this reading"}</button>
                        <button className="transcription-quiet" disabled={actionBusy} onClick={() => void chooseRecognition(line, false)}>{draft ? "Keep my current text and edits" : "Keep current text"}</button></div>
                    </div>}
                    {draft && draft.baseText !== line.text && <p className="transcription-line-warning">This reading changed in another tab. Your unsaved wording is shown below; compare it before saving.</p>}
                    <label>Text for the Word document<textarea aria-label={`Text for line ${index + 1}`} rows={2} value={draft?.text ?? line.text} disabled={actionBusy || session.status !== "ready" || line.dismissed} onChange={(event) => editLine(line, { text: event.target.value })} /></label>
                    <label className="transcription-check"><input type="checkbox" checked={draft?.paragraphBreak ?? line.paragraphBreak} disabled={actionBusy || session.status !== "ready" || line.dismissed} onChange={(event) => editLine(line, { paragraphBreak: event.target.checked })} />Start a new paragraph here</label>
                    {line.source === "ocr" && <details className="transcription-original-reading"><summary>Original machine reading{line.text !== line.ocrText ? " · edited during review" : ""}</summary>{line.originalCropUrl && line.originalCropUrl !== line.cropUrl && <img className="transcription-crop" src={line.originalCropUrl} alt={`Original detected crop for line ${index + 1}`} />}<p>{line.ocrText || "No readable text returned."}</p>{line.ocrAlternatives.map((text) => <button key={text} disabled={actionBusy || session.status !== "ready" || line.dismissed || !!line.recognitionCandidate} onClick={() => editLine(line, { text })}>Use other reading: {text}</button>)}</details>}
                    {line.recognitionHistory.length > 0 && <details className="transcription-original-reading"><summary>Recognition history · {line.recognitionHistory.length} {line.recognitionHistory.length === 1 ? "attempt" : "attempts"}</summary>{line.recognitionHistory.map((attempt, attemptIndex) => <div key={attempt.id} className="transcription-recognition-history"><span>Attempt {attemptIndex + 1}</span><img className="transcription-crop" src={attempt.cropUrl} alt={`Selection used for attempt ${attemptIndex + 1}`} /><p>{attempt.text || "No readable text returned."}</p></div>)}</details>}
                    {line.warning && <p className="transcription-line-warning">{line.warning}</p>}
                    <div className="transcription-line-buttons">{line.dismissed ? <button className="doc-button compact" disabled={actionBusy} onClick={() => void restoreLine(line)}><RotateCcw size={14} /> Restore line</button> : <>
                      <button className="doc-button compact primary" disabled={actionBusy || session.status !== "ready" || !!line.recognitionCandidate || !(draft?.text ?? line.text).trim()} onClick={() => void saveLine(line)}><Check size={14} />{draft ? "Save and confirm" : "Confirm reading"}</button>
                      <button className="transcription-quiet" disabled={actionBusy || session.status !== "ready" || !!line.recognitionCandidate} onClick={() => void saveLine(line, true)}>Omit this line</button>
                      {draft && <button className="transcription-quiet" disabled={actionBusy} onClick={() => setDrafts((current) => { const next = { ...current }; delete next[line.id]; return next; })}>Discard edits</button>}
                    </>}</div>
                  </div>}
                </li>;
              })}
            </ol>
            {session.status === "ready" && <div className="transcription-add">
              {!adding ? <button className="transcription-quiet" onClick={() => { setAdding(true); setAddAfter(selectedId ?? session.lines.at(-1)?.id ?? null); }}><Plus size={14} /> Add text the reader missed</button> : <div className="transcription-manual-form">
                <label>Insert after<select value={addAfter ?? ""} disabled={actionBusy} onChange={(event) => setAddAfter(event.target.value || null)}><option value="">Beginning of the page</option>{session.lines.map((line, index) => <option key={line.id} value={line.id}>Line {index + 1}: {line.text.slice(0, 55)}</option>)}</select></label>
                <label>Missing text<textarea value={manualText} disabled={actionBusy} onChange={(event) => setManualText(event.target.value)} rows={3} /></label>
                <div><button className="doc-button compact" disabled={actionBusy || !manualText.trim()} onClick={() => void addLine()}>Add to transcription</button><button className="transcription-quiet" disabled={actionBusy} onClick={() => { setAdding(false); setManualText(""); }}>Cancel</button></div>
              </div>}
            </div>}
          </>}
          {session.status === "ready" && <div className="transcription-finish">
            {pendingChoices > 0 && <p className="transcription-line-warning">Choose whether to use each new reading or keep your current text before confirming.</p>}
            {unresolved > 0 && <div className="transcription-confirm-all"><span>{unresolved} {unresolved === 1 ? "reading still needs" : "readings still need"} your review.</span><button className="doc-button compact" disabled={actionBusy || dirty || pendingChoices > 0} onClick={() => void mutate("/confirm", {}, "Confirming readings")}>Confirm all readings</button></div>}
            <label className="transcription-check"><input type="checkbox" checked={completenessChecked} disabled={actionBusy || dirty || unresolved > 0 || !included || session.pageReviewed} onChange={(event) => setCompletenessChecked(event.target.checked)} />I checked the entire page for missing text and paragraph order.</label>
            {!session.pageReviewed ? <button className="doc-button compact" disabled={actionBusy || dirty || unresolved > 0 || !included || !completenessChecked} onClick={() => void mutate("/review", {}, "Saving page review")}>Finish page review</button> : <p className="transcription-complete"><Check size={15} />Page review saved. Ready for Word.</p>}
            {session.export && <a className="transcription-audit" href={session.export.auditUrl} download>Download original readings and reviewed text</a>}
          </div>}
        </section>
      </div>
    </main>}
  </div>;
}
