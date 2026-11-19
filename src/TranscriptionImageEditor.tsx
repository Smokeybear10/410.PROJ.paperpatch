import { useEffect, useRef, useState } from "react";
import type { KeyboardEvent, PointerEvent } from "react";
import { LoaderCircle, RotateCcw, RotateCw } from "lucide-react";

export type Selection = [number, number, number, number];
export type Preparation = { rotation: number; straighten: number; crop: Selection };
type Point = [number, number];
type Corner = "nw" | "ne" | "sw" | "se";
type Drag = { kind: "draw" | "move" | Corner; origin: Point; selection: Selection };
const fullPage: Selection = [0, 0, 1, 1];
const clamp = (value: number, low = 0, high = 1) => Math.max(low, Math.min(high, value));

function rotatedSize(width: number, height: number, clockwise: number): Point {
  if (clockwise % 90 === 0) return clockwise % 180 === 0 ? [width, height] : [height, width];
  const angle = clockwise * Math.PI / 180;
  const a = Number(Math.cos(angle).toFixed(15));
  const b = Number(Math.sin(angle).toFixed(15));
  const c = width / 2 - a * width / 2 - b * height / 2;
  const f = height / 2 + b * width / 2 - a * height / 2;
  const corners = [[0, 0], [width, 0], [width, height], [0, height]].map(([x, y]) => [a * x + b * y + c, -b * x + a * y + f]);
  return [Math.ceil(Math.max(...corners.map(([x]) => x))) - Math.floor(Math.min(...corners.map(([x]) => x))),
    Math.ceil(Math.max(...corners.map(([, y]) => y))) - Math.floor(Math.min(...corners.map(([, y]) => y)))];
}

function bounded(selection: Selection): Selection {
  const width = clamp(selection[2], .005);
  const height = clamp(selection[3], .005);
  return [clamp(selection[0], 0, 1 - width), clamp(selection[1], 0, 1 - height), width, height];
}

