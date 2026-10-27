import json
import msvcrt
from pathlib import Path
import sqlite3
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app_paths import initialize_workspace
from desktop_control import host_running, save_config, snapshot, start_host


class DesktopTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = initialize_workspace(Path(self.temp.name))
        self.config = {'account':'sample_account','group_id':'room@chatroom','group_name':'测试群',
                       'poll_seconds':.35,'ordinary_reply_seconds':4}

    def tearDown(self):self.temp.cleanup()

    def test_workspace_keeps_existing_persona_and_separates_runtime(self):
        (self.root/'PERSONA.md').write_text('我的鱼', encoding='utf-8')
        initialize_workspace(self.root)
        self.assertEqual((self.root/'PERSONA.md').read_text(encoding='utf-8'), '我的鱼')
        self.assertEqual(len(list((self.root/'.agents/agents').glob('*.md'))), 3)
        self.assertFalse((self.root/'data/config.json').exists())

    def test_stale_status_is_not_shown_as_a_running_host(self):
        (self.root/'data/status.json').write_text(json.dumps({'state':'running','pid':1234}))
        self.assertEqual(snapshot(self.root)[0]['state'],'stopped')

    def test_active_host_lock_blocks_duplicate_launch(self):
        with (self.root/'.runtime/host.lock').open('a+b') as lock:
            lock.seek(0);msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
            try:
                self.assertTrue(host_running(self.root))
                with patch('desktop_control.subprocess.Popen') as launch:
                    self.assertIsNone(start_host(self.root))
                    launch.assert_not_called()
                with self.assertRaisesRegex(ValueError, '停止后台'):
                    save_config(self.root,self.config)
            finally:msvcrt.locking(lock.fileno(),msvcrt.LK_UNLCK,1)

    def test_changed_group_cannot_mix_existing_memory(self):
        save_config(self.root,self.config)
        with sqlite3.connect(self.root/'data/archive.sqlite') as db:
            db.execute('CREATE TABLE messages(id INTEGER)')
            db.execute('INSERT INTO messages VALUES(1)')
        db.close()
        with self.assertRaisesRegex(ValueError,'已有群聊档案'):
            save_config(self.root,dict(self.config,group_id='other@chatroom'))
        self.assertEqual(json.loads((self.root/'data/config.json').read_text(encoding='utf-8')), self.config)

    def test_saving_group_settings_preserves_private_contacts(self):
        from desktop_ui import Desktop
        contact = {'id': 'wxid_example', 'name': '朋友'}
        state = SimpleNamespace(current_config=dict(self.config, private_contacts=[contact]),
            group_map={'chosen': {'username':'room@chatroom','name':'测试群'}},
            group=SimpleNamespace(get=lambda: 'chosen'),
            account=SimpleNamespace(get=lambda: 'sample_account'),
            cooldown=SimpleNamespace(get=lambda: '2'),
            settings_note=SimpleNamespace(configure=lambda **kw: None))
        with patch('desktop_ui.save_config') as save:
            Desktop.save_settings(state)
        self.assertEqual(save.call_args.args[1]['private_contacts'], [contact])


if __name__ == '__main__':unittest.main()
