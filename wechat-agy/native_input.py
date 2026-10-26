"""Target-bound text submission; keyboard actions stop if WeChat loses focus."""
import win32gui
import uiautomation as auto
from wechatauto import rhythm
from wechatauto.uia_driver import _find_by


class InputFocusLost(RuntimeError):
    pass


def chat_input(uia, group):
    if uia is None or not uia.ensure_window(wake=False):
        raise RuntimeError('微信输入控件不可用，未发送')
    if uia.current_chat() != group:
        raise RuntimeError('请先在微信打开目标群，推荐保留群聊独立窗口；未向其他会话输入')
    edit = uia._chat_input()
    if edit is None:
        raise RuntimeError('未找到目标群的输入框')
    return edit


def keyboard_ready(uia, edit):
    try:
        expected = win32gui.GetAncestor(uia._win.NativeWindowHandle, 2)
        foreground = win32gui.GetAncestor(win32gui.GetForegroundWindow(), 2)
        return bool(expected and expected == foreground and edit.HasKeyboardFocus)
    except Exception:
        return False


def focus_editor(uia, edit):
    uia._activate(uia._win)
    edit.SetFocus()
    if not keyboard_ready(uia, edit):
        raise InputFocusLost('微信输入框未获得焦点，已取消键盘操作')


def send_keys(uia, edit, keys):
    if not keyboard_ready(uia, edit):
        raise InputFocusLost('焦点离开微信，已取消键盘操作')
    # Control.SendKeys silently calls SetFocus and still types when it fails.
    # Use the module function only after verifying the actual foreground/editor.
    auto.SendKeys(keys, interval=.005, waitTime=.03)


def set_text(uia, edit, text):
    if not uia._set_text(edit, text):
        raise RuntimeError('微信输入框不支持直接写入，未使用全局按键回退')


def submit(uia, group):
    if uia.current_chat() != group:
        raise RuntimeError('目标会话已改变，未提交')
    button = _find_by(uia._win, lambda c: c.ControlTypeName == 'ButtonControl' and c.Name == '发送')
    if button is None or not button.IsEnabled:
        raise RuntimeError('微信发送按钮不可用，未提交')
    rhythm.gate('send')
    # This WeChat build exposes Invoke/DoDefaultAction but neither sends.
    # Clicking its bound button avoids a global Enter in another application.
    uia._activate(uia._win)
    if win32gui.GetAncestor(win32gui.GetForegroundWindow(), 2) != win32gui.GetAncestor(uia._win.NativeWindowHandle, 2):
        raise InputFocusLost('微信未成为前台窗口，未点击发送')
    if not uia._click_ctrl(button):
        raise RuntimeError('未能点击微信发送按钮')


def send_text(uia, text, group):
    edit = chat_input(uia, group)
    set_text(uia, edit, text)
    if uia._compare_norm(uia._read_edit_value(edit) or '') != uia._compare_norm(text):
        raise RuntimeError('微信输入框内容与待发正文不一致，未提交')
    submit(uia, group)
    return True, '已通过微信控件提交正文，等待回读'
