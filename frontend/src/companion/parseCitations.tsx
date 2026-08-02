import type { ReactNode } from "react";

/**
 * Splits an assistant message on the `<cite>` / `<unverified>` tags the
 * harness emits (D24) into renderable pieces — cited evidence gets its own
 * visually distinct block, an unverified claim gets the same block plus a
 * `⚠ unverified` badge (UI_DESIGN.md §3.1), reasoning stays plain text.
 */
const TAG_PATTERN = /<(cite|unverified)>(.*?)<\/\1>/gs;

export function renderAssistantContent(text: string): ReactNode[] {
  const pieces: ReactNode[] = [];
  let cursor = 0;
  let citeIndex = 0;
  let match: RegExpExecArray | null;

  TAG_PATTERN.lastIndex = 0;
  while ((match = TAG_PATTERN.exec(text)) !== null) {
    if (match.index > cursor) {
      pieces.push(text.slice(cursor, match.index));
    }
    const [, kind, quote] = match;
    if (kind === "cite") {
      citeIndex += 1;
      pieces.push(
        <span className="companion__cite" key={match.index}>
          {quote}
          <sup className="companion__cite-index">[{citeIndex}]</sup>
        </span>,
      );
    } else {
      pieces.push(
        <span className="companion__unverified" key={match.index}>
          {quote}
          <span className="companion__unverified-badge">⚠ unverified</span>
        </span>,
      );
    }
    cursor = match.index + match[0].length;
  }
  if (cursor < text.length) {
    pieces.push(text.slice(cursor));
  }
  return pieces;
}
