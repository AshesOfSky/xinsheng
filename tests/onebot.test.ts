import { test } from 'node:test';
import assert from 'node:assert/strict';
import { once } from 'node:events';
import { WebSocketServer } from 'ws';
import { OneBotClient, ActionError } from '../src/server/onebot.js';

test('同一连接的事件与乱序 API 响应按 echo 正确分流', async t => {
  const server = new WebSocketServer({ host: '127.0.0.1', port: 0 });
  await once(server, 'listening');
  const client = new OneBotClient(); const events: string[] = [], requests: any[] = [];
  client.on('event', event => events.push(event.post_type));
  server.on('connection', (socket, req) => {
    assert.equal(req.headers.authorization, 'Bearer test-token');
    socket.send(JSON.stringify({ post_type: 'meta_event', meta_event_type: 'lifecycle' }));
    socket.send(JSON.stringify({ post_type: 'meta_event', meta_event_type: 'heartbeat', status: { online: true } }));
    socket.on('message', bytes => {
      const r = JSON.parse(bytes.toString()); requests.push(r);
      if (requests.length === 2) {
        socket.send(JSON.stringify({ post_type: 'message', user_id: 20002 }));
        for (const item of [...requests].reverse()) socket.send(JSON.stringify({ status: 'ok', retcode: 0, echo: item.echo, data: { answer: item.action } }));
      }
    });
  });
  t.after(async () => { client.stop(); for (const s of server.clients) s.terminate(); await new Promise<void>(resolve => server.close(() => resolve())); });
  const connected = once(client, 'connected');
  client.start('ws://127.0.0.1:' + (server.address() as { port: number }).port, 'test-token'); await connected;
  const [a, b] = await Promise.all([client.call<{ answer: string }>('first'), client.call<{ answer: string }>('second')]);
  assert.equal(a.answer, 'first'); assert.equal(b.answer, 'second');
  assert.equal(new Set(requests.map(r => r.echo)).size, 2);
  assert.deepEqual(events, ['meta_event', 'meta_event', 'message']); assert.equal(client.state.online, true);
});
test('明确 API 失败与无响应超时分开记录', async t => {
  const server = new WebSocketServer({ host: '127.0.0.1', port: 0 }); await once(server, 'listening');
  server.on('connection', socket => socket.on('message', bytes => {
    const r = JSON.parse(bytes.toString());
    if (r.action === 'fail') socket.send(JSON.stringify({ status: 'failed', retcode: 1200, echo: r.echo, message: '测试失败' }));
  }));
  const client = new OneBotClient();
  t.after(async () => { client.stop(); for (const s of server.clients) s.terminate(); await new Promise<void>(resolve => server.close(() => resolve())); });
  const connected = once(client, 'connected'); client.start('ws://127.0.0.1:' + (server.address() as { port: number }).port, 'test-token'); await connected;
  await assert.rejects(client.call('fail'), e => e instanceof ActionError && e.uncertain === false && e.retcode === 1200);
  await assert.rejects(client.call('timeout', {}, 30), e => e instanceof ActionError && e.uncertain === true);
});
