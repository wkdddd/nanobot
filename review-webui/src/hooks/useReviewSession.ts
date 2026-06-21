import { useState, useCallback, useRef, useEffect } from "react";
import type { InboundEvent } from "@/lib/types";
import type { NanobotClient } from "@/lib/nanobot-client";
import type {
  OutboundReviewContext,
  ReviewAction,
  ReviewDepth,
  ReviewFocus,
  ReviewTargetType,
  ToolProgressEvent,
  UIMessage,
} from "@/lib/types";

export type ReviewPhase =
  | "idle"
  | "configuring"
  | "submitting"
  | "planning"
  | "prefetching"
  | "reviewing"
  | "validating"
  | "finalizing"
  | "history"
  | "completed"
  | "stopped"
  | "error";

export interface ReviewTask {
  target: string;
  targetType?: ReviewTargetType;
  action?: ReviewAction;
  depth?: ReviewDepth;
  focus?: ReviewFocus[];
  targetPaths?: string[];
}

export interface DimensionResult {
  dimension: string;
  status: string;
  acceptedCount: number;
  rejectedCount: number;
  uncertainCount: number;
}

export interface Finding {
  severity: string;
  dimension: string;
  file: string;
  line: number | null;
  title: string;
  impact: string;
  recommendation: string;
  confidence?: string;
  evidence?: string;
}

export interface ChatMessage {
  id: string;
  role: "user" | "agent";
  type: "text" | "finding" | "report";
  content: string;
  timestamp: number;
  finding?: Finding;
  thinking?: string;
  streaming?: boolean;
}

export interface ReviewSessionState {
  phase: ReviewPhase;
  task: ReviewTask | null;
  dimensions: DimensionResult[];
  findings: Finding[];
  reportMarkdown: string;
  logs: string[];
  error: string | null;
  messages: ChatMessage[];
}

const INITIAL_STATE: ReviewSessionState = {
  phase: "idle",
  task: null,
  dimensions: [],
  findings: [],
  reportMarkdown: "",
  logs: [],
  error: null,
  messages: [],
};

function labelValue(value: string | undefined, fallback = "auto"): string {
  const trimmed = value?.trim();
  return trimmed || fallback;
}

function formatReviewFocus(focus: ReviewFocus[] | undefined): string {
  return focus && focus.length > 0 ? focus.join(", ") : "all";
}

function formatReviewPaths(paths: string[] | undefined): string {
  return paths && paths.length > 0 ? paths.join(", ") : "all";
}

function isReviewTask(review: UIMessage["review"] | ReviewTask): review is ReviewTask {
  if (!review) return false;
  return "targetType" in review || "depth" in review || "targetPaths" in review;
}

function formatReviewRequestContent(
  content: string,
  review: UIMessage["review"] | ReviewTask | undefined,
): string {
  if (!review) return content;
  const task = isReviewTask(review);
  const target = review.target;
  const targetType = task ? review.targetType : review.target_type;
  const mode = task ? review.depth : review.mode;
  const action = review.action;
  const focus = review.focus;
  const targetPaths = task ? review.targetPaths : review.target_paths;
  const title = content.trim() || "审查";
  const lines = [
    title,
    `目标: ${labelValue(target, "(not set)")}`,
    `类型: ${labelValue(targetType)}`,
    `模式: ${labelValue(mode, "full")}`,
    `动作: ${labelValue(action, "repo")}`,
    `关注: ${formatReviewFocus(focus)}`,
    `路径: ${formatReviewPaths(targetPaths)}`,
  ];
  return lines.join("\n");
}

function isLikelyReviewReport(content: string): boolean {
  const text = content.trim();
  if (!text) return false;
  return (
    /^#{1,3}\s+.*(?:review|审查|报告)/im.test(text) ||
    /(?:^|\n)\s*\|\s*(?:severity|严重级别|file|文件|finding|问题)\s*\|/i.test(text) ||
    /(?:^|\n)\s*(?:findings|审查发现|recommendations|建议)\s*[:：]?/i.test(text)
  );
}

