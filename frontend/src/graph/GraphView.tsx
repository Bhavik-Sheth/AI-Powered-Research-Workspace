import { useEffect, useMemo, useRef, useState } from "react";
import type cytoscape from "cytoscape";
import CytoscapeComponent from "react-cytoscapejs";
import { getProjectGraphApiProjectsProjectIdGraphGet, type GraphEdge } from "@research-os/api-client";

import { ErrorCard } from "../design/ErrorCard";
import "./GraphView.css";
import { categoryOf, colorFor, fillOpacityFor, LEGEND, type LegendCategory, nodesFromEdges, shapeFor } from "./nodeStyle";

// `padding` reserves space around the *node* bounding box, not the label
// text drawn under each node — a node the cose layout places near the
// canvas edge had its centered, up-to-160px-wide label (`text-max-width`
// below) clipped by the container, rendering as an unreadable mid-word
// fragment (e.g. "ion Is All Y…" for "Attention Is All You Need"). 80px
// covers half that label width plus the node's own radius on either side.
//
// `nodeDimensionsIncludeLabels: true` is the other half of the fix: cose's
// own default (`false`, per node_modules/cytoscape/src/extensions/layout/
// cose.mjs) only spaces nodes apart using their 22x22 bounding box and is
// blind to label size entirely, which is why wrapped 3-line labels used to
// overlap their neighbours regardless of repulsion strength. With it on,
// cose treats each node's rendered label as part of its footprint, so
// `nodeRepulsion`/`idealEdgeLength` below only need a modest bump — verified
// against the same source file (`nodeRepulsion`/`idealEdgeLength` accept a
// plain number at runtime via `is.fn(...)`, the TS types just require the
// function form).
const LAYOUT = {
  name: "cose",
  animate: false,
  padding: 80,
  nodeDimensionsIncludeLabels: true,
  componentSpacing: 60,
  nodeRepulsion: () => 6000,
  idealEdgeLength: () => 90,
} as const;

const LABEL_FONT = '10px "Space Grotesk", sans-serif';
const LABEL_MAX_WIDTH = 160;
const LABEL_MAX_LINES = 3;

let measureCtx: CanvasRenderingContext2D | null | undefined;
function getMeasureCtx(): CanvasRenderingContext2D | null {
  if (measureCtx !== undefined) return measureCtx;
  measureCtx = typeof document === "undefined" ? null : document.createElement("canvas").getContext("2d");
  if (measureCtx) measureCtx.font = LABEL_FONT;
  return measureCtx;
}

/** A single token still wider than `maxWidth` on its own line (a slugified
 * concept id like `we-propose-a-new-…` has no whitespace at all, so the
 * word-wrap pass below hands this exactly one "word" covering the whole
 * label) gets hard-broken character by character — the only way to bound
 * its width when there is no natural break point to wrap on. */
function hardBreak(word: string, maxWidth: number, ctx: CanvasRenderingContext2D): string[] {
  const lines: string[] = [];
  let current = "";
  for (const char of word) {
    const candidate = current + char;
    if (current && ctx.measureText(candidate).width > maxWidth) {
      lines.push(current);
      current = char;
    } else {
      current = candidate;
    }
  }
  if (current) lines.push(current);
  return lines;
}

function wrapLines(text: string, maxWidth: number, ctx: CanvasRenderingContext2D): string[] {
  // Break on whitespace *and* after a hyphen (keeping the hyphen with the
  // preceding chunk, the usual hyphenation convention) — a slugified label
  // has hyphens as its only natural break points, no whitespace at all.
  const words = text.split(/(?<=-)|\s+/).filter(Boolean);
  const lines: string[] = [];
  let current = "";
  for (const word of words) {
    const candidate = current ? `${current}${current.endsWith("-") ? "" : " "}${word}` : word;
    if (current && ctx.measureText(candidate).width > maxWidth) {
      lines.push(current);
      current = word;
    } else {
      current = candidate;
    }
    if (!current.endsWith("-") && ctx.measureText(current).width > maxWidth) {
      // Even a single chunk (or the word that just started a fresh line)
      // overflows on its own — hard-break it rather than leaving one
      // unbounded line.
      const broken = hardBreak(current, maxWidth, ctx);
      lines.push(...broken.slice(0, -1));
      current = broken[broken.length - 1];
    }
  }
  if (current) lines.push(current);
  return lines;
}

