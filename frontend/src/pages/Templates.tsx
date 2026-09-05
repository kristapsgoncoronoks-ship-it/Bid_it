import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Badge, Button, QueryState, Skeleton } from "../components/ui";
import { api, apiError } from "../lib/api";
import type { TemplateList } from "../lib/types";
import { useConfirm } from "../components/ui/useConfirm";

/**
 * Document templates (lifecycle phase 5 machinery — owner direction).
 *
 * The trust model, as the screen shows it: the PLATFORM'S master documents are
 * read-only starting points (the demo texts say plainly they are examples, not
 * legal advice; the owner's lawyer's standardized texts will replace them).
 * "Adjust" copies a master's text into an editor; SAVE keeps it as this
 * company's own version — as many named versions as they like — and a later
 * platform edit never reaches into a saved copy.
 *
 * Placeholders like {{project.code}} or {{customer.name}} are filled when a
 * document is generated from a project; anything the system doesn't know stays
 * visibly unreplaced, so a gap can be seen before anyone signs.
 */
export default function Templates() {
  const { confirm, dialog } = useConfirm();
  const qc = useQueryClient();
  const [err, setErr] = useState<string | null>(null);
  const [editing, setEditing] = useState<{
    id?: string; // present = editing an own template; absent = new from master/scratch
    source_platform_id?: string;
    kind: string;
    name: string;
    body: string;
  } | null>(null);

  const data = useQuery<TemplateList>({
    queryKey: ["templates"],
    queryFn: async () => (await api.get("/templates")).data,
  });

  const save = useMutation({
    mutationFn: async () => {
      if (!editing) return;
      if (editing.id) {
        return (
          await api.patch(`/templates/${editing.id}`, {
            name: editing.name,
            body: editing.body,
          })
        ).data;
      }
      return (
        await api.post("/templates", {
          name: editing.name,
          kind: editing.kind,
          body: editing.body,
          source_platform_id: editing.source_platform_id ?? null,
        })
      ).data;
    },
    onSuccess: () => {
      setEditing(null);
      setErr(null);
      qc.invalidateQueries({ queryKey: ["templates"] });
    },
    onError: (e) => setErr(apiError(e)),
  });

  const remove = useMutation({
    mutationFn: async (id: string) => api.delete(`/templates/${id}`),
    onSuccess: () => {
      setErr(null);
      qc.invalidateQueries({ queryKey: ["templates"] });
    },
    onError: (e) => setErr(apiError(e)),
  });

  return (
    <div className="space-y-6">
      {dialog}
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Document templates</h1>
        <p className="text-sm text-slate-500">
          Standard documents you can adjust to your needs. Save as many versions
          as you like and choose one whenever you generate a document from a
          project. Placeholders like {"{{project.code}}"} are filled
          automatically; anything unknown stays visible so you can spot gaps.
        </p>
      </div>

      {err && (
        <div className="rounded-md border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">
          {err}
        </div>
      )}

      {editing && (
        <div className="card space-y-3 p-6">
          <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-500">
            {editing.id ? "Edit your version" : "New version"}
          </h2>
          <div className="flex flex-wrap gap-2">
            <div className="grow">
              <label className="label" htmlFor="name">Name</label>
              <input id="name"
                className="input"
                value={editing.name}
                onChange={(e) => setEditing({ ...editing, name: e.target.value })}
                placeholder="Our contract (strict payment terms)"
              />
            </div>
            <div>
              <label className="label" htmlFor="kind">Kind</label>
              <select id="kind"
                className="input"
                value={editing.kind}
                disabled={!!editing.id || !!editing.source_platform_id}
                onChange={(e) => setEditing({ ...editing, kind: e.target.value })}
              >
                {["contract", "acceptance", "offer", "other"].map((k) => (
                  <option key={k} value={k}>
                    {k}
                  </option>
                ))}
              </select>
            </div>
          </div>
          <div>
            <label className="label" htmlFor="text">Text</label>
            <textarea id="text"
              className="input min-h-72 font-mono text-xs"
              value={editing.body}
              onChange={(e) => setEditing({ ...editing, body: e.target.value })}
            />
          </div>
          <div className="flex gap-2">
            <Button onClick={() => save.mutate()} disabled={save.isPending || !editing.name}>
              Save version
            </Button>
            <Button variant="secondary" onClick={() => setEditing(null)}>
              Cancel
            </Button>
          </div>
        </div>
      )}

      <QueryState
        query={data}
        loading={<Skeleton className="h-48 w-full" />}
        errorTitle="Couldn’t load templates"
      >
        {(d) => (
          <div className="grid gap-6 lg:grid-cols-2">
            <div className="card space-y-3 p-6">
              <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-500">
                Standard documents
              </h2>
              <p className="text-xs text-slate-400">
                Maintained by the platform. Adjust one to make it yours.
              </p>
              <table className="w-full text-sm">
                <tbody className="divide-y divide-slate-100">
                  {d.platform.map((t) => (
                    <tr key={t.id}>
                      <td className="py-2 text-slate-700">{t.name}</td>
                      <td className="py-2 text-slate-400">{t.kind}</td>
                      <td className="py-2 pl-3 text-right">
                        <button
                          className="btn-ghost text-xs"
                          onClick={() =>
                            setEditing({
                              source_platform_id: t.id,
                              kind: t.kind,
                              name: `${t.name} — our version`,
                              body: t.body,
                            })
                          }
                        >
                          Adjust
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            <div className="card space-y-3 p-6">
              <div className="flex items-center justify-between">
                <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-500">
                  Your versions
                </h2>
                <button
                  className="btn-ghost text-xs"
                  onClick={() =>
                    setEditing({ kind: "contract", name: "", body: "" })
                  }
                >
                  + From scratch
                </button>
              </div>
              {d.own.length === 0 ? (
                <p className="text-sm text-slate-400">
                  Nothing saved yet — adjust a standard document to start.
                </p>
              ) : (
                <table className="w-full text-sm">
                  <tbody className="divide-y divide-slate-100">
                    {d.own.map((t) => (
                      <tr key={t.id}>
                        <td className="py-2 text-slate-700">{t.name}</td>
                        <td className="py-2 text-slate-400">{t.kind}</td>
                        <td className="py-2 pl-2">
                          <Badge tone={t.active ? "success" : "neutral"}>
                            {t.active ? "active" : "inactive"}
                          </Badge>
                        </td>
                        <td className="py-2 pl-3 text-right">
                          <button
                            className="btn-ghost text-xs"
                            onClick={() =>
                              setEditing({
                                id: t.id,
                                kind: t.kind,
                                name: t.name,
                                body: t.body,
                              })
                            }
                          >
                            Edit
                          </button>
                          <button
                            className="btn-ghost text-xs text-rose-500"
                            disabled={remove.isPending}
                            onClick={async () => { if (await confirm({ title: "Delete this template?", body: "Documents already generated from it are kept; nothing new can be generated from it.", confirmLabel: "Delete" })) remove.mutate(t.id); }}
                          >
                            Delete
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
          </div>
        )}
      </QueryState>
    </div>
  );
}
