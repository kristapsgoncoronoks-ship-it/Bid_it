import type { ReactNode } from "react";
import { Button } from "./ui";
import {
  claimRefusal,
  isMappedRefusal,
  type PeriodShape,
} from "../lib/transportClaims";

/** The refusal banner every transport page uses — the mapped sentence, the
 * action to take, and (when we mapped the code) the server's own detail as the
 * specifics it named. The raw slug is never shown.
 *
 * `periodShape` (WO-91) tells the map WHICH period instruction to give for
 * `invalid_period`: a claim page asks for `2026-Q2`, a month-grained analytics
 * or admin page asks for `2026-04`. It defaults to `"claim"`, so a page that
 * never shows a period field is unaffected. */
export function RefusalNotice({
  code,
  detail,
  onDismiss,
  action,
  periodShape,
}: {
  code: string | null;
  detail: string;
  onDismiss?: () => void;
  action?: ReactNode;
  periodShape?: PeriodShape;
}) {
  const refusal = claimRefusal(code, detail, periodShape);
  return (
    <div
      role="alert"
      className="rounded-lg border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-800"
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="font-semibold">{refusal.title}</p>
          {refusal.next && <p className="mt-1 text-rose-700">{refusal.next}</p>}
          {isMappedRefusal(code) && detail && (
            <p className="mt-1 text-xs text-rose-600">{detail}</p>
          )}
          {action && <div className="mt-3">{action}</div>}
        </div>
        {onDismiss && (
          <Button size="sm" variant="ghost" onClick={onDismiss}>
            Dismiss
          </Button>
        )}
      </div>
    </div>
  );
}
