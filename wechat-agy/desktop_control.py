"""Desktop controls use the same workspace and single-host lock as bot.py."""
import json
import msvcrt
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
from contextlib import closing

from app_paths import BUNDLE, ROOT, find_agy, initialize_workspace


def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except (FileNotFoundError, json.JSONDecodeError):
        return {} if default is None else default


def host_running(root=ROOT):
    path = Path(root) / '.runtime/host.lock'
    if not path.exists():
        return False
    with path.open('a+b') as lock:
        lock.seek(0)
        try:
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            return True
        msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
    return False


def snapshot(root=ROOT):
    root = Path(root)
    status = read_json(root / 'data/status.json')
    status['host_running'] = host_running(root)
    if not status['host_running']:
        status['state'] = 'error' if status.get('state') == 'error' else 'stopped'
    catalog = read_json(root / 'data/stickers/catalog.json')
    status['stickers_total'] = len(catalog)
    status['stickers_understood'] = sum(e.get('understanding') == 'clear' for e in catalog.values())
    status['stickers_favorited'] = sum(bool(e.get('added_to_wechat')) for e in catalog.values())
    return status, list(catalog.values())


def entry_command(root=ROOT, host=False):
    if getattr(sys, 'frozen', False):
        command = [sys.executable]
    else:
        interpreter = Path(sys.executable).with_name('pythonw.exe')
        command = [str(interpreter if interpreter.exists() else sys.executable), str(BUNDLE / 'desktop.py')]
    return command + (['--host'] if host else []) + ['--workspace', str(root)]


def start_host(root=ROOT):
    root = initialize_workspace(root)
    if host_running(root):
        return None
    if not read_json(root / 'data/config.json').get('group_id'):
        raise ValueError('请先在「连接设置」选择微信账号和群聊。')
    if not find_agy():
        raise ValueError('未找到 AGY CLI。请先安装并登录，再启动。')
    with (root / '.runtime/launcher.log').open('a', encoding='utf-8') as log:
        return subprocess.Popen(entry_command(root, host=True), cwd=root,
            stdout=log, stderr=log, creationflags=subprocess.CREATE_NO_WINDOW)


def stop_host(root=ROOT):
    initialize_workspace(root)
    (Path(root) / '.runtime/stop').write_text('stop', encoding='ascii')


def save_config(root, config):
    root = Path(root)
    if host_running(root):
        raise ValueError('请先停止后台，再修改连接设置。')
    for field in ('account', 'group_id', 'group_name'):
        if not isinstance(config.get(field), str) or not config[field].strip():
            raise ValueError('请选择实际检测到的微信账号和群聊。')
    if not config['group_id'].endswith('@chatroom'):
        raise ValueError('目标必须是微信群聊。')
    previous = read_json(root / 'data/config.json')
    archive = root / 'data/archive.sqlite'
    if previous and archive.exists() and any(previous.get(k) != config[k] for k in ('account', 'group_id')):
        with closing(sqlite3.connect(archive)) as db:
            populated = db.execute('SELECT EXISTS(SELECT 1 FROM messages)').fetchone()[0]
        if populated:
            raise ValueError('这个资料目录已有群聊档案。换账号或群聊时，请选择新的空资料目录，避免混入原有记忆。')
    (root / 'data/config.json').write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding='utf-8')


def accounts():
    from wechatauto.db import list_accounts
    return list_accounts()


def groups(account, root=ROOT):
    from wechatauto import WeChatDB
    db = WeChatDB(account=account, workdir=str(Path(root) / '.runtime/wechat-db'))
    if db.account != account:
        raise RuntimeError('读取到的账号与所选账号不同，请确认对应微信已登录。')
    return sorted(db.get_groups(), key=lambda g: g.get('name', ''))


def dependencies():
    return {'agy': find_agy(), 'codex': shutil.which('codex')}
