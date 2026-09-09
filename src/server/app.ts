import Fastify from 'fastify';
import fastifyStatic from '@fastify/static';
import { existsSync } from 'node:fs';
import { resolve, join } from 'node:path';
import { randomUUID } from 'node:crypto';
import { z } from 'zod';
import type { Config, ModelProvider, Profile } from '../shared/types.js';
import { Store } from './store.js';
import { OneBotClient } from './onebot.js';
import { ReplyEngine } from './engine.js';
import { CodexProvider, segmentSchema } from './codex.js';
import { HistoryImporter } from './history.js';
import { AnalysisService } from './analysis.js';
import { normalizeMessage } from './messages.js';

const editableConfig = z.object({
  wsUrl: z.string().refine(value => {
    try { const u = new URL(value); return ['ws:', 'wss:'].includes(u.protocol) && ['127.0.0.1', 'localhost', '[::1]'].includes(u.hostname) && !u.username && !u.password && !u.search; } catch { return false; }
  }, 'QQ 连接地址必须是本机 WebSocket 地址，令牌请单独填写。').optional(),
  token: z.string().optional(), accountId: z.string().regex(/^\d*$/).optional(), contactId: z.string().regex(/^\d*$/).optional(),
  contactName: z.string().max(100).optional(), mode: z.enum(['confirm', 'auto']).optional(),
  debounceMs: z.number().int().min(500).max(30000).optional(), codexPath: z.string().min(1).optional(),
  model: z.string().min(1).max(100).optional(), replyEffort: z.enum(['low', 'medium', 'high']).optional(),
  analysisEffort: z.enum(['low', 'medium', 'high']).optional(), generationTimeoutMs: z.number().int().min(10000).max(600000).optional(),
  profileId: z.string().optional(),
}).strict();

