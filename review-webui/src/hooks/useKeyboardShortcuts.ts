import { useEffect, useCallback } from "react";

export interface KeyboardShortcutsOptions {
  onNewTask: () => void;
  onOpenSettings: () => void;
  onToggleSidebar: () => void;
  onToggleRightPanel: () => void;
  onEscape: () => void;
}

/**
 * 监听键盘快捷键的 Hook。
 *
 * 支持的快捷键：
 * - Ctrl/Cmd + N      → 新建任务
 * - Ctrl/Cmd + ,      → 打开设置
 * - Ctrl/Cmd + B      → 切换侧边栏
 * - Ctrl/Cmd + Shift + B → 切换右侧面板
 * - Escape            →  Escape 回调
 */
export function useKeyboardShortcuts({
  onNewTask,
  onOpenSettings,
  onToggleSidebar,
  onToggleRightPanel,
  onEscape,
}: KeyboardShortcutsOptions): void {
  const handleKeyDown = useCallback(
    (event: KeyboardEvent) => {
      const { key, ctrlKey, metaKey, shiftKey } = event;
      const isMod = ctrlKey || metaKey;

      // Ctrl/Cmd + Shift + B → 切换右侧面板
      if (isMod && shiftKey && key.toLowerCase() === "b") {
        event.preventDefault();
        onToggleRightPanel();
        return;
      }

      // Ctrl/Cmd + B → 切换侧边栏
      if (isMod && key.toLowerCase() === "b") {
        event.preventDefault();
        onToggleSidebar();
        return;
      }

      // Ctrl/Cmd + N → 新建任务
      if (isMod && key.toLowerCase() === "n") {
        event.preventDefault();
        onNewTask();
        return;
      }

      // Ctrl/Cmd + , → 打开设置
      if (isMod && key === ",") {
        event.preventDefault();
        onOpenSettings();
        return;
      }

      // Escape
      if (key === "Escape") {
        onEscape();
        return;
      }
    },
    [onNewTask, onOpenSettings, onToggleSidebar, onToggleRightPanel, onEscape]
  );

  useEffect(() => {
    window.addEventListener("keydown", handleKeyDown);
    return () => {
      window.removeEventListener("keydown", handleKeyDown);
    };
  }, [handleKeyDown]);
}
