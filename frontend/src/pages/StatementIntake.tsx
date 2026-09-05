import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { EntityPicker } from "../components/EntityPicker";
import {
  Badge,
  Button,
  Card,
  FileUpload,
  PageHeader,
  TextInput,
} from "../components/ui";
import { api, apiError, downloadFile } from "../lib/api";
import type { IssuerProfile } from "../lib/types";

/**
 * WO-S — the front door for transport statements.
 *
 * Seven fuel-card parsers, the nine-rule capture review gate, the deterministic
 * post-capture checks and the anti-drift baseline all shipped before this page
 * existed, and none of them could be reached: `statement_ingest` had no route
 * and no screen, so a statement could only enter the product through a Python
 * prompt. This page is the missing step.
 *
 * Two presentation rules carry the design, and both are about not overclaiming:
 *
 * 1. **The network is never asked for.** The server detects it from the file's
 *    own marker line and refuses when nothing matches. Asking the operator
 *    would invite them to be wrong, and a mislabeled statement parsed as the
 *    wrong network is exactly what fail-closed detection prevents. The
 *    supported list is shown as INFORMATION, never as a control.
 * 2. **Warnings are the result, not a footnote.** A statement that registered
 *    with fourteen advisory findings is the interesting case; those strings
 *    were previously returned to a caller that did not exist.
 */

const PERIOD_HINT = "YYYY-MM";

type Network = { network: string };
type Finding = {
  id: string;
  statement_sha256: string;
  /** WO-AF: the original bytes are on file and can be downloaded. */
  file_available?: boolean;
  filename: string;
  network: string | null;
  period: string;
  outcome: "registered" | "refused";
  severity: "warn" | "error";
  code: string;
  message: string;
  line_seq: number | null;
  status: string;
  resolved_by: string | null;
  resolution_note: string | null;
  created_at: string;
};
type FindingList = { findings: Finding[]; open_count: number; refused_count: number };
type LearnedEntity = { country: string; vat_number: string; entity_name: string | null };
type SampleLine = {
  line_seq: number;
  txn_date: string;
  country: string;
  station: string;
  product: string;
  qty: string;
  currency: string;
  net_local: string;
  vat_local: string;
  net_eur: string;
  vat_eur: string;
  fx_source: string | null;
};
type IngestResult = {
  network: string;
  period: string;
  filename: string;
  statement_sha256: string;
  lines_registered: number;
  entities_learned: LearnedEntity[];
  warnings: string[];
  sample: SampleLine[];
};

export default function StatementIntakePage() {
  const qc = useQueryClient();
  const [entityId, setEntityId] = useState("");
  const [period, setPeriod] = useState("");
  const [coversheet, setCoversheet] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [result, setResult] = useState<IngestResult | null>(null);

  const entities = useQuery<IssuerProfile[]>({
    queryKey: ["issuer", "registry"],
    queryFn: async () => (await api.get("/issuer/registry")).data,
  });

  const networks = useQuery<{ networks: Network[] }>({
    queryKey: ["transport", "statements", "networks"],
    queryFn: async () => (await api.get("/transport/statements/networks")).data,
  });

  const upload = useMutation({
    mutationFn: async () => {
      const form = new FormData();
      form.append("file", files[0]);
      form.append("entity_id", entityId);
      form.append("period", period);
      if (coversheet.trim() !== "") form.append("coversheet_total", coversheet.trim());
      return (await api.post("/transport/statements", form)).data as IngestResult;
    },
    onSuccess: (r) => {
      setErr(null);
      setResult(r);
      setFiles([]);
      // The rows this just created belong to other screens too.
      qc.invalidateQueries({ queryKey: ["transport"] });
    },
    onError: (e: unknown) => {
      setErr(apiError(e));
      setResult(null);
    },
  });

  const ready = entityId !== "" && period.trim() !== "" && files.length === 1;

  return (
    <div className="space-y-6">
      <PageHeader
        title="Register a statement"
        description="Upload a monthly fuel-card statement. Every line becomes a claimable transaction, and every seller entity the file names is added to the registry."
      />

      {err && (
        <div
          role="alert"
          className="rounded-lg border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700"
        >
          {err}
        </div>
      )}

      <Card>
        <div className="grid gap-4 sm:grid-cols-2">
          <EntityPicker
            entities={entities.data}
            value={entityId}
            onChange={setEntityId}
            label="Claiming entity"
            required
          />
          <TextInput
            label="Period"
            required
            value={period}
            placeholder={PERIOD_HINT}
            hint="The accounting month this statement covers."
            onChange={(e) => setPeriod(e.target.value)}
          />
        </div>

        <div className="mt-4">
          <FileUpload
            label="Statement file"
            accept=".csv,text/csv"
            files={files}
            onFilesChange={setFiles}
            hint="CSV, as issued by the fuel-card network. The network is detected from the file itself — you do not need to say which one it is."
          />
        </div>

        <div className="mt-4 grid gap-4 sm:grid-cols-2">
          <TextInput
            label="Coversheet total (optional)"
            value={coversheet}
            placeholder="e.g. 12480.55"
            hint="If the statement carries its own net total, entering it arms the batch tie-out — and a mismatch refuses the upload rather than registering figures that do not add up."
            onChange={(e) => setCoversheet(e.target.value)}
          />
        </div>

        <div className="mt-4 flex items-center gap-3">
          <Button onClick={() => upload.mutate()} disabled={!ready || upload.isPending}>
            {upload.isPending ? "Registering…" : "Register statement"}
          </Button>
          {!ready && (
            <span className="text-sm text-slate-500">
              Choose an entity, a period and one statement file.
            </span>
          )}
        </div>
      </Card>

      <Card>
        <h2 className="mb-2 text-sm font-semibold text-slate-700">Networks this workspace reads</h2>
        <p className="mb-3 text-sm text-slate-500">
          Detected from the file, never chosen here. A statement from a network not listed is
          refused rather than guessed at.
        </p>
        <div className="flex flex-wrap gap-2">
          {(networks.data?.networks ?? []).map((n) => (
            <Badge key={n.network} tone="neutral">
              {n.network}
            </Badge>
          ))}
        </div>
      </Card>

      {result && <IngestReport result={result} />}

      <ReviewQueue />
    </div>
  );
}

