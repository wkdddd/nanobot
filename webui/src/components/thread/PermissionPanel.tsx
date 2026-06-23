import { useCallback, useEffect, useRef, useState } from "react";
import { ChevronDown, ChevronRight, ShieldAlert, ShieldCheck, ShieldX } from "lucide-react";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import type { PermissionRequest } from "@/lib/types";

const COLLAPSE_DELAY_MS = 3000;
const TIMEOUT_S = 300;

interface PermissionPanelProps {
  records: PermissionRequest[];
  isStreaming: boolean;
  onRespond: (requestId: string, approved: boolean) => void;
}

export function PermissionPanel({ records, isStreaming, onRespond }: PermissionPanelProps) {
  const { t } = useTranslation();
  const [collapsed, setCollapsed] = useState(false);
  const collapseLockedUntilRef = useRef<number>(0);
  const hasPending = records.some((r) => !r.resolved);
  const totalCount = records.length;

  // PLACEHOLDER_REST

  // Force expand when pending requests arrive
  useEffect(() => {
    if (hasPending) {
      setCollapsed(false);
      collapseLockedUntilRef.current = Date.now() + COLLAPSE_DELAY_MS;
    }
  }, [hasPending, totalCount]);

  // Auto-collapse when stream ends and no pending
  useEffect(() => {
    if (!isStreaming && !hasPending && totalCount > 0) {
      const timer = setTimeout(() => setCollapsed(true), COLLAPSE_DELAY_MS);
      return () => clearTimeout(timer);
    }
  }, [isStreaming, hasPending, totalCount]);

  const handleCollapse = useCallback(() => {
    if (hasPending) return;
    if (Date.now() < collapseLockedUntilRef.current) return;
    setCollapsed(true);
  }, [hasPending]);

  if (totalCount === 0) return null;

  const pendingCount = records.filter((r) => !r.resolved).length;

  if (collapsed) {
    return (
      <button
        type="button"
        onClick={() => setCollapsed(false)}
        className="mx-4 mb-2 flex items-center gap-1.5 rounded-lg border border-border/50 bg-muted/40 px-3 py-1.5 text-xs text-muted-foreground transition-colors hover:bg-muted/70"
      >
        <ChevronRight className="h-3 w-3" />
        <ShieldAlert className="h-3 w-3" />
        <span>
          {t("permission.recordCount", { count: totalCount })}
        </span>
      </button>
    );
  }

  return (
    <div className="mx-4 mb-2 rounded-xl border border-border/50 bg-card/80 backdrop-blur-sm">
      <button
        type="button"
        onClick={handleCollapse}
        disabled={hasPending}
        className={cn(
          "flex w-full items-center gap-2 px-3 py-2 text-xs font-medium",
          hasPending ? "cursor-default" : "cursor-pointer hover:bg-muted/40",
        )}
      >
        <ChevronDown className="h-3 w-3 text-muted-foreground" />
        <ShieldAlert className="h-3.5 w-3.5 text-amber-600" />
        <span className="text-foreground">
          {t("permission.panelTitle")}
        </span>
        {pendingCount > 0 && (
          <span className="ml-auto rounded-full bg-amber-500/15 px-1.5 py-0.5 text-[10px] font-semibold text-amber-700 dark:text-amber-300">
            {pendingCount}
          </span>
        )}
      </button>
      <div className="max-h-[240px] overflow-y-auto border-t border-border/30 px-3 py-1.5">
        {records.map((record) => (
          <PermissionRow key={record.requestId} record={record} onRespond={onRespond} />
        ))}
      </div>
    </div>
  );
}

// PLACEHOLDER_ROW

function PermissionRow({
  record,
  onRespond,
}: {
  record: PermissionRequest;
  onRespond: (requestId: string, approved: boolean) => void;
}) {
  const { t } = useTranslation();
  const [remaining, setRemaining] = useState(() => {
    if (record.resolved) return 0;
    const elapsed = Math.floor((Date.now() - record.createdAt) / 1000);
    return Math.max(0, TIMEOUT_S - elapsed);
  });

  useEffect(() => {
    if (record.resolved || remaining <= 0) return;
    const interval = setInterval(() => {
      setRemaining((prev) => {
        if (prev <= 1) {
          clearInterval(interval);
          onRespond(record.requestId, false);
          return 0;
        }
        return prev - 1;
      });
    }, 1000);
    return () => clearInterval(interval);
  }, [record.resolved, record.requestId, remaining, onRespond]);

  const commandPreview = record.arguments?.command;

  if (record.resolved) {
    return (
      <div className="flex items-center gap-1.5 py-1 text-xs text-muted-foreground">
        {record.approved ? (
          <ShieldCheck className="h-3 w-3 text-emerald-600 dark:text-emerald-400" />
        ) : (
          <ShieldX className="h-3 w-3 text-red-500 dark:text-red-400" />
        )}
        <span>
          {record.approved ? t("permission.approved") : t("permission.denied")}:
        </span>
        <code className="max-w-[300px] truncate text-[11px]">
          {typeof commandPreview === "string" ? commandPreview : record.toolName}
        </code>
      </div>
    );
  }

  return (
    <div className="my-1 rounded-lg border border-amber-200 bg-amber-50/60 p-2.5 dark:border-amber-800/60 dark:bg-amber-950/20">
      <div className="mb-1.5 flex items-center gap-2">
        <ShieldAlert className="h-3.5 w-3.5 text-amber-600" />
        <span className="text-xs font-medium text-foreground">
          {t("permission.title")}
        </span>
        <span className={cn(
          "ml-auto text-[11px] tabular-nums",
          remaining <= 30 ? "text-red-500 font-medium" : "text-muted-foreground",
        )}>
          {remaining}s
        </span>
      </div>
      <p className="mb-1.5 text-xs text-muted-foreground">
        <code className="rounded bg-muted px-1 py-0.5 text-[11px]">{record.toolName}</code>
      </p>
      {typeof commandPreview === "string" && commandPreview && (
        <code className="mb-2 block max-h-[60px] overflow-hidden whitespace-pre-wrap break-all rounded bg-muted p-1.5 text-[11px]">
          {commandPreview}
        </code>
      )}
      <div className="flex gap-2 justify-end">
        <Button variant="outline" size="sm" className="h-6 px-2 text-xs" onClick={() => onRespond(record.requestId, false)}>
          {t("permission.deny")}
        </Button>
        <Button variant="default" size="sm" className="h-6 px-2 text-xs" onClick={() => onRespond(record.requestId, true)}>
          {t("permission.approve")}
        </Button>
      </div>
    </div>
  );
}