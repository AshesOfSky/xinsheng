"""Select real WeChat mention tokens and append text without replacing them."""
import time

from wechatauto import rhythm
from native_input import chat_input, focus_editor, send_keys, set_text, submit
from wechatauto.uia_driver import _find_by


def send_with_mentions(uia, names, text, group):
    edit = chat_input(uia, group)
    set_text(uia, edit, '')
    focus_editor(uia, edit)
    submitted = False
    try:
        for name in names:
            send_keys(uia, edit, '@')
            popup = uia._win.WindowControl(AutomationId='MentionPopover', searchDepth=2)
            if not popup.Exists(1.5, 0.1):
                return False, '微信 @ 选择器未出现'
            # Paste only the filter, retaining the typed @ that opened the picker.
            uia._clip_set(name)
            send_keys(uia, edit, '{Ctrl}v')
            deadline = time.monotonic() + 1.5
            matches = []
            while time.monotonic() < deadline:
                member_list = _find_by(popup, lambda c: c.AutomationId == 'chat_mention_list')
                matches = [c for c in member_list.GetChildren() if c.Name == name] if member_list else []
                if matches:
                    break
                time.sleep(0.1)
            if len(matches) != 1:
                return False, f'未在 @ 选择器中唯一找到：{name}'
            uia._click_ctrl(matches[0])
            time.sleep(0.1)
        prefix = uia._read_edit_value(edit) or ''
        if not prefix:
            return False, '选择成员后输入框为空'
        # Do not use input_text/send_text here: they clear the native @ tokens.
        uia._clip_set(text)
        send_keys(uia, edit, '{Ctrl}{End}{Ctrl}v')
        expected = prefix + text
        got = uia._read_edit_value(edit) or ''
        if uia._compare_norm(got) != uia._compare_norm(expected):
            return False, '@ 或正文未完整进入输入框'
        submitted = True
        submit(uia, group)
        return True, '已提交真实 @，等待数据库确认'
    finally:
        if not submitted:
            set_text(uia, edit, '')


def outgoing_matches(message, pending):
    if not message['is_self'] or message['id'] <= pending['after_id']:
        return False
    targets = pending.get('mention_ids', [])
    if targets:
        return (set(targets).issubset(message.get('at_ids', []))
                and message['content'].endswith(pending['text']))
    return message['content'] == pending['text']
