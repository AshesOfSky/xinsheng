import { test } from 'node:test';
import assert from 'node:assert/strict';
import { Store } from '../src/server/store.js';
import { HistoryImporter } from '../src/server/history.js';
import { AnalysisService, groundedAnalysis, adjacentExamples, replayCases } from '../src/server/analysis.js';
import { normalizeMessage } from '../src/server/messages.js';
import { config, incoming, selfMessage, FakeModel, until, analysisResult } from './helpers.js';
import type { ChatMessage, Transport, Profile } from '../src/shared/types.js';
function historical(id: number, seconds: number, text = '消息') { return incoming(id, text, { time: seconds }); }
test('历史分页向旧消息读取且重叠不重复，缺少 target_id 的本人历史仍正确归属', async t => {
  const store = new Store(':memory:'); store.saveConfig(config); t.after(() => store.close());
  const calls: any[] = [];
  const transport: Transport = { async call<T>(_action: string, params: any) {
    calls.push(params);
    const pages: Record<string, any[]> = {
      start: [historical(3, 30), { ...selfMessage(2, '本人原话'), time: 20, target_id: undefined }],
      '2': [historical(2, 20), historical(1, 10)],
    };
    // NapCat's false direction reads forward from the cursor, including itself.
    const pageKey = params.message_seq && params.reverse_order ? params.message_seq : 'start';
    return { messages: pages[pageKey] || [] } as T;
  } };
  const importer = new HistoryImporter(store, transport, () => true);
  const job = importer.start(10000, 30000); await until(() => !importer.isRunning);
  const result = store.historyJob(job.id)!;
  assert.equal(result.status, 'completed'); assert.equal(result.imported, 3);
  assert.equal(result.earliest, 10000); assert.equal(result.latest, 30000);
  assert.equal(calls[1].message_seq, '2');
  assert.equal(calls[1].reverse_order, true);
  assert.equal(store.message('10001:20002:2')!.author, 'human');
});
test('分页错误保留进度，恢复从上一个游标继续', async t => {
  const store = new Store(':memory:'); store.saveConfig(config); t.after(() => store.close());
  let fail = true; const cursors: string[] = [];
  const transport: Transport = { async call<T>(_action: string, p: any) {
    cursors.push(p.message_seq || '');
    if (!p.message_seq) return { messages: [historical(3, 30), historical(2, 20)] } as T;
    if (fail) throw new Error('测试断线');
    return { messages: [historical(2, 20), historical(1, 10)] } as T;
  } };
  const importer = new HistoryImporter(store, transport, () => true);
  const job = importer.start(10000, 40000); await until(() => !importer.isRunning);
  assert.equal(store.historyJob(job.id)!.status, 'partial'); assert.equal(store.historyJob(job.id)!.cursor, '2');
  fail = false; importer.resume(job.id); await until(() => !importer.isRunning);
  assert.equal(cursors.at(-1), '2'); assert.equal(store.historyJob(job.id)!.imported, 3);
  assert.equal(store.historyJob(job.id)!.status, 'completed');
});
test('空页与不前进的游标保留为部分取得，不冒充完整历史', async t => {
  const store = new Store(':memory:'); store.saveConfig(config); t.after(() => store.close());
  const transport: Transport = { async call<T>() { return { messages: [historical(5, 50)] } as T; } };
  const importer = new HistoryImporter(store, transport, () => true);
  const job = importer.start(null, 60000); await until(() => !importer.isRunning);
  assert.equal(store.historyJob(job.id)!.status, 'partial'); assert.equal(store.historyJob(job.id)!.pages, 2);
});
test('中文检索返回相关示例，并过滤未来训练画像及记忆', t => {
  const store = new Store(':memory:'); t.after(() => store.close());
  const p: Profile = { id: 'profile', name: '测试', style: '短句', learnedUntil: 100, updatedAt: 100, examples: [
    { id: 'one', context: '今天工作很累', reply: '早点休息', sourceIds: ['source'], sourceTime: 100 },
    { id: 'two', context: '周末去看电影', reply: '好呀', sourceIds: ['source2'], sourceTime: 100 },
  ] };
  store.putProfile(p); assert.equal(store.matchingExamples(p, '工作累了', 1000)[0].id, 'one');
  assert.equal(store.matchingExamples(p, '工作', 50).length, 0);
  store.saveConfig({ ...config, profileId: 'profile' });
  store.putMemory({ id: 'future', accountId: config.accountId, contactId: config.contactId, text: '未来才知道', status: 'confirmed', sourceIds: [], sourceTime: 2000, updatedAt: 2000 });
  store.putSummary({ accountId: config.accountId, contactId: config.contactId, text: '未来摘要', throughTime: 2000 });
  const context = store.context(store.config(), 1000); assert.equal(context.memories.length, 0); assert.equal(context.summary, '');
});
test('模型伪造的示例来源与非本人回复不进入画像', () => {
  const source = [normalizeMessage({ ...selfMessage(1, '早点休息'), time: 10 }, config, 'history')!];
  const result = groundedAnalysis({ ...analysisResult, examples: [
    { context: '累了', reply: '早点休息', sourceIds: [source[0].id] },
    { context: '累了', reply: '明天我会去接你', sourceIds: [source[0].id] },
    { context: '累了', reply: '早点休息', sourceIds: ['fake'] },
  ], memories: [{ text: '假来源', sourceIds: ['fake'], status: 'confirmed' }] }, source);
  assert.equal(result.examples.length, 1); assert.equal(result.memories.length, 0);
});
test('离线回放不泄露同一秒的原回复或之后消息，画像训练早于最早案例', async t => {
  const store = new Store(':memory:'); store.saveConfig(config); t.after(() => store.close());
  for (let i = 0; i < 24; i++) {
    store.addMessage(normalizeMessage(incoming('in-' + i, '问题-' + i, { time: 100 + i }), config, 'history')!);
    store.addMessage(normalizeMessage({ ...selfMessage('out-' + i, '原回复秘密-' + i), time: 100 + i }, config, 'history')!);
  }
  const model = new FakeModel(), analysis = new AnalysisService(store, model);
  const record: any = await analysis.replay();
  assert.equal(record.status, 'completed'); assert.equal(record.results.length, 20);
  assert.equal(model.contexts.length, 20);
  for (let i = 0; i < 20; i++) {
    const ctx = model.contexts[i], caseNumber = i + 4;
    assert.equal(ctx.messages.at(-1)!.text, '问题-' + caseNumber);
    assert.equal(ctx.messages.some(m => m.text === '原回复秘密-' + caseNumber), false);
    assert.equal(ctx.messages.some(m => m.time > (100 + caseNumber) * 1000), false);
  }
  assert.ok(model.analyses[0].every(m => m.time <= 103000));
});
test('分批风格任务可完成，自动回复样本被排除且不自动绑定新画像', async t => {
  const store = new Store(':memory:'); store.saveConfig(config); t.after(() => store.close());
  const human = normalizeMessage(selfMessage(1, '本人原话'), config, 'history')!;
  store.addMessage(human); store.addMessage({ ...human, id: 'assistant', messageId: 'assistant', text: 'AI 自动输出', author: 'assistant' });
  const model = new FakeModel(), analysis = new AnalysisService(store, model), job = analysis.start();
  await until(() => !analysis.state.running);
  assert.equal(analysis.jobs().find(j => j.id === job.id)!.status, 'completed');
  assert.equal(model.analyses[0].length, 1); assert.equal(model.analyses[0][0].author, 'human');
  assert.equal(store.config().profileId, '');
});
test('模型未返回结构化示例时，使用真实相邻对话并保留 AI 发言的边界', () => {
  const contact = normalizeMessage(incoming(1, '累了'), config, 'history')!;
  const human = normalizeMessage(selfMessage(2, '歇会儿'), config, 'history')!;
  const examples = adjacentExamples([contact, human]);
  assert.equal(examples.length, 1); assert.equal(examples[0].reply, '歇会儿'); assert.deepEqual(examples[0].sourceIds, [contact.id, human.id]);
  assert.equal(adjacentExamples([contact, { ...human, id: 'ai', author: 'assistant' }, human]).length, 0);
});
test('同一秒的历史按 real_seq 排序，不受倒序分页入库顺序影响', t => {
  const store = new Store(':memory:'); t.after(() => store.close());
  const late = normalizeMessage({ ...selfMessage(102, '后发'), time: 100, real_seq: '22' }, config, 'history')!;
  const early = normalizeMessage(incoming(101, '先发', { time: 100, real_seq: '21' }), config, 'history')!;
  store.addMessage(late); store.addMessage(early);
  assert.deepEqual(store.messages(config.accountId, config.contactId).map(m => m.text), ['先发', '后发']);
});
test('文字风格回放排除对方本轮或本人回应中的不可读媒体', () => {
  const contact = (id: number) => normalizeMessage(incoming(id, '问题'), config, 'history')!;
  const human = (id: number) => normalizeMessage(selfMessage(id, '回答'), config, 'history')!;
  const messages: ChatMessage[] = [
    { ...contact(1), unsupported: true }, contact(2), human(3),
    contact(4), human(5), { ...human(6), unsupported: true },
    contact(7), human(8),
  ];
  const cases = replayCases(messages);
  assert.equal(cases.length, 1); assert.equal(cases[0].triggerIndex, 6);
  assert.equal(cases[0].expected[0].messageId, '8');
});
