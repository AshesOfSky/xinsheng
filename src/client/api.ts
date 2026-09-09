import type { ChatMessage, Config, Draft, HistoryJob, Memory, Profile } from '../shared/types';
export type AppState = {
  config: Omit<Config, 'token'> & { tokenPresent: boolean };
  connection: { connected: boolean; online: boolean; accountId: string; ready: boolean; error: string; lastError: string; lastHeartbeat: number };
  worker: { workerRunning: boolean; generating: boolean; sending: boolean; pendingCount: number; lastError: string; lastLatencyMs: number; lastUsage: Record<string, number>; syncing: boolean };
  messages: ChatMessage[]; drafts: Draft[]; messageCount: number;
  profiles: { id: string; name: string; exampleCount: number; learnedUntil: number }[]; profile: Profile | null;
  memories: Memory[]; historyJobs: HistoryJob[];
  analysis: { running: boolean; task: string; processed: number; total: number; error: string };
  analysisJobs: { id: string; profileId: string; total: number; processed: number; status: string; error: string }[];
  replays: any[];
};
export async function api<T = any>(path: string, method = 'GET', body?: unknown): Promise<T> {
  const response = await fetch('/api' + path, { method, headers: body === undefined ? {} : { 'Content-Type': 'application/json' }, body: body === undefined ? undefined : JSON.stringify(body) });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || '操作失败，请查看连接状态。');
  return data;
}
