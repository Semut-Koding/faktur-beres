import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Dev server on 127.0.0.1:25173 (standard 5173 + 20000); /api goes to the local API.
export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: 25173,
    strictPort: true,
    proxy: { "/api": "http://127.0.0.1:28000" },
  },
  build: { outDir: "dist", sourcemap: false },
});
