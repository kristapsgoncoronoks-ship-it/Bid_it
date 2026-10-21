# OCR control panel and engine development

The user reports that only the supplier name was correct on an uploaded invoice
and requests an OCR control panel, informed by four public OCR repositories.
The affected invoice has not been provided to this environment. Synthetic parser
repairs cannot establish that this particular extraction is fixed.

This plan uses upstream documentation as evidence about capabilities and limits,
not as instructions to install software or send invoices to a third party.

## Current bounded implementation

Use the existing `/captures`, `/captures/:id` and failure worklist, rather than
adding another upload/review workflow. Ordinary document review remains available
under its existing permissions. Workspace configuration belongs behind
`settings.manage`.

| Action | Description and acceptance | Development prompt |
| --- | --- | --- |
| Open OCR control panel | Show authoritative awaiting-review and unacknowledged-failure counts separately. A missing/failed read must not become zero or an all-clear. The review queue is not a list of all processing attempts. | OCR-CP01: Build the control surface on existing capture APIs, preserve permission and tenant boundaries, and test loading, error, stale-data and partial-read states. |
| Refresh status | Refresh both bounded reads, show pending/error state and prevent duplicate refreshes. This is a read, not a new extraction. | OCR-CP02: Refresh server state with visible recovery and no writes. Verify a held/failed read cannot announce success. |
| Upload / review / inspect failures | Reach the existing working workflow; show source preview and corrections before saving an invoice. Existing conditional failed-extraction retries remain unchanged. | OCR-CP03: Connect existing destinations without a new route or duplicate invoice-creation path. Exercise the actual navigation and existing retry refusal/recovery contracts. |
| Inspect field quality | Count extracted/defaulted/missing fields, recorded corrections and unsaved edits distinctly. Label legacy 0.55/0.85 values as method-based review scores, not measured accuracy. | OCR-CP04: Use live provenance; keep original and normalized values visible and show a query failure explicitly. Never turn a fixed method score into document accuracy. |

The initial panel does not select an engine or language. The current image ships
English/orientation Tesseract data; localized invoice-label parsing is a separate
capability. An API-process capability probe would not prove that a separate
worker has the same installation. Existing capture previews/progress and durable
jobs are reused. No model/runtime dependency is added in this slice.

## Ordered follow-up queue

1. **OCR-ACCEPT: reproduce the customer failure.** Obtain the affected invoice
   and expected fields/rows. Inspect text layer versus scan, language, reading
   order and amount-column roles. Record expected and actual results; add the
   smallest authorized/redacted regression. Require exact amount/currency and
   line completeness, not only a plausible supplier name.
2. **OCR-PROFILE: genuine language and processing controls.** Persist approved
   profiles and snapshot requested/effective language, engine/model revision,
   preprocessing and limits on an immutable attempt. Publish inventory from the
   actual worker with stale/unavailable states. Unsupported languages fail
   explicitly. Changes apply to new attempts; no arbitrary CLI flags, paths,
   download URLs or prompts from the browser.
3. **OCR-PAGES: mixed PDFs and source evidence.** Replace the global text-length
   heuristic with bounded per-page decisions; keep native, OCR, skipped,
   timed-out and failed pages distinct. Persist word/region coordinates and raw
   text with page identity before offering highlights. Preserve the original
   checksum and signed source; derivatives are separately identified.
4. **OCR-RETRY: compare attempts without destroying corrections.** Add durable
   retry command identity and committed-status lookup before exposing parsed
   document, page or selected-region retries. Preserve old results and manual
   corrections; apply a new candidate deliberately. A lost response must not
   create repeated attempts or legal invoices.
5. **OCR-PADDLE: qualify an optional CPU provider.** Compare an explicitly pinned
   lean OCR/mobile model (including documented Latvian support) against current
   OCR on the same authorized invoice corpus. Measure field/row correctness,
   extra/missing rows, review effort, p50/p95 duration and peak RSS. Bound model
   downloads, pixels, threads, batches and concurrent jobs. No default switch
   based only on upstream benchmarks.
6. **OCR-VLM: optional specialist evaluation.** Evaluate Unlimited-OCR and
   dots.ocr only in isolated workers with qualified hardware and model terms.
   Preserve partial/recovered/truncated outputs, token/repetition limits and
   typed financial-field validation. Do not enable a remote-demo fallback.