export function ImageSelection({ src, alt, selection, onChange, disabled = false }: {
  src: string; alt: string; selection: Selection; onChange: (value: Selection) => void; disabled?: boolean;
}) {
  const frame = useRef<HTMLDivElement>(null);
  const drag = useRef<Drag | null>(null);
  const [x, y, width, height] = selection;

  function point(event: PointerEvent): Point {
    const box = frame.current!.getBoundingClientRect();
    return [clamp((event.clientX - box.left) / box.width), clamp((event.clientY - box.top) / box.height)];
  }

  function start(event: PointerEvent, kind: Drag["kind"]) {
    if (disabled || event.button !== 0) return;
    event.preventDefault(); event.stopPropagation();
    drag.current = { kind, origin: point(event), selection: [...selection] };
    frame.current!.setPointerCapture(event.pointerId);
    frame.current!.focus({ preventScroll: true });
  }

  function move(event: PointerEvent) {
    const current = drag.current;
    if (!current || disabled) return;
    const [px, py] = point(event);
    const [left, top, w, h] = current.selection;
    if (current.kind === "move") {
      onChange(bounded([left + px - current.origin[0], top + py - current.origin[1], w, h]));
      return;
    }
    const anchor: Point = current.kind === "draw" ? current.origin
      : [current.kind.endsWith("w") ? left + w : left, current.kind.startsWith("n") ? top + h : top];
    onChange(bounded([Math.min(px, anchor[0]), Math.min(py, anchor[1]), Math.abs(px - anchor[0]), Math.abs(py - anchor[1])]));
  }

  function keyMove(event: KeyboardEvent, corner?: Corner) {
    if (disabled || !["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(event.key)) return;
    event.preventDefault(); event.stopPropagation();
    const step = event.shiftKey ? .02 : .002;
    const dx = event.key === "ArrowLeft" ? -step : event.key === "ArrowRight" ? step : 0;
    const dy = event.key === "ArrowUp" ? -step : event.key === "ArrowDown" ? step : 0;
    if (!corner) { onChange(bounded([x + dx, y + dy, width, height])); return; }
    const left = corner.endsWith("w") ? clamp(x + dx, 0, x + width - .005) : x;
    const top = corner.startsWith("n") ? clamp(y + dy, 0, y + height - .005) : y;
    const right = corner.endsWith("e") ? clamp(x + width + dx, x + .005, 1) : x + width;
    const bottom = corner.startsWith("s") ? clamp(y + height + dy, y + .005, 1) : y + height;
    onChange([left, top, right - left, bottom - top]);
  }

  return <div className="transcription-selection-editor">
    <div className="transcription-selection-frame" ref={frame} tabIndex={disabled ? -1 : 0} role="group"
      aria-label="Image selection. Drag to select text. Arrow keys move the selection; Shift moves it farther."
      onPointerDown={(event) => start(event, "draw")} onPointerMove={move}
      onPointerUp={() => { drag.current = null; }} onPointerCancel={() => { drag.current = null; }}
      onKeyDown={(event) => { if (event.target === event.currentTarget) keyMove(event); }}>
      <img src={src} alt={alt} draggable={false} />
      <div className="transcription-selection-box" style={{ left: `${x * 100}%`, top: `${y * 100}%`, width: `${width * 100}%`, height: `${height * 100}%` }} onPointerDown={(event) => start(event, width === 1 && height === 1 ? "draw" : "move")}>
        {(["nw", "ne", "sw", "se"] as const).map((corner) => <button key={corner} type="button" className={`transcription-selection-handle ${corner}`} disabled={disabled}
          aria-label={`Resize ${corner.startsWith("n") ? "top" : "bottom"} ${corner.endsWith("w") ? "left" : "right"} corner`}
          onPointerDown={(event) => start(event, corner)} onKeyDown={(event) => keyMove(event, corner)} />)}
      </div>
    </div>
    <details className="transcription-selection-precise"><summary>Precise selection</summary><div>
      {(["Left", "Top", "Width", "Height"] as const).map((label, index) => <label key={label}>{label} (%)<input type="number" min={index > 1 ? .5 : 0} max={100} step={.1}
        value={Number((selection[index] * 100).toFixed(1))} disabled={disabled} onChange={(event) => {
          if (!event.target.value || !Number.isFinite(event.target.valueAsNumber)) return;
          const next: Selection = [...selection]; next[index] = event.target.valueAsNumber / 100; onChange(bounded(next));
        }} /></label>)}
    </div></details>
  </div>;
}

export function PhotoPreparation({ file, busy, onCancel, onRead }: {
  file: File; busy: boolean; onCancel: () => void; onRead: (preparation: Preparation) => void;
}) {
  const [image, setImage] = useState<HTMLImageElement | null>(null);
  const [rotation, setRotation] = useState(0);
  const [straighten, setStraighten] = useState(0);
  const [crop, setCrop] = useState<Selection>(fullPage);
  const [preview, setPreview] = useState<{ url: string; ratio: number } | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const url = URL.createObjectURL(file);
    const photo = new Image();
    let active = true;
    photo.onload = () => {
      if (!active) return;
      if (photo.naturalWidth * photo.naturalHeight > 25_000_000) { setError("Choose a photo under 25 megapixels."); return; }
      setImage(photo);
    };
    photo.onerror = () => { if (active) setError("This image could not be opened. Choose a JPEG, PNG, or WebP image."); };
    photo.src = url;
    return () => { active = false; URL.revokeObjectURL(url); };
  }, [file]);

  useEffect(() => {
    if (!image) return;
    const scale = Math.min(1, 3300 / Math.max(image.naturalWidth, image.naturalHeight));
    const width = Math.round(image.naturalWidth * scale);
    const height = Math.round(image.naturalHeight * scale);
    const angle = (rotation + straighten) * Math.PI / 180;
    const canvas = document.createElement("canvas");
    [canvas.width, canvas.height] = rotatedSize(width, height, rotation + straighten);
    const context = canvas.getContext("2d");
    if (!context) { setError("This browser could not prepare the image. Try another browser."); return; }
    context.fillStyle = "white"; context.fillRect(0, 0, canvas.width, canvas.height);
    context.translate(canvas.width / 2, canvas.height / 2); context.rotate(angle);
    context.drawImage(image, -width / 2, -height / 2, width, height);
    setPreview({ url: canvas.toDataURL("image/jpeg", .92), ratio: canvas.width / canvas.height });
  }, [image, rotation, straighten]);

  function rotate(amount: number) {
    setRotation((value) => (value + amount + 360) % 360); setCrop(fullPage);
  }

  return <section className="transcription-preparation" aria-label="Prepare handwritten page">
    <div className="transcription-preparation-title"><h2>Prepare page</h2><span>{file.name}</span></div>
    <p>Make the writing level, then drag around the text you want to read.</p>
    <div className="transcription-photo-tools">
      <button className="doc-button compact" disabled={busy || !image} onClick={() => rotate(-90)}><RotateCcw size={15} />Rotate left</button>
      <button className="doc-button compact" disabled={busy || !image} onClick={() => rotate(90)}><RotateCw size={15} />Rotate right</button>
      <button className="transcription-quiet" disabled={busy || !image} onClick={() => { setRotation(0); setStraighten(0); setCrop(fullPage); }}>Reset photo</button>
    </div>
    <label className="transcription-straighten">Straighten <input type="range" min={-15} max={15} step={.25} value={straighten} disabled={busy || !image}
      onChange={(event) => { setStraighten(Number(event.target.value)); setCrop(fullPage); }} /><output>{straighten > 0 ? "+" : ""}{straighten}°</output></label>
    <p className="transcription-photo-help">Straightening resets the selection. Keep the whole line, including punctuation.</p>
    {error ? <p className="transcription-line-warning" role="alert">{error}</p> : preview ? <div className="transcription-preparation-image" style={{ maxWidth: `min(100%, calc(60vh * ${preview.ratio}))` }}>
      <ImageSelection src={preview.url} alt={`Page preparation preview: ${file.name}`} selection={crop} onChange={setCrop} disabled={busy} />
    </div> : <p role="status"><LoaderCircle className="transcription-spinner" size={16} /> Opening image…</p>}
    <div className="transcription-preparation-actions"><button className="doc-button primary" disabled={busy || !preview || !!error} onClick={() => onRead({ rotation, straighten, crop })}>
      {busy && <LoaderCircle size={16} className="transcription-spinner" />}{busy ? "Uploading page…" : "Read this page"}</button>
      <button className="transcription-quiet" disabled={busy} onClick={onCancel}>Choose another image</button></div>
    <p className="transcription-photo-help">Your original upload is retained. These adjustments only change the reading copy.</p>
  </section>;
}
