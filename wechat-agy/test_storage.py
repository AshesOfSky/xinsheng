import json
from pathlib import Path
import tempfile
import unittest

from storage import Archive


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.archive = Archive(self.temp.name)
        self.archive.member_names([
            {'username': 'member-a', 'nick_name': '同名'},
            {'username': 'member-b', 'nick_name': '同名'},
        ])

    def tearDown(self):
        self.archive.db.close()
        self.temp.cleanup()

    def message(self, key, member, seq=100, is_self=False):
        return {'source_key': key, 'member_id': member, 'name': '同名',
                'created': 1793000000, 'kind': '文本', 'content': '中文😀\u2005',
                'is_self': is_self, 'is_at': False, 'sort_seq': seq}

    def test_real_ids_preserve_same_seq_rows_without_duplicate_archive(self):
        first = self.message('shard0/1', 'member-a')
        self.archive.add(first)
        self.archive.add(self.message('shard1/1', 'member-b'))
        self.assertIsNone(self.archive.add(first))
        rows = self.archive.recent()
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]['content'], '中文😀\u2005')
        lines = list((Path(self.temp.name) / 'messages').glob('*.jsonl'))[0].read_text(encoding='utf-8').splitlines()
        self.assertEqual(len(lines), 2)
        self.assertEqual(json.loads(lines[1])['member_id'], 'member-b')

    def test_member_evidence_cannot_cross_people_or_use_bot_message(self):
        a = self.archive.add(self.message('s/1', 'member-a'))
        b = self.archive.add(self.message('s/2', 'member-b'))
        bot = self.archive.add(self.message('s/3', 'member-a', is_self=True))
        self.archive.save_memory([a, b, bot], {'summary': '测试归纳', 'members': [{
            'member_id': 'member-a', 'observations': [
                {'text': '本人发言', 'evidence_ids': [a['id']]},
                {'text': '错归别人', 'evidence_ids': [b['id']]},
                {'text': '虚构来源', 'evidence_ids': [999]},
                {'text': '机器人自证', 'evidence_ids': [bot['id']]},
            ],
        }]})
        profiles = self.archive.profiles(['member-a', 'member-b'])
        self.assertEqual([x['text'] for x in profiles[0]['observations']], ['本人发言'])
        self.assertEqual(profiles[1]['observations'], [])
        self.assertEqual(self.archive.unsummarized(), [])

    def test_catchup_keeps_context_in_message_time_order(self):
        latest = self.archive.add(dict(self.message('s/2', 'member-a', seq=200), created=200))
        older = self.archive.add(dict(self.message('s/1', 'member-b', seq=100), created=100))
        self.assertEqual([m['id'] for m in self.archive.recent()], [older['id'], latest['id']])
        self.assertEqual(self.archive.recent(1)[0]['id'], latest['id'])


if __name__ == '__main__':
    unittest.main()
