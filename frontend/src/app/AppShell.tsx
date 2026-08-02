import { useState } from "react";

import { CompanionPane, type PendingAsk } from "../companion/CompanionPane";
import type { SelectionState } from "../companion/wsTypes";
import { LibraryView } from "../library/LibraryView";
import { ReaderTab } from "../reader/ReaderTab";
import { SearchResults } from "../search/SearchResults";
import "./AppShell.css";
import { ReadinessStrip } from "./ReadinessStrip";

const NAV_GROUPS: { label: string | null; items: string[] }[] = [
  { label: null, items: ["Dashboard"] },
  { label: "Library", items: ["Papers", "Notes"] },
  { label: "Work", items: ["Experiments", "Writing"] },
  { label: "Discover", items: ["Graph", "Feed", "Matrix"] },
];

// TODO(Phase 1.8): replace with the real tab-stack router (App Shell's
// `useTabStack`) — this local switch exists only so each phase's views are
// reachable for manual verification before real routing lands.
type View = { kind: "readiness" } | { kind: "search" } | { kind: "library"; projectId: string } | { kind: "reader"; paperId: string };

const DEMO_PROJECT_ID = "0a436e50-9b2d-4501-9298-22ca11a900a5";

/**
 * Top bar + nav shell + routed center pane (MODULES.md App Shell). Real
 * routing/tab-stack persistence lands in Phase 1.8. The Companion pane sits
 * on every screen (UI_DESIGN.md §3.1/D32) — the reader's selection popover
 * feeds it a question plus the D33 anchor as ambient `ui_state`.
 */
export function AppShell() {
  const [view, setView] = useState<View>({ kind: "readiness" });
  const [selection, setSelection] = useState<SelectionState | null>(null);
  const [pendingAsk, setPendingAsk] = useState<PendingAsk | null>(null);

  function askCompanion(newSelection: SelectionState, question: string) {
    setSelection(newSelection);
    setPendingAsk({ text: question, nonce: Date.now() });
  }

  return (
    <div className="app-frame">
      <header className="top-bar">Research Companion OS</header>
      <div className="shell-body">
        <nav className="left-nav">
          {NAV_GROUPS.map((group) => (
            <div key={group.label ?? "root"}>
              {group.label && <div className="left-nav__group-label">{group.label}</div>}
              <ul style={{ margin: 0, padding: 0 }}>
                {group.items.map((item) => (
                  <li key={item} className="left-nav__item" style={{ cursor: "pointer" }} onClick={() => {
                    if (item === "Papers") setView({ kind: "library", projectId: DEMO_PROJECT_ID });
                  }}>
                    {item}
                  </li>
                ))}
              </ul>
            </div>
          ))}
          <div className="left-nav__item" style={{ cursor: "pointer" }} onClick={() => setView({ kind: "search" })}>
            Search
          </div>
          <div className="left-nav__item" style={{ cursor: "pointer" }} onClick={() => setView({ kind: "readiness" })}>
            Readiness
          </div>
        </nav>
        <main className="center-pane">
          {view.kind === "readiness" && (
            <>
              <h1 className="center-pane__title">Readiness</h1>
              <ReadinessStrip />
            </>
          )}
          {view.kind === "search" && <SearchResults />}
          {view.kind === "library" && (
            <LibraryView projectId={view.projectId} onOpenPaper={(paperId) => setView({ kind: "reader", paperId })} />
          )}
          {view.kind === "reader" && (
            <ReaderTab paperId={view.paperId} projectId={DEMO_PROJECT_ID} onAskCompanion={askCompanion} />
          )}
        </main>
        <CompanionPane projectId={DEMO_PROJECT_ID} selection={selection} pendingAsk={pendingAsk} />
      </div>
    </div>
  );
}
