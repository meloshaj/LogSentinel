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
        manualChunks: {
          charts: ["recharts"],
          topology: ["@xyflow/react", "dagre"],
          vendor: ["react", "react-dom", "react-router"],
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
    setupFiles: ['./tests/vitest/setup.ts'],
    globals: true,
  },
});
