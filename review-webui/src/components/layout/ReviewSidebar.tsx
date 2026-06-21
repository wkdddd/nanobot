import { useRef } from "react";
import { Plus, MessageSquare, Trash2, Loader2 } from "lucide-react";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import type { ChatSummary } from "@/lib/types";

export interface ReviewSidebarProps {
  tasks: ChatSummary[];
  activeKey: string | null;
  loading: boolean;
  error?: string | null;
  onSelect: (key: string) => void;
  onNewTask: () => void;
  onDelete: (key: string) => void;
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

function TaskItem({
  task,
  isActive,
  onSelect,
  onDelete,
}: {
  task: ChatSummary;
  isActive: boolean;
  onSelect: () => void;
  onDelete: () => void;
}) {
  const deleteBtnRef = useRef<HTMLButtonElement>(null);

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
        "group relative flex w-full flex-col gap-1 rounded-lg px-3 py-2.5 text-left transition-colors cursor-pointer",
        "hover:bg-secondary/60",
        isActive && "bg-secondary",
      )}
    >
      {/* Active indicator bar */}
      {isActive && (
        <span className="absolute left-0 top-1/2 h-5 w-[3px] -translate-y-1/2 rounded-r-full bg-primary" />
      )}

      {/* Title row */}
      <div className="flex items-start justify-between gap-2">
        <span
          className={cn(
            "line-clamp-1 text-sm font-medium leading-snug",
            isActive ? "text-foreground" : "text-foreground/80",
          )}
        >
          {task.title || "Untitled Review"}
        </span>

        {/* Delete button — visible on hover */}
        <button
          ref={deleteBtnRef}
          type="button"
          onClick={(e) => {
            e.stopPropagation();
            onDelete();
          }}
          className={cn(
            "shrink-0 flex h-6 w-6 items-center justify-center rounded-md",
            "text-muted-foreground/0 opacity-0 transition-all",
            "group-hover:text-muted-foreground group-hover:opacity-100",
            "hover:bg-destructive/10 hover:text-destructive",
          )}
          aria-label="Delete task"
        >
          <Trash2 className="h-3.5 w-3.5" />
        </button>
      </div>

      {/* Preview */}
      <p className="line-clamp-1 text-xs text-muted-foreground leading-relaxed">
        {task.preview}
      </p>

      {/* Relative time */}
      {task.updatedAt && (
        <span className="text-[11px] text-muted-foreground/60">
          {formatRelativeTime(task.updatedAt)}
        </span>
      )}
    </div>
  );
}

export function ReviewSidebar({
  tasks,
  activeKey,
  loading,
  error,
  onSelect,
  onNewTask,
  onDelete,
}: ReviewSidebarProps) {
  return (
    <aside className="flex h-full w-[260px] shrink-0 flex-col border-r bg-[hsl(var(--sidebar))]">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3">
        <h2 className="text-sm font-semibold tracking-tight text-[hsl(var(--sidebar-foreground))]">
          Review Tasks
        </h2>
        <Button
          variant="ghost"
          size="icon"
          className="h-7 w-7"
          onClick={onNewTask}
          aria-label="New review task"
        >
          <Plus className="h-4 w-4" />
        </Button>
      </div>

      {/* Divider */}
      <div className="mx-3 h-px bg-[hsl(var(--sidebar-border))]" />

      {/* Task list */}
      <ScrollArea className="flex-1 px-2 py-2">
        {error ? (
          <div className="px-3 py-4 text-xs text-destructive">
            {error}
          </div>
        ) : loading ? (
          <div className="flex flex-col items-center justify-center py-12 text-muted-foreground">
            <Loader2 className="h-5 w-5 animate-spin" />
            <span className="mt-2 text-xs">Loading tasks...</span>
          </div>
        ) : tasks.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-16 text-muted-foreground/60">
            <MessageSquare className="h-8 w-8 stroke-[1.5]" />
            <p className="mt-3 text-sm font-medium">No review tasks yet</p>
            <p className="mt-1 text-xs text-muted-foreground/50">
              Click + to start a new review
            </p>
          </div>
        ) : (
          <div className="flex flex-col gap-0.5">
            {tasks.map((task) => (
              <TaskItem
                key={task.key}
                task={task}
                isActive={task.key === activeKey}
                onSelect={() => onSelect(task.key)}
                onDelete={() => onDelete(task.key)}
              />
            ))}
          </div>
        )}
      </ScrollArea>
    </aside>
  );
}
