export type Mode = 'confirm' | 'auto';
export type Segment = { type: 'text'; data: { text: string } } | { type: 'face'; data: { id: string } };
export type Reply = { action: 'reply' | 'needs_human' | 'skip'; segments: Segment[]; reason: string };
export type Origin = 'history' | 'live';
export type Author = 'contact' | 'human' | 'assistant' | 'unknown';
export interface ChatMessage {
  id: string; accountId: string; contactId: string; messageId: string; sequence: string;
  senderId: string; time: number; segments: Segment[]; text: string;
  unsupported: boolean; author: Author; origin: Origin;
}
export interface Config {
  wsUrl: string; token: string; accountId: string; contactId: string; contactName: string;
  mode: Mode; paused: boolean; pauseReason: string; debounceMs: number;
  codexPath: string; model: string; replyEffort: 'low' | 'medium' | 'high';
  analysisEffort: 'low' | 'medium' | 'high'; generationTimeoutMs: number; profileId: string;
}
export const defaultConfig: Config = {
  wsUrl: 'ws://127.0.0.1:3001', token: '', accountId: '', contactId: '', contactName: '',
  mode: 'confirm', paused: false, pauseReason: '', debounceMs: 3000,
  codexPath: 'codex', model: 'gpt-6-astra', replyEffort: 'low', analysisEffort: 'medium',
  generationTimeoutMs: 120000, profileId: '',
};
export interface Draft {
  id: string; accountId: string; contactId: string; revision: number; triggerIds: string[];
  mode: Mode; status: 'pending' | 'needs_human' | 'sending' | 'sent' | 'unknown' | 'failed' | 'stale' | 'discarded' | 'skipped';
  reply: Reply; createdAt: number; updatedAt: number; sentMessageId: string | null;
  error: string; latencyMs: number; usage: Record<string, number>; edited: boolean;
}
export interface Example {
  id: string; context: string; reply: string; sourceIds: string[]; sourceTime: number;
  edited?: boolean;
}
export interface Profile {
  id: string; name: string; style: string; examples: Example[];
  learnedUntil: number; updatedAt: number;
}
export interface Memory {
  id: string; accountId: string; contactId: string; text: string; status: 'confirmed' | 'unverified';
  sourceIds: string[]; sourceTime: number; updatedAt: number;
}
export interface Summary {
  accountId: string; contactId: string; text: string; throughTime: number;
}
export interface HistoryJob {
  id: string; accountId: string; contactId: string; startTime: number | null; endTime: number;
  cursor: string | null; status: 'running' | 'paused' | 'completed' | 'partial' | 'failed';
  fetched: number; imported: number; earliest: number | null; latest: number | null;
  pages: number; error: string; createdAt: number; updatedAt: number;
}
export interface GenerationContext {
  messages: ChatMessage[]; style: string; examples: Example[]; memories: Memory[];
  summary: string; now: number;
}
export interface GenerationResult { reply: Reply; usage: Record<string, number>; latencyMs: number }
export interface AnalysisResult {
  style: string; summary: string; examples: { context: string; reply: string; sourceIds: string[] }[];
  memories: { text: string; sourceIds: string[]; status: 'confirmed' | 'unverified' }[];
}
export interface ModelProvider {
  generate(context: GenerationContext, signal: AbortSignal): Promise<GenerationResult>;
  analyze(messages: ChatMessage[], signal: AbortSignal, previous?: { style: string; summary: string }): Promise<AnalysisResult>;
}
export interface Transport {
  call<T = unknown>(action: string, params?: Record<string, unknown>, timeoutMs?: number): Promise<T>;
}
export function segmentText(segments: Segment[]): string {
  return segments.map(s => s.type === 'text' ? s.data.text : '[QQ表情:' + s.data.id + ']').join('');
}
