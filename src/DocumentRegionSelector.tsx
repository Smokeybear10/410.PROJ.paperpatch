import { useRef, useState } from "react";
import type { PointerEvent } from "react";
import { LoaderCircle, ScanText } from "lucide-react";
import type { DocumentScan } from "./document-types";
import "./document-region-selector.css";

export type DocumentRegion = [number, number, number, number];

export function DocumentRegionSelector({
  scan, busy, error, reloadNeeded, onReload, onCancel, onSubmit,
}: {
  scan: DocumentScan;
  busy: boolean;
  error: string | null;
  reloadNeeded: boolean;
  onReload: () => void;
  onCancel: () => void;
  onSubmit: (rect: DocumentRegion) => Promise<void>;
}) {
  const [rect, setRect] = useState<DocumentRegion | null>(null);
  const [dimensions, setDimensions] = useState({ width: scan.imageWidth ?? 0, height: scan.imageHeight ?? 0 });
  const [imageError, setImageError] = useState(false);
  const [attempted, setAttempted] = useState(false);
  const [drawing, setDrawing] = useState(false);
  const drag = useRef<{ x: number; y: number; previous: DocumentRegion | null } | null>(null);
  const firstCoordinate = useRef<HTMLInputElement>(null);
  const valid = !!rect && rect[2] * dimensions.width >= 8 && rect[3] * dimensions.height >= 8;
  const point = (event: PointerEvent<HTMLDivElement>) => {
    const bounds = event.currentTarget.getBoundingClientRect();
    return { x: Math.max(0, Math.min(1, (event.clientX - bounds.left) / bounds.width)), y: Math.max(0, Math.min(1, (event.clientY - bounds.top) / bounds.height)) };
  };
  const updateDrag = (event: PointerEvent<HTMLDivElement>) => {
    if (!drag.current) return;
    const end = point(event);
    setRect([Math.min(drag.current.x, end.x), Math.min(drag.current.y, end.y), Math.abs(end.x - drag.current.x), Math.abs(end.y - drag.current.y)]);
  };
  const coordinate = (index: number, value: number) => {
    if (!Number.isFinite(value)) return;
    const next: DocumentRegion = [...(rect ?? [0.15, 0.15, 0.7, 0.2])];
    next[index] = Math.max(0, Math.min(1, value / 100));
    if (index < 2) next[index] = Math.min(next[index], 1 - next[index + 2]);
    else next[index] = Math.min(next[index], 1 - next[index - 2]);
    setRect(next);
  };
  return (
    <section className="doc-region-selector" aria-label="Select handwriting in the photograph">
      <div className="doc-region-heading"><b>1. Select the handwritten text</b><p>Drag a tight box around one line to replace or insert. Leave printed text and strike-throughs outside the box.</p></div>
      <div className="doc-region-image-window">
        <div
          className={`doc-region-image ${busy ? "is-busy" : ""}`}
          onPointerDown={(event) => {
            if (busy || imageError || event.button !== 0) return;
            const start = point(event);
            drag.current = { ...start, previous: rect };
            event.currentTarget.setPointerCapture(event.pointerId);
            setDrawing(true);
            setRect([start.x, start.y, 0, 0]);
            event.preventDefault();
          }}
          onPointerMove={updateDrag}
          onPointerUp={(event) => {
            updateDrag(event);
            drag.current = null;
            setDrawing(false);
            if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
          }}
          onPointerCancel={() => { if (drag.current) setRect(drag.current.previous); drag.current = null; setDrawing(false); }}
          onLostPointerCapture={() => { drag.current = null; setDrawing(false); }}
        >
          <img
            src={scan.alignedUrl}
            alt="Uploaded photograph. Draw a box around handwriting, or use the position controls below."
            draggable={false}
            onLoad={(event) => { setDimensions({ width: event.currentTarget.naturalWidth, height: event.currentTarget.naturalHeight }); setImageError(false); }}
            onError={() => setImageError(true)}
          />
          {rect && <div className="doc-region-box" style={{ left: `${rect[0] * 100}%`, top: `${rect[1] * 100}%`, width: `${rect[2] * 100}%`, height: `${rect[3] * 100}%` }}><span>Handwriting</span></div>}
        </div>
      </div>
      {imageError && <p className="doc-field-error" role="alert">The photo could not be displayed. Close the selector and reload the review before selecting handwriting.</p>}
      <div className="doc-region-position">
        <div className="doc-region-position-heading"><b>Selection position</b><button type="button" className="doc-text-link" disabled={busy || imageError} onClick={() => { setRect([0.15, 0.15, 0.7, 0.2]); firstCoordinate.current?.focus(); }}>Use keyboard controls</button></div>
        <p id={`region-coordinates-${scan.id}`}>Percent of the full photograph. Adjust the four values to move or resize the box.</p>
        <div className="doc-region-coordinates">{["Left", "Top", "Width", "Height"].map((label, index) => <label key={label}>{label} %<input
          ref={index === 0 ? firstCoordinate : undefined}
          type="number" min={0} max={100} step={0.1}
          value={rect ? Number((rect[index] * 100).toFixed(1)) : ""}
          disabled={busy || imageError}
          aria-describedby={`region-coordinates-${scan.id}`}
          onChange={(event) => coordinate(index, event.currentTarget.valueAsNumber)}
        /></label>)}</div>
        {rect && !valid && !drawing && <p className="doc-field-error">Select a larger area; both sides must be at least 8 image pixels.</p>}
      </div>
      {attempted && error && <div className="doc-field-error" role="alert"><p>{error} Your selection is still here.</p>{reloadNeeded && <button type="button" className="doc-text-link" disabled={busy} onClick={onReload}>Reload latest review</button>}</div>}
      <div className="doc-region-actions"><button type="button" className="doc-button compact" disabled={busy} onClick={onCancel}>Cancel</button><button type="button" className="doc-button primary compact" disabled={busy || !valid || imageError || drawing || reloadNeeded} onClick={async () => { if (!rect) return; setAttempted(true); await onSubmit(rect); }}>{busy ? <LoaderCircle size={15} className="doc-spin" /> : <ScanText size={15} />}{busy ? "Reading handwriting…" : "Read selected handwriting"}</button></div>
      <p className="doc-region-next">Next: check the reading and select the words it changes in Word.</p>
    </section>
  );
}
