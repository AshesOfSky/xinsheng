import { defaultConfig, type AnalysisResult, type GenerationContext, type GenerationResult, type ModelProvider, type ChatMessage } from '../src/shared/types.js';
export function incoming(id: string | number, text = '今天有点累', overrides: Record<string, any> = {}) {
  return { post_type: 'message', message_type: 'private', self_id: 10001, user_id: 20002, target_id: 10001, sender: { user_id: 20002 }, message_id: id, time: Math.floor(Date.now() / 1000), message: [{ type: 'text', data: { text } }], ...overrides };
}
export function selfMessage(id: string | number, text = '嗯嗯') {
  return incoming(id, text, { post_type: 'message_sent', user_id: 10001, target_id: 20002, sender: { user_id: 10001 } });
}
export const config = { ...defaultConfig, accountId: '10001', contactId: '20002', token: 'test-token', debounceMs: 15 };
export const replyResult: GenerationResult = { reply: { action: 'reply', segments: [{ type: 'text', data: { text: '先歇一会儿吧' } }], reason: '结合当前对话' }, latencyMs: 1, usage: { input_tokens: 20, output_tokens: 5 } };
export const analysisResult: AnalysisResult = { style: '短句，语气平实', summary: '聊了日常生活', examples: [], memories: [] };
export class FakeModel implements ModelProvider {
  contexts: GenerationContext[] = [];
  analyses: ChatMessage[][] = [];
  generateFn?: (context: GenerationContext, signal: AbortSignal) => Promise<GenerationResult>;
  async generate(context: GenerationContext, signal: AbortSignal) { this.contexts.push(context); return this.generateFn ? this.generateFn(context, signal) : structuredClone(replyResult); }
  async analyze(messages: ChatMessage[]) { this.analyses.push(messages); return structuredClone(analysisResult); }
}
export function deferred<T>() {
  let resolve!: (value: T) => void, reject!: (error: Error) => void;
  const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}
export async function until(condition: () => boolean, timeout = 2500) {
  const start = Date.now();
  while (!condition()) {
    if (Date.now() - start > timeout) throw new Error('等待测试中的异步行为超时');
    await new Promise(resolve => setTimeout(resolve, 10));
  }
}
