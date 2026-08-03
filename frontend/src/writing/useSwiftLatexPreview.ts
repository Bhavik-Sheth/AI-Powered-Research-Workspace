import { useEffect, useRef, useState } from "react";

// The vendored engine (`npm run setup:swiftlatex`, not committed — see
// scripts/fetch-swiftlatex.mjs) exposes these as plain globals when loaded
// via a classic <script> tag, the same way its own upstream demo does.
interface PdfTeXEngineInstance {
  loadEngine(): Promise<void>;
  isReady(): boolean;
  writeMemFSFile(filename: string, content: string): void;
  setEngineMainFile(filename: string): void;
  compileLaTeX(): Promise<{ pdf?: Uint8Array; status: number; log: string }>;
}
declare global {
  interface Window {
    PdfTeXEngine?: new () => PdfTeXEngineInstance;
  }
}

const DEBOUNCE_MS = 1500;
const ENGINE_SCRIPT_SRC = "/PdfTeXEngine.js";

let scriptLoad: Promise<void> | null = null;

function loadEngineScript(): Promise<void> {
  if (scriptLoad) return scriptLoad;
  scriptLoad = new Promise((resolve, reject) => {
    const script = document.createElement("script");
    script.src = ENGINE_SCRIPT_SRC;
    script.onload = () => resolve();
    script.onerror = () => reject(new Error("swiftlatex engine script not found — run `npm run setup:swiftlatex`"));
    document.head.appendChild(script);
  });
  return scriptLoad;
}

export type PreviewStatus = "idle" | "loading-engine" | "compiling" | "ok" | "error" | "unavailable";

/** Manuscript Editor's debounced SwiftLaTeX WASM preview (MODULES.md,
 * D34) — compiles entirely in the renderer, no backend round trip. Lazily
 * loads the engine on first use (Rules.md: heavy assets load on demand),
 * then recompiles `DEBOUNCE_MS` after the source settles. */
export function useSwiftLatexPreview(tex: string) {
  const [status, setStatus] = useState<PreviewStatus>("idle");
  const [pdfUrl, setPdfUrl] = useState<string | null>(null);
  const [log, setLog] = useState("");
  const engineRef = useRef<PdfTeXEngineInstance | null>(null);
  const pdfUrlRef = useRef<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const timer = window.setTimeout(() => {
      void (async () => {
        try {
          if (engineRef.current === null) {
            setStatus("loading-engine");
            await loadEngineScript();
            if (cancelled) return;
            if (!window.PdfTeXEngine) throw new Error("swiftlatex engine script loaded but did not define PdfTeXEngine");
            const engine = new window.PdfTeXEngine();
            await engine.loadEngine();
            if (cancelled) return;
            engineRef.current = engine;
          }
          const engine = engineRef.current;
          if (!engine.isReady() || tex.trim() === "") return;

          setStatus("compiling");
          engine.writeMemFSFile("main.tex", tex);
          engine.setEngineMainFile("main.tex");
          const result = await engine.compileLaTeX();
          if (cancelled) return;
          setLog(result.log);
          if (result.pdf) {
            const url = URL.createObjectURL(new Blob([result.pdf as BlobPart], { type: "application/pdf" }));
            if (pdfUrlRef.current) URL.revokeObjectURL(pdfUrlRef.current);
            pdfUrlRef.current = url;
            setPdfUrl(url);
            setStatus("ok");
          } else {
            setStatus("error");
          }
        } catch (err) {
          if (!cancelled) {
            setStatus("unavailable");
            setLog(err instanceof Error ? err.message : String(err));
          }
        }
      })();
    }, DEBOUNCE_MS);

    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [tex]);

  useEffect(
    () => () => {
      if (pdfUrlRef.current) URL.revokeObjectURL(pdfUrlRef.current);
    },
    [],
  );

  return { status, pdfUrl, log };
}
