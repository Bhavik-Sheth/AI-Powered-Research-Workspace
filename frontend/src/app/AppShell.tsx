import { useEffect, useState } from "react";
import type { PointerEvent as ReactPointerEvent } from "react";
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

/** Picks up where the user left off (Phase 1.8 sign-off): most-recently-
 * opened project, or the first project if none has been opened yet. */
function ProjectGate({ children }: { children: (projectId: string) => React.ReactNode }) {
  const [projects, setProjects] = useState<ProjectResponse[] | null>(null);
  const [projectId, setProjectId] = useState<string | null>(null);

  useEffect(() => {
    void (async () => {
      const { data } = await listProjectsApiProjectsGet({ throwOnError: true });
      setProjects(data);
      const mostRecent = [...data].sort((a, b) => (b.last_opened_at ?? "").localeCompare(a.last_opened_at ?? ""))[0];
      setProjectId(mostRecent?.id ?? null);
    })();
  }, []);

  if (projects === null) return null;
  if (projectId === null || projects.length === 0) {
    return <div className="center-pane__title">No project yet — create one to get started.</div>;
  }
  return (
    <>
      {children(projectId)}
    </>
  );
}

function ProjectSwitcher({ projectId, onSwitch }: { projectId: string; onSwitch: (id: string) => void }) {
  const [projects, setProjects] = useState<ProjectResponse[]>([]);

  useEffect(() => {
    void (async () => {
      const { data } = await listProjectsApiProjectsGet({ throwOnError: true });
      setProjects(data);
    })();
  }, [projectId]);

  return (
    <select className="top-bar__switcher" value={projectId} onChange={(event) => onSwitch(event.target.value)}>
      {projects.map((project) => (
        <option key={project.id} value={project.id}>
          {project.name}
        </option>
      ))}
    </select>
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
  const { tabs, activeTab, loaded, openTab, closeTab, activateTab } = useTabStack(projectId);
  const [projectName, setProjectName] = useState("");
  const [selection, setSelection] = useState<SelectionState | null>(null);
  const [pendingAsk, setPendingAsk] = useState<PendingAsk | null>(null);
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

  function renderTabContent(tab: TabRef) {
    switch (tab.kind) {
      case "dashboard":
        return <Dashboard projectId={projectId} projectName={projectName} tabs={tabs} onResume={activateTab} />;
      case "library":
        return <LibraryView projectId={projectId} onOpenPaper={openReaderTab} />;
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
        return <SearchResults projectId={projectId} initialResultId={tab.params?.resultId} />;
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

  return (
    <div className="app-frame">
      <header className="top-bar">
        Research Companion OS
        <ProjectSwitcher projectId={projectId} onSwitch={onSwitchProject} />
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
                  {group.items.map((item) => (
                    <li
                      key={item}
                      className="left-nav__item"
                      style={{ cursor: "pointer" }}
                      onClick={() => {
                        if (item === "Dashboard") openTab(DASHBOARD_TAB);
                        if (item === "Papers") openTab(LIBRARY_TAB);
                        if (item === "Notes") openTab(NOTES_TAB);
                        if (item === "Experiments") openTab(EXPERIMENTS_TAB);
                        if (item === "Matrix") openTab(MATRIX_TAB);
                        if (item === "Graph") openTab(GRAPH_TAB);
                        if (item === "Feed") openTab(FEED_TAB);
                        if (item === "Writing") openTab(WRITING_TAB);
                      }}
                    >
                      {item}
                    </li>
                  ))}
                </ul>
              </div>
            ))}
            <div className="left-nav__item" style={{ cursor: "pointer" }} onClick={() => openTab(SEARCH_TAB)}>
              Search
            </div>
            <div className="left-nav__item" style={{ cursor: "pointer" }} onClick={() => openTab(SETTINGS_TAB)}>
              Settings
            </div>
            <div className="left-nav__item" style={{ cursor: "pointer" }} onClick={() => openTab(READINESS_TAB)}>
              Readiness
            </div>
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
            <div className="tab-bar">
              {tabs.map((tab) => (
                <div
                  key={tab.id}
                  className={`tab-bar__tab ${tab.id === activeTab ? "tab-bar__tab--active" : ""}`}
                  onClick={() => activateTab(tab.id)}
                >
                  <span className="tab-bar__label">{tab.label}</span>
                  {tab.kind !== "dashboard" && (
                    <span
                      className="tab-bar__close"
                      onClick={(event) => {
                        event.stopPropagation();
                        closeTab(tab.id);
                      }}
                    >
                      ×
                    </span>
                  )}
                </div>
              ))}
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
