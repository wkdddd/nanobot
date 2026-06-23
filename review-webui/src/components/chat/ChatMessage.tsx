import { cn } from "@/lib/utils";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { FindingDetail } from "@/components/findings/FindingDetail";
import { ChevronDown, ChevronRight } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import type { Finding } from "@/hooks/useReviewSession";

interface ChatMessageProps {
  message: {
    id: string;
    role: "user" | "agent";
    type: "text" | "finding" | "report";
    content: string;
    timestamp: number;
    finding?: Finding;
    thinking?: string;
    streaming?: boolean;
  };
  onSelectFinding?: (finding: Finding) => void;
}

const LOCATION_RE = /^(.+\.(?:[A-Za-z0-9]+)):(\d+)$/;

function findingFromLocation(value: string): Finding | null {
  const match = value.trim().match(LOCATION_RE);
  if (!match) return null;
  return {
    severity: "medium",
    dimension: "report",
    file: match[1],
    line: Number.parseInt(match[2], 10),
    title: `Code context for ${value.trim()}`,
    impact: "",
    recommendation: "",
  };
}

export function ChatMessage({ message, onSelectFinding }: ChatMessageProps) {
  const isUser = message.role === "user";
  const isThinkingOnly = !isUser
    && message.type === "text"
    && message.content.trim().length === 0
    && !!message.thinking?.trim();
  const hasThinking = message.thinking && message.thinking.length > 0;
  const [userToggledThinking, setUserToggledThinking] = useState(false);
  const [showThinking, setShowThinking] = useState(() => Boolean(message.streaming || isThinkingOnly));
  const collapseTimerRef = useRef<number | null>(null);
  const hasVisibleContent = message.type !== "text" || message.content.trim().length > 0;
  const toggleThinking = () => {
    setUserToggledThinking(true);
    setShowThinking((value) => !value);
  };

  useEffect(() => {
    if (!hasThinking) return;
    if (collapseTimerRef.current !== null) {
      window.clearTimeout(collapseTimerRef.current);
      collapseTimerRef.current = null;
    }
    if (message.streaming) {
      if (!userToggledThinking) setShowThinking(true);
      return;
    }
    if (isThinkingOnly) {
      if (!userToggledThinking) setShowThinking(true);
      return;
    }
    if (!userToggledThinking) {
      collapseTimerRef.current = window.setTimeout(() => {
        setShowThinking(false);
        collapseTimerRef.current = null;
      }, 700);
    }
    return () => {
      if (collapseTimerRef.current !== null) {
        window.clearTimeout(collapseTimerRef.current);
        collapseTimerRef.current = null;
      }
    };
  }, [hasThinking, isThinkingOnly, message.streaming, userToggledThinking]);

  return (
    <div className={cn("flex flex-col", isUser ? "items-end" : "items-start")}>
      {/* Thinking block (agent only, collapsible) */}
      {hasThinking && !isUser && (
        <div className="mb-1.5 w-full max-w-[80%]">
          <button
            onClick={toggleThinking}
            className="flex items-center gap-1 text-[11px] text-muted-foreground hover:text-foreground transition-colors px-1"
            aria-label="Toggle thinking"
            aria-expanded={showThinking}
          >
            {showThinking ? (
              <ChevronDown className="w-2.5 h-2.5" />
            ) : (
              <ChevronRight className="w-2.5 h-2.5" />
            )}
            <span className="italic">
              {message.streaming && message.type !== "report"
                ? "Thinking..."
                : isThinkingOnly
                  ? "已停止前的思考"
                  : "Thinking"}
            </span>
          </button>
          {showThinking && (
            <div className="mt-0.5 px-2 py-1.5 text-[11px] text-muted-foreground/80 leading-relaxed bg-muted/50 rounded-md border border-border/50 max-h-32 overflow-y-auto scrollbar-thin">
              {message.thinking}
            </div>
          )}
        </div>
      )}

      {hasVisibleContent && (
        <div
          className={cn(
            "text-xs leading-relaxed",
            isUser
              ? "px-3 py-2 bg-primary/10 rounded-xl rounded-tr-sm text-foreground max-w-[65%]"
              : "max-w-[80%]"
          )}
        >
          {message.type === "finding" && message.finding ? (
            <div
              className="cursor-pointer"
              onClick={() => onSelectFinding?.(message.finding!)}
            >
              <FindingDetail finding={message.finding} variant="card" />
            </div>
          ) : message.type === "report" ? (
            <div className="prose prose-sm markdown-content max-w-none">
              <ReactMarkdown
                remarkPlugins={[remarkGfm]}
                components={{
                  code({ children, className, ...props }) {
                    const text = String(children).trim();
                    const locationFinding = findingFromLocation(text);
                    if (!className && locationFinding) {
                      return (
                        <button
                          type="button"
                          className="rounded bg-muted px-1.5 py-0.5 font-mono text-[0.85em] text-primary hover:bg-primary/10"
                          onClick={() => onSelectFinding?.(locationFinding)}
                        >
                          {text}
                        </button>
                      );
                    }
                    return (
                      <code className={className} {...props}>
                        {children}
                      </code>
                    );
                  },
                }}
              >
                {message.content}
              </ReactMarkdown>
            </div>
          ) : (
            <span className="whitespace-pre-wrap">{message.content}</span>
          )}

          {/* Streaming cursor */}
          {message.streaming && (
            <span
              className="inline-block w-1 h-3 bg-foreground/60 animate-pulse ml-0.5 align-text-bottom rounded-sm"
            />
          )}
        </div>
      )}
      {!hasVisibleContent && message.streaming && !isUser && (
        <div
          className="ml-1 inline-block h-2.5 w-2.5 rounded-full border-2 border-muted-foreground/40 border-t-muted-foreground animate-spin"
          aria-label="Thinking"
        />
      )}
    </div>
  );
}
