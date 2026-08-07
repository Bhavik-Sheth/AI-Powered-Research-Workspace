import { useEffect, useRef, useState } from "react";
import type {
  DragEvent as ReactDragEvent,
  KeyboardEvent as ReactKeyboardEvent,
  PointerEvent as ReactPointerEvent,
} from "react";
import { listProjectsApiProjectsGet, type ProjectResponse } from "@research-os/api-client";

import { CompanionPane, type PendingAsk } from "../companion/CompanionPane";
import type { SelectionState } from "../companion/wsTypes";
import { Dashboard } from "../dashboard/Dashboard";
import { ExperimentsBoard } from "../experiments/ExperimentsBoard";
import { FeedView } from "../feed/FeedView";
import { GraphView } from "../graph/GraphView";
import { LibraryView } from "../library/LibraryView";
import { MatrixView } from "../matrix/MatrixView";
import { NotesView } from "../notes/NotesView";
import { ReaderTab } from "../reader/ReaderTab";
import { SearchResults } from "../search/SearchResults";
import { SettingsPanel } from "../settings/SettingsPanel";
import { useCollapsible } from "../state/useCollapsible";
import { usePaneWidth } from "../state/usePaneWidth";
import { useProjectSocket } from "../state/useProjectSocket";
import { type TabRef, useTabStack } from "../state/useTabStack";
import { ManuscriptTab } from "../writing/ManuscriptTab";
import "./AppShell.css";
import { AppBootScreen } from "./ErrorBoundary";
import { ReadinessStrip } from "./ReadinessStrip";

// Icon-rail minimum keeps the nav usable as a landmark even at its narrowest;
// the Companion minimum keeps a chat bubble and its citation readable. The
// center pane never gets a minimum of its own — it is the flex remainder.
const NAV_MIN_WIDTH = 56;
const NAV_DEFAULT_WIDTH = 200;
const NAV_MAX_WIDTH = 420;
const COMPANION_MIN_WIDTH = 240;
const COMPANION_DEFAULT_WIDTH = 280;
const COMPANION_MAX_WIDTH = 520;

/** A drag handle between two panes. `direction` says which way the pointer
 * must move to grow the pane it resizes (+1 = right grows it, -1 = left
 * grows it), since the nav sits left of its handle and the Companion sits
 * right of its own. Reads `width` once at drag-start so a fast drag never
 * compounds against a stale render. */
function ResizeHandle({
  width,
  onChange,
  direction,
  onDragStateChange,
  label,
}: {
  width: number;
  onChange: (next: number) => void;
  direction: 1 | -1;
  onDragStateChange: (dragging: boolean) => void;
  label: string;
}) {
  function handlePointerDown(event: ReactPointerEvent<HTMLDivElement>) {
    event.preventDefault();
    const target = event.currentTarget;
    target.setPointerCapture(event.pointerId);
    const startX = event.clientX;
    const startWidth = width;
    onDragStateChange(true);

    function onMove(moveEvent: PointerEvent) {
      onChange(startWidth + direction * (moveEvent.clientX - startX));
    }
    function onUp() {
      target.releasePointerCapture(event.pointerId);
      onDragStateChange(false);
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
    }
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
  }

  return (
    <div
      className="pane-resizer"
      onPointerDown={handlePointerDown}
      role="separator"
      aria-orientation="vertical"
      aria-label={label}
    />
  );
}

// Subpixel rounding on getBoundingClientRect means an edge-to-edge fit can
// report a fraction of a pixel of "overhang" that isn't real overflow.
const OVERFLOW_EPSILON_PX = 1;

/** Tracks whether `ref`'s content is wider than its visible box, and which
 * of its direct children (matched by `data-tab-id`) are actually scrolled
 * outside that visible box — re-checked whenever the box or its content
 * resizes (ResizeObserver) or the box scrolls, driving both the tab strip's
 * overflow control and its overflow menu's contents from one measurement
 * rather than a one-shot pass or a second parallel mechanism. */
