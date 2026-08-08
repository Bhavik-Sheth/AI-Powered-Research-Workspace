import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  createHighlightApiProjectsProjectIdHighlightsPost,
  getPaperApiPapersPaperIdGet,
  listProjectPapersApiProjectsProjectIdPapersGet,
  patchProjectPaperApiProjectsProjectIdPapersPaperIdPatch,
  promoteReferenceStubApiProjectsProjectIdPapersPaperIdPromotePost,
  type LibraryEntry,
  type PaperCardField,
  type PaperDetail,
} from "@research-os/api-client";

import type { SelectionState } from "../companion/wsTypes";
import { relevanceLabel, RELEVANCE_VALUES, type Relevance } from "../design/labels";
import { fetchBinary } from "../state/bridge";
import { loadDocument, RenderingCancelledException, TextLayer, type PDFDocumentProxy } from "./pdf";
import "./ReaderTab.css";
import { useAnchorSync } from "./useAnchorSync";

const CONTEXT_WINDOW = 40;

interface SelectionPopover {
  quote: string;
  prefix: string;
  suffix: string;
  x: number;
  y: number;
}

/** Real prefix/suffix from the paper's own parsed text, not the PDF.js text
 * layer's — the two text streams differ in whitespace/hyphenation (D33),
 * and the harness's substring validator checks against parsed text. An
 * `indexOf` miss just means weaker disambiguation context, not a failure —
 * the quote itself is still independently re-validated server-side. */
function contextFor(fullText: string, quote: string): { prefix: string; suffix: string } {
  const at = fullText.indexOf(quote);
  if (at === -1) return { prefix: "", suffix: "" };
  return {
    prefix: fullText.slice(Math.max(0, at - CONTEXT_WINDOW), at),
    suffix: fullText.slice(at + quote.length, at + quote.length + CONTEXT_WINDOW),
  };
}

const FIELD_LABEL: Record<string, string> = {
  problem: "Problem",
  method: "Method",
  datasets: "Datasets",
  results: "Results",
  limitations: "Limitations",
};
const FIELD_ORDER = ["problem", "method", "datasets", "results", "limitations"];

function normalise(text: string): string {
  return text.replace(/\s+/g, " ").trim().toLowerCase();
}

/** `paper_content.datasets`/`code_links` are untyped JSONB (Schema.md) — the
 * enrichment pass that populates them writes whatever a source gives it, so
 * these read the common `name`/`title`/`url` shapes defensively rather than
 * assuming one. */
function datasetLabel(dataset: Record<string, unknown>): string {
  const name = dataset.name ?? dataset.title;
  return typeof name === "string" && name ? name : JSON.stringify(dataset);
}

function codeLinkUrl(codeLink: Record<string, unknown>): string | null {
  const url = codeLink.url ?? codeLink.repo_url ?? codeLink.href;
  return typeof url === "string" && url ? url : null;
}

/** Phase 6.5: which of the three enrichment tiers found this link — shown
 * as a small provenance tag so "the paper's own text said so" reads
 * differently from "HuggingFace/Firecrawl guessed it". */
function linkSource(entry: Record<string, unknown>): string | null {
  const source = entry.source;
  return typeof source === "string" && source ? source : null;
}

