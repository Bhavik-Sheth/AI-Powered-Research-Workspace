import { useState } from "react";

/** A user-toggled UI preference (Left Nav / Companion pane collapse) — pure
 * client layout state, persisted in localStorage so it survives a reload.
 * Not project data, so it never touches the backend.
 */
export function useCollapsible(key: string): [boolean, () => void] {
  const storageKey = `researchos.${key}`;
  const [collapsed, setCollapsed] = useState(() => localStorage.getItem(storageKey) === "true");

  function toggle() {
    setCollapsed((prev) => {
      const next = !prev;
      localStorage.setItem(storageKey, String(next));
      return next;
    });
  }

  return [collapsed, toggle];
}
