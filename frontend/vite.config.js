import fs from "node:fs";
import path from "node:path";
import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";

const VENDOR_DIR = path.resolve(__dirname, "../research/fastpath-frontend/vendor");

const MIME_BY_EXT = {
  ".js": "application/javascript; charset=utf-8",
  ".mjs": "application/javascript; charset=utf-8",
  ".wasm": "application/wasm",
  ".task": "application/octet-stream",
};

function copyDirRecursive(src, dest) {
  fs.mkdirSync(dest, { recursive: true });
  for (const entry of fs.readdirSync(src, { withFileTypes: true })) {
    const srcPath = path.join(src, entry.name);
    const destPath = path.join(dest, entry.name);
    if (entry.isDirectory()) {
      copyDirRecursive(srcPath, destPath);
    } else if (entry.isFile()) {
      fs.copyFileSync(srcPath, destPath);
    }
  }
}

function mediaPipeVendorPlugin() {
  return {
    name: "mediapipe-local-vendor",
    configureServer(server) {
      server.middlewares.use("/vendor", (req, res, next) => {
        const rawPath = (req.url || "/").split("?")[0];
        const relPath = decodeURIComponent(rawPath).replace(/^\/+/, "");
        const resolved = path.resolve(VENDOR_DIR, relPath);
        if (resolved !== VENDOR_DIR && !resolved.startsWith(VENDOR_DIR + path.sep)) {
          res.statusCode = 403;
          res.end("Forbidden");
          return;
        }
        if (!fs.existsSync(resolved) || !fs.statSync(resolved).isFile()) {
          next();
          return;
        }
        const ext = path.extname(resolved).toLowerCase();
        res.setHeader("Content-Type", MIME_BY_EXT[ext] || "application/octet-stream");
        res.setHeader("Cache-Control", "public, max-age=3600");
        res.end(fs.readFileSync(resolved));
      });
    },
    closeBundle() {
      if (!fs.existsSync(VENDOR_DIR)) return;
      const distVendor = path.resolve(__dirname, "dist/vendor");
      fs.mkdirSync(distVendor, { recursive: true });
      for (const fileName of ["vision_bundle.js", "hand_landmarker.task"]) {
        const srcFile = path.join(VENDOR_DIR, fileName);
        if (fs.existsSync(srcFile)) {
          fs.copyFileSync(srcFile, path.join(distVendor, fileName));
        }
      }
      const wasmSrc = path.join(VENDOR_DIR, "wasm");
      if (fs.existsSync(wasmSrc)) {
        copyDirRecursive(wasmSrc, path.join(distVendor, "wasm"));
      }
    },
  };
}

export default defineConfig({
  plugins: [vue(), mediaPipeVendorPlugin()],
  server: {
    host: "127.0.0.1",
    port: 5173,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: "dist",
    chunkSizeWarningLimit: 1500,
  },
});
