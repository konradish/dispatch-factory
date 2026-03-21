import { useState, useCallback, useEffect, useRef } from "react";
import { detachTerminal } from "@/lib/api";

export interface TerminalTab {
  sessionName: string;
  port: number;
}

interface TerminalPanelProps {
  tabs: TerminalTab[];
  onRemoveTab: (sessionName: string) => void;
  visible: boolean;
  onToggle: () => void;
  terminalEnabled: boolean;
}

export default function TerminalPanel({
  tabs,
  onRemoveTab,
  visible,
  onToggle,
  terminalEnabled,
}: TerminalPanelProps) {
  const [activeTab, setActiveTab] = useState<string | null>(null);
  const [height, setHeight] = useState(350);
  const dragging = useRef(false);
  const startY = useRef(0);
  const startHeight = useRef(0);

  // Set active tab to first tab if current is gone
  useEffect(() => {
    if (tabs.length > 0 && (!activeTab || !tabs.find((t) => t.sessionName === activeTab))) {
      setActiveTab(tabs[0].sessionName);
    } else if (tabs.length === 0) {
      setActiveTab(null);
    }
  }, [tabs, activeTab]);

  // Keyboard: Escape to close
  useEffect(() => {
    function handleKey(e: KeyboardEvent) {
      if (e.key === "Escape" && visible) {
        onToggle();
      }
    }
    window.addEventListener("keydown", handleKey);
    return () => window.removeEventListener("keydown", handleKey);
  }, [visible, onToggle]);

  // Resize drag handlers
  const onMouseDown = useCallback((e: React.MouseEvent) => {
    dragging.current = true;
    startY.current = e.clientY;
    startHeight.current = height;
    e.preventDefault();

    function onMouseMove(ev: MouseEvent) {
      if (!dragging.current) return;
      const delta = startY.current - ev.clientY;
      setHeight(Math.max(150, Math.min(800, startHeight.current + delta)));
    }
    function onMouseUp() {
      dragging.current = false;
      document.removeEventListener("mousemove", onMouseMove);
      document.removeEventListener("mouseup", onMouseUp);
    }
    document.addEventListener("mousemove", onMouseMove);
    document.addEventListener("mouseup", onMouseUp);
  }, [height]);

  async function handleDetach(sessionName: string) {
    await detachTerminal(sessionName);
    onRemoveTab(sessionName);
  }

  if (!visible) return null;

  const activeTerminal = tabs.find((t) => t.sessionName === activeTab);

  return (
    <div
      className="fixed bottom-0 left-0 right-0 bg-bg-surface border-t border-gray-800 z-50 flex flex-col"
      style={{ height }}
    >
      {/* Drag handle */}
      <div
        className="h-1.5 cursor-row-resize bg-gray-800 hover:bg-accent-blue/50 transition-colors flex-shrink-0"
        onMouseDown={onMouseDown}
      />

      {/* Tab bar */}
      <div className="flex items-center justify-between px-3 py-1.5 border-b border-gray-800 flex-shrink-0">
        <div className="flex items-center gap-1 overflow-x-auto">
          {tabs.length === 0 ? (
            <span className="text-xs text-gray-600 mono px-2">
              No terminals attached
            </span>
          ) : (
            tabs.map((tab) => (
              <button
                key={tab.sessionName}
                onClick={() => setActiveTab(tab.sessionName)}
                className={`flex items-center gap-2 px-3 py-1 rounded text-xs mono transition-colors ${
                  activeTab === tab.sessionName
                    ? "bg-bg-surface-alt text-gray-200"
                    : "text-gray-500 hover:text-gray-300"
                }`}
              >
                <span className="h-1.5 w-1.5 rounded-full bg-accent-green" />
                {tab.sessionName}
              </button>
            ))
          )}
        </div>
        <div className="flex items-center gap-2 flex-shrink-0">
          {activeTab && (
            <button
              onClick={() => handleDetach(activeTab)}
              className="px-2 py-1 text-xs mono text-gray-500 hover:text-gray-300 transition-colors"
            >
              Detach
            </button>
          )}
          <button
            onClick={onToggle}
            className="px-2 py-1 text-xs text-gray-500 hover:text-gray-300 transition-colors"
            title="Close terminal (Esc)"
          >
            &times;
          </button>
        </div>
      </div>

      {/* Terminal content */}
      <div className="flex-1 overflow-hidden">
        {!terminalEnabled ? (
          <div className="flex items-center justify-center h-full text-gray-500 text-sm">
            Terminal embedding disabled. Set{" "}
            <code className="mono mx-1 text-gray-400">
              terminal.enabled = true
            </code>{" "}
            in .dispatch-factory.toml
          </div>
        ) : !activeTerminal ? (
          <div className="flex items-center justify-center h-full text-gray-500 text-sm">
            {tabs.length === 0
              ? "Attach to a session from the Pipeline view to open a terminal."
              : "Terminal not available. Start ttyd or enable terminal in config."}
          </div>
        ) : (
          <div className="relative w-full h-full">
            <div className="absolute top-2 right-3 z-10 text-[10px] mono text-gray-600 bg-black/80 px-2 py-1 rounded pointer-events-none">
              Claude Code --print mode: terminal is quiet while thinking, output appears when done
            </div>
            <iframe
              key={activeTerminal.sessionName}
              src={`http://127.0.0.1:${activeTerminal.port}`}
              className="w-full h-full border-0"
              title={`Terminal: ${activeTerminal.sessionName}`}
            />
          </div>
        )}
      </div>
    </div>
  );
}
