import { useState, useCallback } from "react";
import { Prism as SyntaxHighlighter } from "react-syntax-highlighter";
import { oneDark } from "react-syntax-highlighter/dist/esm/styles/prism";
import { Clipboard, Check, FileCode } from "lucide-react";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { ScrollArea } from "@/components/ui/scroll-area";

export interface CodeBlockProps {
  code: string;
  language?: string;
  fileName?: string;
  startLine?: number;
  className?: string;
}

export function CodeBlock({
  code,
  language = "typescript",
  fileName,
  startLine,
  className,
}: CodeBlockProps) {
  const [copied, setCopied] = useState(false);

  const handleCopy = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(code);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // Fallback: ignore copy errors
    }
  }, [code]);

  const showLineNumbers = startLine !== undefined && startLine !== null;

  return (
    <div
      className={cn(
        "rounded-lg border border-[#e8e0d4] bg-[#faf6f0] shadow-sm overflow-hidden",
        className
      )}
    >
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-2.5 border-b border-[#e8e0d4] bg-[#f5f0e8]">
        <div className="flex items-center gap-2 min-w-0">
          <FileCode className="h-4 w-4 text-[#8c7b6b] shrink-0" />
          <span className="text-sm font-medium text-[#5c4f42] truncate">
            {fileName || language}
          </span>
        </div>
        <TooltipProvider delayDuration={200}>
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                variant="ghost"
                size="icon"
                className="h-7 w-7 shrink-0 text-[#8c7b6b] hover:text-[#5c4f42] hover:bg-[#ebe5db]"
                onClick={handleCopy}
                aria-label={copied ? "Copied" : "Copy code"}
              >
                {copied ? (
                  <Check className="h-3.5 w-3.5 text-emerald-600" />
                ) : (
                  <Clipboard className="h-3.5 w-3.5" />
                )}
              </Button>
            </TooltipTrigger>
            <TooltipContent side="left" sideOffset={6}>
              <p>{copied ? "Copied" : "Copy code"}</p>
            </TooltipContent>
          </Tooltip>
        </TooltipProvider>
      </div>

      {/* Code */}
      <ScrollArea className="max-h-[480px]">
        <div className="relative">
          <SyntaxHighlighter
            language={language}
            style={oneDark}
            showLineNumbers={showLineNumbers}
            startingLineNumber={startLine ?? 1}
            wrapLines={false}
            customStyle={{
              margin: 0,
              padding: "1rem 1.25rem",
              fontSize: "0.8125rem",
              lineHeight: "1.6",
              background: "#2d2a2e",
              borderRadius: 0,
            }}
            lineNumberStyle={{
              minWidth: "2.5em",
              paddingRight: "1em",
              color: "#65606b",
              textAlign: "right",
              userSelect: "none",
            }}
            codeTagProps={{
              style: {
                fontFamily:
                  'ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace',
              },
            }}
          >
            {code}
          </SyntaxHighlighter>
        </div>
      </ScrollArea>
    </div>
  );
}
