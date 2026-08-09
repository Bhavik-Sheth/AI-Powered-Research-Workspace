/** The one place the renderer reads `{port, token}` exposed by the Electron preload (D2). */

import { client } from "@research-os/api-client";

import { beginActivity, endActivity } from "./backgroundActivity";

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

  // Every REST call this app makes goes through this one generated client,
  // so this is the single place that ticks the global background-activity
  // signal (Bug Fix Plan Phase 6.12) — no per-call wiring needed anywhere
  // else. Exactly one of `response`/`error` fires per request (`error`
  // only when `fetch` itself never produced a response, e.g. offline), so
  // this never double-decrements.
  client.interceptors.request.use((request) => {
    beginActivity();
    return request;
  });
  client.interceptors.response.use((response) => {
    endActivity();
    return response;
  });
  client.interceptors.error.use((error) => {
    endActivity();
    return error;
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
  beginActivity();
  try {
    const response = await fetch(`http://127.0.0.1:${bridge.port}${path}`, {
      headers: { Authorization: `Bearer ${bridge.token}` },
    });
    if (!response.ok) {
      throw new Error(`${path} failed: ${response.status}`);
    }
    return await response.arrayBuffer();
  } finally {
    endActivity();
  }
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

/** Posts a raw binary body (recorded audio), returns the parsed JSON reply. */
export async function postBinaryForJson<T>(path: string, body: ArrayBuffer): Promise<T> {
  const bridge = resolveBridge();
  beginActivity();
  try {
    const response = await fetch(`http://127.0.0.1:${bridge.port}${path}`, {
      method: "POST",
      headers: { Authorization: `Bearer ${bridge.token}`, "Content-Type": "application/octet-stream" },
      body,
    });
    if (!response.ok) {
      throw new Error(`${path} failed: ${response.status}`);
    }
    return await response.json();
  } finally {
    endActivity();
  }
}

/** Posts a JSON body, returns the raw binary reply (synthesized audio). */
export async function postJsonForBinary(path: string, body: unknown): Promise<ArrayBuffer> {
  const bridge = resolveBridge();
  beginActivity();
  try {
    const response = await fetch(`http://127.0.0.1:${bridge.port}${path}`, {
      method: "POST",
      headers: { Authorization: `Bearer ${bridge.token}`, "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!response.ok) {
      throw new Error(`${path} failed: ${response.status}`);
    }
    return await response.arrayBuffer();
  } finally {
    endActivity();
  }
}
