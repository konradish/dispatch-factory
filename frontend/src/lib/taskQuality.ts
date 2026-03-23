/**
 * Client-side task quality gate — mirrors backend validation.
 * Rejects tasks < 20 chars or matching known vague patterns.
 */

export const TASK_MIN_LENGTH = 20;

const VAGUE_PATTERNS = /^(test|testing|try this|check|fix|fix it|update|updates|do it|what are next steps\??|next steps\??|todo|tbd|placeholder|asdf|hello|make it work|do something|finish this|needs work|investigate|look into)$/i;

export function validateTaskQuality(task: string): string | null {
  const trimmed = task.trim();
  if (!trimmed) return null; // empty handled elsewhere
  if (trimmed.length < TASK_MIN_LENGTH) {
    return `Task too short (${trimmed.length}/${TASK_MIN_LENGTH} chars). Describe a concrete deliverable.`;
  }
  if (VAGUE_PATTERNS.test(trimmed)) {
    return "Task is too vague. Describe a concrete deliverable (e.g. 'Add retry logic to payment webhook handler').";
  }
  return null;
}
