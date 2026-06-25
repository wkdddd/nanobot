import { FileText, AlertTriangle, Info } from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
import type { Finding } from "@/hooks/useReviewSession";

interface ReportSummaryProps {
  summary: string;
  findings: Finding[];
}

export function ReportSummary({ summary, findings }: ReportSummaryProps) {
  const critical = findings.filter((f) => f.severity.toLowerCase() === "critical").length;
  const high = findings.filter((f) => f.severity.toLowerCase() === "high").length;
  const medium = findings.filter((f) => f.severity.toLowerCase() === "medium").length;
  const low = findings.filter((f) => f.severity.toLowerCase() === "low").length;

  return (
    <Card className="border-2 border-primary/20">
      <CardContent className="p-4 space-y-2">
        {/* Title */}
        <div className="flex items-center gap-1.5">
          <FileText className="h-3.5 w-3.5 text-primary" />
          <h3 className="text-xs font-semibold text-foreground">
            Review Report Summary
          </h3>
        </div>

        {/* Summary text */}
        {summary && (
          <p className="text-xs text-muted-foreground leading-relaxed">
            {summary}
          </p>
        )}

        {/* Stats */}
        <div className="flex flex-wrap gap-2 pt-1">
          <div className="flex items-center gap-1">
            <AlertTriangle className="h-3 w-3 text-red-500" />
            <span className="text-xs font-medium text-foreground">{critical}</span>
            <span className="text-[10px] text-muted-foreground">Critical</span>
          </div>
          <div className="flex items-center gap-1">
            <AlertTriangle className="h-3 w-3 text-orange-500" />
            <span className="text-xs font-medium text-foreground">{high}</span>
            <span className="text-[10px] text-muted-foreground">High</span>
          </div>
          <div className="flex items-center gap-1">
            <AlertTriangle className="h-3 w-3 text-yellow-500" />
            <span className="text-xs font-medium text-foreground">{medium}</span>
            <span className="text-[10px] text-muted-foreground">Medium</span>
          </div>
          <div className="flex items-center gap-1">
            <Info className="h-3 w-3 text-blue-500" />
            <span className="text-xs font-medium text-foreground">{low}</span>
            <span className="text-[10px] text-muted-foreground">Low</span>
          </div>
          <div className="flex items-center gap-1">
            <FileText className="h-3 w-3 text-muted-foreground" />
            <span className="text-xs font-medium text-foreground">{findings.length}</span>
            <span className="text-[10px] text-muted-foreground">Total</span>
          </div>
        </div>
      </CardContent>
    </Card>
  );
}
