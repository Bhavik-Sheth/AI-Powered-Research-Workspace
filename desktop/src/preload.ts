/**
 * The only crossing of the IPC boundary (MODULES.md Desktop Shell): reads
 * `{port, token}` from the process arguments main appended via
 * `additionalArguments`, and exposes exactly that to the renderer.
 */

import { contextBridge } from "electron";

function readArg(prefix: string): string | undefined {
  const arg = process.argv.find((entry) => entry.startsWith(prefix));
  return arg?.slice(prefix.length);
}

const port = readArg("--researchos-port=");
const token = readArg("--researchos-token=");

if (port && token) {
  contextBridge.exposeInMainWorld("researchOS", {
    port: Number(port),
    token,
  });
}
