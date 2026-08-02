/**
 * Desktop Shell entry point (MODULES.md). Zero business logic: spawn/signal
 * the sidecar, generate the per-launch bearer token, own the BrowserWindow,
 * and hand `{port, token}` to the preload. Any import beyond that is a
 * review stop (Rules.md).
 */

import { randomBytes } from "node:crypto";
import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import path from "node:path";
import { app, BrowserWindow } from "electron";

const REPO_ROOT = path.join(__dirname, "..", "..");
const BACKEND_DIR = path.join(REPO_ROOT, "backend");
const FRONTEND_INDEX = path.join(REPO_ROOT, "frontend", "dist", "index.html");
const SIDECAR_PORT_TIMEOUT_MS = 30_000;

let sidecar: ChildProcessWithoutNullStreams | null = null;

function generateBearerToken(): string {
  return randomBytes(32).toString("hex");
}

/**
 * Spawns the sidecar and resolves once it has printed its bound port on
 * stdout — binding a socket happens before any Postgres/migration work, so
 * this resolves in well under a second; it is not the same wait as "the
 * sidecar is ready" (TRD §2.2's readiness capabilities), which the renderer
 * polls for itself once it has the bridge.
 */
function spawnSidecar(token: string): Promise<number> {
  return new Promise((resolve, reject) => {
    const child = spawn("uv", ["run", "python", "main.py"], {
      cwd: BACKEND_DIR,
      env: { ...process.env, BEARER_TOKEN: token },
    });
    sidecar = child;

    const timeout = setTimeout(() => {
      reject(new Error("sidecar did not report a port in time"));
    }, SIDECAR_PORT_TIMEOUT_MS);

    let portResolved = false;
    child.stdout.on("data", (chunk: Buffer) => {
      const text = chunk.toString("utf-8");
      process.stdout.write(`[sidecar] ${text}`);
      if (!portResolved) {
        const firstLine = text.split("\n", 1)[0].trim();
        const port = Number(firstLine);
        if (Number.isInteger(port) && port > 0) {
          portResolved = true;
          clearTimeout(timeout);
          resolve(port);
        }
      }
    });
    child.stderr.on("data", (chunk: Buffer) => {
      process.stderr.write(`[sidecar] ${chunk.toString("utf-8")}`);
    });
    child.on("exit", (code) => {
      if (!portResolved) {
        clearTimeout(timeout);
        reject(new Error(`sidecar exited before reporting a port (code ${code})`));
      }
    });
  });
}

function stopSidecar(): void {
  if (!sidecar) {
    return;
  }
  const child = sidecar;
  sidecar = null;
  child.kill("SIGTERM");
  setTimeout(() => {
    if (!child.killed) {
      child.kill("SIGKILL");
    }
  }, 5_000);
}

async function createWindow(): Promise<void> {
  const token = generateBearerToken();

  let bridgeArgs: string[];
  try {
    const port = await spawnSidecar(token);
    bridgeArgs = [`--researchos-port=${port}`, `--researchos-token=${token}`];
  } catch (err) {
    // Rules.md: a sidecar that fails to bind shows a startup-failure state
    // rather than hanging. Without a port there is nothing to bridge; the
    // window still opens so that failure is visible instead of silent.
    console.error("[desktop] sidecar startup failed:", err);
    bridgeArgs = [];
  }

  const win = new BrowserWindow({
    width: 1440,
    height: 900,
    show: false,
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
      additionalArguments: bridgeArgs,
    },
  });
  win.once("ready-to-show", () => win.show());

  const devServerUrl = process.env.RESEARCHOS_DEV_SERVER_URL;
  if (devServerUrl) {
    await win.loadURL(devServerUrl);
  } else {
    await win.loadFile(FRONTEND_INDEX);
  }
}

app.whenReady().then(() => {
  void createWindow();
});

app.on("window-all-closed", () => {
  stopSidecar();
  if (process.platform !== "darwin") {
    app.quit();
  }
});

app.on("before-quit", stopSidecar);
