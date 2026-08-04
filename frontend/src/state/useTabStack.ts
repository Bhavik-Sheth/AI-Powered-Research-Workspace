import { useEffect, useState } from "react";
import { getProjectApiProjectsProjectIdGet, saveTabStackApiProjectsProjectIdTabStackPut, type TabRef } from "@research-os/api-client";

export type { TabRef };

/**
 * The center-pane tab stack (MODULES.md App Shell, Phase 1.8 sign-off) —
 * real state, persisted per project and restored on launch ("opening a
 * second paper opens a new independently-scrolled tab" means each open tab
 * keeps its own identity; this hook only owns the stack, not what each tab
 * renders).
 */
export function useTabStack(projectId: string) {
  const [tabs, setTabs] = useState<TabRef[]>([]);
  const [activeTab, setActiveTab] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setLoaded(false);
    (async () => {
      const { data } = await getProjectApiProjectsProjectIdGet({ path: { project_id: projectId }, throwOnError: true });
      if (cancelled) return;
      setTabs(data.tab_stack);
      setActiveTab(data.active_tab);
      setLoaded(true);
    })();
    return () => {
      cancelled = true;
    };
  }, [projectId]);

  function persist(nextTabs: TabRef[], nextActive: string | null) {
    void saveTabStackApiProjectsProjectIdTabStackPut({
      path: { project_id: projectId },
      body: { tab_stack: nextTabs, active_tab: nextActive },
    });
  }

  function openTab(tab: TabRef) {
    setTabs((prev) => {
      const next = prev.some((t) => t.id === tab.id) ? prev : [...prev, tab];
      persist(next, tab.id);
      return next;
    });
    setActiveTab(tab.id);
  }

  function closeTab(id: string) {
    setTabs((prev) => {
      const next = prev.filter((t) => t.id !== id);
      const nextActive = activeTab === id ? (next[next.length - 1]?.id ?? null) : activeTab;
      persist(next, nextActive);
      setActiveTab(nextActive);
      return next;
    });
  }

  function activateTab(id: string) {
    setActiveTab(id);
    persist(tabs, id);
  }

  /** Moves `id` to sit immediately before `beforeId` (or to the end when
   * `beforeId` is null) — the one place tab order changes. */
  function reorderTab(id: string, beforeId: string | null) {
    setTabs((prev) => {
      if (id === beforeId) return prev;
      const dragged = prev.find((t) => t.id === id);
      if (!dragged) return prev;
      const withoutDragged = prev.filter((t) => t.id !== id);
      const insertAt = beforeId === null ? withoutDragged.length : withoutDragged.findIndex((t) => t.id === beforeId);
      if (insertAt === -1) return prev;
      const next = [...withoutDragged.slice(0, insertAt), dragged, ...withoutDragged.slice(insertAt)];
      persist(next, activeTab);
      return next;
    });
  }

  return { tabs, activeTab, loaded, openTab, closeTab, activateTab, reorderTab };
}
