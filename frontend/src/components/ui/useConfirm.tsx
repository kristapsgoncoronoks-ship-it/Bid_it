import { useCallback, useState, type ReactNode } from "react";
import { ConfirmDialog } from "./ConfirmDialog";

export interface ConfirmOptions {
  title: ReactNode;
  /** The consequence, in plain language. */
  body?: ReactNode;
  confirmLabel?: string;
  cancelLabel?: string;
  tone?: "primary" | "danger";
}

/**
 * FE-008 (audit 2026-09-05) — a promise-shaped `ConfirmDialog`, so a delete
 * button can read `if (await confirm({...})) remove.mutate(id)` instead of a
 * bare `remove.mutate(id)` (thirteen of those shipped) or `window.confirm`
 * (which the design system replaced for a reason: it cannot be styled,
 * traps focus in the browser chrome and is blocked by some kiosk browsers).
 *
 * Render `dialog` once anywhere in the owning component's tree.
 */
export function useConfirm(): {
  confirm: (opts: ConfirmOptions) => Promise<boolean>;
  dialog: ReactNode;
} {
  const [pending, setPending] = useState<{
    opts: ConfirmOptions;
    resolve: (ok: boolean) => void;
  } | null>(null);

  const confirm = useCallback(
    (opts: ConfirmOptions) =>
      new Promise<boolean>((resolve) => {
        setPending({ opts, resolve });
      }),
    [],
  );

  const settle = (ok: boolean) => {
    pending?.resolve(ok);
    setPending(null);
  };

  const dialog = pending ? (
    <ConfirmDialog
      open
      onClose={() => settle(false)}
      onConfirm={() => settle(true)}
      title={pending.opts.title}
      confirmLabel={pending.opts.confirmLabel ?? "Confirm"}
      cancelLabel={pending.opts.cancelLabel}
      tone={pending.opts.tone ?? "danger"}
    >
      {pending.opts.body}
    </ConfirmDialog>
  ) : null;

  return { confirm, dialog };
}
