import { useState } from "react";
import { cn } from "@/lib/utils";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { ChevronDown, ChevronRight } from "lucide-react";
import { SeverityBadge } from "./SeverityBadge";
import type { Finding } from "@/hooks/useReviewSession";

interface FindingCardProps {
  finding: Finding;
  expanded: boolean;
  compact?: boolean;
  onToggle: () => void;
}

const SEVERITY_STRIPE: Record<string, string> = {
  critical: "bg-[hsl(var(--severity-critical))]",
  high: "bg-[hsl(var(--severity-high))]",
  medium: "bg-[hsl(var(--severity-medium))]",
  low: "bg-[hsl(var(--severity-low))]",
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
    <div className="mt-2">
      <button
        type="button"
        onClick={() => setOpen(!open)}
        className="flex items-center gap-1 text-xs font-medium text-muted-foreground hover:text-foreground transition-colors"
      >
        {open ? (
          <ChevronDown className="w-3 h-3" />
        ) : (
          <ChevronRight className="w-3 h-3" />
        )}
        {title}
      </button>
      {open && (
        <p className="text-sm text-muted-foreground mt-1.5 leading-relaxed pl-4 border-l-2 border-muted">
          {content}
        </p>
      )}
    </div>
  );
}

/** Compact single-row variant */
function CompactRow({ finding, onToggle, expanded }: FindingCardProps) {
  return (
    <button
      type="button"
      className={cn(
        "flex items-center gap-2 w-full text-left px-3 py-2 rounded-lg transition-colors",
        "hover:bg-secondary/60",
        expanded && "bg-secondary ring-1 ring-primary/20",
      )}
      onClick={onToggle}
    >
      <div className={cn("w-[3px] h-8 rounded-full shrink-0", SEVERITY_STRIPE[finding.severity.toLowerCase()] ?? "bg-muted")} />
      <SeverityBadge severity={finding.severity} />
      <code className="text-[11px] font-mono text-muted-foreground truncate">
        {finding.file}{finding.line != null ? `:${finding.line}` : ""}
      </code>
      <span className="text-xs text-foreground truncate flex-1 min-w-0">
        {finding.title}
      </span>
      <Badge variant="secondary" className="text-[10px] capitalize shrink-0">
        {finding.dimension}
      </Badge>
    </button>
  );
}

/** Full card variant */
function FullCard({ finding, onToggle, expanded }: FindingCardProps) {
  const stripeColor =
    SEVERITY_STRIPE[finding.severity.toLowerCase()] ?? "bg-muted";

  return (
    <Card
      className={cn(
        "overflow-hidden transition-all duration-200 hover:shadow-md",
        expanded && "ring-1 ring-primary/20",
      )}
    >
      <div className="flex">
        <div className={cn("w-[3px] flex-shrink-0", stripeColor)} />
        <CardContent className="p-4 flex-1 min-w-0">
          <button
            type="button"
            className="text-left w-full bg-transparent border-0 p-0 cursor-pointer"
            onClick={onToggle}
          >
            <div className="flex items-center gap-2 flex-wrap">
              <SeverityBadge severity={finding.severity} />
              <code className="text-xs font-mono text-muted-foreground bg-muted px-1.5 py-0.5 rounded">
                {finding.file}
                {finding.line != null && `:${finding.line}`}
              </code>
              <Badge variant="secondary" className="text-[10px] capitalize">
                {finding.dimension}
              </Badge>
              {finding.confidence && (
                <span className="text-[10px] text-muted-foreground ml-auto">
                  Confidence: {finding.confidence}
                </span>
              )}
            </div>
            <h4 className="text-sm font-semibold mt-2 text-foreground">
              {finding.title}
            </h4>
          </button>

          {expanded && (
            <div className="mt-2">
              {finding.impact && (
                <CollapsibleSection title="Impact" content={finding.impact} />
              )}
              {finding.recommendation && (
                <CollapsibleSection title="Recommendation" content={finding.recommendation} />
              )}
            </div>
          )}
        </CardContent>
      </div>
    </Card>
  );
}

export function FindingCard(props: FindingCardProps) {
  return props.compact ? <CompactRow {...props} /> : <FullCard {...props} />;
}
