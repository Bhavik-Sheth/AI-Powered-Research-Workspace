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

// sRGB hex, not `oklch(...)`: Cytoscape's canvas renderer only parses
// hex/rgb/hsl/named CSS colours, not the CSS Color 4 `oklch()` function —
// passing it through silently drops every node's fill.
//
// Six hues stepped for the dark card surface (#121212), validated with
// `dataviz/scripts/validate_palette.js --mode dark --surface #121212`:
// all inside the L 0.48–0.67 band, adjacent-pair CVD ΔE 14.1 (deutan) and
// normal-vision ΔE 22.4 — both clean — and every mark ≥ 3:1 on the surface.
//
// Six categorical hues cannot pass the all-pairs colour-blindness check
// (the dataviz cap is three; nothing with six passes). That is legal here
// only because colour is NOT load-bearing: every node type also has a
// distinct shape (`shapeFor` — ellipse / round-rectangle / hexagon /
// diamond), `repo` renders outline-only, and every node is labelled. A
// deuteranope tells idea (diamond) from dataset (round-rectangle) by shape,
// not hue. This is still a large improvement on the previous palette, where
// the two most-confusable hues (blue/purple, ΔE 11.6) were also the ones
// drawn side-by-side in the legend.
//
// LEGEND order is deliberate — blue and purple are kept non-adjacent so the
// adjacent-pair check actually compares them. `colorFor`/`shapeFor` key off
// a category Map, so this array's order only sets the legend's display
// order, never which colour a node gets. Keep the hexes in sync with
// `--graph-node-*` in tokens.css.
export const LEGEND: { category: LegendCategory; label: string; color: string; shape: string }[] = [
  { category: "paper", label: "Paper", color: "#2389e2", shape: "ellipse" },
  { category: "dataset", label: "Dataset", color: "#20a04e", shape: "round-rectangle" },
  { category: "repo", label: "Code / repo", color: "#ba5db3", shape: "round-rectangle" },
  { category: "method", label: "Method / concept", color: "#bd7400", shape: "hexagon" },
  { category: "author", label: "Author", color: "#8f6edb", shape: "ellipse" },
  { category: "idea", label: "Idea / note", color: "#d55753", shape: "diamond" },
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
