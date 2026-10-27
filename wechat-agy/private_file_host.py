"""Queue Office work for one explicitly marked private contact."""
import json
from pathlib import Path
import re
import time
import threading
import xml.etree.ElementTree as ET

from private_file_worker import run_file_task
from political_guard import PoliticalGuard


FILE_REQUEST = re.compile(
    r'表格|文档|文件|附件|excel|xlsx|word|docx|'
    r'表头|加粗|居中|标红|字体|行高|列宽|单元格|'
    r'(?:检查|核对|审查|审阅|审核|查一下|看一下).{0,20}(?:表|文件|文档|附件|数据|公式|内容)|'
    r'(?:发|传|回传)(?:回|给)(?:我|来)|'
    r'(?:帮|请|麻烦|能否|可以).{0,20}(?:处理|修改|整理|制作|计算|统计|填|做)|'
    r'(?:处理|修改|整理|制作|计算|统计|填|做).{0,12}(?:一下|这个|那份)', re.I | re.S)
FILE_FOLLOWUP = re.compile(r'再加|再改|加一|改成|换成|取消|不用|别做|等一下|等等|错了', re.I)


def office_attachment(message):
    if message['kind'] != '文件/链接/卡片':
        return False
    try:
        root = ET.fromstring(message['content'])
        app = root if root.tag == 'appmsg' else root.find('.//appmsg')
        return app is not None and app.findtext('type') == '6' and \
            Path(app.findtext('title') or '').suffix.lower() in ('.xlsx', '.docx')
    except (ET.ParseError, TypeError):
        return False


def file_row(contact_id, message):
    prefix = contact_id + '/'
    source = message['source_key']
    if not source.startswith(prefix):
        raise ValueError('附件不属于当前联系人')
    shard, local_id = source[len(prefix):].rsplit('/', 1)
    return {'sender_username': message['member_id'], 'shard': shard,
            'local_id': int(local_id), 'content': message['content'],
            'create_time': message['created']}


def _store(chat):
    with chat.archive.lock, chat.archive.db:
        chat.archive.db.execute('''CREATE TABLE IF NOT EXISTS private_file_tasks (
            attachment_id INTEGER PRIMARY KEY, request_ids TEXT NOT NULL,
            result_path TEXT NOT NULL, status TEXT NOT NULL)''')


def _get(chat, attachment_id):
    with chat.archive.lock:
        row = chat.archive.db.execute('SELECT * FROM private_file_tasks WHERE attachment_id=?',
                                      (attachment_id,)).fetchone()
    return dict(row) if row else None


def _put(chat, attachment_id, request_ids, path, status):
    with chat.archive.lock, chat.archive.db:
        chat.archive.db.execute('''INSERT INTO private_file_tasks VALUES (?, ?, ?, ?)
            ON CONFLICT(attachment_id) DO UPDATE SET request_ids=excluded.request_ids,
            result_path=excluded.result_path, status=excluded.status''',
            (attachment_id, json.dumps(request_ids), str(path), status))


