import { execSync } from "node:child_process";
import { writeFileSync } from "node:fs";
import { fileURLToPath, URL } from "node:url";
import react from "@vitejs/plugin-react";
import { defineConfig, type Plugin } from "vite";

// The Studio backend (decider-lab ui) serves the built SPA from src/decider_lab/ui/static.
// `npm run dev` serves the SPA with hot reload and proxies /api to a running backend:
//   decider-lab ui --port 7861 --no-browser       (or BACKEND_PORT=... npm run dev)
//   open http://localhost:5173/?token=<the token it printed>
const backendPort = process.env.BACKEND_PORT ?? "7861";
const outDir = fileURLToPath(new URL("../src/decider_lab/ui/static", import.meta.url));

function buildStamp(): Plugin {
  return {
    name: "studio-build-stamp",
    apply: "build",
    closeBundle() {
      let rev = "dev";
      try {
        rev = execSync("git rev-parse --short HEAD", { stdio: ["ignore", "pipe", "ignore"] }).toString().trim();
      } catch {
        /* not a git checkout */
      }
      writeFileSync(`${outDir}/build.txt`, `${rev}\n`);
    },
  };
}

export default defineConfig({
  plugins: [react(), buildStamp()],
  resolve: { alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) } },
  build: {
    outDir,
    emptyOutDir: true,
    sourcemap: false,
    chunkSizeWarningLimit: 900,
    rollupOptions: {
      output: {
        manualChunks: {
          react: ["react", "react-dom", "react-router-dom", "@tanstack/react-query"],
          charts: ["recharts"],
          editor: ["@uiw/react-codemirror", "@codemirror/lang-yaml", "@codemirror/lint"],
        },
      },
    },
  },
  server: {
    port: 5173,
    strictPort: true,
    proxy: {
      "/api": {
        target: `http://127.0.0.1:${backendPort}`,
        changeOrigin: true,
        // the backend refuses foreign Origins on writes; the dev proxy is same-machine
        configure: (proxy) => proxy.on("proxyReq", (req) => req.removeHeader("origin")),
      },
    },
  },
  preview: { port: 4173 },
});
