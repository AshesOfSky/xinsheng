"""Windows 11 微信连接验证；不发送消息、不调用模型。"""
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time

VERSION = '0.1.0'


def event_record(event):
    return {
        'kind': 'message', 'group': event.group, 'content': event.content,
        'timestamp': event.timestamp, 'is_at_me': event.is_at_me,
        'group_nickname': event.group_nickname,
        'sender': None, 'sender_status': 'unsupported',
        'manual_self_detection': 'unsupported',
    }


def emit(record):
    # Frozen Windows workers may use GBK despite PYTHONIOENCODING.
    print(json.dumps(record, ensure_ascii=True), flush=True)


def worker(group, nickname):
    # wx4py also writes human-readable logs to stdout.
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
    from wx4py import CallbackHandler, WeChatClient
    def received(event):
        emit(event_record(event))
        return None
    try:
        with WeChatClient() as wx:
            emit({'kind': 'status', 'text': '已连接微信窗口；正在打开指定群。尚未验证收到新消息。'})
            wx.process_groups([group], [CallbackHandler(received, auto_reply=False)],
                              group_nicknames={group: nickname}, block=True)
    except Exception as exc:
        emit({'kind': 'error', 'text': str(exc)})
        return 1
    return 0


def main():
    import tkinter as tk
    from tkinter import ttk, messagebox, filedialog
    root = tk.Tk()
    root.title('微信群聊连接测试 0.1.0')
    root.geometry('840x620')
    root.minsize(760, 560)
    body = ttk.Frame(root, padding=18)
    body.pack(fill='both', expand=True)
    ttk.Label(body, text='微信连接测试 · Windows 11', font=('Microsoft YaHei', 17, 'bold')).pack(anchor='w')
    ttk.Label(body, text='先登录微信小号，再填写唯一的测试群名称及小号在群里的昵称。\n只读取指定群；不会发消息，也不会调用 AI。', wraplength=780).pack(anchor='w', pady=10)
    form = ttk.Frame(body)
    form.pack(fill='x')
    group = tk.StringVar()
    nickname = tk.StringVar()
    for row, (label, variable) in enumerate([('测试群完整名称', group), ('小号在群里的昵称', nickname)]):
        ttk.Label(form, text=label).grid(row=row, column=0, sticky='w', pady=6)
        ttk.Entry(form, textvariable=variable, width=55).grid(row=row, column=1, sticky='ew', padx=10)
    form.columnconfigure(1, weight=1)
    ttk.Label(body, text='已知限制：发送者及手动发送方向不可识别；@ 为文本匹配结果。\n连接库可能调整 Windows 辅助功能设置并重启微信；会打开群窗口。请在专用电脑运行。', wraplength=780, foreground='#9a3412').pack(anchor='w', pady=12)
    state = tk.StringVar(value='未启动')
    ttk.Label(body, textvariable=state).pack(anchor='w')
    output = tk.Text(body, height=15, wrap='word', font=('Microsoft YaHei', 10))
    output.pack(fill='both', expand=True, pady=8)
    output.configure(state='disabled')
    events = queue.Queue()
    proc = None
    records = []
    buttons = ttk.Frame(body)
    buttons.pack(fill='x')

    def show(text):
        output.configure(state='normal')
        output.insert('end', text + '\n')
        output.see('end')
        output.configure(state='disabled')

    def collect(child):
        for line in child.stdout:
            events.put(line.rstrip())
        events.put({'ended': child.wait()})

    def start():
        nonlocal proc
        if proc is not None:
            return
        if not group.get().strip() or not nickname.get().strip():
            messagebox.showinfo('填写测试信息', '请填写群完整名称及群昵称。')
            return
        if not messagebox.askokcancel('开始连接测试', '已在这台电脑登录测试小号吗？\n连接库可能调整辅助功能设置、重启微信并打开群窗口。\n请避免同名群；本工具不会发送任何消息。'):
            return
        records.clear()
        args = [sys.executable]
        if not getattr(sys, 'frozen', False):
            args.append(str(Path(__file__).resolve()))
        args += ['--worker', group.get().strip(), nickname.get().strip()]
        env = dict(os.environ, PYTHONIOENCODING='utf-8')
        try:
            proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    text=True, encoding='utf-8', errors='replace', env=env,
                                    creationflags=subprocess.CREATE_NO_WINDOW)
        except OSError as exc:
            messagebox.showerror('启动失败', str(exc))
            return
        start_button.configure(state='disabled')
        state.set('连接中；收到测试消息才算接收链路验证成功')
        threading.Thread(target=collect, args=(proc,), daemon=True).start()

    def stop():
        if proc is not None and proc.poll() is None:
            proc.terminate()
            state.set('正在停止监听…')

    def poll():
        nonlocal proc
        try:
            while True:
                item = events.get_nowait()
                if isinstance(item, dict):
                    proc = None
                    state.set('监听已结束；可重新开始')
                    start_button.configure(state='normal')
                    show('进程退出码：' + str(item['ended']))
                    continue
                try:
                    record = json.loads(item)
                except ValueError:
                    show(item)
                    continue
                show(json.dumps(record, ensure_ascii=False))
                if isinstance(record, dict):
                    records.append(record)
                    if record.get('kind') == 'message':
                        state.set('已收到群消息；请核对群名、内容及 @ 结果。发送者仍不支持。')
                    elif record.get('kind') == 'error':
                        state.set('连接失败；请查看下方错误')
        except queue.Empty:
            pass
        root.after(150, poll)

    def export():
        path = filedialog.asksaveasfilename(title='保存本地诊断结果（含测试群消息）', defaultextension='.json', initialfile='wechat-probe.json')
        if path:
            try:
                Path(path).write_text(json.dumps({'version': VERSION, 'exported_at': time.time(),
                    'sender_support': False, 'manual_self_detection': False,
                    'send_test': 'not_implemented', 'events': records}, ensure_ascii=False, indent=2), encoding='utf-8')
                show('诊断已保存到本地。文件包含群名及测试消息，请勿上传公开仓库。')
            except OSError as exc:
                messagebox.showerror('保存失败', str(exc))

    def close():
        stop()
        root.destroy()

    start_button = ttk.Button(buttons, text='开始只读测试', command=start)
    start_button.pack(side='left')
    ttk.Button(buttons, text='停止监听', command=stop).pack(side='left', padx=8)
    ttk.Button(buttons, text='保存诊断结果', command=export).pack(side='right')
    root.protocol('WM_DELETE_WINDOW', close)
    poll()
    root.mainloop()


if __name__ == '__main__':
    if '--self-test' in sys.argv:
        from wx4py import CallbackHandler
        from types import SimpleNamespace
        example = SimpleNamespace(group='测试群', content='你好😀\u2005第二行\n结束', timestamp=0,
                                  group_nickname='测试小号', is_at_me=False)
        assert CallbackHandler(lambda event: None, auto_reply=False).handle(example) is None
        emit(event_record(example))
        raise SystemExit(0)
    if '--worker' in sys.argv:
        raise SystemExit(worker(sys.argv[2], sys.argv[3]))
    main()