export async function createApp(options: { dataDir?: string; staticDir?: string; model?: ModelProvider; client?: OneBotClient; logger?: boolean; autoConnect?: boolean } = {}) {
  const dataDir = resolve(options.dataDir ?? 'data');
  const app = Fastify({ logger: options.logger ?? false, bodyLimit: 1024 * 1024 });
  const store = new Store(join(dataDir, 'assistant.sqlite'));
  const client = options.client ?? new OneBotClient();
  const model = options.model ?? new CodexProvider(() => store.config(), join(dataDir, 'codex-work'));
  let engine: ReplyEngine;
  const ready = () => {
    const c = store.config();
    return client.state.connected && client.state.online && !!c.accountId && c.accountId === client.state.accountId && !!c.contactId && !engine.state.syncing;
  };
  engine = new ReplyEngine(store, client, model, ready);
  const history = new HistoryImporter(store, client, ready);
  const analysis = new AnalysisService(store, model);
  let connectionEpoch = 0;
  let buffer: Record<string, any>[] = [];
  let lastConnectionError = '';
  const probes = new Set<AbortController>();
  const jobs = new Set<Promise<unknown>>();
  const track = (promise: Promise<unknown>) => { jobs.add(promise); promise.finally(() => jobs.delete(promise)).catch(() => {}); return promise; };
  async function synchronize() {
    const epoch = ++connectionEpoch, startedAt = Date.now();
    engine.disconnected(); engine.state.syncing = true; buffer = [];
    try {
      await client.identify();
      const c = store.config();
      if (!c.accountId || !c.contactId || client.state.accountId !== c.accountId) return;
      const last = store.messages(c.accountId, c.contactId, { limit: 1 }).at(-1);
      let cursor: string | undefined;
      for (;;) {
        if (epoch !== connectionEpoch || !client.state.connected) return;
        let response: { messages: Record<string, any>[] };
        try {
          response = await client.call('get_friend_msg_history', { user_id: c.contactId, count: 100, ...(cursor ? { message_seq: cursor } : {}), reverse_order: true, disable_get_url: true, parse_mult_msg: false }, 60000);
        } catch (e) {
          const reason = e instanceof Error ? e.message : String(e);
          // This endpoint reports an empty conversation as "消息...不存在".
          if (!cursor && !last && /消息.*不存在/.test(reason)) break;
          throw e;
        }
        if (epoch !== connectionEpoch) return;
        if (!Array.isArray(response.messages)) throw new Error('初始历史响应格式不正确。');
        if (!response.messages.length) break;
        response.messages.sort((a, b) => Number(a.time) - Number(b.time) || Number(a.real_seq ?? 0) - Number(b.real_seq ?? 0));
        let oldest = Number.MAX_SAFE_INTEGER;
        for (const raw of response.messages) {
          const m = normalizeMessage({ ...raw, target_id: raw.target_id ?? c.contactId }, c, 'history');
          if (m) { store.addMessage(m); oldest = Math.min(oldest, m.time); }
        }
        if (!last || oldest <= last.time) break;
        const next = String(response.messages[0]?.message_id ?? '');
        if (!next || next === cursor) { lastConnectionError = '断线期间的历史只读取到接口边界，更早记录待核。'; break; }
        cursor = next;
      }
      if (epoch !== connectionEpoch) return;
      engine.beginLive(startedAt);
      const buffered = buffer; buffer = [];
      for (const frame of buffered) engine.ingest(frame);
    } catch (error) {
      if (epoch !== connectionEpoch) return;
      lastConnectionError = error instanceof Error ? error.message : String(error);
      engine.state.lastError = '初始化会话失败：' + lastConnectionError;
      const c = store.config(); store.saveConfig({ ...c, paused: true, pauseReason: '初始上下文未能同步，请检查连接后重新连接。' });
    } finally {
      if (epoch === connectionEpoch) { engine.state.syncing = false; engine.emit('change'); }
    }
  }
  client.on('connected', () => { lastConnectionError = ''; void track(synchronize()); });
  client.on('disconnected', () => { connectionEpoch++; buffer = []; engine.disconnected(); engine.state.syncing = false; });
  client.on('event', frame => {
    if (!['message', 'message_sent'].includes(frame.post_type)) return;
    if (engine.state.syncing) {
      if (normalizeMessage(frame, store.config(), 'live')) buffer.push(frame);
    } else engine.ingest(frame);
  });
  app.addHook('onRequest', async (request, reply) => {
    if (!['GET', 'HEAD', 'OPTIONS'].includes(request.method) && request.headers.origin) {
      const origin = new URL(request.headers.origin);
      if (!['127.0.0.1', 'localhost', '[::1]'].includes(origin.hostname)) return reply.code(403).send({ error: '仅接受本机管理页面的操作。' });
    }
  });
  app.setErrorHandler((error, _request, reply) => {
    if (error instanceof z.ZodError) return reply.code(400).send({ error: error.issues.map(i => i.path.join('.') + '：' + i.message).join('；') });
    return reply.code((error as { statusCode?: number }).statusCode ?? 400).send({ error: error instanceof Error ? error.message : String(error) });
  });
  app.get('/api/state', async () => {
    const c = store.config(), { token, ...publicConfig } = c;
    const profile = store.profile(c.profileId);
    return {
      config: { ...publicConfig, tokenPresent: !!token }, connection: { ...client.state, ready: ready(), error: lastConnectionError },
      worker: engine.state, messages: store.messages(c.accountId, c.contactId, { limit: 120 }), drafts: store.drafts(c.accountId, c.contactId, 60),
      messageCount: store.messageCount(c.accountId, c.contactId),
      profiles: store.profiles().map(p => ({ id: p.id, name: p.name, exampleCount: p.examples.length, learnedUntil: p.learnedUntil })),
      profile: profile ? { ...profile, examples: profile.examples.slice(-12) } : null,
      memories: store.memories(c.accountId, c.contactId),
      historyJobs: store.historyJobs().filter(j => j.accountId === c.accountId && j.contactId === c.contactId).slice(0, 20),
      analysis: analysis.state, analysisJobs: analysis.jobs().filter(j => j.accountId === c.accountId && j.contactId === c.contactId).map(({ sourceIds, ...j }) => ({ ...j, total: sourceIds.length })).slice(0, 20),
      replays: store.replays().filter(r => r.accountId === c.accountId && r.contactId === c.contactId),
    };
  });
  app.get('/api/health', async () => ({ app: 'qq-reply-assistant', version: '0.1.0', pid: process.pid, dataDir }));
  app.get<{ Params: { id: string } }>('/api/messages/:id', async (request, reply) => {
    const c = store.config(), m = store.message(request.params.id);
    if (!m || m.accountId !== c.accountId || m.contactId !== c.contactId) return reply.code(404).send({ error: '当前会话中没有这条来源消息。' });
    return m;
  });
  app.patch('/api/config', async request => {
    const patch = editableConfig.parse(request.body), old = store.config();
    if (patch.profileId && !store.profile(patch.profileId)) throw new Error('所选画像不存在。');
    const config = { ...old, ...patch };
    if (old.accountId !== config.accountId || old.contactId !== config.contactId) config.profileId = patch.profileId ?? '';
    if (config.accountId && config.accountId === config.contactId) throw new Error('目标联系人不能是当前登录账号本人。');
    store.saveConfig(config); engine.configured(old, config);
    if (old.wsUrl !== config.wsUrl || old.token !== config.token) { client.stop(); }
    else if (client.state.connected && (old.contactId !== config.contactId || old.accountId !== config.accountId)) void track(synchronize());
    return { ok: true };
  });
  app.post('/api/connect', async () => {
    const c = store.config();
    if (!c.token) throw new Error('请先填写与 NapCat 相同的连接令牌。');
    client.start(c.wsUrl, c.token); return { ok: true };
  });
  app.post('/api/disconnect', async () => { client.stop(); return { ok: true }; });
  app.get('/api/friends', async () => {
    if (!client.state.connected) throw new Error('请先连接 QQ。');
    return client.call('get_friend_list');
  });
  app.post('/api/control/pause', async () => { engine.pause('已由你暂停。'); return { ok: true }; });
  app.post('/api/control/resume', async () => {
    if (!ready()) throw new Error('请先连接并确认账号、联系人及 QQ 在线状态。');
    const c = store.config(); store.saveConfig({ ...c, paused: false, pauseReason: '' });
    engine.beginLive(); return { ok: true };
  });
  app.post('/api/control/stop', async () => { setTimeout(() => void app.close(), 150); return { ok: true }; });
  app.post('/api/drafts/generate', async () => {
    const promise = engine.regenerate(); void track(promise);
    await Promise.resolve();
    if (!engine.state.generating) await promise;
    return { ok: true };
  });
  app.post('/api/drafts/compose', async () => engine.compose());
  app.patch<{ Params: { id: string } }>('/api/drafts/:id', async request => {
    const { segments } = z.object({ segments: z.array(segmentSchema).min(1).max(30) }).parse(request.body);
    return engine.edit(request.params.id, segments);
  });
  app.post<{ Params: { id: string } }>('/api/drafts/:id/send', async request => engine.send(request.params.id));
  app.post<{ Params: { id: string } }>('/api/drafts/:id/discard', async request => { engine.discard(request.params.id); return { ok: true }; });
  app.post<{ Params: { id: string } }>('/api/drafts/:id/resolve', async request => {
    const { outcome } = z.object({ outcome: z.enum(['sent', 'not_sent']) }).parse(request.body); engine.resolveUnknown(request.params.id, outcome); return { ok: true };
  });
  app.post('/api/history', async request => {
    const body = z.object({ startTime: z.number().nonnegative().nullable(), endTime: z.number().nonnegative() }).parse(request.body);
    return history.start(body.startTime, body.endTime);
  });
  app.post<{ Params: { id: string } }>('/api/history/:id/resume', async request => history.resume(request.params.id));
  app.post<{ Params: { id: string } }>('/api/history/:id/pause', async request => { history.pause(request.params.id); return { ok: true }; });
  app.post('/api/analysis', async () => analysis.start());
  app.post('/api/analysis/pause', async () => { analysis.stop(); return { ok: true }; });
  app.post<{ Params: { id: string } }>('/api/analysis/:id/resume', async request => { analysis.resume(request.params.id); return { ok: true }; });
  app.post('/api/profiles', async request => {
    const body = z.object({ name: z.string().min(1).max(100) }).parse(request.body); return store.makeProfile(body.name);
  });
  app.get<{ Params: { id: string } }>('/api/profiles/:id', async request => {
    const p = store.profile(request.params.id); if (!p) throw new Error('画像不存在。'); return p;
  });
  app.patch<{ Params: { id: string } }>('/api/profiles/:id', async request => {
    if (analysis.jobs().some(j => j.profileId === request.params.id && j.status === 'running')) throw new Error('这份画像仍在整理中，请先暂停分析或等待完成，再编辑保存。');
    const patch = z.object({ name: z.string().min(1).max(100).optional(), style: z.string().max(30000).optional(), examples: z.array(z.object({ id: z.string(), context: z.string().max(6000), reply: z.string().min(1).max(6000), sourceIds: z.array(z.string()), sourceTime: z.number(), edited: z.boolean().optional() })).optional() }).parse(request.body);
    const profile = store.profile(request.params.id); if (!profile) throw new Error('画像不存在。');
    const edited: Profile = { ...profile, ...patch, updatedAt: Date.now(), learnedUntil: Date.now() };
    store.putProfile(edited);
    if (store.config().profileId === edited.id) engine.cancel('画像已更新，请重新生成。');
    return edited;
  });
  app.post('/api/memories', async request => {
    const body = z.object({ text: z.string().min(1).max(3000), status: z.enum(['confirmed', 'unverified']) }).parse(request.body), c = store.config();
    if (!c.accountId || !c.contactId) throw new Error('请先选择账号和联系人。');
    const m = { ...body, id: randomUUID(), accountId: c.accountId, contactId: c.contactId, sourceIds: [], sourceTime: Date.now(), updatedAt: Date.now() };
    store.putMemory(m); return m;
  });
  app.patch<{ Params: { id: string } }>('/api/memories/:id', async request => {
    const c = store.config(), m = store.memories(c.accountId, c.contactId).find(m => m.id === request.params.id);
    if (!m) throw new Error('记忆不存在。');
    const patch = z.object({ text: z.string().min(1).max(3000).optional(), status: z.enum(['confirmed', 'unverified']).optional() }).parse(request.body);
    store.putMemory({ ...m, ...patch, sourceTime: Date.now(), updatedAt: Date.now() }); return { ok: true };
  });
  app.delete<{ Params: { id: string } }>('/api/memories/:id', async request => {
    const c = store.config(); store.deleteMemory(request.params.id, c.accountId, c.contactId); return { ok: true };
  });
  app.post('/api/replays', async () => {
    if (analysis.state.running) throw new Error('请等待当前分析任务完成。');
    // replay validates synchronously before its first model await; errors are returned to this request.
    const promise = analysis.replay(); void track(promise);
    await Promise.resolve();
    if (!analysis.state.running) return await promise;
    return { ok: true };
  });
  app.patch<{ Params: { id: string; index: string } }>('/api/replays/:id/:index', async request => {
    const c = store.config(), r = store.replays().find(r => r.id === request.params.id && r.accountId === c.accountId && r.contactId === c.contactId) as Record<string, any> | undefined;
    const body = z.object({ review: z.string().max(2000) }).parse(request.body), index = Number(request.params.index);
    if (!r || !Number.isInteger(index) || !r.results[index]) throw new Error('回放样本不存在。');
    r.results[index].review = body.review; store.saveReplay(r as Record<string, unknown> & { id: string }); return { ok: true };
  });
  app.post('/api/codex/probe', async () => {
    if (!(model instanceof CodexProvider)) return { ok: true, simulated: true };
    const abort = new AbortController(); probes.add(abort);
    try { return await track(model.probe(abort.signal)); } finally { probes.delete(abort); }
  });
  const staticDir = resolve(options.staticDir ?? 'dist/client');
  if (existsSync(join(staticDir, 'index.html'))) {
    await app.register(fastifyStatic, { root: staticDir });
    app.setNotFoundHandler((request, reply) => request.url.startsWith('/api/') ? reply.code(404).send({ error: '接口不存在。' }) : reply.sendFile('index.html'));
  } else app.get('/', async (_request, reply) => reply.type('text/html').send('<p>开发界面：<a href="http://127.0.0.1:5173">打开心声</a>。生产界面请先运行 pnpm build。</p>'));
  app.addHook('onClose', async () => {
    history.stop(); analysis.stop(); engine.stop(); for (const p of probes) p.abort(); client.stop();
    await Promise.allSettled([...jobs]);
    await new Promise<void>(resolveWait => {
      const finish = () => {
        if (!engine.state.generating && !engine.state.sending && !analysis.state.running && !history.isRunning) {
          engine.off('change', finish); history.off('change', finish); analysis.off('change', finish); resolveWait();
        }
      };
      engine.on('change', finish); history.on('change', finish); analysis.on('change', finish); finish();
    });
    store.close();
  });
  if (options.autoConnect !== false && store.config().token) {
    app.addHook('onReady', async () => { const c = store.config(); client.start(c.wsUrl, c.token); });
  }
  return { app, store, client, engine, history, analysis };
}
