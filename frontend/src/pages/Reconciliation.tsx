import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";
import { Badge, Button, Card, type Tone } from "../components/ui";
import { api, apiError } from "../lib/api";
import { money, shortDate } from "../lib/format";
import type { BankLine, BankStatement, MatchCandidate } from "../lib/types";

const LINE_TONE: Record<BankLine["status"], Tone> = {
  matched: "success",
  ignored: "neutral",
  unmatched: "warning",
};

export default function ReconciliationPage() {
  const qc = useQueryClient();
  const [err, setErr] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);

  const statements = useQuery<BankStatement[]>({
    queryKey: ["reconciliation", "statements"],
    queryFn: async () => (await api.get("/reconciliation/statements")).data,
  });

  const invalidate = () => {
    setErr(null);
    qc.invalidateQueries({ queryKey: ["reconciliation"] });
  };
  const onErr = (e: unknown) => setErr(apiError(e));

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold">Bank reconciliation</h1>
        <p className="text-slate-500">
          Import a bank statement, then match each line to a receipt or a paid expense payout.
        </p>
      </div>

      {err && (
        <div className="rounded-lg border border-rose-200 bg-rose-50 px-4 py-2 text-sm text-rose-700">
          {err}
        </div>
      )}

      <ImportStatement onImported={invalidate} onErr={onErr} />

      <Card>
        <h2 className="mb-3 text-sm font-semibold text-slate-700">Statements</h2>
        <div className="space-y-2">
          {(statements.data ?? []).map((s) => {
            const done = s.matched + s.ignored;
            const complete = done >= s.line_count && s.line_count > 0;
            return (
              <div key={s.id} className="rounded-lg border border-slate-200 p-3">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div className="flex items-center gap-2">
                    <Badge tone={complete ? "success" : "info"}>
                      {done}/{s.line_count} reconciled
                    </Badge>
                    <span className="font-medium">{s.filename}</span>
                    <span className="text-xs text-slate-400">
                      {shortDate(s.created_at)} · {s.method}
                      {s.unmatched > 0 ? ` · ${s.unmatched} to review` : ""}
                    </span>
                  </div>
                  <Button
                    size="sm"
                    variant="secondary"
                    onClick={() => setSelected(selected === s.id ? null : s.id)}
                  >
                    {selected === s.id ? "Hide lines" : "Reconcile"}
                  </Button>
                </div>
                {selected === s.id && (
                  <StatementLines statementId={s.id} onChanged={invalidate} onErr={onErr} />
                )}
              </div>
            );
          })}
          {(statements.data ?? []).length === 0 && (
            <p className="text-sm text-slate-400">No statements imported yet.</p>
          )}
        </div>
      </Card>
    </div>
  );
}

function ImportStatement({
  onImported,
  onErr,
}: {
  onImported: () => void;
  onErr: (e: unknown) => void;
}) {
  const fileRef = useRef<HTMLInputElement>(null);
  const [warnings, setWarnings] = useState<string[]>([]);

  const upload = useMutation({
    mutationFn: async (file: File) => {
      const form = new FormData();
      form.append("file", file);
      return (await api.post("/reconciliation/import", form)).data as {
        imported: number;
        warnings: string[];
      };
    },
    onSuccess: (r) => {
      setWarnings(r.warnings ?? []);
      if (fileRef.current) fileRef.current.value = "";
      onImported();
    },
    onError: onErr,
  });

  return (
    <Card>
      <h2 className="mb-3 text-sm font-semibold text-slate-700">Import a statement</h2>
      <div className="flex flex-wrap items-center gap-3">
        <input
          ref={fileRef}
          type="file"
          accept=".csv,.pdf"
          className="text-sm"
          onChange={(e) => {
            const f = e.target.files?.[0];
            if (f) upload.mutate(f);
          }}
        />
        {upload.isPending && <span className="text-sm text-slate-400">Importing…</span>}
      </div>
      <p className="mt-2 text-xs text-slate-400">
        CSV (date, description, amount — or debit/credit columns) or a PDF statement.
      </p>
      {warnings.length > 0 && (
        <ul className="mt-2 list-disc pl-5 text-xs text-amber-600">
          {warnings.map((w, i) => (
            <li key={i}>{w}</li>
          ))}
        </ul>
      )}
    </Card>
  );
}