function useOverflow<T extends HTMLElement>(ref: React.RefObject<T | null>, deps: unknown[]) {
  const [overflowing, setOverflowing] = useState(false);
  const [hiddenTabIds, setHiddenTabIds] = useState<Set<string>>(new Set());

  useEffect(() => {
    const el = ref.current;
    if (!el) return;

    const check = () => {
      setOverflowing(el.scrollWidth > el.clientWidth);

      const containerRect = el.getBoundingClientRect();
      const hidden = new Set<string>();
      for (const child of el.children) {
        const tabId = (child as HTMLElement).dataset.tabId;
        if (!tabId) continue;
        const childRect = child.getBoundingClientRect();
        const fullyVisible =
          childRect.left >= containerRect.left - OVERFLOW_EPSILON_PX &&
          childRect.right <= containerRect.right + OVERFLOW_EPSILON_PX;
        if (!fullyVisible) hidden.add(tabId);
      }
      setHiddenTabIds(hidden);
    };

    check();
    const observer = new ResizeObserver(check);
    observer.observe(el);
    el.addEventListener("scroll", check, { passive: true });
    return () => {
      observer.disconnect();
      el.removeEventListener("scroll", check);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  return { overflowing, hiddenTabIds };
}

/** Fires `onActivate` on Enter/Space, mirroring the native `<button>`
 * Enter/Space-to-click behavior — for the handful of controls below that
 * must stay non-`<button>` elements (an `<li>` that needs to keep its list
 * semantics, a draggable tab strip) but still need to be keyboard-reachable
 * (Phase 4.1). */
function activateOnKey(onActivate: () => void) {
  return (event: ReactKeyboardEvent<HTMLElement>) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      onActivate();
    }
  };
}

const NAV_GROUPS: { label: string | null; items: string[] }[] = [
  { label: null, items: ["Dashboard"] },
  { label: "Library", items: ["Papers", "Notes"] },
  { label: "Work", items: ["Experiments", "Writing"] },
  { label: "Discover", items: ["Graph", "Feed", "Matrix"] },
];

const DASHBOARD_TAB: TabRef = { id: "dashboard", kind: "dashboard", params: {}, label: "Dashboard" };
const LIBRARY_TAB: TabRef = { id: "library", kind: "library", params: {}, label: "Papers" };
const NOTES_TAB: TabRef = { id: "notes", kind: "notes", params: {}, label: "Notes" };
const EXPERIMENTS_TAB: TabRef = { id: "experiments", kind: "experiments", params: {}, label: "Experiments" };
const SEARCH_TAB: TabRef = { id: "search", kind: "search", params: {}, label: "Search" };
const MATRIX_TAB: TabRef = { id: "matrix", kind: "matrix", params: {}, label: "Matrix" };
const GRAPH_TAB: TabRef = { id: "graph", kind: "graph", params: {}, label: "Graph" };
const FEED_TAB: TabRef = { id: "feed", kind: "feed", params: {}, label: "Feed" };
const WRITING_TAB: TabRef = { id: "writing", kind: "writing", params: {}, label: "Writing" };
const READINESS_TAB: TabRef = { id: "readiness", kind: "readiness", params: {}, label: "Readiness" };
const SETTINGS_TAB: TabRef = { id: "settings", kind: "settings", params: {}, label: "Settings" };

// The one place a nav row's label maps to the tab it opens — both the click
// handler and the active-row highlight (UI_DESIGN.md §2 "Row states") read
// from this instead of each keeping its own copy of the mapping.
const NAV_ITEM_TABS: Record<string, TabRef> = {
  Dashboard: DASHBOARD_TAB,
  Papers: LIBRARY_TAB,
  Notes: NOTES_TAB,
  Experiments: EXPERIMENTS_TAB,
  Matrix: MATRIX_TAB,
  Graph: GRAPH_TAB,
  Feed: FEED_TAB,
  Writing: WRITING_TAB,
};

// Reading-heavy screens dim the frame's blueprint grid behind them
// (UI_DESIGN.md §7 / §9.2 item H).
const QUIET_GRID_KINDS = new Set<TabRef["kind"]>(["reader", "writing", "notes"]);

/** Picks up where the user left off (Phase 1.8 sign-off): most-recently-
 * opened project, or the first project if none has been opened yet. */
function ProjectGate({ children }: { children: (projectId: string) => React.ReactNode }) {
  const [projects, setProjects] = useState<ProjectResponse[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [reloadNonce, setReloadNonce] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setError(null);
    void (async () => {
      try {
        const { data } = await listProjectsApiProjectsGet({ throwOnError: true });
        if (cancelled) return;
        setProjects(data);
      } catch (err) {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : "Could not load projects");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [reloadNonce]);

  if (error) {
    return (
      <AppBootScreen title="Could not load projects" message={error} onRetry={() => setReloadNonce((n) => n + 1)} />
    );
  }
  if (projects === null) {
    return <AppBootScreen title="Loading…" />;
  }
  const mostRecent = [...projects].sort((a, b) => (b.last_opened_at ?? "").localeCompare(a.last_opened_at ?? ""))[0];
  if (!mostRecent) {
    return <div className="center-pane__title">No project yet — create one to get started.</div>;
  }
  return (
    <>
      {children(mostRecent.id)}
    </>
  );
}

/** The project switcher chip (UI_DESIGN.md §2 "Top bar"). Styled as a chip
 * via `.top-bar__switcher` on the native `<select>` itself (`buttons.css`'s
 * own comment already names this "the one existing convention for a styled
 * <select> in this codebase") rather than replacing the element with a
 * hand-rolled dropdown — that would mean reproducing the native control's
 * keyboard nav and screen-reader semantics from scratch for what the spec
 * only asks to be a restyle (Rules.md AI Agent Behaviour: stop and ask
 * before that kind of scope growth; this stays inside "restyle a control"). */
function ProjectSwitcher({ projectId, onSwitch }: { projectId: string; onSwitch: (id: string) => void }) {
  const [projects, setProjects] = useState<ProjectResponse[]>([]);

  useEffect(() => {
    void (async () => {
      const { data } = await listProjectsApiProjectsGet({ throwOnError: true });
      setProjects(data);
    })();
  }, [projectId]);

  return (
    <div className="top-bar__switcher-wrap">
      <select className="top-bar__switcher" value={projectId} onChange={(event) => onSwitch(event.target.value)}>
        {projects.map((project) => (
          <option key={project.id} value={project.id}>
            {project.name}
          </option>
        ))}
      </select>
    </div>
  );
}

/**
 * Top bar + nav shell + a persisted, multi-tab center pane (MODULES.md App
 * Shell, Phase 1.8 sign-off). The Companion pane sits on every screen
 * (UI_DESIGN.md §3.1/D32) — the reader's selection popover feeds it a
 * question plus the D33 anchor as ambient `ui_state`. Every open tab stays
 * mounted (hidden, not unmounted) so switching back to one preserves its
 * scroll position — "opening a second paper opens a new independently-
 * scrolled tab."
 */
function ProjectShell({ projectId, onSwitchProject }: { projectId: string; onSwitchProject: (id: string) => void }) {
  const {
    tabs,
    activeTab,
    loaded,
    error: tabStackError,
    reload: reloadTabStack,
    openTab,
    closeTab,
    activateTab,
    reorderTab,
  } = useTabStack(projectId);
  const [draggingTabId, setDraggingTabId] = useState<string | null>(null);
  const [overflowOpen, setOverflowOpen] = useState(false);
  const tabBarRef = useRef<HTMLDivElement | null>(null);
  const { overflowing: tabBarOverflowing, hiddenTabIds } = useOverflow(
    tabBarRef,
    // Order matters as much as count — reordering the strip changes which
    // tabs are scrolled out of view without changing tabs.length.
    [tabs.map((tab) => tab.id).join("|")],
  );
  const [projectName, setProjectName] = useState("");
  const [selection, setSelection] = useState<SelectionState | null>(null);
  const [pendingAsk, setPendingAsk] = useState<PendingAsk | null>(null);
  // Top-bar global search (UI_DESIGN.md §2, Phase 5.4) — opens/focuses the
  // same canonical Search tab the left-nav "Search" row opens, feeding it
  // the typed query via `SearchResults`' `initialQuery` prop rather than
  // building a second, parallel search mechanism.
  const [topSearchValue, setTopSearchValue] = useState("");
  const [pendingSearch, setPendingSearch] = useState<{ text: string; nonce: number } | null>(null);
  const [navCollapsed, toggleNav] = useCollapsible("leftNavCollapsed");
  const [companionCollapsed, toggleCompanion] = useCollapsible("companionCollapsed");
  const [navWidth, setNavWidth] = usePaneWidth("leftNavWidth", NAV_DEFAULT_WIDTH, NAV_MIN_WIDTH, NAV_MAX_WIDTH);
  const [companionWidth, setCompanionWidth] = usePaneWidth(
    "companionWidth",
    COMPANION_DEFAULT_WIDTH,
    COMPANION_MIN_WIDTH,
    COMPANION_MAX_WIDTH,
  );
  const [navResizing, setNavResizing] = useState(false);
  const [companionResizing, setCompanionResizing] = useState(false);
  // The project's one WebSocket session (backend/ws/__init__.py: exactly one
  // live connection per project_id) — owned here, once, so CompanionPane and
  // ExperimentsBoard (via renderTabContent) can both consume it without
  // either opening a second connection that would evict the other's.
  const socket = useProjectSocket(projectId);

  useEffect(() => {
    void (async () => {
      const { data } = await listProjectsApiProjectsGet({ throwOnError: true });
      setProjectName(data.find((p) => p.id === projectId)?.name ?? "");
    })();
  }, [projectId]);

  useEffect(() => {
    if (loaded && tabs.length === 0) {
      openTab(DASHBOARD_TAB);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loaded]);

  function askCompanion(newSelection: SelectionState, question: string) {
    setSelection(newSelection);
    setPendingAsk({ text: question, nonce: Date.now() });
  }

  function openReaderTab(paperId: string, title: string) {
    openTab({ id: `reader:${paperId}`, kind: "reader", params: { paperId, projectId }, label: title });
  }

  function submitTopSearch() {
    if (!topSearchValue.trim()) return;
    openTab(SEARCH_TAB);
    setPendingSearch({ text: topSearchValue, nonce: Date.now() });
  }

  // A tool result's `ui_action` produces the same route transition the
  // user's own click would (Bug Fix Plan Phase 2.3) — one dispatch point,
  // not a callback per tool.
  function handleCompanionUIAction(action: string, payload: Record<string, unknown>) {
    if (action === "open_paper" && typeof payload.paper_id === "string") {
      openReaderTab(payload.paper_id, typeof payload.title === "string" ? payload.title : "");
    } else if (action === "open_search_results" && typeof payload.result_id === "string") {
      openTab({ id: `search:${payload.result_id}`, kind: "search", params: { resultId: payload.result_id }, label: "Search" });
    } else if (action === "open_note") {
      openTab(NOTES_TAB);
    }
  }

  // The read set a cross-paper compare claim (US4) is allowed to cite —
  // every paper currently open as a reader tab (MODULES.md Agent Harness).
  const openPaperIds = tabs
    .map((tab) => (tab.kind === "reader" ? tab.params?.paperId : undefined))
    .filter((paperId): paperId is string => Boolean(paperId));

  const activeTabKind = tabs.find((tab) => tab.id === activeTab)?.kind;

  function renderTabContent(tab: TabRef) {
    switch (tab.kind) {
      case "dashboard":
        return (
          <Dashboard
            projectId={projectId}
            projectName={projectName}
            tabs={tabs}
            activeTabId={activeTab}
            onResume={activateTab}
          />
        );
      case "library":
        return <LibraryView projectId={projectId} onOpenPaper={openReaderTab} onAddPaper={() => openTab(SEARCH_TAB)} />;
      case "notes":
        return <NotesView projectId={projectId} />;
      case "experiments":
        return <ExperimentsBoard projectId={projectId} socket={socket} />;
      case "matrix":
        return <MatrixView projectId={projectId} onOpenPaper={openReaderTab} />;
      case "graph":
        return <GraphView projectId={projectId} onOpenPaper={openReaderTab} />;
      case "feed":
        return <FeedView projectId={projectId} />;
      case "writing":
        return <ManuscriptTab projectId={projectId} />;
      case "reader":
        return <ReaderTab paperId={tab.params?.paperId ?? ""} projectId={projectId} onAskCompanion={askCompanion} />;
      case "search":
        return (
          <SearchResults
            projectId={projectId}
            initialResultId={tab.params?.resultId}
            initialQuery={tab.id === SEARCH_TAB.id ? pendingSearch : undefined}
          />
        );
      case "settings":
        return (
          <>
            <h1 className="center-pane__title">Settings</h1>
            <SettingsPanel projectId={projectId} />
          </>
        );
      case "readiness":
        return (
          <>
            <h1 className="center-pane__title">Readiness</h1>
            <ReadinessStrip />
          </>
        );
      default:
        return null;
    }
  }

  // A tab-stack load failure with nothing ever successfully loaded would
  // otherwise leave `loaded` stuck false forever and the center pane
  // permanently empty with no explanation — surface it instead of hanging.
  if (tabStackError && tabs.length === 0) {
    return <AppBootScreen title="Could not load this project" message={tabStackError} onRetry={reloadTabStack} />;
  }

  return (
    <div className={`app-frame ${activeTabKind && QUIET_GRID_KINDS.has(activeTabKind) ? "app-frame--quiet-grid" : ""}`}>
      <header className="top-bar">
        Research Companion OS
        <ProjectSwitcher projectId={projectId} onSwitch={onSwitchProject} />
        <div className="top-bar__spacer" />
        <input
          type="text"
          className="top-bar__search"
          value={topSearchValue}
          onChange={(event) => setTopSearchValue(event.target.value)}
          onKeyDown={(event) => event.key === "Enter" && submitTopSearch()}
          placeholder="Search everything…"
          aria-label="Search everything"
        />
        <button
          type="button"
          className="top-bar__settings"
          aria-label="Settings"
          onClick={() => openTab(SETTINGS_TAB)}
        >
          ⚙
        </button>
      </header>
      <div className="shell-body">
        <div
          className={`pane-shell pane-shell--left ${navCollapsed ? "pane-shell--collapsed" : ""} ${navResizing ? "pane-shell--resizing" : ""}`}
          style={navCollapsed ? undefined : { width: navWidth }}
        >
          <nav className="left-nav">
            {NAV_GROUPS.map((group) => (
              <div key={group.label ?? "root"}>
                {group.label && <div className="left-nav__group-label">{group.label}</div>}
                <ul style={{ margin: 0, padding: 0 }}>
                  {group.items.map((item) => {
                    const openThisTab = () => openTab(NAV_ITEM_TABS[item]);
                    return (
                      <li
                        key={item}
                        className={`left-nav__item ${NAV_ITEM_TABS[item].kind === activeTabKind ? "left-nav__item--active" : ""}`}
                        style={{ cursor: "pointer" }}
                        tabIndex={0}
                        role="button"
                        onClick={openThisTab}
                        onKeyDown={activateOnKey(openThisTab)}
                      >
                        {item}
                      </li>
                    );
                  })}
                </ul>
              </div>
            ))}
            <button
              type="button"
              className={`left-nav__item ${activeTabKind === SEARCH_TAB.kind ? "left-nav__item--active" : ""}`}
              onClick={() => openTab(SEARCH_TAB)}
            >
              Search
            </button>
            <button
              type="button"
              className={`left-nav__item ${activeTabKind === SETTINGS_TAB.kind ? "left-nav__item--active" : ""}`}
              onClick={() => openTab(SETTINGS_TAB)}
            >
              Settings
            </button>
            <button
              type="button"
              className={`left-nav__item ${activeTabKind === READINESS_TAB.kind ? "left-nav__item--active" : ""}`}
              onClick={() => openTab(READINESS_TAB)}
            >
              Readiness
            </button>
          </nav>
        </div>
        {!navCollapsed && (
          <ResizeHandle
            width={navWidth}
            onChange={setNavWidth}
            direction={1}
            onDragStateChange={setNavResizing}
            label="Resize navigation"
          />
        )}
        <button
          type="button"
          className="pane-toggle"
          onClick={toggleNav}
          aria-label={navCollapsed ? "Expand navigation" : "Collapse navigation"}
        >
          {navCollapsed ? "›" : "‹"}
        </button>
        <div className="center-pane-column">
          {tabs.length > 1 && (
            <div className="tab-bar-row">
              <div className="tab-bar" ref={tabBarRef}>
                {tabs.map((tab, index) => {
                  const activateThisTab = () => activateTab(tab.id);
                  return (
                    <div
                      key={tab.id}
                      data-tab-id={tab.id}
                      draggable
                      className={`tab-bar__tab ${tab.id === activeTab ? "tab-bar__tab--active" : ""} ${tab.id === draggingTabId ? "tab-bar__tab--dragging" : ""}`}
                      tabIndex={0}
                      role="button"
                      onClick={activateThisTab}
                      onKeyDown={(event) => {
                        // The close button below is a real, independently
                        // focusable <button> nested inside this element —
                        // its own Enter/Space keydown bubbles here too, so
                        // only react to a keydown that targeted this tab
                        // itself (mirrors the close button's onClick
                        // stopPropagation, which guards the mouse case).
                        if (event.target !== event.currentTarget) return;
                        activateOnKey(activateThisTab)(event);
                      }}
                      onDragStart={(event: ReactDragEvent<HTMLDivElement>) => {
                        event.dataTransfer.effectAllowed = "move";
                        setDraggingTabId(tab.id);
                      }}
                      onDragEnd={() => setDraggingTabId(null)}
                      onDragOver={(event: ReactDragEvent<HTMLDivElement>) => {
                        if (!draggingTabId || draggingTabId === tab.id) return;
                        event.preventDefault();
                      }}
                      onDrop={(event: ReactDragEvent<HTMLDivElement>) => {
                        if (!draggingTabId || draggingTabId === tab.id) return;
                        event.preventDefault();
                        const rect = event.currentTarget.getBoundingClientRect();
                        const dropBeforeThisTab = event.clientX < rect.left + rect.width / 2;
                        const beforeId = dropBeforeThisTab ? tab.id : (tabs[index + 1]?.id ?? null);
                        reorderTab(draggingTabId, beforeId);
                        setDraggingTabId(null);
                      }}
                    >
                      <span className="tab-bar__label">{tab.label}</span>
                      {tab.kind !== "dashboard" && (
                        <button
                          type="button"
                          className="tab-bar__close"
                          aria-label={`Close ${tab.label}`}
                          onClick={(event) => {
                            event.stopPropagation();
                            closeTab(tab.id);
                          }}
                        >
                          ×
                        </button>
                      )}
                    </div>
                  );
                })}
              </div>
              {tabBarOverflowing && (
                <div className="tab-bar__overflow">
                  <button
                    type="button"
                    className="tab-bar__overflow-toggle"
                    aria-label="Show hidden tabs"
                    onClick={() => setOverflowOpen((open) => !open)}
                  >
                    »
                  </button>
                  {overflowOpen && (
                    <ul className="tab-bar__overflow-menu">
                      {tabs.filter((tab) => hiddenTabIds.has(tab.id)).map((tab) => {
                        const selectFromOverflow = () => {
                          activateTab(tab.id);
                          setOverflowOpen(false);
                          document.querySelector(`[data-tab-id="${tab.id}"]`)?.scrollIntoView({ inline: "nearest" });
                        };
                        return (
                          <li
                            key={tab.id}
                            className={tab.id === activeTab ? "tab-bar__overflow-item--active" : ""}
                            tabIndex={0}
                            role="button"
                            onClick={selectFromOverflow}
                            onKeyDown={activateOnKey(selectFromOverflow)}
                          >
                            {tab.label}
                          </li>
                        );
                      })}
                    </ul>
                  )}
                </div>
              )}
            </div>
          )}
          <main className="center-pane-stack">
            {tabs.map((tab) => (
              <div key={tab.id} className="center-pane" style={{ display: tab.id === activeTab ? undefined : "none" }}>
                {renderTabContent(tab)}
              </div>
            ))}
          </main>
        </div>
        <button
          type="button"
          className="pane-toggle"
          onClick={toggleCompanion}
          aria-label={companionCollapsed ? "Expand companion" : "Collapse companion"}
        >
          {companionCollapsed ? "‹" : "›"}
        </button>
        {!companionCollapsed && (
          <ResizeHandle
            width={companionWidth}
            onChange={setCompanionWidth}
            direction={-1}
            onDragStateChange={setCompanionResizing}
            label="Resize companion"
          />
        )}
        <div
          className={`pane-shell pane-shell--right ${companionCollapsed ? "pane-shell--collapsed" : ""} ${companionResizing ? "pane-shell--resizing" : ""}`}
          style={companionCollapsed ? undefined : { width: companionWidth }}
        >
          <CompanionPane
            projectId={projectId}
            selection={selection}
            pendingAsk={pendingAsk}
            socket={socket}
            openPaperIds={openPaperIds}
            onUIAction={handleCompanionUIAction}
          />
        </div>
      </div>
    </div>
  );
}

export function AppShell() {
  return <ProjectGate>{(projectId) => <ProjectShellWithSwitch initialProjectId={projectId} />}</ProjectGate>;
}

function ProjectShellWithSwitch({ initialProjectId }: { initialProjectId: string }) {
  const [projectId, setProjectId] = useState(initialProjectId);
  return <ProjectShell projectId={projectId} onSwitchProject={setProjectId} />;
}
