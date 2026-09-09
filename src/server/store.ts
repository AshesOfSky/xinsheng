import { DatabaseSync } from 'node:sqlite';
import { mkdirSync } from 'node:fs';
import { dirname } from 'node:path';
import { randomUUID } from 'node:crypto';
import { defaultConfig, type Config, type ChatMessage, type Draft, type Profile, type Memory, type HistoryJob, type Summary, type GenerationContext, type Example } from '../shared/types.js';

const segmenter = new Intl.Segmenter('zh-CN', { granularity: 'word' });
export function tokenize(text: string): string[] {
  const words = [...segmenter.segment(text.toLowerCase())].filter(s => s.isWordLike).map(s => s.segment);
  const grams = (text.match(/[\p{Script=Han}]+/gu) ?? []).flatMap(s => [...s].slice(1).map((_, i) => [...s].slice(i, i + 2).join('')));
  return [...new Set([...words, ...grams])];
}
function readJson<T>(row: unknown): T | undefined {
  return row ? JSON.parse((row as { json: string }).json) as T : undefined;
}
export class Store {
  readonly db: DatabaseSync;
  constructor(path: string) {
    if (path !== ':memory:') mkdirSync(dirname(path), { recursive: true });
    this.db = new DatabaseSync(path);
    this.db.exec(
      'PRAGMA journal_mode=WAL; PRAGMA busy_timeout=5000; PRAGMA foreign_keys=ON;' +
      'CREATE TABLE IF NOT EXISTS settings (id INTEGER PRIMARY KEY CHECK(id=1), json TEXT NOT NULL);' +
      'CREATE TABLE IF NOT EXISTS messages (id TEXT PRIMARY KEY, account TEXT NOT NULL, contact TEXT NOT NULL, time INTEGER NOT NULL, author TEXT NOT NULL, json TEXT NOT NULL);' +
      'CREATE INDEX IF NOT EXISTS messages_conversation ON messages(account,contact,time);' +
      'CREATE TABLE IF NOT EXISTS drafts (id TEXT PRIMARY KEY, account TEXT NOT NULL, contact TEXT NOT NULL, status TEXT NOT NULL, time INTEGER NOT NULL, json TEXT NOT NULL);' +
      'CREATE TABLE IF NOT EXISTS profiles (id TEXT PRIMARY KEY, json TEXT NOT NULL);' +
      'CREATE VIRTUAL TABLE IF NOT EXISTS examples_fts USING fts5(id UNINDEXED, profile_id UNINDEXED, tokens);' +
      'CREATE TABLE IF NOT EXISTS memories (id TEXT PRIMARY KEY, account TEXT NOT NULL, contact TEXT NOT NULL, json TEXT NOT NULL);' +
      'CREATE TABLE IF NOT EXISTS history_jobs (id TEXT PRIMARY KEY, time INTEGER NOT NULL, json TEXT NOT NULL);' +
      'CREATE TABLE IF NOT EXISTS summaries (id TEXT PRIMARY KEY, json TEXT NOT NULL);' +
      'CREATE TABLE IF NOT EXISTS replays (id TEXT PRIMARY KEY, json TEXT NOT NULL);'
    );
    if (!this.db.prepare('SELECT 1 FROM settings WHERE id=1').get()) this.saveConfig(defaultConfig);
  }
  close() { this.db.close(); }
  config(): Config { return { ...defaultConfig, ...readJson<Config>(this.db.prepare('SELECT json FROM settings WHERE id=1').get()) }; }
  saveConfig(config: Config) { this.db.prepare('INSERT OR REPLACE INTO settings VALUES(1,?)').run(JSON.stringify(config)); }
  addMessage(message: ChatMessage): boolean {
    return this.db.prepare('INSERT OR IGNORE INTO messages VALUES(?,?,?,?,?,?)')
      .run(message.id, message.accountId, message.contactId, message.time, message.author, JSON.stringify(message)).changes === 1;
  }
  updateMessage(message: ChatMessage) {
    this.db.prepare('UPDATE messages SET time=?,author=?,json=? WHERE id=?').run(message.time, message.author, JSON.stringify(message), message.id);
  }
  message(id: string) { return readJson<ChatMessage>(this.db.prepare('SELECT json FROM messages WHERE id=?').get(id)); }
  markAuthor(id: string, author: ChatMessage['author']) {
    const m = this.message(id);
    if (m) { m.author = author; this.db.prepare('UPDATE messages SET author=?,json=? WHERE id=?').run(author, JSON.stringify(m), id); }
  }
  messages(account: string, contact: string, options: { before?: number; limit?: number; after?: number } = {}): ChatMessage[] {
    const rows = this.db.prepare("SELECT json FROM messages WHERE account=? AND contact=? AND time<=? AND time>=? ORDER BY time DESC,CAST(json_extract(json,'$.sequence') AS INTEGER) DESC,rowid DESC LIMIT ?")
      .all(account, contact, options.before ?? Number.MAX_SAFE_INTEGER, options.after ?? 0, options.limit ?? 200);
    return rows.map(r => readJson<ChatMessage>(r)!).reverse();
  }
  messageCount(account: string, contact: string): number {
    return Number((this.db.prepare('SELECT count(*) AS n FROM messages WHERE account=? AND contact=?').get(account, contact) as { n: number }).n);
  }
  putDraft(draft: Draft) {
    this.db.prepare('INSERT OR REPLACE INTO drafts VALUES(?,?,?,?,?,?)').run(draft.id, draft.accountId, draft.contactId, draft.status, draft.createdAt, JSON.stringify(draft));
  }
  draft(id: string) { return readJson<Draft>(this.db.prepare('SELECT json FROM drafts WHERE id=?').get(id)); }
  drafts(account: string, contact: string, limit = 100): Draft[] {
    return this.db.prepare('SELECT json FROM drafts WHERE account=? AND contact=? ORDER BY time DESC LIMIT ?')
      .all(account, contact, limit).map(r => readJson<Draft>(r)!);
  }
  stalePending(account: string, contact: string, reason: string) {
    for (const d of this.drafts(account, contact, 10000)) {
      if (d.status === 'pending' || d.status === 'needs_human') {
        d.status = 'stale'; d.error = reason; d.updatedAt = Date.now(); this.putDraft(d);
      }
    }
  }
  recover() {
    for (const r of this.db.prepare("SELECT json FROM drafts WHERE status IN ('sending','pending','needs_human')").all()) {
      const d = readJson<Draft>(r)!;
      d.status = d.status === 'sending' ? 'unknown' : 'stale';
      d.error = d.status === 'unknown' ? '服务在发送过程中中断，请核对 QQ，系统不会自动重发。' : '服务已重启，请基于最新消息重新生成。';
      this.putDraft(d);
    }
    for (const j of this.historyJobs()) if (j.status === 'running') { j.status = 'paused'; j.error = '服务重启，导入可继续。'; this.putHistoryJob(j); }
  }
  profile(id: string) { return readJson<Profile>(this.db.prepare('SELECT json FROM profiles WHERE id=?').get(id)); }
  profiles(): Profile[] { return this.db.prepare('SELECT json FROM profiles').all().map(r => readJson<Profile>(r)!); }
  putProfile(profile: Profile) {
    this.db.exec('BEGIN');
    try {
      this.db.prepare('INSERT OR REPLACE INTO profiles VALUES(?,?)').run(profile.id, JSON.stringify(profile));
      this.db.prepare('DELETE FROM examples_fts WHERE profile_id=?').run(profile.id);
      const insert = this.db.prepare('INSERT INTO examples_fts VALUES(?,?,?)');
      for (const e of profile.examples) insert.run(e.id, profile.id, tokenize(e.context).join(' '));
      this.db.exec('COMMIT');
    } catch (e) { this.db.exec('ROLLBACK'); throw e; }
  }
  matchingExamples(profile: Profile | undefined, query: string, before: number): Example[] {
    if (!profile || profile.learnedUntil > before) return [];
    const terms = tokenize(query).slice(-50);
    const eligible = profile.examples.filter(e => e.sourceTime <= before).sort((a, b) => b.sourceTime - a.sourceTime);
    if (!terms.length) return eligible.slice(0, 6);
    const match = terms.map(t => '"' + t.replaceAll('"', '""') + '"').join(' OR ');
    const rows = this.db.prepare('SELECT id FROM examples_fts WHERE examples_fts MATCH ? AND profile_id=? ORDER BY rank LIMIT 30').all(match, profile.id) as { id: string }[];
    const ranked = rows.map(r => eligible.find(e => e.id === r.id)).filter((e): e is Example => !!e);
    return [...ranked, ...eligible.filter(e => !ranked.some(r => r.id === e.id))].slice(0, 6);
  }
  memories(account: string, contact: string): Memory[] {
    return this.db.prepare('SELECT json FROM memories WHERE account=? AND contact=?').all(account, contact).map(r => readJson<Memory>(r)!);
  }
  putMemory(memory: Memory) {
    this.db.prepare('INSERT OR REPLACE INTO memories VALUES(?,?,?,?)').run(memory.id, memory.accountId, memory.contactId, JSON.stringify(memory));
  }
  deleteMemory(id: string, account: string, contact: string) { this.db.prepare('DELETE FROM memories WHERE id=? AND account=? AND contact=?').run(id, account, contact); }
  putSummary(summary: Summary) {
    this.db.prepare('INSERT OR REPLACE INTO summaries VALUES(?,?)').run(summary.accountId + ':' + summary.contactId, JSON.stringify(summary));
  }
  context(config: Config, before = Date.now()): GenerationContext {
    const messages = this.messages(config.accountId, config.contactId, { before, limit: 40 });
    const profile = this.profile(config.profileId);
    const summary = readJson<Summary>(this.db.prepare('SELECT json FROM summaries WHERE id=?').get(config.accountId + ':' + config.contactId));
    const terms = new Set(tokenize(messages.slice(-5).map(m => m.text).join(' ')));
    const memories = this.memories(config.accountId, config.contactId).filter(m => m.sourceTime <= before);
    const relevant = memories.map(m => ({ m, matches: tokenize(m.text).filter(t => terms.has(t)).length }))
      .filter(r => r.matches > 0).sort((a, b) => b.matches - a.matches || b.m.sourceTime - a.m.sourceTime).map(r => r.m);
    const recent = memories.filter(m => m.status === 'confirmed').sort((a, b) => b.sourceTime - a.sourceTime).slice(0, 4);
    return {
      messages, now: before,
      style: profile && profile.learnedUntil <= before ? profile.style : '',
      examples: this.matchingExamples(profile, messages.slice(-5).map(m => m.text).join(' '), before),
      memories: [...relevant, ...recent.filter(m => !relevant.some(r => r.id === m.id))].slice(0, 12),
      summary: summary && summary.throughTime <= before ? summary.text : '',
    };
  }
  putHistoryJob(job: HistoryJob) { job.updatedAt = Date.now(); this.db.prepare('INSERT OR REPLACE INTO history_jobs VALUES(?,?,?)').run(job.id, job.createdAt, JSON.stringify(job)); }
  historyJob(id: string) { return readJson<HistoryJob>(this.db.prepare('SELECT json FROM history_jobs WHERE id=?').get(id)); }
  historyJobs(): HistoryJob[] { return this.db.prepare('SELECT json FROM history_jobs ORDER BY time DESC').all().map(r => readJson<HistoryJob>(r)!); }
  saveReplay(record: Record<string, unknown> & { id: string }) { this.db.prepare('INSERT OR REPLACE INTO replays VALUES(?,?)').run(record.id, JSON.stringify(record)); }
  replays(): Record<string, unknown>[] { return this.db.prepare('SELECT json FROM replays ORDER BY rowid DESC LIMIT 30').all().map(r => readJson<Record<string, unknown>>(r)!); }
  makeProfile(name = '我的表达风格'): Profile {
    const profile: Profile = { id: randomUUID(), name, style: '', examples: [], learnedUntil: 0, updatedAt: Date.now() };
    this.putProfile(profile); return profile;
  }
}
