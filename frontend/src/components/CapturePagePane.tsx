import { useQuery } from "@tanstack/react-query";
import { useEffect, useState, type ReactNode } from "react";
import { useAuth } from "../auth/AuthContext";
import { api, apiError, downloadFile } from "../lib/api";

type Page = { number: number; width: number; height: number; size: number };
type Manifest = { status: string; pages: Page[]; total_pages: number | null; truncated: boolean; message: string | null };
type Props = { runId: string; filename: string | null; fallback: ReactNode };

export function CapturePagePane(props: Props) {
  const { user, org } = useAuth();
  return <Pages key={`${user?.id}:${org?.id}:${props.runId}`} {...props} />;
}

function Pages({ runId, filename, fallback }: Props) {
  const { user, org } = useAuth();
  const [index, setIndex] = useState(0);
  const [downloading, setDownloading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showOriginal, setShowOriginal] = useState(false);
  const manifest = useQuery<Manifest>({ queryKey: ["capture-pages", user?.id, org?.id, runId],
    queryFn: async () => (await api.get(`/invoices/captures/${runId}/pages`)).data,
    retry: false });
  if (manifest.isPending) return <p role="status" className="card p-4">Loading page previews…</p>;
  if (manifest.isError) return <div>
    <p role="alert">Couldn’t load page previews. <button className="underline" title="Reload stored preview metadata" onClick={() => manifest.refetch()}>Retry previews</button></p>
    {showOriginal ? fallback : <button className="btn-ghost" title="Load the full original document only when needed" onClick={() => setShowOriginal(true)}>Load original viewer</button>}
  </div>;
  const data = manifest.data;
  if (data.status !== "ready" || !data.pages?.length) return <div>
    {data.message && <p className="mb-2 text-xs text-slate-500">{data.message}</p>}
    {showOriginal ? fallback : <button className="btn-ghost" title="Load the full original document only when needed" onClick={() => setShowOriginal(true)}>Load original viewer</button>}
  </div>;
  const selected = data.pages[Math.min(index, data.pages.length - 1)];
  const download = async () => {
    setDownloading(true); setError(null);
    try { await downloadFile(`/invoices/captures/${runId}/source`, filename ?? "document"); }
    catch (e) { setError(apiError(e)); }
    finally { setDownloading(false); }
  };
  return <div className="card p-0">
    <div className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-200 p-3">
      <h2 className="text-sm font-semibold">Source page preview</h2>
      <button className="text-sm text-brand-600 underline" title="Download the authoritative original document; previews are reduced images" disabled={downloading} onClick={download}>{downloading ? "Downloading…" : "Download original"}</button>
    </div>
    <div className="flex items-center justify-center gap-3 p-2 text-sm">
      <button className="btn-ghost" title="Show the preceding stored page" disabled={index <= 0} onClick={() => setIndex(index - 1)}>Previous page</button>
      <span>Page {selected.number} of {data.total_pages ?? data.pages.length}</span>
      <button className="btn-ghost" title="Show the next stored page" disabled={index >= data.pages.length - 1} onClick={() => setIndex(index + 1)}>Next page</button>
    </div>
    {data.truncated && <p className="px-3 text-xs text-amber-700">Previews show the first {data.pages.length} of {data.total_pages} pages. Download the original for remaining pages.</p>}
    {error && <p role="alert" className="px-3 text-sm text-rose-600">{error}</p>}
    <PageImage key={selected.number} runId={runId} page={selected.number} />
  </div>;
}

function PageImage({ runId, page }: { runId: string; page: number }) {
  const { user, org } = useAuth();
  const [url, setUrl] = useState<string | null>(null);
  const [decodeError, setDecodeError] = useState(false);
  const image = useQuery<Blob>({ queryKey: ["capture-page-image", user?.id, org?.id, runId, page], gcTime: 0,
    retry: false, queryFn: async () => (await api.get(`/invoices/captures/${runId}/pages/${page}/doc`, { responseType: "blob" })).data });
  useEffect(() => {
    if (!image.data) return;
    const next = URL.createObjectURL(image.data); setUrl(next); setDecodeError(false);
    return () => URL.revokeObjectURL(next);
  }, [image.data]);
  if (image.isError || decodeError) return <p role="alert" className="p-3">Page preview unavailable. Use Download original or <button className="underline" title="Reload this stored page image" onClick={() => { setDecodeError(false); image.refetch(); }}>Retry page</button>.</p>;
  if (!url) return <p role="status" className="p-3">Loading page {page}…</p>;
  return <img src={url} alt={`Source page ${page}`} onError={() => setDecodeError(true)} className="max-h-[38rem] w-full object-contain p-2" />;
}
