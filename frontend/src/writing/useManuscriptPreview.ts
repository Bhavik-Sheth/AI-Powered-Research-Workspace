import { useEffect, useRef, useState } from "react";

import { updateDocumentApiProjectsProjectIdDocumentsDocumentIdPut } from "@research-os/api-client";

import { fetchBinary } from "../state/bridge";

const DEBOUNCE_MS = 1500;

export type PreviewStatus = "idle" | "compiling" | "ok" | "error";

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
 * in its own state regardless of this hook's outcome. */
export function useManuscriptPreview(tex: string, projectId: string, documentId: string | null, title: string) {
  const [status, setStatus] = useState<PreviewStatus>("idle");
  const [pdfUrl, setPdfUrl] = useState<string | null>(null);
  const [log, setLog] = useState("");
  const pdfUrlRef = useRef<string | null>(null);
  const generationRef = useRef(0);

  useEffect(() => {
    if (documentId === null || tex.trim() === "") return;
    let cancelled = false;
    const timer = window.setTimeout(() => {
      void (async () => {
        const generation = ++generationRef.current;
        setStatus("compiling");
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
    }, DEBOUNCE_MS);

    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [tex, projectId, documentId, title]);

  useEffect(
    () => () => {
      if (pdfUrlRef.current) URL.revokeObjectURL(pdfUrlRef.current);
    },
    [],
  );

  return { status, pdfUrl, log };
}
