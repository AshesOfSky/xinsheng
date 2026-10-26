"""DaFeiYu desktop and frozen host entry point."""
import argparse
import json
import os
from pathlib import Path
import sys


def main():
    parser = argparse.ArgumentParser(description='大肥鱼 · 微信群聊助手')
    parser.add_argument('--workspace', type=Path, help='使用现有资料目录')
    parser.add_argument('--host', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--self-test', type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.workspace:
        os.environ['DAFEIYU_HOME'] = str(args.workspace.resolve())
    from app_paths import ROOT, VERSION, initialize_workspace
    initialize_workspace()
    if sys.stdout is None:
        sys.stdout = (ROOT / '.runtime/desktop.log').open('a', encoding='utf-8', buffering=1)
    if sys.stderr is None:
        sys.stderr = sys.stdout
    if args.self_test:
        import tkinter as tk
        from PIL import Image, ImageTk
        from wechatauto import WeChatDB
        from wechatauto.guia import WeChatGUI
        import winsdk.windows.media.ocr
        import winsdk.windows.graphics.imaging
        import winsdk.windows.storage
        import bot
        from desktop_control import snapshot
        from desktop_ui import Desktop
        window = Desktop()
        window.withdraw()
        icon = ImageTk.PhotoImage(Image.new('RGB', (12, 12), '#2879d0'))
        tk.Label(window, image=icon, text='大肥鱼😀').pack()
        window.update()
        window.destroy()
        result = {'ok': True, 'version': VERSION, 'frozen': bool(getattr(sys, 'frozen', False)),
                  'agents': len(list((ROOT / '.agents/agents').glob('*.md'))),
                  'persona': (ROOT / 'PERSONA.md').exists(), 'state': snapshot()[0]['state']}
        args.self_test.write_text(json.dumps(result, ensure_ascii=False), encoding='utf-8')
        return
    if args.host:
        import bot
        sys.argv = [sys.argv[0]]
        bot.main()
    else:
        from desktop_ui import Desktop
        Desktop().mainloop()


if __name__ == '__main__':
    main()
