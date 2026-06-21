import { useMemo, useState } from "react";
import {
  Check,
  Download,
  Github,
  Loader2,
  Play,
  Plus,
  Trash2,
  X,
} from "lucide-react";
import { autoTaskReportUrl } from "@/lib/api";
import { parseTargetPathsText } from "@/lib/target-paths";
import { cn } from "@/lib/utils";
import type { AutoTask, AutoTaskPayload, AutoTaskRun, ReviewDepth, ReviewFocus } from "@/lib/types";
import { useClient } from "@/providers/ClientProvider";
import { useAutoTasks } from "@/hooks/useAutoTasks";
import { ReviewConfig } from "@/components/review/ReviewConfig";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

function formatTime(value?: string | null): string {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString();
}

function isGitHubRepo(value: string): boolean {
  const repo = value.trim();
  if (!repo) return false;
  const shorthand = /^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/;
  const url = /^https?:\/\/(?:www\.)?github\.com\/[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+(?:\.git)?\/?$/;
  const hostPath = /^github\.com\/[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+(?:\.git)?\/?$/;
  return shorthand.test(repo) || url.test(repo) || hostPath.test(repo);
}

function TaskForm({
  onSubmit,
  submitting,
}: {
  onSubmit: (payload: AutoTaskPayload) => Promise<void>;
  submitting: boolean;
}) {
  const [name, setName] = useState("");
  const [repo, setRepo] = useState("");
  const [mode, setMode] = useState<ReviewDepth>("full");
  const [focus, setFocus] = useState<ReviewFocus[]>([]);
  const [targetPaths, setTargetPaths] = useState("");
  const [enabled, setEnabled] = useState(true);
  const [repoError, setRepoError] = useState<string | null>(null);

  const submit = async () => {
    if (!repo.trim() || submitting) return;
    if (!isGitHubRepo(repo)) {
      setRepoError("Use owner/repo or a github.com repository URL.");
      return;
    }
    await onSubmit({
      name: name.trim() || repo.trim(),
      repo: repo.trim(),
      enabled,
      mode,
      focus: focus.length ? focus : null,
      target_paths: parseTargetPathsText(targetPaths),
    });
    setName("");
    setRepo("");
    setMode("full");
    setFocus([]);
    setTargetPaths("");
    setEnabled(true);
    setRepoError(null);
  };

  return (
    <div className="border-b bg-card/40 px-5 py-4">
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <span className="inline-flex items-center gap-1.5 rounded border bg-background px-2.5 py-1 text-xs font-medium text-muted-foreground">
          <Github className="h-3.5 w-3.5" />
          GitHub PR only
        </span>
        <span className="rounded border bg-background px-2.5 py-1 text-xs font-medium text-muted-foreground">
          Action: diff
        </span>
      </div>
      <div className="grid gap-3 lg:grid-cols-[1fr_1fr_auto_auto]">
        <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="Task name" />
        <div>
          <Input
            value={repo}
            onChange={(e) => {
              setRepo(e.target.value);
              if (repoError) setRepoError(null);
            }}
            placeholder="owner/repo or https://github.com/owner/repo"
            aria-invalid={!!repoError}
          />
          {repoError ? (
            <p className="mt-1 text-xs text-destructive">{repoError}</p>
          ) : null}
        </div>
        <label className="inline-flex h-10 items-center gap-2 rounded border px-3 text-xs font-medium text-muted-foreground">
          <input
            type="checkbox"
            checked={enabled}
            onChange={(e) => setEnabled(e.target.checked)}
            className="h-3.5 w-3.5"
          />
          Enabled
        </label>
        <Button onClick={submit} disabled={!repo.trim() || submitting} className="gap-2">
          {submitting ? <Loader2 className="h-4 w-4 animate-spin" /> : <Plus className="h-4 w-4" />}
          Add
        </Button>
      </div>
      <div className="mt-4">
        <ReviewConfig
          depth={mode}
          onDepthChange={setMode}
          focus={focus}
          onFocusChange={setFocus}
          targetPaths={targetPaths}
          onTargetPathsChange={setTargetPaths}
        />
      </div>
    </div>
  );
}

function RunNow({ onRun }: { onRun: (prNumber: number) => Promise<void> }) {
  const [value, setValue] = useState("");
  const [running, setRunning] = useState(false);
  const prNumber = Number.parseInt(value, 10);
  return (
    <div className="flex items-center gap-2">
      <Input
        value={value}
        onChange={(e) => setValue(e.target.value)}
        placeholder="PR #"
        className="h-8 w-24"
      />
      <Button
        size="sm"
        variant="outline"
        className="gap-1.5"
        disabled={!Number.isFinite(prNumber) || prNumber <= 0 || running}
        onClick={async () => {
          setRunning(true);
          try {
            await onRun(prNumber);
            setValue("");
          } finally {
            setRunning(false);
          }
        }}
      >
        {running ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Play className="h-3.5 w-3.5" />}
        Run
      </Button>
    </div>
  );
}

function Reports({ task, runs }: { task: AutoTask; runs: AutoTaskRun[] }) {
  const { token } = useClient();
  if (!runs.length) {
    return <p className="text-xs text-muted-foreground">No reports yet.</p>;
  }
  return (
    <div className="divide-y rounded border">
      {runs.map((run) => (
        <div key={run.run_id} className="grid gap-2 px-3 py-2 text-xs md:grid-cols-[1fr_auto_auto] md:items-center">
          <div className="min-w-0">
            <div className="truncate font-medium">
              PR #{run.pr_number}{run.pr_title ? ` - ${run.pr_title}` : ""}
            </div>
            <div className="mt-0.5 text-muted-foreground">
              {formatTime(run.started_at)} - {run.status}
              {run.reason ? ` - ${run.reason}` : ""}
            </div>
          </div>
          {run.session_key ? (
            <span className="text-muted-foreground">{run.session_key}</span>
          ) : <span />}
          {run.status === "completed" && run.report_available ? (
            <a href={autoTaskReportUrl(task.id, run.run_id, token)}>
              <Button size="sm" variant="outline" className="h-8 gap-1.5">
                <Download className="h-3.5 w-3.5" />
                Download
              </Button>
            </a>
          ) : (
            <span className="text-muted-foreground">No report</span>
          )}
        </div>
      ))}
    </div>
  );
}

function TaskRow({
  task,
  runs,
  onToggle,
  onDelete,
  onRunNow,
}: {
  task: AutoTask;
  runs: AutoTaskRun[];
  onToggle: () => Promise<void>;
  onDelete: () => Promise<void>;
  onRunNow: (prNumber: number) => Promise<void>;
}) {
  return (
    <div className="rounded border bg-background">
      <div className="flex flex-col gap-3 p-4 lg:flex-row lg:items-start lg:justify-between">
        <div>
          <div className="flex items-center gap-2">
            <Github className="h-4 w-4 text-muted-foreground" />
            <h3 className="font-medium">{task.name}</h3>
            <span className={cn("rounded px-2 py-0.5 text-xs", task.enabled ? "bg-emerald-500/10 text-emerald-700" : "bg-muted text-muted-foreground")}>
              {task.enabled ? "enabled" : "disabled"}
            </span>
          </div>
          <p className="mt-1 text-sm text-muted-foreground">{task.repo}</p>
          <p className="mt-1 text-xs text-muted-foreground">
            Last run: {formatTime(task.last_run_at) || "never"}{task.last_status ? ` - ${task.last_status}` : ""}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <RunNow onRun={onRunNow} />
          <Button size="sm" variant="outline" onClick={onToggle} className="gap-1.5">
            {task.enabled ? <X className="h-3.5 w-3.5" /> : <Check className="h-3.5 w-3.5" />}
            {task.enabled ? "Disable" : "Enable"}
          </Button>
          <Button size="sm" variant="ghost" onClick={onDelete} className="gap-1.5 text-destructive">
            <Trash2 className="h-3.5 w-3.5" />
            Delete
          </Button>
        </div>
      </div>
      <div className="border-t p-4">
        <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">Reports</div>
        <Reports task={task} runs={runs} />
      </div>
    </div>
  );
}

export function AutoTasksView() {
  const { tasks, runsByTask, loading, error, actions } = useAutoTasks();
  const [submitting, setSubmitting] = useState(false);
  const sortedTasks = useMemo(() => tasks, [tasks]);

  return (
    <div className="flex h-full flex-col overflow-hidden bg-background">
      <div className="border-b px-5 py-4">
        <h1 className="text-lg font-semibold">GitHub Auto Tasks</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Automatically review GitHub pull request diffs from webhook events.
        </p>
      </div>
      <TaskForm
        submitting={submitting}
        onSubmit={async (payload) => {
          setSubmitting(true);
          try {
            await actions.create(payload);
          } finally {
            setSubmitting(false);
          }
        }}
      />
      <div className="flex-1 overflow-y-auto p-5">
        {error ? <p className="text-sm text-destructive">{error}</p> : null}
        {loading ? (
          <div className="flex items-center gap-2 text-sm text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin" />
            Loading auto tasks...
          </div>
        ) : sortedTasks.length === 0 ? (
          <div className="rounded border border-dashed p-8 text-center text-sm text-muted-foreground">
            No auto tasks yet.
          </div>
        ) : (
          <div className="flex flex-col gap-4">
            {sortedTasks.map((task) => (
              <TaskRow
                key={task.id}
                task={task}
                runs={runsByTask[task.id] ?? []}
                onToggle={() => actions.update(task.id, { enabled: !task.enabled, repo: task.repo })}
                onDelete={() => actions.remove(task.id)}
                onRunNow={(prNumber) => actions.runNow(task.id, prNumber)}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
