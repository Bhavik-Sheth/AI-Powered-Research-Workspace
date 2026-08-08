/** Thin PDF.js setup — the only file that imports pdfjs-dist directly. */

// The `legacy` build, not the plain `pdfjs-dist` one: pdfjs-dist 6.x's
// default build assumes a JS engine new enough to have things like
// `Uint8Array.prototype.toHex` and `Promise.withResolvers` natively
// (its own `engines.node` is ">=22.13.0") — Electron's bundled Chromium here
// predates several of those, which surfaced as "hashOriginal.toHex is not a
// function" on every PDF open and, once that was papered over, a page that
// loaded (outline, extractive card) but rendered blank. The `legacy` build
// is pdfjs-dist's own supported answer to that gap: the same API, with
// feature-detected polyfills for exactly this class of older engine.
import * as pdfjsLib from "pdfjs-dist/legacy/build/pdf.mjs";
import workerSrc from "pdfjs-dist/legacy/build/pdf.worker.mjs?url";

pdfjsLib.GlobalWorkerOptions.workerSrc = workerSrc;

export async function loadDocument(data: ArrayBuffer) {
  return pdfjsLib.getDocument({ data }).promise;
}

export type { PDFDocumentProxy, PDFPageProxy } from "pdfjs-dist/legacy/build/pdf.mjs";
export { TextLayer, RenderingCancelledException } from "pdfjs-dist/legacy/build/pdf.mjs";