Each queue entry is a development assignment with the outcome and verification
written into its paragraph. Integer-only amounts, mixed net/tax/gross columns,
broader supplier/header heuristics and line-field defaults remain open alongside
the page/profile work. The menu's project-detail parent alias is a separate UI
follow-up; this OCR plan does not close the wider backlog.

## Pinned upstream evidence and decisions

| Repository | Reviewed revision | Useful lesson / integration decision |
| --- | --- | --- |
| [OCRmyPDF](https://github.com/ocrmypdf/OCRmyPDF/tree/cf0df3da06aef04fe9a5bf91c6feff36370bb3eb) | `cf0df3da` | PDF preparation, explicit languages, rotation versus deskew and per-page processing modes. A text sidecar can omit pages. It is not an invoice-field extractor or a PDF sanitizer. Consider optional preprocessing, retaining originals. |
| [PaddleOCR](https://github.com/PaddlePaddle/PaddleOCR/tree/dab3fe35379033fdcb2d0e9572fac0b36c9a9ebf) | `dab3fe35` | Recognition text/scores/boxes, CPU execution and an explicit Latvian `lv` mapping to `latin_PP-OCRv5_mobile_rec`. Pin the chosen model; current default pipeline versions may differ. First alternative to benchmark on the VPS. |
| [Unlimited-OCR](https://github.com/baidu/Unlimited-OCR/tree/92ad94b53a51950fea0416aced42331dfb4dd59e) | `92ad94b5` | Page/mode identity and layout evidence. Inspected examples use NVIDIA GPU inference; no qualified minimum VRAM or Hostinger sizing was found. Optional experiment, not an automatic app dependency. |
| [studio-dots-ai/dots.ocr](https://github.com/studio-dots-ai/dots.ocr/tree/36d7248878f181c6257dc1cbd799ae940970444f) | `36d72488` | Region grounding, page boxes and explicit partial/recovered output. The inspected README describes dots.mocr while the tree contains dots_ocr: resolve the version/instructions mismatch before integration. |

Primary references: OCRmyPDF's [language/sidecar cookbook](https://github.com/ocrmypdf/OCRmyPDF/blob/cf0df3da06aef04fe9a5bf91c6feff36370bb3eb/docs/cookbook.md),
[processing modes and limits](https://github.com/ocrmypdf/OCRmyPDF/blob/cf0df3da06aef04fe9a5bf91c6feff36370bb3eb/docs/advanced.md),
[security](https://github.com/ocrmypdf/OCRmyPDF/blob/cf0df3da06aef04fe9a5bf91c6feff36370bb3eb/docs/pdfsecurity.md);
Paddle's [result/options contract](https://github.com/PaddlePaddle/PaddleOCR/blob/dab3fe35379033fdcb2d0e9572fac0b36c9a9ebf/docs/version3.x/pipeline_usage/OCR.en.md)
and [language mapping](https://github.com/PaddlePaddle/PaddleOCR/blob/dab3fe35379033fdcb2d0e9572fac0b36c9a9ebf/docs/version3.x/algorithm/PP-OCRv5/PP-OCRv5_multi_languages.en.md);
Unlimited's [inference code](https://github.com/baidu/Unlimited-OCR/blob/92ad94b53a51950fea0416aced42331dfb4dd59e/infer.py);
dots' [parser](https://github.com/studio-dots-ai/dots.ocr/blob/36d7248878f181c6257dc1cbd799ae940970444f/dots_ocr/parser.py)
and [pinned original model card](https://huggingface.co/dots-studio/dots.ocr/blob/c0111ce6bc07803dbc267932ffef0ae3a51dc951/README.md).

OCRmyPDF code is MPL-2.0; PaddleOCR code Apache-2.0; Unlimited code/model carry
MIT text; dots code has MIT text plus separate model agreements. These labels
do not settle the licenses of every weight, binary or assembled runtime.
Qualify the actual distributed artifacts and notices before integration.

Research did not install any of these projects, run their inference, benchmark
the VPS or transfer a customer invoice. Detailed pinned-source receipts and
downloaded documentation are retained in the task artifacts.
