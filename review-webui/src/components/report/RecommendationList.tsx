import { Card, CardContent } from "@/components/ui/card";
import { ArrowDown } from "lucide-react";

interface RecommendationListProps {
  recommendations: string[];
}

export function RecommendationList({ recommendations }: RecommendationListProps) {
  if (recommendations.length === 0) {
    return (
      <div className="text-sm text-muted-foreground italic py-4 text-center">
        No recommendations available.
      </div>
    );
  }

  return (
    <Card className="paper-texture">
      <CardContent className="p-5 space-y-3">
        <div className="flex items-center gap-2">
          <ArrowDown className="w-4 h-4 text-primary" />
          <h3 className="text-base font-semibold text-foreground">
            Recommendations
          </h3>
        </div>

        <ol className="space-y-3">
          {recommendations.map((rec, index) => (
            <li key={index} className="flex gap-3">
              {/* Number indicator */}
              <span className="flex-shrink-0 flex items-center justify-center w-6 h-6 rounded-full bg-primary/10 text-primary text-xs font-bold">
                {index + 1}
              </span>
              <p className="text-sm text-muted-foreground leading-relaxed pt-0.5">
                {rec}
              </p>
            </li>
          ))}
        </ol>
      </CardContent>
    </Card>
  );
}
