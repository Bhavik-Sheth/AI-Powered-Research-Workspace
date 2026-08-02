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
});
