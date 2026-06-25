import { GripVertical } from "lucide-react";
import {
  Group,
  Panel,
  Separator,
  type GroupProps,
  type SeparatorProps,
} from "react-resizable-panels";

import { cn } from "@/lib/utils";

/**
 * Thin shadcn-style wrappers around `react-resizable-panels` v4.
 *
 * v4 API notes (differs from the classic v2 shadcn component):
 * - Exports are `Group` / `Panel` / `Separator` (not PanelGroup/PanelResizeHandle).
 * - Orientation is set via `orientation` (not `direction`).
 * - Size units: numeric = pixels, unitless string = percentage, also supports
 *   "px", "vw", "vh", "rem", "em". This lets us mix pixel min/default with a
 *   viewport-relative max (e.g. minSize={300}, maxSize="50vw").
 */
function ResizablePanelGroup({ className, ...props }: GroupProps) {
  return <Group className={cn("h-full w-full", className)} {...props} />;
}

const ResizablePanel = Panel;

function ResizableHandle({ className, ...props }: SeparatorProps) {
  return (
    <Separator
      className={cn(
        "relative flex w-px items-center justify-center bg-border after:absolute after:inset-y-0 after:left-1/2 after:w-1.5 after:-translate-x-1/2 hover:bg-accent",
        className,
      )}
      {...props}
    >
      <span className="z-10 flex h-4 w-3 items-center justify-center rounded-sm border bg-border">
        <GripVertical className="size-2.5" />
      </span>
    </Separator>
  );
}

export { ResizableHandle, ResizablePanel, ResizablePanelGroup };
