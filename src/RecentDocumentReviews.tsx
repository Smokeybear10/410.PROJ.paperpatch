import { useEffect, useState } from "react";
import { ArrowRight, FileText, RotateCcw } from "lucide-react";
import { z } from "zod";

const reviewsSchema = z.array(z.object({
  id: z.string().regex(/^[a-f0-9]{32}$/),
  filename: z.string(),
  currentFilename: z.string(),
  updatedAt: z.string().datetime({ offset: true }),
  editCount: z.number().int().nonnegative(),
  pendingCount: z.number().int().nonnegative(),
  pendingCaptures: z.number().int().nonnegative().default(0),
  scanCount: z.number().int().nonnegative(),
  kind: z.enum(["document", "example", "trial"]),
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

  useEffect(() => {
    const controller = new AbortController();
    setError(false);
    fetch("/api/documents", { signal: controller.signal })
      .then(async (response) => {
        if (!response.ok) throw new Error("Saved reviews could not be loaded.");
        return reviewsSchema.parse(await response.json());
      })
      .then((value) => { if (!controller.signal.aborted) setReviews(value); })
      .catch(() => {
        if (!controller.signal.aborted) setError(true);
      });
    return () => controller.abort();
  }, [attempt]);

  if (!reviews.length && !error) return null;

  return (
    <section className="doc-recent" aria-labelledby="recent-reviews-heading">
      <div className="doc-recent-heading">
        <div>
          <h2 id="recent-reviews-heading">Continue a review.</h2>
          <p>Recent reviews saved on this computer.</p>
        </div>
        {reviews.length > 3 && (
          <button className="doc-text-link" onClick={() => setExpanded(!expanded)}>
            {expanded ? "Show fewer" : `Show all ${reviews.length}`}
          </button>
        )}
      </div>
      {error ? (
        <div className="doc-recent-error" role="status">
          <p>Saved reviews could not be loaded. Your files have not changed.</p>
          <button className="doc-text-link" disabled={busy} onClick={() => setAttempt(attempt + 1)}>
            <RotateCcw size={14} /> Try again
          </button>
        </div>
      ) : (
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
                    {review.kind === "example" ? "Working example" : review.kind === "trial" ? "Pen check" : "Word document"}
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
    </section>
  );
}
