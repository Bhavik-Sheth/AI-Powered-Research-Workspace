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

/**
 * Authenticated fetch for binary responses (the PDF endpoint) — every REST
 * request carries the bearer token (D2), which a plain `<iframe src>` or
 * `<embed>` cannot attach, so this is the one place a reader tab fetches
 * PDF bytes directly instead of going through the generated JSON client.
 */
export async function fetchBinary(path: string): Promise<ArrayBuffer> {
  const bridge = resolveBridge();
  const response = await fetch(`http://127.0.0.1:${bridge.port}${path}`, {
    headers: { Authorization: `Bearer ${bridge.token}` },
  });
  if (!response.ok) {
    throw new Error(`${path} failed: ${response.status}`);
  }
  return response.arrayBuffer();
}

/**
 * `ws://127.0.0.1:<port>/ws/session/:projectId?token=...` — the WebSocket
 * upgrade carries the bearer token as a query param (D2), since the
 * browser `WebSocket` constructor cannot set an `Authorization` header.
 */
export function wsSessionUrl(projectId: string): string {
  const bridge = resolveBridge();
  return `ws://127.0.0.1:${bridge.port}/ws/session/${projectId}?token=${encodeURIComponent(bridge.token)}`;
}
