import { useEffect, useRef } from "react";
import type { SessionSummary } from "@/types";

/**
 * Track session state changes and fire browser notifications
 * when a worker completes, deploys, or errors.
 */
export function useNotifications(sessions: SessionSummary[]) {
  const prevIds = useRef<Set<string>>(new Set());
  const initialized = useRef(false);

  useEffect(() => {
    // Request permission on first load
    if ("Notification" in window && Notification.permission === "default") {
      Notification.requestPermission();
    }
  }, []);

  useEffect(() => {
    if (!initialized.current) {
      // First load — just record current state, don't notify
      prevIds.current = new Set(sessions.map((s) => s.id));
      initialized.current = true;
      return;
    }

    const currentIds = new Set(sessions.map((s) => s.id));

    // Sessions that disappeared = they finished
    for (const prevId of prevIds.current) {
      if (!currentIds.has(prevId)) {
        notify(`Worker finished: ${prevId}`, "Session is no longer active");
      }
    }

    // Sessions that appeared = new dispatch
    for (const id of currentIds) {
      if (!prevIds.current.has(id)) {
        const session = sessions.find((s) => s.id === id);
        if (session) {
          notify(
            `New worker: ${session.project}`,
            session.task || session.id
          );
        }
      }
    }

    prevIds.current = currentIds;
  }, [sessions]);
}

function notify(title: string, body: string) {
  if ("Notification" in window && Notification.permission === "granted") {
    new Notification(title, { body, icon: "/favicon.ico" });
  }
}
