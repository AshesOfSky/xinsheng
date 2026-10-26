"""Verify the selected group's identities without printing messages or keys."""
import json
from pathlib import Path
import time
import argparse
from app_paths import ROOT, initialize_workspace

from wechatauto import WeChatDB

initialize_workspace()
root = ROOT
parser = argparse.ArgumentParser()
parser.add_argument('--group', help='目标群的准确名称；默认使用本地配置')
parser.add_argument('--account', help='微信账号目录名；默认使用本地配置')
args = parser.parse_args()
config_path = root / 'data/config.json'
config = json.loads(config_path.read_text(encoding='utf-8')) if config_path.exists() else {}
name = args.group or config.get('group_name')
if not name:
    parser.error('请先在桌面客户端选择群聊，或通过 --group 指定群名。')
work = root / '.runtime' / 'wechat-db'
work.mkdir(parents=True, exist_ok=True)
started = time.monotonic()
db = WeChatDB(account=args.account or config.get('account'), workdir=str(work))
self_info = db.get_self_info()
groups = [g for g in db.search_contact(name)
          if g['username'].endswith('@chatroom') and
          name in (g.get('nick_name'), g.get('remark'))]
if len(groups) != 1:
    raise RuntimeError(f'目标群匹配数为 {len(groups)}，未选择其他群')
group = groups[0]
rows = db.get_messages(group['username'], limit=200)
members = db.get_group_members(group['username'])
data = root / 'data'
data.mkdir(exist_ok=True)
(data / 'database-check.json').write_text(json.dumps({
    'account': db.account, 'self': self_info, 'group': group,
    'members': members, 'messages': rows,
}, ensure_ascii=False, indent=2), encoding='utf-8')
latest = max((r['sort_seq'] for r in rows), default=0)
poll_start = time.monotonic()
db.get_new_messages(group['username'], since_seq=latest)
print(json.dumps({
    'self_name': self_info.get('nick_name'), 'group_found': True,
    'messages': len(rows), 'members': len(members),
    'identified': sum(bool(r.get('sender_username')) for r in rows),
    'self_messages': sum(r.get('sender_username') == db.wxid for r in rows),
    'fields': sorted(rows[0]) if rows else [],
    'startup_seconds': round(poll_start - started, 3),
    'incremental_poll_seconds': round(time.monotonic() - poll_start, 3),
}, ensure_ascii=True), flush=True)
