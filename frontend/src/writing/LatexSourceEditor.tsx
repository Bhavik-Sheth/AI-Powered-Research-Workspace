import { autocompletion } from "@codemirror/autocomplete";
import { defaultKeymap, history, historyKeymap } from "@codemirror/commands";
import { StreamLanguage } from "@codemirror/language";
import { stex } from "@codemirror/legacy-modes/mode/stex";
import { EditorState } from "@codemirror/state";
import { EditorView, keymap, lineNumbers } from "@codemirror/view";
import { type MutableRefObject, useEffect, useRef } from "react";

import { citeCompletionSource } from "./citeAutocomplete";
import { citationDecorations } from "./citationDecorations";
import { latexMathWidgets } from "./latexMathWidget";

// The editor sits on the dark `--surface-raised` pane, so it needs a dark
// theme rather than CodeMirror's light default. Colours come from the design
// tokens (tokens.css) so the source pane stays in step with the rest of the
// app; `{ dark: true }` flips CodeMirror's own built-in light/dark switches.
const FONT_THEME = EditorView.theme(
  {
    "&": {
      fontSize: "13px",
      height: "100%",
      color: "var(--text)",
      backgroundColor: "transparent",
    },
    ".cm-content": {
      fontFamily: "var(--font-mono)",
      caretColor: "var(--accent)",
    },
    ".cm-scroller": { overflow: "auto", lineHeight: "1.6" },
    "&.cm-focused .cm-cursor": { borderLeftColor: "var(--accent)" },
    "&.cm-focused .cm-selectionBackground, .cm-selectionBackground, ::selection": {
      backgroundColor: "var(--accent-tint-25)",
    },
    ".cm-gutters": {
      backgroundColor: "transparent",
      color: "var(--text-faint)",
      border: "none",
    },
    ".cm-activeLine": { backgroundColor: "rgba(255, 255, 255, 0.03)" },
    ".cm-activeLineGutter": { backgroundColor: "transparent", color: "var(--text-muted)" },
    ".cm-tooltip": {
      backgroundColor: "var(--surface-raised)",
      border: "1px solid var(--border)",
      borderRadius: "var(--radius-input)",
      color: "var(--text)",
    },
    ".cm-tooltip-autocomplete ul li[aria-selected]": {
      backgroundColor: "var(--accent-tint-15)",
      color: "var(--text-strong)",
    },
  },
  { dark: true },
);

/** The `.tex` source pane (MODULES.md Manuscript Editor) — CodeMirror 6 in
 * JetBrains Mono with LaTeX highlighting and the live inline-math widgets.
 * Uncontrolled by design (CodeMirror owns its own text state); `onChange`
 * fires post-edit for the caller's own debounced save/preview. */
export function LatexSourceEditor({
  value,
  onChange,
  viewRef: externalViewRef,
  projectId,
}: {
  value: string;
  onChange: (tex: string) => void;
  viewRef: MutableRefObject<EditorView | null>;
  projectId: string;
}) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const viewRef = externalViewRef;
  const onChangeRef = useRef(onChange);
  onChangeRef.current = onChange;

  useEffect(() => {
    if (!containerRef.current) return;
    const view = new EditorView({
      parent: containerRef.current,
      state: EditorState.create({
        doc: value,
        extensions: [
          lineNumbers(),
          history(),
          keymap.of([...defaultKeymap, ...historyKeymap]),
          StreamLanguage.define(stex),
          latexMathWidgets,
          citationDecorations,
          autocompletion({ override: [citeCompletionSource(projectId)] }),
          FONT_THEME,
          EditorView.updateListener.of((update) => {
            if (update.docChanged) onChangeRef.current(update.state.doc.toString());
          }),
        ],
      }),
    });
    viewRef.current = view;
    return () => {
      view.destroy();
      viewRef.current = null;
    };
    // Mounted once; external `value` changes (draft switch) are applied via
    // the effect below rather than tearing down and rebuilding the view.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const view = viewRef.current;
    if (!view) return;
    const current = view.state.doc.toString();
    if (current !== value) {
      view.dispatch({ changes: { from: 0, to: current.length, insert: value } });
    }
  }, [value]);

  return <div className="latex-editor__source" ref={containerRef} />;
}

/** Inserts `text` at the current cursor (or replaces the selection) — backs
 * toolbar snippet/insert-image/insert-Mermaid/`\cite` actions, which all
 * act on the same live CodeMirror instance rather than the React-level
 * `value` string (Rules.md: pull complexity downward, one insertion path). */
export function insertAtCursor(view: EditorView | null, text: string) {
  if (!view) return;
  const { from, to } = view.state.selection.main;
  view.dispatch({ changes: { from, to, insert: text }, selection: { anchor: from + text.length } });
  view.focus();
}
