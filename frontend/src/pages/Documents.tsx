import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Badge, Card, EmptyState, QueryState, Skeleton } from "../components/ui";
import { api } from "../lib/api";
import type { DocumentEntry } from "../lib/types";

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

const KIND_TONE: Record<string, "success" | "warning" | "neutral"> = {
  invoice: "success",
  receipt: "warning",
};

/** Read-only registry of every stored original in the workspace (admin-gated on
 * the server — it spans every user's uploads). Metadata only: the bytes are
 * served by each owner's surface (invoice, expense, partner…), never from here. */
export default function DocumentsPage() {
  const [kind, setKind] = useState("");

  const docs = useQuery<DocumentEntry[]>({
    queryKey: ["documents", kind],
    queryFn: async () =>
      (await api.get(`/documents${kind ? `?kind=${encodeURIComponent(kind)}` : ""}`)).data,
  });

  const kinds = Array.from(new Set((docs.data ?? []).map((d) => d.kind))).sort();

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold">Documents</h1>
        <p className="text-slate-500">
          Every stored original this workspace holds, with its integrity hash. This registry is
          read-only — documents are added by uploads and downloaded from their owning record.
        </p>
      </div>

      <label className="flex items-center gap-2 text-sm text-slate-500">
        Kind
        <select
          className="rounded-lg border border-slate-300 px-2 py-1 text-sm"
          value={kind}
          onChange={(e) => setKind(e.target.value)}
        >
          <option value="">All kinds</option>
          {kinds.map((k) => (
            <option key={k} value={k}>
              {k}
            </option>
          ))}
        </select>
      </label>

      <Card>
        <QueryState
          query={docs}
          loading={<Skeleton className="h-24 w-full" />}
          isEmpty={(rows) => rows.length === 0}
          empty={
            <EmptyState
              title="No documents yet"
              description="Originals appear here as invoices, receipts and attachments are uploaded."
            />
          }
          errorTitle="Couldn’t load the document registry"
        >
          {(rows) => (
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-xs text-slate-400">
                  <th className="py-1">Filename</th>
                  <th className="py-1">Kind</th>
                  <th className="py-1">Type</th>
                  <th className="py-1">Size</th>
                  <th className="py-1">SHA-256</th>
                  <th className="py-1">Uploaded</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((d) => (
                  <tr key={d.id} className="border-t border-slate-100">
                    <td className="py-1 font-medium">{d.filename ?? "—"}</td>
                    <td className="py-1">
                      <Badge tone={KIND_TONE[d.kind] ?? "neutral"}>{d.kind}</Badge>
                    </td>
                    <td className="py-1 text-xs text-slate-500">{d.mime ?? "—"}</td>
                    <td className="py-1 tabular-nums text-xs">{formatSize(d.size)}</td>
                    <td className="py-1 font-mono text-xs" title={d.sha256}>
                      {d.sha256.slice(0, 12)}…
                    </td>
                    <td className="py-1 text-xs text-slate-500">
                      {new Date(d.created_at).toLocaleDateString()}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </QueryState>
      </Card>
    </div>
  );
}
