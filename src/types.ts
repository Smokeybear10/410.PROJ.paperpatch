import { z } from "zod";

const cellSchema = z.object({
  kind: z.string(),
  value: z.union([z.string(), z.number(), z.boolean(), z.null()]),
  address: z.string(),
  protected: z.boolean(),
});
const valueSchema = cellSchema.pick({ kind: true, value: true });
const tableSchema = z.object({
  headers: z.array(z.string()),
  sha256: z.string(),
  rows: z.array(
    z.object({
      id: z.string(),
      label: z.string(),
      cells: z.record(z.string(), cellSchema),
    }),
  ),
});
const candidateSchema = z.object({
  id: z.string(),
  scanId: z.string().nullable(),
  boxId: z.string().nullable().optional(),
  text: z.string(),
  ocrText: z.string().nullable(),
  source: z.enum(["ocr", "manual", "demo"]),
  cropUrl: z.string().nullable(),
  createdAt: z.string(),
  label: z.string(),
  page: z.number().nullable(),
  recognitionState: z
    .enum(["read", "needs_review", "blank", "illustrative"])
    .nullable()
    .optional(),
  transcriptionState: z
    .enum(["unverified", "confirmed", "corrected", "dismissed"])
    .default("unverified"),
});
const boxSchema = z.object({
  boxId: z.string(),
  rowId: z.string(),
  field: z.string(),
  rect: z.tuple([z.number(), z.number(), z.number(), z.number()]),
  inkStatus: z.enum(["detected", "written", "blank", "uncertain"]),
  cropUrl: z.string().nullable().optional(),
  recognitionState: z.enum(["read", "needs_review", "blank", "illustrative"]),
  text: z.string(),
});
const scanSchema = z.object({
  id: z.string(),
  filename: z.string(),
  status: z.enum(["queued", "processing", "ready", "failed"]),
  stage: z.string(),
  createdAt: z.string(),
  finishedAt: z.string().nullable(),
  reviewedAt: z.string().nullable().default(null),
  page: z.number().nullable(),
  originalUrl: z.string().nullable(),
  alignedUrl: z.string().nullable(),
  readingCount: z.number(),
  progress: z.object({ done: z.number(), total: z.number() }),
  warnings: z.array(z.string()),
  error: z.string().nullable(),
  quality: z
    .object({
      blurScore: z.number().optional(),
      contrast: z.number().optional(),
      warnings: z.array(z.string()).default([]),
    })
    .nullable()
    .optional(),
  boxes: z.array(boxSchema).default([]),
});
const proposalSchema = z.object({
  id: z.string(),
  rowId: z.string(),
  field: z.string(),
  text: z.string(),
  cropUrl: z.string().nullable(),
  source: z.enum(["ocr", "manual", "demo"]),
  candidates: z.array(candidateSchema).default([]),
  selectedCandidateId: z.string().nullable().default(null),
  paperConflict: z.boolean().default(false),
});
const itemSchema = proposalSchema.extend({
  baseline: cellSchema,
  current: cellSchema,
  proposed: valueSchema.nullable(),
  status: z.enum([
    "ready",
    "conflict",
    "paper_conflict",
    "unchanged",
    "already_applied",
    "protected",
    "invalid",
    "replayed",
  ]),
  reason: z.string(),
  rowLabel: z.string(),
  requiresReadingCheck: z.boolean().default(false),
});
const verificationSchema = z.object({
  status: z.literal("passed"),
  changedCellCount: z.number().int().nonnegative(),
  preservedFormulaCount: z.number().int().nonnegative(),
  unchangedPartCount: z.number().int().nonnegative(),
  totalPartCount: z.number().int().nonnegative(),
  currentHash: z.string(),
  outputHash: z.string(),
});
const historySchema = z.object({
  id: z.string(),
  createdAt: z.string().nullable(),
  count: z.number(),
  filename: z.string(),
  url: z.string(),
  auditUrl: z.string().nullable(),
  inputWorkbookUrl: z.string().optional(),
  verification: verificationSchema.optional(),
});
export const sessionSchema = z.object({
  id: z.string(),
  filename: z.string(),
  currentFilename: z.string(),
  inspection: z.object({
    sheets: z.array(
      z.object({
        name: z.string(),
        headers: z.array(z.string()),
        rows: z.number(),
      }),
    ),
  }),
  config: z
    .object({
      sheet: z.string(),
      idColumn: z.string(),
      editableColumns: z.array(z.string()),
    })
    .nullable(),
  base: tableSchema.nullable(),
  current: tableSchema.nullable(),
  manifest: z
    .object({
      pages: z.array(z.object({ number: z.number(), previewUrl: z.string() })),
      pdfUrl: z.string(),
    })
    .nullable(),
  proposals: z.array(proposalSchema),
  items: z.array(itemSchema),
  warnings: z.array(z.string()),
  revision: z.number(),
  demo: z.boolean(),
  practice: z.boolean().default(false),
  practiceResources: z
    .object({
      baselineUrl: z.string(),
      latestUrl: z.string(),
    })
    .optional(),
  scans: z.array(scanSchema).default([]),
  reviewDecisions: z
    .record(z.string(), z.enum(["paper", "current"]))
    .default({}),
  history: z.array(historySchema).default([]),
  export: z
    .object({
      url: z.string(),
      filename: z.string(),
      patches: z.array(z.unknown()),
      count: z.number(),
      auditUrl: z.string().nullable().optional(),
      inputWorkbookUrl: z.string().optional(),
      verification: verificationSchema.optional(),
    })
    .nullable(),
});
export const exportPreviewSchema = z.object({
  revision: z.number(),
  currentHash: z.string(),
  canExport: z.boolean(),
  pendingCount: z.number(),
  uncheckedPageCount: z.number().default(0),
  failedSourceCount: z.number().default(0),
  sourceCount: z.number(),
  protectedCount: z.number(),
  preservedDigitalCount: z.number(),
  retainedFormulaChangeCount: z.number().default(0),
  paperChanges: z.array(
    z.object({
      proposalId: z.string(),
      rowId: z.string(),
      rowLabel: z.string(),
      field: z.string(),
      address: z.string(),
      before: cellSchema,
      after: valueSchema,
      source: z.string(),
    }),
  ),
  digitalChanges: z.array(
    z.object({
      rowId: z.string(),
      rowLabel: z.string(),
      field: z.string(),
      address: z.string(),
      before: cellSchema,
      after: cellSchema,
    }),
  ),
  formulaChanges: z
    .array(
      z.object({
        rowId: z.string(),
        rowLabel: z.string(),
        field: z.string(),
        address: z.string(),
        before: cellSchema,
        after: cellSchema,
      }),
    )
    .default([]),
});

