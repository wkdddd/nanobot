import { useRef, useState, useCallback } from "react";
import { cn } from "@/lib/utils";
import { ArrowUp, Square } from "lucide-react";
import { Button } from "@/components/ui/button";

interface ChatInputProps {
  onSend: (text: string) => void;
  disabled?: boolean;
  placeholder?: string;
  isStreaming?: boolean;
  onPause?: () => void;
}

export function ChatInput({
  onSend,
  disabled = false,
  placeholder = "Type a message…",
  isStreaming = false,
  onPause,
}: ChatInputProps) {
  const [text, setText] = useState("");
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const adjustHeight = useCallback(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${el.scrollHeight}px`;
  }, []);

  const handleChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    setText(e.target.value);
    requestAnimationFrame(adjustHeight);
  };

  const handleSend = useCallback(() => {
    const trimmed = text.trim();
    if (!trimmed || disabled) return;
    onSend(trimmed);
    setText("");
    const el = textareaRef.current;
    if (el) {
      el.style.height = "auto";
    }
  }, [text, disabled, onSend]);

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const isEmpty = text.trim().length === 0;

  return (
    <div className="flex items-end gap-1.5 border-t bg-background px-3 py-2">
      <div className="relative flex-1">
        <textarea
          ref={textareaRef}
          value={text}
          onChange={handleChange}
          onKeyDown={handleKeyDown}
          disabled={disabled}
          placeholder={placeholder}
          name="message"
          aria-label="Message input"
          rows={1}
          className={cn(
            "flex w-full resize-none rounded-lg border border-input bg-background px-3 py-2 pr-9 text-xs ring-offset-background placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50",
            "min-h-[36px] leading-5 overflow-hidden"
          )}
          style={{ height: "auto" }}
        />
      </div>
      {isStreaming ? (
        <Button
          size="icon"
          className="h-8 w-8 shrink-0 rounded-lg bg-destructive/90 hover:bg-destructive text-destructive-foreground transition-opacity"
          onClick={onPause}
          aria-label="Stop task"
        >
          <Square className="h-3.5 w-3.5" />
        </Button>
      ) : (
        <Button
          size="icon"
          className={cn(
            "h-8 w-8 shrink-0 rounded-lg transition-opacity",
            isEmpty && "opacity-50"
          )}
          onClick={handleSend}
          disabled={disabled || isEmpty}
          aria-label="Send message"
        >
          <ArrowUp className="h-3.5 w-3.5" />
        </Button>
      )}
    </div>
  );
}
