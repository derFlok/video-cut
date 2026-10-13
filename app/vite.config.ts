/// <reference types="vitest/config" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The production build goes straight into the Python package (served by `videocut ui`).
// `--mode mock` swaps the real API for the in-memory fake (development, screenshots).
export default defineConfig(({ mode }) => ({
  plugins: [react()],
  base: "./",
  define: { __MOCK__: JSON.stringify(mode === "mock") },
  build: {
    outDir: "../src/videocut/web/dist", // relative to app/ (no Node types needed here)
    emptyOutDir: true,
    sourcemap: false,
  },
  server: { host: "127.0.0.1" },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["src/__tests__/setup.ts"],
    css: false,
  },
}));
