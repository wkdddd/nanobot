import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";
import path from "node:path";

function numericArg(name: string): number | undefined {
  const index = process.argv.indexOf(name);
  if (index >= 0 && index + 1 < process.argv.length) {
    const value = Number(process.argv[index + 1]);
    return Number.isFinite(value) ? value : undefined;
  }
  const prefix = `${name}=`;
  const match = process.argv.find((arg) => arg.startsWith(prefix));
  if (match) {
    const value = Number(match.slice(prefix.length));
    return Number.isFinite(value) ? value : undefined;
  }
  return undefined;
}

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  const target = env.NANOBOT_API_URL ?? "http://127.0.0.1:8765";
  const wsTarget = target.replace(/^http/, "ws");
  const port = numericArg("--port") ?? Number(env.VITE_PORT ?? 5173);
  const hmrPort = Number(env.VITE_HMR_PORT ?? port + 1);

  return {
    plugins: [react()],
    resolve: {
      alias: {
        "@": path.resolve(__dirname, "./src"),
      },
    },
    optimizeDeps: {
      // Radix dialog was introduced mid-session for the mobile sidebar sheet.
      // When Vite re-optimizes it on a running dev server, the browser can race
      // and request stale chunk paths from `.vite/deps`. Excluding it keeps dev
      // reloads stable instead of rewriting those chunk filenames under us.
      exclude: ["@radix-ui/react-dialog"],
    },
    build: {
      outDir: path.resolve(__dirname, "../nanobot/web/dist"),
      emptyOutDir: true,
      sourcemap: false,
    },
    server: {
      host: "127.0.0.1",
      port,
      strictPort: true,
      // Move Vite's HMR socket to a dedicated port so it doesn't collide with
      // the ``/`` proxy below (Vite HMR and the nanobot ws upgrade both sit on
      // the root path, which triggers spurious write-after-end errors as each
      // side tries to close the other's socket).
      hmr: {
        host: "127.0.0.1",
        port: hmrPort,
      },
      proxy: {
        "/webui": { target, changeOrigin: true },
        "/api": { target, changeOrigin: true },
        "/auth": { target, changeOrigin: true },
        // Forward only WebSocket upgrades on ``/`` to the nanobot gateway;
        // plain HTTP GETs on ``/`` must stay with Vite so it can serve the SPA.
        // ``bypass`` returning the original URL skips the proxy for that
        // request; returning undefined lets the proxy (and ws upgrade handler)
        // take it.
        "/": {
          target: wsTarget,
          ws: true,
          changeOrigin: true,
          bypass: (req) =>
            req.headers.upgrade === "websocket" ? undefined : req.url,
        },
      },
    },
    test: {
      environment: "happy-dom",
      globals: true,
      setupFiles: ["./src/tests/setup.ts"],
    },
  };
});
