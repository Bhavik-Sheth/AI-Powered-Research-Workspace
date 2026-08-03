import type { EditorView } from "@codemirror/view";
import { useEffect, useRef, useState } from "react";
import {
  checkCitationsApiProjectsProjectIdDocumentsDocumentIdCheckCitationsPost,
  createDocumentApiProjectsProjectIdDocumentsPost,
  exportBibtexApiProjectsProjectIdBibtexGet,
  insertAssetApiProjectsProjectIdDocumentsDocumentIdAssetsPost,
  listDocumentsApiProjectsProjectIdDocumentsGet,
  updateDocumentApiProjectsProjectIdDocumentsDocumentIdPut,
  type CitationFinding,
  type CompileResult,
  type Document,
} from "@research-os/api-client";

import { setCitationFindings } from "./citationDecorations";
import { insertAtCursor, LatexSourceEditor } from "./LatexSourceEditor";
import "./ManuscriptTab.css";
import { renderMermaidToSvg } from "./renderMermaid";
import { useSwiftLatexPreview } from "./useSwiftLatexPreview";

const AUTOSAVE_DEBOUNCE_MS = 1200;
const STARTER_TEX = "\\documentclass{article}\n\\begin{document}\n\n\\end{document}\n";

function isCompileResult(value: Document | CompileResult): value is CompileResult {
  return "success" in value;
}

function countWords(tex: string): number {
  const trimmed = tex.trim();
  return trimmed === "" ? 0 : trimmed.split(/\s+/).length;
}

function countCites(tex: string): number {
  return (tex.match(/\\cite\{[^}]*\}/g) ?? []).length;
}

/** Manuscript Editor (MODULES.md) — `drafts list │ source/preview split`.
 * SwiftLaTeX WASM drives the always-on debounced preview; Tectonic-in-
 * Docker is the explicit escape hatch for a final full-package compile
 * (D34). `\cite` autocompletes from the project's own references, and
 * "Check citations" flags missing targets and uncited claims inline. */
