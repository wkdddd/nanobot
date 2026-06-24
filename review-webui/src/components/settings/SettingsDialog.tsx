import * as React from "react";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Separator } from "@/components/ui/separator";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import {
  Shield,
  FlaskConical,
  Building2,
  Gauge,
  Bug,
  Wrench,
  Package,
  X,
  RotateCcw,
  Check,
} from "lucide-react";
import type { ReviewDepth, ReviewFocus } from "@/lib/types";

export interface ReviewSettings {
  defaultDepth: ReviewDepth;
  defaultFocus: ReviewFocus[];
}

const DEFAULT_SETTINGS: ReviewSettings = {
  defaultDepth: "full",
  defaultFocus: [
    "security",
    "tests",
    "architecture",
    "performance",
    "bug-risk",
    "maintainability",
    "dependency",
  ],
};

const DEPTH_OPTIONS: { value: ReviewDepth; label: string; description: string }[] = [
  { value: "quick", label: "Quick", description: "Fast scan focused on obvious high-risk issues" },
  { value: "full", label: "Full", description: "Balanced review across the selected dimensions" },
  { value: "deep", label: "Deep", description: "More thorough analysis for subtle or systemic risks" },
];

const DIMENSIONS: {
  key: ReviewFocus;
  label: string;
  icon: React.ElementType;
  description: string;
}[] = [
  {
    key: "security",
    label: "Security",
    icon: Shield,
    description: "Auth, injection, data exposure, unsafe inputs, and other security risks.",
  },
  {
    key: "tests",
    label: "Tests",
    icon: FlaskConical,
    description: "Coverage, brittle assertions, missing edge cases, and verification gaps.",
  },
  {
    key: "architecture",
    label: "Architecture",
    icon: Building2,
    description: "Module boundaries, coupling, abstractions, and long-term design risks.",
  },
  {
    key: "performance",
    label: "Performance",
    icon: Gauge,
    description: "I/O hotspots, algorithmic cost, caching, concurrency, and resource use.",
  },
  {
    key: "bug-risk",
    label: "Bug Risk",
    icon: Bug,
    description: "Logic errors, state inconsistencies, null handling, and race conditions.",
  },
  {
    key: "maintainability",
    label: "Maintainability",
    icon: Wrench,
    description: "Readability, duplication, complexity, naming, and maintainability issues.",
  },
  {
    key: "dependency",
    label: "Dependencies",
    icon: Package,
    description: "Dependency health, vulnerabilities, licensing, and supply-chain concerns.",
  },
];

interface SettingsDialogProps {
  open: boolean;
  onClose: () => void;
  settings: ReviewSettings;
  onSettingsChange: (s: ReviewSettings) => void;
}

export function SettingsDialog({
  open,
  onClose,
  settings,
  onSettingsChange,
}: SettingsDialogProps) {
  const handleDepthChange = (depth: ReviewDepth) => {
    onSettingsChange({ ...settings, defaultDepth: depth });
  };

  const toggleDimension = (key: ReviewFocus) => {
    const next = settings.defaultFocus.includes(key)
      ? settings.defaultFocus.filter((item) => item !== key)
      : [...settings.defaultFocus, key];
    onSettingsChange({ ...settings, defaultFocus: next });
  };

  const handleReset = () => {
    onSettingsChange({ ...DEFAULT_SETTINGS });
  };

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      <div
        className="absolute inset-0 bg-black/50 backdrop-blur-sm"
        onClick={onClose}
        aria-hidden="true"
      />

      <div className="relative z-10 flex max-h-[85vh] w-full max-w-lg flex-col overflow-hidden rounded-lg border bg-background shadow-lg">
        <div className="flex items-center justify-between border-b px-6 py-4">
          <h2 className="text-lg font-semibold">Settings</h2>
          <Button
            variant="ghost"
            size="icon"
            className="h-8 w-8"
            onClick={onClose}
            aria-label="Close"
          >
            <X className="h-4 w-4" />
          </Button>
        </div>

        <div className="flex-1 space-y-6 overflow-y-auto px-6 py-5">
          <section className="space-y-3">
            <h3 className="text-sm font-medium text-foreground">
              Default Review Depth
            </h3>
            <div className="space-y-2">
              {DEPTH_OPTIONS.map((option) => (
                <label
                  key={option.value}
                  className={cn(
                    "flex cursor-pointer items-start gap-3 rounded-md border p-3 transition-colors",
                    settings.defaultDepth === option.value
                      ? "border-primary bg-primary/5"
                      : "border-border hover:bg-accent/50",
                  )}
                >
                  <input
                    type="radio"
                    name="review-depth"
                    value={option.value}
                    checked={settings.defaultDepth === option.value}
                    onChange={() => handleDepthChange(option.value)}
                    className="mt-1 h-4 w-4 accent-primary"
                  />
                  <div className="flex-1">
                    <div className="text-sm font-medium">{option.label}</div>
                    <div className="mt-0.5 text-xs text-muted-foreground">
                      {option.description}
                    </div>
                  </div>
                  {settings.defaultDepth === option.value && (
                    <Check className="mt-0.5 h-4 w-4 shrink-0 text-primary" />
                  )}
                </label>
              ))}
            </div>
          </section>

          <Separator />

          <section className="space-y-3">
            <h3 className="text-sm font-medium text-foreground">Default Focus</h3>
            <TooltipProvider delayDuration={200}>
              <div className="flex flex-wrap gap-2">
                {DIMENSIONS.map((dim) => {
                  const active = settings.defaultFocus.includes(dim.key);
                  const Icon = dim.icon;
                  return (
                    <Tooltip key={dim.key}>
                      <TooltipTrigger asChild>
                        <button
                          type="button"
                          onClick={() => toggleDimension(dim.key)}
                          className={cn(
                            "inline-flex items-center gap-1.5 rounded-full border px-3 py-1.5 text-xs font-medium transition-colors",
                            active
                              ? "border-primary bg-primary text-primary-foreground hover:bg-primary/90"
                              : "border-border bg-background text-muted-foreground hover:bg-accent hover:text-accent-foreground",
                          )}
                        >
                          <Icon className="h-3.5 w-3.5" />
                          <span>{dim.label}</span>
                          {active && <Check className="ml-0.5 h-3 w-3" />}
                        </button>
                      </TooltipTrigger>
                      <TooltipContent side="bottom">
                        <p>{dim.description}</p>
                      </TooltipContent>
                    </Tooltip>
                  );
                })}
              </div>
            </TooltipProvider>
          </section>
        </div>

        <div className="flex items-center justify-between border-t bg-muted/30 px-6 py-4">
          <Button
            variant="ghost"
            size="sm"
            className="text-muted-foreground hover:text-foreground"
            onClick={handleReset}
          >
            <RotateCcw className="mr-1.5 h-3.5 w-3.5" />
            Reset Defaults
          </Button>
          <Button size="sm" onClick={onClose}>
            Done
          </Button>
        </div>
      </div>
    </div>
  );
}
