"""Read only the selected group from local databases; send through the client UI."""
from pathlib import Path
import time
import xml.etree.ElementTree as ET

import zstandard
from wechatauto import WeChatDB
from group_names import group_member_names


class WeChatBridge:
    def __init__(self, config, root):
        self.config = config
        self.db = WeChatDB(account=config['account'],
                          workdir=str(Path(root) / '.runtime' / 'wechat-db'))
        if self.db.account != config['account']:
            raise RuntimeError('当前微信账号与配置不同')
        self.group = config['group_id']
        self.self_id = self.db.wxid
        self.refresh_members()
        self.gui = None
        self.last_send_timing = {}

    def refresh_members(self):
        members = self.db.get_group_members(self.group)
        connection = self.db._contact_conn()
        try:
            row = connection.execute('SELECT ext_buffer FROM chat_room WHERE username=?', (self.group,)).fetchone()
            aliases = group_member_names(row[0] if row else b'')
        finally:
            connection.close()
        self.members = [dict(m, group_name=aliases.get(m['username'], '')) for m in members]
        self.names = {m['username']: m['group_name'] or m.get('remark') or m.get('nick_name') or m['username']
                      for m in self.members}

    def normalize(self, row):
        member = row.get('sender_username') or ''
        content = row.get('content') or ''
        prefix, separator, body = content.partition(':\n')
        if separator and prefix in self.names:
            if not member:
                member = prefix
            if member == prefix:
                content = body
        at_ids = []
        if row['type'] == '文本':
            raw = self.db.get_message_row(self.group, row['local_id'], shard=row['shard'])
            source = raw.get('source') if raw else None
            if isinstance(source, bytes):
                if source.startswith(b'\x28\xb5\x2f\xfd'):
                    source = zstandard.ZstdDecompressor().decompress(source)
                source = source.decode('utf-8')
            if source and source.lstrip().startswith('<'):
                node = ET.fromstring(source)
                at_list = node.findtext('.//atuserlist') or ''
                at_ids = [s.strip() for s in at_list.split(',') if s.strip()]
        return {
            'source_key': f'{self.group}/{row["shard"]}/{row["local_id"]}',
            'member_id': member,
            'name': self.names.get(member, member or '未知发送者'),
            'created': row['create_time'], 'kind': row['type'], 'content': content,
            'is_self': member == self.self_id, 'is_at': self.self_id in at_ids,
            'at_ids': at_ids,
            'sort_seq': row['sort_seq'],
        }

    def history(self):
        rows, offset = [], 0
        while True:
            page = self.db.get_messages(self.group, limit=500, offset=offset)
            rows.extend(page)
            if len(page) < 500:
                break
            offset += len(page)
        return sorted(rows, key=lambda r: (r['sort_seq'], r['local_id']))

    def newer(self, seq):
        # WeChat can assign the same sort_seq to several rows. Re-read the last
        # value; the archive's real shard/local_id identity removes duplicates.
        return self.db.get_new_messages(self.group, since_seq=max(0, seq - 1), limit=200)

    def prepare_sender(self):
        import win32gui
        from wechatauto.guia import WeChatGUI
        from wechatauto import rhythm
        rhythm.set_profile('fast')
        # Bind the already-open group window when available: no search/navigation.
        hwnd = win32gui.FindWindow('mmui::ChatSingleWindow', self.config['group_name'])
        self.gui = None
        gui = WeChatGUI(hwnd=hwnd or None)
        if gui._get_db().account != self.config['account']:
            raise RuntimeError('发送窗口账号与监听账号不同')
        if gui._get_uia() is None:
            raise RuntimeError('微信 UIA 暂不可用')
        self.gui = gui

    def send(self, text, mention_ids=()):
        started = time.monotonic()
        if self.gui is None or not self.gui.is_alive():
            self.prepare_sender()
        # The main polling loop confirms the new self-message in our existing
        # account-bound DB reader, instead of constructing/querying another one.
        try:
            if mention_ids:
                from native_mentions import send_with_mentions
                self.refresh_members()
                names = [self.names[mid] for mid in dict.fromkeys(mention_ids)]
                return send_with_mentions(self.gui._get_uia(), names, text, self.config['group_name'])
            from native_input import send_text
            return send_text(self.gui._get_uia(), text, self.config['group_name'])
        finally:
            self.last_send_timing = {'ui_seconds': round(time.monotonic() - started, 3)}

    def send_sticker(self, entry):
        from native_stickers import send_original
        started = time.monotonic()
        if self.gui is None or not self.gui.is_alive():
            self.prepare_sender()
        try:
            return send_original(self.gui,entry['path'],self.config['group_name'])
        finally:
            self.last_send_timing = {'ui_seconds':round(time.monotonic()-started,3)}

    def favorite_ids(self):
        connection = self.db._open('emoticon\\emoticon.db')
        try:
            return {r[0] for r in connection.execute('SELECT md5 FROM kFavEmoticonOrderTable')}
        finally:
            connection.close()

    def add_latest_sticker(self, entry, message):
        if message['kind'] != '动画表情':
            return None  # Pasted static images have no Add to stickers menu.
        before = self.favorite_ids()
        for identity in (entry['id'],entry.get('wechat_id')):
            if identity in before:
                return identity
        rows = self.db.get_messages(self.group,limit=1)
        if not rows or self.normalize(rows[0])['source_key']!=message['source_key']:
            return None  # The group moved on; keep chatting instead of scrolling history.
        uia = self.gui._get_uia()
        if uia._right_click_latest_row(self.config['group_name']) is None:
            return None
        try:
            item = uia._uia_find_menu_item('添加')
            if item is None or '表情' not in item.Name:
                return None
            uia._click_ctrl(item)
            deadline = time.monotonic()+2
            while time.monotonic()<deadline:
                after = self.favorite_ids()
                if entry['id'] in after:
                    return entry['id']
                added = after-before
                if len(added)==1:
                    return added.pop()
                time.sleep(.15)
            return None
        finally:
            from native_input import keyboard_ready, send_keys
            edit = uia._chat_input()
            if edit is not None and keyboard_ready(uia, edit):
                send_keys(uia, edit, '{Esc}')
