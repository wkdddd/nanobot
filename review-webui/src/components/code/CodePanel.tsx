import { Code2, MousePointerClick } from "lucide-react";
import { cn } from "@/lib/utils";
import { CodeBlock } from "./CodeBlock";
import type { Finding } from "@/hooks/useReviewSession";

export interface CodePanelProps {
  finding: Finding | null;
  className?: string;
}

/**
 * 根据文件扩展名推断语言，用于 SyntaxHighlighter
 */
function inferLanguage(filePath: string): string {
  const ext = filePath.split(".").pop()?.toLowerCase();
  switch (ext) {
    case "ts":
    case "tsx":
      return "typescript";
    case "js":
    case "jsx":
      return "javascript";
    case "py":
      return "python";
    case "java":
      return "java";
    case "go":
      return "go";
    case "rs":
      return "rust";
    case "cpp":
    case "cc":
    case "cxx":
      return "cpp";
    case "c":
      return "c";
    case "rb":
      return "ruby";
    case "php":
      return "php";
    case "swift":
      return "swift";
    case "kt":
      return "kotlin";
    case "scala":
      return "scala";
    case "sh":
    case "bash":
      return "bash";
    case "yaml":
    case "yml":
      return "yaml";
    case "json":
      return "json";
    case "md":
    case "markdown":
      return "markdown";
    case "css":
      return "css";
    case "html":
    case "htm":
      return "html";
    case "xml":
      return "xml";
    case "sql":
      return "sql";
    case "dockerfile":
      return "docker";
    default:
      return "typescript";
  }
}

/**
 * 根据 Finding 信息生成 Mock 代码片段
 * - 如果有 evidence，优先使用 evidence
 * - 如果有行号，生成包含上下文的模拟代码
 */
function generateMockCode(finding: Finding): { code: string; startLine: number } {
  // 如果存在 evidence，直接返回
  if (finding.evidence && finding.evidence.trim().length > 0) {
    const lines = finding.evidence.split("\n");
    const startLine = finding.line ?? 1;
    return { code: finding.evidence, startLine };
  }

  const line = finding.line ?? 1;
  const startLine = Math.max(1, line - 4);
  const fileName = finding.file.split("/").pop() || finding.file;

  // 根据维度生成不同风格的 mock 代码
  const dimension = finding.dimension.toLowerCase();
  let body = "";

  if (dimension.includes("security")) {
    body = `// TODO: Review security implication
function processInput(data) {
  // Potential issue at line ${line}
  const result = eval(data);  // ⚠️ ${finding.title}
  return result;
}

// Recommendation: ${finding.recommendation}`;
  } else if (dimension.includes("performance")) {
    body = `// TODO: Optimize performance
function fetchData() {
  const items = [];
  for (let i = 0; i < 10000; i++) {
    // Inefficient loop at line ${line}
    items.push(getItem(i));  // ⚠️ ${finding.title}
  }
  return items;
}

// Recommendation: ${finding.recommendation}`;
  } else if (dimension.includes("maintain")) {
    body = `// TODO: Improve maintainability
class UserManager {
  constructor() {
    this.data = {};
  }

  handle() {
    // Complex logic at line ${line}
    if (a && b || c && !d || e) {  // ⚠️ ${finding.title}
      doSomething();
    }
  }
}

// Recommendation: ${finding.recommendation}`;
  } else {
    body = `// Review finding: ${finding.title}
// File: ${fileName}
// Line: ${line}

function example() {
  // Issue context
  const value = null;
  // ⚠️ ${finding.title}
  return value.property;
}

// Recommendation: ${finding.recommendation}`;
  }

  return { code: body, startLine };
}

export function CodePanel({ finding, className }: CodePanelProps) {
  return (
    <div
      className={cn(
        "flex flex-col h-full rounded-xl border border-[#e8e0d4] bg-[#fdfbf7] shadow-sm",
        className
      )}
    >
      {/* Header */}
      <div className="flex items-center gap-2 px-4 py-3 border-b border-[#e8e0d4] bg-[#f7f3ec]">
        <Code2 className="h-4 w-4 text-[#8c7b6b]" />
        <h3 className="text-sm font-semibold text-[#5c4f42]">代码上下文</h3>
      </div>

      {/* Content */}
      <div className="flex-1 p-4 overflow-auto">
        {!finding ? (
          <div className="flex flex-col items-center justify-center h-full text-center gap-3 py-12">
            <div className="rounded-full bg-[#f0ebe3] p-4">
              <MousePointerClick className="h-6 w-6 text-[#b0a08c]" />
            </div>
            <div>
              <p className="text-sm font-medium text-[#8c7b6b]">
                选择一个发现项以查看相关代码
              </p>
              <p className="text-xs text-[#b0a08c] mt-1">
                在左侧列表中点击任意发现项即可预览对应代码
              </p>
            </div>
          </div>
        ) : (
          <div className="space-y-3">
            {/* Finding info bar */}
            <div className="flex items-center gap-2 text-xs text-[#8c7b6b]">
              <span className="font-medium text-[#5c4f42]">{finding.file}</span>
              {finding.line !== null && (
                <>
                  <span className="text-[#d4c8b8]">|</span>
                  <span>第 {finding.line} 行</span>
                </>
              )}
              <span className="text-[#d4c8b8]">|</span>
              <span
                className={cn(
                  "inline-flex items-center rounded px-1.5 py-0.5 font-medium",
                  finding.severity === "high" &&
                    "bg-red-50 text-red-700",
                  finding.severity === "medium" &&
                    "bg-amber-50 text-amber-700",
                  finding.severity === "low" &&
                    "bg-blue-50 text-blue-700",
                  finding.severity === "info" &&
                    "bg-slate-50 text-slate-600"
                )}
              >
                {finding.severity}
              </span>
            </div>

            {/* Code block */}
            {(() => {
              const { code, startLine } = generateMockCode(finding);
              return (
                <CodeBlock
                  code={code}
                  language={inferLanguage(finding.file)}
                  fileName={finding.file.split("/").pop() || finding.file}
                  startLine={startLine}
                />
              );
            })()}
          </div>
        )}
      </div>
    </div>
  );
}