class FileCoordinator:
    """Run document work off the chat loop and submit only a current result."""

    def __init__(self, bot, chat, contact):
        self.bot, self.chat, self.contact = bot, chat, contact
        self.job = None
        self.retry_at = 0.0
        _store(chat)

    def _start(self, attachment, instructions, latest_id):
        job = {'attachment': attachment, 'instructions': instructions,
               'latest_id': latest_id, 'done': threading.Event(), 'result': None, 'error': None}
        self.job = job

        def work():
            guard = None
            try:
                guard = PoliticalGuard(label=f"private-{self.contact['id']}-file-policy")
                targets = [{'id': m['id'], 'text': m['content'], 'bot': False} for m in instructions]
                blocked, _ = guard.filter(targets, [])
                if blocked:
                    raise ValueError('文件要求无法排除涉政内容，保持安静')
                request = '\n'.join(m['content'] for m in instructions)
                result = run_file_task(self.chat.db, self.contact['id'],
                    file_row(self.contact['id'], attachment), request, guard, self.bot.data.parent)
                _, blocked_name = guard.filter([], [], draft=result.name)
                if blocked_name:
                    raise ValueError('结果文件名称无法排除涉政内容，保持安静')
                job['result'] = result
            except Exception as exc:
                job['error'] = str(exc)
            finally:
                try:
                    if guard is not None:
                        guard.close()
                finally:
                    job['done'].set()

        threading.Thread(target=work, daemon=True, name='private-file-task').start()

    def step(self, pending):
        """Return normal messages, held messages, and whether work changed."""
        if self.contact.get('relationship') != 'girlfriend' or self.chat.relationship != 'girlfriend':
            return pending, [], False
        attachments = [m for m in pending if m['kind'] == '文件/链接/卡片']
        if not attachments:
            return pending, [], False
        attachment = next((m for m in attachments if office_attachment(m)), attachments[0])
        attachment_ids = {m['id'] for m in attachments}
        if not office_attachment(attachment):
            return [m for m in pending if m['id'] not in attachment_ids], attachments, False
        saved = _get(self.chat, attachment['id'])
        if saved and saved['status'] == 'confirmed':
            completed = {attachment['id'], *json.loads(saved['request_ids'])}
            self.chat.ack([m for m in pending if m['id'] in completed])
            return [m for m in pending if m['id'] not in completed], [], True
        if saved and saved['status'] in ('sending', 'unconfirmed'):
            held = attachment_ids | set(json.loads(saved['request_ids']))
            self.bot.update_private_status(self.contact['id'], private_state='error',
                private_error='文件发送状态待核，请检查私聊消息和草稿')
            return [m for m in pending if m['id'] not in held], [m for m in pending if m['id'] in held], False

        recent = self.chat.archive.recent(100)
        incoming = [m for m in recent if not m['is_self'] and m['kind'] == '文本'
                    and m['member_id'] == self.contact['id']]
        candidates = [m for m in incoming if FILE_REQUEST.search(m['content']) and
                      (m['id'] > attachment['id'] or 0 <= attachment['created'] - m['created'] <= 300)]
        if not candidates:
            return [m for m in pending if m['id'] not in attachment_ids], attachments, False
        waiting_ids = {m['id'] for m in pending}
        first = next((m for m in candidates if m['id'] in waiting_ids), candidates[-1])
        instructions = [m for m in incoming if m['id'] >= first['id']]
        task_ids = {m['id'] for m in instructions if FILE_REQUEST.search(m['content'])
                    or FILE_FOLLOWUP.search(m['content'])}
        task_ids.add(first['id'])
        held = attachment_ids | task_ids
        normal = [m for m in pending if m['id'] not in held]
        deferred = [m for m in pending if m['id'] in held]
        latest_id = max((m['id'] for m in incoming), default=0)

        if self.job:
            job = self.job
            if not job['done'].is_set():
                return normal, deferred, False
            self.job = None
            if job['error']:
                self.retry_at = time.monotonic() + 60
                self.bot.update_private_status(self.contact['id'], private_state='error', private_error=job['error'])
                return normal, deferred, False
            if job['attachment']['id'] != attachment['id'] or latest_id > job['latest_id']:
                return pending, [], True
            result = job['result']
            request_ids = [m['id'] for m in job['instructions']]
            _put(self.chat, attachment['id'], request_ids, result, 'ready')
        elif time.monotonic() < self.retry_at:
            return normal, deferred, False
        elif saved and saved['status'] == 'ready' and Path(saved['result_path']).is_file() \
                and json.loads(saved['request_ids']) == [m['id'] for m in instructions]:
            result = Path(saved['result_path'])
            request_ids = json.loads(saved['request_ids'])
        else:
            if time.monotonic() >= self.retry_at:
                self._start(attachment, instructions, latest_id)
            return normal, deferred, False

        # Read once more immediately before submission, including messages that
        # arrived while the result was being prepared or the UI was busy.
        new_messages = self.chat.poll()
        if new_messages:
            _put(self.chat, attachment['id'], [], '', 'stale')
            return pending + new_messages, [], True
        if self.bot.stop.is_set():
            return normal, deferred, False
        _put(self.chat, attachment['id'], request_ids, result, 'sending')
        from private_files import send_private_file
        try:
            outcome = send_private_file(self.bot, self.chat, self.contact, result)
        except Exception as exc:
            # An exception after entering the sender is ambiguous; never retry.
            _put(self.chat, attachment['id'], request_ids, result, 'unconfirmed')
            self.bot.update_private_status(self.contact['id'], private_state='error', private_error=str(exc))
            return normal, deferred, False
        if outcome.status == 'not_attempted':
            _put(self.chat, attachment['id'], request_ids, result, 'ready')
            self.retry_at = time.monotonic() + 30
            self.bot.update_private_status(self.contact['id'], private_state='error', private_error=outcome.detail)
            return normal, deferred, False
        status = 'confirmed' if outcome.status == 'confirmed' else 'unconfirmed'
        _put(self.chat, attachment['id'], request_ids, result, status)
        completed = {attachment['id'], *request_ids}
        self.chat.ack([m for m in pending if m['id'] in completed])
        self.bot.update_private_status(self.contact['id'], private_state='ready' if status == 'confirmed' else 'error',
            private_error=None if status == 'confirmed' else outcome.detail)
        return [m for m in pending if m['id'] not in completed], [], True
