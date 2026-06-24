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
import { Textarea } from "@/components/ui/textarea";
import type { ReviewDepth, ReviewFocus } from "@/lib/types";

/* ------------------------------------------------------------------ */
/*  Depth selector                                                     */
/* ------------------------------------------------------------------ */

export interface ReviewConfigProps {
  depth: ReviewDepth;
  onDepthChange: (d: ReviewDepth) => void;
  focus: ReviewFocus[];
  onFocusChange: (f: ReviewFocus[]) => void;
  targetPaths: string;
  onTargetPathsChange: (p: string) => void;
}

const DEPTH_OPTIONS = [
  { value: "quick", label: "Quick", description: "Surface-level scan" },
  { value: "full", label: "Full", description: "Standard review" },
  { value: "deep", label: "Deep", description: "Exhaustive analysis" },
] as const;

/* ------------------------------------------------------------------ */
/*  Focus dimension chips                                              */
/* ------------------------------------------------------------------ */

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

const BASIC_VALUES = BASIC_FOCUS.map((o) => o.value);
const ADVANCED_VALUES = ADVANCED_FOCUS.map((o) => o.value);

/* ------------------------------------------------------------------ */
/*  Component                                                          */
/* ------------------------------------------------------------------ */

export function ReviewConfig({
  depth,
  onDepthChange,
  focus,
  onFocusChange,
  targetPaths,
  onTargetPathsChange,
}: ReviewConfigProps) {
  /* Toggle a focus chip on/off */
  const toggleFocus = (value: ReviewFocus) => {
    if (focus.includes(value)) {
      onFocusChange(focus.filter((f) => f !== value));
    } else {
      onFocusChange([...focus, value]);
    }
  };

  const allSelected = (values: ReviewFocus[]) =>
    values.every((v) => focus.includes(v));

  const toggleGroup = (values: ReviewFocus[]) => {
    if (allSelected(values)) {
      onFocusChange(focus.filter((f) => !values.includes(f)));
    } else {
      const merged = [...focus, ...values.filter((v) => !focus.includes(v))];
      onFocusChange(merged);
    }
  };

  const renderChips = (options: FocusOption[]) =>
    options.map((opt) => {
      const isActive = focus.includes(opt.value);
      const Icon = opt.icon;
      return (
        <button
          key={opt.value}
          type="button"
          onClick={() => toggleFocus(opt.value)}
          className={cn(
            "inline-flex items-center gap-1.5 rounded-full border px-3 py-1.5 text-xs font-medium transition-all duration-200",
            isActive
              ? "border-primary/30 bg-primary/10 text-primary"
              : "border-border/60 bg-background/60 text-muted-foreground hover:border-primary/20 hover:text-foreground",
          )}
        >
          <Icon className="h-3.5 w-3.5" strokeWidth={1.8} />
          {opt.label}
        </button>
      );
    });

  return (
    <div className="flex flex-col gap-6">
      {/* ── Depth selector ── */}
      <fieldset>
        <legend className="mb-2.5 text-xs font-semibold uppercase tracking-wider text-muted-foreground/70">
          Review Depth
        </legend>
        <div className="flex gap-2">
          {DEPTH_OPTIONS.map((opt) => (
            <button
              key={opt.value}
              type="button"
              onClick={() => onDepthChange(opt.value)}
              className={cn(
                "flex flex-col items-center gap-1 rounded-lg border px-4 py-2.5 transition-all duration-200",
                "min-w-[88px]",
                depth === opt.value
                  ? "border-primary/40 bg-primary/8 text-primary shadow-[0_0_0_1px_hsl(var(--primary)/0.15)]"
                  : "border-border/60 bg-background/60 text-muted-foreground hover:border-primary/20 hover:bg-primary/4",
              )}
            >
              <span className="text-sm font-semibold">{opt.label}</span>
              <span className="text-[11px] leading-tight opacity-70">
                {opt.description}
              </span>
            </button>
          ))}
        </div>
      </fieldset>

      {/* ── Focus dimensions ── */}
      <fieldset>
        <legend className="mb-2.5 text-xs font-semibold uppercase tracking-wider text-muted-foreground/70">
          Focus Areas
        </legend>
        <div className="flex flex-col gap-3">
          {/* Basic dimensions */}
          <div>
            <div className="mb-1.5 flex items-center justify-between">
              <span className="text-[11px] font-medium text-muted-foreground/60">
                基础维度
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
                {allSelected(BASIC_VALUES) ? "已全选" : "全选"}
              </button>
            </div>
            <div className="flex flex-wrap gap-2">
              {renderChips(BASIC_FOCUS)}
            </div>
          </div>

          {/* Advanced dimensions */}
          <div>
            <div className="mb-1.5 flex items-center justify-between">
              <span className="text-[11px] font-medium text-muted-foreground/60">
                进阶维度
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
                {allSelected(ADVANCED_VALUES) ? "已全选" : "全选"}
              </button>
            </div>
            <div className="flex flex-wrap gap-2">
              {renderChips(ADVANCED_FOCUS)}
            </div>
          </div>
        </div>
      </fieldset>

      {/* ── Target paths ── */}
      <fieldset>
        <legend className="mb-2.5 text-xs font-semibold uppercase tracking-wider text-muted-foreground/70">
          Target Paths
          <span className="ml-1.5 font-normal normal-case tracking-normal text-muted-foreground/50">
            (optional)
          </span>
        </legend>
        <Textarea
          value={targetPaths}
          onChange={(e) => onTargetPathsChange(e.target.value)}
          placeholder={
            "Specify files or directories to focus on, one per line or comma-separated:\nsrc/components/\nsrc/utils/api.ts"
          }
          rows={3}
          className={cn(
            "resize-none text-sm leading-relaxed",
            "border-border/70 bg-background/80",
            "placeholder:text-muted-foreground/40",
            "focus-visible:border-primary/30 focus-visible:ring-primary/20",
          )}
        />
      </fieldset>
    </div>
  );
}
