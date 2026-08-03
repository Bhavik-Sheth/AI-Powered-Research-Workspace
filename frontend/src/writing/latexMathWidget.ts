import type { Range } from "@codemirror/state";
import { Decoration, type DecorationSet, EditorView, ViewPlugin, type ViewUpdate, WidgetType } from "@codemirror/view";
import katex from "katex";
import "katex/dist/katex.min.css";

// Live inline math (D34, UI_DESIGN.md §4.8) — a KaTeX render appended right
// after each `$...$`/`$$...$$` span, updated as the document changes. This
// is not a hide-the-source WYSIWYG replacement (Overleaf-style full math
// rendering hides the raw text); the source stays visible and editable,
// with the rendered symbol as a companion widget, which is what "live
// inline math" in a plain-text LaTeX editor means in practice.
const INLINE_MATH = /\$([^$\n]+)\$/g;
const BLOCK_MATH = /\$\$([^$]+)\$\$/g;

class MathWidget extends WidgetType {
  constructor(
    private readonly source: string,
    private readonly displayMode: boolean,
  ) {
    super();
  }

  eq(other: MathWidget): boolean {
    return other.source === this.source && other.displayMode === this.displayMode;
  }

  toDOM(): HTMLElement {
    const span = document.createElement("span");
    span.className = "latex-math-widget";
    try {
      katex.render(this.source, span, { displayMode: this.displayMode, throwOnError: false });
    } catch {
      span.textContent = this.source;
    }
    return span;
  }
}

function buildDecorations(view: EditorView): DecorationSet {
  const widgets: Range<Decoration>[] = [];
  for (const { from, to } of view.visibleRanges) {
    const text = view.state.doc.sliceString(from, to);
    const consumed = new Set<number>();
    for (const match of text.matchAll(BLOCK_MATH)) {
      const start = from + (match.index ?? 0);
      const end = start + match[0].length;
      for (let i = start; i < end; i++) consumed.add(i);
      widgets.push(Decoration.widget({ widget: new MathWidget(match[1], true), side: 1 }).range(end));
    }
    for (const match of text.matchAll(INLINE_MATH)) {
      const start = from + (match.index ?? 0);
      const end = start + match[0].length;
      if (consumed.has(start)) continue;
      widgets.push(Decoration.widget({ widget: new MathWidget(match[1], false), side: 1 }).range(end));
    }
  }
  return Decoration.set(
    widgets.sort((a, b) => a.from - b.from),
    true,
  );
}

export const latexMathWidgets = ViewPlugin.fromClass(
  class {
    decorations: DecorationSet;
    constructor(view: EditorView) {
      this.decorations = buildDecorations(view);
    }
    update(update: ViewUpdate) {
      if (update.docChanged || update.viewportChanged) this.decorations = buildDecorations(update.view);
    }
  },
  { decorations: (plugin) => plugin.decorations },
);
