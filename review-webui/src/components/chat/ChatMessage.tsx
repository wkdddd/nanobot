import { cn } from "@/lib/utils";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { FindingDetail } from "@/components/findings/FindingDetail";
import { ChevronDown, ChevronRight } from "lucide-react";
import { useState } from "react";
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

export function ChatMessage({ message, onSelectFinding }: ChatMessageProps) {
  const isUser = message.role === "user";
  const [showThinking, setShowThinking] = useState(Boolean(message.streaming));
  const hasThinking = message.thinking && message.thinking.length > 0;
  const hasVisibleContent = message.type !== "text" || message.content.trim().length > 0;

  return (
    <div className={cn("flex flex-col", isUser ? "items-end" : "items-start")}>
      {/* Thinking block (agent only, collapsible) */}
      {hasThinking && !isUser && (
        <div className="mb-2 w-full max-w-[85%]">
          <button
            onClick={() => setShowThinking(!showThinking)}
            className="flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground transition-colors px-1"
            aria-label="Toggle thinking"
            aria-expanded={showThinking}
          >
            {showThinking ? (
              <ChevronDown className="w-3 h-3" />
            ) : (
              <ChevronRight className="w-3 h-3" />
            )}
            <span className="italic">
              {message.streaming && message.type !== "report" ? "Thinking..." : "Thinking"}
            </span>
          </button>
          {showThinking && (
            <div className="mt-1 px-3 py-2 text-xs text-muted-foreground/80 leading-relaxed bg-muted/50 rounded-lg border border-border/50 max-h-40 overflow-y-auto scrollbar-thin">
              {message.thinking}
            </div>
          )}
        </div>
      )}

      {hasVisibleContent && (
        <div
          className={cn(
            "text-sm leading-relaxed",
            isUser
              ? "px-4 py-2.5 bg-primary/10 rounded-2xl rounded-tr-sm text-foreground max-w-[70%]"
              : "max-w-[85%]"
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
              <ReactMarkdown remarkPlugins={[remarkGfm]}>
                {message.content}
              </ReactMarkdown>
            </div>
          ) : (
            <span className="whitespace-pre-wrap">{message.content}</span>
          )}

          {/* Streaming cursor */}
          {message.streaming && (
            <span
              className="inline-block w-1.5 h-4 bg-foreground/60 animate-pulse ml-0.5 align-text-bottom rounded-sm"
            />
          )}
        </div>
      )}
      {!hasVisibleContent && message.streaming && !isUser && (
        <div
          className="ml-1 inline-block h-3 w-3 rounded-full border-2 border-muted-foreground/40 border-t-muted-foreground animate-spin"
          aria-label="Thinking"
        />
      )}
    </div>
  );
}
