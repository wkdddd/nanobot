import { cn } from "@/lib/utils";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { CheckCircle2, XCircle, Clock } from "lucide-react";
import type { DimensionResult } from "@/hooks/useReviewSession";

interface DimensionCardsProps {
  dimensions: DimensionResult[];
  activeDimension: string | null;
  onSelectDimension: (dimension: string | null) => void;
}

function getStatusIcon(status: string) {
  switch (status) {
    case "completed":
    case "validated":
    case "done":
      return <CheckCircle2 className="w-5 h-5 text-green-600" />;
    case "error":
    case "failed":
      return <XCircle className="w-5 h-5 text-red-500" />;
    case "pending":
    case "running":
    case "in_progress":
      return <Clock className="w-5 h-5 text-amber-500" />;
    default:
      return <Clock className="w-5 h-5 text-muted-foreground" />;
  }
}

function getStatusLabel(status: string) {
  switch (status) {
    case "completed":
    case "validated":
    case "done":
      return "Validated";
    case "error":
    case "failed":
      return "Error";
    case "pending":
      return "Pending";
    case "running":
    case "in_progress":
      return "Running";
    default:
      return status;
  }
}

export function DimensionCards({
  dimensions,
  activeDimension,
  onSelectDimension,
}: DimensionCardsProps) {
  if (dimensions.length === 0) {
    return (
      <div className="text-sm text-muted-foreground italic py-4 text-center">
        No dimensions available yet.
      </div>
    );
  }

  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-2">
      {dimensions.map((dim) => {
        const totalFindings =
          dim.acceptedCount + dim.rejectedCount + dim.uncertainCount;
        const isActive = activeDimension === dim.dimension;

        return (
          <Card
            key={dim.dimension}
            className={cn(
              "cursor-pointer transition-all duration-200 hover:shadow-md paper-texture",
              isActive && "ring-2 ring-primary border-primary",
            )}
            role="button"
            tabIndex={0}
            onClick={() =>
              onSelectDimension(isActive ? null : dim.dimension)
            }
            onKeyDown={(e) => {
              if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                onSelectDimension(isActive ? null : dim.dimension);
              }
            }}
          >
            <CardContent className="p-3">
              <div className="flex items-start justify-between gap-2">
                <div className="flex items-center gap-2 min-w-0">
                  {getStatusIcon(dim.status)}
                  <div className="min-w-0">
                    <h4 className="text-xs font-semibold truncate">
                      {dim.dimension}
                    </h4>
                    <span className="text-[11px] text-muted-foreground">
                      {getStatusLabel(dim.status)}
                    </span>
                  </div>
                </div>
                {totalFindings > 0 && (
                  <Badge
                    variant="secondary"
                    className="flex-shrink-0 text-[11px]"
                  >
                    {totalFindings}
                  </Badge>
                )}
              </div>

              {/* Sub-counts */}
              <div className="flex gap-2 mt-2 text-[11px] text-muted-foreground">
                {dim.acceptedCount > 0 && (
                  <span className="flex items-center gap-1">
                    <span className="w-1 h-1 rounded-full bg-green-500" />
                    {dim.acceptedCount} accepted
                  </span>
                )}
                {dim.rejectedCount > 0 && (
                  <span className="flex items-center gap-1">
                    <span className="w-1 h-1 rounded-full bg-red-500" />
                    {dim.rejectedCount} rejected
                  </span>
                )}
                {dim.uncertainCount > 0 && (
                  <span className="flex items-center gap-1">
                    <span className="w-1 h-1 rounded-full bg-yellow-500" />
                    {dim.uncertainCount} uncertain
                  </span>
                )}
              </div>
            </CardContent>
          </Card>
        );
      })}
    </div>
  );
}
