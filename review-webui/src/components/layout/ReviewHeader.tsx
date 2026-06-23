import { Settings, LogOut } from "lucide-react";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";

export interface ReviewHeaderProps {
  connectionStatus: string;
  modelName: string | null;
  onOpenSettings?: () => void;
  onLogout?: () => void;
}

const STATUS_CONFIG: Record<
  string,
  { color: string; label: string; pulse: boolean }
> = {
  open: { color: "bg-emerald-500", label: "Connected", pulse: false },
  connected: { color: "bg-emerald-500", label: "Connected", pulse: false },
  idle: { color: "bg-emerald-500", label: "Connected", pulse: false },
  connecting: {
    color: "bg-amber-400",
    label: "Connecting...",
    pulse: true,
  },
  reconnecting: {
    color: "bg-amber-400",
    label: "Reconnecting...",
    pulse: true,
  },
  closed: { color: "bg-red-500", label: "Disconnected", pulse: false },
  error: { color: "bg-red-500", label: "Error", pulse: false },
};

export function ReviewHeader({
  connectionStatus,
  modelName,
  onOpenSettings,
  onLogout,
}: ReviewHeaderProps) {
  const status = STATUS_CONFIG[connectionStatus] ?? {
    color: "bg-muted-foreground",
    label: connectionStatus,
    pulse: false,
  };

  return (
    <TooltipProvider delayDuration={300}>
      <header
        className={cn(
          "flex h-[44px] shrink-0 items-center justify-between border-b bg-card px-3",
          "shadow-[0_1px_2px_0_hsl(var(--foreground)/0.04)]",
        )}
      >
        {/* Left: Logo + brand */}
        <div className="flex items-center gap-2">
          <img
            src="/logo.png"
            alt="Review Agent"
            className="h-7 w-7 rounded-md object-cover"
          />
          <span className="text-sm font-semibold tracking-tight text-foreground">
            Review Agent
          </span>
        </div>

        {/* Right: Connection status + model + actions */}
        <div className="flex items-center gap-1.5">
          <Tooltip>
            <TooltipTrigger asChild>
              <div className="flex items-center gap-2 cursor-default px-2">
                <span className="relative flex h-2 w-2">
                  {status.pulse && (
                    <span
                      className={cn(
                        "absolute inline-flex h-full w-full animate-ping rounded-full opacity-75",
                        status.color,
                      )}
                    />
                  )}
                  <span
                    className={cn(
                      "relative inline-flex h-2 w-2 rounded-full",
                      status.color,
                    )}
                  />
                </span>
                <span className="text-xs text-muted-foreground">
                  {status.label}
                </span>
              </div>
            </TooltipTrigger>
            <TooltipContent>
              <p>WebSocket: {connectionStatus}</p>
            </TooltipContent>
          </Tooltip>

          {modelName && (
            <>
              <div className="h-3 w-px bg-border" />
              <span className="text-[11px] font-medium text-muted-foreground/80">
                {modelName}
              </span>
            </>
          )}

          <div className="h-3 w-px bg-border mx-0.5" />

          {/* Settings button */}
          {onOpenSettings && (
            <Tooltip>
              <TooltipTrigger asChild>
                <Button
                  variant="ghost"
                  size="icon"
                  className="h-6 w-6"
                  onClick={onOpenSettings}
                  aria-label="Settings"
                >
                  <Settings className="h-3 w-3" />
                </Button>
              </TooltipTrigger>
              <TooltipContent>
                <p>Settings (Ctrl+,)</p>
              </TooltipContent>
            </Tooltip>
          )}

          {/* Logout button */}
          {onLogout && (
            <Tooltip>
              <TooltipTrigger asChild>
                <Button
                  variant="ghost"
                  size="icon"
                  className="h-6 w-6"
                  onClick={onLogout}
                  aria-label="Logout"
                >
                  <LogOut className="h-3 w-3" />
                </Button>
              </TooltipTrigger>
              <TooltipContent>
                <p>Logout</p>
              </TooltipContent>
            </Tooltip>
          )}
        </div>
      </header>
    </TooltipProvider>
  );
}
