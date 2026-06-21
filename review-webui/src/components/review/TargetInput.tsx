import { useCallback, useMemo } from "react";
import { Github, FolderOpen, Search, ArrowRight } from "lucide-react";
import { cn } from "@/lib/utils";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";

export interface TargetInputProps {
  value: string;
  onChange: (v: string) => void;
  onSubmit: () => void;
}

function detectTargetType(value: string): "github" | "local" | "default" {
  const trimmed = value.trim();
  if (
    trimmed.startsWith("https://github.com") ||
    trimmed.startsWith("http://github.com") ||
    trimmed.startsWith("github.com") ||
    trimmed.startsWith("https://www.github.com")
  ) {
    return "github";
  }
  // Local path heuristics: starts with /, ./, ../, C:\, D:\, etc.
  if (
    trimmed.startsWith("/") ||
    trimmed.startsWith("./") ||
    trimmed.startsWith("../") ||
    trimmed.startsWith("~") ||
    /^[A-Za-z]:\\/.test(trimmed)
  ) {
    return "local";
  }
  return "default";
}

const TARGET_ICONS = {
  github: Github,
  local: FolderOpen,
  default: Search,
} as const;

export function TargetInput({ value, onChange, onSubmit }: TargetInputProps) {
  const targetType = useMemo(() => detectTargetType(value), [value]);
  const Icon = TARGET_ICONS[targetType];

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLInputElement>) => {
      if (e.key === "Enter" && !e.nativeEvent.isComposing) {
        e.preventDefault();
        onSubmit();
      }
    },
    [onSubmit],
  );

  return (
    <div className="relative">
      {/* Leading icon */}
      <div className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground">
        <Icon className="h-4 w-4" strokeWidth={1.8} />
      </div>

      {/* Input */}
      <Input
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={handleKeyDown}
        placeholder="GitHub repo URL, PR link, or local path..."
        className={cn(
          "h-11 pl-10 pr-12 text-sm",
          "border-border/70 bg-background/80",
          "placeholder:text-muted-foreground/50",
          "transition-all duration-200",
          "focus-visible:border-primary/30 focus-visible:ring-primary/20",
        )}
      />

      {/* Submit button */}
      <Button
        type="button"
        size="icon"
        variant="ghost"
        className={cn(
          "absolute right-1 top-1/2 h-8 w-8 -translate-y-1/2",
          "text-muted-foreground hover:text-primary",
          "disabled:opacity-30",
        )}
        onClick={onSubmit}
        disabled={!value.trim()}
        aria-label="Submit target"
      >
        <ArrowRight className="h-4 w-4" />
      </Button>
    </div>
  );
}
