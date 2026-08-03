import type { Range } from "@codemirror/state";
import { StateEffect, StateField } from "@codemirror/state";
import { Decoration, type DecorationSet, EditorView } from "@codemirror/view";
import type { CitationFinding } from "@research-os/api-client";

// Inline citation treatment (D34, UI_DESIGN.md §4.8): every `\cite{...}`
// gets the evidence tint as soon as it's typed — no check needed, it's
// just "this is a citation". `unsupported`/`missing` findings only appear
// after an explicit "Check citations" run and are marked on top.
const CITE = /\\cite\{[^}]*\}/g;

export const setCitationFindingsEffect = StateEffect.define<CitationFinding[]>();

function buildDecorations(text: string, findings: CitationFinding[]): DecorationSet {
  const marks: Range<Decoration>[] = [];
  for (const match of text.matchAll(CITE)) {
    const start = match.index ?? 0;
    marks.push(Decoration.mark({ class: "citation-evidence" }).range(start, start + match[0].length));
  }
  for (const finding of findings) {
    if (!finding.span) continue;
    const cls = finding.kind === "missing" ? "citation-missing" : "citation-unsupported";
    const title = finding.note ?? "unsupported claim — no linked source yet";
    let idx = text.indexOf(finding.span);
    while (idx !== -1) {
      marks.push(Decoration.mark({ class: cls, attributes: { title } }).range(idx, idx + finding.span.length));
      idx = text.indexOf(finding.span, idx + 1);
    }
  }
  marks.sort((a, b) => a.from - b.from || a.to - b.to);
  return Decoration.set(marks, true);
}

const citationFindingsField = StateField.define<CitationFinding[]>({
  create: () => [],
  update(value, tr) {
    for (const effect of tr.effects) if (effect.is(setCitationFindingsEffect)) return effect.value;
    return value;
  },
});

const citationDecorationField = StateField.define<DecorationSet>({
  create: (state) => buildDecorations(state.doc.toString(), state.field(citationFindingsField)),
  update(deco, tr) {
    if (tr.docChanged || tr.effects.some((e) => e.is(setCitationFindingsEffect))) {
      return buildDecorations(tr.state.doc.toString(), tr.state.field(citationFindingsField));
    }
    return deco.map(tr.changes);
  },
  provide: (field) => EditorView.decorations.from(field),
});

export const citationDecorations = [citationFindingsField, citationDecorationField];

export function setCitationFindings(view: EditorView | null, findings: CitationFinding[]) {
  view?.dispatch({ effects: setCitationFindingsEffect.of(findings) });
}
