import tempfile
import threading
import unittest
from pathlib import Path

from bot import Bot


class PrivateStatusTests(unittest.TestCase):
    def test_contacts_keep_independent_ready_and_error_states(self):
        with tempfile.TemporaryDirectory() as folder:
            bot = Bot.__new__(Bot)
            bot.data = Path(folder)
            bot.config = {'private_contacts': [
                {'id': 'wxid_one', 'name': '甲'}, {'id': 'wxid_two', 'name': '乙'}]}
            bot.status = {}
            bot.status_lock = threading.Lock()
            bot.update_private_status('wxid_one', private_ready=True, private_state='ready', private_error=None)
            self.assertFalse(bot.status['private_ready'])
            bot.update_private_status('wxid_two', private_ready=False, private_state='error', private_error='启动失败')
            bot.update_private_status('wxid_one', private_state='ready', private_error=None)
            self.assertFalse(bot.status['private_ready'])
            self.assertIn('乙: 启动失败', bot.status['private_error'])
            bot.update_private_status('wxid_two', private_ready=True, private_state='ready', private_error=None)
            self.assertTrue(bot.status['private_ready'])
            self.assertIsNone(bot.status['private_error'])
            self.assertEqual(bot.status['private_state'], 'ready')


if __name__ == '__main__':
    unittest.main()
