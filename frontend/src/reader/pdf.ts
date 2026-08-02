/** Thin PDF.js setup — the only file that imports pdfjs-dist directly. */

import * as pdfjsLib from "pdfjs-dist";
import workerSrc from "pdfjs-dist/build/pdf.worker.mjs?url";

pdfjsLib.GlobalWorkerOptions.workerSrc = workerSrc;

export async function loadDocument(data: ArrayBuffer) {
  return pdfjsLib.getDocument({ data }).promise;
}

export type { PDFDocumentProxy, PDFPageProxy } from "pdfjs-dist";
export { TextLayer } from "pdfjs-dist";
