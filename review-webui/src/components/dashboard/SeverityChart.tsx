import { useMemo } from "react";
import { cn } from "@/lib/utils";
import type { Finding } from "@/hooks/useReviewSession";

interface SeverityChartProps {
  findings: Finding[];
}

const SEVERITY_CONFIG = [
  { key: "critical", label: "Critical", colorClass: "bg-[hsl(var(--severity-critical))]" },
  { key: "high", label: "High", colorClass: "bg-[hsl(var(--severity-high))]" },
  { key: "medium", label: "Medium", colorClass: "bg-[hsl(var(--severity-medium))]" },
  { key: "low", label: "Low", colorClass: "bg-[hsl(var(--severity-low))]" },
] as const;

export function SeverityChart({ findings }: SeverityChartProps) {
  const total = findings.length;

  const segments = useMemo(() => {
    if (total === 0) return [];

    return SEVERITY_CONFIG.map((seg) => {
      const count = findings.filter((f) => f.severity === seg.key).length;
      const percentage = total > 0 ? (count / total) * 100 : 0;
      return { ...seg, count, percentage };
    }).filter((s) => s.count > 0);
  }, [findings, total]);

  if (total === 0) {
    return (
      <div className="text-sm text-muted-foreground italic py-4 text-center">
        No findings to display.
      </div>
    );
  }

  return (
    <div className="space-y-3">
      {/* Stacked bar */}
      <div className="flex h-8 rounded-lg overflow-hidden bg-muted">
        {segments.map((seg) => (
          <div
            key={seg.key}
            className={cn(
              seg.colorClass,
              "transition-all duration-500 relative group",
            )}
            style={{ width: `${seg.percentage}%` }}
          >
            {/* Tooltip on hover */}
            {seg.percentage > 8 && (
              <span className="absolute inset-0 flex items-center justify-center text-[11px] font-semibold text-white drop-shadow-sm">
                {seg.count}
              </span>
            )}
          </div>
        ))}
      </div>

      {/* Legend */}
      <div className="flex flex-wrap gap-4">
        {segments.map((seg) => (
          <div key={seg.key} className="flex items-center gap-1.5">
            <span
              className={cn(
                "w-3 h-3 rounded-sm flex-shrink-0",
                seg.colorClass,
              )}
            />
            <span className="text-xs text-muted-foreground">
              {seg.label}
            </span>
            <span className="text-xs font-semibold">
              {seg.count}
            </span>
            <span className="text-xs text-muted-foreground">
              ({seg.percentage.toFixed(1)}%)
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}
