"""Exercise the real private loop with fake Office processing and WeChat I/O."""
from contextlib import ExitStack
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from private_chat import PrivateConversation
from private_files import FileSendResult
from private_host import private_loop
from test_private_chat import Database, Model, row


class Guard:
    def filter(self, batch, context, draft=''):
        blocked = {m['id'] for m in batch if '涉政' in m['text']}
        return blocked, '涉政' in draft

    def close(self):
        pass


class PrivateFileIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'PERSONA.md').write_text('大肥鱼', encoding='utf-8')
        self.db = Database([])
        self.contact = {'id': 'wxid_friend', 'name': '朋友', 'relationship': 'girlfriend'}
        fast = Model({'reply': True, 'text': '晚饭可以吃面', 'needs_high': False,
                      'needs_web': False, 'research_message_ids': [], 'sticker_code': ''})
        self.chat = PrivateConversation(self.db, self.root, 'wxid_friend', '朋友',
            relationship='girlfriend', fast=fast, high=Model({}), strong=Model({}), guard=Guard())
        self.addCleanup(self.chat.close)
        self.ready = threading.Event()
        self.text_sent = threading.Event()
        self.texts = []
        self.statuses = []
        self.bot = SimpleNamespace(stop=threading.Event(), send_lock=threading.Lock(),
            bridge=SimpleNamespace(db=self.db), data=self.root / 'data',
            config={'poll_seconds': .01}, private_sessions={},
            private_sessions_lock=threading.Lock(), update_private_status=self.status)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch('private_host.PrivateConversation', return_value=self.chat))
        self.stack.enter_context(patch.object(self.chat, 'close', return_value=None))
        self.stack.enter_context(patch('private_host.send_private', side_effect=self.send_text))
        self.stack.enter_context(patch('private_host.confirm_private', return_value=True))
        self.stack.enter_context(patch('private_file_host.PoliticalGuard', side_effect=lambda **kw: Guard()))
        self.thread = None

    def status(self, contact_id, **values):
        self.statuses.append(values)
        if values.get('private_ready'):
            self.ready.set()

    def send_text(self, bridge, contact, text):
        self.texts.append(text)
        self.text_sent.set()
        return True, ''

    def start_loop(self):
        self.thread = threading.Thread(target=private_loop, args=(self.bot, self.contact), daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_loop)
        self.assertTrue(self.ready.wait(3), '私聊循环未就绪')

    def stop_loop(self):
        self.bot.stop.set()
        if self.thread:
            self.thread.join(5)
            self.assertFalse(self.thread.is_alive(), '私聊循环未停止')

    def add_file(self, seq=1):
        msg = row(seq, seq, 'wxid_friend',
                  '<msg><appmsg><type>6</type><title>表格.xlsx</title></appmsg></msg>')
        msg['type'] = '文件/链接/卡片'
        self.db.rows.append(msg)

    def add_text(self, seq, text):
        self.db.rows.append(row(seq, seq, 'wxid_friend', text))

    def test_file_without_instructions_does_not_block_unrelated_chat(self):
        self.start_loop()
        self.add_file()
        self.add_text(2, '晚饭吃什么')
        self.assertTrue(self.text_sent.wait(3), '等待中的附件阻塞了普通私聊')
        self.stop_loop()
        self.assertEqual(self.texts, ['晚饭可以吃面'])
        self.assertEqual([m['kind'] for m in self.chat.pending()], ['文件/链接/卡片'])

    def test_failed_file_work_does_not_block_unrelated_chat_or_retry_immediately(self):
        attempted = threading.Event()
        def fail(*args, **kwargs):
            attempted.set()
            raise RuntimeError('文件尚未下载')
        runner = self.stack.enter_context(patch('private_file_host.run_file_task', side_effect=fail))
        self.start_loop()
        self.add_file()
        self.add_text(2, '把表头加粗后发回给我')
        self.assertTrue(attempted.wait(3), '文件任务未开始')
        self.add_text(3, '晚饭吃什么')
        self.assertTrue(self.text_sent.wait(3), '文件处理失败阻塞了普通私聊')
        time.sleep(.2)
        self.stop_loop()
        self.assertEqual(runner.call_count, 1, '文件任务不应在短时间内反复重跑')
        self.assertEqual(self.texts, ['晚饭可以吃面'])
        self.assertEqual(len(self.chat.pending()), 2)

    def test_instructions_arriving_during_work_replace_result_before_send(self):
        first_started = threading.Event()
        finish_first = threading.Event()
        sent = threading.Event()
        calls = []
        outputs = [self.root / 'first.xlsx', self.root / 'second.xlsx']
        for output in outputs:
            output.write_text('test', encoding='utf-8')
        def run(db, contact_id, attachment, request, guard, root):
            calls.append(request)
            if len(calls) == 1:
                first_started.set()
                if not finish_first.wait(3):
                    raise TimeoutError('测试没有解除第一轮任务')
            return outputs[len(calls) - 1]
        submitted = []
        def send(bot, chat, contact, path):
            submitted.append(Path(path))
            sent.set()
            return FileSendResult('confirmed', 'ok')
        self.stack.enter_context(patch('private_file_host.run_file_task', side_effect=run))
        self.stack.enter_context(patch('private_files.send_private_file', side_effect=send))
        self.start_loop()
        self.add_file()
        self.add_text(2, '帮我整理这个表格')
        self.assertTrue(first_started.wait(3))
        self.add_text(3, '把表头加粗')
        finish_first.set()
        self.assertTrue(sent.wait(5), '最新结果未发送')
        self.stop_loop()
        self.assertGreaterEqual(len(calls), 2)
        self.assertIn('把表头加粗', calls[-1])
        self.assertEqual(submitted, [outputs[1]], '不能发送旧要求生成的文件')

    def test_unconfirmed_send_is_not_retried(self):
        outcome = threading.Event()
        output = self.root / 'result.xlsx'
        output.write_text('test', encoding='utf-8')
        self.stack.enter_context(patch('private_file_host.run_file_task', return_value=output))
        def send(*args):
            outcome.set()
            return FileSendResult('unconfirmed', '待核')
        sender = self.stack.enter_context(patch('private_files.send_private_file', side_effect=send))
        self.start_loop()
        self.add_file()
        self.add_text(2, '整理一下这个文件')
        self.assertTrue(outcome.wait(4))
        time.sleep(.2)
        self.stop_loop()
        self.assertEqual(sender.call_count, 1)

    def test_political_file_request_is_not_processed_and_stays_queued(self):
        filtered = threading.Event()
        class Policy(Guard):
            def filter(self, batch, context, draft=''):
                if any('涉政' in m['text'] for m in batch):
                    filtered.set()
                return super().filter(batch, context, draft)
        self.stack.enter_context(patch('private_file_host.PoliticalGuard', return_value=Policy()))
        runner = self.stack.enter_context(patch('private_file_host.run_file_task'))
        sender = self.stack.enter_context(patch('private_files.send_private_file'))
        self.start_loop()
        self.add_file()
        self.add_text(2, '请把涉政内容写进表格')
        self.assertTrue(filtered.wait(3), '文件要求没有经过涉政判定')
        self.stop_loop()
        runner.assert_not_called()
        sender.assert_not_called()
        self.assertEqual(len(self.chat.pending()), 2)
        self.assertEqual(self.texts, [])

    def test_uncertain_file_draft_hold_blocks_later_text(self):
        hold_set = threading.Event()
        output = self.root / 'result.xlsx'
        output.write_text('test', encoding='utf-8')
        self.stack.enter_context(patch('private_file_host.run_file_task', return_value=output))
        def send(bot, chat, contact, path):
            hold = bot.data / 'private' / contact['id'] / 'file-send-pending'
            hold.parent.mkdir(parents=True, exist_ok=True)
            hold.touch()
            hold_set.set()
            return FileSendResult('unconfirmed', '待核')
        self.stack.enter_context(patch('private_files.send_private_file', side_effect=send))
        self.start_loop()
        self.add_file()
        self.add_text(2, '整理一下这个文件')
        self.assertTrue(hold_set.wait(4))
        self.add_text(3, '晚饭吃什么')
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and not any(
                '文件发送状态待核' in str(s.get('private_error', '')) for s in self.statuses):
            time.sleep(.01)
        self.assertTrue(any('文件发送状态待核' in str(s.get('private_error', '')) for s in self.statuses))
        self.stop_loop()
        self.assertEqual(self.texts, [])
        self.assertIn('晚饭吃什么', [m['content'] for m in self.chat.pending()])

    def test_file_request_before_attachment_is_preserved_in_work(self):
        output = self.root / 'result.xlsx'
        output.write_text('test', encoding='utf-8')
        sent = threading.Event()
        runner = self.stack.enter_context(patch('private_file_host.run_file_task', return_value=output))
        def send(*args):
            sent.set()
            return FileSendResult('confirmed', 'ok')
        self.stack.enter_context(patch('private_files.send_private_file', side_effect=send))
        self.start_loop()
        self.add_text(1, '把表头加粗后发回给我')
        self.assertTrue(self.text_sent.wait(3), '前置说明未处理')
        self.add_file(2)
        self.assertTrue(sent.wait(4), '文件任务未关联前置说明')
        self.stop_loop()
        self.assertIn('把表头加粗后发回给我', runner.call_args.args[3])

    def test_review_gross_profit_table_is_routed_to_file_work(self):
        started = threading.Event()
        def run(db, contact_id, attachment, request, guard, root):
            started.set()
            raise RuntimeError('测试不产生或发送文件')
        runner = self.stack.enter_context(patch('private_file_host.run_file_task', side_effect=run))
        sender = self.stack.enter_context(patch('private_files.send_private_file'))
        self.start_loop()
        self.add_text(1, '检查一下毛利表')
        self.add_file(2)
        self.assertTrue(started.wait(3), '毛利表核查请求未进入文件处理')
        self.stop_loop()
        self.assertEqual(runner.call_args.args[3], '检查一下毛利表')
        sender.assert_not_called()


if __name__ == '__main__':
    unittest.main()
