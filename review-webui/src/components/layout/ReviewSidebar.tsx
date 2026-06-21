import { useRef } from "react";
import { Plus, MessageSquare, Trash2, Loader2, Github } from "lucide-react";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import type { ChatSummary, ReviewFocus } from "@/lib/types";

export interface ReviewSidebarProps {
  autoTaskSessions: ChatSummary[];
  dailySessions: ChatSummary[];
  activeKey: string | null;
  loading: boolean;
  error?: string | null;
  onSelect: (key: string) => void;
  onNewTask: () => void;
  onDelete: (key: string) => void;
  onOpenAutoTasks: () => void;
}

function formatRelativeTime(dateStr: string | null): string {
  if (!dateStr) return "";
  const date = new Date(dateStr);
  const now = new Date();
  const diffMs = now.getTime() - date.getTime();
  const diffSec = Math.floor(diffMs / 1000);
  const diffMin = Math.floor(diffSec / 60);
  const diffHr = Math.floor(diffMin / 60);
  const diffDay = Math.floor(diffHr / 24);

  if (diffSec < 60) return "just now";
  if (diffMin < 60) return `${diffMin}m ago`;
  if (diffHr < 24) return `${diffHr}h ago`;
  if (diffDay < 7) return `${diffDay}d ago`;
  return date.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

function shortReviewTarget(target: string | undefined): string {
  const trimmed = target?.trim();
  if (!trimmed) return "";
  try {
    const url = new URL(trimmed);
    if (url.hostname.toLowerCase().endsWith("github.com")) {
      const parts = url.pathname.split("/").filter(Boolean);
      if (parts.length >= 2) return `${parts[0]}/${parts[1]}`;
    }
    const tail = url.pathname.split(/[\\/]/).filter(Boolean).pop();
    return tail || url.hostname || trimmed;
  } catch {
    const normalized = trimmed.replace(/[\\/]+$/, "");
    return normalized.split(/[\\/]/).filter(Boolean).pop() || normalized;
  }
}

function reviewListField(metadata: Record<string, unknown> | undefined, key: string): string[] {
  const value = metadata?.[key];
  if (!Array.isArray(value)) return [];
  return value.filter((item): item is string => typeof item === "string" && item.trim().length > 0);
}

function reviewFocusLabel(focus: ReviewFocus[] | string[]): string {
  return focus.length > 0 ? focus.join(", ") : "";
}

function TaskItem({
  task,
  isActive,
  autoTask,
  onSelect,
  onDelete,
}: {
  task: ChatSummary;
  isActive: boolean;
  autoTask?: boolean;
  onSelect: () => void;
  onDelete: () => void;
}) {
  const deleteBtnRef = useRef<HTMLButtonElement>(null);
  const isReview = !!task.reviewTarget;
  const reviewFocus = reviewListField(task.metadata, "review_focus");
  const reviewPaths = reviewListField(task.metadata, "review_target_paths");
  const reviewSource = [
    task.reviewAction,
    task.reviewMode,
    task.reviewTargetType,
    reviewFocusLabel(reviewFocus),
    reviewPaths.length > 0 ? `${reviewPaths.length} path${reviewPaths.length === 1 ? "" : "s"}` : "",
  ].filter(Boolean).join(" · ");
  const title = isReview
    ? shortReviewTarget(task.reviewTarget) || "Review"
    : task.title
    || (autoTask && task.githubRepo && task.githubPrNumber
      ? `${task.githubRepo} PR #${task.githubPrNumber}`
      : autoTask
        ? "AutoTask Review"
        : "Untitled Review");
  const source = isReview
    ? reviewSource
    : autoTask
    ? [
        task.githubRepo,
        task.githubPrNumber ? `PR #${task.githubPrNumber}` : null,
        task.reviewMode,
        task.reviewAction,
      ].filter(Boolean).join(" - ")
    : "";
  const preview = isReview ? (task.reviewTarget || "") : (task.preview || "");

  return (
    <div
      role="button"
      tabIndex={0}
      onClick={onSelect}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onSelect();
        }
      }}
      className={cn(
        "group relative flex w-full flex-col gap-0.5 rounded-md px-2.5 py-2 text-left transition-colors cursor-pointer",
        "hover:bg-secondary/60",
        isActive && "bg-secondary",
      )}
    >
      {/* Active indicator bar */}
      {isActive && (
        <span className="absolute left-0 top-1/2 h-4 w-[2px] -translate-y-1/2 rounded-r-full bg-primary" />
      )}

      {/* Title row */}
      <div className="flex items-start justify-between gap-1.5">
        <span
          className={cn(
            "line-clamp-1 text-xs font-medium leading-snug",
            isActive ? "text-foreground" : "text-foreground/80",
          )}
        >
          {title}
        </span>

        {/* Delete button, visible on hover. */}
        <button
          ref={deleteBtnRef}
          type="button"
          onClick={(e) => {
            e.stopPropagation();
            onDelete();
          }}
          className={cn(
            "shrink-0 flex h-5 w-5 items-center justify-center rounded-md",
            "text-muted-foreground/0 opacity-0 transition-all",
            "group-hover:text-muted-foreground group-hover:opacity-100",
            "hover:bg-destructive/10 hover:text-destructive",
          )}
          aria-label="Delete task"
        >
          <Trash2 className="h-3 w-3" />
        </button>
      </div>

      {source ? (
        <p className="line-clamp-1 text-[11px] font-medium text-muted-foreground leading-relaxed">
          {source}
        </p>
      ) : null}

      <p className="line-clamp-1 text-[11px] text-muted-foreground leading-relaxed">
        {preview}
      </p>

      {/* Relative time */}
      {task.updatedAt && (
        <span className="text-[10px] text-muted-foreground/60">
          {formatRelativeTime(task.updatedAt)}
        </span>
      )}
    </div>
  );
}

