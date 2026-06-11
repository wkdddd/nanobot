import { MessageBubble } from "@/components/MessageBubble";
import {
  AgentActivityCluster,
  isAgentActivityMember,
} from "@/components/thread/AgentActivityCluster";
import { PermissionCard } from "@/components/thread/PermissionCard";
import { fmtDateTime } from "@/lib/format";
import type { UIMessage } from "@/lib/types";

const DATE_SEPARATOR_MIN_GAP_MS = 5 * 60 * 1000;

interface ThreadMessagesProps {
  messages: UIMessage[];
  /** When true, agent turn still in flight — keeps activity cluster expanded. */
  isStreaming?: boolean;
  onPermissionRespond?: (requestId: string, approved: boolean) => void;
}

export type DisplayUnit =
  | { type: "cluster"; messages: UIMessage[] }
  | { type: "date-separator"; id: string; createdAt: number }
  | { type: "single"; message: UIMessage };

/** True when this unit index is the last assistant text slice before the next user message (or end of thread). */
export function isFinalAssistantSliceBeforeNextUser(
  units: DisplayUnit[],
  index: number,
): boolean {
  const u = units[index];
  if (u.type !== "single" || u.message.role !== "assistant") return true;
  for (let j = index + 1; j < units.length; j++) {
    const v = units[j];
    if (v.type === "date-separator") continue;
    if (v.type === "single" && v.message.role === "user") break;
    return false;
  }
  return true;
}

function buildDisplayUnits(messages: UIMessage[]): DisplayUnit[] {
  const out: DisplayUnit[] = [];
  let i = 0;
  let lastDateSeparatorAt: number | null = null;
  while (i < messages.length) {
    const m = messages[i];
    if (isAgentActivityMember(m)) {
      const cluster: UIMessage[] = [];
      while (i < messages.length && isAgentActivityMember(messages[i])) {
        cluster.push(messages[i]);
        i += 1;
      }
      out.push({ type: "cluster", messages: cluster });
      continue;
    }
    if (
      m.role === "user"
      && Number.isFinite(m.createdAt)
      && (
        lastDateSeparatorAt === null
        || m.createdAt - lastDateSeparatorAt >= DATE_SEPARATOR_MIN_GAP_MS
      )
    ) {
      out.push({ type: "date-separator", id: `date-${m.id}`, createdAt: m.createdAt });
      lastDateSeparatorAt = m.createdAt;
    }
    out.push({ type: "single", message: m });
    i += 1;
  }
  return out;
}

export function ThreadMessages({ messages, isStreaming = false, onPermissionRespond }: ThreadMessagesProps) {
  const units = buildDisplayUnits(messages);

  return (
    <div className="flex w-full flex-col">
      {units.map((unit, index) => {
        const prev = units[index - 1];
        const marginTop =
          index > 0
            ? marginAfterPrevUnit(prev)
            : "";
        const next = units[index + 1];
        const hasBodyBelow =
          unit.type === "cluster"
          && next?.type === "single"
          && next.message.role === "assistant";

        return (
          <div key={unitKey(unit, index)} className={marginTop}>
            {unit.type === "cluster" ? (
              <>
                <AgentActivityCluster
                  messages={unit.messages}
                  isTurnStreaming={isStreaming}
                  hasBodyBelow={hasBodyBelow}
                />
                {onPermissionRespond && (() => {
                  const allRecords = unit.messages.flatMap(m => m.permissionRecords ?? []);
                  return allRecords.length > 0 ? (
                    <div className="mt-1.5">
                      <PermissionCard records={allRecords} onRespond={onPermissionRespond} />
                    </div>
                  ) : null;
                })()}
              </>
            ) : unit.type === "date-separator" ? (
              <DateSeparator createdAt={unit.createdAt} />
            ) : (
              <>
                <MessageBubble
                  message={unit.message}
                  showAssistantCopyAction={
                    unit.message.role === "assistant"
                      ? isFinalAssistantSliceBeforeNextUser(units, index)
                      : true
                  }
                />
                {unit.message.role === "assistant"
                  && unit.message.permissionRecords
                  && unit.message.permissionRecords.length > 0
                  && onPermissionRespond ? (
                  <div className="mt-1.5">
                    <PermissionCard
                      records={unit.message.permissionRecords}
                      onRespond={onPermissionRespond}
                    />
                  </div>
                ) : null}
              </>
            )}
          </div>
        );
      })}
    </div>
  );
}

function unitKey(unit: DisplayUnit, index: number): string {
  if (unit.type === "cluster") {
    const anchor = unit.messages[0]?.id;
    return anchor != null ? `cluster-${anchor}` : `cluster-idx-${index}`;
  }
  if (unit.type === "date-separator") return unit.id;
  return unit.message.id;
}

function marginAfterPrevUnit(prev: DisplayUnit): string {
  if (prev.type === "date-separator") {
    return "mt-3";
  }
  if (prev.type === "cluster") {
    return "mt-4";
  }
  const p = prev.message;
  const denseP =
    p.kind === "trace"
    || (
      p.role === "assistant"
      && p.content.trim().length === 0
      && (!!p.reasoning || !!p.reasoningStreaming)
    );
  if (denseP) {
    return "mt-2";
  }
  return "mt-5";
}

function DateSeparator({ createdAt }: { createdAt: number }) {
  const label = fmtDateTime(createdAt);
  if (!label) return null;
  const dateTime = Number.isFinite(createdAt) ? new Date(createdAt).toISOString() : undefined;
  return (
    <div className="flex w-full justify-center">
      <time
        dateTime={dateTime}
        className="max-w-full rounded-full bg-muted/65 px-2.5 py-1 text-[11px] leading-none text-muted-foreground/80"
      >
        {label}
      </time>
    </div>
  );
}
