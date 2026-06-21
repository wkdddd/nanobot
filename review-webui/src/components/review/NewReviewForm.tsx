import { useState, useCallback, useEffect } from "react";
import { Loader2 } from "lucide-react";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Separator } from "@/components/ui/separator";
import { TargetInput } from "./TargetInput";
import { ReviewConfig } from "./ReviewConfig";
import type { ReviewDepth, ReviewFocus } from "@/lib/types";

export interface NewReviewSubmit {
  target: string;
  depth: ReviewDepth;
  focus: ReviewFocus[];
  targetPaths: string;
}

export interface NewReviewFormProps {
  onSubmit: (task: NewReviewSubmit) => void;
  submitting: boolean;
  defaultDepth?: ReviewDepth;
  defaultFocus?: ReviewFocus[];
  defaultTargetPaths?: string;
}

export function NewReviewForm({
  onSubmit,
  submitting,
  defaultDepth = "full",
  defaultFocus = [],
  defaultTargetPaths = "",
}: NewReviewFormProps) {
  const [target, setTarget] = useState("");
  const [depth, setDepth] = useState<ReviewDepth>(defaultDepth);
  const [focus, setFocus] = useState<ReviewFocus[]>(defaultFocus);
  const [targetPaths, setTargetPaths] = useState(defaultTargetPaths);

  useEffect(() => {
    setDepth(defaultDepth);
    setFocus(defaultFocus);
    setTargetPaths(defaultTargetPaths);
  }, [defaultDepth, defaultFocus, defaultTargetPaths]);

  const handleSubmit = useCallback(() => {
    const trimmed = target.trim();
    if (!trimmed || submitting) return;
    onSubmit({ target: trimmed, depth, focus, targetPaths: targetPaths.trim() });
  }, [target, depth, focus, targetPaths, submitting, onSubmit]);

  return (
    <div className="h-full overflow-y-auto scrollbar-thin scrollbar-track-transparent">
      <div className="flex items-start justify-center px-4 py-8 min-h-full">
        <Card className="w-full max-w-xl border-border/50 shadow-[0_1px_3px_0_hsl(var(--foreground)/0.04),0_4px_12px_0_hsl(var(--foreground)/0.02)]">
          <CardContent className="p-6">
            {/* Title */}
            <div className="mb-6">
              <h1 className="text-xl font-semibold tracking-tight text-foreground">
                New Code Review
              </h1>
              <p className="mt-1 text-sm text-muted-foreground/70">
                Provide a target and configure the review parameters.
              </p>
            </div>

            <div className="flex flex-col gap-5">
              {/* Target input */}
              <div>
                <label className="mb-1.5 block text-xs font-semibold uppercase tracking-wider text-muted-foreground/70">
                  Target
                </label>
                <TargetInput
                  value={target}
                  onChange={setTarget}
                  onSubmit={handleSubmit}
                />
              </div>

              <Separator className="bg-border/50" />

              {/* Review configuration */}
              <ReviewConfig
                depth={depth}
                onDepthChange={setDepth}
                focus={focus}
                onFocusChange={setFocus}
                targetPaths={targetPaths}
                onTargetPathsChange={setTargetPaths}
              />

              <Separator className="bg-border/50" />

              {/* Submit */}
              <div className="flex justify-end">
                <Button
                  type="button"
                  onClick={handleSubmit}
                  disabled={!target.trim() || submitting}
                  className={cn(
                    "h-9 min-w-[120px] gap-2 font-medium text-sm",
                    "bg-primary text-primary-foreground",
                    "shadow-[0_1px_2px_0_hsl(var(--primary)/0.3)]",
                    "hover:bg-primary/90",
                    "disabled:opacity-40 disabled:shadow-none",
                  )}
                >
                  {submitting ? (
                    <>
                      <Loader2 className="h-3.5 w-3.5 animate-spin" />
                      Starting\u2026
                    </>
                  ) : (
                    "Start Review"
                  )}
                </Button>
              </div>
            </div>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
