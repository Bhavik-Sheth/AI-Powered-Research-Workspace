import type { ReactNode } from "react";

/**
 * Splits an assistant message on the `<cite>` / `<unverified>` tags the
 * harness emits (D24) into renderable pieces — cited evidence gets its own
 * visually distinct block, an unverified claim gets the same block plus a
 * `⚠ unverified` badge (UI_DESIGN.md §3.1), reasoning stays plain text.
 */
const TAG_PATTERN = /<(cite|unverified)>(.*?)<\/\1>/gs;

/**
 * Inline markdown within one already-split segment — bold, italic, inline
 * code. No stdlib formatter exists for this, and the workspace has no
 * markdown dependency (`frontend/package.json`) to reach for at resolution
 * step 3; step 4 (e.g. `react-markdown`) would add a new dependency, which
 * Rules.md "Ask first" gates on approval, so this is the scoped custom
 * parser at resolution step 5. Underscore emphasis (`_like_this_`) is
 * deliberately not recognised — this app's text is full of snake_case
 * identifiers (`gte_modernbert_base`) that would otherwise be mangled.
 * Applied per segment, so it can never read past a `<cite>`/`<unverified>`
 * boundary — that split happens first, in `renderAssistantContent`, and
 * this only ever sees the text already on one side of it.
 */
const INLINE_PATTERN = /\*\*(.+?)\*\*|`(.+?)`|\*(.+?)\*/g;

function renderInline(text: string, keyPrefix: string): ReactNode[] {
  const nodes: ReactNode[] = [];
  let cursor = 0;
  let match: RegExpExecArray | null;
  let index = 0;

  INLINE_PATTERN.lastIndex = 0;
  while ((match = INLINE_PATTERN.exec(text)) !== null) {
    if (match.index > cursor) {
      nodes.push(text.slice(cursor, match.index));
    }
    const [, bold, code, italic] = match;
    const key = `${keyPrefix}-${index++}`;
    if (bold !== undefined) {
      nodes.push(<strong key={key}>{bold}</strong>);
    } else if (code !== undefined) {
      nodes.push(
        <code className="companion__code" key={key}>
          {code}
        </code>,
      );
    } else {
      nodes.push(<em key={key}>{italic}</em>);
    }
    cursor = match.index + match[0].length;
  }
  if (cursor < text.length) {
    nodes.push(text.slice(cursor));
  }
  return nodes;
}

export function renderAssistantContent(text: string): ReactNode[] {
  const pieces: ReactNode[] = [];
  let cursor = 0;
  let citeIndex = 0;
  let match: RegExpExecArray | null;

  TAG_PATTERN.lastIndex = 0;
  while ((match = TAG_PATTERN.exec(text)) !== null) {
    if (match.index > cursor) {
      pieces.push(...renderInline(text.slice(cursor, match.index), `plain-${match.index}`));
    }
    const [, kind, quote] = match;
    if (kind === "cite") {
      citeIndex += 1;
      pieces.push(
        <span className="companion__cite" key={match.index}>
          {renderInline(quote, `cite-${match.index}`)}
          <sup className="companion__cite-index">[{citeIndex}]</sup>
        </span>,
      );
    } else {
      pieces.push(
        <span className="companion__unverified" key={match.index}>
          {renderInline(quote, `unverified-${match.index}`)}
          <span className="companion__unverified-badge">⚠ unverified</span>
        </span>,
      );
    }
    cursor = match.index + match[0].length;
  }
  if (cursor < text.length) {
    pieces.push(...renderInline(text.slice(cursor), `plain-${cursor}`));
  }
  return pieces;
}
