import { useEffect, useMemo, useRef, useState } from "react";
import type cytoscape from "cytoscape";
import CytoscapeComponent from "react-cytoscapejs";
import { getProjectGraphApiProjectsProjectIdGraphGet, type GraphEdge } from "@research-os/api-client";

import { ErrorCard } from "../design/ErrorCard";
import "./GraphView.css";
import { categoryOf, colorFor, fillOpacityFor, LEGEND, type LegendCategory, nodesFromEdges, shapeFor } from "./nodeStyle";

// `padding` reserves space around the *node* bounding box, not the label
// text drawn under each node — a node the cose layout places near the
// canvas edge had its centered, up-to-90px-wide label (`text-max-width`
// below) clipped by the container before the label's own ellipsis ever
// kicked in, rendering as an unreadable mid-word fragment (e.g. "ion Is
// All Y…" for "Attention Is All You Need"). 80px covers half that label
// width plus the node's own radius on either side.
const LAYOUT = { name: "cose", animate: false, padding: 80 } as const;

function elementsFor(edges: GraphEdge[], paperTitles: Record<string, string>) {
  const nodes = nodesFromEdges(edges).map((node) => ({
    data: { id: node.id, rawId: node.label, label: paperTitles[node.label] ?? node.label, nodeType: node.nodeType },
  }));
  const cyEdges = edges.map((edge) => ({
    data: {
      id: edge.id,
      source: `${edge.src_type}:${edge.src_id}`,
      target: `${edge.dst_type}:${edge.dst_id}`,
      relation: edge.relation,
      provenance: edge.provenance,
    },
  }));
  return CytoscapeComponent.normalizeElements([...nodes, ...cyEdges]);
}

const STYLESHEET = [
  {
    selector: "node",
    style: {
      "background-color": (el: cytoscape.NodeSingular) => colorFor(el.data("nodeType")),
      "background-opacity": (el: cytoscape.NodeSingular) => fillOpacityFor(el.data("nodeType")),
      shape: (el: cytoscape.NodeSingular) => shapeFor(el.data("nodeType")),
      "border-width": (el: cytoscape.NodeSingular) => (categoryOf(el.data("nodeType")) === "repo" ? 1.5 : 1),
      "border-style": (el: cytoscape.NodeSingular) => (categoryOf(el.data("nodeType")) === "repo" ? "dashed" : "solid"),
      "border-color": (el: cytoscape.NodeSingular) => colorFor(el.data("nodeType")),
      label: "data(label)",
      "font-size": 10,
      // Literal values, not `var(--font-ui)` / `var(--text-strong)`: same
      // reason as the LEGEND colours above — Cytoscape's canvas stylesheet
      // cannot resolve a CSS custom property at all.
      "font-family": '"Space Grotesk", sans-serif',
      color: "#001018",
      "text-valign": "bottom",
      "text-margin-y": 4,
      "text-wrap": "ellipsis",
      "text-max-width": "90px",
      width: 22,
      height: 22,
    },
  },
  {
    selector: "node:selected",
    style: { "border-width": 3, "border-color": "#0089df" },
  },
  {
    selector: "edge",
    style: {
      width: 1.5,
      "line-color": "#7aa7b0",
      "target-arrow-color": "#7aa7b0",
      "target-arrow-shape": "triangle",
      "curve-style": "bezier",
      "line-style": (el: cytoscape.EdgeSingular) => (el.data("provenance") === "llm" ? "dashed" : "solid"),
      "line-dash-pattern": [5, 4],
    },
  },
];

/** Graph View (MODULES.md) — the project-scoped knowledge graph, node type
 * encoded by colour and shape, edge provenance by dash (US11). */
