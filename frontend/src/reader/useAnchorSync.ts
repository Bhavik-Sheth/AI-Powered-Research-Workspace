import { useCallback, useState } from "react";

export interface ActiveAnchor {
  quote: string;
  charStart: number;
  charEnd: number;
}

/**
 * Drives "click a card field / PDF span / citation, the others light up
 * together" (D33) — exactly one active span at a time. Cross-pane sync with
 * the Companion citation lands in Phase 1.5; this is the reader-local half.
 */
export function useAnchorSync() {
  const [activeAnchor, setActiveAnchor] = useState<ActiveAnchor | null>(null);

  const focusAnchor = useCallback((anchor: ActiveAnchor | null) => {
    setActiveAnchor(anchor);
  }, []);

  return { activeAnchor, focusAnchor };
}
