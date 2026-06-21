import type { BootstrapResponse } from "./types";

const SECRET_STORAGE_KEY = "nanobot-review-webui.bootstrap-secret";

export function loadSavedSecret(): string {
  if (typeof window === "undefined") return "";
  try {
    return window.localStorage.getItem(SECRET_STORAGE_KEY) ?? "";
  } catch {
    return "";
  }
}

export function saveSecret(secret: string): void {
  try {
    window.localStorage.setItem(SECRET_STORAGE_KEY, secret);
  } catch {
    // ignore
  }
}

export function clearSavedSecret(): void {
  try {
    window.localStorage.removeItem(SECRET_STORAGE_KEY);
  } catch {
    // ignore
  }
}

export async function fetchBootstrap(
  baseUrl: string = "",
  secret: string = "",
): Promise<BootstrapResponse> {
  const headers: Record<string, string> = {};
  if (secret) {
    headers["X-Nanobot-Auth"] = secret;
  }
  const res = await fetch(`${baseUrl}/webui/bootstrap`, {
    method: "GET",
    credentials: "same-origin",
    headers,
  });
  if (!res.ok) {
    throw new Error(`bootstrap failed: HTTP ${res.status}`);
  }
  const body = (await res.json()) as BootstrapResponse;
  if (!body.token || !body.ws_path) {
    throw new Error("bootstrap response missing token or ws_path");
  }
  return body;
}

export function deriveWsUrl(wsPath: string, token: string): string {
  const path = wsPath && wsPath.startsWith("/") ? wsPath : `/${wsPath || ""}`;
  const query = `?token=${encodeURIComponent(token)}`;
  if (typeof window === "undefined") {
    return `ws://127.0.0.1:8765${path}${query}`;
  }
  const scheme = window.location.protocol === "https:" ? "wss" : "ws";
  const host = window.location.host;
  return `${scheme}://${host}${path}${query}`;
}
