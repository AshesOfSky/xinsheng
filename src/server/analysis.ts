import { EventEmitter } from 'node:events';
import { randomUUID } from 'node:crypto';
import type { AnalysisResult, ChatMessage, Example, GenerationContext, Memory, ModelProvider, Profile } from '../shared/types.js';
import { Store } from './store.js';

interface AnalysisJob {
  id: string; accountId: string; contactId: string; profileId: string; sourceIds: string[];
  processed: number; status: 'running' | 'paused' | 'completed' | 'failed'; error: string;
  summary: string; updatedAt: number;
}
export function groundedAnalysis(result: AnalysisResult, source: ChatMessage[]) {
  const allowed = new Map(source.filter(m => m.author === 'human' || m.author === 'contact').map(m => [m.id, m]));
  const validIds = (ids: string[]) => ids.length > 0 && ids.every(id => allowed.has(id));
  const examples: Example[] = result.examples.filter(e =>
    e.reply.trim().length > 0 && validIds(e.sourceIds) && e.sourceIds.some(id => allowed.get(id)?.author === 'human') &&
    e.sourceIds.filter(id => allowed.get(id)?.author === 'human').map(id => allowed.get(id)!.text).join('\n').includes(e.reply)
  ).map(e => ({ ...e, id: randomUUID(), sourceTime: Math.max(...e.sourceIds.map(id => allowed.get(id)!.time)) }));
  const memories = result.memories.filter(m => validIds(m.sourceIds)).map(m => ({ ...m, sourceTime: Math.max(...m.sourceIds.map(id => allowed.get(id)!.time)) }));
  return { examples, memories };
}
export function adjacentExamples(messages: ChatMessage[]): Example[] {
  const examples: Example[] = [];
  for (let i = 1; i < messages.length; i++) {
    if (messages[i].author !== 'human' || messages[i].unsupported || messages[i - 1].author !== 'contact' || messages[i - 1].unsupported) continue;
    let from = i - 1, to = i + 1;
    while (from > 0 && i - from < 4 && messages[from - 1].author === 'contact' && !messages[from - 1].unsupported) from--;
    while (to < messages.length && messages[to].author === 'human' && !messages[to].unsupported) to++;
    const sources = messages.slice(from, to);
    const reply = messages.slice(i, to).map(m => m.text).join('\n');
    if (reply.trim()) examples.push({ id: randomUUID(), context: messages.slice(from, i).map(m => m.text).join('\n'), reply, sourceIds: sources.map(m => m.id), sourceTime: sources.at(-1)!.time });
    i = to - 1;
  }
  return examples;
}
export function replayCases(messages: ChatMessage[], count = 20) {
  const candidates: { triggerIndex: number; expected: ChatMessage[] }[] = [];
  for (let i = 1; i < messages.length; i++) {
    if (messages[i].author !== 'human' || messages[i - 1].author !== 'contact' || messages[i - 1].unsupported) continue;
    const expected = [messages[i]];
    for (let j = i + 1; j < messages.length && messages[j].author === 'human'; j++) expected.push(messages[j]);
    if (expected.some(m => m.unsupported)) continue;
    let readableInput = true;
    for (let j = i - 1; j >= 0 && messages[j].author === 'contact'; j--) if (messages[j].unsupported) readableInput = false;
    if (!readableInput) continue;
    candidates.push({ triggerIndex: i - 1, expected });
  }
  return candidates.slice(-count);
}
export class AnalysisService extends EventEmitter {
  private abort?: AbortController;
  state = { running: false, task: '', processed: 0, total: 0, error: '' };
  constructor(private store: Store, private model: ModelProvider) {
    super();
    store.db.exec('CREATE TABLE IF NOT EXISTS analysis_jobs (id TEXT PRIMARY KEY, json TEXT NOT NULL)');
    for (const j of this.jobs()) if (j.status === 'running') { j.status = 'paused'; j.error = '服务重启，可继续分析。'; this.put(j); }
  }
  private put(job: AnalysisJob) { job.updatedAt = Date.now(); this.store.db.prepare('INSERT OR REPLACE INTO analysis_jobs VALUES(?,?)').run(job.id, JSON.stringify(job)); }
  jobs(): AnalysisJob[] { return this.store.db.prepare('SELECT json FROM analysis_jobs ORDER BY rowid DESC').all().map(r => JSON.parse((r as { json: string }).json)); }
  start() {
    if (this.state.running) throw new Error('已有模型分析或回放正在运行。');
    const c = this.store.config();
    const messages = this.store.messages(c.accountId, c.contactId, { limit: 1000000 });
    if (!messages.some(m => m.author === 'human' && !m.unsupported)) throw new Error('请先导入包含本人发言的历史记录。');
    const profile = this.store.makeProfile('从历史提取 · ' + new Date().toLocaleDateString('zh-CN'));
    const job: AnalysisJob = { id: randomUUID(), accountId: c.accountId, contactId: c.contactId, profileId: profile.id, sourceIds: messages.map(m => m.id), processed: 0, status: 'running', error: '', summary: '', updatedAt: Date.now() };
    this.put(job); void this.run(job); return job;
  }
  resume(id: string) {
    if (this.state.running) throw new Error('已有任务正在运行。');
    const job = this.jobs().find(j => j.id === id), c = this.store.config();
    if (!job || job.accountId !== c.accountId || job.contactId !== c.contactId) throw new Error('请选择任务对应的账号和联系人。');
    if (job.status === 'completed') throw new Error('分析已完成。');
    job.status = 'running'; job.error = ''; this.put(job); void this.run(job);
  }
  stop() { this.abort?.abort(); }
  private async run(job: AnalysisJob) {
    this.abort = new AbortController(); const signal = this.abort.signal;
    this.state = { running: true, task: '分批提取历史风格', processed: job.processed, total: job.sourceIds.length, error: '' }; this.emit('change');
    try {
      while (job.processed < job.sourceIds.length) {
        const profile = this.store.profile(job.profileId)!;
        const batch = job.sourceIds.slice(Math.max(0, job.processed - 10), job.processed + 200).map(id => this.store.message(id)).filter((m): m is ChatMessage => !!m);
        const eligible = batch.filter(m => ['human', 'contact'].includes(m.author) && !m.unsupported);
        const result = eligible.length ? await this.model.analyze(eligible, signal, { style: profile.style, summary: job.summary }) :
          { style: profile.style, summary: job.summary, examples: [], memories: [] } as AnalysisResult;
        if (signal.aborted) throw new DOMException('已暂停', 'AbortError');
        const grounded = groundedAnalysis(result, batch);
        if (!grounded.examples.length) grounded.examples.push(...adjacentExamples(batch));
        profile.style = result.style; profile.learnedUntil = Math.max(profile.learnedUntil, ...batch.map(m => m.time));
        profile.updatedAt = Date.now();
        for (const e of grounded.examples) if (!profile.examples.some(existing => existing.reply === e.reply && existing.sourceIds.join(',') === e.sourceIds.join(','))) profile.examples.push(e);
        this.store.putProfile(profile);
        const existing = this.store.memories(job.accountId, job.contactId);
        for (const m of grounded.memories) if (!existing.some(e => e.text === m.text)) {
          this.store.putMemory({ ...m, id: randomUUID(), accountId: job.accountId, contactId: job.contactId, status: 'unverified', updatedAt: Date.now() });
        }
        job.summary = result.summary; this.store.putSummary({ accountId: job.accountId, contactId: job.contactId, text: job.summary, throughTime: profile.learnedUntil });
        job.processed = Math.min(job.sourceIds.length, job.processed + 200);
        this.put(job); this.state.processed = job.processed; this.emit('change');
      }
      job.status = 'completed';
      // Binding remains an explicit UI choice; finishing analysis does not change active persona.
    } catch (e) {
      job.status = signal.aborted ? 'paused' : 'failed'; job.error = e instanceof Error ? e.message : String(e); this.state.error = job.error;
    } finally { this.put(job); this.state.running = false; this.abort = undefined; this.emit('change'); }
  }
  async replay() {
    if (this.state.running) throw new Error('已有任务正在运行。');
    const c = this.store.config();
    const all = this.store.messages(c.accountId, c.contactId, { limit: 1000000 });
    const cases = replayCases(all);
    if (!cases.length) throw new Error('没有可回放的“对方消息→本人回复”样本。');
    this.abort = new AbortController(); const signal = this.abort.signal;
    this.state = { running: true, task: '离线回放（不发送 QQ）', processed: 0, total: cases.length, error: '' }; this.emit('change');
    const record: Record<string, any> & { id: string } = { id: randomUUID(), accountId: c.accountId, contactId: c.contactId, createdAt: Date.now(), status: 'running', count: cases.length, requestedCount: 20, results: [], error: '', trainingCount: 0 };
    this.store.saveReplay(record);
    try {
      const trainingFull = all.slice(0, cases[0].triggerIndex).slice(-800);
      const training = trainingFull.filter(m => ['human', 'contact'].includes(m.author) && !m.unsupported);
      record.trainingCount = training.length;
      const learned = training.some(m => m.author === 'human') ? await this.model.analyze(training, signal) : { style: '', summary: '', examples: [], memories: [] } as AnalysisResult;
      const grounded = groundedAnalysis(learned, training);
      if (!grounded.examples.length) grounded.examples.push(...adjacentExamples(trainingFull));
      const memories: Memory[] = grounded.memories.map(m => ({ ...m, id: randomUUID(), accountId: c.accountId, contactId: c.contactId, status: 'unverified', updatedAt: Date.now() }));
      for (const item of cases) {
        if (signal.aborted) throw new DOMException('已停止回放', 'AbortError');
        const prefix = all.slice(0, item.triggerIndex + 1);
        const context: GenerationContext = {
          messages: prefix.slice(-40), style: learned.style, summary: learned.summary,
          examples: grounded.examples.slice(-6), memories, now: prefix.at(-1)!.time,
        };
        const result = await this.model.generate(context, signal);
        record.results.push({ triggerId: prefix.at(-1)!.id, input: prefix.slice(-6).map(m => ({ author: m.author, text: m.text, time: m.time })), expected: item.expected.map(m => m.text).join('\n'), generated: result.reply, latencyMs: result.latencyMs, usage: result.usage, review: '' });
        this.state.processed++; this.store.saveReplay(record); this.emit('change');
      }
      record.status = 'completed';
    } catch (e) {
      record.status = signal.aborted ? 'paused' : 'failed'; record.error = e instanceof Error ? e.message : String(e); this.state.error = record.error;
    } finally { this.store.saveReplay(record); this.state.running = false; this.abort = undefined; this.emit('change'); }
    return record;
  }
}
