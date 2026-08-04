import { useCallback, useState } from "react";

/** A user-dragged pane width (left nav / Companion column) — pure client
 * layout state, persisted in localStorage so it survives a restart. Not
 * project data, so it never touches the backend (same pattern as
 * `useCollapsible`, which owns the sibling collapse-to-zero preference for
 * the same panes).
 */
export function usePaneWidth(key: string, defaultWidth: number, min: number, max: number): [number, (next: number) => void] {
  const storageKey = `researchos.${key}`;
  const [width, setWidthState] = useState(() => {
    const stored = Number(localStorage.getItem(storageKey));
    return Number.isFinite(stored) && stored > 0 ? Math.min(Math.max(stored, min), max) : defaultWidth;
  });

  const setWidth = useCallback(
    (next: number) => {
      const clamped = Math.min(Math.max(next, min), max);
      setWidthState(clamped);
      localStorage.setItem(storageKey, String(clamped));
    },
    [storageKey, min, max],
  );

  return [width, setWidth];
}
