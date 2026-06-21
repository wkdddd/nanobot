import type { ChatSummary, SessionMessagesPayload, WebuiThreadPersistedPayload } from "./types";

export class ApiError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.status = status;
    this.name = "ApiError";
  }
}

export interface ApiAuth {
  token: string;
  refreshAuth: () => Promise<string | null>;
}

async function fetchWithToken(
  url: string,
  token: string,
  init?: RequestInit,
): Promise<Response> {
  return fetch(url, {
    ...(init ?? {}),
    headers: {
      ...(init?.headers ?? {}),
      Authorization: `Bearer ${token}`,
    },
    credentials: "same-origin",
  });
}

async function request<T>(
  url: string,
  auth: ApiAuth,
  init?: RequestInit,
): Promise<T> {
  let res = await fetchWithToken(url, auth.token, init);
  if (res.status === 401) {
    const refreshed = await auth.refreshAuth();
    if (refreshed) {
      res = await fetchWithToken(url, refreshed, init);
    }
  }
  if (!res.ok) {
    throw new ApiError(res.status, `HTTP ${res.status}`);
  }
  return (await res.json()) as T;
}

function errorMessage(status: number, fallback: string, body?: unknown): string {
  if (body && typeof body === "object") {
    const detail = (body as { detail?: unknown; error?: unknown; message?: unknown }).detail
      ?? (body as { error?: unknown }).error
      ?? (body as { message?: unknown }).message;
    if (typeof detail === "string" && detail.trim()) {
      return `${fallback}: ${detail}`;
    }
  }
  return `${fallback}: HTTP ${status}`;
}

function splitKey(key: string): { channel: string; chatId: string } {
  const idx = key.indexOf(":");
  if (idx === -1) return { channel: "", chatId: key };
  return { channel: key.slice(0, idx), chatId: key.slice(idx + 1) };
}

export async function listSessions(auth: ApiAuth): Promise<ChatSummary[]> {
  type Row = {
    key: string;
    created_at: string | null;
    updated_at: string | null;
    title?: string;
    preview?: string;
  };

  const body = await request<{ sessions: Row[] }>("/api/sessions", auth);
  return body.sessions.map((session) => ({
    key: session.key,
    ...splitKey(session.key),
    createdAt: session.created_at,
    updatedAt: session.updated_at,
    title: session.title ?? "",
    preview: session.preview ?? "",
  }));
}

export async function fetchWebuiThread(
  auth: ApiAuth,
  key: string,
): Promise<WebuiThreadPersistedPayload | null> {
  const url = `/api/sessions/${encodeURIComponent(key)}/webui-thread`;
  let res = await fetchWithToken(url, auth.token);
  if (res.status === 401) {
    const refreshed = await auth.refreshAuth();
    if (refreshed) {
      res = await fetchWithToken(url, refreshed);
    }
  }
  if (res.status === 404) return null;
  if (!res.ok) {
    let body: unknown = null;
    try {
      body = await res.json();
    } catch {
      // Ignore malformed error bodies; status still carries the failure.
    }
    throw new ApiError(res.status, errorMessage(res.status, "Failed to load webui thread", body));
  }
  return (await res.json()) as WebuiThreadPersistedPayload;
}

export async function fetchSessionMessages(
  auth: ApiAuth,
  key: string,
): Promise<SessionMessagesPayload | null> {
  const url = `/api/sessions/${encodeURIComponent(key)}/messages`;
  let res = await fetchWithToken(url, auth.token);
  if (res.status === 401) {
    const refreshed = await auth.refreshAuth();
    if (refreshed) {
      res = await fetchWithToken(url, refreshed);
    }
  }
  if (res.status === 404) return null;
  if (!res.ok) {
    let body: unknown = null;
    try {
      body = await res.json();
    } catch {
      // Ignore malformed error bodies; status still carries the failure.
    }
    throw new ApiError(res.status, errorMessage(res.status, "Failed to load session messages", body));
  }
  return (await res.json()) as SessionMessagesPayload;
}

export async function deleteSession(auth: ApiAuth, key: string): Promise<boolean> {
  const body = await request<{ deleted: boolean }>(
    `/api/sessions/${encodeURIComponent(key)}/delete`,
    auth,
  );
  return body.deleted;
}