export function GraphView({ projectId, onOpenPaper }: { projectId: string; onOpenPaper: (paperId: string, title: string) => void }) {
  const [edges, setEdges] = useState<GraphEdge[] | null>(null);
  const [paperIds, setPaperIds] = useState<Record<string, string>>({});
  const [paperTitles, setPaperTitles] = useState<Record<string, string>>({});
  // Filter chips key off the 6 canonical categories `nodeStyle.ts` already
  // defines (§4.7/§1), not the raw `nodeType` strings the graph endpoint
  // returns — Schema.md's wider vocabulary (`topic`, `experiment`,
  // `highlight`, …) folds into those 6 via `categoryOf`, so the filter row
  // never grows past what the legend documents.
  const [activeCategories, setActiveCategories] = useState<Set<LegendCategory> | null>(null);
  const [selected, setSelected] = useState<{ id: string; rawId: string; nodeType: string; label: string; edges: GraphEdge[] } | null>(
    null,
  );
  const [error, setError] = useState<string | null>(null);
  const cyRef = useRef<cytoscape.Core | null>(null);

  async function load() {
    setError(null);
    try {
      const { data } = await getProjectGraphApiProjectsProjectIdGraphGet({ path: { project_id: projectId }, throwOnError: true });
      setEdges(data.edges);
      setPaperIds(data.paper_ids ?? {});
      setPaperTitles(data.paper_titles ?? {});
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load the graph");
    }
  }

  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId]);

  // Legend order, filtered to the categories actually present — the
  // `nodeStyle.ts` legend entries are the sanctioned 6, so this is never a
  // raw enumeration of whatever `nodeType` strings happen to be in the data.
  const categoriesPresent = useMemo(() => {
    if (!edges) return [];
    const present = new Set(nodesFromEdges(edges).map((n) => categoryOf(n.nodeType)));
    return LEGEND.filter((entry) => present.has(entry.category));
  }, [edges]);
  const visibleEdges = useMemo(() => {
    if (!edges) return [];
    if (!activeCategories) return edges;
    return edges.filter(
      (edge) => activeCategories.has(categoryOf(edge.src_type)) && activeCategories.has(categoryOf(edge.dst_type)),
    );
  }, [edges, activeCategories]);

  function toggleCategory(category: LegendCategory) {
    setActiveCategories((prev) => {
      const base = prev ?? new Set(categoriesPresent.map((entry) => entry.category));
      const next = new Set(base);
      if (next.has(category)) next.delete(category);
      else next.add(category);
      return next;
    });
  }

  function handleNodeSelect(id: string, nodeType: string, rawId: string) {
    const nodeEdges = (edges ?? []).filter(
      (edge) => `${edge.src_type}:${edge.src_id}` === id || `${edge.dst_type}:${edge.dst_id}` === id,
    );
    const label = nodeType === "paper" ? (paperTitles[rawId] ?? rawId) : rawId;
    setSelected({ id, rawId, nodeType, label, edges: nodeEdges });
  }

  if (edges === null) {
    if (error) return <ErrorCard title="Could not load the graph" message={error} onRetry={load} />;
    return <p>Loading…</p>;
  }

  if (edges.length === 0) {
    return (
      <div className="graph__empty">
        <p>No graph yet — edges appear once papers in this project have metadata (cites, datasets, code) or have been opened.</p>
      </div>
    );
  }

  return (
    <div className="graph">
      <div className="graph__header">
        <h2>Knowledge Graph</h2>
        <div className="graph__filters">
          {categoriesPresent.map((entry) => {
            const on = activeCategories === null || activeCategories.has(entry.category);
            return (
              <button
                key={entry.category}
                type="button"
                className={`graph__chip ${on ? "graph__chip--on" : ""}`}
                style={{ borderColor: on ? entry.color : undefined }}
                onClick={() => toggleCategory(entry.category)}
              >
                <span className="graph__chip-dot" style={{ background: entry.color }} />
                {entry.label}
              </button>
            );
          })}
        </div>
      </div>

      <div className="graph__canvas-wrap">
        <CytoscapeComponent
          elements={elementsFor(visibleEdges, paperTitles)}
          style={{ width: "100%", height: "100%" }}
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
          stylesheet={STYLESHEET as any}
          layout={LAYOUT}
          cy={(cy) => {
            cyRef.current = cy;
            cy.off("tap", "node");
            cy.on("tap", "node", (event) => {
              const node = event.target;
              handleNodeSelect(node.id(), node.data("nodeType"), node.data("rawId"));
            });
          }}
        />

        <div className="graph__legend">
          <h4>Legend</h4>
          {LEGEND.map((entry) => (
            <div key={entry.category} className="graph__legend-row">
              <span className={`graph__legend-shape graph__legend-shape--${entry.shape}`} style={{ background: entry.color }} />
              {entry.label}
            </div>
          ))}
          <div className="graph__legend-divider" />
          <div className="graph__legend-row">
            <span className="graph__legend-line graph__legend-line--solid" /> from metadata
          </div>
          <div className="graph__legend-row">
            <span className="graph__legend-line graph__legend-line--dashed" /> LLM-inferred
          </div>
        </div>

        {selected && (
          <aside className="graph__detail">
            <button type="button" className="graph__detail-close" aria-label="Close details" onClick={() => setSelected(null)}>
              ×
            </button>
            <span className="graph__chip-dot" style={{ background: colorFor(selected.nodeType) }} />
            <span className="graph__detail-type">{selected.nodeType}</span>
            <h3>{selected.label}</h3>
            <p className="graph__detail-summary">{selected.edges.length} connection(s)</p>
            <div className="graph__detail-edges">
              {selected.edges.map((edge) => (
                <div key={edge.id} className="graph__detail-edge">
                  <span>{edge.src_id === selected.rawId ? `→ ${edge.relation} → ${edge.dst_id}` : `${edge.src_id} → ${edge.relation} →`}</span>
                  <span className="graph__detail-provenance">({edge.provenance === "llm" ? "inferred" : edge.provenance})</span>
                </div>
              ))}
            </div>
            {selected.nodeType === "paper" && paperIds[selected.rawId] && (
              <button type="button" className="graph__detail-open" onClick={() => onOpenPaper(paperIds[selected.rawId], selected.label)}>
                Open
              </button>
            )}
          </aside>
        )}
      </div>
    </div>
  );
}
