import { useState } from "react";
import { cn } from "@/lib/utils";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { ChevronDown, ChevronRight } from "lucide-react";
import { SeverityBadge } from "./SeverityBadge";
import type { Finding } from "@/hooks/useReviewSession";

interface FindingDetailProps {
  finding: Finding | null;
  variant?: "panel" | "card";
}

const SEVERITY_STRIPE: Record<string, string> = {
  critical: "bg-red-500",
  high: "bg-orange-500",
  medium: "bg-yellow-500",
  low: "bg-blue-500",
};

function CollapsibleSection({
  title,
  content,
  defaultOpen = false,
}: {
  title: string;
  content: string;
  defaultOpen?: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);

  return (
    <div className="mt-1.5">
      <button
        onClick={() => setOpen(!open)}
        className="flex items-center gap-1 text-[11px] font-medium text-muted-foreground hover:text-foreground transition-colors"
      >
        {open ? (
          <ChevronDown className="w-2.5 h-2.5" />
        ) : (
          <ChevronRight className="w-2.5 h-2.5" />
        )}
        {title}
      </button>
      {open && (
        <div className="text-xs text-muted-foreground mt-1 leading-relaxed pl-3 border-l-2 border-muted">
          {content}
        </div>
      )}
    </div>
  );
}

export function FindingDetail({ finding, variant = "panel" }: FindingDetailProps) {
  if (!finding) {
    if (variant === "card") return null;
    return (
      <div className="flex flex-col items-center justify-center h-full py-12 text-muted-foreground">
        <p className="text-sm font-medium">No finding selected</p>
        <p className="text-xs mt-1">
          Click on a finding card to view details.
        </p>
      </div>
    );
  }

  const isCard = variant === "card";

  return (
    <Card className={cn("overflow-hidden paper-texture", isCard && "shadow-sm")}>
      <div className="flex">
        <div className={cn("w-[3px] flex-shrink-0", SEVERITY_STRIPE[finding.severity.toLowerCase()] ?? "bg-muted")} />
        <CardContent className={cn("flex-1 min-w-0", isCard ? "p-2.5" : "p-3")}>
          {/* Header row */}
          <div className="flex items-center gap-1.5 flex-wrap">
            <SeverityBadge severity={finding.severity} />
            <code className="text-[11px] font-mono text-muted-foreground bg-muted px-1 py-0 rounded">
              {finding.file}
              {finding.line != null && `:${finding.line}`}
            </code>
            <Badge variant="secondary" className="text-[9px] capitalize">
              {finding.dimension}
            </Badge>
            {finding.confidence && (
              <span className="text-[9px] text-muted-foreground ml-auto">
                Confidence: {finding.confidence}
              </span>
            )}
          </div>

          {/* Title */}
          <h4 className={cn("font-semibold mt-1.5 text-foreground", isCard ? "text-xs" : "text-xs")}>
            {finding.title}
          </h4>

          {/* Sections */}
          {finding.impact && (
            <CollapsibleSection title="Impact" content={finding.impact} defaultOpen={!isCard} />
          )}
          {finding.recommendation && (
            <CollapsibleSection title="Recommendation" content={finding.recommendation} defaultOpen={!isCard} />
          )}

          {/* Evidence (if available) */}
          {finding.evidence && (
            <CollapsibleSection
              title="Evidence"
              content={finding.evidence}
              defaultOpen={!isCard}
            />
          )}
        </CardContent>
      </div>
    </Card>
  );
}