function StatementLines({
  statementId,
  onChanged,
  onErr,
}: {
  statementId: string;
  onChanged: () => void;
  onErr: (e: unknown) => void;
}) {
  const qc = useQueryClient();
  const [openLine, setOpenLine] = useState<string | null>(null);
  const lines = useQuery<BankLine[]>({
    queryKey: ["reconciliation", "lines", statementId],
    queryFn: async () =>
      (await api.get(`/reconciliation/statements/${statementId}/lines`)).data,
  });

  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["reconciliation", "lines", statementId] });
    onChanged();
  };

  const unmatch = useMutation({
    mutationFn: async (lineId: string) =>
      (await api.delete(`/reconciliation/lines/${lineId}/match`)).data,
    onSuccess: refresh,
    onError: onErr,
  });
  const ignore = useMutation({
    mutationFn: async (lineId: string) =>
      (await api.post(`/reconciliation/lines/${lineId}/ignore`)).data,
    onSuccess: refresh,
    onError: onErr,
  });

  return (
    <div className="mt-3 border-t border-slate-100 pt-3">
      <table className="w-full text-sm">
        <thead>
          <tr className="text-left text-xs text-slate-400">
            <th className="py-1">Date</th>
            <th className="py-1">Description</th>
            <th className="py-1 text-right">Amount</th>
            <th className="py-1">Status</th>
            <th className="py-1"></th>
          </tr>
        </thead>
        <tbody>
          {(lines.data ?? []).map((ln) => {
            const negative = Number(ln.amount) < 0;
            return (
              <tr key={ln.id} className="border-t border-slate-100 align-top">
                <td className="py-1 text-slate-500">{shortDate(ln.line_date)}</td>
                <td className="py-1">{ln.description || "—"}</td>
                <td
                  className={`py-1 text-right tabular-nums ${negative ? "text-rose-600" : "text-emerald-700"}`}
                >
                  {money(ln.amount, "EUR")}
                </td>
                <td className="py-1">
                  <Badge tone={LINE_TONE[ln.status]}>{ln.status}</Badge>
                </td>
                <td className="py-1 text-right">
                  {ln.status === "unmatched" && (
                    <div className="flex justify-end gap-2">
                      <Button
                        size="sm"
                        variant="secondary"
                        onClick={() => setOpenLine(openLine === ln.id ? null : ln.id)}
                      >
                        {openLine === ln.id ? "Close" : "Find match"}
                      </Button>
                      <Button size="sm" variant="ghost" onClick={() => ignore.mutate(ln.id)}>
                        Ignore
                      </Button>
                    </div>
                  )}
                  {ln.status === "matched" && (
                    <Button size="sm" variant="ghost" onClick={() => unmatch.mutate(ln.id)}>
                      Unmatch
                    </Button>
                  )}
                  {ln.status === "ignored" && (
                    <Button size="sm" variant="ghost" onClick={() => unmatch.mutate(ln.id)}>
                      Restore
                    </Button>
                  )}
                  {openLine === ln.id && (
                    <Candidates lineId={ln.id} onMatched={() => { setOpenLine(null); refresh(); }} onErr={onErr} />
                  )}
                </td>
              </tr>
            );
          })}
          {(lines.data ?? []).length === 0 && (
            <tr>
              <td colSpan={5} className="py-2 text-slate-400">
                No lines.
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}

function Candidates({
  lineId,
  onMatched,
  onErr,
}: {
  lineId: string;
  onMatched: () => void;
  onErr: (e: unknown) => void;
}) {
  const candidates = useQuery<MatchCandidate[]>({
    queryKey: ["reconciliation", "candidates", lineId],
    queryFn: async () => (await api.get(`/reconciliation/lines/${lineId}/candidates`)).data,
  });
  const match = useMutation({
    mutationFn: async (c: MatchCandidate) =>
      (await api.post(`/reconciliation/lines/${lineId}/match`, { kind: c.kind, target_id: c.id }))
        .data,
    onSuccess: onMatched,
    onError: onErr,
  });

  return (
    <div className="mt-2 rounded-lg border border-slate-200 bg-slate-50 p-2 text-left">
      {(candidates.data ?? []).length === 0 && (
        <p className="text-xs text-slate-400">No matching receipt or payout found.</p>
      )}
      {(candidates.data ?? []).map((c) => (
        <div key={`${c.kind}-${c.id}`} className="flex items-center justify-between gap-2 py-1">
          <span className="text-xs text-slate-600">
            {c.kind === "receipt" ? "Receipt" : "Payout"} · {money(c.amount, "EUR")} ·{" "}
            {shortDate(c.date)}
            {c.reference ? ` · ${c.reference}` : ""}
            {c.days_off > 0 ? ` · ${c.days_off}d off` : ""}
          </span>
          <Button size="sm" loading={match.isPending} onClick={() => match.mutate(c)}>
            Match
          </Button>
        </div>
      ))}
    </div>
  );
}
