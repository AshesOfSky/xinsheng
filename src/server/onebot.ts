import { EventEmitter } from 'node:events';
import { randomUUID } from 'node:crypto';
import WebSocket from 'ws';
import type { Transport } from '../shared/types.js';

export class ActionError extends Error {
  constructor(message: string, public uncertain = false, public retcode?: number) { super(message); this.name = 'ActionError'; }
}
export class OneBotClient extends EventEmitter implements Transport {
  state = { connected: false, online: false, accountId: '', lastHeartbeat: 0, lastError: '', connectedAt: 0 };
  private socket?: WebSocket;
  private retry?: NodeJS.Timeout;
  private stopped = true;
  private url = '';
  private token = '';
  private pending = new Map<string, { resolve: (value: any) => void; reject: (error: Error) => void; timer: NodeJS.Timeout }>();
  start(url: string, token: string) { this.stop(); this.url = url; this.token = token; this.stopped = false; this.connect(); }
  private connect() {
    if (this.stopped) return;
    const socket = new WebSocket(this.url, { headers: { Authorization: 'Bearer ' + this.token }, handshakeTimeout: 10000 });
    this.socket = socket;
    socket.on('open', () => {
      if (this.socket !== socket || this.stopped) return;
      this.state = { ...this.state, connected: true, online: false, lastError: '', connectedAt: Date.now() };
      this.emit('connected'); this.emit('state');
    });
    socket.on('message', bytes => {
      if (this.socket !== socket) return;
      let frame: Record<string, any>;
      try { frame = JSON.parse(bytes.toString()); } catch { this.state.lastError = '收到无法解析的 OneBot 数据'; this.emit('state'); return; }
      if (frame.post_type) {
        if (frame.post_type === 'meta_event' && frame.meta_event_type === 'heartbeat') {
          this.state.lastHeartbeat = Date.now();
          if (typeof frame.status?.online === 'boolean') this.state.online = frame.status.online;
          this.emit('state');
        }
        this.emit('event', frame);
        return;
      }
      const request = this.pending.get(String(frame.echo));
      if (!request) return;
      // Streaming action chunks are not the final result. This app only calls ordinary actions.
      if (frame.stream === 'stream') return;
      this.pending.delete(String(frame.echo)); clearTimeout(request.timer);
      if (frame.status === 'ok' && frame.retcode === 0) request.resolve(frame.data);
      else request.reject(new ActionError(String(frame.message || frame.wording || 'QQ 接口调用失败（' + frame.retcode + '）'), frame.status === 'async' || frame.retcode === 1, frame.retcode));
    });
    socket.on('error', error => { this.state.lastError = error.message; this.emit('state'); });
    socket.on('close', () => {
      if (this.socket !== socket) return;
      this.state.connected = false; this.state.online = false;
      this.rejectPending('QQ 连接中断，未收到调用结果');
      this.emit('disconnected'); this.emit('state');
      if (!this.stopped) this.retry = setTimeout(() => this.connect(), 5000);
    });
  }
  async identify() {
    const [identity, status] = await Promise.all([
      this.call<{ user_id: number | string; nickname: string }>('get_login_info'),
      this.call<{ online: boolean; good?: boolean }>('get_status'),
    ]);
    this.state.accountId = String(identity.user_id); this.state.online = status.online === true;
    this.emit('state'); return identity;
  }
  call<T = unknown>(action: string, params: Record<string, unknown> = {}, timeoutMs = 15000): Promise<T> {
    if (!this.socket || this.socket.readyState !== WebSocket.OPEN) return Promise.reject(new ActionError('QQ 尚未连接'));
    const echo = randomUUID();
    return new Promise<T>((resolve, reject) => {
      const timer = setTimeout(() => { this.pending.delete(echo); reject(new ActionError('QQ 接口等待超时，结果待核', true)); }, timeoutMs);
      this.pending.set(echo, { resolve, reject, timer });
      this.socket!.send(JSON.stringify({ action, params, echo }), error => {
        if (error && this.pending.has(echo)) { clearTimeout(timer); this.pending.delete(echo); reject(new ActionError(error.message, true)); }
      });
    });
  }
  private rejectPending(reason: string) {
    for (const req of this.pending.values()) { clearTimeout(req.timer); req.reject(new ActionError(reason, true)); }
    this.pending.clear();
  }
  stop() {
    this.stopped = true; clearTimeout(this.retry); this.retry = undefined;
    const socket = this.socket; this.socket = undefined;
    socket?.removeAllListeners(); socket?.on('error', () => {}); socket?.terminate();
    this.rejectPending('连接已停止，未完成的发送结果待核');
    this.state.connected = false; this.state.online = false;
    this.emit('disconnected'); this.emit('state');
  }
}
