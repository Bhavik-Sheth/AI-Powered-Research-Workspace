import { useEffect, useRef, useState } from "react";
import type {
  DragEvent as ReactDragEvent,
  KeyboardEvent as ReactKeyboardEvent,
  PointerEvent as ReactPointerEvent,
} from "react";
import { getAnchorApiAnchorsAnchorIdGet, listProjectsApiProjectsGet, type ProjectResponse } from "@research-os/api-client";
import { Route, Routes, useLocation, useNavigate, useParams } from "react-router-dom";

import { CompanionPane, type PendingAsk } from "../companion/CompanionPane";
import type { Citation, SelectionState } from "../companion/wsTypes";
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
// center pane's own minimum (CENTER_PANE_MIN_WIDTH, AppShell.css) keeps a
// PDF page or a matrix row from being squeezed toward zero (Phase 6.2) —
// below it the layout scrolls horizontally rather than compressing further.
const NAV_MIN_WIDTH = 56;
const NAV_DEFAULT_WIDTH = 200;
const NAV_MAX_WIDTH = 420;
const COMPANION_MIN_WIDTH = 240;
const COMPANION_DEFAULT_WIDTH = 280;
const COMPANION_MAX_WIDTH = 520;
// UI_DESIGN.md §7's "responsive story for ~1280px and below" (§9.2 item I).
const RESPONSIVE_BREAKPOINT_PX = 1280;

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

/** A resolved Companion citation, on its way to whichever reader tab
 * `paperId` names (Phase 6.1) — `nonce` marks each click distinct so the
 * same anchor clicked twice in a row is still consumed (mirrors
 * `PendingAsk`). */
interface PendingAnchor {
  paperId: string;
  quote: string;
  charStart: number;
  charEnd: number;
  nonce: number;
}

// The static tab a bare (paramless) route kind opens or activates — every
// kind except "reader" and "search" has exactly one instance, so the same
// TabRef this project already uses for its nav rows and initial-tab checks
// doubles as the URL <-> tab mapping's other direction.
const STATIC_TAB_BY_KIND: Record<string, TabRef> = {
  dashboard: DASHBOARD_TAB,
  library: LIBRARY_TAB,
  notes: NOTES_TAB,
  experiments: EXPERIMENTS_TAB,
  matrix: MATRIX_TAB,
  graph: GRAPH_TAB,
  feed: FEED_TAB,
  writing: WRITING_TAB,
  settings: SETTINGS_TAB,
  readiness: READINESS_TAB,
};

/**
 * The one place a tab's identity becomes a URL and back (Phase 6.4, D32).
 * Deliberately narrow: it only knows the same `TabRef` shapes `useTabStack`
 * already persists, not a general routing scheme — reader and search are
 * the only two kinds that carry an identifying param in the path, matching
 * how their tab ids already embed that same param (`reader:<paperId>`,
 * `search:<resultId>`).
 */
function tabPath(projectId: string, tab: TabRef): string {
  const base = `/p/${projectId}`;
  switch (tab.kind) {
    case "reader":
      return `${base}/paper/${tab.params?.paperId ?? ""}`;
    case "search":
      return tab.params?.resultId ? `${base}/search/${tab.params.resultId}` : `${base}/search`;
    case "dashboard":
      return base;
    // UI_DESIGN.md §4.3 names this route "papers", not the module/tab-kind
    // name "library" — the one place that distinction is made.
    case "library":
      return `${base}/papers`;
    default:
      return `${base}/${tab.kind}`;
  }
}

/** The reverse of `tabPath` — parses the part of the URL after `/p/:projectId`
 * back into the `TabRef` it names, so a browser back/forward step or a
 * pasted deep link can be resolved the same way a click already is. Returns
 * `null` for a path this app doesn't recognise (left for the caller to
 * treat as "stay put" rather than a hard 404 — there is no not-found screen
 * to route to, this app has ten fixed views). */