export function uiMessageToChatMessage(message: UIMessage): ChatMessage | null {
  if (message.kind === "trace") return null;
  if (message.role !== "user" && message.role !== "assistant") return null;
  const content = message.role === "user"
    ? formatReviewRequestContent(message.content, message.review)
    : message.content;
  const thinking = message.role === "assistant" ? message.reasoning : undefined;
  if (message.role === "assistant" && !content.trim() && !thinking?.trim()) {
    return null;
  }
  const type = message.role === "assistant" && isLikelyReviewReport(message.content) ? "report" : "text";
  return {
    id: message.id,
    role: message.role === "user" ? "user" : "agent",
    type,
    content,
    timestamp: message.createdAt,
    thinking,
    streaming: message.role === "assistant" ? message.reasoningStreaming : undefined,
  };
}

function extractContent(value: unknown): string {
  if (typeof value === "string") return value;
  if (!Array.isArray(value)) return "";
  return value
    .map((part) => {
      if (typeof part === "string") return part;
      if (!part || typeof part !== "object") return "";
      const text = (part as { text?: unknown }).text;
      if (typeof text === "string") return text;
      const type = (part as { type?: unknown }).type;
      if (typeof type === "string" && type !== "text") return `[${type}]`;
      return "";
    })
    .filter(Boolean)
    .join("\n");
}

function numberFromTimestamp(value: unknown, fallback: number): number {
  if (typeof value === "number" && Number.isFinite(value)) {
    return value > 10_000_000_000 ? value : Math.round(value * 1000);
  }
  if (typeof value === "string") {
    const parsed = Date.parse(value);
    if (Number.isFinite(parsed)) return parsed;
  }
  return fallback;
}

function reviewFromMetadata(metadata: unknown): UIMessage["review"] | undefined {
  if (!metadata || typeof metadata !== "object") return undefined;
  const data = metadata as Record<string, unknown>;
  if (data.review && typeof data.review === "object") {
    return data.review as UIMessage["review"];
  }
  const target = typeof data.review_target === "string" ? data.review_target : undefined;
  const targetType = typeof data.review_target_type === "string"
    ? data.review_target_type as OutboundReviewContext["target_type"]
    : undefined;
  const mode = typeof data.review_mode_variant === "string"
    ? data.review_mode_variant as OutboundReviewContext["mode"]
    : undefined;
  const action = typeof data.review_action === "string"
    ? data.review_action as OutboundReviewContext["action"]
    : undefined;
  const focus = Array.isArray(data.review_focus)
    ? data.review_focus.filter((item): item is ReviewFocus => typeof item === "string")
    : undefined;
  const targetPaths = Array.isArray(data.review_target_paths)
    ? data.review_target_paths.filter((item): item is string => typeof item === "string")
    : undefined;
  if (!target && !targetType && !mode && !action && !focus && !targetPaths) {
    return undefined;
  }
  return {
    target,
    target_type: targetType,
    mode,
    action,
    focus,
    target_paths: targetPaths,
  };
}

export function sessionMessageToUIMessage(
  message: Record<string, unknown>,
  index: number,
): UIMessage | null {
  const role = message.role;
  if (role !== "user" && role !== "assistant") return null;
  const content = extractContent(message.content);
  if (!content.trim()) return null;
  const createdAt = numberFromTimestamp(
    message.createdAt ?? message.created_at ?? message.timestamp,
    Date.now() + index,
  );
  const review = reviewFromMetadata(message.metadata);
  return {
    id: typeof message.id === "string" ? message.id : `history-${index}`,
    role,
    content,
    kind: "message",
    createdAt,
    review,
  };
}

