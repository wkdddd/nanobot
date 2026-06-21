import { useState, useCallback, useEffect, useRef } from "react";
import { deleteSession, listSessions, type ApiAuth } from "@/lib/api";
import type { ChatSummary } from "@/lib/types";
import { useClient } from "@/providers/ClientProvider";

export function useReviewTasks() {
  const { client, token, refreshAuth } = useClient();
  const [tasks, setTasks] = useState<ChatSummary[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const tokenRef = useRef(token);
  const refreshAuthRef = useRef(refreshAuth);
  tokenRef.current = token;
  refreshAuthRef.current = refreshAuth;

  const auth = useCallback(
    (): ApiAuth => ({
      token: tokenRef.current,
      refreshAuth: refreshAuthRef.current,
    }),
    [],
  );

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setTasks(await listSessions(auth()));
    } catch (err) {
      const message = err instanceof Error ? err.message : "Failed to load sessions";
      console.error("Failed to load review sessions", err);
      setError(message);
    } finally {
      setLoading(false);
    }
  }, [auth]);

  useEffect(() => {
    refresh();
    const unsub = client.onSessionUpdate(() => {
      refresh();
    });
    return unsub;
  }, [client, refresh]);

  const createTask = useCallback(async (): Promise<string> => {
    const chatId = await client.newChat();
    const key = `websocket:${chatId}`;
    setTasks((prev) => [
      {
        key,
        channel: "websocket",
        chatId,
        createdAt: new Date().toISOString(),
        updatedAt: new Date().toISOString(),
        title: "",
        preview: "",
      },
      ...prev.filter((task) => task.key !== key),
    ]);
    return chatId;
  }, [client]);

  const deleteTask = useCallback(async (key: string) => {
    try {
      await deleteSession(auth(), key);
      setTasks((prev) => prev.filter((task) => task.key !== key));
      setError(null);
    } catch (err) {
      const message = err instanceof Error ? err.message : "Failed to delete session";
      console.error("Failed to delete review session", err);
      setError(message);
      throw err;
    }
  }, [auth]);

  return { tasks, loading, error, refresh, createTask, deleteTask };
}
