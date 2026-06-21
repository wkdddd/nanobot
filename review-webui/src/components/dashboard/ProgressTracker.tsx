import { cn } from "@/lib/utils";
import {
  CheckCircle2,
  Circle,
  ClipboardList,
  Download,
  Search,
  CheckSquare,
  FileText,
} from "lucide-react";

const STEPS = [
  { key: "planning", label: "Plan", icon: ClipboardList },
  { key: "prefetching", label: "Prefetch", icon: Download },
  { key: "reviewing", label: "Review", icon: Search },
  { key: "validating", label: "Validate", icon: CheckSquare },
  { key: "finalizing", label: "Report", icon: FileText },
] as const;

const PHASE_ORDER: Record<string, number> = {
  idle: -1,
  configuring: -1,
  submitting: -1,
  planning: 0,
  prefetching: 1,
  reviewing: 2,
  validating: 3,
  finalizing: 4,
  completed: 5,
};

interface ProgressTrackerProps {
  phase: string;
}

export function ProgressTracker({ phase }: ProgressTrackerProps) {
  const currentIndex = PHASE_ORDER[phase] ?? -1;
  const isCompleted = phase === "completed";

  return (
    <div className="w-full py-4">
      <div className="flex items-center justify-between">
        {STEPS.map((step, index) => {
          const stepIndex = index;
          let status: "completed" | "active" | "pending" = "pending";

          if (isCompleted) {
            status = "completed";
          } else if (currentIndex > stepIndex) {
            status = "completed";
          } else if (currentIndex === stepIndex) {
            status = "active";
          }

          const Icon = step.icon;

          return (
            <div key={step.key} className="flex items-center flex-1 last:flex-none">
              {/* Step circle + label */}
              <div className="flex flex-col items-center gap-1.5">
                <div
                  className={cn(
                    "relative flex items-center justify-center w-10 h-10 rounded-full transition-all duration-300",
                    status === "completed" &&
                      "bg-accent text-accent-foreground shadow-sm",
                    status === "active" &&
                      "bg-primary text-primary-foreground shadow-md",
                    status === "pending" &&
                      "bg-muted text-muted-foreground",
                  )}
                >
                  {status === "completed" ? (
                    <CheckCircle2 className="w-5 h-5" />
                  ) : status === "active" ? (
                    <>
                      <Icon className="w-4 h-4" />
                      {/* Animated pulse ring */}
                      <span className="absolute inset-0 rounded-full bg-primary/30 animate-ping" />
                    </>
                  ) : (
                    <Circle className="w-4 h-4" />
                  )}
                </div>
                <span
                  className={cn(
                    "text-xs font-medium transition-colors duration-300",
                    status === "completed" && "text-accent-foreground",
                    status === "active" && "text-primary font-semibold",
                    status === "pending" && "text-muted-foreground",
                  )}
                >
                  {step.label}
                </span>
              </div>

              {/* Connecting line */}
              {index < STEPS.length - 1 && (
                <div className="flex-1 mx-2 mt-[-1.25rem]">
                  <div
                    className={cn(
                      "h-0.5 rounded-full transition-colors duration-500",
                      isCompleted || currentIndex > stepIndex
                        ? "bg-accent"
                        : "bg-muted",
                    )}
                  />
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
