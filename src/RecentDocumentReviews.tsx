import { useEffect, useState } from "react";
import { ArrowRight, FileText, RotateCcw } from "lucide-react";
import { z } from "zod";

const reviewsSchema = z.array(z.object({
  id: z.string().regex(/^[a-f0-9]{32}$/),
  filename: z.string(),
  currentFilename: z.string(),
  updatedAt: z.string().datetime({ offset: true }),
  cursor: z.string().regex(/^\d{1,20}:[a-f0-9]{32}$/),
  editCount: z.number().int().nonnegative(),
  pendingCount: z.number().int().nonnegative(),
  pendingCaptures: z.number().int().nonnegative().default(0),
  scanCount: z.number().int().nonnegative(),
  kind: z.enum(["document", "example", "trial", "handwriting"]),
  exported: z.boolean(),
}));

type SavedReview = z.infer<typeof reviewsSchema>[number];

function reviewStatus(review: SavedReview): string {
  if (review.pendingCaptures) return "Photo processing";
  if (review.exported) return "Word files ready";
  if (review.pendingCount) return `${review.pendingCount} to review`;
  if (review.editCount || review.scanCount) return "Continue review";
  return "Ready for paper edits";
}

const updatedDate = new Intl.DateTimeFormat(undefined, {
  month: "short", day: "numeric", hour: "numeric", minute: "2-digit",
});

export function RecentDocumentReviews({ busy }: { busy: boolean }) {
  const [reviews, setReviews] = useState<SavedReview[]>([]);
  const [error, setError] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const [expanded, setExpanded] = useState(false);
  const [cursor, setCursor] = useState<string | null>(null);
  const [hasOlder, setHasOlder] = useState(false);
  const [loading, setLoading] = useState(true);
  const [includeSamples, setIncludeSamples] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    setError(false);
    setLoading(true);
    const params = new URLSearchParams({ includeSamples: String(includeSamples) });
    if (cursor) params.set("before", cursor);
    fetch(`/api/documents?${params}`, { signal: controller.signal })
      .then(async (response) => {
        if (!response.ok) throw new Error("Saved reviews could not be loaded.");
        return reviewsSchema.parse(await response.json());
      })
      .then((value) => {
        if (controller.signal.aborted) return;
        setReviews((current) => cursor
          ? [...current, ...value.filter((review) => !current.some((saved) => saved.id === review.id))]
          : value);
        setHasOlder(value.length === 20);
      })
      .catch(() => {
        if (!controller.signal.aborted) setError(true);
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [attempt, cursor, includeSamples]);

  return (
    <section className="doc-recent" aria-labelledby="recent-reviews-heading">
      <div className="doc-recent-heading">
        <div>
          <h2 id="recent-reviews-heading">Recent documents</h2>
          <p>Pick up where you left off.</p>
        </div>
        <label className="doc-recent-sample-toggle">
          <input
            type="checkbox"
            checked={includeSamples}
            disabled={busy}
            onChange={(event) => {
              setIncludeSamples(event.target.checked);
              setReviews([]);
              setCursor(null);
              setExpanded(false);
              setHasOlder(false);
              setError(false);
              setLoading(true);
            }}
          />
          Include sample reviews
        </label>
        {reviews.length > 3 && (
          <button className="doc-text-link" onClick={() => setExpanded(!expanded)}>
            {expanded ? "Show fewer" : "Show more reviews"}
          </button>
        )}
      </div>
      {!reviews.length && !error && (
        <p className="doc-recent-empty" role="status">
          {loading ? "Loading documents…" : "No documents yet. Open a Word file to start your first review."}
        </p>
      )}
      {reviews.length > 0 && (
        <ul>
          {reviews.slice(0, expanded ? reviews.length : 3).map((review) => (
            <li key={review.id}>
              <a
                href={`/?document=${review.id}`}
                aria-disabled={busy || undefined}
                tabIndex={busy ? -1 : undefined}
                onClick={(event) => { if (busy) event.preventDefault(); }}
              >
                <FileText size={20} aria-hidden="true" />
                <span className="doc-recent-file">
                  <strong title={review.currentFilename}>{review.currentFilename}</strong>
                  <span>
                    {review.kind === "example" ? "Prepared example" : review.kind === "trial" ? "Pen check" : review.kind === "handwriting" ? "Handwriting sample" : "Word document"}
                    <span aria-hidden="true"> · </span>
                    <time dateTime={review.updatedAt}>{updatedDate.format(new Date(review.updatedAt))}</time>
                  </span>
                </span>
                <span className="doc-recent-status">{reviewStatus(review)}</span>
                <ArrowRight size={16} aria-hidden="true" />
              </a>
            </li>
          ))}
        </ul>
      )}
      {error && (
        <div className="doc-recent-error" role="status">
          <p>{reviews.length ? "Older reviews could not be loaded." : "Saved reviews could not be loaded."} Your files have not changed.</p>
          <button className="doc-text-link" disabled={busy || loading} onClick={() => setAttempt(attempt + 1)}>
            <RotateCcw size={14} /> Try again
          </button>
        </div>
      )}
      {expanded && hasOlder && !error && (
        <div className="doc-recent-more">
          <button className="doc-text-link" disabled={busy || loading} onClick={() => setCursor(reviews.at(-1)?.cursor ?? null)}>
            {loading ? "Loading older reviews…" : "Load older reviews"}
          </button>
        </div>
      )}
    </section>
  );
}
