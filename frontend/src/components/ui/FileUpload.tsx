import { useId, useRef, useState, type ReactNode } from "react";
import { cx } from "../../lib/cx";

function humanSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export interface FileUploadProps {
  label: ReactNode;
  /** Selected files (controlled). */
  files: File[];
  onFilesChange: (files: File[]) => void;
  /** e.g. ".pdf,.png,image/*". */
  accept?: string;
  multiple?: boolean;
  hint?: ReactNode;
  error?: ReactNode;
  disabled?: boolean;
  className?: string;
}

/**
 * Accessible file upload. The dropzone is a real focusable control (Enter/Space
 * open the native picker), the underlying `<input type="file">` is visually hidden
 * but wired to a label, and drag-and-drop is layered on top for pointer users.
 * Selected files list with per-file remove; sizes shown. Validation (type/size) is
 * the caller's job — pass an `error` string to surface it.
 */
export function FileUpload({
  label, files, onFilesChange, accept, multiple = false, hint, error, disabled, className = "",
}: FileUploadProps) {
  const inputId = useId();
  const hintId = `${inputId}-hint`;
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);

  function addFiles(list: FileList | null) {
    if (!list || list.length === 0) return;
    const incoming = Array.from(list);
    onFilesChange(multiple ? [...files, ...incoming] : incoming.slice(0, 1));
  }

  function removeAt(i: number) {
    onFilesChange(files.filter((_, idx) => idx !== i));
  }

  function open() {
    if (!disabled) inputRef.current?.click();
  }

  return (
    <div className={cx("flex flex-col gap-1.5", className)}>
      <span className="text-sm font-medium text-slate-700">{label}</span>

      <div
        role="button"
        tabIndex={disabled ? -1 : 0}
        aria-disabled={disabled || undefined}
        aria-describedby={hint ? hintId : undefined}
        onClick={open}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            open();
          }
        }}
        onDragOver={(e) => {
          if (disabled) return;
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          if (disabled) return;
          e.preventDefault();
          setDragging(false);
          addFiles(e.dataTransfer.files);
        }}
        className={cx(
          "flex cursor-pointer flex-col items-center justify-center gap-1 rounded-xl border-2 border-dashed px-6 py-8 text-center transition",
          "focus-visible:outline-hidden focus-visible:ring-2 focus-visible:ring-brand-300 focus-visible:ring-offset-1",
          disabled && "cursor-not-allowed opacity-50",
          error ? "border-rose-300 bg-rose-50/40" : dragging ? "border-brand-400 bg-brand-50" : "border-slate-300 bg-slate-50 hover:border-brand-300 hover:bg-brand-50/40",
        )}
      >
        <svg width="24" height="24" viewBox="0 0 24 24" fill="none" aria-hidden="true" className="text-slate-400">
          <path d="M12 16V4m0 0l-4 4m4-4l4 4" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
          <path d="M4 16v2a2 2 0 002 2h12a2 2 0 002-2v-2" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
        </svg>
        <p className="text-sm font-medium text-slate-600">
          <span className="text-brand-700">Choose a file</span> or drag it here
        </p>
        {hint && (
          <p id={hintId} className="text-xs text-slate-400">
            {hint}
          </p>
        )}
        <input
          ref={inputRef}
          id={inputId}
          type="file"
          accept={accept}
          multiple={multiple}
          disabled={disabled}
          className="sr-only"
          onChange={(e) => {
            addFiles(e.target.files);
            e.target.value = ""; // allow re-selecting the same file
          }}
        />
      </div>

      {files.length > 0 && (
        <ul className="flex flex-col gap-1.5">
          {files.map((file, i) => (
            <li
              key={`${file.name}-${i}`}
              className="flex items-center justify-between gap-3 rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm"
            >
              <span className="flex min-w-0 items-center gap-2">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" aria-hidden="true" className="shrink-0 text-slate-400">
                  <path d="M14 3v5h5M14 3H7a2 2 0 00-2 2v14a2 2 0 002 2h10a2 2 0 002-2V8l-5-5z" stroke="currentColor" strokeWidth="2" strokeLinejoin="round" />
                </svg>
                <span className="truncate text-slate-700">{file.name}</span>
                <span className="shrink-0 text-xs text-slate-400 tabular-nums">{humanSize(file.size)}</span>
              </span>
              <button
                type="button"
                onClick={() => removeAt(i)}
                aria-label={`Remove ${file.name}`}
                className="shrink-0 rounded-sm p-1 text-slate-400 hover:bg-slate-100 hover:text-rose-600 focus-visible:outline-hidden focus-visible:ring-2 focus-visible:ring-brand-300"
              >
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" aria-hidden="true">
                  <path d="M6 6l12 12M18 6L6 18" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
                </svg>
              </button>
            </li>
          ))}
        </ul>
      )}

      {error && (
        <p role="alert" className="text-xs font-medium text-rose-600">
          {error}
        </p>
      )}
    </div>
  );
}
