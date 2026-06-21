import type { ReactNode } from "react";
import { PanelLeft } from "lucide-react";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { ReviewHeader } from "./ReviewHeader";
import { ReviewSidebar } from "./ReviewSidebar";
import { SessionInfoBar, type SessionInfo } from "./SessionInfoBar";
import type { ChatSummary } from "@/lib/types";

export interface ReviewShellProps {
  /** Header props */
  connectionStatus: string;
  modelName: string | null;
  onOpenSettings?: () => void;
  onLogout?: () => void;

  /** Sidebar props */
  autoTaskSessions: ChatSummary[];
  dailySessions: ChatSummary[];
  activeKey: string | null;
  sidebarLoading: boolean;
  sidebarError?: string | null;
  onTaskSelect: (key: string) => void;
  onNewTask: () => void;
  onTaskDelete: (key: string) => void;
  onOpenAutoTasks: () => void;

  /** Content areas */
  mainContent: ReactNode;
  rightPanelContent?: ReactNode;

  /** Panel visibility */
  sidebarOpen: boolean;
  onToggleSidebar: () => void;

  /** Session info bar */
  sessionInfo?: SessionInfo | null;
}

export function ReviewShell({
  connectionStatus,
  modelName,
  onOpenSettings,
  onLogout,
  autoTaskSessions,
  dailySessions,
  activeKey,
  sidebarLoading,
  sidebarError,
  onTaskSelect,
  onNewTask,
  onTaskDelete,
  onOpenAutoTasks,
  mainContent,
  rightPanelContent,
  sidebarOpen,
  onToggleSidebar,
  sessionInfo,
}: ReviewShellProps) {
  return (
    <div className="flex h-screen flex-col overflow-hidden bg-background">
      {/* Fixed header */}
      <ReviewHeader
        connectionStatus={connectionStatus}
        modelName={modelName}
        onOpenSettings={onOpenSettings}
        onLogout={onLogout}
      />

      {/* Body: sidebar | main+right */}
      <div className="flex flex-1 overflow-hidden relative">
        {/* Left sidebar (collapsible) */}
        {sidebarOpen ? (
          <div className="flex shrink-0 relative">
            <ReviewSidebar
              autoTaskSessions={autoTaskSessions}
              dailySessions={dailySessions}
              activeKey={activeKey}
              loading={sidebarLoading}
              error={sidebarError}
              onSelect={onTaskSelect}
              onNewTask={onNewTask}
              onDelete={onTaskDelete}
              onOpenAutoTasks={onOpenAutoTasks}
            />
          </div>
        ) : null}

        {/* Main content area + right panel (always rendered together) */}
        <main className="flex-1 overflow-hidden flex flex-col relative">
          {/* Toolbar: sidebar toggle + session info in one row */}
          <div className="flex items-center px-2.5 py-1 border-b bg-card/50 shrink-0 gap-1.5">
            <Button
              variant="ghost"
              size="icon"
              className="h-5 w-5 rounded shrink-0"
              onClick={onToggleSidebar}
              title={sidebarOpen ? "Collapse sidebar (Ctrl+B)" : "Expand sidebar (Ctrl+B)"}
            >
              <PanelLeft className="h-3 w-3" />
            </Button>

            {/* Session info inline */}
            <SessionInfoBar info={sessionInfo || null} />
          </div>

          {/* Content row: chat thread + right panel */}
          <div className="flex flex-1 min-h-0 overflow-hidden">
            {/* Chat thread */}
            <div className="flex-1 min-w-0 overflow-hidden">
              {mainContent}
            </div>

            {/* Right panel (always visible if content exists) */}
            {rightPanelContent ? (
              <aside className="h-full w-[300px] shrink-0 overflow-y-auto border-l bg-card scrollbar-thin scrollbar-track-transparent">
                {rightPanelContent}
              </aside>
            ) : null}
          </div>
        </main>
      </div>
    </div>
  );
}
