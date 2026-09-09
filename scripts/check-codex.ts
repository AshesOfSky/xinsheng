import { resolve } from 'node:path';
import { defaultConfig } from '../src/shared/types.js';
import { CodexProvider } from '../src/server/codex.js';
import { normalizeMessage } from '../src/server/messages.js';
const config = { ...defaultConfig, generationTimeoutMs: 120000 };
const provider = new CodexProvider(() => config, resolve('data/codex-work'));
try {
  if (process.argv.includes('--analysis')) {
    const sampleConfig = { ...config, accountId: '10001', contactId: '20002' };
    const base = { self_id: 10001, target_id: 20002, message_type: 'private' };
    const samples = [
      { ...base, user_id: 20002, sender: { user_id: 20002 }, message_id: 1, time: 1788840000, message: [{ type: 'text', data: { text: '今天有点累' } }] },
      { ...base, user_id: 10001, sender: { user_id: 10001 }, message_id: 2, time: 1788840010, message: [{ type: 'text', data: { text: '先歇会儿吧，别勉强' } }] },
      { ...base, user_id: 20002, sender: { user_id: 20002 }, message_id: 3, time: 1788840020, message: [{ type: 'text', data: { text: '我平时不喝咖啡' } }] },
      { ...base, user_id: 10001, sender: { user_id: 10001 }, message_id: 4, time: 1788840030, message: [{ type: 'text', data: { text: '嗯嗯，记住啦' } }] },
    ].map(r => normalizeMessage(r, sampleConfig, 'history')!);
    const result = await provider.analyze(samples, new AbortController().signal);
    console.log(JSON.stringify({ model: config.model, source: 'synthetic test messages only', result }, null, 2));
  } else {
    const result = await provider.probe(new AbortController().signal);
    console.log(JSON.stringify({ model: config.model, reply: result.reply, latencyMs: result.latencyMs, usage: result.usage }, null, 2));
  }
} catch (error) { console.error(error instanceof Error ? error.message : error); process.exitCode = 1; }
