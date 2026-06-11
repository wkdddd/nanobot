import { useEffect, useState } from "react";
import { ChevronRight, ShieldAlert, ShieldCheck, ShieldX } from "lucide-react";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import type { PermissionRequest } from "@/lib/types";

const TIMEOUT_S = 300;

interface PermissionCardProps {
  records: PermissionRequest[];
  onRespond: (requestId: string, approved: boolean) => void;
}

export function PermissionCard({ records, onRespond }: PermissionCardProps) {
  const { t } = useTranslation();
  const [collapsed, setCollapsed] = useState(false);
  const pending = records.filter((r) => !r.resolved);
  const resolved = records.filter((r) => r.resolved);
  const allResolved = pending.length === 0 && resolved.length > 0;

  const headerIcon = allResolved
    ? <ShieldCheck className="h-3.5 w-3.5 text-emerald-600 dark:text-emerald-400" />
    : <ShieldAlert className="h-3.5 w-3.5 text-amber-600" />;

  const summary = allResolved
    ? t("permission.recordCount", { count: resolved.length })
    : t("permission.panelTitle");

  return (
    <div className="w-full animate-in fade-in-0 duration-200">
      <button
        type="button"
        onClick={() => setCollapsed((v) => !v)}
        className={cn(
          "group flex w-full items-center gap-2 rounded-md px-2 py-1.5",
          "text-xs text-muted-foreground transition-colors hover:bg-muted/45",
        )}
      >
        {headerIcon}
        <span className="font-medium">{summary}</span>
        {pending.length > 0 && (
          <span className="rounded-full bg-amber-500/20 px-1.5 py-0.5 text-[10px] font-semibold text-amber-700 dark:text-amber-300">
            {pending.length}
          </span>
        )}
        <ChevronRight
          aria-hidden
          className={cn(
            "ml-auto h-3.5 w-3.5 transition-transform duration-200",
            !collapsed && "rotate-90",
          )}
        />
      </button>

      {!collapsed && (
        <div className={cn(
          "mt-1 space-y-1.5 border-l pl-3",
          allResolved ? "border-emerald-500/40" : "border-amber-500/60",
          "animate-in fade-in-0 slide-in-from-top-1 duration-200",
        )}>
          {pending.map((record) => (
            <PendingRow key={record.requestId} record={record} onRespond={onRespond} />
          ))}
          {resolved.map((record) => (
            <ResolvedRow key={record.requestId} record={record} />
          ))}
        </div>
      )}
    </div>
  );
}

function ResolvedRow({ record }: { record: PermissionRequest }) {
  const { t } = useTranslation();
  const commandPreview = record.arguments?.command;
  return (
    <div className="flex items-center gap-1.5 py-0.5 text-xs text-muted-foreground">
      {record.approved ? (
        <ShieldCheck className="h-3 w-3 text-emerald-600 dark:text-emerald-400" />
      ) : (
        <ShieldX className="h-3 w-3 text-red-500 dark:text-red-400" />
      )}
      <span>{record.approved ? t("permission.approved") : t("permission.denied")}</span>
      <code className="max-w-[240px] truncate text-[11px] text-muted-foreground/70">
        {typeof commandPreview === "string" ? commandPreview : record.toolName}
      </code>
    </div>
  );
}

function PendingRow({
  record,
  onRespond,
}: {
  record: PermissionRequest;
  onRespond: (requestId: string, approved: boolean) => void;
}) {
  const { t } = useTranslation();
  const [remaining, setRemaining] = useState(() => {
    const elapsed = Math.floor((Date.now() - record.createdAt) / 1000);
    return Math.max(0, TIMEOUT_S - elapsed);
  });

  useEffect(() => {
    if (remaining <= 0) return;
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
  }, [record.requestId, remaining, onRespond]);

  const commandPreview = record.arguments?.command;

  return (
    <div className="rounded-lg border border-amber-200 bg-amber-50/60 p-2.5 dark:border-amber-800/60 dark:bg-amber-950/20">
      <div className="mb-1.5 flex items-center gap-2">
        <ShieldAlert className="h-3.5 w-3.5 text-amber-600" />
        <span className="text-xs font-medium text-foreground">
          {t("permission.title")}
        </span>
        <span className={cn(
          "ml-auto text-[11px] tabular-nums",
          remaining <= 30 ? "font-medium text-red-500" : "text-muted-foreground",
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
      <div className="flex justify-end gap-2">
        <Button
          variant="outline"
          size="sm"
          className="h-6 px-2 text-xs"
          onClick={() => onRespond(record.requestId, false)}
        >
          {t("permission.deny")}
        </Button>
        <Button
          variant="default"
          size="sm"
          className="h-6 px-2 text-xs"
          onClick={() => onRespond(record.requestId, true)}
        >
          {t("permission.approve")}
        </Button>
      </div>
    </div>
  );
}