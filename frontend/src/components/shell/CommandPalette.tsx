import { useEffect, useId, useMemo, useRef, useState, type KeyboardEvent as ReactKeyboardEvent } from "react";
import { useNavigate } from "react-router-dom";
import { Modal } from "../ui/Modal";
import type { NavGroup, NavItem } from "./nav";

/**
 * "Go to page" (Ctrl/⌘ K) — TW-P2-01, reference integration R4 (Twenty's
 * permission-aware command menu, transferred as a principle).
 *
 * The palette receives the SAME `navGroups` the sidebar renders. `Layout` has
 * already applied module gating and the SERVED permissions to that list, so
 * quick navigation cannot become a second authorization or availability
 * model: a destination the sidebar does not show is not findable here either.
 *
 * It is page navigation, not workspace search — no API call, no record content
 * indexed. The name, the empty state and the missing magnifier all say so.
 *
 * Keyboard contract (R4 review, Adversarial A-1 / Product U-3): focus lands in
 * the query on open (the Modal's `initialFocusRef`) and STAYS there while
 * typing — the focus trap places focus once (FE-022). ↑↓ move, Enter opens,
 * Esc closes (the Modal handles Esc).
 * The input is a combobox over the listbox so a screen reader is told which
 * page is highlighted.
 */

interface CommandPaletteProps {
  navGroups: NavGroup[];
}

interface PaletteEntry {
  group: string;
  item: NavItem;
}

const MAX_RESULTS = 12;

function normalise(value: string): string {
  return value.trim().toLowerCase();
}

export function CommandPalette({ navGroups }: CommandPaletteProps) {
  const navigate = useNavigate();
  const listboxId = useId();
  const inputRef = useRef<HTMLInputElement>(null);
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [activeIndex, setActiveIndex] = useState(0);

  const entries = useMemo<PaletteEntry[]>(
    () => navGroups.flatMap((group) => group.items.map((item) => ({ group: group.title, item }))),
    [navGroups],
  );

  const matches = useMemo(() => {
    const needle = normalise(query);
    if (!needle) return entries.slice(0, MAX_RESULTS);
    return entries
      .filter(({ group, item }) => normalise(`${item.label} ${group} ${item.to}`).includes(needle))
      .slice(0, MAX_RESULTS);
  }, [entries, query]);

  const close = () => {
    setOpen(false);
    setQuery("");
    setActiveIndex(0);
  };

  const openPalette = () => {
    setQuery("");
    setActiveIndex(0);
    setOpen(true);
  };

  const choose = (entry: PaletteEntry) => {
    close();
    navigate(entry.item.to);
  };

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (!event.altKey && !event.shiftKey && (event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        if (open) close();
        else openPalette();
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [open]);

  const onInputKeyDown = (event: ReactKeyboardEvent<HTMLInputElement>) => {
    if (event.key === "ArrowDown") {
      event.preventDefault();
      if (matches.length > 0) setActiveIndex((current) => (current + 1) % matches.length);
      return;
    }
    if (event.key === "ArrowUp") {
      event.preventDefault();
      if (matches.length > 0) setActiveIndex((current) => (current - 1 + matches.length) % matches.length);
      return;
    }
    if (event.key === "Enter" && matches.length > 0) {
      event.preventDefault();
      choose(matches[Math.min(activeIndex, matches.length - 1)]);
    }
  };

  const optionId = (index: number) => `${listboxId}-option-${index}`;
  const highlighted = matches.length > 0 ? Math.min(activeIndex, matches.length - 1) : -1;

  return (
    <>
      <button
        type="button"
        onClick={openPalette}
        aria-label="Go to page"
        aria-keyshortcuts="Control+K Meta+K"
        className="hidden items-center gap-2 rounded-lg border border-slate-200 bg-white px-2.5 py-1.5 text-sm text-slate-500 shadow-xs transition hover:border-slate-300 hover:text-slate-700 focus-visible:outline-hidden focus-visible:ring-2 focus-visible:ring-brand-300 sm:flex"
      >
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" aria-hidden="true">
          <path d="M5 12h14M13 6l6 6-6 6" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
        <span>Go to page…</span>
        <kbd className="rounded border border-slate-200 bg-slate-50 px-1.5 py-0.5 text-[10px] text-slate-400">Ctrl/⌘ K</kbd>
      </button>

      <Modal open={open} onClose={close} title="Go to page" size="md" initialFocusRef={inputRef}>
        <label htmlFor="go-to-page-query" className="sr-only">
          Page name
        </label>
        <input
          id="go-to-page-query"
          ref={inputRef}
          role="combobox"
          aria-expanded="true"
          aria-controls={listboxId}
          aria-autocomplete="list"
          aria-activedescendant={highlighted >= 0 ? optionId(highlighted) : undefined}
          value={query}
          onChange={(event) => {
            setQuery(event.target.value);
            setActiveIndex(0);
          }}
          onKeyDown={onInputKeyDown}
          placeholder="Type a page name…"
          className="w-full rounded-lg border border-slate-300 bg-white px-3 py-2.5 text-sm text-slate-800 outline-hidden placeholder:text-slate-400 focus:border-brand-400 focus:ring-2 focus:ring-brand-100"
        />

        <div id={listboxId} role="listbox" aria-label="Matching pages" className="mt-3 max-h-80 space-y-1 overflow-y-auto">
          {matches.map((entry, index) => (
            <button
              key={`${entry.group}:${entry.item.to}`}
              id={optionId(index)}
              type="button"
              role="option"
              aria-selected={index === highlighted}
              onMouseEnter={() => setActiveIndex(index)}
              onClick={() => choose(entry)}
              className={
                "flex w-full items-center gap-3 rounded-lg px-3 py-2 text-left transition " +
                (index === highlighted ? "bg-brand-50 text-brand-800" : "text-slate-700 hover:bg-slate-50")
              }
            >
              <span className="shrink-0 text-slate-500">{entry.item.icon}</span>
              <span className="min-w-0 flex-1">
                <span className="block truncate text-sm font-medium">{entry.item.label}</span>
                <span className="block truncate text-xs text-slate-400">{entry.group}</span>
              </span>
            </button>
          ))}
        </div>
        {matches.length === 0 && (
          <p role="status" className="mt-3 rounded-lg bg-slate-50 px-3 py-6 text-center text-sm text-slate-500">
            No page matches. To find an invoice or a document, open its page and filter there.
          </p>
        )}

        <p className="mt-3 text-xs text-slate-400">↑↓ move · Enter open · Esc close</p>
      </Modal>
    </>
  );
}
