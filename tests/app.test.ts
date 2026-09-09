import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { basename, dirname, join } from 'node:path';
import { once } from 'node:events';
import { WebSocketServer, type WebSocket } from 'ws';
import { createApp } from '../src/server/app.js';
import { config, incoming, selfMessage, FakeModel, until, deferred, analysisResult } from './helpers.js';
import { normalizeMessage } from '../src/server/messages.js';

test('本地接口完成配置、收信、草稿编辑、确认发送、重连且不公开令牌', async t => {
  const directory = await mkdtemp(join(tmpdir(), 'qq-reply-test-'));
  const server = new WebSocketServer({ host: '127.0.0.1', port: 0 }); await once(server, 'listening');
  let socket: WebSocket, sends = 0, lastParams: any;
  server.on('connection', client => {
    socket = client;
    client.send(JSON.stringify({ post_type: 'meta_event', meta_event_type: 'lifecycle' }));
    client.on('message', bytes => {
      const r = JSON.parse(bytes.toString());
      const data: Record<string, any> = {
        get_login_info: { user_id: 10001, nickname: '测试账号' },
        get_status: { online: true }, get_friend_msg_history: { messages: [] },
        get_friend_list: [{ user_id: 20002, nickname: '测试联系人' }],
      };
      if (r.action === 'send_private_msg') { sends++; lastParams = r.params; data.send_private_msg = { message_id: 700 + sends }; }
      client.send(JSON.stringify({ status: 'ok', retcode: 0, echo: r.echo, data: data[r.action] }));
    });
  });
  const service = await createApp({ dataDir: directory, model: new FakeModel(), autoConnect: false });
  t.after(async () => {
    await service.app.close(); for (const s of server.clients) s.terminate(); await new Promise<void>(resolve => server.close(() => resolve()));
    assert.equal(dirname(directory), tmpdir()); assert.ok(basename(directory).startsWith('qq-reply-test-')); await rm(directory, { recursive: true, force: true });
  });
  await service.app.ready();
  let response = await service.app.inject({ method: 'PATCH', url: '/api/config', payload: { ...config, paused: undefined, pauseReason: undefined, wsUrl: 'ws://127.0.0.1:' + (server.address() as { port: number }).port, debounceMs: 500 } });
  assert.equal(response.statusCode, 200, response.body);
  await service.app.inject({ method: 'POST', url: '/api/connect' });
  await until(() => service.client.state.online && service.client.state.accountId === '10001' && !service.engine.state.syncing);
  socket!.send(JSON.stringify(incoming(1, '测试前端到后端')));
  await until(() => service.store.drafts(config.accountId, config.contactId).length === 1);
  response = await service.app.inject('/api/state');
  const state = response.json(); assert.equal(state.config.token, undefined); assert.equal(state.config.tokenPresent, true);
  assert.equal(state.worker.workerRunning, true); assert.equal(state.connection.ready, true); assert.equal(sends, 0);
  const draft = state.drafts[0];
  response = await service.app.inject({ method: 'PATCH', url: '/api/drafts/' + draft.id, payload: { segments: [{ type: 'text', data: { text: '人工修改后的测试回复' } }, { type: 'face', data: { id: '14' } }] } });
  assert.equal(response.statusCode, 200, response.body);
  response = await service.app.inject({ method: 'POST', url: '/api/drafts/' + draft.id + '/send' });
  assert.equal(response.json().status, 'sent'); assert.equal(sends, 1); assert.equal(lastParams.user_id, '20002'); assert.equal(lastParams.message[1].data.id, '14');
  response = await service.app.inject({ method: 'POST', url: '/api/drafts/' + draft.id + '/send' });
  assert.equal(response.statusCode, 400); assert.equal(sends, 1);
  await service.app.inject({ method: 'POST', url: '/api/disconnect' }); await service.app.inject({ method: 'POST', url: '/api/connect' });
  await until(() => service.client.state.online && !service.engine.state.syncing);
  assert.equal(sends, 1); assert.equal(service.engine.state.workerRunning, true);
});

test('未配置时给出明确错误，非本机连接被拒绝，切换联系人清除旧画像绑定', async t => {
  const directory = await mkdtemp(join(tmpdir(), 'qq-reply-test-'));
  const service = await createApp({ dataDir: directory, model: new FakeModel(), autoConnect: false });
  t.after(async () => { await service.app.close(); assert.equal(dirname(directory), tmpdir()); assert.ok(basename(directory).startsWith('qq-reply-test-')); await rm(directory, { recursive: true, force: true }); });
  assert.equal((await service.app.inject({ method: 'POST', url: '/api/connect' })).statusCode, 400);
  assert.equal((await service.app.inject({ method: 'POST', url: '/api/drafts/generate' })).statusCode, 400);
  assert.equal((await service.app.inject({ method: 'POST', url: '/api/replays' })).statusCode, 400);
  const badUrl = await service.app.inject({ method: 'PATCH', url: '/api/config', payload: { wsUrl: 'ws://example.com:3001' } });
  assert.equal(badUrl.statusCode, 400);
  const foreign = await service.app.inject({ method: 'PATCH', url: '/api/config', headers: { origin: 'https://example.com' }, payload: { mode: 'auto' } });
  assert.equal(foreign.statusCode, 403); assert.equal(service.store.config().mode, 'confirm');
  const p = service.store.makeProfile();
  service.store.saveConfig({ ...config, profileId: p.id });
  await service.app.inject({ method: 'PATCH', url: '/api/config', payload: { contactId: '30003' } });
  assert.equal(service.store.config().profileId, '');
});

test('历史分析写入期间先暂停编辑，完成后保存的人工风格不会被覆盖', async t => {
  const directory = await mkdtemp(join(tmpdir(), 'qq-reply-test-'));
  const model = new FakeModel(), result = deferred<typeof analysisResult>();
  model.analyze = () => result.promise;
  const service = await createApp({ dataDir: directory, model, autoConnect: false });
  t.after(async () => {
    result.resolve(structuredClone(analysisResult)); await service.app.close();
    assert.equal(dirname(directory), tmpdir()); assert.ok(basename(directory).startsWith('qq-reply-test-')); await rm(directory, { recursive: true, force: true });
  });
  service.store.saveConfig(config);
  service.store.addMessage(normalizeMessage(selfMessage(1, '真实本人样本'), config, 'history')!);
  const job = (await service.app.inject({ method: 'POST', url: '/api/analysis' })).json();
  const blocked = await service.app.inject({ method: 'PATCH', url: '/api/profiles/' + job.profileId, payload: { style: '本人手工修改' } });
  assert.equal(blocked.statusCode, 400); assert.match(blocked.json().error, /仍在整理/);
  result.resolve(structuredClone(analysisResult)); await until(() => !service.analysis.state.running);
  const saved = await service.app.inject({ method: 'PATCH', url: '/api/profiles/' + job.profileId, payload: { style: '本人手工修改' } });
  assert.equal(saved.statusCode, 200); assert.equal(service.store.profile(job.profileId)!.style, '本人手工修改');
});
