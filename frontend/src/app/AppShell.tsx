import { useEffect, useState } from "react";
import { listProjectsApiProjectsGet, type ProjectResponse } from "@research-os/api-client";

import { CompanionPane, type PendingAsk } from "../companion/CompanionPane";
import type { SelectionState } from "../companion/wsTypes";
import { Dashboard } from "../dashboard/Dashboard";
import { ExperimentsBoard } from "../experiments/ExperimentsBoard";
import { LibraryView } from "../library/LibraryView";
import { NotesView } from "../notes/NotesView";
import { ReaderTab } from "../reader/ReaderTab";
import { SearchResults } from "../search/SearchResults";
import { useCollapsible } from "../state/useCollapsible";
import { useProjectSocket } from "../state/useProjectSocket";
import { type TabRef, useTabStack } from "../state/useTabStack";
import "./AppShell.css";
import { ReadinessStrip } from "./ReadinessStrip";

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
const READINESS_TAB: TabRef = { id: "readiness", kind: "readiness", params: {}, label: "Readiness" };

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
      case "reader":
        return <ReaderTab paperId={tab.params?.paperId ?? ""} projectId={projectId} onAskCompanion={askCompanion} />;
      case "search":
        return <SearchResults projectId={projectId} />;
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
        <div className={`pane-shell pane-shell--left ${navCollapsed ? "pane-shell--collapsed" : ""}`}>
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
            <div className="left-nav__item" style={{ cursor: "pointer" }} onClick={() => openTab(READINESS_TAB)}>
              Readiness
            </div>
          </nav>
        </div>
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
        <div className={`pane-shell pane-shell--right ${companionCollapsed ? "pane-shell--collapsed" : ""}`}>
          <CompanionPane projectId={projectId} selection={selection} pendingAsk={pendingAsk} socket={socket} />
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
