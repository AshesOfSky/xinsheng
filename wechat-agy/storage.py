"""Local archive and member observations, keyed by actual WeChat identities."""
from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import sqlite3
import threading


class Archive:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(self.directory / 'archive.sqlite', check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY, source_key TEXT UNIQUE NOT NULL,
                member_id TEXT NOT NULL, name TEXT NOT NULL, created INTEGER NOT NULL,
                kind TEXT NOT NULL, content TEXT NOT NULL, is_self INTEGER NOT NULL,
                is_at INTEGER NOT NULL, sort_seq INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS members (
                member_id TEXT PRIMARY KEY, name TEXT NOT NULL,
                observations TEXT NOT NULL DEFAULT '[]', updated INTEGER);
            CREATE TABLE IF NOT EXISTS summaries (
                id INTEGER PRIMARY KEY, first_id INTEGER NOT NULL,
                last_id INTEGER NOT NULL, text TEXT NOT NULL, created INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS outbox (
                id INTEGER PRIMARY KEY, trigger_id INTEGER NOT NULL,
                text TEXT NOT NULL, status TEXT NOT NULL, created INTEGER NOT NULL);
        ''')

    def member_names(self, members):
        with self.lock, self.db:
            for member in members:
                mid = member['username']
                name = member.get('group_name') or member.get('remark') or member.get('nick_name') or mid
                self.db.execute('''INSERT INTO members(member_id,name) VALUES(?,?)
                    ON CONFLICT(member_id) DO UPDATE SET name=excluded.name''', (mid, name))

    def add(self, message):
        with self.lock, self.db:
            cur = self.db.execute('''INSERT OR IGNORE INTO messages
                (source_key,member_id,name,created,kind,content,is_self,is_at,sort_seq)
                VALUES(:source_key,:member_id,:name,:created,:kind,:content,:is_self,:is_at,:sort_seq)''', message)
            if not cur.rowcount:
                return None
            message = dict(message, id=cur.lastrowid)
            if message['member_id']:
                self.db.execute('''INSERT INTO members(member_id,name) VALUES(?,?)
                    ON CONFLICT(member_id) DO UPDATE SET name=excluded.name''',
                    (message['member_id'], message['name']))
        with self.lock:
            day = datetime.fromtimestamp(message['created']).strftime('%Y-%m-%d')
            archive_dir = self.directory / 'messages'
            archive_dir.mkdir(exist_ok=True)
            with (archive_dir / f'{day}.jsonl').open('a', encoding='utf-8') as f:
                f.write(json.dumps(message, ensure_ascii=False) + '\n')
            return message

    def recent(self, limit=30):
        with self.lock:
            rows = self.db.execute('SELECT * FROM messages ORDER BY created DESC,sort_seq DESC,id DESC LIMIT ?', (limit,)).fetchall()
            return [dict(r) for r in reversed(rows)]

    def unsummarized(self, limit=80):
        with self.lock:
            last = self.db.execute('SELECT COALESCE(MAX(last_id),0) FROM summaries').fetchone()[0]
            return [dict(r) for r in self.db.execute(
                'SELECT * FROM messages WHERE id>? ORDER BY id LIMIT ?', (last, limit)).fetchall()]

    def sticker_messages(self):
        with self.lock:
            return [dict(r) for r in self.db.execute(
                "SELECT * FROM messages WHERE kind='动画表情' AND is_self=0 ORDER BY id DESC").fetchall()]

    def profiles(self, member_ids):
        with self.lock:
            profiles = []
            for mid in dict.fromkeys(member_ids):
                if not mid:
                    continue
                row = self.db.execute('SELECT * FROM members WHERE member_id=?', (mid,)).fetchone()
                if row:
                    p = dict(row)
                    p['observations'] = json.loads(p['observations'])
                    profiles.append(p)
            return profiles

    def latest_summary(self):
        with self.lock:
            row = self.db.execute('SELECT text FROM summaries ORDER BY id DESC LIMIT 1').fetchone()
            return row[0] if row else ''

    def save_memory(self, messages, result):
        """Reject unsupported attribution instead of attaching it to a member."""
        valid_ids = {m['id']: m for m in messages if m['member_id'] and not m['is_self']}
        with self.lock, self.db:
            for update in result['members']:
                mid = update['member_id']
                current = self.db.execute('SELECT observations FROM members WHERE member_id=?', (mid,)).fetchone()
                if current is None:
                    continue
                observations = json.loads(current[0])
                for item in update['observations']:
                    ids = item['evidence_ids']
                    if not ids or any(i not in valid_ids or valid_ids[i]['member_id'] != mid for i in ids):
                        continue
                    item = {'text': item['text'].strip(), 'evidence_ids': ids}
                    if item['text'] and item not in observations:
                        observations.append(item)
                self.db.execute('UPDATE members SET observations=?,updated=strftime(\'%s\',\'now\') WHERE member_id=?',
                                (json.dumps(observations, ensure_ascii=False), mid))
            self.db.execute('''INSERT INTO summaries(first_id,last_id,text,created)
                VALUES(?,?,?,strftime('%s','now'))''',
                (messages[0]['id'], messages[-1]['id'], result['summary']))
        self.export_notes()

    def record_outgoing(self, trigger_id, text, status):
        with self.lock, self.db:
            cur = self.db.execute('''INSERT INTO outbox(trigger_id,text,status,created)
                VALUES(?,?,?,strftime('%s','now'))''', (trigger_id, text, status))
            return cur.lastrowid

    def outgoing_status(self, outbox_id, status):
        with self.lock, self.db:
            self.db.execute('UPDATE outbox SET status=? WHERE id=?', (status, outbox_id))

    def export_notes(self):
        with self.lock:
            members = self.db.execute('SELECT * FROM members ORDER BY name').fetchall()
            summaries = self.db.execute('SELECT * FROM summaries ORDER BY id').fetchall()
        member_lines = ['# 群成员侧写', '', '只依据本地归档的发言；没有发言材料的成员不生成猜测。',
                        '以下为有限样本下的发言观察和自述记录，不代表已核实的经历或稳定性格、能力判断。', '']
        for m in members:
            member_lines.extend([f'## {m["name"]}', '', f'成员 ID：`{m["member_id"]}`', ''])
            observations = json.loads(m['observations'])
            if not observations:
                member_lines.append('材料不足，暂未形成侧写。')
            for item in observations:
                refs = '、'.join(f'#{i}' for i in item['evidence_ids'])
                member_lines.append(f'- {item["text"]}（来源消息 {refs}）')
            member_lines.append('')
        (self.directory / '成员侧写.md').write_text('\n'.join(member_lines), encoding='utf-8')
        lines = ['# 群聊归纳', '', '来源编号对应 archive.sqlite 和 messages/ 中的 id。',
                 '以下是群内发言的归纳；涉及行情、新闻、经历或图片内容的说法不等于已经外部核实。', '']
        for s in summaries:
            stamp = datetime.fromtimestamp(s['created']).strftime('%Y-%m-%d %H:%M')
            lines.extend([f'## {stamp} · 消息 #{s["first_id"]}—#{s["last_id"]}', '', s['text'], ''])
        (self.directory / '群聊归纳.md').write_text('\n'.join(lines), encoding='utf-8')
