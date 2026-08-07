/** Hand-written WS wire types (TRD §4.1) — the WebSocket surface is not part
 * of the OpenAPI schema `packages/api-client` generates from, so these are
 * the one place they're declared, matching `backend/harness/models.py` /
 * `backend/ws/__init__.py` field for field (Rules.md: names match the wire
 * shape). `Citation`'s two variants are the one exception — that same shape
 * is also `MessageOut.citations` on the REST conversation-history response,
 * so it's imported from the generated client rather than redeclared here
 * (Rules.md: never hand-declare a type the generated client already
 * exports).
 */

import type { AnchorCitation, MemoryCitation } from "@research-os/api-client";

export type Citation = AnchorCitation | MemoryCitation;

export interface AnchorHint {
  page: number;
  bbox: [number, number, number, number];
}

export interface QuoteAnchorInput {
  quote: string;
  prefix: string;
  suffix: string;
  hint?: AnchorHint | null;
}

export interface SelectionState {
  paper_id: string;
  anchor: QuoteAnchorInput;
}

export interface UIState {
  selection?: SelectionState | null;
  open_paper_ids?: string[];
}

export type DownstreamEvent =
  | { event: "status"; text: string }
  | { event: "text_delta"; delta: string }
  | { event: "tool_call"; tool_name: string; args: Record<string, unknown> }
  | { event: "tool_result"; tool_name: string; model_view: string; result_id: string | null }
  | { event: "ui_action"; action: string; payload: Record<string, unknown> }
  | { event: "turn_complete"; turn_id: string; interrupted: boolean; iterations: number; citations: Citation[] }
  | { event: "error"; code: string; message: string; recoverable: boolean; what_still_worked: string | null };

export type UpstreamEvent =
  | { event: "user_message"; text: string; ui_state: UIState }
  | { event: "ui_state"; selection?: SelectionState | null }
  | { event: "interrupt" };
