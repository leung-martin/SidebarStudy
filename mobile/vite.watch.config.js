// Builds the grading code for the Wear OS app: one plain script (no modules) for QuickJS.
import { defineConfig } from "vite";

export default defineConfig({
  publicDir: false,
  build: {
    outDir: "../wear/app/src/main/assets",
    emptyOutDir: false,
    target: "es2020",
    minify: true,
    lib: { entry: "src/watch-bridge.js", name: "SSBundle", formats: ["iife"], fileName: () => "grading.js" },
  },
});
