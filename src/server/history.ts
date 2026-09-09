import { EventEmitter } from 'node:events';
import { randomUUID } from 'node:crypto';
import type { Config, HistoryJob, Transport } from '../shared/types.js';
import { Store } from './store.js';
import { normalizeMessage } from './messages.js';

export class HistoryImporter extends EventEmitter {
  private active?: string;
  private stopping = false;
  constructor(private store: Store, private transport: Transport, private ready: () => boolean) { super(); }
  get isRunning() { return this.active !== undefined; }
  start(startTime: number | null, endTime: number): HistoryJob {
    if (!this.ready()) throw new Error('请先连接并选择当前 QQ 联系人。');
    if (this.active) throw new Error('已有历史导入任务正在运行。');
    if (startTime != null && startTime > endTime) throw new Error('开始时间不能晚于结束时间。');
    const c = this.store.config();
    const job: HistoryJob = { id: randomUUID(), accountId: c.accountId, contactId: c.contactId, startTime, endTime, cursor: null, status: 'running', fetched: 0, imported: 0, earliest: null, latest: null, pages: 0, error: '', createdAt: Date.now(), updatedAt: Date.now() };
    this.store.putHistoryJob(job); void this.run(job); return job;
  }
  resume(id: string) {
    if (this.active) throw new Error('已有导入任务正在运行。');
    if (!this.ready()) throw new Error('QQ 尚未就绪。');
    const job = this.store.historyJob(id), c = this.store.config();
    if (!job || job.accountId !== c.accountId || job.contactId !== c.contactId) throw new Error('请选择任务对应的账号及联系人。');
    if (job.status === 'completed') throw new Error('该范围已处理完成。');
    job.status = 'running'; job.error = ''; this.store.putHistoryJob(job); void this.run(job); return job;
  }
  pause(id: string) {
    if (this.active !== id) throw new Error('这项导入当前没有运行。');
    this.stopping = true;
    const job = this.store.historyJob(id)!; job.status = 'paused'; this.store.putHistoryJob(job); this.emit('change');
  }
  stop() { this.stopping = true; }
  private async run(job: HistoryJob) {
    this.active = job.id; this.stopping = false; this.emit('change');
    const initial = this.store.config();
    const scoped: Config = { ...initial, accountId: job.accountId, contactId: job.contactId };
    try {
      while (!this.stopping) {
        if (!this.ready() || this.store.config().accountId !== job.accountId || this.store.config().contactId !== job.contactId) {
          job.status = 'paused'; job.error = '连接或联系人已改变，可以恢复导入。'; break;
        }
        const response = await this.transport.call<{ messages: Record<string, any>[] }>('get_friend_msg_history', {
          user_id: job.contactId, count: 100, ...(job.cursor ? { message_seq: job.cursor } : {}),
          reverse_order: true, disable_get_url: true, parse_mult_msg: false,
        }, 60000);
        if (this.stopping) { job.status = 'paused'; break; }
        if (!Array.isArray(response?.messages)) throw new Error('历史接口未返回 messages 数组。');
        const page = response.messages;
        if (!page.length) { job.status = 'partial'; job.error = '接口没有返回更早的记录，历史完整性待核。'; break; }
        page.sort((a, b) => Number(a.time) - Number(b.time) || (Number(a.real_seq ?? 0) - Number(b.real_seq ?? 0)));
        let reachedStart = false;
        for (const raw of page) {
          const m = normalizeMessage({ ...raw, target_id: raw.target_id ?? job.contactId, message_type: 'private' }, scoped, 'history');
          if (!m) continue;
          job.fetched++;
          if (job.startTime != null && m.time <= job.startTime) reachedStart = true;
          if (m.time > job.endTime || (job.startTime != null && m.time < job.startTime)) continue;
          if (this.store.addMessage(m)) job.imported++;
          job.earliest = Math.min(job.earliest ?? m.time, m.time); job.latest = Math.max(job.latest ?? m.time, m.time);
        }
        job.pages++;
        const nextCursor = page[0]?.message_id == null ? null : String(page[0].message_id);
        if (reachedStart) { job.status = 'completed'; job.error = '已到达所选开始时间，覆盖情况仍以实际返回记录为准。'; }
        else if (!nextCursor || nextCursor === job.cursor) { job.status = 'partial'; job.error = '接口分页不再前进，已保留全部取得的记录；更早范围待核。'; }
        else job.cursor = nextCursor;
        this.store.putHistoryJob(job); this.emit('change');
        if (job.status !== 'running') break;
      }
      if (this.stopping && job.status === 'running') job.status = 'paused';
    } catch (e) {
      job.status = job.pages ? 'partial' : 'failed';
      job.error = e instanceof Error ? e.message : String(e);
    } finally {
      this.store.putHistoryJob(job); this.active = undefined; this.emit('change');
    }
  }
}
