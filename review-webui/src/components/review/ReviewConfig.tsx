import {
  ShieldCheck,
  FlaskConical,
  Blocks,
  Gauge,
  Bug,
  Wrench,
  Package,
  CheckCheck,
} from "lucide-react";
import { cn } from "@/lib/utils";
import type { ReviewDepth, ReviewFocus } from "@/lib/types";

export interface ReviewConfigProps {
  depth: ReviewDepth;
  onDepthChange: (d: ReviewDepth) => void;
  focus: ReviewFocus[];
  onFocusChange: (f: ReviewFocus[]) => void;
}

const DEPTH_OPTIONS = [
  { value: "quick", label: "Quick", description: "Surface-level scan" },
  { value: "full", label: "Full", description: "Standard review" },
  { value: "deep", label: "Deep", description: "Exhaustive analysis" },
] as const;

interface FocusOption {
  value: ReviewFocus;
  label: string;
  icon: React.ElementType;
}

const BASIC_FOCUS: FocusOption[] = [
  { value: "security", label: "Security", icon: ShieldCheck },
  { value: "tests", label: "Tests", icon: FlaskConical },
  { value: "bug-risk", label: "Bug Risk", icon: Bug },
  { value: "performance", label: "Performance", icon: Gauge },
];

const ADVANCED_FOCUS: FocusOption[] = [
  { value: "architecture", label: "Architecture", icon: Blocks },
  { value: "maintainability", label: "Maintainability", icon: Wrench },
  { value: "dependency", label: "Dependencies", icon: Package },
];

const BASIC_VALUES = BASIC_FOCUS.map((option) => option.value);
const ADVANCED_VALUES = ADVANCED_FOCUS.map((option) => option.value);

export function ReviewConfig({
  depth,
  onDepthChange,
  focus,
  onFocusChange,
}: ReviewConfigProps) {
  const toggleFocus = (value: ReviewFocus) => {
    if (focus.includes(value)) {
      onFocusChange(focus.filter((item) => item !== value));
    } else {
      onFocusChange([...focus, value]);
    }
  };

  const allSelected = (values: ReviewFocus[]) =>
    values.every((value) => focus.includes(value));

  const toggleGroup = (values: ReviewFocus[]) => {
    if (allSelected(values)) {
      onFocusChange(focus.filter((item) => !values.includes(item)));
    } else {
      onFocusChange([...focus, ...values.filter((value) => !focus.includes(value))]);
    }
  };

  const renderChips = (options: FocusOption[]) =>
    options.map((option) => {
      const isActive = focus.includes(option.value);
      const Icon = option.icon;
      return (
        <button
          key={option.value}
          type="button"
          onClick={() => toggleFocus(option.value)}
          className={cn(
            "inline-flex items-center gap-1.5 rounded-full border px-3 py-1.5 text-xs font-medium transition-all duration-200",
            isActive
              ? "border-primary/30 bg-primary/10 text-primary"
              : "border-border/60 bg-background/60 text-muted-foreground hover:border-primary/20 hover:text-foreground",
          )}
        >
          <Icon className="h-3.5 w-3.5" strokeWidth={1.8} />
          {option.label}
        </button>
      );
    });

  return (
    <div className="flex flex-col gap-6">
      <fieldset>
        <legend className="mb-2.5 text-xs font-semibold uppercase tracking-wider text-muted-foreground/70">
          Review Depth
        </legend>
        <div className="flex gap-2">
          {DEPTH_OPTIONS.map((option) => (
            <button
              key={option.value}
              type="button"
              onClick={() => onDepthChange(option.value)}
              className={cn(
                "flex min-w-[88px] flex-col items-center gap-1 rounded-lg border px-4 py-2.5 transition-all duration-200",
                depth === option.value
                  ? "border-primary/40 bg-primary/8 text-primary shadow-[0_0_0_1px_hsl(var(--primary)/0.15)]"
                  : "border-border/60 bg-background/60 text-muted-foreground hover:border-primary/20 hover:bg-primary/4",
              )}
            >
              <span className="text-sm font-semibold">{option.label}</span>
              <span className="text-[11px] leading-tight opacity-70">
                {option.description}
              </span>
            </button>
          ))}
        </div>
      </fieldset>

      <fieldset>
        <legend className="mb-2.5 text-xs font-semibold uppercase tracking-wider text-muted-foreground/70">
          Focus Areas
        </legend>
        <div className="flex flex-col gap-3">
          <div>
            <div className="mb-1.5 flex items-center justify-between">
              <span className="text-[11px] font-medium text-muted-foreground/60">
                Core
              </span>
              <button
                type="button"
                onClick={() => toggleGroup(BASIC_VALUES)}
                className={cn(
                  "inline-flex items-center gap-1 rounded-md px-2 py-0.5 text-[11px] font-medium transition-colors",
                  allSelected(BASIC_VALUES)
                    ? "text-primary"
                    : "text-muted-foreground/50 hover:text-foreground",
                )}
              >
                <CheckCheck className="h-3 w-3" strokeWidth={2} />
                {allSelected(BASIC_VALUES) ? "All selected" : "Select all"}
              </button>
            </div>
            <div className="flex flex-wrap gap-2">
              {renderChips(BASIC_FOCUS)}
            </div>
          </div>

          <div>
            <div className="mb-1.5 flex items-center justify-between">
              <span className="text-[11px] font-medium text-muted-foreground/60">
                Advanced
              </span>
              <button
                type="button"
                onClick={() => toggleGroup(ADVANCED_VALUES)}
                className={cn(
                  "inline-flex items-center gap-1 rounded-md px-2 py-0.5 text-[11px] font-medium transition-colors",
                  allSelected(ADVANCED_VALUES)
                    ? "text-primary"
                    : "text-muted-foreground/50 hover:text-foreground",
                )}
              >
                <CheckCheck className="h-3 w-3" strokeWidth={2} />
                {allSelected(ADVANCED_VALUES) ? "All selected" : "Select all"}
              </button>
            </div>
            <div className="flex flex-wrap gap-2">
              {renderChips(ADVANCED_FOCUS)}
            </div>
          </div>
        </div>
      </fieldset>
    </div>
  );
}
