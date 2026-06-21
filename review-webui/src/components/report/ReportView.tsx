import { useMemo } from "react";
import { Separator } from "@/components/ui/separator";
import { ReportSummary } from "./ReportSummary";
import { DimensionCards } from "@/components/dashboard/DimensionCards";
import { SeverityChart } from "@/components/dashboard/SeverityChart";
import { FindingList } from "@/components/findings/FindingList";
import { RecommendationList } from "./RecommendationList";
import type { Finding } from "@/hooks/useReviewSession";
import type { DimensionResult } from "@/hooks/useReviewSession";

interface ReportViewProps {
  summary: string;
  findings: Finding[];
  dimensions: DimensionResult[];
  recommendations: string[];
  activeDimension: string | null;
  onSelectDimension: (dimension: string | null) => void;
  onSelectFinding?: (finding: Finding) => void;
}

export function ReportView({
  summary,
  findings,
  dimensions,
  recommendations,
  activeDimension,
  onSelectDimension,
  onSelectFinding,
}: ReportViewProps) {
  // Extract recommendations from findings if not provided
  const derivedRecommendations = useMemo(() => {
    if (recommendations.length > 0) return recommendations;
    // Derive from findings, prioritizing critical/high
    const sorted = [...findings].sort((a, b) => {
      const order: Record<string, number> = { critical: 0, high: 1, medium: 2, low: 3 };
      return (order[a.severity.toLowerCase()] ?? 99) - (order[b.severity.toLowerCase()] ?? 99);
    });
    return sorted
      .filter((f) => f.recommendation)
      .map((f) => f.recommendation);
  }, [findings, recommendations]);

  return (
    <div className="space-y-4">
      {/* Executive summary */}
      <ReportSummary summary={summary} findings={findings} />

      <Separator />

      {/* Dimension cards */}
      <section>
        <h3 className="text-xs font-semibold text-foreground mb-2">
          Review Dimensions
        </h3>
        <DimensionCards
          dimensions={dimensions}
          activeDimension={activeDimension}
          onSelectDimension={onSelectDimension}
        />
      </section>

      <Separator />

      {/* Severity distribution */}
      <section>
        <h3 className="text-xs font-semibold text-foreground mb-2">
          Severity Distribution
        </h3>
        <SeverityChart findings={findings} />
      </section>

      <Separator />

      {/* Findings list */}
      <section>
        <h3 className="text-xs font-semibold text-foreground mb-2">
          Findings
        </h3>
        <FindingList findings={findings} activeDimension={activeDimension} onSelectFinding={onSelectFinding} />
      </section>

      <Separator />

      {/* Recommendations */}
      <section>
        <RecommendationList recommendations={derivedRecommendations} />
      </section>
    </div>
  );
}
