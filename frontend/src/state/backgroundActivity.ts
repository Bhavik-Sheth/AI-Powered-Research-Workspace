/**
 * Global "something is happening in the background" signal (Bug Fix Plan
 * Phase 6.12). A single counter, incremented/decremented by the generated
 * API client's own request/response interceptors (wired once in
 * `bridge.ts::configureApiClient`) — every REST call this app makes ticks
 * it automatically, with no per-component wiring needed. `AppShell`'s
 * top-right indicator is the one reader; nothing else needs to know this
 * exists.
 *
 * A plain module-level store + `useSyncExternalStore`, not React Context:
 * this is mutated from outside any component (the client interceptors),
 * so there is no natural provider to own it, and a context re-render on
 * every tick would be wasted work for the one small badge that reads it.
 */

import { useSyncExternalStore } from "react";

let pending = 0;
const listeners = new Set<() => void>();

function notify(): void {
  for (const listener of listeners) listener();
}

/** Called by the API client's request interceptor — one call in flight. */
export function beginActivity(): void {
  pending += 1;
  if (pending === 1) notify();
}

/** Called by the API client's response/error interceptors — one call settled. */
export function endActivity(): void {
  pending = Math.max(0, pending - 1);
  if (pending === 0) notify();
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

/** `true` whenever at least one API request is still in flight. */
export function useBackgroundActivity(): boolean {
  return useSyncExternalStore(
    subscribe,
    () => pending > 0,
    () => false,
  );
}
