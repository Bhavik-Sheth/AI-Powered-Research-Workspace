import { useEffect, useRef, useState } from "react";
import { getPaperApiPapersPaperIdGet, type PaperCardField, type PaperDetail } from "@research-os/api-client";

import { fetchBinary } from "../state/bridge";
import { loadDocument, TextLayer, type PDFDocumentProxy } from "./pdf";
import "./ReaderTab.css";
import { useAnchorSync } from "./useAnchorSync";

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

/** One page: a canvas render plus an invisible, selectable text layer. */
function Page({ page, pageNumber, containerRef }: { page: import("./pdf").PDFPageProxy; pageNumber: number; containerRef: (el: HTMLDivElement | null) => void }) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const textLayerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      const viewport = page.getViewport({ scale: 1.4 });
      const canvas = canvasRef.current;
      if (!canvas || cancelled) return;
      canvas.width = viewport.width;
      canvas.height = viewport.height;
      const ctx = canvas.getContext("2d");
      if (!ctx) return;
      await page.render({ canvas, canvasContext: ctx, viewport }).promise;

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
export function ReaderTab({ paperId }: { paperId: string }) {
  const [detail, setDetail] = useState<PaperDetail | null>(null);
  const [pdfDoc, setPdfDoc] = useState<PDFDocumentProxy | null>(null);
  const [pages, setPages] = useState<import("./pdf").PDFPageProxy[]>([]);
  const [error, setError] = useState<string | null>(null);
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

  async function scrollToQuote(quote: string) {
    const target = normalise(quote);
    for (const page of pages) {
      const content = await page.getTextContent();
      const pageText = normalise(content.items.map((item) => ("str" in item ? item.str : "")).join(" "));
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

  if (error) {
    return <div className="reader__degraded">{error}</div>;
  }
  if (!detail) {
    return <div className="reader__degraded">Loading…</div>;
  }
  if (!detail.paper.pdf_origin) {
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
      </div>
    );
  }

  const sections = detail.content?.sections ?? [];
  const references = detail.content?.references ?? [];
  const card = detail.card ?? [];
  const cardByField = new Map(card.map((f) => [f.field_key, f]));

  return (
    <div className="reader">
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
        <h4>Datasets ({detail.content?.datasets.length ?? 0})</h4>
        <h4>Code ({detail.content?.code_links.length ?? 0})</h4>
      </nav>

      <div className="reader__pages">
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
                <p
                  className={`reader__card-quote ${activeAnchor?.quote === field.value ? "reader__card-quote--active" : ""}`}
                  onClick={() => handleFieldClick(field)}
                >
                  {field.value}
                </p>
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
    </div>
  );
}
