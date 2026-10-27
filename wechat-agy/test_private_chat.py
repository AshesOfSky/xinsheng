import tempfile
import unittest
import json
from pathlib import Path

from private_chat import PrivateConversation


def row(seq, local_id, sender, text, shard='message_0.db'):
    return {'sort_seq': seq, 'local_id': local_id, 'sender_username': sender,
            'content': text, 'shard': shard, 'create_time': 1000 + seq, 'type': '文本'}


class Database:
    wxid = 'wxid_bot'

    def __init__(self, rows):
        self.rows = list(rows)

    def get_messages(self, user, limit=200):
        return list(reversed(self.rows[-limit:]))

    def get_new_messages(self, user, since_seq=0, limit=200):
        return [r for r in self.rows if r['sort_seq'] > since_seq][:limit]


class Model:
    def __init__(self, result, model='test-model'):
        self.result = result
        self.model = model
        self.calls = []
        self.last_result = {'steps': []}
        self.closed = False

    def ask(self, payload, **kwargs):
        self.calls.append(payload)
        return self.result

    def close(self):
        self.closed = True


class PrivateConversationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.fast = Model({'reply': True, 'text': '收到', 'needs_high': False,
                           'needs_web': False, 'research_message_ids': [], 'sticker_code': ''})
        self.high = Model({'reply': True, 'text': '深入回答', 'sources': []})
        self.strong = Model({'reply': True, 'text': '深入回答'}, model='gpt-6-astra')
        self.db = Database([row(10, 1, 'wxid_friend', '历史来信'),
                            row(11, 2, 'wxid_bot', '历史回复')])
        self.chat = PrivateConversation(self.db, self.root, 'wxid_friend', '朋友',
                                        fast=self.fast, high=self.high, strong=self.strong)
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(self.chat.close)

    def test_history_and_own_messages_are_not_returned_as_new(self):
        self.chat.start()
        self.assertEqual(self.chat.poll(), [])
        self.assertEqual(self.chat.pending(), [])
        # A new row may share the last snapshot's sort_seq.
        self.db.rows += [row(11, 3, 'wxid_friend', '新问题'),
                         row(12, 4, 'wxid_bot', '自己发的'),
                         row(13, 5, '', '未知发件人')]
        incoming = self.chat.poll()
        self.assertEqual([m['content'] for m in incoming], ['新问题'])
        self.assertEqual(self.chat.poll(), [])
        self.assertEqual([m['content'] for m in self.chat.pending()], ['新问题'])
        self.assertEqual([m['content'] for m in self.chat.archive.recent()],
                         ['历史来信', '历史回复', '新问题', '自己发的'])

    def test_private_context_is_isolated_and_fast_reply_has_no_send(self):
        self.chat.start()
        self.db.rows.append(row(12, 3, 'wxid_friend', '你好'))
        incoming = self.chat.poll()
        self.assertEqual(self.chat.generate(incoming, '鱼的人格'), {'reply': True, 'text': '收到'})
        payload = self.fast.calls[-1]
        self.assertEqual(payload['new_messages'][0]['text'], '你好')
        self.assertEqual(payload['unanswered_mentions'], [])
        self.assertEqual(self.high.calls, [])
        self.assertTrue((self.root / 'data/private/wxid_friend/archive.sqlite').exists())
        self.assertFalse((self.root / 'data/archive.sqlite').exists())

    def test_serious_question_uses_private_strong_session(self):
        self.chat.start()
        self.db.rows.append(row(12, 3, 'wxid_friend', '请仔细算一下'))
        incoming = self.chat.poll()
        self.fast.result.update(needs_high=True, research_message_ids=[incoming[0]['id']])
        self.assertEqual(self.chat.generate(incoming, '鱼的人格')['text'], '深入回答')
        self.assertEqual(self.strong.calls[0]['research_questions'][0]['text'], '请仔细算一下')
        self.assertEqual(self.high.calls, [])

    def test_material_edit_uses_strong_even_if_fast_misclassifies(self):
        self.chat.start()
        request = '宝宝，你帮我修改以下内容，保留段落，字数不超过一百字：' + '示例文字。' * 80
        self.db.rows.append(row(12, 3, 'wxid_friend', request))
        result = self.chat.generate(self.chat.poll(), '鱼的人格')
        self.assertEqual(result['text'], '深入回答')
        self.assertEqual(self.chat.last_model, 'gpt-6-astra')
        self.assertEqual(self.strong.calls[0]['research_questions'][0]['text'], request)
        self.assertEqual(self.high.calls, [])

    def test_live_research_keeps_verified_web_path(self):
        self.chat.start()
        self.db.rows.append(row(12, 3, 'wxid_friend', '请查最新公开数据'))
        incoming = self.chat.poll()
        self.fast.result.update(needs_high=True, needs_web=True, research_message_ids=[incoming[0]['id']])
        self.high.result.update(text='已核对', sources=[{'title':'来源','url':'https://example.org/source'}])
        self.high.last_result['steps'] = [{'tool':'search_web', 'tool_error':None}]
        result = self.chat.generate(incoming, '鱼的人格')
        self.assertIn('https://example.org/source', result['text'])
        self.assertEqual(self.strong.calls, [])

    def test_failed_work_model_keeps_incoming_request_pending(self):
        self.chat.start()
        self.db.rows.append(row(12, 3, 'wxid_friend', '请帮我修改这一段材料'))
        incoming = self.chat.poll()
        def fail(payload, **kwargs):
            raise RuntimeError('Codex 暂时不可用')
        self.strong.ask = fail
        with self.assertRaisesRegex(RuntimeError, '暂时不可用'):
            self.chat.generate(incoming, '鱼的人格')
        self.assertEqual([m['id'] for m in self.chat.pending()], [incoming[0]['id']])

    def test_two_contacts_do_not_share_archive_or_model_context(self):
        self.chat.start()
        self.db.rows.append(row(12, 3, 'wxid_friend', '朋友甲的问题'))
        self.chat.generate(self.chat.poll(), '鱼')
        other_db = Database([row(20, 8, 'wxid_other', '朋友乙的历史')])
        other_fast = Model({'reply': True, 'text': '收到', 'needs_high': False,
                            'needs_web': False, 'research_message_ids': [], 'sticker_code': ''})
        other_high = Model({'reply': True, 'text': '深入回答', 'sources': []})
        other_strong = Model({'reply': True, 'text': '深入回答'})
        other = PrivateConversation(other_db, self.root, 'wxid_other', '另一位朋友',
                                    fast=other_fast, high=other_high, strong=other_strong)
        self.addCleanup(other.close)
        other.start()
        other_db.rows.append(row(21, 9, 'wxid_other', '朋友乙的问题'))
        other.generate(other.poll(), '鱼')
        texts = [m['text'] for m in other_fast.calls[0]['recent_messages']]
        self.assertEqual(texts, ['朋友乙的历史', '朋友乙的问题'])
        self.assertNotIn('朋友甲的问题', texts)
        self.assertEqual(len(self.fast.calls), 1)

    def test_new_media_asks_for_text_and_never_passes_xml_to_models(self):
        self.chat.start()
        media = [row(12, 3, 'wxid_friend', '<msg><img aeskey="private-image-key"/></msg>'),
                 row(13, 4, 'wxid_friend', '<msg><voice cdnurl="private-voice-url"/></msg>'),
                 row(14, 5, 'wxid_friend', '<msg><appmsg><title>private-file-key</title></appmsg></msg>')]
        for item, kind in zip(media, ['图片', '语音', '文件/链接/卡片']):
            item['type'] = kind
        system = row(15, 6, 'wxid_friend', '好友验证系统提示')
        system['type'] = '系统消息'
        self.db.rows.extend(media + [system])
        incoming = self.chat.poll()
        self.assertEqual([m['kind'] for m in incoming], ['图片', '语音', '文件/链接/卡片'])
        reply = self.chat.generate(incoming, '鱼的人格')
        self.assertTrue(reply['reply'])
        self.assertIn('读不到', reply['text'])
        self.assertIn('文字', reply['text'])
        self.assertEqual(self.fast.calls, [])
        self.assertEqual(self.high.calls, [])

        self.db.rows.append(row(16, 7, 'wxid_friend', '我应该怎么办'))
        text = self.chat.poll()
        self.fast.result.update(needs_high=True, research_message_ids=[text[0]['id']])
        self.chat.generate(text, '鱼的人格')
        for payload in (self.fast.calls[0], self.strong.calls[0]):
            encoded = json.dumps(payload, ensure_ascii=False)
            self.assertNotIn('private-image-key', encoded)
            self.assertNotIn('private-voice-url', encoded)
            self.assertNotIn('private-file-key', encoded)
            self.assertIn('内容尚未读取', encoded)
            self.assertIn('不要假装看过或听过', encoded)

    def test_mixed_text_and_image_answers_text_and_mentions_unread_image(self):
        self.chat.start()
        picture = row(13, 4, 'wxid_friend', '<msg><img aeskey="private-image-key"/></msg>')
        picture['type'] = '图片'
        self.db.rows.extend([row(12, 3, 'wxid_friend', '先帮我想个标题'), picture])
        batch = self.chat.poll()
        self.strong.result['text'] = '可以叫《春日随笔》。'
        result = self.chat.generate(batch, '鱼的人格')
        self.assertIn('可以叫《春日随笔》。', result['text'])
        self.assertIn('读不到', result['text'])
        self.assertIn('文字', result['text'])
        payload = self.fast.calls[0]
        self.assertEqual(payload['new_messages'][0]['text'], '先帮我想个标题')
        self.assertIn('内容尚未读取', payload['new_messages'][1]['text'])
        self.assertIn('不要假装看过或听过', payload['instruction'])
        self.assertNotIn('private-image-key', json.dumps(payload, ensure_ascii=False))

    def test_mixed_text_and_file_routes_only_text_to_high(self):
        self.chat.start()
        attachment = row(13, 4, 'wxid_friend', '<msg><appmsg><aeskey>private-file-key</aeskey></appmsg></msg>')
        attachment['type'] = '文件/链接/卡片'
        self.db.rows.extend([row(12, 3, 'wxid_friend', '仔细算一下 2 加 2'), attachment])
        batch = self.chat.poll()
        self.fast.result.update(needs_high=True, research_message_ids=[m['id'] for m in batch])
        self.strong.result['text'] = '2 加 2 等于 4。'
        result = self.chat.generate(batch, '鱼的人格')
        self.assertIn('2 加 2 等于 4。', result['text'])
        self.assertIn('读不到', result['text'])
        research = self.strong.calls[0]['research_questions']
        self.assertEqual([m['text'] for m in research], ['仔细算一下 2 加 2'])
        self.assertNotIn('private-file-key', json.dumps(self.strong.calls[0], ensure_ascii=False))

    def test_cannot_generate_for_another_sender_or_without_baseline(self):
        with self.assertRaises(RuntimeError):
            self.chat.poll()
        with self.assertRaises(ValueError):
            self.chat.generate([{'member_id': 'wxid_other', 'is_self': False}], '鱼')
        with self.assertRaises(ValueError):
            PrivateConversation(self.db, self.root, 'wxid_friend@chatroom', '群',
                                fast=self.fast, high=self.high, strong=self.strong)

    def reopen(self):
        self.chat.close()
        self.chat = PrivateConversation(self.db, self.root, 'wxid_friend', '朋友',
                                        fast=self.fast, high=self.high, strong=self.strong)
        self.addCleanup(self.chat.close)
        self.chat.start()

    def test_pending_and_offline_messages_survive_restart_without_history_replay(self):
        self.chat.start()
        self.db.rows.append(row(12, 3, 'wxid_friend', '待处理'))
        self.chat.poll()
        self.chat.close()
        self.db.rows.extend([row(13, 4, 'wxid_friend', '停机期间收到'),
                             row(14, 5, 'wxid_bot', '自己发的')])
        self.chat = PrivateConversation(self.db, self.root, 'wxid_friend', '朋友',
                                        fast=self.fast, high=self.high, strong=self.strong)
        self.addCleanup(self.chat.close)
        self.chat.start()
        pending = self.chat.pending()
        self.assertEqual([m['content'] for m in pending], ['待处理', '停机期间收到'])
        self.chat.ack(pending)
        self.reopen()
        self.assertEqual(self.chat.pending(), [])
        self.assertEqual(self.chat.poll(), [])

    def test_claim_release_and_ack_are_durable_for_the_whole_batch(self):
        self.chat.start()
        self.db.rows.extend([row(12, 3, 'wxid_friend', '第一条'),
                             row(13, 4, 'wxid_friend', '第二条')])
        self.chat.poll()
        batch = self.chat.pending()
        with self.assertRaises(ValueError):
            self.chat.claim(batch + [{'id': 999}])
        self.assertEqual(len(self.chat.pending()), 2)
        self.chat.claim(batch)
        self.reopen()
        self.assertEqual(self.chat.pending(), [])
        self.chat.release(batch)  # caller has proved no input was attempted
        self.assertEqual(len(self.chat.pending()), 2)
        self.chat.claim(batch)
        self.chat.ack(batch)
        self.reopen()
        self.assertEqual(self.chat.pending(), [])

    def test_outbox_sending_states_are_not_retried(self):
        self.chat.start()
        for seq in range(12, 16):
            self.db.rows.append(row(seq, seq, 'wxid_friend', f'问题 {seq}'))
        self.chat.poll()
        batch = self.chat.pending()
        for message, status in zip(batch, ('sending', 'submitted', 'unconfirmed', 'confirmed')):
            self.chat.archive.record_outgoing(message['id'], '回复', status)
        self.assertEqual(self.chat.pending(), [])
        with self.assertRaises(ValueError):
            self.chat.claim(batch)
        self.reopen()
        self.assertEqual(self.chat.pending(), [])

    def test_recovery_if_archive_was_written_before_queue_at_same_sequence(self):
        self.chat.start()
        item = row(11, 3, 'wxid_friend', '同序号的新消息')
        self.db.rows.append(item)
        self.chat._archive_row(item)  # simulate a stop after archive commit
        self.reopen()
        self.assertEqual([m['content'] for m in self.chat.pending()], ['同序号的新消息'])
        self.assertEqual(self.chat.poll(), [])

    def test_existing_archive_upgrades_without_replaying_sent_batch(self):
        # Simulate an archive created by the previous private host, before the
        # durable cursor existed, with an uncertain later coalesced batch.
        old = [row(10, 1, 'wxid_friend', '旧问题'),
               row(11, 2, 'wxid_bot', '上一条已经发送'),
               row(12, 3, 'wxid_friend', '已发送批次的第一条'),
               row(13, 4, 'wxid_friend', '已发送批次的最后一条'),
               row(14, 5, 'wxid_friend', '之后尚未处理')]
        self.db.rows = list(old)
        for item in old:
            self.chat._archive_row(item)
        last_sent = next(m for m in self.chat.archive.recent() if m['content'] == '已发送批次的最后一条')
        self.chat.archive.record_outgoing(last_sent['id'], '可能已经发送的回复', 'unconfirmed')
        self.db.rows.append(row(15, 6, 'wxid_friend', '停机时新到的来信'))
        self.chat.start()
        self.assertEqual([m['content'] for m in self.chat.pending()],
                         ['之后尚未处理', '停机时新到的来信'])
        self.reopen()
        self.assertEqual([m['content'] for m in self.chat.pending()],
                         ['之后尚未处理', '停机时新到的来信'])


if __name__ == '__main__':
    unittest.main()
