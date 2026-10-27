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
FILE_TOPIC = re.compile(r'表格|表头|(?:毛利|利润|收益|财务|统计|工作|销售|成本|收入|明细|汇总)表|文件|文档|附件|工作簿|excel|xlsx|word|docx', re.I)
MODIFICATION_REQUEST = re.compile(
    r'(?:帮(?:我|忙)?|麻烦|可以|能否|直接).{0,12}(?:修改|改|调整|修正|完善)|'
    r'(?:表格|文件|文档|这[个份张]|那[个份张]).{0,12}(?:修改|改|调整|修正|完善)', re.I | re.S)
NO_MODIFICATION = re.compile(r'(?:不要|不必|无需|不需要|别).{0,5}(?:修改|改|调整|修正|完善)')
FILE_REMINDER = re.compile(r'^[\s？?！!。]*(?:大?肥鱼)?(?:干活|在吗|弄好了|做好了吗|进度|怎么样|好了吗)[\s？?！!。]*$')


def is_followup_request(message):
    return (message['kind'] == '文本' and bool(MODIFICATION_REQUEST.search(message['content']))
            and not NO_MODIFICATION.search(message['content']))


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
        chat.archive.db.execute('''CREATE TABLE IF NOT EXISTS private_file_sources (
            attachment_id INTEGER PRIMARY KEY, result_path TEXT NOT NULL)''')


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


def _confirmed_source(chat, attachment_id, path=None):
    with chat.archive.lock, chat.archive.db:
        if path is not None:
            chat.archive.db.execute('''INSERT INTO private_file_sources VALUES (?, ?)
                ON CONFLICT(attachment_id) DO UPDATE SET result_path=excluded.result_path''',
                (attachment_id, str(path)))
        row = chat.archive.db.execute('SELECT result_path FROM private_file_sources WHERE attachment_id=?',
                                      (attachment_id,)).fetchone()
    return Path(row['result_path']) if row else None


