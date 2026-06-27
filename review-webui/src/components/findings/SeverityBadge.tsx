import { cn } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";

interface SeverityBadgeProps {
  severity: string;
}

const SEVERITY_STYLES: Record<string, string> = {
  critical: "bg-red-100 text-red-700 border-red-200 dark:bg-red-950 dark:text-red-400 dark:border-red-900",
  high: "bg-orange-100 text-orange-700 border-orange-200 dark:bg-orange-950 dark:text-orange-400 dark:border-orange-900",
  medium: "bg-yellow-100 text-yellow-700 border-yellow-200 dark:bg-yellow-950 dark:text-yellow-400 dark:border-yellow-900",
  low: "bg-blue-100 text-blue-700 border-blue-200 dark:bg-blue-950 dark:text-blue-400 dark:border-blue-900",
};

const SEVERITY_LABELS: Record<string, string> = {
  critical: "Critical",
  high: "High",
  medium: "Medium",
  low: "Low",
};

export function SeverityBadge({ severity }: SeverityBadgeProps) {
  const normalizedSeverity = severity.toLowerCase();
  const style = SEVERITY_STYLES[normalizedSeverity] ?? "bg-muted text-muted-foreground border-muted";
  const label = SEVERITY_LABELS[normalizedSeverity] ?? severity;

  return (
    <Badge
      variant="outline"
      className={cn(
        "text-[11px] font-semibold px-2 py-0 leading-5 border",
        style,
      )}
    >
      {label}
    </Badge>
  );
}
