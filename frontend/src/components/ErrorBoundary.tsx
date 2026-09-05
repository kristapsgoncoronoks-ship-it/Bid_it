import { Component, type ErrorInfo, type ReactNode } from "react";
import { ErrorState } from "./ui/ErrorState";

/**
 * FE-003 (audit 2026-09-05) — the one render throw that used to white out the
 * whole app. React unmounts the entire tree when a render error reaches the
 * root; with no boundary anywhere, a single malformed response on one page
 * (`d.items.map` on an empty object, a formatter fed `undefined`) blanked the
 * shell, the nav and the banner — no message, no way back but a reload.
 *
 * Mounted around the routed page inside the shell, keyed by pathname, so a
 * throw takes out ONE page and the user keeps the shell, the nav and a
 * "Try again" that re-renders the page. Event handlers, async code and
 * effects are not covered by boundaries — those already surface through the
 * query/mutation caches and the toast.
 */
interface Props {
  children: ReactNode;
  /** Change this to reset the boundary (the shell passes the pathname). */
  resetKey?: string;
}

interface State {
  error: Error | null;
}

export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    // The console is the one place a render crash is still visible to the
    // developer; nothing here is sent anywhere.
    console.error("page render failed", error, info.componentStack);
  }

  componentDidUpdate(prev: Props): void {
    if (this.state.error && prev.resetKey !== this.props.resetKey) {
      this.setState({ error: null });
    }
  }

  render(): ReactNode {
    if (this.state.error) {
      return (
        <ErrorState
          title="This page hit an error"
          description="The rest of the workspace is unaffected. Try again, or pick another page from the navigation."
          onRetry={() => this.setState({ error: null })}
          className="card"
        />
      );
    }
    return this.props.children;
  }
}
