"""Target-bound private file submission; the caller owns durable job state.

An ``unconfirmed`` result must never be automatically retried: a paste or click
may have succeeded even when the next operation failed.  Database confirmation
means a matching outgoing message was recorded locally, not remote delivery.
"""
from dataclasses import dataclass
from pathlib import Path
import time
import xml.etree.ElementTree as ET

from native_input import chat_input, editor_content_children, editor_empty, focus_editor, send_keys, submit
from private_host import PrivateTargetUnavailable, file_send_hold


@dataclass(frozen=True)
class FileSendResult:
    status: str  # not_attempted, unconfirmed, confirmed
    detail: str


def _file_draft_visible(edit, filename):
    """Only accept a single accessible draft whose name is the exact file name."""
    children = editor_content_children(edit)
    if len(children) != 1:
        return False
    def names(control, depth):
        yield (control.Name or '').strip()
        if depth:
            for child in control.GetChildren():
                yield from names(child, depth - 1)
    return filename in names(children[0], 4)


def _file_message(row, filename):
    if row.get('type') != '文件/链接/卡片':
        return False
    try:
        root = ET.fromstring(row.get('content') or '')
        app = root if root.tag == 'appmsg' else root.find('.//appmsg')
        return (app is not None and (app.findtext('type') or '').strip() == '6'
                and (app.findtext('title') or '').strip() == filename)
    except (ET.ParseError, TypeError):
        return False


def confirm_private_file(chat, filename, previous_keys, started):
    """Confirm one new self-authored file record in this exact wxid's DB."""
    for row in chat.db.get_messages(chat.contact_id, limit=50):
        key = (row.get('shard'), row.get('local_id'))
        if (key not in previous_keys and row.get('sender_username') == chat.self_id
                and row.get('create_time', 0) >= started - 2
                and _file_message(row, filename)):
            return True
    return False


def send_private_file(bot, chat, contact, path, timeout=10):
    """Submit one file under the same lock as group and private text sends.

    The caller must persist a possibly-sent state *before* calling this method
    and must not retry an ``unconfirmed`` result.  The input tree needs to
    expose the attachment's exact filename; otherwise submission stops.
    """
    import win32gui
    from wechatauto.guia import WeChatGUI

    path = Path(path).resolve()
    if not path.is_file():
        return FileSendResult('not_attempted', '待发送文件不存在')
    if path.suffix.lower() in {'.png', '.jpg', '.jpeg', '.gif', '.bmp', '.webp'}:
        return FileSendResult('not_attempted', '图片会作为图片消息发送，文件通道不支持')
    if contact['id'] != chat.contact_id or bot.bridge.config['account'] != chat.db.account:
        return FileSendResult('not_attempted', '私聊对象或账号不匹配')
    attempted = False
    with bot.send_lock:
        try:
            hold = file_send_hold(bot, contact)
            if hold.exists():
                raise PrivateTargetUnavailable('文件发送状态待核，请先检查私聊草稿和消息')
            # Snapshot before touching input; inability to confirm is not permission
            # to guess which conversation or previous message was ours.
            previous = chat.db.get_messages(chat.contact_id, limit=50)
            previous_keys = {(row.get('shard'), row.get('local_id')) for row in previous}
            hwnd = win32gui.FindWindow(None, contact['name'])
            if not hwnd:
                raise PrivateTargetUnavailable('目标私聊独立窗口未打开')
            gui = WeChatGUI(hwnd=hwnd)
            if gui._get_db().account != bot.bridge.config['account']:
                raise PrivateTargetUnavailable('私聊窗口账号与监听账号不同')
            uia = gui._get_uia()
            if uia is None or uia._win.AutomationId != 'ChatSingleWindow' + contact['id']:
                raise PrivateTargetUnavailable('私聊窗口身份与指定微信 ID 不一致')
            edit = chat_input(uia, contact['name'])
            if not editor_empty(uia, edit):
                raise PrivateTargetUnavailable('私聊输入框已有草稿或无法确认草稿为空')
            focus_editor(uia, edit)
            if not gui.copy_files_to_clipboard([str(path)]):
                raise PrivateTargetUnavailable('无法准备文件剪贴板')
            # Persist before paste so a process stop cannot let a later text
            # reply submit an uncertain attachment draft.
            hold.parent.mkdir(parents=True, exist_ok=True)
            hold.touch(exist_ok=False)
            attempted = True
            send_keys(uia, edit, '{Ctrl}v')
            time.sleep(1)
            if uia.current_chat() != contact['name'] or not _file_draft_visible(edit, path.name):
                return FileSendResult('unconfirmed', '文件草稿无法识别，未点击发送；请检查微信输入框')
            started = time.time()
            submit(uia, contact['name'])
        except Exception as exc:
            return FileSendResult('unconfirmed' if attempted else 'not_attempted', str(exc))
    deadline = time.monotonic() + max(0, timeout)
    while True:
        try:
            if confirm_private_file(chat, path.name, previous_keys, started):
                with bot.send_lock:
                    hold.unlink()
                return FileSendResult('confirmed', '目标私聊已记录同名文件消息')
        except Exception:
            return FileSendResult('unconfirmed', '文件已提交，但消息数据库回读失败')
        if time.monotonic() >= deadline:
            return FileSendResult('unconfirmed', '文件已提交，但未回读到对应消息')
        time.sleep(min(.5, max(0, deadline - time.monotonic())))