/**
 * Cytoscape's `text-wrap: 'wrap'` (verified against
 * node_modules/cytoscape/src/style/properties.mjs, v3.34.0) wraps a label to
 * fit `text-max-width` but has no `text-max-lines` counterpart — confirmed
 * absent from that same properties list — so an unbounded label just kept
 * growing taller, which is what made wrapped labels collide with neighbours.
 * This wraps the label ourselves with the same width budget the stylesheet
 * uses, then caps it at 3 lines with an ellipsis, so the layout can reserve
 * a bounded amount of vertical space per node.
 */
function wrapAndCapLabel(text: string, maxWidth: number = LABEL_MAX_WIDTH, maxLines: number = LABEL_MAX_LINES): string {
  const ctx = getMeasureCtx();
  if (!ctx) return text;
  const allLines = wrapLines(text, maxWidth, ctx);
  if (allLines.length <= maxLines) return allLines.join("\n");

  const visible = allLines.slice(0, maxLines);
  let lastLine = visible[maxLines - 1];
  while (lastLine.length > 0 && ctx.measureText(`${lastLine}…`).width > maxWidth) {
    lastLine = lastLine.slice(0, -1).trimEnd();
  }
  visible[maxLines - 1] = `${lastLine}…`;
  return visible.join("\n");
}

function elementsFor(edges: GraphEdge[], paperTitles: Record<string, string>) {
  const nodes = nodesFromEdges(edges).map((node) => {
    const fullLabel = paperTitles[node.label] ?? node.label;
    return {
      data: { id: node.id, rawId: node.label, label: wrapAndCapLabel(fullLabel), fullLabel, nodeType: node.nodeType },
    };
  });
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
      "font-family": '"Geist", sans-serif',
      color: "#dedede",
      "text-valign": "bottom",
      "text-margin-y": 4,
      // The label text itself is already wrapped-and-capped to 3 lines by
      // `wrapAndCapLabel` above; `text-wrap`/`text-max-width` here are a
      // safety net in case the browser's actual glyph metrics differ
      // slightly from the offscreen canvas measurement.
      "text-wrap": "wrap",
      "text-max-width": "160px",
      width: 22,
      height: 22,
    },
  },
  {
    selector: "node:selected",
    style: { "border-width": 3, "border-color": "#ff6c02" },
  },
  {
    selector: "edge",
    style: {
      width: 1.5,
      "line-color": "#5c5c5c",
      "target-arrow-color": "#5c5c5c",
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
  const [tooltip, setTooltip] = useState<{ x: number; y: number; text: string } | null>(null);
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
            // Native Cytoscape pointer events (verified against
            // node_modules/cytoscape/src/extensions/renderer/base/
            // load-listeners.mjs, which emits `mouseover`/`mouseout` on
            // elements) rather than hand-rolled DOM listeners — shows the
            // complete, un-truncated title near the cursor since the
            // on-canvas label is wrapped-and-capped at 3 lines.
            cy.off("mouseover", "node");
            cy.off("mouseout", "node");
            cy.off("mousemove", "node");
            cy.on("mouseover", "node", (event) => {
              const node = event.target;
              const pos = node.renderedPosition();
              setTooltip({ x: pos.x, y: pos.y, text: node.data("fullLabel") ?? node.data("label") });
            });
            cy.on("mousemove", "node", (event) => {
              const node = event.target;
              const pos = node.renderedPosition();
              setTooltip({ x: pos.x, y: pos.y, text: node.data("fullLabel") ?? node.data("label") });
            });
            cy.on("mouseout", "node", () => setTooltip(null));
          }}
        />

        {tooltip && (
          <div className="graph__tooltip" style={{ left: tooltip.x, top: tooltip.y }}>
            {tooltip.text}
          </div>
        )}

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
