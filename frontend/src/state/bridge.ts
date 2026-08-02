/** The one place the renderer reads `{port, token}` exposed by the Electron preload (D2). */

import { client } from "@research-os/api-client";

export interface DesktopBridge {
  port: number;
  token: string;
}

declare global {
  interface Window {
    researchOS?: DesktopBridge;
  }
}

function resolveBridge(): DesktopBridge {
  if (window.researchOS) {
    return window.researchOS;
  }
  // Dev-only fallback so the renderer can run standalone (`npm run dev`)
  // against a sidecar started by hand, without the Electron shell.
  const devPort = import.meta.env.VITE_DEV_PORT;
  const devToken = import.meta.env.VITE_DEV_TOKEN;
  if (import.meta.env.DEV && devPort && devToken) {
    return { port: Number(devPort), token: devToken };
  }
  throw new Error("researchOS bridge is not available — not running inside the Electron shell");
}

/** Configures the generated API client's base URL and bearer token, once, at renderer start. */
export function configureApiClient(): void {
  const bridge = resolveBridge();
  client.setConfig({
    baseUrl: `http://127.0.0.1:${bridge.port}`,
    headers: { Authorization: `Bearer ${bridge.token}` },
  });
}
