import "./AppShell.css";
import { ReadinessStrip } from "./ReadinessStrip";

const NAV_GROUPS: { label: string | null; items: string[] }[] = [
  { label: null, items: ["Dashboard"] },
  { label: "Library", items: ["Papers", "Notes"] },
  { label: "Work", items: ["Experiments", "Writing"] },
  { label: "Discover", items: ["Graph", "Feed", "Matrix"] },
];

/**
 * Top bar + nav shell + routed center pane (MODULES.md App Shell). Phase 1.1
 * ships the shell and the readiness strip only — nav items are not yet
 * routable and the center pane has no real view until Phase 1.2 onward.
 */
export function AppShell() {
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
                  <li key={item} className="left-nav__item">
                    {item}
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </nav>
        <main className="center-pane">
          <h1 className="center-pane__title">Readiness</h1>
          <ReadinessStrip />
        </main>
      </div>
    </div>
  );
}
