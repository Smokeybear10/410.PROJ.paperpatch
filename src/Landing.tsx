import { useRef, useState } from "react";
import { ArrowRight, FileSpreadsheet, FolderOpen, PenLine } from "lucide-react";
import "./landing.css";

type LandingProps = {
  onUpload: (file: File) => void;
  onDemo: () => void;
  onTrial: () => void;
  busy: boolean;
};

const exampleRows = [
  {
    id: "EQ-104",
    name: "Cordless drills",
    printed: "12",
    excel: "10",
    paper: "9 / 8",
  },
  {
    id: "EQ-205",
    name: "Safety helmets",
    printed: "24",
    excel: "24",
    paper: "22",
  },
  {
    id: "EQ-407",
    name: "Portable lights",
    printed: "6",
    excel: "5",
    paper: "5",
  },
];

export default function Landing({
  onUpload,
  onDemo,
  onTrial,
  busy,
}: LandingProps) {
  const input = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);

  return (
    <main className="launch">
      <div className="launch-document">
        <header className="launch-introduction">
          <h1 id="launch-title">
            Bring paper corrections back without losing Excel edits.
          </h1>
          <p>
            Compare handwritten corrections with the workbook you printed and
            the Excel file you have now. Match by row ID, review conflicts, then
            export a new workbook with a record of what changed.
          </p>
        </header>
        <section className="launch-trial" aria-labelledby="trial-title">
          <div>
            <h2 id="trial-title">Try the equipment handover</h2>
            <p>
              Two crews disagree after Excel has changed. Resolve the readings
              and inspect the exported file.
            </p>
          </div>
          <div className="launch-trial-actions">
            <button
              type="button"
              className="launch-open-button"
              disabled={busy}
              onClick={onDemo}
            >
              Open example <span>Prepared readings</span>
              <ArrowRight size={17} />
            </button>
            <button
              className="launch-example-button"
              type="button"
              disabled={busy}
              onClick={onTrial}
            >
              <PenLine size={15} strokeWidth={1.6} />
              Try with your own handwriting
              <ArrowRight size={16} />
            </button>
            <span className="launch-trial-note">
              Download a print page and sample Excel, then upload a photo.
            </span>
          </div>
        </section>
        <section
          className={`launch-open ${dragging ? "is-dragging" : ""}`}
          aria-labelledby="launch-upload-title"
          onDragOver={(event) => {
            event.preventDefault();
            if (!busy) setDragging(true);
          }}
          onDragLeave={(event) => {
            if (
              !event.currentTarget.contains(event.relatedTarget as Node | null)
            ) {
              setDragging(false);
            }
          }}
          onDrop={(event) => {
            event.preventDefault();
            setDragging(false);
            const file = event.dataTransfer.files[0];
            if (file && !busy) onUpload(file);
          }}
        >
          <h2 id="launch-upload-title">Use your own workbook</h2>
          <p className="launch-description">
            Choose the fields people can correct on paper. Keep the latest Excel
            file for the comparison when the pages come back.
          </p>
          <div className="launch-open-actions">
            <input
              ref={input}
              type="file"
              accept=".xlsx"
              hidden
              tabIndex={-1}
              onChange={(event) => {
                const file = event.target.files?.[0];
                if (file) onUpload(file);
                event.target.value = "";
              }}
            />
            <button
              type="button"
              className="launch-open-button"
              disabled={busy}
              onClick={() => input.current?.click()}
            >
              <FolderOpen size={17} strokeWidth={1.6} />
              Choose workbook
            </button>
            <span>or drop an .xlsx file here</span>
          </div>
          <p className="launch-file-note">
            Up to 500 rows · one worksheet · your original file stays unchanged
          </p>
        </section>

        <section className="launch-example" aria-labelledby="example-title">
          <div className="launch-example-toolbar">
            <div>
              <h2 id="example-title">See how a conflict is resolved</h2>
              <p>Two crews return different counts. Excel changed too.</p>
            </div>
          </div>
          <div className="example-file-line">
            <span>
              <FileSpreadsheet size={16} strokeWidth={1.5} />
              Field-equipment.xlsx
            </span>
            <span>Prepared readings · no handwriting recognition</span>
          </div>
          <div className="example-ledger-wrap">
            <table className="example-ledger-table">
              <caption className="sr-only">
                Example equipment quantities compared across three versions
              </caption>
              <thead>
                <tr>
                  <th scope="col">Equipment</th>
                  <th scope="col">Printed</th>
                  <th scope="col">Excel now</th>
                  <th scope="col">On paper</th>
                </tr>
              </thead>
              <tbody>
                {exampleRows.map((row, index) => (
                  <tr
                    key={row.id}
                    className={index === 0 ? "example-conflict" : ""}
                  >
                    <th scope="row">
                      <span className="example-item-name">{row.name}</span>
                      <span className="example-item-id">{row.id}</span>
                    </th>
                    <td data-label="Printed">{row.printed}</td>
                    <td data-label="Excel now">{row.excel}</td>
                    <td data-label="On paper" className="example-paper-value">
                      {row.paper}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="example-ledger-note">
            <strong>10, 9 or 8 drills?</strong> Keep both paper sources, choose
            the correct count, and preserve the other Excel edits.
          </p>
        </section>
      </div>
    </main>
  );
}
