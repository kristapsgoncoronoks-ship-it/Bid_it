import { useState } from "react";
import { PageHeader, Card, Timeline, Button, ConfirmDialog, Badge } from "../../components/ui";
import { toast } from "../../components/Toast";
import { FIXTURE_TIMELINE } from "../fixtures";

/**
 * Administration — audit trail + privileged actions, including a destructive flow
 * guarded by a danger `ConfirmDialog`. Demonstrates the audit `Timeline` and the
 * confirm pattern for irreversible actions. (Fixture data.)
 */
export default function Administration() {
  const [confirming, setConfirming] = useState(false);

  return (
    <div className="space-y-6">
      <PageHeader
        title="Administration"
        description="Security, audit, and workspace-level controls."
        meta={<Badge tone="warning">Elevated access</Badge>}
      />

      <div className="grid gap-6 lg:grid-cols-3">
        <Card title="Audit trail" className="lg:col-span-2">
          <Timeline
            events={FIXTURE_TIMELINE.map((e) => ({
              id: e.id, title: e.title, actor: e.actor, timestamp: e.timestamp, detail: e.detail, tone: e.tone,
            }))}
          />
        </Card>

        <div className="space-y-6">
          <Card title="Data export">
            <p className="text-sm text-slate-500">Download a signed archive of this entity's records.</p>
            <div className="mt-3">
              <Button variant="secondary" size="sm">Request export</Button>
            </div>
          </Card>

          <Card title="Danger zone" className="border-rose-200">
            <p className="text-sm text-slate-500">Permanently remove this legal entity and all its data.</p>
            <div className="mt-3">
              <Button variant="danger" size="sm" onClick={() => setConfirming(true)}>Delete entity</Button>
            </div>
          </Card>
        </div>
      </div>

      <ConfirmDialog
        open={confirming}
        onClose={() => setConfirming(false)}
        onConfirm={() => { setConfirming(false); toast.success("Delete confirmed (fixture — no-op)"); }}
        title="Delete this legal entity?"
        confirmLabel="Delete permanently"
        tone="danger"
      >
        This removes every invoice, payment, and contact under the entity. This action cannot be undone. In the fixtures it does nothing.
      </ConfirmDialog>
    </div>
  );
}
