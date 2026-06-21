import { useRef, useState, useCallback } from "react";
import { cn } from "@/lib/utils";
import { ArrowUp } from "lucide-react";
import { Button } from "@/components/ui/button";

interface ChatInputProps {
  onSend: (text: string) => void;
  disabled?: boolean;
  placeholder?: string;
}

export function ChatInput({
  onSend,
  disabled = false,
  placeholder = "Type a message\u2026",
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
    <div className="flex items-end gap-2 border-t bg-background px-4 py-3">
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
            "flex w-full resize-none rounded-xl border border-input bg-background px-4 py-3 pr-10 text-sm ring-offset-background placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50",
            "min-h-[44px] leading-6 overflow-hidden"
          )}
          style={{ height: "auto" }}
        />
      </div>
      <Button
        size="icon"
        className={cn(
          "h-10 w-10 shrink-0 rounded-xl transition-opacity",
          isEmpty && "opacity-50"
        )}
        onClick={handleSend}
        disabled={disabled || isEmpty}
        aria-label="Send message"
      >
        <ArrowUp className="h-4 w-4" />
      </Button>
    </div>
  );
}
