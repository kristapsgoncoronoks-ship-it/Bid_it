import { useEffect, useState, type ReactNode } from "react";
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
 */
export function Portal({ children }: { children: ReactNode }) {
  const [el] = useState(() => {
    const node = document.createElement("div");
    node.setAttribute("data-iq-portal", "");
    return node;
  });

  useEffect(() => {
    document.body.appendChild(el);
    return () => {
      document.body.removeChild(el);
    };
  }, [el]);

  return createPortal(children, el);
}
