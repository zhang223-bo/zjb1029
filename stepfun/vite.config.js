import fs from "node:fs";
import path from "node:path";
import { defineConfig } from "vite";

const ortDir = path.resolve("ort");

export default defineConfig({
  server: {
    proxy: { "/api": "http://127.0.0.1:8787" },
  },
  plugins: [
    {
      name: "serve-ort",
      configureServer(server) {
        server.middlewares.use("/ort", (req, res, next) => {
          let name = decodeURIComponent((req.url || "/").split("?")[0]);
          if (name.startsWith("/ort/")) name = name.slice(4);
          name = name.replace(/^\/+/, "");
          const file = path.resolve(ortDir, name);
          const root = ortDir.endsWith(path.sep) ? ortDir : ortDir + path.sep;
          if (!file.startsWith(root) || !fs.existsSync(file) || !fs.statSync(file).isFile()) {
            return next();
          }
          if (file.endsWith(".mjs") || file.endsWith(".js")) {
            res.setHeader("Content-Type", "text/javascript; charset=utf-8");
          } else if (file.endsWith(".wasm")) {
            res.setHeader("Content-Type", "application/wasm");
          }
          fs.createReadStream(file).pipe(res);
        });
      },
    },
  ],
});