/**
 * WO-Z — the review queue.
 *
 * Before this, everything the ingest found lived in one response: close the tab
 * and the finding was gone, and a REFUSED statement left nothing at all,
 * because its reasons were folded into an error string and rolled back with
 * the transaction. This panel is the persisted surface those findings always
 * needed.
 *
 * It sits on the intake page on purpose rather than behind its own route. The
 * queue is what you look at immediately after uploading — a separate screen
 * would be one more thing to remember, and a surface nobody visits is how a
 * shipped feature becomes invisible.
 */
function ReviewQueue() {
  const qc = useQueryClient();
  const [note, setNote] = useState<Record<string, string>>({});
  const queue = useQuery<FindingList>({
    queryKey: ["transport", "statement-findings"],
    queryFn: async () => (await api.get("/transport/statements/findings")).data,
  });

  const close = useMutation({
    mutationFn: async (args: { id: string; status: "resolved" | "dismissed" }) =>
      (
        await api.post(`/transport/statements/findings/${args.id}/close`, {
          status: args.status,
          note: note[args.id]?.trim() || null,
        })
      ).data,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["transport", "statement-findings"] }),
  });

  const findings = queue.data?.findings ?? [];

  return (
    <Card>
      <div className="mb-3 flex items-baseline justify-between gap-3">
        <h2 className="text-sm font-semibold text-slate-700">Statements needing a look</h2>
        {!!queue.data && (
          <span className="text-xs text-slate-500 tabular-nums">
            {queue.data.open_count} open
            {queue.data.refused_count > 0 && ` · ${queue.data.refused_count} blocked a registration`}
          </span>
        )}
      </div>

      {findings.length === 0 ? (
        <p className="text-sm text-slate-500">
          Nothing open. Findings appear here when a statement registers with advisory notes, or
          when one is refused — and they stay until somebody says what happened to them.
        </p>
      ) : (
        <ul className="divide-y divide-slate-100">
          {findings.map((f) => (
            <li key={f.id} className="py-3">
              <div className="flex flex-wrap items-center gap-2">
                <Badge tone={f.severity === "error" ? "danger" : "warning"}>
                  {f.outcome === "refused" ? "Blocked registration" : "Advisory"}
                </Badge>
                <span className="truncate text-sm font-medium text-slate-700">{f.filename}</span>
                <span className="text-xs text-slate-400 tabular-nums">{f.period}</span>
                {f.line_seq !== null && (
                  <span className="text-xs text-slate-400">line {f.line_seq}</span>
                )}
              </div>
              <p className="mt-1 text-sm text-slate-600">{f.message}</p>
              {f.file_available && (
                <button
                  type="button"
                  className="mt-1 text-xs text-brand-600 hover:underline"
                  onClick={() =>
                    downloadFile(`/transport/statements/${f.statement_sha256}/file`, f.filename)
                  }
                >
                  Download the statement
                </button>
              )}
              <div className="mt-2 flex flex-wrap items-center gap-2">
                <TextInput
                  label="Note"
                  placeholder="What happened? (optional)"
                  value={note[f.id] ?? ""}
                  onChange={(e) => setNote((n) => ({ ...n, [f.id]: e.target.value }))}
                  className="min-w-[16rem] flex-1"
                />
                <Button
                  variant="secondary"
                  disabled={close.isPending}
                  onClick={() => close.mutate({ id: f.id, status: "resolved" })}
                >
                  Resolved
                </Button>
                <Button
                  variant="ghost"
                  disabled={close.isPending}
                  onClick={() => close.mutate({ id: f.id, status: "dismissed" })}
                >
                  Not an issue
                </Button>
              </div>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

function IngestReport({ result }: { result: IngestResult }) {
  return (
    <Card>
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <Badge tone="success">Registered</Badge>
        <span className="font-medium">{result.filename}</span>
        <span className="text-sm text-slate-500">
          {result.network} · {result.period} · {result.lines_registered} line(s)
        </span>
      </div>

      <p className="mb-4 break-all text-xs text-slate-400">
        Statement fingerprint {result.statement_sha256}
        {" · "}
        <button
          type="button"
          className="text-brand-600 hover:underline"
          onClick={() =>
            downloadFile(`/transport/statements/${result.statement_sha256}/file`, result.filename)
          }
        >
          Download the statement
        </button>
      </p>

      {result.warnings.length > 0 ? (
        <div className="mb-4 rounded-lg border border-amber-200 bg-amber-50 p-3">
          <h3 className="mb-2 text-sm font-semibold text-amber-800">
            {result.warnings.length} finding(s) to review
          </h3>
          <p className="mb-2 text-sm text-amber-700">
            These are advisory. The statement registered; nothing here blocked it or changed a
            figure.
          </p>
          <ul className="list-disc space-y-1 pl-5 text-sm text-amber-900">
            {result.warnings.map((w, i) => (
              <li key={i}>{w}</li>
            ))}
          </ul>
        </div>
      ) : (
        <p className="mb-4 text-sm text-slate-500">
          No findings — the capture review, the post-capture checks and the drift comparison all
          passed without a remark.
        </p>
      )}

      {result.entities_learned.length > 0 && (
        <div className="mb-4">
          <h3 className="mb-2 text-sm font-semibold text-slate-700">
            Seller entities this file added
          </h3>
          <ul className="space-y-1 text-sm">
            {result.entities_learned.map((e) => (
              <li key={`${e.country}-${e.vat_number}`}>
                <span className="font-medium">{e.country}</span> · {e.vat_number}
                {e.entity_name ? ` · ${e.entity_name}` : ""}
              </li>
            ))}
          </ul>
        </div>
      )}

      {result.sample.length > 0 && (
        <div>
          <h3 className="mb-2 text-sm font-semibold text-slate-700">
            First {result.sample.length} of {result.lines_registered} line(s)
          </h3>
          <p className="mb-2 text-sm text-slate-500">
            A sample, so you can confirm the right file landed. The full set lives on the
            transactions screens.
          </p>
          <div className="overflow-x-auto">
            <table className="min-w-full text-sm">
              <thead>
                <tr className="text-left text-xs uppercase tracking-wide text-slate-500">
                  <th className="py-1 pr-4">#</th>
                  <th className="py-1 pr-4">Date</th>
                  <th className="py-1 pr-4">Country</th>
                  <th className="py-1 pr-4">Station</th>
                  <th className="py-1 pr-4">Product</th>
                  <th className="py-1 pr-4">Qty</th>
                  <th className="py-1 pr-4">Net</th>
                  <th className="py-1 pr-4">VAT</th>
                  <th className="py-1 pr-4">Net EUR</th>
                  <th className="py-1">Rate source</th>
                </tr>
              </thead>
              <tbody className="tabular-nums">
                {result.sample.map((l) => (
                  <tr key={l.line_seq} className="border-t border-slate-100">
                    <td className="py-1 pr-4">{l.line_seq}</td>
                    <td className="py-1 pr-4">{l.txn_date}</td>
                    <td className="py-1 pr-4">{l.country}</td>
                    <td className="py-1 pr-4">{l.station}</td>
                    <td className="py-1 pr-4">{l.product}</td>
                    <td className="py-1 pr-4">{l.qty}</td>
                    <td className="py-1 pr-4">
                      {l.net_local} {l.currency}
                    </td>
                    <td className="py-1 pr-4">
                      {l.vat_local} {l.currency}
                    </td>
                    <td className="py-1 pr-4">{l.net_eur}</td>
                    <td className="py-1">{l.fx_source ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </Card>
  );
}
