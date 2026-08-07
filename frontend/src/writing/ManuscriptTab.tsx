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
import "../design/buttons.css";
import { ErrorCard } from "../design/ErrorCard";
import "./ManuscriptTab.css";
import { renderMermaidToSvg } from "./renderMermaid";
import { useManuscriptPreview } from "./useManuscriptPreview";

/** One error surface for the whole tab — editor content is never discarded
 * on failure (MODULES.md Manuscript Editor), so `retry` always redoes the
 * exact failed action rather than reloading (which could otherwise clobber
 * an in-progress edit with stale server state). */
interface ManuscriptError {
  title: string;
  message: string;
  retry: () => void;
}

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
 * Tectonic-in-Docker, compiling against a TeX Live bundle baked into its
 * image, drives the always-on debounced preview (Phase 4.1 — supersedes
 * D34's SwiftLaTeX-WASM default, which could never work offline: its
 * upstream release ships no format file, and even a working one would
 * still fetch every `\usepackage` target from a third-party host at
 * compile time). `\cite` autocompletes from the project's own references,
 * and "Check citations" flags missing targets and uncited claims inline. */
export function ManuscriptTab({ projectId }: { projectId: string }) {
  const [documents, setDocuments] = useState<Document[] | null>(null);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [tex, setTex] = useState("");
  const [showMermaidPanel, setShowMermaidPanel] = useState(false);
  const [mermaidSource, setMermaidSource] = useState("graph TD\n  A[Idea] --> B[Result]\n");
  // `null` = no "Check citations" run yet for this draft; `[]` = a run
  // completed and found nothing — the panel must tell those two apart so a
  // clean draft doesn't read as a dead button (Phase 4.2).
  const [findings, setFindings] = useState<CitationFinding[] | null>(null);
  const [checkingCitations, setCheckingCitations] = useState(false);
  const [error, setError] = useState<ManuscriptError | null>(null);
  const viewRef = useRef<EditorView | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const saveTimerRef = useRef<number | null>(null);
  const dirtyRef = useRef(false);
  // Mirror the latest `tex` / active document so `flushAutosave` — called
  // from `openDraft` (before switching) and from the unmount cleanup below
  // — always sees the pre-switch values even though it can't rely on this
  // render's `tex`/`active` after `setActiveId`/`setTex` have been called.
  const texRef = useRef(tex);
  texRef.current = tex;

  const active = documents?.find((d) => d.id === activeId) ?? null;
  const activeRef = useRef(active);
  activeRef.current = active;
  const preview = useManuscriptPreview(tex, projectId, activeId, active?.title ?? "");

  async function refresh(selectId?: string) {
    try {
      const { data } = await listDocumentsApiProjectsProjectIdDocumentsGet({ path: { project_id: projectId }, throwOnError: true });
      setDocuments(data);
      const next = selectId ?? activeId ?? data[0]?.id ?? null;
      setActiveId(next);
      const doc = data.find((d) => d.id === next);
      setTex(doc?.body ?? "");
      setError(null);
    } catch (err) {
      setError({
        title: "Could not load drafts",
        message: err instanceof Error ? err.message : "Could not load drafts",
        retry: () => void refresh(selectId),
      });
    }
  }

  useEffect(() => {
    void refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId]);

  /** Saves `doc`'s pending `nextTex` right now, bypassing the debounce timer
   * — used both by the timer itself and by a flush. Only clears `dirtyRef`
   * on success, so a failed save is retried by the next flush/switch rather
   * than being silently treated as clean (Bug Fix Plan Phase 3.1/3.2). */
  async function autosave(doc: Document, nextTex: string) {
    try {
      await updateDocumentApiProjectsProjectIdDocumentsDocumentIdPut({
        path: { project_id: projectId, document_id: doc.id },
        body: { title: doc.title, tex: nextTex },
        throwOnError: true,
      });
      dirtyRef.current = false;
      setDocuments((prev) => prev?.map((d) => (d.id === doc.id ? { ...d, body: nextTex } : d)) ?? prev);
      setError(null);
    } catch (err) {
      setError({
        title: "Could not save this draft",
        message: err instanceof Error ? err.message : "Could not save this draft",
        retry: () => void autosave(doc, nextTex),
      });
    }
  }

  function scheduleAutosave(doc: Document, nextTex: string) {
    dirtyRef.current = true;
    if (saveTimerRef.current) window.clearTimeout(saveTimerRef.current);
    saveTimerRef.current = window.setTimeout(() => {
      saveTimerRef.current = null;
      void autosave(doc, nextTex);
    }, AUTOSAVE_DEBOUNCE_MS);
  }

  /** Runs a still-pending autosave immediately instead of letting it be
   * dropped — called before switching the open draft and on unmount, so an
   * edit made inside the ~1.2s debounce window is never silently lost
   * (Bug Fix Plan Phase 3.2). */
  function flushAutosave() {
    if (saveTimerRef.current) {
      window.clearTimeout(saveTimerRef.current);
      saveTimerRef.current = null;
    }
    if (dirtyRef.current && activeRef.current) {
      void autosave(activeRef.current, texRef.current);
    }
  }

  useEffect(() => {
    return () => flushAutosave();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function openDraft(doc: Document) {
    flushAutosave();
    setActiveId(doc.id);
    setTex(doc.body);
    setFindings(null);
    setCitationFindings(viewRef.current, []);
  }

  async function createDraft() {
    try {
      const { data } = await createDocumentApiProjectsProjectIdDocumentsPost({
        path: { project_id: projectId },
        body: { title: `Untitled draft ${(documents?.length ?? 0) + 1}`, tex: STARTER_TEX },
        throwOnError: true,
      });
      const doc = isCompileResult(data) ? data.document : data;
      await refresh(doc.id);
    } catch (err) {
      setError({
        title: "Could not create a new draft",
        message: err instanceof Error ? err.message : "Could not create a new draft",
        retry: () => void createDraft(),
      });
    }
  }

  function onSourceChange(nextTex: string) {
    setTex(nextTex);
    if (!active) return;
    scheduleAutosave(active, nextTex);
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

  async function attemptImageUpload(file: File) {
    setError(null);
    try {
      await uploadAsset(file.name, file);
    } catch (err) {
      setError({
        title: "Could not upload this image",
        message: err instanceof Error ? err.message : "Could not upload this image",
        retry: () => void attemptImageUpload(file),
      });
    }
  }

  async function onPickImage(event: React.ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    await attemptImageUpload(file);
  }

  async function insertMermaidDiagram() {
    setError(null);
    try {
      const svg = await renderMermaidToSvg(mermaidSource);
      await uploadAsset(`diagram-${Date.now()}.svg`, new Blob([svg], { type: "image/svg+xml" }));
      setShowMermaidPanel(false);
    } catch (err) {
      setError({
        title: "Could not insert this diagram",
        message: err instanceof Error ? err.message : "Could not insert this diagram",
        retry: () => void insertMermaidDiagram(),
      });
    }
  }

  async function checkCitations() {
    if (!active) return;
    setCheckingCitations(true);
    setError(null);
    try {
      const { data } = await checkCitationsApiProjectsProjectIdDocumentsDocumentIdCheckCitationsPost({
        path: { project_id: projectId, document_id: active.id },
        throwOnError: true,
      });
      setFindings(data.findings);
      setCitationFindings(viewRef.current, data.findings);
    } catch (err) {
      setError({
        title: "Could not check citations",
        message: err instanceof Error ? err.message : "Could not check citations",
        retry: () => void checkCitations(),
      });
    } finally {
      setCheckingCitations(false);
    }
  }

  async function downloadBibtex() {
    setError(null);
    try {
      const { data } = await exportBibtexApiProjectsProjectIdBibtexGet({ path: { project_id: projectId }, throwOnError: true });
      const url = URL.createObjectURL(new Blob([data as string], { type: "text/plain" }));
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = "references.bib";
      anchor.click();
      URL.revokeObjectURL(url);
    } catch (err) {
      setError({
        title: "Could not export references",
        message: err instanceof Error ? err.message : "Could not export references",
        retry: () => void downloadBibtex(),
      });
    }
  }

  if (documents === null) {
    if (error) return <ErrorCard title={error.title} message={error.message} onRetry={error.retry} />;
    return <p>Loading…</p>;
  }

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
        <button type="button" className="writing__new-draft btn btn--primary" onClick={() => void createDraft()}>
          + New draft
        </button>
      </aside>

      {!active ? (
        <div className="writing__empty">
          <p className="writing__empty-title">No drafts yet in this project.</p>
          <button type="button" className="btn btn--primary" onClick={() => void createDraft()}>
            + New draft
          </button>
          {error && <ErrorCard title={error.title} message={error.message} onRetry={error.retry} />}
        </div>
      ) : (
        <div className="writing__editor">
          <header className="writing__editor-header">
            <h2 className="writing__editor-title">{active.title}</h2>
            <div className="writing__toolbar">
              <input ref={fileInputRef} type="file" accept="image/png,image/jpeg,image/svg+xml" hidden onChange={(e) => void onPickImage(e)} />
              <button type="button" className="btn btn--outline" onClick={() => fileInputRef.current?.click()}>
                + Image
              </button>
              <button type="button" className="btn btn--outline" onClick={() => setShowMermaidPanel((prev) => !prev)}>
                + Mermaid
              </button>
              <button type="button" className="btn btn--outline" disabled={checkingCitations} onClick={() => void checkCitations()}>
                {checkingCitations ? "Checking…" : "Check citations"}
              </button>
              <button type="button" className="btn btn--outline" onClick={() => void downloadBibtex()}>
                Export BibTeX
              </button>
            </div>
          </header>

          {error && <ErrorCard title={error.title} message={error.message} onRetry={error.retry} />}

          {showMermaidPanel && (
            <div className="writing__mermaid-panel">
              <textarea value={mermaidSource} onChange={(e) => setMermaidSource(e.target.value)} rows={4} />
              <button type="button" className="btn btn--primary" onClick={() => void insertMermaidDiagram()}>
                Insert diagram
              </button>
            </div>
          )}

          <div className="writing__split">
            <LatexSourceEditor value={tex} onChange={onSourceChange} viewRef={viewRef} projectId={projectId} />
            <div className="writing__preview">
              {preview.status === "idle" && <p className="writing__preview-note">Start typing to compile a preview.</p>}
              {preview.status === "empty" && (
                <p className="writing__preview-note writing__preview-note--empty">Nothing to preview yet — add some text to the document body.</p>
              )}
              {preview.status === "compiling" && <p className="writing__preview-note">Compiling preview…</p>}
              {preview.status === "error" && <pre className="writing__compile-error">{preview.log.slice(-2000)}</pre>}
              {preview.status === "ok" && preview.pdfUrl && (
                <iframe className="writing__preview-frame" src={preview.pdfUrl} title="LaTeX preview" />
              )}
            </div>
          </div>

          {findings !== null && (
            <div className="writing__citation-findings">
              {findings.length === 0 ? (
                <p className="writing__citation-finding writing__citation-finding--clean">No issues found.</p>
              ) : (
                findings.map((finding, index) => (
                  <p key={index} className={`writing__citation-finding writing__citation-finding--${finding.kind}`}>
                    {finding.kind === "missing" ? finding.note : "unsupported claim — no linked source yet"}
                  </p>
                ))
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
