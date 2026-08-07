/// <reference types="vitest/config" />
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  // Electron loads the built bundle from disk (file://), so asset URLs must
  // be relative rather than absolute (TRD §1.3).
  base: "./",
  build: {
    outDir: "dist",
  },
  // Phase 7.2 — one config, not a separate vitest.config.ts, since nothing
  // here needs to diverge from the dev/build setup above.
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
  },
});
