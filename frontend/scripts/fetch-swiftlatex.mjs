// One-time setup step (README "Running it locally"): downloads the
// SwiftLaTeX PdfTeX WASM engine's own upstream release into
// public/swiftlatex/, so the Manuscript Editor's live preview
// (useSwiftLatexPreview.ts) can load it as a same-origin static asset. Not
// committed to git (a compiled binary blob, not source — Rules.md's "never
// commit a model weight file" applies the same way here) and not an npm
// dependency — SwiftLaTeX ships no npm package, only this GitHub release
// zip (https://github.com/SwiftLaTeX/SwiftLaTeX, AGPL-3.0).

import { execFileSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const RELEASE_URL = "https://github.com/SwiftLaTeX/SwiftLaTeX/files/8066082/15022022.zip";
const FILES = ["PdfTeXEngine.js", "swiftlatexpdftex.js", "swiftlatexpdftex.wasm"];

const here = path.dirname(fileURLToPath(import.meta.url));
// Flat in public/, not a subfolder: PdfTeXEngine.js's own worker loader
// does `new Worker('swiftlatexpdftex.js')` — a bare relative specifier
// resolved against the page's URL, not the script's own — so the sibling
// file must sit at the site root for that lookup to resolve.
const destDir = path.resolve(here, "..", "public");

if (FILES.every((name) => fs.existsSync(path.join(destDir, name)))) {
  console.log("swiftlatex assets already present, skipping fetch.");
  process.exit(0);
}

fs.mkdirSync(destDir, { recursive: true });
const zipPath = path.join(destDir, "_release.zip");
execFileSync("curl", ["-sL", "-o", zipPath, RELEASE_URL]);
execFileSync("unzip", ["-o", zipPath, ...FILES, "-d", destDir]);
fs.rmSync(zipPath);

console.log(`fetched SwiftLaTeX pdftex engine into ${destDir}`);
