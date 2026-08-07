import { useState } from "react";

/** A user-toggled UI preference (Left Nav / Companion pane collapse) — pure
 * client layout state, persisted in localStorage so it survives a reload.
 * Not project data, so it never touches the backend.
 */
export function useCollapsible(key: string): [boolean, () => void, (next: boolean) => void] {
  const storageKey = `researchos.${key}`;
  const [collapsed, setCollapsedState] = useState(() => localStorage.getItem(storageKey) === "true");

  function toggle() {
    setCollapsedState((prev) => {
      const next = !prev;
      localStorage.setItem(storageKey, String(next));
      return next;
    });
  }

  // The direct setter — distinct from `toggle` because a viewport-width
  // trigger (Phase 6.2's auto-collapse) needs to force a specific state
  // ("below this width, collapsed") rather than flip whatever it currently
  // is, which a plain toggle can't express without risking re-expanding a
  // nav the user had already collapsed on purpose.
  function setCollapsed(next: boolean) {
    setCollapsedState(next);
    localStorage.setItem(storageKey, String(next));
  }

  return [collapsed, toggle, setCollapsed];
}
