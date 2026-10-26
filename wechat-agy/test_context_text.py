import unittest
from context_text import for_model


class ContextTests(unittest.TestCase):
    def test_media_transport_metadata_is_not_sent_to_model(self):
        msg = {'kind': '图片', 'content': '<msg><img aeskey="private" cdnurl="internal"/></msg>'}
        self.assertEqual(for_model(msg)['content'], '[图片]')
        self.assertIn('aeskey', msg['content'])

    def test_card_keeps_title_without_transport_fields(self):
        msg = {'kind': '文件/链接/卡片', 'content': '<msg><appmsg><title>群友的文字</title><aeskey>private</aeskey></appmsg></msg>'}
        self.assertEqual(for_model(msg)['content'], '[文件/链接/卡片] 群友的文字')


if __name__ == '__main__':
    unittest.main()
