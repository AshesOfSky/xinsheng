// Local UI acceptance fixture. Uses a simulated OneBot server and synthetic messages.
// It is not imported by the production server.
import { once } from 'node:events';
import { resolve } from 'node:path';
import { WebSocketServer } from 'ws';
import { createApp } from '../src/server/app.js';
import { config, incoming, selfMessage, FakeModel } from './helpers.js';
const server = new WebSocketServer({ host: '127.0.0.1', port: 0 }); await once(server, 'listening');
const fake = new FakeModel();
const service = await createApp({ dataDir: resolve('test-results/ui-preview'), model: fake, autoConnect: false });
service.store.saveConfig({ ...config, mode: 'confirm', paused: false, contactName: '本地验证用联系人', debounceMs: 500, wsUrl: 'ws://127.0.0.1:' + (server.address() as { port: number }).port });
server.on('connection', socket => {
  socket.send(JSON.stringify({ post_type: 'meta_event', meta_event_type: 'lifecycle' }));
  socket.on('message', bytes => {
    const r = JSON.parse(bytes.toString());
    const results: Record<string, unknown> = { get_login_info: { user_id: 10001, nickname: '本地测试' }, get_status: { online: true }, get_friend_msg_history: { messages: [] }, get_friend_list: [{ user_id: 20002, nickname: '本地验证用联系人' }] };
    if (r.action === 'send_private_msg') {
      const id = Date.now(); socket.send(JSON.stringify({ ...selfMessage(id), message: r.params.message }));
      results[r.action] = { message_id: id };
    }
    socket.send(JSON.stringify({ status: 'ok', retcode: 0, echo: r.echo, data: results[r.action] }));
  });
});
service.app.post('/fixture/incoming', async req => {
  const body = req.body as { text?: string; self?: boolean };
  for (const socket of server.clients) socket.send(JSON.stringify(body.self ? selfMessage(Date.now(), body.text) : incoming(Date.now(), body.text)));
  return { fixtureOnly: true };
});
service.app.addHook('onClose', async () => { for (const socket of server.clients) socket.terminate(); await new Promise<void>(resolve => server.close(() => resolve())); });
await service.app.listen({ host: '127.0.0.1', port: 23211 });
service.client.start(service.store.config().wsUrl, config.token);
console.log('Synthetic UI acceptance preview: http://127.0.0.1:23211');
for (const signal of ['SIGINT', 'SIGTERM'] as const) process.once(signal, () => void service.app.close());