export function ManuscriptTab({ projectId }: { projectId: string }) {
  const [documents, setDocuments] = useState<Document[] | null>(null);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [tex, setTex] = useState("");
  const [tectonicResult, setTectonicResult] = useState<CompileResult | null>(null);
  const [compiling, setCompiling] = useState(false);
  const [showMermaidPanel, setShowMermaidPanel] = useState(false);
  const [mermaidSource, setMermaidSource] = useState("graph TD\n  A[Idea] --> B[Result]\n");
  const [findings, setFindings] = useState<CitationFinding[]>([]);
  const [checkingCitations, setCheckingCitations] = useState(false);
  const viewRef = useRef<EditorView | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const saveTimerRef = useRef<number | null>(null);
  const dirtyRef = useRef(false);

  const active = documents?.find((d) => d.id === activeId) ?? null;
  const preview = useSwiftLatexPreview(tex);

  async function refresh(selectId?: string) {
    const { data } = await listDocumentsApiProjectsProjectIdDocumentsGet({ path: { project_id: projectId }, throwOnError: true });
    setDocuments(data);
    const next = selectId ?? activeId ?? data[0]?.id ?? null;
    setActiveId(next);
    const doc = data.find((d) => d.id === next);
    setTex(doc?.body ?? "");
  }

  useEffect(() => {
    void refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId]);

  function openDraft(doc: Document) {
    setActiveId(doc.id);
    setTex(doc.body);
    setTectonicResult(null);
    setFindings([]);
    setCitationFindings(viewRef.current, []);
  }

  async function createDraft() {
    const { data } = await createDocumentApiProjectsProjectIdDocumentsPost({
      path: { project_id: projectId },
      body: { title: `Untitled draft ${(documents?.length ?? 0) + 1}`, tex: STARTER_TEX },
      throwOnError: true,
    });
    const doc = isCompileResult(data) ? data.document : data;
    await refresh(doc.id);
  }

  function onSourceChange(nextTex: string) {
    setTex(nextTex);
    dirtyRef.current = true;
    if (saveTimerRef.current) window.clearTimeout(saveTimerRef.current);
    saveTimerRef.current = window.setTimeout(() => void autosave(nextTex), AUTOSAVE_DEBOUNCE_MS);
  }

  async function autosave(nextTex: string) {
    if (!active) return;
    dirtyRef.current = false;
    await updateDocumentApiProjectsProjectIdDocumentsDocumentIdPut({
      path: { project_id: projectId, document_id: active.id },
      body: { title: active.title, tex: nextTex },
      throwOnError: true,
    });
    setDocuments((prev) => prev?.map((d) => (d.id === active.id ? { ...d, body: nextTex } : d)) ?? prev);
  }

  async function compileWithTectonic() {
    if (!active) return;
    setCompiling(true);
    try {
      const { data } = await updateDocumentApiProjectsProjectIdDocumentsDocumentIdPut({
        path: { project_id: projectId, document_id: active.id },
        body: { title: active.title, tex, engine: "tectonic" },
        throwOnError: true,
      });
      if (isCompileResult(data)) {
        setTectonicResult(data);
        if (data.success) await refresh(active.id);
      }
    } finally {
      setCompiling(false);
    }
  }

  async function uploadAsset(filename: string, blob: Blob) {
    if (!active) return;
    const { data } = await insertAssetApiProjectsProjectIdDocumentsDocumentIdAssetsPost({
      path: { project_id: projectId, document_id: active.id },
      body: { file: new File([blob], filename) },
      throwOnError: true,
    });
    const relativeToAssets = data.path.split("/manuscript/")[1] ?? data.path;
    insertAtCursor(viewRef.current, `\\includegraphics{${relativeToAssets}}\n`);
  }

  async function onPickImage(event: React.ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    await uploadAsset(file.name, file);
  }

  async function insertMermaidDiagram() {
    const svg = await renderMermaidToSvg(mermaidSource);
    await uploadAsset(`diagram-${Date.now()}.svg`, new Blob([svg], { type: "image/svg+xml" }));
    setShowMermaidPanel(false);
  }

  async function checkCitations() {
    if (!active) return;
    setCheckingCitations(true);
    try {
      const { data } = await checkCitationsApiProjectsProjectIdDocumentsDocumentIdCheckCitationsPost({
        path: { project_id: projectId, document_id: active.id },
        throwOnError: true,
      });
      setFindings(data.findings);
      setCitationFindings(viewRef.current, data.findings);
    } finally {
      setCheckingCitations(false);
    }
  }

  async function downloadBibtex() {
    const { data } = await exportBibtexApiProjectsProjectIdBibtexGet({ path: { project_id: projectId }, throwOnError: true });
    const url = URL.createObjectURL(new Blob([data as string], { type: "text/plain" }));
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = "references.bib";
    anchor.click();
    URL.revokeObjectURL(url);
  }

  if (documents === null) return <p>Loading…</p>;

  return (
    <div className="writing">
      <aside className="writing__drafts">
        <div className="writing__drafts-header">Drafts</div>
        {documents.map((doc) => (
          <button
            key={doc.id}
            type="button"
            className={`writing__draft-row ${doc.id === activeId ? "writing__draft-row--active" : ""}`}
            onClick={() => openDraft(doc)}
          >
            <span className="writing__draft-title">{doc.title}</span>
            <span className="writing__draft-meta">
              {countWords(doc.body).toLocaleString()} words · {countCites(doc.body)} cites
            </span>
          </button>
        ))}
        <button type="button" className="writing__new-draft" onClick={() => void createDraft()}>
          + New draft
        </button>
      </aside>

      {!active ? (
        <div className="writing__empty">
          <p className="writing__empty-title">No drafts yet in this project.</p>
          <button type="button" onClick={() => void createDraft()}>
            + New draft
          </button>
        </div>
      ) : (
        <div className="writing__editor">
          <header className="writing__editor-header">
            <h2 className="writing__editor-title">{active.title}</h2>
            <div className="writing__toolbar">
              <input ref={fileInputRef} type="file" accept="image/png,image/jpeg,image/svg+xml" hidden onChange={(e) => void onPickImage(e)} />
              <button type="button" onClick={() => fileInputRef.current?.click()}>
                + Image
              </button>
              <button type="button" onClick={() => setShowMermaidPanel((prev) => !prev)}>
                + Mermaid
              </button>
              <button type="button" disabled={compiling} onClick={() => void compileWithTectonic()}>
                {compiling ? "Compiling…" : "Compile with Tectonic"}
              </button>
              <button type="button" disabled={checkingCitations} onClick={() => void checkCitations()}>
                {checkingCitations ? "Checking…" : "Check citations"}
              </button>
              <button type="button" onClick={() => void downloadBibtex()}>
                Export BibTeX
              </button>
            </div>
          </header>

          {showMermaidPanel && (
            <div className="writing__mermaid-panel">
              <textarea value={mermaidSource} onChange={(e) => setMermaidSource(e.target.value)} rows={4} />
              <button type="button" onClick={() => void insertMermaidDiagram()}>
                Insert diagram
              </button>
            </div>
          )}

          <div className="writing__split">
            <LatexSourceEditor value={tex} onChange={onSourceChange} viewRef={viewRef} projectId={projectId} />
            <div className="writing__preview">
              {preview.status === "unavailable" && (
                <p className="writing__preview-note">
                  Live preview unavailable — run <code>npm run setup:swiftlatex</code> in <code>frontend/</code>.
                </p>
              )}
              {preview.status === "loading-engine" && <p className="writing__preview-note">Loading preview engine…</p>}
              {preview.status === "compiling" && <p className="writing__preview-note">Compiling preview…</p>}
              {preview.status === "error" && <pre className="writing__compile-error">{preview.log}</pre>}
              {preview.status === "ok" && preview.pdfUrl && (
                <iframe className="writing__preview-frame" src={preview.pdfUrl} title="LaTeX preview" />
              )}
            </div>
          </div>

          {tectonicResult && (
            <div className={`writing__tectonic-result ${tectonicResult.success ? "writing__tectonic-result--ok" : "writing__tectonic-result--error"}`}>
              {tectonicResult.success ? (
                <p>Compiled cleanly with Tectonic — written to the manuscript folder in your vault.</p>
              ) : (
                <>
                  <p>Tectonic compile failed — the saved draft is unchanged.</p>
                  <pre className="writing__compile-error">{tectonicResult.log.slice(-2000)}</pre>
                </>
              )}
            </div>
          )}

          {findings.length > 0 && (
            <div className="writing__citation-findings">
              {findings.map((finding, index) => (
                <p key={index} className={`writing__citation-finding writing__citation-finding--${finding.kind}`}>
                  {finding.kind === "missing" ? finding.note : "unsupported claim — no linked source yet"}
                </p>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
