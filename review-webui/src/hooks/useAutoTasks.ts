import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  createAutoTask,
  deleteAutoTask,
  listAutoTaskRuns,
  listAutoTasks,
  runAutoTaskNow,
  updateAutoTask,
  type ApiAuth,
} from "@/lib/api";
import type { AutoTask, AutoTaskPayload, AutoTaskRun } from "@/lib/types";
import { useClient } from "@/providers/ClientProvider";

export function useAutoTasks() {
  const { token, refreshAuth } = useClient();
  const [tasks, setTasks] = useState<AutoTask[]>([]);
  const [runsByTask, setRunsByTask] = useState<Record<string, AutoTaskRun[]>>({});
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const tokenRef = useRef(token);
  const refreshAuthRef = useRef(refreshAuth);
  tokenRef.current = token;
  refreshAuthRef.current = refreshAuth;

  const auth = useCallback(
    (): ApiAuth => ({ token: tokenRef.current, refreshAuth: refreshAuthRef.current }),
    [],
  );

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const nextTasks = await listAutoTasks(auth());
      setTasks(nextTasks);
      const runEntries = await Promise.all(
        nextTasks.map(async (task) => [task.id, await listAutoTaskRuns(auth(), task.id)] as const),
      );
      setRunsByTask(Object.fromEntries(runEntries));
    } catch (err) {
      const message = err instanceof Error ? err.message : "Failed to load auto tasks";
      setError(message);
    } finally {
      setLoading(false);
    }
  }, [auth]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const actions = useMemo(() => ({
    create: async (payload: AutoTaskPayload) => {
      await createAutoTask(auth(), payload);
      await refresh();
    },
    update: async (id: string, payload: Partial<AutoTaskPayload>) => {
      await updateAutoTask(auth(), id, payload);
      await refresh();
    },
    remove: async (id: string) => {
      await deleteAutoTask(auth(), id);
      await refresh();
    },
    runNow: async (id: string, prNumber: number) => {
      await runAutoTaskNow(auth(), id, prNumber);
      await refresh();
    },
  }), [auth, refresh]);

  return { tasks, runsByTask, loading, error, refresh, actions };
}
