import { useEffect, useRef, useState } from "react";
import { ArrowUpRight } from "lucide-react";

export function DocumentCropViewer({ src, alt }: { src: string; alt: string }) {
  const viewport = useRef<HTMLDivElement>(null);
  const drag = useRef<{ x: number; y: number; left: number; top: number } | null>(null);
  const [zoom, setZoom] = useState(1);
  const [width, setWidth] = useState(0);
  const [dimensions, setDimensions] = useState({ width: 0, height: 0 });
  const [panning, setPanning] = useState(false);
  const fitWidth = dimensions.width && dimensions.height
    ? Math.min(width, (180 * dimensions.width) / dimensions.height)
    : width;
  const imageWidth = fitWidth * zoom;
  const imageHeight = dimensions.width ? imageWidth * dimensions.height / dimensions.width : 80;

  useEffect(() => {
    const element = viewport.current;
    if (!element) return;
    const observer = new ResizeObserver(([entry]) => setWidth(entry.contentRect.width));
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    setZoom(1);
    drag.current = null;
    setPanning(false);
  }, [src]);

  useEffect(() => {
    const element = viewport.current;
    if (!element) return;
    element.scrollTo({
      left: Math.max(0, (imageWidth - element.clientWidth) / 2),
      top: Math.max(0, (imageHeight - element.clientHeight) / 2),
    });
  }, [zoom, imageWidth, imageHeight]);

  return (
    <div className="doc-crop-viewer">
      <div className="doc-crop-controls">
        <div role="group" aria-label="Source image magnification">
          {[1, 2, 4].map((value) => (
            <button key={value} type="button" aria-pressed={zoom === value} onClick={() => setZoom(value)}>
              {value === 1 ? "Fit" : `${value}×`}
            </button>
          ))}
        </div>
        <a href={src} target="_blank" rel="noreferrer">Full-size image <ArrowUpRight size={12} /></a>
      </div>
      <div
        ref={viewport}
        className={`doc-crop-window ${zoom > 1 ? "zoomed" : ""} ${panning ? "panning" : ""}`}
        style={{ height: Math.max(58, Math.min(zoom === 1 ? 180 : 260, imageHeight)) }}
        tabIndex={zoom > 1 ? 0 : -1}
        role="region"
        aria-label={zoom > 1 ? "Enlarged source image. Drag or use arrow keys to pan." : "Source image"}
        onPointerDown={(event) => {
          if (zoom === 1 || event.button !== 0) return;
          const element = event.currentTarget;
          drag.current = { x: event.clientX, y: event.clientY, left: element.scrollLeft, top: element.scrollTop };
          element.setPointerCapture(event.pointerId);
          element.focus({ preventScroll: true });
          setPanning(true);
          event.preventDefault();
        }}
        onPointerMove={(event) => {
          if (!drag.current) return;
          event.currentTarget.scrollLeft = drag.current.left + drag.current.x - event.clientX;
          event.currentTarget.scrollTop = drag.current.top + drag.current.y - event.clientY;
        }}
        onPointerUp={() => { drag.current = null; setPanning(false); }}
        onPointerCancel={() => { drag.current = null; setPanning(false); }}
        onLostPointerCapture={() => { drag.current = null; setPanning(false); }}
      >
        <img
          className="doc-crop-image"
          src={src}
          alt={alt}
          draggable={false}
          style={{ width: imageWidth || "100%", marginInline: zoom === 1 ? "auto" : 0 }}
          onLoad={(event) => setDimensions({ width: event.currentTarget.naturalWidth, height: event.currentTarget.naturalHeight })}
        />
      </div>
      {zoom > 1 && <p className="doc-crop-pan-hint">Drag to pan, or focus the image and use arrow keys.</p>}
    </div>
  );
}