function SessionSection({
  title,
  tasks,
  activeKey,
  loading,
  emptyTitle,
  emptyDescription,
  autoTask,
  onSelect,
  onDelete,
}: {
  title: string;
  tasks: ChatSummary[];
  activeKey: string | null;
  loading?: boolean;
  emptyTitle: string;
  emptyDescription: string;
  autoTask?: boolean;
  onSelect: (key: string) => void;
  onDelete: (key: string) => void;
}) {
  return (
    <section className="mb-3">
      <div className="mb-1 flex items-center justify-between px-1.5">
        <h3 className="text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
          {title}
        </h3>
      </div>
      {loading ? (
        <div className="flex items-center gap-1.5 px-2 py-2 text-[11px] text-muted-foreground">
          <Loader2 className="h-3 w-3 animate-spin" />
          Loading...
        </div>
      ) : tasks.length === 0 ? (
        <div className="px-2 py-2 text-[11px] text-muted-foreground/60">
          <p className="font-medium">{emptyTitle}</p>
          <p className="mt-0.5 leading-relaxed">{emptyDescription}</p>
        </div>
      ) : (
        <div className="flex flex-col gap-0">
          {tasks.map((task) => (
            <TaskItem
              key={task.key}
              task={task}
              autoTask={autoTask}
              isActive={task.key === activeKey}
              onSelect={() => onSelect(task.key)}
              onDelete={() => onDelete(task.key)}
            />
          ))}
        </div>
      )}
    </section>
  );
}

export function ReviewSidebar({
  autoTaskSessions,
  dailySessions,
  activeKey,
  loading,
  error,
  onSelect,
  onNewTask,
  onDelete,
  onOpenAutoTasks,
}: ReviewSidebarProps) {
  return (
    <aside className="flex h-full w-[220px] shrink-0 flex-col border-r bg-[hsl(var(--sidebar))]">
      {/* Header */}
      <div className="flex items-center justify-between px-3 py-2">
        <h2 className="text-xs font-semibold tracking-tight text-[hsl(var(--sidebar-foreground))]">
          Review Tasks
        </h2>
        <div className="flex items-center gap-0.5">
          <Button
            variant="ghost"
            size="icon"
            className="h-6 w-6"
            onClick={onOpenAutoTasks}
            aria-label="Open AutoTask rules"
            title="Open AutoTask rules"
          >
            <Github className="h-3.5 w-3.5" />
          </Button>
          <Button
            variant="ghost"
            size="icon"
            className="h-6 w-6"
            onClick={onNewTask}
            aria-label="New review task"
            title="New review task"
          >
            <Plus className="h-3.5 w-3.5" />
          </Button>
        </div>
      </div>

      {/* Divider */}
      <div className="mx-2 h-px bg-[hsl(var(--sidebar-border))]" />

      {/* Task list */}
      <ScrollArea className="flex-1 px-1.5 py-1">
        {error ? (
          <div className="px-3 py-4 text-xs text-destructive">
            {error}
          </div>
        ) : (
          <>
            <SessionSection
              title="AutoTask"
              tasks={autoTaskSessions}
              activeKey={activeKey}
              loading={loading}
              emptyTitle="No AutoTask sessions"
              emptyDescription="GitHub PR auto reviews will appear here after a rule runs."
              autoTask
              onSelect={onSelect}
              onDelete={onDelete}
            />
            <SessionSection
              title="日常任务"
              tasks={dailySessions}
              activeKey={activeKey}
              loading={loading}
              emptyTitle="No daily sessions"
              emptyDescription="Click + to start a manual review."
              onSelect={onSelect}
              onDelete={onDelete}
            />
            {!loading && autoTaskSessions.length === 0 && dailySessions.length === 0 ? (
              <div className="flex flex-col items-center justify-center py-10 text-muted-foreground/50">
                <MessageSquare className="h-7 w-7 stroke-[1.5]" />
              </div>
            ) : null}
          </>
        )}
      </ScrollArea>
    </aside>
  );
}
