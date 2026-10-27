import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from native_input import InputFocusLost, editor_empty, send_keys, send_text, submit


class InputTests(unittest.TestCase):
    def test_editor_empty_requires_readable_value_and_empty_observed_viewport(self):
        viewport = SimpleNamespace(AutomationId='chat_input_field.qt_scrollarea_viewport',
                                   GetChildren=lambda: [])
        edit = SimpleNamespace(GetChildren=lambda: [viewport])
        uia = SimpleNamespace(_read_edit_value=lambda _: '')
        self.assertTrue(editor_empty(uia, edit))
        viewport.GetChildren = lambda: [SimpleNamespace(Name='附件.xlsx')]
        self.assertFalse(editor_empty(uia, edit))
        viewport.GetChildren = lambda: []
        uia._read_edit_value = lambda _: None
        self.assertFalse(editor_empty(uia, edit))
        uia._read_edit_value = lambda _: '已有文字'
        self.assertFalse(editor_empty(uia, edit))

    def test_unrecognized_or_unreadable_input_tree_rejects_send(self):
        uia = SimpleNamespace(_read_edit_value=lambda _: '')
        with self.assertRaises(RuntimeError):
            editor_empty(uia, SimpleNamespace(GetChildren=lambda: []))
        def failed():
            raise RuntimeError('UIA 失败')
        with self.assertRaises(RuntimeError):
            editor_empty(uia, SimpleNamespace(GetChildren=failed))

    def test_focus_lost_does_not_type_or_attempt_refocus(self):
        edit = Mock()
        with patch('native_input.keyboard_ready', return_value=False), patch('native_input.auto.SendKeys') as keys:
            with self.assertRaises(InputFocusLost):
                send_keys(Mock(), edit, '{Ctrl}v')
        keys.assert_not_called()
        edit.SetFocus.assert_not_called()

    def test_text_uses_target_value_pattern_without_global_keyboard(self):
        uia = Mock()
        uia._read_edit_value.return_value = '你好😀'
        uia._compare_norm.side_effect = lambda t:t
        edit = Mock()
        with patch('native_input.chat_input', return_value=edit), \
                patch('native_input.submit') as clicked, patch('native_input.auto.SendKeys') as keys:
            self.assertTrue(send_text(uia, '你好😀', '测试群')[0])
        uia._set_text.assert_called_once_with(edit, '你好😀')
        keys.assert_not_called()
        clicked.assert_called_once_with(uia, '测试群')

    def test_failed_foreground_activation_does_not_click_send(self):
        uia = Mock()
        uia.current_chat.return_value = '测试群'
        uia._win.NativeWindowHandle = 100
        with patch('native_input._find_by', return_value=SimpleNamespace(IsEnabled=True)), \
                patch('native_input.rhythm.gate'), \
                patch('native_input.win32gui.GetForegroundWindow', return_value=200), \
                patch('native_input.win32gui.GetAncestor', side_effect=lambda h,f:h):
            with self.assertRaises(InputFocusLost):submit(uia, '测试群')
        uia._click_ctrl.assert_not_called()

    def test_changed_conversation_does_not_submit(self):
        uia = Mock()
        uia.current_chat.return_value = '另一个群'
        with self.assertRaisesRegex(RuntimeError, '目标会话'):
            submit(uia, '测试群')
        uia._click_ctrl.assert_not_called()


if __name__ == '__main__':unittest.main()
