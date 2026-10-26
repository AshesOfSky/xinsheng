from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import zstandard
from wechat_bridge import WeChatBridge
from native_mentions import outgoing_matches
from group_names import group_member_names


class IdentityTests(unittest.TestCase):
    def test_failed_sender_initialization_is_not_reused(self):
        bridge = WeChatBridge.__new__(WeChatBridge)
        bridge.config = {'group_name':'测试群','account':'test_account'}
        bridge.gui = None
        candidate = Mock()
        candidate._get_db.side_effect = RuntimeError('微信进程已退出')
        with patch('win32gui.FindWindow', return_value=123), \
                patch('wechatauto.guia.WeChatGUI', return_value=candidate), \
                patch('wechatauto.rhythm.set_profile'):
            with self.assertRaisesRegex(RuntimeError, '微信进程已退出'):
                bridge.prepare_sender()
        self.assertIsNone(bridge.gui)

    def bridge(self, source):
        bridge = WeChatBridge.__new__(WeChatBridge)
        bridge.group = 'room@chatroom'
        bridge.self_id = 'wxid_bot'
        bridge.names = {'wxid_member': '同名成员', 'wxid_bot': '大肥鱼'}
        bridge.db = SimpleNamespace(get_message_row=lambda *a, **k: {'source': source})
        return bridge

    def row(self, content):
        return {'shard': 'message/message_0.db', 'local_id': 1, 'sort_seq': 9,
                'type': '文本', 'sender_username': 'wxid_member',
                'content': content, 'create_time': 100}

    def test_compressed_real_mention_and_sender_prefix(self):
        source = zstandard.ZstdCompressor().compress(
            b'<msgsource><atuserlist><![CDATA[wxid_bot]]></atuserlist></msgsource>')
        result = self.bridge(source).normalize(self.row('wxid_member:\n@大肥鱼\u2005你好😀'))
        self.assertTrue(result['is_at'])
        self.assertFalse(result['is_self'])
        self.assertEqual(result['member_id'], 'wxid_member')
        self.assertEqual(result['content'], '@大肥鱼\u2005你好😀')
        self.assertEqual(result['at_ids'], ['wxid_bot'])

    def test_typed_name_is_not_a_metadata_mention(self):
        source = b'<msgsource><atuserlist><![CDATA[wxid_other]]></atuserlist></msgsource>'
        result = self.bridge(source).normalize(self.row('wxid_member:\n@大肥鱼 只是文字'))
        self.assertFalse(result['is_at'])

    def test_outgoing_mention_requires_target_id_in_metadata(self):
        pending = {'after_id':10, 'text':'查证结果', 'mention_ids':['wxid_member']}
        sent = {'id':11, 'is_self':True, 'content':'@同名成员\u2005查证结果', 'at_ids':[]}
        self.assertFalse(outgoing_matches(sent, pending))
        sent['at_ids'] = ['wxid_other']
        self.assertFalse(outgoing_matches(sent, pending))
        sent['at_ids'] = ['wxid_member']
        self.assertTrue(outgoing_matches(sent, pending))

    def test_group_alias_is_read_separately_from_contact_nickname(self):
        def field(number, value):
            return bytes([number << 3 | 2, len(value)]) + value
        member = field(1, b'wxid_member') + field(2, '群内新昵称'.encode()) + b'\x18\x11' + field(4, b'wxid_inviter')
        unnamed = field(1, b'wxid_other') + b'\x18\x11'
        self.assertEqual(group_member_names(field(1, member) + field(1, unnamed)), {'wxid_member':'群内新昵称'})


if __name__ == '__main__':
    unittest.main()