/** One page: a canvas render plus an invisible, selectable text layer. */
function Page({ page, pageNumber, containerRef }: { page: import("./pdf").PDFPageProxy; pageNumber: number; containerRef: (el: HTMLDivElement | null) => void }) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const textLayerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let cancelled = false;
    let renderTask: ReturnType<import("./pdf").PDFPageProxy["render"]> | null = null;
    (async () => {
      const viewport = page.getViewport({ scale: 1.4 });
      const canvas = canvasRef.current;
      if (!canvas || cancelled) return;
      canvas.width = viewport.width;
      canvas.height = viewport.height;
      const ctx = canvas.getContext("2d");
      if (!ctx) return;
      renderTask = page.render({ canvas, canvasContext: ctx, viewport });
      try {
        await renderTask.promise;
      } catch (err) {
        if (err instanceof RenderingCancelledException) {
          return; // expected — the effect's own cleanup already tore this render down
        }
        // A real render failure (e.g. an unsupported PDF feature) would
        // otherwise look identical to a blank-but-fine page — nothing else
        // in this effect surfaces it.
        console.error("PDF page render failed", err);
        return;
      }

      if (textLayerRef.current && !cancelled) {
        textLayerRef.current.style.width = `${viewport.width}px`;
        textLayerRef.current.style.height = `${viewport.height}px`;
        const textContentSource = page.streamTextContent();
        const layer = new TextLayer({ textContentSource, container: textLayerRef.current, viewport });
        await layer.render();
      }
    })();
    return () => {
      cancelled = true;
      renderTask?.cancel();
    };
  }, [page]);

  return (
    <div className="reader__page" ref={containerRef} data-page={pageNumber}>
      <canvas ref={canvasRef} />
      <div className="reader__text-layer" ref={textLayerRef} />
    </div>
  );
}

