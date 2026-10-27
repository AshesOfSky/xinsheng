"""Daily local host: WeChat database -> AGY -> WeChat UI, with local memory."""
from __future__ import annotations

import argparse
from datetime import datetime
import json
import logging
import msvcrt
import os
from pathlib import Path
import queue
import threading
import time

from agy_client import AgySession, GenerationSuperseded, FAST_REPLY_SCHEMA, RESEARCH_REPLY_SCHEMA, MEMORY_SCHEMA, MODEL, CHAT_MODEL, ROOT
from storage import Archive
from wechat_bridge import WeChatBridge
from context_text import for_model
from conversation import ConversationInbox
from chat_prompt import reply_payload, compact_message
from reply_router import generate_reply, researched_reply
from native_mentions import outgoing_matches
from native_stickers import media_matches
from stickers import StickerLibrary, sticker_id
from classify_stickers import classify, SCHEMA as STICKER_SCHEMA
from app_paths import initialize_workspace
from private_host import private_loop


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')


class Bot:
    def __init__(self, config, join_now=False):
        self.config = config
        self.data = ROOT / 'data'
        self.archive = Archive(self.data)
        self.bridge = WeChatBridge(config, ROOT)
        self.archive.member_names(self.bridge.members)
        self.stop = threading.Event()
        self.jobs = ConversationInbox()
        self.research_jobs = queue.Queue()
        self.research_pending = {}
        self.research_lock = threading.Lock()
        self.stickers = StickerLibrary(self.data / 'stickers')
        self.sticker_jobs = queue.PriorityQueue()
        self.favorite_jobs = queue.Queue()
        self.sticker_model = AgySession(schema=STICKER_SCHEMA, label='stickers', timeout=120, agent='wechat-sticker-reader')
        self.reply_model = AgySession(schema=FAST_REPLY_SCHEMA, label='reply-fast', model=CHAT_MODEL)
        self.deep_model = AgySession(label='reply-deep', agent='wechat-researcher',
                                     schema=RESEARCH_REPLY_SCHEMA, timeout=180)
        self.memory_model = AgySession(schema=MEMORY_SCHEMA, label='memory', timeout=180)
        self.last_send = 0.0
        self.send_lock = threading.Lock()
        self.private_sessions = {}
        self.private_sessions_lock = threading.Lock()
        self.status_lock = threading.Lock()
        self.pending_lock = threading.RLock()
        self.pending_sends = {}
        self.status = {'pid': os.getpid(), 'model': CHAT_MODEL, 'reasoning_model': MODEL,
                       'memory_model': MODEL, 'group': config['group_name'],
                       'started': datetime.now().isoformat(timespec='seconds'), 'state': 'starting'}
        self.seq = 0
        self.join_now = join_now
        self.last_idle_trigger = 0

    def update_status(self, **values):
        with self.status_lock:
            self.status.update(values)
            self.status['updated'] = datetime.now().isoformat(timespec='seconds')
            write_json(self.data / 'status.json', self.status)

    def seed(self, queue_recent=False):
        was_initialized = bool(self.archive.recent(1))
        rows = self.bridge.history()
        for row in rows:
            msg = self.archive.add(self.bridge.normalize(row))
            if (queue_recent and was_initialized and msg and not msg['is_self']
                    and msg['kind'] == '文本'
                    and time.time() - msg['created'] <= (600 if msg['is_at'] else 45)):
                self.jobs.put(dict(msg, _received_at=time.monotonic()))
        self.seq = max((r['sort_seq'] for r in rows), default=0)
        self.archive.export_notes()
        self.update_status(history_rows=len(rows), members=len(self.bridge.members))

    def summarize(self):
        messages = self.archive.unsummarized()
        if not messages:
            return False
        member_ids = [m['member_id'] for m in messages if not m['is_self']]
        result = self.memory_model.ask({
            'task': 'memory',
            'instruction': '归纳这一批讨论的话题、重要信息和未解决问题。对有新证据的成员，'
                           '补充简短的自述事实、兴趣、表达习惯、适合的回应方式。'
                           '不要重复已有观察；临时玩笑不能上升成稳定性格。'
                           '每条新观察的 evidence_ids 必须来自该成员本批真实发言。'
                           '只归纳已提供的文字，媒体占位不能当作看过图片或文件。'
                           '市场消息与个人经历写为该成员的说法，不作为已核验的外部事实。'
                           '单次发言只描述本次表达，不下稳定能力或性格结论。'
                           '无新材料的成员不要更新；机器人自己的发言不能作为人物侧写证据。',
            'previous_profiles': self.archive.profiles(member_ids),
            'messages': [for_model(m, self.stickers) for m in messages],
        })
        if self.stop.is_set():
            return False
        self.archive.save_memory(messages, result)
        self.update_status(last_summary_id=messages[-1]['id'], memory_error=None)
        logging.info('summary saved through message #%s', messages[-1]['id'])
        return True

    def memory_loop(self):
        # Warm the slower reasoning session off the live-chat path.
        try:
            self.deep_model.ask({'task': 'warmup',
                'persona': (ROOT / 'PERSONA.md').read_text(encoding='utf-8').partition('原创口吻示例')[0],
                'instruction': '准备处理群里的认真问题；本轮不需联网，只回复 {"reply":false,"text":"","sources":[]}。'})
            self.update_status(reasoning_ready=True)
        except Exception as exc:
            if self.stop.is_set():
                return
            logging.exception('reasoning warmup failed')
            self.update_status(reasoning_error=str(exc))
        # Summarize in batches as messages accumulate, or every ten minutes.
        last = 0.0
        while not self.stop.is_set():
            pending = self.archive.unsummarized()
            now = time.monotonic()
            if pending and (len(pending) >= 50 or now - last >= 600):
                if self.jobs.size() or self.status.get('reply_state') == 'generating':
                    self.stop.wait(2)
                    continue
                try:
                    self.summarize()
                except Exception as exc:
                    if self.stop.is_set():
                        break
                    logging.exception('memory generation failed')
                    self.update_status(memory_error=str(exc))
                    self.stop.wait(60)
                last = time.monotonic()
            self.stop.wait(2)

    def reply_loop(self):
        import pythoncom
        pythoncom.CoInitialize()
        try:
            try:
                self.bridge.prepare_sender()
                self.reply_model.ask({
                    'task': 'warmup',
                    'persona': (ROOT / 'PERSONA.md').read_text(encoding='utf-8').partition('原创口吻示例')[0],
                    'instruction': '准备接收实时群聊。现在只回复 {"reply":false,"text":"","needs_high":false,"needs_web":false,"research_message_ids":[],"sticker_code":""}。',
                })
                self.update_status(reply_ready=True)
            except Exception as exc:
                if self.stop.is_set():
                    return
                logging.exception('reply warmup failed')
                self.update_status(reply_error=str(exc))
            while not self.stop.is_set():
                batch = self.jobs.take(self.stop, self.last_send + self.config['ordinary_reply_seconds'], timeout=.5)
                if not batch:
                    if not self.stop.is_set():
                        self.collect_favorite()
                    continue
                completed = [m for m in batch if m.get('_completed_research')]
                if completed:
                    msg = completed[0]
                    for other in batch:
                        if other is not msg:
                            self.jobs.put(other)
                    batch = [msg]
                msg = batch[-1]
                try:
                    required = any(self.jobs.required(m) for m in batch)
                    if not required and time.time() - msg['created'] > 45 and not msg.get('idle_followup'):
                        continue
                    if completed:
                        result = msg['_completed_research']
                        used_model = MODEL
                        generation_seconds = msg['_research_seconds']
                        generation_started = msg['_generation_started']
                        snapshot_id = self.archive.recent(1)[0]['id']
                    else:
                        payload, snapshot_id = reply_payload(self.archive, batch,
                            (ROOT / 'PERSONA.md').read_text(encoding='utf-8'), self.stickers)
                        with self.research_lock:
                            payload['background_questions'] = [compact_message(m) for m in self.research_pending.values()]
                        generation_started = time.monotonic()
                        self.update_status(reply_state='generating', batch_messages=len(batch))
                        try:
                            result, used_model, generation_seconds = generate_reply(self.reply_model, self.deep_model, payload,
                                cancel=(lambda: self.stop.is_set() or self.jobs.has_mention()) if not required
                                       else self.stop.is_set,
                                defer=lambda request, web, elapsed: self.defer_research(
                                    request, web, elapsed, batch, generation_started))
                        except GenerationSuperseded:
                            raise
                        except Exception:
                            # A failed generation has not sent anything; keep required @ for retry.
                            self.jobs.restore_required(batch)
                            if required:
                                self.stop.wait(2)
                            raise
                        if result.get('deferred'):
                            self.update_status(reply_state='ready', last_decision='认真问题已转后台，继续接话')
                            continue
                    if self.stop.is_set():
                        continue
                    if not completed and self.jobs.has_newer(snapshot_id):
                        # The group has moved on while the model was thinking.
                        # Only unanswered @ are retained; ordinary chatter is merged.
                        self.jobs.restore_required(batch)
                        self.update_status(reply_state='refreshing', last_decision='话题更新，旧草稿未发送')
                        logging.info('discarded stale draft through #%s', snapshot_id)
                        continue
                    if type(result.get('reply')) is not bool or not isinstance(result.get('text'), str):
                        raise ValueError('模型回复格式不正确，未发送')
                    text = result['text'].strip()
                    selected = None
                    code = result.get('sticker_code', '') if not completed else ''
                    if code:
                        selected = self.stickers.select(code, payload['sticker_choices'])
                    if required and not text and not selected:
                        text = '大肥鱼在呢，这句我还没捋明白，你再展开说说？'
                    if not required and not result.get('reply'):
                        self.update_status(reply_state='ready', last_decision='保持安静', reply_error=None)
                        continue
                    if not text and not selected:
                        continue
                    with self.send_lock:
                        if self.stop.is_set():
                            continue
                        if not completed and self.jobs.has_newer(snapshot_id):
                            self.jobs.restore_required(batch)
                            continue
                        if not text:
                            self.send_sticker(msg, selected, snapshot_id, generation_seconds, used_model)
                            continue
                        outbox_id = self.archive.record_outgoing(msg['id'], text, 'sending')
                        mention_ids = msg.get('_research_member_ids', [])
                        with self.pending_lock:
                            self.pending_sends[outbox_id] = {
                                'text': text, 'after_id': snapshot_id, 'trigger_id': msg['id'],
                                'trigger_created': msg['created'], 'started': time.monotonic(),
                                'model_seconds': generation_seconds, 'model': used_model,
                                'queue_seconds': round(generation_started - msg.get('_received_at', generation_started), 3),
                                'mention_ids': mention_ids,
                            }
                        try:
                            ok, detail = self.bridge.send(text, mention_ids=mention_ids) if mention_ids else self.bridge.send(text)
                        except Exception as exc:
                            ok, detail = False, str(exc)
                        self.last_send = time.monotonic()
                        with self.pending_lock:
                            pending = self.pending_sends.get(outbox_id)
                            if pending:
                                pending.update(self.bridge.last_send_timing)
                                pending['send_detail'] = detail
                                self.archive.outgoing_status(outbox_id, 'submitted' if ok else 'unconfirmed')
                            self.update_status(reply_state='ready',
                                last_decision=('已提交，等待回读' if ok else '发送待核') if pending else '已回复并确认',
                                last_trigger_id=msg['id'], last_reply=text,
                                reply_seconds=generation_seconds, last_reply_model=used_model,
                                reply_error=None if ok else detail)
                        if selected and ok:
                            self.send_sticker(msg, selected, snapshot_id, generation_seconds, used_model)
                    logging.info('reply trigger=%s submitted=%s ui=%s detail=%s',
                                 msg['id'], ok, self.bridge.last_send_timing, detail if not ok else '')
                except GenerationSuperseded:
                    self.jobs.restore_required(batch)
                    self.update_status(reply_state='refreshing', last_decision='新 @ 优先，普通草稿取消')
                except Exception as exc:
                    if self.stop.is_set():
                        break
                    logging.exception('reply failed for message #%s', msg['id'])
                    self.update_status(reply_error=str(exc))
        finally:
            pythoncom.CoUninitialize()

    def send_sticker(self, message, entry, after_id, seconds, model):
        outbox_id = self.archive.record_outgoing(message['id'], f'[表情:{entry["code"]}]', 'sending')
        with self.pending_lock:
            self.pending_sends[outbox_id] = {'sticker_id':entry['id'],'sticker_code':entry['code'],
                'wechat_id':entry.get('wechat_id'),
                'image_format':Path(entry['path']).suffix.lower(),'after_id':after_id,
                'trigger_id':message['id'],'trigger_created':message['created'],'started':time.monotonic(),
                'model_seconds':seconds,'model':model}
        try:
            ok, detail = self.bridge.send_sticker(entry)
        except Exception as exc:
            ok, detail = False, str(exc)
        self.last_send = time.monotonic()
        with self.pending_lock:
            pending = self.pending_sends.get(outbox_id)
            if pending:
                pending.update(self.bridge.last_send_timing, send_detail=detail)
                self.archive.outgoing_status(outbox_id, 'submitted' if ok else 'unconfirmed')
        if ok:
            self.stickers.update(entry['id'], last_used=time.time())
        self.update_status(reply_state='ready',last_sticker=entry['code'],
            sticker_send_error=None if ok else detail, last_reply_model=model)
        logging.info('sticker submitted code=%s ok=%s timing=%s',entry['code'],ok,self.bridge.last_send_timing)

    def sticker_loop(self):
        while not self.stop.is_set():
            try:
                _, message = self.sticker_jobs.get(timeout=.5)
            except queue.Empty:
                continue
            try:
                entry = self.stickers.collect(message)
                if self.stop.is_set():
                    break
                if entry and not entry.get('added_to_wechat'):
                    self.favorite_jobs.put((entry,message))
                if entry and not entry.get('understanding'):
                    self.update_status(sticker_state='learning', sticker_learning=entry['code'])
                    result = classify(entry,self.sticker_model,cancel=self.stop.is_set)
                    self.stickers.update(entry['id'],**result,classification_error=None)
                    self.stickers.export_index()
                    if (time.time()-message['created']<=45 and
                            self.archive.recent(1)[0]['id']==message['id']):
                        self.jobs.put(dict(message,_received_at=time.monotonic()))
                entries = self.stickers.entries()
                self.update_status(sticker_state='ready',sticker_error=None,
                    stickers_total=len(entries),stickers_understood=sum(e.get('understanding')=='clear' for e in entries),
                    stickers_codex=sum(e.get('classified_by')=='codex' for e in entries))
            except Exception as exc:
                if self.stop.is_set():
                    break
                logging.exception('sticker learning failed message=%s',message['id'])
                self.update_status(sticker_error=f'消息 {message["id"]}: {type(exc).__name__}')
            finally:
                self.sticker_jobs.task_done()

    def collect_favorite(self):
        try:
            entry,message = self.favorite_jobs.get_nowait()
        except queue.Empty:
            return
        try:
            with self.send_lock:
                identity = self.bridge.add_latest_sticker(entry,message)
            if identity:
                self.stickers.update(entry['id'],added_to_wechat=True,wechat_id=identity)
                self.update_status(last_sticker_favorited=entry['code'],sticker_favorite_error=None)
        except Exception as exc:
            logging.exception('sticker favorite failed code=%s',entry['code'])
            self.update_status(sticker_favorite_error=type(exc).__name__)
        finally:
            self.favorite_jobs.task_done()

    def defer_research(self, request, web, elapsed, batch, generation_started):
        ids = {m['id'] for m in request['research_questions']}
        targets = [m for m in batch if m['id'] in ids]
        with self.research_lock:
            self.research_pending.update((m['id'], m) for m in targets)
        self.research_jobs.put({'request': request, 'web': web, 'elapsed': elapsed,
            'targets': targets, 'generation_started': generation_started})

    def research_loop(self):
        while not self.stop.is_set():
            try:
                job = self.research_jobs.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                self.update_status(research_state='searching' if job['web'] else 'thinking')
                result = researched_reply(self.deep_model, job['request'], job['web'], cancel=self.stop.is_set)
                seconds = job['elapsed'] + self.deep_model.last_result.get('seconds', 0)
                self.update_status(research_error=None,
                    last_research_tools=[s['tool'] for s in self.deep_model.last_result.get('steps', []) if s.get('tool')])
            except Exception as exc:
                if self.stop.is_set():
                    break
                logging.exception('background research failed')
                result = {'reply': True, 'text': '刚才那次查证没有完成，大肥鱼先不拿旧消息乱报结论。'}
                seconds = time.monotonic() - job['generation_started']
                self.update_status(research_error=str(exc))
            if not self.stop.is_set():
                target = job['targets'][-1]
                if result.get('text'):
                    result['text'] = '回你刚才的问题：' + result['text']
                self.jobs.put(dict(target, force_reply=True, _completed_research=result,
                    _research_member_ids=list(dict.fromkeys(m['member_id'] for m in job['targets'])),
                    _research_seconds=round(seconds, 3), _generation_started=job['generation_started']))
                self.update_status(research_state='ready')
            with self.research_lock:
                for target in job['targets']:
                    self.research_pending.pop(target['id'], None)
            self.research_jobs.task_done()

    def confirm_sends(self, message=None):
        with self.pending_lock:
            for outbox_id, pending in list(self.pending_sends.items()):
                matched = message and (media_matches(message,pending) if pending.get('sticker_id')
                                       else outgoing_matches(message,pending))
                if matched:
                    self.archive.outgoing_status(outbox_id, 'confirmed')
                    self.pending_sends.pop(outbox_id)
                    latency = {k: pending[k] for k in ('queue_seconds', 'model_seconds', 'ui_seconds') if k in pending}
                    latency['message_to_sent_seconds'] = message['created'] - pending['trigger_created']
                    self.update_status(last_decision='已回复并确认', reply_error=None, last_latency=latency)
                    if pending.get('sticker_id'):
                        self.update_status(last_sticker_confirmed=pending['sticker_code'],sticker_send_error=None)
                        entry = self.stickers.lookup(pending['sticker_code'])
                        if not entry.get('added_to_wechat'):
                            self.favorite_jobs.put((entry,message))
                    logging.info('reply confirmed trigger=%s latency=%s', pending['trigger_id'], latency)
                elif time.monotonic() - pending['started'] > 20:
                    self.archive.outgoing_status(outbox_id, 'unconfirmed')
                    self.pending_sends.pop(outbox_id)
                    self.update_status(reply_error='20 秒内未回读确认；不重复发送。' + pending.get('send_detail', ''))

    def run(self):
        self.seed(queue_recent=True)
        for message in self.archive.sticker_messages():
            self.sticker_jobs.put((-message['id'],message))
        if self.join_now:
            latest = next((m for m in reversed(self.archive.recent(30))
                           if not m['is_self'] and m['kind'] == '文本'), None)
            if latest and time.time() - latest['created'] < 1800:
                self.jobs.put(dict(latest, force_reply=True, _received_at=time.monotonic()))
        reply_thread = threading.Thread(target=self.reply_loop, daemon=True)
        memory_thread = threading.Thread(target=self.memory_loop, daemon=True)
        research_thread = threading.Thread(target=self.research_loop, daemon=True)
        sticker_thread = threading.Thread(target=self.sticker_loop, daemon=True)
        private_threads = [threading.Thread(target=private_loop, args=(self, contact), daemon=True)
                           for contact in self.config.get('private_contacts', [])]
        reply_thread.start()
        memory_thread.start()
        research_thread.start()
        sticker_thread.start()
        for thread in private_threads:
            thread.start()
        self.update_status(state='running')
        logging.info('ready; chat=%s reasoning=%s memory=%s', CHAT_MODEL, MODEL, MODEL)
        try:
            while not self.stop.is_set():
                if (ROOT / '.runtime' / 'stop').exists():
                    break
                started = time.monotonic()
                rows = self.bridge.newer(self.seq)
                for row in rows:
                    self.seq = max(self.seq, row['sort_seq'])
                    msg = self.archive.add(self.bridge.normalize(row))
                    if msg and not msg['is_self'] and msg['kind'] == '文本':
                        self.jobs.put(dict(msg, _received_at=time.monotonic()))
                    if msg and not msg['is_self'] and msg['kind'] == '动画表情':
                        self.sticker_jobs.put((-msg['id'],msg))
                        if '尚未看清' not in self.stickers.describe(msg):
                            self.jobs.put(dict(msg,_received_at=time.monotonic()))
                    if msg and msg['is_self']:
                        self.confirm_sends(msg)
                self.confirm_sends()
                recent = self.archive.recent(1)
                if recent:
                    last = recent[0]
                    idle = time.time() - last['created']
                    if (not last['is_self'] and last['kind'] == '文本' and
                            300 <= idle < 1800 and last['id'] != self.last_idle_trigger):
                        self.last_idle_trigger = last['id']
                        self.jobs.put(dict(last, idle_followup=True, _received_at=time.monotonic()))
                self.update_status(last_poll_ms=round((time.monotonic() - started) * 1000, 1),
                                   queue_size=self.jobs.size())
                self.stop.wait(self.config['poll_seconds'])
        finally:
            self.stop.set()
            self.reply_model.close()
            self.deep_model.close()
            self.memory_model.close()
            self.sticker_model.close()
            with self.private_sessions_lock:
                sessions = list(self.private_sessions.values())
            for session in sessions:
                session.fast.close()
                session.high.close()
            reply_thread.join(timeout=8)
            memory_thread.join(timeout=8)
            research_thread.join(timeout=8)
            sticker_thread.join(timeout=8)
            for thread in private_threads:
                thread.join(timeout=8)
            self.archive.export_notes()
            self.update_status(state='stopped')


