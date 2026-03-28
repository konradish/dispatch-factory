import { useState, useEffect, useRef, useCallback } from "react";
import { foremanChat, fetchChatHistory, fetchThreads, createThread } from "@/lib/api";
import type { ForemanResult, ChatThread } from "@/lib/api";

interface Message {
  role: "human" | "foreman";
  text: string;
  timestamp: number;
  actions?: ForemanResult["actions"];
}

interface ForemanChatProps {
  visible: boolean;
  onClose: () => void;
}

function formatTime(ts: number): string {
  return new Date(ts * 1000).toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
}

function formatDate(ts: number): string {
  const d = new Date(ts * 1000);
  const now = new Date();
  const diff = (now.getTime() - d.getTime()) / 86_400_000;
  if (diff < 1) return "Today";
  if (diff < 2) return "Yesterday";
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

export default function ForemanChat({ visible, onClose }: ForemanChatProps) {
  const [threads, setThreads] = useState<ChatThread[]>([]);
  const [activeThreadId, setActiveThreadId] = useState("default");
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [showThreads, setShowThreads] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  // Auto-scroll on new messages
  useEffect(() => {
    if (scrollRef.current) scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
  }, [messages]);

  // Focus input when panel opens
  useEffect(() => {
    if (visible && inputRef.current) inputRef.current.focus();
  }, [visible]);

  // Load threads and history
  const loadThreads = useCallback(async () => {
    const r = await fetchThreads();
    if (r.data) setThreads(r.data);
  }, []);

  const loadHistory = useCallback(async (threadId: string) => {
    const r = await fetchChatHistory(threadId, 50);
    if (r.data) {
      setMessages(r.data.map((m) => ({ role: m.role, text: m.text, timestamp: m.timestamp, actions: m.actions })));
    } else {
      setMessages([]);
    }
  }, []);

  useEffect(() => {
    if (visible) {
      loadThreads();
      loadHistory(activeThreadId);
    }
  }, [visible, activeThreadId, loadThreads, loadHistory]);

  async function handleSend() {
    const text = input.trim();
    if (!text || sending) return;
    setInput("");
    setSending(true);
    setMessages((prev) => [...prev, { role: "human", text, timestamp: Date.now() / 1000 }]);
    const result = await foremanChat(text, activeThreadId);
    setSending(false);
    if (result.data) {
      setMessages((prev) => [...prev, {
        role: "foreman",
        text: result.data!.assessment || "No assessment.",
        timestamp: result.data!.timestamp || Date.now() / 1000,
        actions: result.data!.actions,
      }]);
    } else {
      setMessages((prev) => [...prev, { role: "foreman", text: `Error: ${result.error}`, timestamp: Date.now() / 1000 }]);
    }
    loadThreads(); // Refresh thread list
  }

  async function handleNewThread() {
    const r = await createThread();
    if (r.data) {
      setActiveThreadId(r.data.id);
      setMessages([]);
      loadThreads();
      setShowThreads(false);
    }
  }

  function switchThread(threadId: string) {
    setActiveThreadId(threadId);
    setShowThreads(false);
  }

  if (!visible) return null;

  const activeThread = threads.find((t) => t.id === activeThreadId);

  return (
    <div className="fixed top-0 right-0 z-50 h-full w-[480px] max-w-full bg-bg-base border-l border-gray-800 flex flex-col shadow-2xl animate-slide-in">
      {/* Header */}
      <div className="shrink-0 border-b border-gray-800 px-4 py-3 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <button
            onClick={() => setShowThreads(!showThreads)}
            className="text-gray-500 hover:text-gray-300 transition-colors p-1"
            title="Toggle threads"
          >
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M4 6h16M4 12h16M4 18h16" />
            </svg>
          </button>
          <div className="h-2.5 w-2.5 rounded-full bg-accent-purple" />
          <span className="mono text-sm font-medium text-gray-200">
            {activeThread?.title || "Foreman"}
          </span>
        </div>
        <div className="flex items-center gap-1">
          <button
            onClick={handleNewThread}
            className="text-gray-500 hover:text-gray-300 transition-colors p-1"
            title="New thread"
          >
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M12 5v14M5 12h14" />
            </svg>
          </button>
          <button onClick={onClose} className="text-gray-500 hover:text-gray-300 transition-colors p-1" title="Close (Esc)">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M18 6L6 18M6 6l12 12" />
            </svg>
          </button>
        </div>
      </div>

      {/* Thread sidebar */}
      {showThreads && (
        <div className="shrink-0 border-b border-gray-800 max-h-64 overflow-y-auto">
          <div className="px-4 py-2">
            <div className="text-[10px] text-gray-500 uppercase tracking-wider font-semibold mb-2">Threads</div>
            <button
              onClick={() => switchThread("default")}
              className={`w-full text-left px-3 py-2 rounded text-xs mb-1 transition-colors ${
                activeThreadId === "default" ? "bg-accent-purple/15 text-accent-purple" : "text-gray-400 hover:bg-bg-surface-alt"
              }`}
            >
              <div className="font-medium">Default</div>
              <div className="text-[10px] text-gray-600">Main conversation</div>
            </button>
            {threads.filter((t) => t.id !== "default").map((thread) => (
              <button
                key={thread.id}
                onClick={() => switchThread(thread.id)}
                className={`w-full text-left px-3 py-2 rounded text-xs mb-1 transition-colors ${
                  activeThreadId === thread.id ? "bg-accent-purple/15 text-accent-purple" : "text-gray-400 hover:bg-bg-surface-alt"
                }`}
              >
                <div className="font-medium truncate">{thread.title}</div>
                <div className="text-[10px] text-gray-600">
                  {formatDate(thread.last_message_at)} · {thread.message_count} msg
                </div>
              </button>
            ))}
          </div>
        </div>
      )}

      {/* Messages */}
      <div ref={scrollRef} className="flex-1 overflow-y-auto px-5 py-4 space-y-3">
        {messages.length === 0 && (
          <div className="text-center text-gray-600 text-sm py-8">
            {activeThreadId === "default" ? "Chat with the factory foreman." : "New thread — start a conversation."}
          </div>
        )}
        {messages.map((msg, i) => (
          <div key={i} className={`flex flex-col ${msg.role === "human" ? "items-start" : "items-end"}`}>
            <div className={`max-w-[85%] rounded-lg px-3 py-2 text-sm leading-relaxed ${
              msg.role === "human"
                ? "bg-accent-blue/10 border border-accent-blue/20 text-gray-200"
                : "bg-accent-purple/10 border border-accent-purple/20 text-gray-300"
            }`}>
              <div className="flex items-center justify-between mb-1">
                <span className={`text-[10px] font-semibold mono uppercase ${msg.role === "human" ? "text-accent-blue" : "text-accent-purple"}`}>
                  {msg.role === "human" ? "you" : "foreman"}
                </span>
                <span className="text-[10px] text-gray-600 ml-3">{formatTime(msg.timestamp)}</span>
              </div>
              <p className="whitespace-pre-wrap">{msg.text}</p>
            </div>
            {msg.actions && msg.actions.length > 0 && (
              <div className="max-w-[85%] mt-1 space-y-1">
                <div className="text-[10px] text-gray-500 uppercase tracking-wider font-semibold px-1">Actions</div>
                {msg.actions.map((action, j) => (
                  <div key={j} className="flex items-center gap-2 px-2 py-1 rounded bg-bg-surface border border-gray-800 text-xs">
                    <span className="mono text-gray-400">{action.type}</span>
                    <span className={`mono font-semibold ${
                      action.status === "ok" ? "text-accent-green" : action.status === "blocked" ? "text-accent-yellow" : "text-accent-red"
                    }`}>{action.status}</span>
                    {(action.detail || action.reason) && <span className="text-gray-500 truncate">{action.detail || action.reason}</span>}
                  </div>
                ))}
              </div>
            )}
          </div>
        ))}
        {sending && (
          <div className="flex justify-end">
            <div className="bg-accent-purple/10 border border-accent-purple/20 rounded-lg px-3 py-2 flex items-center gap-2">
              <div className="h-3 w-3 border-2 border-accent-purple/30 border-t-accent-purple rounded-full animate-spin" />
              <span className="text-xs text-accent-purple">Thinking...</span>
            </div>
          </div>
        )}
      </div>

      {/* Input */}
      <div className="shrink-0 border-t border-gray-800 px-5 py-3">
        <textarea
          ref={inputRef}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="Ask the foreman..."
          rows={2}
          className="w-full bg-bg-surface border border-gray-700 rounded-lg px-3 py-2 text-sm text-gray-200 placeholder-gray-600 focus:outline-none focus:border-accent-purple resize-none"
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); handleSend(); }
            if (e.key === "Escape") onClose();
          }}
          disabled={sending}
        />
      </div>
    </div>
  );
}
