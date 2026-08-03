import { useEffect, useMemo, useRef, useState } from "react";
import type cytoscape from "cytoscape";
import CytoscapeComponent from "react-cytoscapejs";
import { getProjectGraphApiProjectsProjectIdGraphGet, type GraphEdge } from "@research-os/api-client";

import "./GraphView.css";
import { categoryOf, colorFor, fillOpacityFor, LEGEND, nodesFromEdges, shapeFor } from "./nodeStyle";

const LAYOUT = { name: "cose", animate: false, padding: 40 } as const;

function elementsFor(edges: GraphEdge[]) {
  const nodes = nodesFromEdges(edges).map((node) => ({
    data: { id: node.id, label: node.label, nodeType: node.nodeType },
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
      "font-family": "var(--font-ui)",
      color: "var(--text-strong)",
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
    style: { "border-width": 3, "border-color": "var(--accent)" },
  },
  {
    selector: "edge",
    style: {
      width: 1.5,
      "line-color": "var(--graph-edge)",
      "target-arrow-color": "var(--graph-edge)",
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
  const [activeTypes, setActiveTypes] = useState<Set<string> | null>(null);
  const [selected, setSelected] = useState<{ id: string; nodeType: string; label: string; edges: GraphEdge[] } | null>(null);
  const cyRef = useRef<cytoscape.Core | null>(null);

  useEffect(() => {
    void (async () => {
      const { data } = await getProjectGraphApiProjectsProjectIdGraphGet({ path: { project_id: projectId }, throwOnError: true });
      setEdges(data.edges);
      setPaperIds(data.paper_ids ?? {});
    })();
  }, [projectId]);

  const nodeTypesPresent = useMemo(() => (edges ? [...new Set(nodesFromEdges(edges).map((n) => n.nodeType))] : []), [edges]);
  const visibleEdges = useMemo(() => {
    if (!edges) return [];
    if (!activeTypes) return edges;
    return edges.filter((edge) => activeTypes.has(edge.src_type) && activeTypes.has(edge.dst_type));
  }, [edges, activeTypes]);

  function toggleType(nodeType: string) {
    setActiveTypes((prev) => {
      const base = prev ?? new Set(nodeTypesPresent);
      const next = new Set(base);
      if (next.has(nodeType)) next.delete(nodeType);
      else next.add(nodeType);
      return next;
    });
  }

  function handleNodeSelect(id: string, nodeType: string, label: string) {
    const nodeEdges = (edges ?? []).filter(
      (edge) => `${edge.src_type}:${edge.src_id}` === id || `${edge.dst_type}:${edge.dst_id}` === id,
    );
    setSelected({ id, nodeType, label, edges: nodeEdges });
  }

  if (edges === null) return <p>Loading…</p>;

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
          {nodeTypesPresent.map((nodeType) => {
            const on = activeTypes === null || activeTypes.has(nodeType);
            return (
              <button
                key={nodeType}
                type="button"
                className={`graph__chip ${on ? "graph__chip--on" : ""}`}
                style={{ borderColor: on ? colorFor(nodeType) : undefined }}
                onClick={() => toggleType(nodeType)}
              >
                <span className="graph__chip-dot" style={{ background: colorFor(nodeType) }} />
                {nodeType}
              </button>
            );
          })}
        </div>
      </div>

      <div className="graph__canvas-wrap">
        <CytoscapeComponent
          elements={elementsFor(visibleEdges)}
          style={{ width: "100%", height: "100%" }}
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
          stylesheet={STYLESHEET as any}
          layout={LAYOUT}
          cy={(cy) => {
            cyRef.current = cy;
            cy.off("tap", "node");
            cy.on("tap", "node", (event) => {
              const node = event.target;
              handleNodeSelect(node.id(), node.data("nodeType"), node.data("label"));
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
            <button type="button" className="graph__detail-close" onClick={() => setSelected(null)}>
              ×
            </button>
            <span className="graph__chip-dot" style={{ background: colorFor(selected.nodeType) }} />
            <span className="graph__detail-type">{selected.nodeType}</span>
            <h3>{selected.label}</h3>
            <p className="graph__detail-summary">{selected.edges.length} connection(s)</p>
            <div className="graph__detail-edges">
              {selected.edges.map((edge) => (
                <div key={edge.id} className="graph__detail-edge">
                  <span>{edge.src_id === selected.label ? `→ ${edge.relation} → ${edge.dst_id}` : `${edge.src_id} → ${edge.relation} →`}</span>
                  <span className="graph__detail-provenance">({edge.provenance === "llm" ? "inferred" : edge.provenance})</span>
                </div>
              ))}
            </div>
            {selected.nodeType === "paper" && paperIds[selected.label] && (
              <button type="button" className="graph__detail-open" onClick={() => onOpenPaper(paperIds[selected.label], selected.label)}>
                Open
              </button>
            )}
          </aside>
        )}
      </div>
    </div>
  );
}
