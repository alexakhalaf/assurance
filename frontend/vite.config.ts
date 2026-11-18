import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  build: { outDir: "../assurance/ui", emptyOutDir: true, chunkSizeWarningLimit: 1500 },
  server: { proxy: { "/api": "http://localhost:8000" } },
});
