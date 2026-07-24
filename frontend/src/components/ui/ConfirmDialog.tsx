import type { ReactNode } from "react";
import { Modal } from "./Modal";
import { Button } from "./Button";

export interface ConfirmDialogProps {
  open: boolean;
  onClose: () => void;
  onConfirm: () => void;
  title: ReactNode;
  /** The body — explain the consequence in plain language. */
  children?: ReactNode;
  confirmLabel?: string;
  cancelLabel?: string;
  /** Use the danger button for irreversible actions (delete, void). */
  tone?: "primary" | "danger";
  /** Disables buttons + shows a spinner on confirm (e.g. while a mutation runs). */
  loading?: boolean;
}

/**
 * The canonical "are you sure?" surface, composed from `Modal`. Keeps confirm
 * flows visually + behaviourally identical everywhere: focus lands inside, Escape
 * cancels, and the backdrop does NOT dismiss (an explicit choice is required for a
 * consequential action).
 */
export function ConfirmDialog({
  open,
  onClose,
  onConfirm,
  title,
  children,
  confirmLabel = "Confirm",
  cancelLabel = "Cancel",
  tone = "primary",
  loading = false,
}: ConfirmDialogProps) {
  return (
    <Modal
      open={open}
      onClose={onClose}
      title={title}
      size="sm"
      closeOnBackdrop={false}
      footer={
        <>
          <Button variant="secondary" onClick={onClose} disabled={loading}>
            {cancelLabel}
          </Button>
          <Button variant={tone} onClick={onConfirm} loading={loading}>
            {confirmLabel}
          </Button>
        </>
      }
    >
      {children}
    </Modal>
  );
}
