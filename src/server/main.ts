import { resolve } from 'node:path';
import { mkdir, writeFile, unlink } from 'node:fs/promises';
import { createApp } from './app.js';
const dataDir = resolve(process.env.QQ_REPLY_DATA_DIR ?? 'data');
const port = Number(process.env.QQ_REPLY_PORT ?? 23210);
const { app } = await createApp({ dataDir, logger: true });
await mkdir(dataDir, { recursive: true });
app.addHook('onClose', async () => { await unlink(resolve(dataDir, 'server.pid')).catch(() => {}); });
try {
  await app.listen({ host: '127.0.0.1', port });
  await writeFile(resolve(dataDir, 'server.pid'), String(process.pid), 'utf8');
  console.log('心声已启动：http://127.0.0.1:' + port);
} catch (e) { console.error(e); process.exitCode = 1; await app.close(); }
for (const signal of ['SIGINT', 'SIGTERM'] as const) process.once(signal, () => void app.close());
