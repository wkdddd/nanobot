import { useEffect, useMemo, useState } from "react";
import { AlertCircle, Code2, Loader2, MousePointerClick } from "lucide-react";
import { cn } from "@/lib/utils";
import { CodeBlock } from "./CodeBlock";
import { SeverityBadge } from "@/components/findings/SeverityBadge";
import { fetchCodeContext, type ApiAuth } from "@/lib/api";
import type { Finding } from "@/hooks/useReviewSession";
import type { CodeContextPayload } from "@/lib/types";

export interface CodePanelProps {
  finding: Finding | null;
  sessionKey: string | null;
  auth: ApiAuth;
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

type LoadState =
  | { status: "idle" }
  | { status: "loading" }
  | { status: "ready"; context: CodeContextPayload }
  | { status: "error"; message: string };

function fallbackEvidence(finding: Finding | null): CodeContextPayload | null {
  if (!finding?.evidence?.trim()) return null;
  return {
    file: finding.file,
    line: finding.line ?? 1,
    startLine: finding.line ?? 1,
    endLine: (finding.line ?? 1) + finding.evidence.split("\n").length - 1,
    code: finding.evidence,
    source: "local",
    truncated: false,
  };
}

export function CodePanel({ finding, sessionKey, auth, className }: CodePanelProps) {
  const [loadState, setLoadState] = useState<LoadState>({ status: "idle" });
  const evidenceFallback = useMemo(() => fallbackEvidence(finding), [finding]);

  useEffect(() => {
    if (!finding?.file || !sessionKey) {
      setLoadState({ status: "idle" });
      return;
    }
    let cancelled = false;
    setLoadState({ status: "loading" });
    fetchCodeContext(auth, sessionKey, finding.file, finding.line)
      .then((context) => {
        if (!cancelled) setLoadState({ status: "ready", context });
      })
      .catch((error) => {
        if (!cancelled) {
          setLoadState({
            status: "error",
            message: error instanceof Error ? error.message : "Failed to load code context",
          });
        }
      });
    return () => {
      cancelled = true;
    };
  }, [auth, finding?.file, finding?.line, sessionKey]);

  const context =
    loadState.status === "ready" ? loadState.context : loadState.status === "error" ? evidenceFallback : null;

  return (
    <div
      className={cn(
        "flex flex-col h-full rounded-xl border border-[#e8e0d4] bg-[#fdfbf7] shadow-sm",
        className
      )}
    >
      {/* Header */}
      <div className="flex items-center gap-1.5 px-3 py-2 border-b border-[#e8e0d4] bg-[#f7f3ec]">
        <Code2 className="h-3.5 w-3.5 text-[#8c7b6b]" />
        <h3 className="text-xs font-semibold text-[#5c4f42]">代码上下文</h3>
      </div>

      {/* Content */}
      <div className="flex-1 p-3 overflow-auto">
        {!finding ? (
          <div className="flex flex-col items-center justify-center h-full text-center gap-2 py-10">
            <div className="rounded-full bg-[#f0ebe3] p-3">
              <MousePointerClick className="h-5 w-5 text-[#b0a08c]" />
            </div>
            <div>
              <p className="text-xs font-medium text-[#8c7b6b]">
                选择一个发现项以查看相关代码
              </p>
              <p className="text-[11px] text-[#b0a08c] mt-0.5">
                在左侧列表中点击任意发现项即可预览对应代码
              </p>
            </div>
          </div>
        ) : (
          <div className="space-y-2">
            {/* Finding info bar */}
            <div className="flex items-center gap-2 text-xs text-[#8c7b6b]">
              {finding.line !== null && <span>第 {finding.line} 行</span>}
              {finding.line !== null && <span className="text-[#d4c8b8]">|</span>}
              <SeverityBadge severity={finding.severity} />
            </div>

            {/* Code block */}
            {loadState.status === "loading" ? (
              <div className="flex items-center gap-1.5 rounded-md border border-[#e8e0d4] bg-[#faf6f0] px-2.5 py-1.5 text-[11px] text-[#8c7b6b]">
                <Loader2 className="h-3 w-3 animate-spin" />
                正在加载代码上下文...
              </div>
            ) : loadState.status === "error" && !context ? (
              <div className="flex items-start gap-1.5 rounded-md border border-red-200 bg-red-50 px-2.5 py-1.5 text-[11px] text-red-700">
                <AlertCircle className="mt-0.5 h-3 w-3 shrink-0" />
                <span>{loadState.message}</span>
              </div>
            ) : context ? (
              <>
                {loadState.status === "error" && (
                  <div className="flex items-start gap-1.5 rounded-md border border-amber-200 bg-amber-50 px-2.5 py-1.5 text-[11px] text-amber-700">
                    <AlertCircle className="mt-0.5 h-3 w-3 shrink-0" />
                    <span>{loadState.message}，已显示审查证据片段。</span>
                  </div>
                )}
                <CodeBlock
                  code={context.code}
                  language={inferLanguage(finding.file)}
                  fileName={finding.file.split("/").pop() || finding.file}
                  startLine={context.startLine}
                  highlightLine={context.line}
                />
              </>
            ) : (
              <div className="rounded-md border border-[#e8e0d4] bg-[#faf6f0] px-2.5 py-1.5 text-[11px] text-[#8c7b6b]">
                当前发现项没有可定位的代码文件。
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
