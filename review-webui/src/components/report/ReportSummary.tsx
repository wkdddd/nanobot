import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Card, CardContent } from "@/components/ui/card";
import { Separator } from "@/components/ui/separator";
import type { Finding } from "@/hooks/useReviewSession";

interface ReportSummaryProps {
  summary: string;
  findings: Finding[];
}

export function ReportSummary({ summary, findings }: ReportSummaryProps) {
  const criticalCount = findings.filter((f) => f.severity === "critical").length;
  const highCount = findings.filter((f) => f.severity === "high").length;
  const totalCount = findings.length;

  return (
    <Card className="paper-texture">
      <CardContent className="p-5 space-y-3">
        <h3 className="text-base font-semibold text-foreground">
          Executive Summary
        </h3>

        {/* Key stats inline */}
        {totalCount > 0 && (
          <div className="flex flex-wrap gap-3 text-xs">
            <span className="bg-muted px-2 py-1 rounded-md">
              <span className="font-semibold">{totalCount}</span> total findings
            </span>
            {criticalCount > 0 && (
              <span className="bg-red-100 text-red-700 px-2 py-1 rounded-md">
                <span className="font-semibold">{criticalCount}</span> critical
              </span>
            )}
            {highCount > 0 && (
              <span className="bg-orange-100 text-orange-700 px-2 py-1 rounded-md">
                <span className="font-semibold">{highCount}</span> high
              </span>
            )}
          </div>
        )}

        <Separator />

        {/* Markdown summary */}
        {summary ? (
          <div className="prose prose-sm markdown-content max-w-none text-muted-foreground">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>
              {summary}
            </ReactMarkdown>
          </div>
        ) : (
          <p className="text-sm text-muted-foreground italic">
            No summary available yet.
          </p>
        )}
      </CardContent>
    </Card>
  );
}