export const exportReceiptSchema = z.object({
  version: z.number(),
  id: z.string(),
  createdAt: z.string().nullable(),
  sessionId: z.string(),
  filename: z.string(),
  currentHash: z.string(),
  outputHash: z.string(),
  inputWorkbookUrl: z.string().optional(),
  verification: verificationSchema.optional(),
  paperChanges: exportPreviewSchema.shape.paperChanges,
  digitalChanges: exportPreviewSchema.shape.digitalChanges,
  preservedDigitalCount: z.number(),
  formulaChanges: exportPreviewSchema.shape.formulaChanges,
  retainedFormulaChangeCount: z.number().default(0),
});

export const readingDraftSchema = z.object({
  text: z.string().max(250),
  candidateId: z.string().nullable(),
});
export const manualDraftSchema = z.object({
  rowId: z.string(),
  field: z.string(),
  text: z.string().max(250),
  scanId: z.string().optional(),
  boxId: z.string().optional(),
});

export type Cell = z.infer<typeof cellSchema>;
export type Table = z.infer<typeof tableSchema>;
export type Candidate = z.infer<typeof candidateSchema>;
export type ScanBox = z.infer<typeof boxSchema>;
export type Scan = z.infer<typeof scanSchema>;
export type Proposal = z.infer<typeof proposalSchema>;
export type Item = z.infer<typeof itemSchema>;
export type Session = z.infer<typeof sessionSchema>;
export type ExportPreview = z.infer<typeof exportPreviewSchema>;
export type ExportReceipt = z.infer<typeof exportReceiptSchema>;
export type Decision = "paper" | "current";
export type ManualDraft = z.infer<typeof manualDraftSchema>;
export type ManualTarget = {
  rowId: string;
  field: string;
  scanId?: string;
  boxId?: string;
};
