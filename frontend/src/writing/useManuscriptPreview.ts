import { useEffect, useRef, useState } from "react";

import { updateDocumentApiProjectsProjectIdDocumentsDocumentIdPut } from "@research-os/api-client";

import { fetchBinary } from "../state/bridge";

const DEBOUNCE_MS = 1500;
const DOCUMENT_BODY = /\\begin\{document\}([\s\S]*?)\\end\{document\}/;
// An unescaped `%` starts a LaTeX comment that runs to end of line; `\%` is
// a literal percent sign and must not be stripped.
const LINE_COMMENT = /(?<!\\)%.*$/gm;

export type PreviewStatus = "idle" | "compiling" | "ok" | "error" | "empty";

/** True when the manuscript has no real content to compile: no text between
 * `\begin{document}` and `\end{document}` once comments and whitespace are
 * discounted. Tests the document body rather than the raw source, so
 * `STARTER_TEX`'s boilerplate preamble never counts as "content" on its
 * own — a fresh draft reads as empty until the writer adds real body text. */
export function isManuscriptBodyEmpty(tex: string): boolean {
  const match = tex.match(DOCUMENT_BODY);
  const body = match ? match[1] : tex;
  return body.replace(LINE_COMMENT, "").trim() === "";
}

/** Manuscript Editor's debounced compiled preview (MODULES.md, D34 as
 * superseded by Phase 4.1 — see docker/tectonic.Dockerfile). Compiles
 * entirely against Tectonic-in-Docker's locally baked TeX Live bundle, so
 * this never makes a network request of its own; the backend's own compile
 * runs with `--network none`. Replaces the old SwiftLaTeX WASM preview,
 * which could never have worked offline (its release ships no `.fmt` file,
 * and even a working one would still resolve `\usepackage` targets from
 * SwiftLaTeX's hosted texlive endpoint at compile time).
 *
 * Owns the debounce timer, one instance per open document tab, same as the
 * hook it replaces. A compile failure surfaces `log` for the compile-error
 * panel without discarding editor content — the caller already keeps `tex`
 * in its own state regardless of this hook's outcome.
 *
 * Skips the compile entirely (status `"empty"`) while `isManuscriptBodyEmpty`
 * holds — a fresh draft's boilerplate preamble has zero pages of output,
 * which Tectonic treats as fatal rather than benign, so compiling it would
 * only ever surface an `xdvipdfmx`/"No pages of output" error (Phase 4.1). */
export function useManuscriptPreview(tex: string, projectId: string, documentId: string | null, title: string) {
  const [status, setStatus] = useState<PreviewStatus>("idle");
  const [pdfUrl, setPdfUrl] = useState<string | null>(null);
  const [log, setLog] = useState("");
  const pdfUrlRef = useRef<string | null>(null);
  const generationRef = useRef(0);
  // Set by the debounce-scheduling effect below whenever a compile+save is
  // pending; cleared once it actually runs (naturally, via the timer, or
  // via a flush). The `documentId`-only effect further down calls this on
  // switch/unmount so an edit inside the debounce window is still sent to
  // the server (flush) rather than silently dropped — it just isn't applied
  // to this hook's own display state afterward (cancel), since the UI has
  // already moved on to a different document (Bug Fix Plan Phase 3.2).
  const flushRef = useRef<(() => void) | null>(null);

  useEffect(() => {
    if (documentId === null) {
      flushRef.current = null;
      return;
    }
    if (isManuscriptBodyEmpty(tex)) {
      setStatus("empty");
      flushRef.current = null;
      return;
    }
    let cancelled = false;

    const runCompile = () => {
      void (async () => {
        const generation = ++generationRef.current;
        if (!cancelled) setStatus("compiling");
        try {
          const { data } = await updateDocumentApiProjectsProjectIdDocumentsDocumentIdPut({
            path: { project_id: projectId, document_id: documentId },
            body: { title, tex, engine: "tectonic" },
            throwOnError: true,
          });
          if (cancelled || generation !== generationRef.current) return;
          if (!("success" in data)) return; // engine wasn't honoured server-side — nothing to preview
          if (!data.success) {
            setLog(data.log);
            setStatus("error");
            return;
          }
          const pdfBytes = await fetchBinary(`/api/projects/${projectId}/documents/${documentId}/pdf`);
          if (cancelled || generation !== generationRef.current) return;
          const url = URL.createObjectURL(new Blob([pdfBytes], { type: "application/pdf" }));
          if (pdfUrlRef.current) URL.revokeObjectURL(pdfUrlRef.current);
          pdfUrlRef.current = url;
          setPdfUrl(url);
          setLog(data.log);
          setStatus("ok");
        } catch (err) {
          if (!cancelled) {
            setStatus("error");
            setLog(err instanceof Error ? err.message : String(err));
          }
        }
      })();
    };

    const timer = window.setTimeout(() => {
      flushRef.current = null;
      runCompile();
    }, DEBOUNCE_MS);

    flushRef.current = () => {
      window.clearTimeout(timer);
      flushRef.current = null;
      runCompile();
    };

    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [tex, projectId, documentId, title]);

  // Flush (not merely cancel) a pending compile+save when the open document
  // changes or this hook's owner unmounts. Deliberately keyed on
  // `documentId` alone, not the effect above's full dependency list — a
  // keystroke (which changes `tex`) must still debounce normally; only an
  // actual document switch (or unmount) should force the pending save out
  // early (Bug Fix Plan Phase 3.2: flush-then-cancel, not cancel-only).
  useEffect(() => {
    return () => {
      flushRef.current?.();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [documentId]);

  useEffect(
    () => () => {
      if (pdfUrlRef.current) URL.revokeObjectURL(pdfUrlRef.current);
    },
    [],
  );

  return { status, pdfUrl, log };
}
