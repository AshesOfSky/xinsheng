import { test, type TestContext } from 'node:test';
import assert from 'node:assert/strict';
import { Store } from '../src/server/store.js';
import { ReplyEngine } from '../src/server/engine.js';
import { ActionError } from '../src/server/onebot.js';
import type { Transport, Draft } from '../src/shared/types.js';
import { incoming, selfMessage, config, FakeModel, until, deferred, replyResult } from './helpers.js';
function setup(t: TestContext, mode: 'confirm' | 'auto' = 'confirm') {
  const store = new Store(':memory:'); store.saveConfig({ ...config, mode });
  const sent: any[] = [], model = new FakeModel();
  const transport: Transport = { async call<T>(action: string, params: any) { sent.push({ action, params }); return { message_id: 'sent-' + sent.length } as T; } };
  const engine = new ReplyEngine(store, transport, model, () => true); engine.beginLive(0);
  t.after(async () => { engine.stop(); await until(() => !engine.state.generating && !engine.state.sending); store.close(); });
  return { store, sent, model, engine, transport, drafts: () => store.drafts(config.accountId, config.contactId) };
}
test('人工确认模式自动生成但不发送；确认后继续等待新消息', async t => {
  const x = setup(t); x.engine.ingest(incoming(1)); await until(() => x.drafts().length === 1);
  assert.equal(x.sent.length, 0); await x.engine.send(x.drafts()[0].id);
  assert.equal(x.sent.length, 1); assert.equal(x.store.config().paused, false);
  x.engine.ingest(incoming(2)); await until(() => x.drafts().some(d => d.status === 'pending'));
  assert.equal(x.sent.length, 1);
});
test('重复推送只处理一次，相同文字不同 ID 分别保留并合并生成', async t => {
  const x = setup(t); x.engine.ingest(incoming(1, '好')); x.engine.ingest(incoming(1, '好')); x.engine.ingest(incoming(2, '好'));
  await until(() => x.drafts().length === 1);
  assert.equal(x.store.messageCount(config.accountId, config.contactId), 2);
  assert.equal(x.model.contexts.length, 1); assert.equal(x.drafts()[0].triggerIds.length, 2);
});
test('其他联系人和群聊不会进入目标会话', async t => {
  const x = setup(t);
  x.engine.ingest(incoming(1, '别人的消息', { user_id: 30003, sender: { user_id: 30003 } }));
  x.engine.ingest(incoming(2, '群消息', { message_type: 'group' }));
  assert.equal(x.store.messageCount(config.accountId, config.contactId), 0); assert.equal(x.engine.state.pendingCount, 0);
});
test('生成期间的新消息使旧结果失效，只发送最新一轮', async t => {
  const x = setup(t, 'auto'); const first = deferred<typeof replyResult>(); let calls = 0;
  x.model.generateFn = async () => ++calls === 1 ? first.promise : structuredClone(replyResult);
  x.engine.ingest(incoming(1)); await until(() => x.model.contexts.length === 1);
  x.engine.ingest(incoming(2, '我想先休息')); first.resolve(structuredClone(replyResult));
  await until(() => x.sent.length === 1);
  assert.equal(x.model.contexts.length, 2); assert.equal(x.drafts().filter(d => d.status === 'sent').length, 1);
  assert.equal(x.drafts().find(d => d.status === 'sent')!.triggerIds.length, 2);
});
test('自动切到人工会拦住正在生成的自动回复，旧草稿不会被切换自动后补发', async t => {
  const x = setup(t, 'auto'); const deferredReply = deferred<typeof replyResult>();
  x.model.generateFn = () => deferredReply.promise;
  x.engine.ingest(incoming(1)); await until(() => x.model.contexts.length === 1);
  const old = x.store.config(), next = { ...old, mode: 'confirm' as const };
  x.store.saveConfig(next); x.engine.configured(old, next); deferredReply.resolve(structuredClone(replyResult));
  await until(() => !x.engine.state.generating);
  assert.equal(x.sent.length, 0);
  x.store.saveConfig(old); x.engine.configured(next, old);
  assert.equal(x.sent.length, 0);
});
test('应用自己的发送事件先于回执也不会误判为人工接管', async t => {
  const x = setup(t, 'auto'); const ack = deferred<any>();
  x.transport.call = async <T>() => { x.sent.push('send'); return ack.promise as Promise<T>; };
  x.engine.ingest(incoming(1)); await until(() => x.engine.state.sending);
  x.engine.ingest(selfMessage('501')); ack.resolve({ message_id: '501' });
  await until(() => !x.engine.state.sending);
  assert.equal(x.store.config().paused, false);
  assert.equal(x.store.message('10001:20002:501')!.author, 'assistant');
});
test('应用回执先于本人消息事件不会误判或触发第二轮回复', async t => {
  const x = setup(t, 'auto'); x.engine.ingest(incoming(1)); await until(() => x.sent.length === 1 && !x.engine.state.sending);
  x.engine.ingest(selfMessage('sent-1'));
  assert.equal(x.store.config().paused, false); assert.equal(x.model.contexts.length, 1);
});
test('自动模式手机手动回复取消旧草稿并暂停，其他联系人的本人消息不影响此会话', async t => {
  const x = setup(t, 'auto'), first = deferred<typeof replyResult>();
  x.model.generateFn = () => first.promise;
  x.engine.ingest(incoming(1)); await until(() => x.engine.state.generating);
  x.engine.ingest({ ...selfMessage(40), target_id: 30003 }); assert.equal(x.store.config().paused, false);
  x.engine.ingest(selfMessage(41));
  first.resolve(structuredClone(replyResult)); await until(() => !x.engine.state.generating);
  assert.equal(x.store.config().paused, true); assert.equal(x.sent.length, 0);
  x.engine.ingest(incoming(42)); assert.equal(x.engine.state.pendingCount, 0);
});
test('确认模式手机回复只取消旧草稿，下一条对方消息继续生成且不发送', async t => {
  const x = setup(t); x.engine.ingest(incoming(1)); await until(() => x.drafts().length === 1);
  x.engine.ingest(selfMessage(41));
  assert.equal(x.store.config().paused, false); assert.equal(x.drafts()[0].status, 'stale');
  x.engine.ingest(incoming(42)); await until(() => x.drafts().some(d => d.status === 'pending'));
  assert.equal(x.model.contexts.length, 2); assert.equal(x.sent.length, 0);
});
test('明确暂停确认模式后，手机回复和新消息不会擅自恢复生成', async t => {
  const x = setup(t); x.engine.pause('已由你暂停。');
  x.engine.ingest(selfMessage(41)); x.engine.ingest(incoming(42));
  assert.equal(x.store.config().paused, true); assert.equal(x.store.config().pauseReason, '已由你暂停。');
  assert.equal(x.engine.state.pendingCount, 0); assert.equal(x.model.contexts.length, 0);
});
test('发送过程中存在不匹配的本人消息，核对回执后接管', async t => {
  const x = setup(t, 'auto'), ack = deferred<any>();
  x.transport.call = async <T>() => ack.promise as Promise<T>;
  x.engine.ingest(incoming(1)); await until(() => x.engine.state.sending);
  x.engine.ingest(selfMessage(50, '我自己回了')); x.engine.ingest(selfMessage(51)); ack.resolve({ message_id: 51 });
  await until(() => !x.engine.state.sending);
  assert.equal(x.store.config().paused, true); assert.equal(x.store.message('10001:20002:50')!.author, 'human');
  assert.equal(x.store.message('10001:20002:51')!.author, 'assistant');
});
test('发送超时标记待核，用户重复点击不能重发', async t => {
  const x = setup(t); x.transport.call = async () => { x.sent.push('attempt'); throw new ActionError('超时', true); };
  x.engine.ingest(incoming(1)); await until(() => x.drafts().length === 1);
  const d = await x.engine.send(x.drafts()[0].id); assert.equal(d.status, 'unknown');
  await assert.rejects(x.engine.send(d.id)); assert.equal(x.sent.length, 1);
  x.engine.resolveUnknown(d.id, 'not_sent'); assert.equal(x.drafts()[0].status, 'discarded');
});
test('模型失败、空格式错误不会成为发送正文', async t => {
  const x = setup(t, 'auto'); x.model.generateFn = async () => { throw new Error('模型额度不足'); };
  x.engine.ingest(incoming(1)); await until(() => x.drafts().length === 1);
  assert.equal(x.drafts()[0].status, 'needs_human'); assert.equal(x.store.config().paused, true); assert.equal(x.sent.length, 0);
});
test('不支持的媒体进入人工处理，不调用模型或发送', async t => {
  const x = setup(t, 'auto'); x.engine.ingest(incoming(1, '', { message: [{ type: 'image', data: { file: 'test.jpg' } }] }));
  await until(() => x.drafts().length === 1);
  assert.equal(x.model.contexts.length, 0); assert.equal(x.sent.length, 0); assert.equal(x.drafts()[0].status, 'needs_human');
});
test('历史导入和连接开始前的消息不会触发回复', async t => {
  const x = setup(t, 'auto'); x.engine.beginLive(Date.now());
  x.engine.ingest(incoming(1), 'history'); x.engine.ingest(incoming(2, '旧消息', { time: Math.floor(Date.now() / 1000) - 60 }));
  assert.equal(x.engine.state.pendingCount, 0); assert.equal(x.sent.length, 0); assert.equal(x.store.messageCount(config.accountId, config.contactId), 2);
});
test('重启将未发送草稿作废、发送中任务标待核，不补发', async t => {
  const x = setup(t);
  const base: Draft = { id: 'a', accountId: config.accountId, contactId: config.contactId, revision: 1, triggerIds: [], mode: 'auto', status: 'sending', reply: replyResult.reply, createdAt: Date.now(), updatedAt: Date.now(), sentMessageId: null, error: '', latencyMs: 0, usage: {}, edited: false };
  x.store.putDraft(base); x.store.putDraft({ ...base, id: 'b', status: 'pending' }); x.store.recover();
  assert.equal(x.store.draft('a')!.status, 'unknown'); assert.equal(x.store.draft('b')!.status, 'stale'); assert.equal(x.sent.length, 0);
});
test('面板手工接管暂停自动处理，必须填写并确认才发送', async t => {
  const x = setup(t, 'auto');
  const draft = x.engine.compose();
  assert.equal(x.store.config().paused, true);
  assert.equal(x.sent.length, 0); assert.equal(x.model.contexts.length, 0);
  await assert.rejects(x.engine.send(draft.id));
  x.engine.edit(draft.id, [{ type: 'text', data: { text: '手工测试' } }, { type: 'face', data: { id: '14' } }]);
  const result = await x.engine.send(draft.id);
  assert.equal(result.status, 'sent'); assert.equal(x.sent.length, 1);
  assert.equal(x.store.config().paused, true);
  await assert.rejects(x.engine.send(draft.id));
});
test('确认模式手工回复后仍会为新的对方消息生成草稿', async t => {
  const x = setup(t); const draft = x.engine.compose();
  assert.equal(x.store.config().paused, false);
  x.engine.edit(draft.id, [{ type: 'text', data: { text: '我先回一句' } }]);
  await x.engine.send(draft.id);
  x.engine.ingest(incoming(42)); await until(() => x.drafts().some(d => d.status === 'pending'));
  assert.equal(x.sent.length, 1); assert.equal(x.model.contexts.length, 1);
});
