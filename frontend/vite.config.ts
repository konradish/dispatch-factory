import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import path from "path";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  server: {
    host: "0.0.0.0",
    port: 5174,
    strictPort: true,
    allowedHosts: ["factory.localhost", "localhost"],
    proxy: {
      "/api/ws": {
        target: "http://127.0.0.1:8420",
        ws: true,
      },
      "/api": {
        target: "http://127.0.0.1:8420",
      },
    },
  },
});
