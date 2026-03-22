import { useState, useEffect, useCallback } from "react";
import PipelineList from "@/components/PipelineList";
import TicketCreate from "@/components/TicketCreate";
import HistoryView from "@/components/HistoryView";
import FactoryLog from "@/components/FactoryLog";
import TerminalPanel from "@/components/TerminalPanel";
import SessionDetail from "@/components/SessionDetail";
import type { TerminalTab } from "@/components/TerminalPanel";
import { attachTerminal } from "@/lib/api";
type Tab = "pipeline" | "create" | "history" | "log";

export default function App() {
  const [activeTab, setActiveTab] = useState<Tab>("pipeline");
  const [terminalVisible, setTerminalVisible] = useState(false);
  const [terminalTabs, setTerminalTabs] = useState<TerminalTab[]>([]);
  const [terminalEnabled] = useState(true); // Will be driven by config later
  const [selectedSession, setSelectedSession] = useState<string | null>(null);

  const toggleTerminal = useCallback(() => {
    setTerminalVisible((v) => !v);
  }, []);

  // Keyboard shortcuts
  useEffect(() => {
    function handleKey(e: KeyboardEvent) {
      // Ignore when typing in inputs
      const tag = (e.target as HTMLElement)?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
      // Ignore when inside iframe
      if ((e.target as HTMLElement)?.closest?.("iframe")) return;

      if (e.key === "t" || e.key === "T") {
        toggleTerminal();
      } else if (e.key === "n" || e.key === "N") {
        setActiveTab("create");
      }
    }
    window.addEventListener("keydown", handleKey);
    return () => window.removeEventListener("keydown", handleKey);
  }, [toggleTerminal]);

  function handleAttachTerminal(sessionName: string, port: number) {
    setTerminalTabs((prev) => {
      if (prev.find((t) => t.sessionName === sessionName)) return prev;
      return [...prev, { sessionName, port }];
    });
    setTerminalVisible(true);
  }

  async function handleDispatched(stdout: string) {
    // Parse session ID from dispatch stdout: "session : worker-recipebrain-1615"
    const match = stdout.match(/session\s*:\s*([\w-]+)/);
    if (match) {
      const sessionId = match[1];
      // Auto-attach terminal
      const result = await attachTerminal(sessionId);
      if (result.data) {
        handleAttachTerminal(result.data.session, result.data.port);
      }
    }
    // Switch to pipeline view
    setActiveTab("pipeline");
  }

  function handleRemoveTab(sessionName: string) {
    setTerminalTabs((prev) =>
      prev.filter((t) => t.sessionName !== sessionName)
    );
  }

  return (
    <div
      className="min-h-screen flex flex-col"
      style={{ paddingBottom: terminalVisible ? 350 : 0 }}
    >
      {/* Header */}
      <header className="sticky top-0 z-40 bg-bg-base/80 backdrop-blur-sm border-b border-gray-800">
        <div className="max-w-6xl mx-auto px-6 py-3 flex items-center justify-between">
          <div className="flex items-center gap-4">
            <h1 className="mono text-sm font-semibold tracking-wider text-gray-200">
              DISPATCH FACTORY
            </h1>
            <div className="flex items-center gap-1.5">
              <span className="h-2 w-2 rounded-full bg-accent-green animate-pulse" />
              <span className="text-xs text-gray-500">online</span>
            </div>
          </div>

          <nav className="flex items-center gap-1">
            <button
              onClick={() => setActiveTab("pipeline")}
              className={`px-4 py-1.5 text-sm rounded transition-colors ${
                activeTab === "pipeline"
                  ? "bg-bg-surface-alt text-gray-200"
                  : "text-gray-500 hover:text-gray-300"
              }`}
            >
              Pipeline
            </button>
            <button
              onClick={() => setActiveTab("create")}
              className={`px-4 py-1.5 text-sm rounded transition-colors ${
                activeTab === "create"
                  ? "bg-bg-surface-alt text-gray-200"
                  : "text-gray-500 hover:text-gray-300"
              }`}
            >
              Create Ticket
            </button>
            <button
              onClick={() => setActiveTab("history")}
              className={`px-4 py-1.5 text-sm rounded transition-colors ${
                activeTab === "history"
                  ? "bg-bg-surface-alt text-gray-200"
                  : "text-gray-500 hover:text-gray-300"
              }`}
            >
              History
            </button>
            <button
              onClick={() => setActiveTab("log")}
              className={`px-4 py-1.5 text-sm rounded transition-colors ${
                activeTab === "log"
                  ? "bg-bg-surface-alt text-gray-200"
                  : "text-gray-500 hover:text-gray-300"
              }`}
            >
              Factory Log
            </button>
            <div className="w-px h-5 bg-gray-800 mx-2" />
            <button
              onClick={toggleTerminal}
              className={`px-3 py-1.5 text-xs mono rounded transition-colors ${
                terminalVisible
                  ? "bg-accent-green/20 text-accent-green"
                  : "text-gray-500 hover:text-gray-300"
              }`}
              title="Toggle terminal (T)"
            >
              Terminal
            </button>
          </nav>
        </div>
      </header>

      {/* Main content */}
      <main className="flex-1 max-w-6xl mx-auto w-full px-6 py-6">
        {activeTab === "pipeline" && (
          <PipelineList onAttachTerminal={handleAttachTerminal} onSelectSession={setSelectedSession} />
        )}
        {activeTab === "create" && (
          <TicketCreate onDispatched={handleDispatched} />
        )}
        {activeTab === "history" && <HistoryView onSelectSession={setSelectedSession} />}
        {activeTab === "log" && <FactoryLog onSelectSession={setSelectedSession} />}
      </main>

      {/* Keyboard hints */}
      <div className="fixed bottom-2 right-4 z-30 flex gap-3 text-xs text-gray-700">
        <kbd className="mono px-1.5 py-0.5 border border-gray-800 rounded">
          T
        </kbd>
        <span>terminal</span>
        <kbd className="mono px-1.5 py-0.5 border border-gray-800 rounded">
          N
        </kbd>
        <span>new ticket</span>
      </div>

      {/* Session detail slide-over */}
      {selectedSession && (
        <SessionDetail
          sessionId={selectedSession}
          onClose={() => setSelectedSession(null)}
        />
      )}

      {/* Terminal panel */}
      <TerminalPanel
        tabs={terminalTabs}
        onRemoveTab={handleRemoveTab}
        visible={terminalVisible}
        onToggle={toggleTerminal}
        terminalEnabled={terminalEnabled}
      />
    </div>
  );
}