function pathToTabRef(projectId: string, subpath: string): TabRef | null {
  const parts = subpath.split("/").filter(Boolean);
  if (parts.length === 0) return DASHBOARD_TAB;
  if (parts[0] === "paper" && parts[1]) {
    return { id: `reader:${parts[1]}`, kind: "reader", params: { paperId: parts[1], projectId }, label: "" };
  }
  if (parts[0] === "search") {
    return parts[1]
      ? { id: `search:${parts[1]}`, kind: "search", params: { resultId: parts[1] }, label: "Search" }
      : SEARCH_TAB;
  }
  if (parts[0] === "papers") return LIBRARY_TAB;
  return STATIC_TAB_BY_KIND[parts[0]] ?? null;
}

/** Landing at `/` (Phase 1.8 sign-off / Phase 6.4): resolves the most-
 * recently-opened project, or the first project if none has been opened
 * yet, and redirects to its dashboard (`/p/:projectId`) — deep-linking
 * straight to `/p/:projectId/...` skips this entirely. */
function RootRedirect() {
  const [projects, setProjects] = useState<ProjectResponse[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [reloadNonce, setReloadNonce] = useState(0);
  const navigate = useNavigate();

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

  useEffect(() => {
    if (!projects || projects.length === 0) return;
    const mostRecent = [...projects].sort((a, b) => (b.last_opened_at ?? "").localeCompare(a.last_opened_at ?? ""))[0];
    navigate(`/p/${mostRecent.id}`, { replace: true });
  }, [projects, navigate]);

  if (error) {
    return (
      <AppBootScreen title="Could not load projects" message={error} onRetry={() => setReloadNonce((n) => n + 1)} />
    );
  }
  if (projects === null) {
    return <AppBootScreen title="Loading…" />;
  }
  if (projects.length === 0) {
    return <div className="center-pane__title">No project yet — create one to get started.</div>;
  }
  // The redirect effect above fires on this same render pass; this is only
  // ever visible for the instant before it does.
  return <AppBootScreen title="Loading…" />;
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
    updateTabLabel,
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
  // A Companion citation click, resolved to the paper + span it names
  // (Phase 6.1) — opens/activates that reader tab and, once mounted, is
  // consumed there the same one-shot way `pendingAsk` already is.
  const [pendingAnchor, setPendingAnchor] = useState<PendingAnchor | null>(null);
  const [navCollapsed, toggleNav, setNavCollapsed] = useCollapsible("leftNavCollapsed");
  const [companionCollapsed, toggleCompanion] = useCollapsible("companionCollapsed");

  // Below UI_DESIGN.md §7's ~1280px threshold, the nav collapses to icons
  // automatically — "the nav collapses to icons before the companion is
  // ever squeezed" (§7/§9.2 item I), since dropping the companion breaks
  // the product's premise (D32). One-directional: crossing under the
  // threshold forces a collapse; crossing back over it does not force a
  // re-expand, so a nav the user re-opened at a narrow width (the manual
  // toggle still works either way) isn't yanked shut again on the next tick.
  useEffect(() => {
    const query = window.matchMedia(`(max-width: ${RESPONSIVE_BREAKPOINT_PX}px)`);
    function applyBreakpoint(matches: boolean) {
      if (matches) setNavCollapsed(true);
    }
    applyBreakpoint(query.matches);
    const handler = (event: MediaQueryListEvent) => applyBreakpoint(event.matches);
    query.addEventListener("change", handler);
    return () => query.removeEventListener("change", handler);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
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
  const navigate = useNavigate();
  const location = useLocation();
  // Bidirectional URL <-> active-tab sync (Phase 6.4, D32). One shared flag
  // instead of two, so whichever effect below causes the other's deps to
  // change, that one run is recognised as "caused by us" and skipped —
  // without it the two effects would navigate at each other forever. Only
  // the *active* tab is reflected in the URL; the rest of the stack (which
  // tabs are open at all, their scroll position) stays exactly what it
  // already was — real state owned by `useTabStack`, not derived from the
  // address bar.
  const syncingRef = useRef<"fromUrl" | "fromState" | null>(null);

  useEffect(() => {
    void (async () => {
      const { data } = await listProjectsApiProjectsGet({ throwOnError: true });
      setProjectName(data.find((p) => p.id === projectId)?.name ?? "");
    })();
  }, [projectId]);

  // State -> URL: the active tab changed (by a click, a Companion
  // `ui_action`, or `closeTab` picking a new one) — push the matching path
  // so back/forward and a relaunch land on the right screen.
  useEffect(() => {
    if (!loaded) return;
    const active = tabs.find((tab) => tab.id === activeTab);
    if (!active) return;
    const path = tabPath(projectId, active);
    if (path === location.pathname) return;
    if (syncingRef.current === "fromUrl") {
      syncingRef.current = null;
      return;
    }
    syncingRef.current = "fromState";
    navigate(path);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeTab, loaded]);

  // URL -> state: a back/forward step, or a deep link opened directly,
  // changed the address bar without going through `openTab`/`activateTab` —
  // resolve it to a tab and activate it (opening it first if it isn't
  // already in the stack, e.g. a reader tab closed earlier that back now
  // wants to revisit).
  useEffect(() => {
    if (!loaded) return;
    if (syncingRef.current === "fromState") {
      syncingRef.current = null;
      return;
    }
    const subpath = location.pathname.startsWith(`/p/${projectId}`) ? location.pathname.slice(`/p/${projectId}`.length) : "";
    const target = pathToTabRef(projectId, subpath);
    if (!target || target.id === activeTab) return;
    syncingRef.current = "fromUrl";
    if (tabs.some((tab) => tab.id === target.id)) activateTab(target.id);
    else openTab(target);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [location.pathname, loaded]);

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

  // A Companion citation click (Phase 6.1) — only an "anchor" citation has
  // a reader position to jump to; a "memory" citation renders inert
  // (parseCitations.tsx never calls this for one). Opens the cited paper's
  // tab (its title fills in via `onTitleResolved` if it wasn't already
  // open) and hands it the resolved span the same one-shot way
  // `pendingAsk` already reaches the Companion.
  async function handleCiteClick(citation: Citation) {
    if (citation.kind !== "anchor") return;
    const { data: anchor } = await getAnchorApiAnchorsAnchorIdGet({
      path: { anchor_id: citation.anchor_id },
      throwOnError: true,
    });
    openReaderTab(anchor.paper_id, "");
    setPendingAnchor({
      paperId: anchor.paper_id,
      quote: anchor.quote,
      charStart: anchor.char_start,
      charEnd: anchor.char_end,
      nonce: Date.now(),
    });
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
        return (
          <ReaderTab
            paperId={tab.params?.paperId ?? ""}
            projectId={projectId}
            onAskCompanion={askCompanion}
            onTitleResolved={tab.label ? undefined : (title) => updateTabLabel(tab.id, title)}
            pendingAnchor={pendingAnchor?.paperId === tab.params?.paperId ? pendingAnchor : null}
          />
        );
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
            onCiteClick={(citation) => void handleCiteClick(citation)}
          />
        </div>
      </div>
    </div>
  );
}

export function AppShell() {
  return (
    <Routes>
      <Route path="/" element={<RootRedirect />} />
      <Route path="/p/:projectId/*" element={<ProjectShellRoute />} />
    </Routes>
  );
}

/** Reads `projectId` off the URL and mounts the shell for it — switching
 * projects (`ProjectSwitcher`) is just a `navigate` to another project's
 * dashboard, the same as any other in-app navigation (Phase 6.4). */
function ProjectShellRoute() {
  const { projectId } = useParams<{ projectId: string }>();
  const navigate = useNavigate();
  if (!projectId) return null; // the route pattern guarantees this; narrows the type
  return <ProjectShell projectId={projectId} onSwitchProject={(id) => navigate(`/p/${id}`)} />;
}
