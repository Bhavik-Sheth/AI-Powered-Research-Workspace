import { Component, type ErrorInfo, type ReactNode } from "react";

import "./ErrorBoundary.css";

/**
 * Centered full-viewport status screen (Frontend Improvement Plan Phase 1) —
 * the one shape for every "the app hasn't rendered its shell yet" state: a
 * startup failure before React even mounts (main.tsx), a caught render
 * crash (`ErrorBoundary` below), or a top-level loading/error state before
 * the project shell exists (App.tsx, AppShell.tsx's `ProjectGate`).
 */
export function AppBootScreen({
  title,
  message,
  onRetry,
  retryLabel = "Retry",
}: {
  title: string;
  message?: string;
  onRetry?: () => void;
  retryLabel?: string;
}) {
  return (
    <div className="app-boot">
      <p className="app-boot__title">{title}</p>
      {message && <p className="app-boot__message">{message}</p>}
      {onRetry && (
        <button type="button" className="app-boot__retry" onClick={onRetry}>
          {retryLabel}
        </button>
      )}
    </div>
  );
}

interface Props {
  children: ReactNode;
}

interface State {
  error: Error | null;
}

/**
 * Catches any uncaught render-time exception anywhere below it in the tree
 * and shows a recoverable fallback instead of leaving the app permanently
 * blank (Frontend Improvement Plan Phase 1.1). Reload is the only recovery
 * offered — an error boundary can reset its own state, but the exception
 * already ran mid-render, so the children that threw are not safe to
 * re-mount without a fresh app instance.
 */
export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error("Uncaught render error", error, info.componentStack);
  }

  render(): ReactNode {
    if (this.state.error) {
      return (
        <AppBootScreen
          title="This screen crashed"
          message={this.state.error.message}
          onRetry={() => window.location.reload()}
          retryLabel="Reload"
        />
      );
    }
    return this.props.children;
  }
}
