import { ReportSummary } from "./ReportSummary";
import type { Finding } from "@/hooks/useReviewSession";

interface ReportViewProps {
  summary: string;
  findings: Finding[];
  needsConfirmationCount?: number;
  rejectedCount?: number;
}

export function ReportView({
  summary,
  findings,
  needsConfirmationCount = 0,
  rejectedCount = 0,
}: ReportViewProps) {
  return (
    <div className="space-y-4">
      {/* Executive summary */}
      <ReportSummary summary={summary} findings={findings} />

      {(needsConfirmationCount > 0 || rejectedCount > 0) && (
        <div className="grid grid-cols-2 gap-2">
          <div className="rounded-md border bg-background px-2 py-1.5">
            <p className="text-[10px] uppercase text-muted-foreground">Needs Confirmation</p>
            <p className="text-sm font-semibold text-foreground">{needsConfirmationCount}</p>
          </div>
          <div className="rounded-md border bg-background px-2 py-1.5">
            <p className="text-[10px] uppercase text-muted-foreground">Rejected/Skipped</p>
            <p className="text-sm font-semibold text-foreground">{rejectedCount}</p>
          </div>
        </div>
      )}
    </div>
  );
}