def load_config():
    path = ROOT / 'data' / 'config.json'
    if not path.exists():
        if not (ROOT / 'data' / 'database-check.json').exists():
            raise RuntimeError('尚未选择微信账号和群聊，请在桌面客户端的连接设置中保存配置。')
        snapshot = json.loads((ROOT / 'data' / 'database-check.json').read_text(encoding='utf-8'))
        write_json(path, {
            'account': snapshot['account'], 'group_id': snapshot['group']['username'],
            'group_name': snapshot['group']['nick_name'],
            'poll_seconds': 0.35, 'ordinary_reply_seconds': 4,
        })
    return json.loads(path.read_text(encoding='utf-8'))


def main():
    initialize_workspace()
    parser = argparse.ArgumentParser()
    parser.add_argument('--prepare', action='store_true', help='Archive and summarize without replying')
    parser.add_argument('--join-now', action='store_true', help='Join the current discussion once')
    args = parser.parse_args()
    runtime = ROOT / '.runtime'
    runtime.mkdir(exist_ok=True)
    lock = (runtime / 'host.lock').open('a+b')
    lock.seek(0)
    try:
        msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        raise SystemExit('大肥鱼已运行，无需重复启动。')
    (runtime / 'stop').unlink(missing_ok=True)
    logging.basicConfig(filename=runtime / 'host.log', encoding='utf-8', level=logging.INFO, force=True,
                        format='%(asctime)s %(levelname)s %(message)s')
    bot = None
    try:
        bot = Bot(load_config(), join_now=args.join_now)
        if args.prepare:
            bot.seed()
            while bot.summarize():
                pass
            bot.update_status(state='prepared')
        else:
            bot.run()
    except Exception as exc:
        logging.exception('host failed')
        if bot:
            bot.update_status(state='error', error=str(exc))
        raise
    finally:
        if bot:
            bot.stop.set()
            bot.reply_model.close()
            bot.deep_model.close()
            bot.memory_model.close()
            bot.sticker_model.close()
        lock.close()


if __name__ == '__main__':
    main()
