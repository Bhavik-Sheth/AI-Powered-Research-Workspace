// Regenerates this package from the backend's own OpenAPI schema (Rules.md:
// "regenerated from FastAPI's OpenAPI schema on every backend change and
// committed in the same commit"). Two steps: dump the schema by importing
// the FastAPI app directly (no server needed), then run the TS codegen.

import { execFileSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const packageRoot = path.resolve(here, "..");
const backendDir = path.resolve(packageRoot, "../../backend");
const schemaPath = path.resolve(packageRoot, "openapi.json");

const schema = execFileSync("uv", ["run", "python", "scripts/dump_openapi.py"], {
  cwd: backendDir,
  env: { ...process.env, BEARER_TOKEN: "codegen-placeholder" },
  encoding: "utf-8",
});

const fs = await import("node:fs");
fs.writeFileSync(schemaPath, schema);

execFileSync("npx", ["@hey-api/openapi-ts"], { cwd: packageRoot, stdio: "inherit" });
