"""Paste an original sticker as media, without the library's color/OCR waits."""
import time
from wechatauto import rhythm
from stickers import sticker_id
from native_input import chat_input, focus_editor, send_keys, set_text, submit


def send_original(gui, path, group):
    uia = gui._get_uia()
    edit = chat_input(uia, group)
    set_text(uia, edit, '')
    focus_editor(uia, edit)
    if not gui.copy_files_to_clipboard([str(path)]):
        return False, '原图未放入剪贴板'
    send_keys(uia, edit, '{Ctrl}v')
    deadline = time.monotonic()+3
    while time.monotonic()<deadline:
        if '\ufffc' in (uia._read_edit_value(edit) or ''):
            submit(uia, group)
            return True, '已提交原表情图片，等待回读'
        time.sleep(.1)
    return False, '未读到图片草稿，未按发送'


def media_matches(message, pending):
    if not message['is_self'] or message['id'] <= pending['after_id']:
        return False
    if message['kind']=='动画表情':
        return sticker_id(message) in (pending['sticker_id'],pending.get('wechat_id'))
    # JPEG/PNG are uploaded by WeChat as image messages; media is serialized by
    # the same send lock, and the new own image confirms this clipboard submit.
    return message['kind']=='图片' and pending.get('image_format')!='.gif'