class FileCoordinator:
    """Run document work off the chat loop and submit only a current result."""

    def __init__(self, bot, chat, contact):
        self.bot, self.chat, self.contact = bot, chat, contact
        self.job = None
        self.retry_at = 0.0
        _store(chat)

    def _start(self, attachment, instructions, latest_id, context=(), source_override=None):
        job = {'attachment': attachment, 'instructions': instructions,
               'latest_id': latest_id, 'done': threading.Event(), 'result': None, 'error': None}
        self.job = job

        def work():
            guard = None
            try:
                guard = PoliticalGuard(label=f"private-{self.contact['id']}-file-policy")
                targets = [{'id': m['id'], 'text': m['content'], 'bot': bool(m['is_self'])}
                           for m in (*context, *instructions)]
                blocked, _ = guard.filter(targets, [])
                if blocked:
                    raise ValueError('文件要求无法排除涉政内容，保持安静')
                request = '\n'.join(m['content'] for m in instructions)
                if context:
                    request = ('前文仅供理解本次要求；对前文提到的问题须先依据文件核实，不能猜测或编造。\n'
                               + '\n'.join(m['content'] for m in context)
                               + '\n本次要求：\n' + request)
                args = (self.chat.db, self.contact['id'], file_row(self.contact['id'], attachment),
                        request, guard, self.bot.data.parent)
                result = (run_file_task(*args, source_override=source_override)
                          if source_override is not None else run_file_task(*args))
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

    def _previous_file(self, pending):
        """Link only an immediate file-related follow-up within 15 minutes."""
        messages = [m for m in pending if is_followup_request(m)]
        if not messages:
            return None
        # Keep the first pending follow-up as the anchor while later requests
        # are folded into the same task and invalidate a running result.
        request = messages[0]
        recent = self.chat.archive.recent(30)
        prior = [m for m in recent if m['id'] < request['id']]
        files = [m for m in prior if m['member_id'] == self.contact['id']
                 and not m['is_self'] and office_attachment(m)
                 and 0 <= request['created'] - m['created'] <= 900]
        if not files:
            return None
        attachment = files[-1]
        texts = [m for m in prior if not m['is_self'] and m['kind'] == '文本'
                 and m['member_id'] == self.contact['id']]
        if not texts:
            return None
        # A brief "肥鱼干活" reminder continues the file topic; general chat
        # interrupts it and prevents automatic reassociation.
        cursor = len(texts) - 1
        while cursor >= 0 and FILE_REMINDER.fullmatch(texts[cursor]['content'].strip()):
            cursor -= 1
        if cursor < 0 or len(texts) - 1 - cursor > 2:
            return None
        previous = texts[cursor]
        if not FILE_TOPIC.search(previous['content']) or not FILE_REQUEST.search(previous['content']):
            return None
        if abs(previous['created'] - attachment['created']) > 300:
            return None
        context = [previous]
        replies = [m for m in prior if m['is_self'] and m['kind'] == '文本' and m['id'] > previous['id']]
        if replies:
            context.append(replies[-1])
        return attachment, request['id'], context

    def step(self, pending):
        """Return normal messages, held messages, and whether work changed."""
        if self.contact.get('relationship') != 'girlfriend' or self.chat.relationship != 'girlfriend':
            return pending, [], False
        linked = None
        if not any(m['kind'] == '文件/链接/卡片' for m in pending):
            linked = self._previous_file(pending)
            if not linked:
                return pending, [], False
            pending = list(pending) + [linked[0]]
        normal, deferred, changed = self._step(pending, linked)
        if linked:
            # The old attachment remains archived, never returned to the queue.
            normal = [m for m in normal if m['id'] != linked[0]['id']]
            deferred = [m for m in deferred if m['id'] != linked[0]['id']]
        return normal, deferred, changed

    def _step(self, pending, linked):
        attachments = [m for m in pending if m['kind'] == '文件/链接/卡片']
        if not attachments:
            return pending, [], False
        attachment = next((m for m in attachments if office_attachment(m)), attachments[0])
        attachment_ids = {m['id'] for m in attachments}
        if not office_attachment(attachment):
            return [m for m in pending if m['id'] not in attachment_ids], attachments, False
        saved = _get(self.chat, attachment['id'])
        source_override = _confirmed_source(self.chat, attachment['id']) if linked else None
        if saved and saved['status'] == 'confirmed' and linked and linked[1] not in json.loads(saved['request_ids']):
            if source_override is None:
                source_override = _confirmed_source(self.chat, attachment['id'], saved['result_path'])
            saved = None  # A different, explicit follow-up is a new task.
        if source_override is not None and not source_override.is_file():
            self.bot.update_private_status(self.contact['id'], private_state='error',
                private_error='此前文件处理结果已不可用，不能安全继续修改')
            held = attachment_ids | {linked[1]}
            return [m for m in pending if m['id'] not in held], [m for m in pending if m['id'] in held], False
        if saved and saved['status'] == 'confirmed':
            completed = {attachment['id'], *json.loads(saved['request_ids'])}
            self.chat.ack([m for m in pending if m['id'] in completed])
            return [m for m in pending if m['id'] not in completed], [], True
        if saved and saved['status'] in ('sending', 'unconfirmed'):
            held = attachment_ids | set(json.loads(saved['request_ids']))
            if linked:
                held.add(linked[1])
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
                self._start(attachment, instructions, latest_id, linked[2] if linked else (), source_override)
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
        if status == 'confirmed':
            _confirmed_source(self.chat, attachment['id'], result)
        completed = {attachment['id'], *request_ids}
        self.chat.ack([m for m in pending if m['id'] in completed])
        self.bot.update_private_status(self.contact['id'], private_state='ready' if status == 'confirmed' else 'error',
            private_error=None if status == 'confirmed' else outcome.detail)
        return [m for m in pending if m['id'] not in completed], [], True
