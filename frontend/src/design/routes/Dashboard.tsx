import { Link } from "react-router-dom";
import { Card, PageHeader, Timeline, EmptyState, Button } from "../../components/ui";
import { NAV_GROUPS } from "../../components/shell/nav";
import { FIXTURE_TIMELINE } from "../fixtures";

/**
 * Dashboard — deliberately NOT a wall of fake KPIs. It orients the user with entry
 * points into each area and a recent-activity feed, and shows an explicit empty
 * state where real metrics would live once data syncs. (Fixture data.)
 */
export default function Dashboard() {
  const shortcuts = NAV_GROUPS.flatMap((g) => g.items).filter((i) => i.to !== "/design");

  return (
    <div>
      <PageHeader
        title="Good morning"
        description="Your finance workspace at a glance. Jump into an area or review recent activity."
        actions={
          <Link to="/design/gallery">
            <Button variant="secondary">View component gallery</Button>
          </Link>
        }
      />

      <div className="grid gap-6 lg:grid-cols-3">
        <div className="lg:col-span-2 space-y-6">
          <Card title="Jump to">
            <ul className="grid gap-2 sm:grid-cols-2">
              {shortcuts.map((s) => (
                <li key={s.to}>
                  <Link
                    to={s.to}
                    className="flex items-center gap-3 rounded-lg border border-slate-200 px-3 py-2.5 text-sm font-medium text-slate-700 hover:border-brand-300 hover:bg-brand-50/50 focus-visible:outline-hidden focus-visible:ring-2 focus-visible:ring-brand-300"
                  >
                    <span className="text-slate-400">{s.icon}</span>
                    {s.label}
                  </Link>
                </li>
              ))}
            </ul>
          </Card>

          <Card title="Cash position">
            <EmptyState
              title="Metrics appear once data syncs"
              description="This is where period totals and trends will render from your connected accounts — intentionally empty in the fixtures so nothing here is mistaken for real figures."
            />
          </Card>
        </div>

        <Card title="Recent activity">
          <Timeline
            events={FIXTURE_TIMELINE.map((e) => ({
              id: e.id,
              title: e.title,
              actor: e.actor,
              timestamp: e.timestamp,
              detail: e.detail,
              tone: e.tone,
            }))}
          />
          <div className="mt-3">
            <Button variant="ghost" size="sm">
              View all activity
            </Button>
          </div>
        </Card>
      </div>
    </div>
  );
}
