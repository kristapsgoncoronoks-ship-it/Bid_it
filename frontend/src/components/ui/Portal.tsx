import { useEffect, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";

/**
 * Renders children into a detached node appended to <body>, so overlays escape any
 * parent `overflow:hidden` / stacking context and always sit on top.
 *
 * The target node is created synchronously (lazy `useState` initializer) rather
 * than in an effect, so children — and their refs — are committed on the FIRST
 * render. This matters: a consumer like `Modal` attaches a focus-trap ref and reads
 * it in `useEffect`; if the portal target only appeared a render later, that ref
 * would still be null and the trap would silently no-op. The append to <body>
 * happens in an effect (child effects run before the parent's, so the node is in
 * the document before the parent's focus logic runs). Client-only (Vite SPA).
 *
 * Removal is deferred by one microtask (FE-022, reference R4 review): in
 * development React's StrictMode simulates an unmount right after the first
 * mount, and a cleanup that detached the node synchronously threw the focus
 * the consumer had just placed inside it back to `<body>` — the go-to-page
 * palette opened unfocused. The simulated remount cancels the pending
 * removal, so the node stays attached; a real unmount removes it a tick later.
 */
export function Portal({ children }: { children: ReactNode }) {
  const [el] = useState(() => {
    const node = document.createElement("div");
    node.setAttribute("data-iq-portal", "");
    return node;
  });
  const cancelRemoval = useRef<(() => void) | null>(null);

  useEffect(() => {
    cancelRemoval.current?.();
    cancelRemoval.current = null;
    if (!el.isConnected) document.body.appendChild(el);
    return () => {
      let cancelled = false;
      cancelRemoval.current = () => {
        cancelled = true;
      };
      queueMicrotask(() => {
        if (!cancelled) el.remove();
      });
    };
  }, [el]);

  return createPortal(children, el);
}
