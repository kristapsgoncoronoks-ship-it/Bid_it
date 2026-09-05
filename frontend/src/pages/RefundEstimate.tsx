import { useMutation } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { ModuleInactive } from "../components/ModuleGate";
import {
  Badge,
  Button,
  Card,
  DataTable,
  EmptyState,
  PageHeader,
  Skeleton,
  TextInput,
} from "../components/ui";
import { FileUpload } from "../components/ui";
import { api, apiError } from "../lib/api";
import { decimalMoney } from "../lib/format";
import { hasVatPerm } from "../lib/roles";
import { useModules } from "../lib/useModules";
import type { VatEstimate } from "../lib/types";

/**
 * The refund-estimate funnel (WO-AC; G4.8, R43) — *"upload last quarter, see
 * your refund opportunity"*.
 *
 * WHAT THIS SCREEN MUST NOT LET AN OPERATOR BELIEVE
 * ---------------------------------------------------
 * That the number is a claim. It is not, and §2.3 says so in the spec's own
 * words: `recoverable_eur = vat_eur`, invoiced VAT ASSUMED recoverable, *"a
 * sales preview, never a filed figure"*. Every gate in the real pipeline —
 * supplier registration, receipt control, documents, waivers, the Art. 17
 * minimum, the fee — can only reduce it. So the server's own caveat is
 * rendered next to the headline rather than tucked in a footnote, and R53
 * forbids swapping it for the wording used on a contractual claim-back.
 *
 * NOTHING IS STORED. The page says so, because an operator uploading a
 * prospect's statement is entitled to know whether they have just put that
 * prospect's data into the workspace. They have not.
 *
 * WHY THE THREE-STATE MINIMUM IS RENDERED AS THREE STATES
 * --------------------------------------------------------
 * `below_minimum` is `true` / `false` / `null`, and `null` means the Art. 17
 * comparison could not be made in the country's own currency (Sweden and
 * Denmark compare a local amount; a country whose lines arrive in mixed
 * currencies has no single one). Rendering `null` as "clears the threshold"
 * would tell someone a claim passes a check nobody ran — the same collapse
 * `estimate.py` refuses on the server.
 */

function MinimumBadge({ row }: { row: { below_minimum: boolean | null; threshold: string; threshold_currency: string } }) {
  if (row.below_minimum === null) {
    return (
      <Badge tone="neutral">
        Not compared — mixed currencies
      </Badge>
    );
  }
  if (row.below_minimum) {
    return (
      <Badge tone="warning">
        Below {decimalMoney(row.threshold, row.threshold_currency)}
      </Badge>
    );
  }
  return <Badge tone="success">Clears {decimalMoney(row.threshold, row.threshold_currency)}</Badge>;
}

export default function RefundEstimatePage() {
  const { user } = useAuth();
  const modules = useModules();
  const canWrite = hasVatPerm(user, "vat.write"); // cosmetic — the server enforces
  const [period, setPeriod] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [result, setResult] = useState<VatEstimate | null>(null);

  const enabled = modules.isEnabled("transport");

  const run = useMutation({
    mutationFn: async () => {
      const form = new FormData();
      form.append("file", files[0]);
      form.append("period", period.trim());
      return (await api.post("/transport/estimate", form)).data as VatEstimate;
    },
    onSuccess: (r) => {
      setErr(null);
      setResult(r);
    },
    onError: (e: unknown) => {
      setErr(apiError(e));
      setResult(null);
    },
  });

  if (modules.isLoading) return <Skeleton className="h-24 w-full" />;
  if (!enabled) {
    return (
      <div className="space-y-6">
        <PageHeader title="Refund estimate" />
        <ModuleInactive name={"Transport & VAT refunds"} />
      </div>
    );
  }

  const ready = files.length === 1 && period.trim() !== "";

  return (
    <div className="space-y-6">
      <PageHeader
        title="Refund estimate"
        description="Upload a quarter's fuel-card statement to see roughly how much VAT is sitting in it, per country. Nothing is stored — this reads the file and forgets it."
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
          <TextInput
            label="Claim period"
            required
            value={period}
            placeholder="2026-Q2"
            hint="YYYY-Q1..Q4, or YYYY-YEAR. This picks the minimum that applies — €400 for a quarter, €50 for a full year."
            onChange={(e) => setPeriod(e.target.value)}
          />
        </div>
        <div className="mt-4">
          <FileUpload
            label="Statement file"
            accept=".csv,text/csv"
            files={files}
            onFilesChange={setFiles}
            hint="CSV, as issued by the fuel-card network. The network is detected from the file itself."
          />
        </div>
        <div className="mt-4">
          <Button loading={run.isPending} disabled={!ready || !canWrite} onClick={() => run.mutate()}>
            Estimate the refund
          </Button>
          {!canWrite && (
            <p className="mt-2 text-xs text-slate-400">
              Your role can view estimates but not run one.
            </p>
          )}
        </div>
      </Card>

      {result && (
        <>
          <Card title="The opportunity">
            <p className="text-3xl font-semibold text-slate-800">
              {decimalMoney(result.recoverable_eur, "EUR")}
            </p>
            <p className="mt-1 text-sm text-slate-500">
              across {result.lines} line{result.lines === 1 ? "" : "s"} from a {result.network}{" "}
              statement, framed as {result.period}
            </p>
            <p
              className="mt-4 rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800"
              role="note"
            >
              {result.caveat}
            </p>
            {result.warnings.length > 0 && (
              <ul className="mt-3 space-y-1 text-xs text-slate-500">
                {result.warnings.map((w) => (
                  <li key={w}>{w}</li>
                ))}
              </ul>
            )}
          </Card>

          <Card title="By refund country" padded={false}>
            {result.countries.length === 0 ? (
              <EmptyState
                title="No countries in this statement"
                description="Every line was excluded, or the file carried none."
              />
            ) : (
              <DataTable
                rows={result.countries}
                rowKey={(c) => c.country}
                columns={[
                  { key: "country", header: "Country", cell: (c) => <span className="font-mono">{c.country}</span> },
                  { key: "lines", header: "Lines", align: "right", cell: (c) => c.lines },
                  { key: "litres", header: "Litres", align: "right", cell: (c) => c.litres },
                  {
                    key: "vat",
                    header: "VAT (EUR)",
                    align: "right",
                    cell: (c) => decimalMoney(c.vat_eur, "EUR"),
                  },
                  {
                    key: "minimum",
                    header: "Art. 17 minimum",
                    cell: (c) => <MinimumBadge row={c} />,
                  },
                  {
                    key: "excluded",
                    header: "Not converted",
                    align: "right",
                    cell: (c) =>
                      c.unconverted_lines > 0 ? (
                        <span className="text-amber-700">{c.unconverted_lines}</span>
                      ) : (
                        "—"
                      ),
                  },
                ]}
              />
            )}
          </Card>

          <Card title="Next step">
            <p className="text-sm text-slate-600">
              If this is worth pursuing, add the entity as a prospect on the customer activation
              screen — that is where onboarding starts, and it is the only thing on this page that
              writes anything.
            </p>
            <Link
              to="/vat-customers"
              className="mt-3 inline-block text-sm font-medium text-slate-700 underline"
            >
              Customer activation
            </Link>
          </Card>
        </>
      )}
    </div>
  );
}
