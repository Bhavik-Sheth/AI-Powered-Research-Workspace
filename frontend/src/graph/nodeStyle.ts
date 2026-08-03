import type { GraphEdge } from "@research-os/api-client";

/**
 * The categorical node palette (UI_DESIGN.md §1/§4.7) — literal values
 * mirroring `frontend/src/design/tokens.css`'s `--graph-node-*` custom
 * properties, duplicated here because Cytoscape's canvas renderer cannot
 * resolve a CSS custom property. `Schema.md`'s wider node-type vocabulary
 * (`topic`, `experiment`, `highlight`, …) folds into the nearest of the six
 * documented categories rather than inventing new ones.
 */
export type LegendCategory = "paper" | "author" | "dataset" | "method" | "repo" | "idea";

const CATEGORY_BY_NODE_TYPE: Record<string, LegendCategory> = {
  paper: "paper",
  author: "author",
  dataset: "dataset",
  method: "method",
  concept: "method",
  topic: "method",
  repo: "repo",
  note: "idea",
  experiment: "idea",
  highlight: "idea",
};

export function categoryOf(nodeType: string): LegendCategory {
  return CATEGORY_BY_NODE_TYPE[nodeType] ?? "idea";
}

// sRGB hex, not the `oklch(...)` UI_DESIGN.md §1 gives literally: Cytoscape's
// canvas renderer only parses hex/rgb/hsl/named CSS colours, not the CSS
// Color 4 `oklch()` function — passing it through silently drops every
// node's fill (canvas logs "invalid style property" and falls back to
// black). These are the browser's own gamut-mapped conversion of each
// oklch() value, computed once via `canvas.fillStyle` + `getImageData`.
export const LEGEND: { category: LegendCategory; label: string; color: string; shape: string }[] = [
  { category: "paper", label: "Paper", color: "#0089df", shape: "ellipse" },
  { category: "author", label: "Author", color: "#736bc9", shape: "ellipse" },
  { category: "dataset", label: "Dataset", color: "#2a914b", shape: "round-rectangle" },
  { category: "method", label: "Method / concept", color: "#b37903", shape: "hexagon" },
  { category: "repo", label: "Code / repo", color: "#aa55a4", shape: "round-rectangle" },
  { category: "idea", label: "Idea / note", color: "#bf534e", shape: "diamond" },
];

const LEGEND_BY_CATEGORY = new Map(LEGEND.map((entry) => [entry.category, entry]));

export function colorFor(nodeType: string): string {
  return LEGEND_BY_CATEGORY.get(categoryOf(nodeType))!.color;
}

export function shapeFor(nodeType: string): string {
  return LEGEND_BY_CATEGORY.get(categoryOf(nodeType))!.shape;
}

/** `repo` nodes render outline-only, no fill (UI_DESIGN.md §1). */
export function fillOpacityFor(nodeType: string): number {
  return categoryOf(nodeType) === "repo" ? 0 : 1;
}

export interface GraphNode {
  id: string;
  nodeType: string;
  label: string;
}

/** Every distinct `(type, id)` endpoint touched by `edges` — nodes are never
 * fetched separately; they're implied by the edge set (Schema.md: "a
 * concept node has no table by design"). */
export function nodesFromEdges(edges: GraphEdge[]): GraphNode[] {
  const seen = new Map<string, GraphNode>();
  for (const edge of edges) {
    const srcKey = `${edge.src_type}:${edge.src_id}`;
    const dstKey = `${edge.dst_type}:${edge.dst_id}`;
    if (!seen.has(srcKey)) seen.set(srcKey, { id: srcKey, nodeType: edge.src_type, label: edge.src_id });
    if (!seen.has(dstKey)) seen.set(dstKey, { id: dstKey, nodeType: edge.dst_type, label: edge.dst_id });
  }
  return [...seen.values()];
}