function generateId(): string {
  return `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

function appendThinking(message: ChatMessage, text: string, streaming = true): ChatMessage {
  const nextThinking = message.thinking ? `${message.thinking}${text}` : text;
  return {
    ...message,
    thinking: nextThinking,
    streaming: message.type === "report" ? message.streaming : streaming,
  };
}

function appendProgressLine(message: ChatMessage, text: string): ChatMessage {
  const trimmed = text.trim();
  if (!trimmed) return message;
  const prefix = message.thinking && message.thinking.trim() ? "\n" : "";
  return appendThinking(message, `${prefix}${trimmed}\n`, true);
}

function appendProgressLines(message: ChatMessage, lines: string[]): ChatMessage {
  return lines.reduce((next, line) => appendProgressLine(next, line), message);
}

function formatElapsed(ms: unknown): string {
  return typeof ms === "number" && Number.isFinite(ms) ? `${(ms / 1000).toFixed(1)}s` : "";
}

function formatReviewPrefetchEvent(event: ToolProgressEvent): string | null {
  if (event.name !== "review_prefetch") return null;
  const args = event.arguments && typeof event.arguments === "object"
    ? event.arguments as Record<string, unknown>
    : {};
  const action = typeof args.action === "string" ? args.action : "repo";
  const targetType = typeof args.target_type === "string" ? args.target_type : "auto";
  const metadata = event.metadata && typeof event.metadata === "object"
    ? event.metadata as Record<string, unknown>
    : {};
  if (event.phase === "start") {
    return `Preparing review context: ${action} / ${targetType}`;
  }
  if (event.phase === "end") {
    const elapsed = formatElapsed(metadata.elapsed_ms);
    if (event.result === "ok" || event.result === "no_summary") {
      const rawChars = typeof metadata.raw_chars === "number" ? metadata.raw_chars : null;
      const chars = rawChars !== null ? `, ${rawChars} chars` : "";
      return `Review context ready${elapsed ? ` in ${elapsed}` : ""}${chars}`;
    }
    const reason = typeof event.error === "string" && event.error.trim()
      ? `: ${event.error.trim()}`
      : "";
    return `Review context failed${elapsed ? ` after ${elapsed}` : ""}${reason}`;
  }
  if (event.phase === "skip") {
    const reason = typeof event.error === "string" && event.error.trim()
      ? ` (${event.error.trim()})`
      : "";
    return `Review context prefetch skipped${reason}`;
  }
  return null;
}

function progressLinesFromEvent(ev: InboundEvent): string[] {
  if (ev.event !== "message" || ev.kind !== "progress") return [];
  const lines: string[] = [];
  if (ev.text?.trim()) {
    lines.push(ev.text.trim());
  }
  if (Array.isArray(ev.tool_events)) {
    for (const toolEvent of ev.tool_events) {
      const line = formatReviewPrefetchEvent(toolEvent);
      if (line) lines.push(line);
    }
  }
  return lines;
}

function markLastStreamingComplete(messages: ChatMessage[]): ChatMessage[] {
  if (messages.length === 0) return messages;
  const updated = [...messages];
  for (let i = updated.length - 1; i >= 0; i -= 1) {
    const item = updated[i];
    if (item.role === "agent" && item.streaming) {
      updated[i] = { ...item, streaming: false };
      break;
    }
  }
  return updated;
}

function markAllStreamingComplete(messages: ChatMessage[]): ChatMessage[] {
  return messages.map((message) =>
    message.streaming ? { ...message, streaming: false } : message
  );
}

function hasVisibleAgentFinal(messages: ChatMessage[]): boolean {
  return messages.some((message) =>
    message.role === "agent" && message.content.trim().length > 0
  );
}

function hasThinkingOnlyAgent(messages: ChatMessage[]): boolean {
  return messages.some((message) =>
    message.role === "agent"
    && message.content.trim().length === 0
    && !!message.thinking?.trim()
  );
}

function completedOrStoppedPhase(messages: ChatMessage[]): ReviewPhase {
  if (hasVisibleAgentFinal(messages)) return "completed";
  if (hasThinkingOnlyAgent(messages)) return "stopped";
  return "history";
}

function isBusyPhase(phase: ReviewPhase): boolean {
  return phase === "submitting"
    || phase === "prefetching"
    || phase === "reviewing"
    || phase === "finalizing";
}

export function useReviewSession(client: NanobotClient, chatId: string | null) {
  const [state, setState] = useState<ReviewSessionState>(INITIAL_STATE);
  const reportBufferRef = useRef("");
  const currentAgentMessageRef = useRef<string | null>(null);
  const thinkingBufferRef = useRef("");

  const reset = useCallback(() => {
    setState(INITIAL_STATE);
    reportBufferRef.current = "";
    currentAgentMessageRef.current = null;
    thinkingBufferRef.current = "";
  }, []);

  const loadHistory = useCallback((
    messages: UIMessage[],
    task: ReviewTask | null = null,
    error: string | null = null,
    preConverted?: ChatMessage[],
  ) => {
    const chatMessages = preConverted ?? messages
      .map(uiMessageToChatMessage)
      .filter((message): message is ChatMessage => message !== null);
    const reportMarkdown = [...chatMessages].reverse().find((message) => message.type === "report")?.content ?? "";
    setState({
      ...INITIAL_STATE,
      phase: error ? "error" : completedOrStoppedPhase(chatMessages),
      task,
      error,
      reportMarkdown,
      messages: chatMessages,
    });
    reportBufferRef.current = "";
    currentAgentMessageRef.current = null;
    thinkingBufferRef.current = "";
  }, []);

  const startReview = useCallback((task: ReviewTask) => {
    setState((prev) => ({
      ...prev,
      phase: "submitting",
      task,
      error: null,
      logs: [],
      findings: [],
      reportMarkdown: "",
      messages: [
        ...prev.messages,
        {
          id: generateId(),
          role: "user",
          type: "text",
          content: formatReviewRequestContent("审查", task),
          timestamp: Date.now(),
        },
      ],
    }));
    reportBufferRef.current = "";
    currentAgentMessageRef.current = null;
    thinkingBufferRef.current = "";
  }, []);

  const sendFollowUp = useCallback(
    (text: string) => {
      if (!chatId) return;
      const userMsg: ChatMessage = {
        id: generateId(),
        role: "user",
        type: "text",
        content: text,
        timestamp: Date.now(),
      };
      setState((prev) => ({
        ...prev,
        messages: [...prev.messages, userMsg],
      }));
      client.sendMessage(chatId, text);
      currentAgentMessageRef.current = null;
      reportBufferRef.current = "";
      thinkingBufferRef.current = "";
    },
    [client, chatId]
  );

  useEffect(() => {
    if (!chatId || !client) return;

    const unsub = client.onChat(chatId, (ev: InboundEvent) => {
      setState((prev) => {
        if (ev.event === "delta") {
          const text = ev.text || "";
          const kind = ev.kind;
          if (kind === "review_thinking") {
            thinkingBufferRef.current += text;
            const existingId = currentAgentMessageRef.current;
            if (existingId) {
              const updated = prev.messages.map((message) =>
                message.id === existingId ? appendThinking(message, text, true) : message
              );
              return { ...prev, messages: updated, phase: "reviewing" };
            }
            const id = generateId();
            currentAgentMessageRef.current = id;
            return {
              ...prev,
              phase: "reviewing",
              messages: [
                ...prev.messages,
                {
                  id,
                  role: "agent",
                  type: "text",
                  content: "",
                  timestamp: Date.now(),
                  thinking: text,
                  streaming: true,
                },
              ],
            };
          }

          const isReport = kind === "review_report" || reportBufferRef.current.length > 0;
          if (isReport) {
            reportBufferRef.current += text;
            const existingId = currentAgentMessageRef.current;
            if (existingId) {
              const updated = prev.messages.map((message) =>
                message.id === existingId
                  ? {
                      ...message,
                      type: "report" as const,
                      content: message.content + text,
                      thinking: message.thinking || thinkingBufferRef.current || undefined,
                      streaming: true,
                    }
                  : message
              );
              return {
                ...prev,
                phase: "finalizing",
                reportMarkdown: reportBufferRef.current,
                messages: updated,
              };
            }
            const id = generateId();
            currentAgentMessageRef.current = id;
            return {
              ...prev,
              phase: "finalizing",
              reportMarkdown: reportBufferRef.current,
              messages: [
                ...prev.messages,
                {
                  id,
                  role: "agent",
                  type: "report",
                  content: text,
                  timestamp: Date.now(),
                  thinking: thinkingBufferRef.current,
                  streaming: true,
                },
              ],
            };
          }

          const existingId = currentAgentMessageRef.current;
          if (existingId) {
            const updated = prev.messages.map((message) =>
              message.id === existingId && message.type === "text"
                ? { ...message, content: message.content + text, streaming: true }
                : message
            );
            return { ...prev, messages: updated };
          }

          const id = generateId();
          currentAgentMessageRef.current = id;
          return {
            ...prev,
            messages: [
              ...prev.messages,
              {
                id,
                role: "agent",
                type: "text",
                content: text,
                timestamp: Date.now(),
                streaming: true,
              },
            ],
          };
        }

        if (ev.event === "reasoning_delta") {
          const text = ev.text || "";
          thinkingBufferRef.current += text;
          const existingId = currentAgentMessageRef.current;
          if (existingId) {
            const updated = prev.messages.map((message) =>
              message.id === existingId ? appendThinking(message, text, true) : message
            );
            return { ...prev, phase: "reviewing", messages: updated };
          }
          const id = generateId();
          currentAgentMessageRef.current = id;
          return {
            ...prev,
            phase: "reviewing",
            messages: [
              ...prev.messages,
              {
                id,
                role: "agent",
                type: "text",
                content: "",
                timestamp: Date.now(),
                thinking: text,
                streaming: true,
              },
            ],
          };
        }

        if (ev.event === "reasoning_end") {
          return { ...prev, messages: markLastStreamingComplete(prev.messages) };
        }

        if (ev.event === "stream_end") {
          if (ev.kind === "review_thinking") {
            return { ...prev, messages: markLastStreamingComplete(prev.messages) };
          }
          currentAgentMessageRef.current = null;
          return { ...prev, messages: markLastStreamingComplete(prev.messages) };
        }

        if (ev.event === "message") {
          const progressLines = progressLinesFromEvent(ev);
          const newLogs = [...prev.logs];
          if (ev.kind === "tool_hint" || ev.kind === "progress") {
            if (ev.text?.trim()) newLogs.push(ev.text.trim());
            newLogs.push(...progressLines.filter((line) => line !== ev.text?.trim()));
          }

          // 处理 agent_ui blob 中的 finding 流式事件
          if (ev.agent_ui) {
            const uiBlob = ev.agent_ui;

            if (uiBlob.kind === "finding_start") {
              // 创建一个新的流式 finding 消息
              const findingData = (uiBlob.data as Partial<Finding>) || {};
              const findingMsg: ChatMessage = {
                id: generateId(),
                role: "agent",
                type: "finding",
                content: findingData.title || "",
                timestamp: Date.now(),
                finding: {
                  severity: findingData.severity || "medium",
                  dimension: findingData.dimension || "",
                  file: findingData.file || "",
                  line: findingData.line ?? null,
                  title: findingData.title || "",
                  impact: findingData.impact || "",
                  recommendation: findingData.recommendation || "",
                  confidence: findingData.confidence,
                  evidence: findingData.evidence,
                },
                streaming: true,
              };
              return {
                ...prev,
                logs: newLogs,
                messages: [...prev.messages, findingMsg],
              };
            }

            if (uiBlob.kind === "finding_delta") {
              // 更新当前流式 finding 的字段
              const delta = (uiBlob.data as Partial<Finding>) || {};
              const findingId = [...prev.messages].reverse().find((m) => m.type === "finding" && m.streaming)?.id;
              if (!findingId) return { ...prev, logs: newLogs };

              const updated = prev.messages.map((m) => {
                if (m.id !== findingId) return m;
                const updatedFinding = { ...(m.finding || {}), ...delta };
                return {
                  ...m,
                  content: updatedFinding.title || m.content,
                  finding: updatedFinding as Finding,
                };
              });
              return { ...prev, logs: newLogs, messages: updated };
            }

            if (uiBlob.kind === "finding_end") {
              // 完成 finding，标记 streaming=false
              const findingId = [...prev.messages].reverse().find((m) => m.type === "finding" && m.streaming)?.id;
              if (!findingId) return { ...prev, logs: newLogs };

              const finalData = (uiBlob.data as Partial<Finding>) || {};
              const updated = prev.messages.map((m) => {
                if (m.id !== findingId) return m;
                const finalFinding = { ...(m.finding || {}), ...finalData };
                return {
                  ...m,
                  content: finalFinding.title || m.content,
                  finding: finalFinding as Finding,
                  streaming: false,
                };
              });
              return { ...prev, logs: newLogs, messages: updated };
            }
          }

          if (ev.kind === "progress" && progressLines.length > 0) {
            const existingId = currentAgentMessageRef.current;
            if (existingId) {
              const updated = prev.messages.map((message) =>
                message.id === existingId ? appendProgressLines(message, progressLines) : message
              );
              return { ...prev, logs: newLogs, phase: "prefetching", messages: updated };
            }
            const id = generateId();
            currentAgentMessageRef.current = id;
            const thinking = progressLines.map((line) => line.trim()).filter(Boolean).join("\n");
            return {
              ...prev,
              phase: "prefetching",
              logs: newLogs,
              messages: [
                ...prev.messages,
                {
                  id,
                  role: "agent",
                  type: "text",
                  content: "",
                  timestamp: Date.now(),
                  thinking: `${thinking}\n`,
                  streaming: true,
                },
              ],
            };
          }

          if (!ev.kind && ev.text?.trim()) {
            const content = ev.text;
            const isReport = isLikelyReviewReport(content);
            const ordinaryMessage: ChatMessage = {
              id: generateId(),
              role: "agent",
              type: isReport ? "report" : "text",
              content,
              timestamp: Date.now(),
              streaming: false,
            };
            const logs = isReport
              ? [...newLogs, "Review report arrived outside review_report stream"]
              : newLogs;
            return {
              ...prev,
              logs,
              phase: isReport ? "completed" : prev.phase,
              reportMarkdown: isReport ? content : prev.reportMarkdown,
              messages: [...markAllStreamingComplete(prev.messages), ordinaryMessage],
            };
          }

          return { ...prev, logs: newLogs };
        }

        if (ev.event === "turn_end") {
          currentAgentMessageRef.current = null;
          const messages = markLastStreamingComplete(prev.messages);
          const phase = completedOrStoppedPhase(messages);
          return {
            ...prev,
            phase: phase === "history" && isBusyPhase(prev.phase) ? "stopped" : phase,
            messages,
          };
        }

        if (ev.event === "goal_status") {
          if (ev.status !== "idle" || !isBusyPhase(prev.phase)) {
            return prev;
          }
          currentAgentMessageRef.current = null;
          return {
            ...prev,
            phase: "stopped",
            messages: markAllStreamingComplete(prev.messages),
          };
        }

        if (ev.event === "error") {
          currentAgentMessageRef.current = null;
          return {
            ...prev,
            phase: "error",
            error: ev.detail || "Unknown error",
            messages: [
              ...markAllStreamingComplete(prev.messages),
              {
                id: generateId(),
                role: "agent",
                type: "text",
                content: `Error: ${ev.detail || "Unknown error"}`,
                timestamp: Date.now(),
              },
            ],
          };
        }

        if (ev.event === "review_mode_updated") {
          return { ...prev };
        }

        return prev;
      });
    });

    return unsub;
  }, [client, chatId]);

  return { state, reset, loadHistory, startReview, sendFollowUp };
}
