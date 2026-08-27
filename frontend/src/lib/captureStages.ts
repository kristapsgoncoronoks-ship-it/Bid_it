/**
 * What each capture stage is called on screen (WO-X).
 *
 * The server sends codes (`capture_progress.STAGES`) and numbers; the wording
 * lives here, because it is wording. A backend test recomputes the coverage
 * from both sides, so a stage added on one side only fails a gate instead of
 * reaching an operator as a raw identifier.
 *
 * The sentences describe the DOCUMENT's journey, not the system's internals:
 * someone watching a scan upload cares that pages are being read, not that a
 * threadpool is rasterising them.
 */
export const CAPTURE_STAGE_LABELS = {
  queued: "Waiting for a free reader",
  reading: "Reading the document",
  ocr: "Recognising the pages",
  interpreting: "Working out the invoice",
  done: "Finished",
} as const;

export type CaptureStage = keyof typeof CAPTURE_STAGE_LABELS;

/**
 * One line describing where a capture has got to.
 *
 * During OCR the page count is the message — "Recognising page 12 of 40" tells
 * the person far more than any percentage, and it is the sentence that stops a
 * long scan from reading as a hung one. An unknown stage (a run captured before
 * the progress contract existed) falls back to a plain, honest sentence rather
 * than printing a code.
 */
export function captureStageLabel(
  stage: string | null | undefined,
  pagesDone = 0,
  pagesTotal?: number | null,
): string {
  if (stage === "ocr" && pagesTotal) {
    return `Recognising page ${Math.min(pagesDone + 1, pagesTotal)} of ${pagesTotal}`;
  }
  if (stage && stage in CAPTURE_STAGE_LABELS) {
    return CAPTURE_STAGE_LABELS[stage as CaptureStage];
  }
  return "Reading the document";
}
