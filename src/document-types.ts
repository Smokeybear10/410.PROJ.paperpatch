import { z } from "zod";

const paragraphSchema = z.object({
  id: z.string(),
  index: z.number().int(),
  text: z.string(),
  style: z.string(),
  editable: z.boolean(),
  reason: z.string().optional(),
});

const scanSchema = z.object({
  id: z.string(),
  filename: z.string(),
  reviewer: z.string(),
  page: z.number().int(),
  alignedUrl: z.string(),
  originalUrl: z.string(),
  uploadUrl: z.string().optional(),
  captureReportUrl: z.string().optional(),
  recognizer: z.union([z.string(), z.object({ model: z.string() })]).optional(),
  captureSeconds: z.number().nonnegative().optional(),
  captureMode: z.enum(["guided", "automatic"]).optional(),
  imageWidth: z.number().positive().optional(),
  imageHeight: z.number().positive().optional(),
  reviewed: z.boolean(),
  warnings: z.array(z.string()),
  source: z.enum(["photo", "demo", "sample"]),
  markCount: z.number().int(),
  captureReport: z
    .object({
      expectedCount: z.number().int(),
      targetMatches: z.number().int(),
      exactReadings: z.number().int(),
      missedTargets: z.number().int(),
      unexpectedTargets: z.number().int(),
      failedAttemptsBeforeThis: z.number().int().optional(),
      source: z.enum(["physical_reported", "unspecified", "synthetic"]),
      items: z.array(
        z.object({
          paragraphId: z.string(),
          before: z.string(),
          expected: z.string(),
          observed: z.string().nullable(),
          observedKind: z
            .enum(["delete", "replace", "insert"])
            .nullable()
            .optional(),
          targetFound: z.boolean(),
          exact: z.boolean(),
        }),
      ),
      scope: z.string(),
    })
    .optional(),
  marks: z
    .array(
      z.object({
        id: z.string(),
        rect: z.tuple([z.number(), z.number(), z.number(), z.number()]),
        cropUrl: z.string(),
        reason: z.string(),
        origin: z.literal("selection").optional(),
        ocrText: z.string().optional(),
        ocrAlternatives: z.array(z.string()).max(2).optional(),
        recognitionWarning: z.string().nullish(),
        resolution: z.enum(["correction", "ignored"]).nullable().optional(),
        editId: z.string().nullable().optional(),
        suggestedParagraphId: z.string().nullable().optional(),
      }),
    )
    .optional(),
});

const editSchema = z.object({
  id: z.string(),
  paragraphId: z.string(),
  start: z.number().int(),
  end: z.number().int(),
  replacement: z.string(),
  kind: z.enum(["delete", "replace", "insert"]).optional(),
  ocrText: z.string().optional(),
  ocrAlternatives: z.array(z.string()).max(2).optional(),
  reviewer: z.string(),
  scanId: z.string().nullable(),
  sourceMarkId: z.string().nullable().optional(),
  cropUrl: z.string().nullable(),
  rect: z.tuple([z.number(), z.number(), z.number(), z.number()]).nullable(),
  page: z.number().int().nullable(),
  source: z.enum(["ocr", "manual", "demo"]),
  targetSelection: z.literal("manual").optional(),
  confirmed: z.boolean(),
  decision: z.enum(["paper", "current"]).nullable(),
  status: z.enum([
    "ready",
    "conflict",
    "missing",
    "protected",
    "already_applied",
    "overlap",
  ]),
  reason: z.string(),
  baseText: z.string(),
  currentText: z.string(),
  currentParagraphId: z.string().nullable(),
  currentStart: z.number().int().nullable(),
  currentEnd: z.number().int().nullable(),
  paragraphText: z.string(),
});

const exportSchema = z.object({
  id: z.string(),
  createdAt: z.string(),
  url: z.string(),
  cleanUrl: z.string(),
  auditUrl: z.string(),
  inputUrl: z.string(),
  filename: z.string(),
  changeCount: z.number().int(),
  verification: z.object({
    status: z.literal("passed"),
    changedParagraphCount: z.number().int(),
    revisionCount: z.number().int(),
    unchangedPartCount: z.number().int(),
    totalPartCount: z.number().int(),
    currentHash: z.string(),
    outputHash: z.string(),
  }),
});

export const documentSessionSchema = z.object({
  id: z.string(),
  title: z.string(),
  filename: z.string(),
  currentFilename: z.string(),
  baselineHash: z.string().optional(),
  currentHash: z.string(),
  revision: z.number().int(),
  pendingCaptures: z.number().int().nonnegative().default(0),
  demo: z.boolean(),
  sample: z.object({
    kind: z.literal("human_ink_composite"),
    inputUrl: z.string(),
    originalUrl: z.string(),
    sourceUrl: z.string(),
    licenseUrl: z.string(),
    attribution: z.string(),
    description: z.string(),
    provenanceUrl: z.string().optional(),
  }).optional(),
  trial: z
    .object({
      kind: z.literal("physical_capture"),
      instructions: z.array(
        z.object({
          paragraphId: z.string(),
          before: z.string(),
          after: z.string(),
          kind: z.enum(["replace", "delete"]),
        }),
      ),
      expectedCount: z.number().int(),
    })
    .optional(),
  createdAt: z.string(),
  paragraphs: z.array(paragraphSchema),
  currentParagraphs: z.array(paragraphSchema),
  pages: z.array(
    z.object({
      number: z.number().int(),
      previewUrl: z.string(),
      width: z.number(),
      height: z.number(),
      textRanges: z
        .array(
          z.object({
            paragraphId: z.string(),
            start: z.number().int(),
            end: z.number().int(),
          }),
        )
        .optional(),
    }),
  ),
  printUrl: z.string(),
  baselineUrl: z.string(),
  currentUrl: z.string(),
  scans: z.array(scanSchema),
  edits: z.array(editSchema),
  warnings: z.array(z.string()),
  export: exportSchema.nullable(),
});

export type DocumentSession = z.infer<typeof documentSessionSchema>;
export type DocumentEdit = z.infer<typeof editSchema>;
export type DocumentScan = z.infer<typeof scanSchema>;
export type DocumentParagraph = z.infer<typeof paragraphSchema>;

export class DocumentRequestError extends Error {
  constructor(message: string, readonly status: number) {
    super(message);
    this.name = "DocumentRequestError";
  }
}

export function documentError(error: unknown): string {
  if (error instanceof z.ZodError)
    return "The document response was incomplete. Reload the review before continuing.";
  return error instanceof Error
    ? error.message
    : "This action could not be completed. Please try again.";
}

export async function documentRequest(
  url: string,
  init?: RequestInit,
): Promise<DocumentSession> {
  const response = await fetch(url, init);
  const result: unknown = await response.json();
  if (!response.ok) {
    const detail = z.object({ detail: z.string() }).safeParse(result);
    throw new DocumentRequestError(
      detail.success
        ? detail.data.detail
        : `The document request failed (${response.status}).`,
      response.status,
    );
  }
  return documentSessionSchema.parse(result);
}
