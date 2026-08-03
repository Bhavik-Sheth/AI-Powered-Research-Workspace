import mermaid from "mermaid";

let initialized = false;

/** Renders Mermaid source to an SVG string for the editor's "Insert Mermaid
 * diagram" path (D34) — the SVG is then uploaded as a manuscript asset and
 * inlined via `\includegraphics`, same as an uploaded image. */
export async function renderMermaidToSvg(source: string): Promise<string> {
  if (!initialized) {
    mermaid.initialize({ startOnLoad: false, securityLevel: "strict" });
    initialized = true;
  }
  const { svg } = await mermaid.render(`mermaid-${Date.now()}`, source);
  return svg;
}
