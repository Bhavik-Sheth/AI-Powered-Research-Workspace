import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { App } from "./app/App";
import { AppBootScreen, ErrorBoundary } from "./app/ErrorBoundary";
import "./design/tokens.css";
import { configureApiClient } from "./state/bridge";

const root = createRoot(document.getElementById("root")!);
const queryClient = new QueryClient();

try {
  // Runs before React ever mounts, so a missing Electron bridge (or a dev
  // session started without VITE_DEV_PORT/VITE_DEV_TOKEN) has no component
  // tree yet for an ErrorBoundary to catch — this is the one exception
  // handled by hand rather than left to crash the page load silently.
  configureApiClient();
  root.render(
    <StrictMode>
      <ErrorBoundary>
        <QueryClientProvider client={queryClient}>
          <App />
        </QueryClientProvider>
      </ErrorBoundary>
    </StrictMode>,
  );
} catch (err) {
  root.render(
    <AppBootScreen title="Could not start the app" message={err instanceof Error ? err.message : String(err)} />,
  );
}
