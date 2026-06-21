import * as React from "react";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
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
  defaultTargetPaths: string;
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
  defaultTargetPaths: "",
};

const DEPTH_OPTIONS: { value: ReviewDepth; label: string; description: string }[] = [
  { value: "quick", label: "快速", description: "快速扫描，重点关注明显问题" },
  { value: "full", label: "完整", description: "全面审查，平衡深度与速度" },
  { value: "deep", label: "深度", description: "深入分析，逐行检查潜在风险" },
];

const DIMENSIONS: {
  key: ReviewFocus;
  label: string;
  icon: React.ElementType;
  description: string;
}[] = [
  {
    key: "security",
    label: "安全",
    icon: Shield,
    description: "检查安全漏洞、敏感信息泄露、注入风险等",
  },
  {
    key: "tests",
    label: "测试",
    icon: FlaskConical,
    description: "评估测试覆盖率、测试质量、边界条件处理",
  },
  {
    key: "architecture",
    label: "架构",
    icon: Building2,
    description: "分析代码结构、模块化程度、设计模式应用",
  },
  {
    key: "performance",
    label: "性能",
    icon: Gauge,
    description: "识别性能瓶颈、资源泄漏、低效算法",
  },
  {
    key: "bug-risk",
    label: "缺陷风险",
    icon: Bug,
    description: "发现潜在 Bug、空指针、竞态条件等",
  },
  {
    key: "maintainability",
    label: "可维护性",
    icon: Wrench,
    description: "评估代码可读性、复杂度、文档完整性",
  },
  {
    key: "dependency",
    label: "依赖",
    icon: Package,
    description: "检查依赖版本、许可证兼容性、过时组件",
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
      ? settings.defaultFocus.filter((k) => k !== key)
      : [...settings.defaultFocus, key];
    onSettingsChange({ ...settings, defaultFocus: next });
  };

  const handleTargetPathsChange = (value: string) => {
    onSettingsChange({ ...settings, defaultTargetPaths: value });
  };

  const handleReset = () => {
    onSettingsChange({ ...DEFAULT_SETTINGS });
  };

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      {/* Backdrop */}
      <div
        className="absolute inset-0 bg-black/50 backdrop-blur-sm"
        onClick={onClose}
        aria-hidden="true"
      />

      {/* Dialog */}
      <div className="relative z-10 w-full max-w-lg max-h-[85vh] overflow-hidden rounded-lg border bg-background shadow-lg flex flex-col">
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b">
          <h2 className="text-lg font-semibold">设置</h2>
          <Button
            variant="ghost"
            size="icon"
            className="h-8 w-8"
            onClick={onClose}
            aria-label="关闭"
          >
            <X className="h-4 w-4" />
          </Button>
        </div>

        {/* Body */}
        <div className="flex-1 overflow-y-auto px-6 py-5 space-y-6">
          {/* Default Review Depth */}
          <section className="space-y-3">
            <h3 className="text-sm font-medium text-foreground">
              默认审查深度
            </h3>
            <div className="space-y-2">
              {DEPTH_OPTIONS.map((option) => (
                <label
                  key={option.value}
                  className={cn(
                    "flex items-start gap-3 rounded-md border p-3 cursor-pointer transition-colors",
                    settings.defaultDepth === option.value
                      ? "border-primary bg-primary/5"
                      : "border-border hover:bg-accent/50"
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
                    <div className="text-xs text-muted-foreground mt-0.5">
                      {option.description}
                    </div>
                  </div>
                  {settings.defaultDepth === option.value && (
                    <Check className="h-4 w-4 text-primary mt-0.5 shrink-0" />
                  )}
                </label>
              ))}
            </div>
          </section>

          <Separator />

          {/* Default Dimensions */}
          <section className="space-y-3">
            <h3 className="text-sm font-medium text-foreground">默认维度</h3>
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
                              : "border-border bg-background text-muted-foreground hover:bg-accent hover:text-accent-foreground"
                          )}
                        >
                          <Icon className="h-3.5 w-3.5" />
                          <span>{dim.label}</span>
                          {active && <Check className="h-3 w-3 ml-0.5" />}
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

          <Separator />

          {/* Default Target Paths */}
          <section className="space-y-3">
            <h3 className="text-sm font-medium text-foreground">
              默认目标路径
            </h3>
            <Textarea
              placeholder="Enter default target paths, one per line\u2026"
              value={settings.defaultTargetPaths}
              onChange={(e) => handleTargetPathsChange(e.target.value)}
              className="min-h-[80px] resize-none"
              name="target-paths"
            />
            <p className="text-xs text-muted-foreground">
              每行一个路径，留空表示不限制。
            </p>
          </section>
        </div>

        {/* Footer */}
        <div className="flex items-center justify-between px-6 py-4 border-t bg-muted/30">
          <Button
            variant="ghost"
            size="sm"
            className="text-muted-foreground hover:text-foreground"
            onClick={handleReset}
          >
            <RotateCcw className="h-3.5 w-3.5 mr-1.5" />
            恢复默认
          </Button>
          <Button size="sm" onClick={onClose}>
            完成
          </Button>
        </div>
      </div>
    </div>
  );
}
