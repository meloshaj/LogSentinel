import { defineConfig } from "vite";
import path from "path";
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  build: {
    chunkSizeWarningLimit: 600,
    rollupOptions: {
      input: {
        main: path.resolve(__dirname, "index.html"),
        redirect: path.resolve(__dirname, "redirect.html"),
      },
      output: {
        manualChunks(id) {
          if (id.includes("node_modules/@xyflow")) return "flow-vendor";
          if (id.includes("node_modules/recharts")) return "charts-vendor";
          if (id.includes("node_modules/@azure/msal")) return "msal-vendor";
          if (id.includes("node_modules/cytoscape")) return "graph-vendor";
        },
      },
    },
  },
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  assetsInclude: ["**/*.svg", "**/*.csv"],
  test: {
    environment: 'jsdom',
    pool: 'threads',
    setupFiles: ['./tests/vitest/setup.ts'],
    globals: true,
    exclude: ['**/node_modules/**', 'tests/e2e/**'],
    // Keep CI deterministic on constrained runners; the suite is small and
    // spawning one fork per file causes false failures under process quotas.
    fileParallelism: false,
    maxWorkers: 1,
    minWorkers: 1,
  },
});
