import { defineConfig } from "vite";

export default defineConfig({
  base: "./",  // the Android app loads files relative to its own bundle
  build: { outDir: "dist", target: "es2022" },
});
