import { EventEmitter } from 'node:events';
import { randomUUID } from 'node:crypto';
import type { ChatMessage, Config, Draft, GenerationResult, ModelProvider, Reply, Segment, Transport } from '../shared/types.js';
import { segmentText } from '../shared/types.js';
import { Store } from './store.js';
import { normalizeMessage } from './messages.js';
import { ActionError } from './onebot.js';

export class ReplyEngine extends EventEmitter {
  readonly state = { workerRunning: true, generating: false, sending: false, revision: 1, pendingCount: 0, lastError: '', lastLatencyMs: 0, lastUsage: {} as Record<string, number>, syncing: false };
  private pending = new Map<string, ChatMessage>();
  private timer?: NodeJS.Timeout;
  private abort?: AbortController;
  private forceConfirm = false;
  private liveSince = Number.MAX_SAFE_INTEGER;
  private selfDuringSend: ChatMessage[] = [];
  private activeDraft?: Draft;
  private ingested = new Set<string>();
  constructor(public store: Store, private transport: Transport, private model: ModelProvider, private ready: () => boolean) {
    super(); this.store.recover();
  }
  config() { return this.store.config(); }
  beginLive(since = Date.now()) { this.liveSince = Math.floor(since / 1000) * 1000; this.state.syncing = false; this.emit('change'); }
  disconnected() { this.liveSince = Number.MAX_SAFE_INTEGER; this.cancel('连接发生变化，旧草稿已失效。'); }
  configured(previous: Config, next: Config) {
    const critical = previous.accountId !== next.accountId || previous.contactId !== next.contactId || previous.mode !== next.mode ||
      previous.paused !== next.paused || previous.profileId !== next.profileId || previous.model !== next.model ||
      previous.wsUrl !== next.wsUrl || previous.token !== next.token || previous.codexPath !== next.codexPath ||
      previous.replyEffort !== next.replyEffort || previous.analysisEffort !== next.analysisEffort;
    if (critical) {
      this.cancel('配置或模式已改变，请基于最新消息生成。', previous);
    }
    this.emit('change');
  }
  cancel(reason: string, config = this.config()) {
    this.state.revision++; this.abort?.abort(); clearTimeout(this.timer); this.timer = undefined;
    this.pending.clear(); this.state.pendingCount = 0; this.forceConfirm = false;
    this.store.stalePending(config.accountId, config.contactId, reason); this.emit('change');
  }
  pause(reason: string) {
    const c = this.config(); this.cancel(reason);
    this.store.saveConfig({ ...c, paused: true, pauseReason: reason }); this.emit('change');
  }
  private manualReply(reason: string) {
    if (this.config().mode === 'auto') this.pause(reason + ' 自动模式已暂停，继续时请恢复处理。');
    else this.cancel(reason + ' 旧草稿已失效，下一条对方消息仍会生成待确认草稿。');
  }
  ingest(raw: Record<string, any>, origin: 'live' | 'history' = 'live') {
    const config = this.config();
    if (!config.accountId || !config.contactId) return;
    const message = normalizeMessage(raw, config, origin);
    if (!message) return;
    const isFresh = origin === 'live' && message.time >= this.liveSince;
    if (!isFresh) { message.origin = 'history'; this.store.addMessage(message); this.emit('change'); return; }
    if (this.ingested.has(message.id)) return;
    const existing = this.store.message(message.id);
    if (existing?.origin === 'live') {
      if (existing.author === 'assistant' && message.senderId === message.accountId) {
        this.store.updateMessage({ ...message, author: 'assistant' }); this.emit('change');
      }
      return;
    }
    this.ingested.add(message.id);
    if (existing) this.store.db.prepare('DELETE FROM messages WHERE id=?').run(message.id);
    if (message.author === 'human') {
      const known = this.store.drafts(config.accountId, config.contactId, 10000).find(d => d.sentMessageId === message.messageId);
      if (known) message.author = 'assistant';
      else if (this.state.sending) message.author = 'unknown';
      this.store.addMessage(message);
      if (known) { this.emit('change'); return; }
      if (this.state.sending) { this.selfDuringSend.push(message); this.emit('change'); return; }
      this.manualReply('检测到你手动回复。'); return;
    }
    this.store.addMessage(message);
    if (config.paused || !this.ready()) { this.emit('change'); return; }
    this.state.revision++; this.abort?.abort();
    this.store.stalePending(config.accountId, config.contactId, '收到新消息，旧草稿已失效。');
    this.pending.set(message.id, message); this.state.pendingCount = this.pending.size;
    this.schedule(); this.emit('change');
  }
  private schedule() {
    clearTimeout(this.timer);
    this.timer = setTimeout(() => { this.timer = undefined; void this.process(); }, this.config().debounceMs);
  }
  async regenerate(): Promise<void> {
    if (this.config().paused) throw new Error('请先恢复处理，再重新生成草稿。');
    if (!this.ready()) throw new Error('QQ 尚未就绪，不能基于过期会话生成可发送草稿。');
    const config = this.config();
    const messages = this.store.messages(config.accountId, config.contactId, { limit: 40 });
    const last = messages.at(-1);
    if (!last || last.author !== 'contact') throw new Error('最新一条不是对方消息，请等待新的消息。');
    this.cancel('已请求重新生成。'); this.pending.set(last.id, last); this.state.pendingCount = 1;
    this.forceConfirm = true; await this.process();
  }
  private async process() {
    if (this.state.generating || this.state.sending || !this.pending.size || this.config().paused || !this.ready()) return;
    this.state.generating = true;
    const c = this.config(), revision = this.state.revision, batch = [...this.pending.values()];
    const confirmOnly = this.forceConfirm; this.forceConfirm = false;
    const controller = new AbortController(); this.abort = controller;
    this.emit('change');
    try {
      let result: GenerationResult;
      if (batch.some(m => m.unsupported)) {
        result = { reply: { action: 'needs_human', segments: [], reason: '这轮包含图片、语音、引用或其他暂未支持的内容，请你处理。' }, latencyMs: 0, usage: {} };
      } else {
        result = await this.model.generate(this.store.context(c), controller.signal);
      }
      if (controller.signal.aborted || revision !== this.state.revision) return;
      for (const m of batch) this.pending.delete(m.id);
      this.state.pendingCount = this.pending.size;
      this.state.lastLatencyMs = result.latencyMs; this.state.lastUsage = result.usage;
      this.state.lastError = '';
      const draft: Draft = {
        id: randomUUID(), accountId: c.accountId, contactId: c.contactId, revision, triggerIds: batch.map(m => m.id),
        mode: confirmOnly ? 'confirm' : c.mode,
        status: result.reply.action === 'reply' ? 'pending' : result.reply.action === 'skip' ? 'skipped' : 'needs_human',
        reply: result.reply, createdAt: Date.now(), updatedAt: Date.now(), sentMessageId: null, error: '',
        latencyMs: result.latencyMs, usage: result.usage, edited: false,
      };
      this.store.putDraft(draft);
      if (draft.status === 'needs_human' && c.mode === 'auto') this.store.saveConfig({ ...this.config(), paused: true, pauseReason: draft.reply.reason });
      if (draft.status === 'pending' && c.mode === 'auto' && !confirmOnly && !this.config().paused) await this.send(draft.id, false);
    } catch (error) {
      if (controller.signal.aborted || revision !== this.state.revision) return;
      this.state.lastError = error instanceof Error ? error.message : String(error);
      for (const m of batch) this.pending.delete(m.id);
      this.state.pendingCount = this.pending.size;
      const reply: Reply = { action: 'needs_human', segments: [], reason: this.state.lastError };
      this.store.putDraft({
        id: randomUUID(), accountId: c.accountId, contactId: c.contactId, revision, triggerIds: batch.map(m => m.id),
        mode: c.mode, status: 'needs_human', reply, createdAt: Date.now(), updatedAt: Date.now(),
        sentMessageId: null, error: this.state.lastError, latencyMs: 0, usage: {}, edited: false,
      });
      if (c.mode === 'auto') this.store.saveConfig({ ...this.config(), paused: true, pauseReason: '生成失败，请检查运行状态。' });
    } finally {
      this.state.generating = false; this.abort = undefined; this.emit('change');
      if (this.pending.size && !this.config().paused) this.schedule();
    }
  }
  edit(id: string, segments: Segment[]) {
    const draft = this.requireDraft(id);
    if (!['pending', 'needs_human'].includes(draft.status)) throw new Error('这条草稿已不能编辑，请重新生成。');
    if (!segmentText(segments).trim()) throw new Error('回复不能为空。');
    draft.reply = { action: 'reply', segments, reason: '已人工编辑' }; draft.edited = true; draft.status = 'pending'; draft.updatedAt = Date.now();
    this.store.putDraft(draft); this.emit('change'); return draft;
  }
  compose() {
    if (!this.ready()) throw new Error('请先连接 QQ 并确认账号与联系人。');
    if (this.state.sending) throw new Error('正在发送上一条消息，请稍后。');
    const c = this.config();
    if (this.store.drafts(c.accountId, c.contactId).some(d => d.status === 'unknown')) throw new Error('请先核对上一条消息的发送结果。');
    this.manualReply('已开始手工回复。');
    const draft: Draft = {
      id: randomUUID(), accountId: c.accountId, contactId: c.contactId, revision: this.state.revision, triggerIds: [],
      mode: 'confirm', status: 'needs_human', reply: { action: 'needs_human', segments: [], reason: '手工回复：填写内容后确认发送。' },
      createdAt: Date.now(), updatedAt: Date.now(), sentMessageId: null, error: '', latencyMs: 0, usage: {}, edited: true,
    };
    this.store.putDraft(draft); this.emit('change'); return draft;
  }
  discard(id: string) {
    const draft = this.requireDraft(id);
    if (!['pending', 'needs_human', 'failed', 'stale'].includes(draft.status)) throw new Error('当前状态不可放弃。');
    draft.status = 'discarded'; this.store.putDraft(draft); this.emit('change');
  }
  resolveUnknown(id: string, outcome: 'sent' | 'not_sent') {
    const draft = this.requireDraft(id);
    if (draft.status !== 'unknown') throw new Error('只有结果待核的消息需要核对。');
    draft.status = outcome === 'sent' ? 'sent' : 'discarded'; draft.error = '由你核对为：' + (outcome === 'sent' ? '已发送' : '未发送，不自动重试');
    draft.updatedAt = Date.now(); this.store.putDraft(draft); this.emit('change');
  }
  private requireDraft(id: string) {
    const draft = this.store.draft(id), c = this.config();
    if (!draft || draft.accountId !== c.accountId || draft.contactId !== c.contactId) throw new Error('找不到当前联系人的这条草稿。');
    return draft;
  }
  async send(id: string, manual = true): Promise<Draft> {
    const draft = this.requireDraft(id), c = this.config();
    if (draft.status !== 'pending') throw new Error('草稿已发送、失效或等待核对，不能重复发送。');
    if (draft.revision !== this.state.revision) throw new Error('上下文已改变，请重新生成。');
    if (!this.ready()) throw new Error('QQ 尚未就绪。');
    if (!manual && (c.mode !== 'auto' || c.paused || draft.mode !== 'auto')) throw new Error('自动发送已停止。');
    if (this.state.sending) throw new Error('正在发送上一条消息，请稍后。');
    if (!segmentText(draft.reply.segments).trim()) throw new Error('回复不能为空。');
    this.state.sending = true; this.activeDraft = draft; this.selfDuringSend = [];
    draft.status = 'sending'; draft.updatedAt = Date.now(); this.store.putDraft(draft); this.emit('change');
    try {
      const result = await this.transport.call<{ message_id?: number | string }>('send_private_msg', { user_id: c.contactId, message: draft.reply.segments }, 20000);
      if (result?.message_id == null) throw new ActionError('QQ 未返回消息 ID，发送结果待核。', true);
      draft.sentMessageId = String(result.message_id); draft.status = 'sent'; draft.updatedAt = Date.now();
      this.store.putDraft(draft);
      const message: ChatMessage = {
        id: c.accountId + ':' + c.contactId + ':' + draft.sentMessageId, accountId: c.accountId, contactId: c.contactId,
        messageId: draft.sentMessageId, sequence: '', senderId: c.accountId, time: Math.floor(Date.now() / 1000) * 1000,
        segments: draft.reply.segments, text: segmentText(draft.reply.segments), author: 'assistant', unsupported: false, origin: 'live',
      };
      this.store.addMessage(message); this.store.markAuthor(message.id, 'assistant');
    } catch (e) {
      draft.status = e instanceof ActionError && !e.uncertain ? 'failed' : 'unknown';
      draft.error = e instanceof Error ? e.message : String(e); this.state.lastError = draft.error; draft.updatedAt = Date.now(); this.store.putDraft(draft);
      if (c.mode === 'auto') this.store.saveConfig({ ...this.config(), paused: true, pauseReason: '发送未完成，请核对 QQ。' });
    } finally {
      this.state.sending = false; this.activeDraft = undefined;
      const unmatched = this.selfDuringSend.filter(m => m.messageId !== draft.sentMessageId);
      for (const m of this.selfDuringSend) this.store.markAuthor(m.id, m.messageId === draft.sentMessageId ? 'assistant' : draft.status === 'unknown' ? 'unknown' : 'human');
      this.selfDuringSend = [];
      if (unmatched.length) {
        if (draft.status === 'unknown') this.pause('本人消息与发送结果无法确认，请核对后恢复。');
        else this.manualReply('检测到你在发送期间手动回复。');
      }
      if (manual && draft.status === 'sent' && c.mode === 'auto') this.pause('你已确认发送，本轮已接管；需要继续时点击恢复处理。');
      this.emit('change');
    }
    return draft;
  }
  stop() { this.cancel('服务已停止。'); this.state.workerRunning = false; }
}