/** The Reader (MODULES.md) — real PDF.js pages, structure sidebar, extractive card. */
export function ReaderTab({
  paperId,
  projectId,
  onAskCompanion,
  onTitleResolved,
  pendingAnchor,
  onOpenPaper,
}: {
  paperId: string;
  projectId: string;
  onAskCompanion: (selection: SelectionState, question: string) => void;
  /** Fires once the paper's real title loads — lets the tab strip fill in
   * a proper label for a tab that was opened without one (Phase 6.4: a
   * deep link or browser-back reopening a reader tab that isn't already in
   * the stack has no title to hand `openTab` up front, only a paper id). */
  onTitleResolved?: (title: string) => void;
  /** A Companion citation resolved to a span in *this* paper (Phase 6.1) —
   * `null` when the pending anchor, if any, belongs to a different open
   * tab. One-shot, consumed by `nonce` the same way `CompanionPane`'s own
   * `pendingAsk` is. */
  pendingAnchor?: { quote: string; charStart: number; charEnd: number; nonce: number } | null;
  /** Opens a reader tab for a resolved reference stub (Phase 6.3) — the same
   * callback LibraryView/MatrixView/GraphView already receive as
   * `onOpenPaper`, threaded through here for the References box. */
  onOpenPaper?: (paperId: string, title: string) => void;
}) {
  const [detail, setDetail] = useState<PaperDetail | null>(null);
  const [pdfDoc, setPdfDoc] = useState<PDFDocumentProxy | null>(null);
  const [pages, setPages] = useState<import("./pdf").PDFPageProxy[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [popover, setPopover] = useState<SelectionPopover | null>(null);
  const [highlightState, setHighlightState] = useState<"idle" | "saving" | "saved">("idle");
  const [relevanceSaving, setRelevanceSaving] = useState(false);
  const [promoting, setPromoting] = useState(false);
  const queryClient = useQueryClient();
  // Not in `PaperDetail` (that's a paper's own content, not its per-project
  // relevance). Shares its cache key with LibraryView's own list query
  // (Phase 5.3 / Phase 5 live-verify fix) — the Reader header is now the
  // only place relevance is set, and tabs never unmount (App Shell), so
  // without a shared key an already-open Library tab kept showing the
  // pre-change badge/count until a full reload.
  const papersQuery = useQuery({
    queryKey: ["projectPapers", projectId],
    queryFn: async () => {
      const { data } = await listProjectPapersApiProjectsProjectIdPapersGet({
        path: { project_id: projectId },
        throwOnError: true,
      });
      return data;
    },
  });
  const relevance = (papersQuery.data?.find((e) => e.paper.id === paperId)?.relevance as Relevance | undefined) ?? "unset";
  const pageRefs = useRef<Map<number, HTMLDivElement>>(new Map());
  const { activeAnchor, focusAnchor } = useAnchorSync();

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const { data } = await getPaperApiPapersPaperIdGet({
          path: { paper_id: paperId },
          query: { include: "sections,card" },
          throwOnError: true,
        });
        if (cancelled) return;
        setDetail(data);
        onTitleResolved?.(data.paper.title);

        if (data.paper.pdf_origin) {
          const bytes = await fetchBinary(`/api/papers/${paperId}/pdf`);
          const doc = await loadDocument(bytes);
          if (cancelled) return;
          setPdfDoc(doc);
          const loaded = await Promise.all(
            Array.from({ length: doc.numPages }, (_, i) => doc.getPage(i + 1)),
          );
          if (!cancelled) setPages(loaded);
        }
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : "Could not load this paper");
      }
    })();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [paperId]);

  // Writes the patch response straight into the shared `projectPapers`
  // cache entry — both this control and LibraryView's badges/filter-chip
  // counts update immediately, with no second round-trip and no dependency
  // on which one of them happens to refetch next.
  async function handleSetRelevance(value: Relevance) {
    setRelevanceSaving(true);
    try {
      const { data } = await patchProjectPaperApiProjectsProjectIdPapersPaperIdPatch({
        path: { project_id: projectId, paper_id: paperId },
        body: { relevance: value },
        throwOnError: true,
      });
      queryClient.setQueryData<LibraryEntry[]>(["projectPapers", projectId], (prev) =>
        prev?.map((entry) => (entry.paper.id === paperId ? { ...entry, relevance: data.relevance } : entry)),
      );
    } catch {
      // Swallowed the same way handleHighlight below resets on failure —
      // the control just stays on its last known value.
    } finally {
      setRelevanceSaving(false);
    }
  }

  async function handlePromote() {
    setPromoting(true);
    try {
      const { data } = await promoteReferenceStubApiProjectsProjectIdPapersPaperIdPromotePost({
        path: { project_id: projectId, paper_id: paperId },
        throwOnError: true,
      });
      setDetail((prev) => (prev ? { ...prev, paper: data } : prev));
      queryClient.invalidateQueries({ queryKey: ["projectPapers", projectId] });
    } catch {
      // Stays on the degraded/stub view; the button remains clickable to retry.
    } finally {
      setPromoting(false);
    }
  }

  async function scrollToQuote(quote: string) {
    const target = normalise(quote);
    for (const page of pages) {
      const content = await page.getTextContent();
      // No separator between items: PDF.js already embeds the space a text
      // run needs inside that run's own `str` (confirmed against this
      // reader's actual output), so forcing one between every item instead
      // splits a heading that PDF.js emitted as multiple runs — e.g.
      // "CONCLUSION" arriving as two items reads back as "C ONCLUSION" or
      // worse, breaking the match entirely.
      const pageText = normalise(content.items.map((item) => ("str" in item ? item.str : "")).join(""));
      if (pageText.includes(target)) {
        pageRefs.current.get(page.pageNumber)?.scrollIntoView({ behavior: "smooth", block: "center" });
        return;
      }
    }
  }

  function handleFieldClick(field: PaperCardField) {
    focusAnchor({ quote: field.value, charStart: field.char_start, charEnd: field.char_end });
    void scrollToQuote(field.value);
  }

  // A Companion citation for this paper landed (Phase 6.1) — the exact
  // `focusAnchor` + `scrollToQuote` pair a card-field click already
  // triggers, just driven by a prop instead of a click. `handledNonceRef`
  // consumes each nonce once, mirroring `CompanionPane`'s own `pendingAsk`;
  // gated on `pages.length` since `scrollToQuote` has nothing to search
  // before the PDF has finished loading.
  const handledAnchorNonceRef = useRef<number | null>(null);
  useEffect(() => {
    if (!pendingAnchor || pendingAnchor.nonce === handledAnchorNonceRef.current || pages.length === 0) return;
    handledAnchorNonceRef.current = pendingAnchor.nonce;
    focusAnchor({ quote: pendingAnchor.quote, charStart: pendingAnchor.charStart, charEnd: pendingAnchor.charEnd });
    void scrollToQuote(pendingAnchor.quote);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pendingAnchor, pages.length]);

  function handleTextSelection() {
    const sel = window.getSelection();
    if (!sel || sel.isCollapsed || sel.rangeCount === 0) {
      setPopover(null);
      return;
    }
    const quote = sel.toString().trim();
    const anchorEl = sel.getRangeAt(0).startContainer.parentElement;
    if (!quote || !detail?.content || !anchorEl?.closest(".reader__text-layer")) {
      return;
    }
    const rect = sel.getRangeAt(0).getBoundingClientRect();
    const { prefix, suffix } = contextFor(detail.content.full_text, quote);
    setHighlightState("idle");
    setPopover({ quote, prefix, suffix, x: rect.left + rect.width / 2, y: rect.top });
  }

  function closePopover() {
    setPopover(null);
    window.getSelection()?.removeAllRanges();
  }

  async function handleHighlight() {
    if (!popover) return;
    setHighlightState("saving");
    try {
      await createHighlightApiProjectsProjectIdHighlightsPost({
        path: { project_id: projectId },
        body: { paper_id: paperId, anchor: { quote: popover.quote, prefix: popover.prefix, suffix: popover.suffix } },
        throwOnError: true,
      });
      setHighlightState("saved");
    } catch {
      setHighlightState("idle");
    }
  }

  function handleAsk(question: string) {
    if (!popover) return;
    onAskCompanion({ paper_id: paperId, anchor: { quote: popover.quote, prefix: popover.prefix, suffix: popover.suffix } }, question);
    closePopover();
  }

  if (error) {
    return <div className="reader__degraded">{error}</div>;
  }
  if (!detail) {
    return <div className="reader__degraded">Loading…</div>;
  }
  if (!detail.paper.pdf_origin) {
    // A reference stub (Phase 6.3) has `fetch_state: "skipped"` — deliberately
    // never fetched, distinct from a paper whose OA fetch genuinely found
    // nothing (`"degraded"`). Only the former gets an `Add to library` action.
    const isStub = detail.paper.fetch_state === "skipped";
    return (
      <div className="reader__degraded">
        <h2>{detail.paper.title}</h2>
        <p>{detail.paper.abstract ?? "No abstract available."}</p>
        {detail.paper.source_url && (
          <p>
            <a href={detail.paper.source_url} target="_blank" rel="noreferrer">
              View at source
            </a>
          </p>
        )}
        {isStub && (
          <button type="button" className="reader__promote" disabled={promoting} onClick={() => void handlePromote()}>
            {promoting ? "Adding…" : "Add to library"}
          </button>
        )}
      </div>
    );
  }

  const sections = detail.content?.sections ?? [];
  const references = detail.content?.references ?? [];
  const datasets = detail.content?.datasets ?? [];
  const codeLinks = detail.content?.code_links ?? [];
  const card = detail.card ?? [];
  const cardByField = new Map(card.map((f) => [f.field_key, f]));

  return (
    <div className="reader">
      <header className="reader__header">
        <h1 className="reader__header-title" title={detail.paper.title}>
          {detail.paper.title}
        </h1>
        <div className="reader__relevance" role="group" aria-label="Relevance">
          <span className="reader__relevance-label">RELEVANCE</span>
          <div className="reader__relevance-control">
            {RELEVANCE_VALUES.map((value) => (
              <button
                key={value}
                type="button"
                className={`reader__relevance-segment ${relevance === value ? "reader__relevance-segment--active" : ""}`}
                disabled={relevanceSaving}
                onClick={() => void handleSetRelevance(value)}
              >
                {relevanceLabel[value]}
              </button>
            ))}
          </div>
        </div>
      </header>

      <div className="reader__body">
        <nav className="reader__sidebar">
          <h4>Sections</h4>
          {sections.map((section) => (
            <button
              key={section.section_id}
              type="button"
              className="reader__section-link"
              onClick={() => void scrollToQuote(section.heading)}
            >
              {section.heading}
            </button>
          ))}
          <h4>References ({references.length})</h4>
          {references.length === 0 && <p className="reader__not-stated">not stated in this paper</p>}
          {references.map((reference) => {
            const label = reference.title ?? reference.raw;
            // A resolved reference (Phase 6.3) opens the referenced paper's
            // own tab — a stub if it was never added, the real reader if it
            // already was. One with no resolved `paper_id` falls back to
            // the previous behaviour of jumping to its own citation text.
            return (
              <button
                key={reference.ref_id}
                type="button"
                className="reader__section-link"
                title={label}
                onClick={() =>
                  reference.paper_id ? onOpenPaper?.(reference.paper_id, label) : void scrollToQuote(reference.raw)
                }
              >
                {label}
              </button>
            );
          })}

          <h4>Datasets ({datasets.length})</h4>
          {datasets.length === 0 && <p className="reader__not-stated">not stated in this paper</p>}
          {datasets.map((dataset, index) => (
            <button
              key={index}
              type="button"
              className="reader__section-link"
              onClick={() => void scrollToQuote(datasetLabel(dataset))}
            >
              {datasetLabel(dataset)}
              {linkSource(dataset) && <span className="reader__source-tag">{linkSource(dataset)}</span>}
            </button>
          ))}

          <h4>Code ({codeLinks.length})</h4>
          {codeLinks.length === 0 && <p className="reader__not-stated">not stated in this paper</p>}
          {codeLinks.map((codeLink, index) => {
            const url = codeLinkUrl(codeLink);
            const source = linkSource(codeLink);
            return url ? (
              <a key={index} className="reader__section-link" href={url} target="_blank" rel="noreferrer">
                {url}
                {source && <span className="reader__source-tag">{source}</span>}
              </a>
            ) : (
              <p key={index} className="reader__section-link">
                {JSON.stringify(codeLink)}
              </p>
            );
          })}
        </nav>

        {/* Detects the end of a mouse-driven text selection to show the
            popover — not a click/activation handler, so it has no keyboard
            equivalent to add: selecting text via keyboard already works
            natively (Shift+arrows) without ever touching this listener. */}
        {/* eslint-disable-next-line jsx-a11y/no-static-element-interactions */}
        <div className="reader__pages" onMouseUp={handleTextSelection}>
          {pages.map((page) => (
            <Page
              key={page.pageNumber}
              page={page}
              pageNumber={page.pageNumber}
              containerRef={(el) => {
                if (el) pageRefs.current.set(page.pageNumber, el);
              }}
            />
          ))}
          {pdfDoc && pages.length === 0 && <p>Rendering…</p>}
        </div>

        <aside className="reader__card">
          <h4>Extractive card</h4>
          {FIELD_ORDER.map((fieldKey) => {
            const field = cardByField.get(fieldKey);
            return (
              <div key={fieldKey} className="reader__card-field">
                <h5>{FIELD_LABEL[fieldKey]}</h5>
                {field ? (
                  <button
                    type="button"
                    className={`reader__card-quote ${activeAnchor?.quote === field.value ? "reader__card-quote--active" : ""}`}
                    onClick={() => handleFieldClick(field)}
                  >
                    {field.value}
                  </button>
                ) : (
                  <p className="reader__not-stated">not stated in this paper</p>
                )}
                {field && (
                  <p className="reader__card-offset">
                    {field.section_heading ? `§${field.section_heading} · ` : ""}
                    {field.char_start}–{field.char_end}
                  </p>
                )}
              </div>
            );
          })}
        </aside>

        {popover && (
          <div className="reader__popover" style={{ left: popover.x, top: popover.y }}>
            <button type="button" onClick={() => handleAsk(`What does this mean: "${popover.quote}"?`)}>
              Ask about this
            </button>
            <button type="button" onClick={handleHighlight} disabled={highlightState === "saving"}>
              {highlightState === "saved" ? "Highlighted ✓" : highlightState === "saving" ? "Saving…" : "Highlight"}
            </button>
            <button type="button" onClick={() => handleAsk(`Explain this in simple terms: "${popover.quote}"`)}>
              Explain
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
