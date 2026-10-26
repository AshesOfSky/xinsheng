"""A small native control panel for the existing DaFeiYu host."""
from datetime import datetime
import os
from pathlib import Path
import queue
import shutil
import subprocess
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import webbrowser

from PIL import Image, ImageTk
from app_paths import ROOT, VERSION, BUNDLE
from desktop_control import (accounts, dependencies, entry_command, groups, host_running,
                             read_json, save_config, snapshot, start_host, stop_host)

BG = '#f0f5fa'
INK = '#17324d'
MUTED = '#62778a'
BLUE = '#247cce'


class Desktop(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f'大肥鱼 · 微信群聊助手  {VERSION}')
        width, height = min(1040,self.winfo_screenwidth()-80), min(780,self.winfo_screenheight()-100)
        self.geometry(f'{width}x{height}+40+30')
        self.minsize(min(900,width), min(680,height))
        icon = BUNDLE/'assets/fish.ico'
        if icon.exists():self.iconbitmap(str(icon))
        self.configure(bg=BG)
        self.events = queue.Queue()
        self.launcher = None
        self.catalog = {}
        self.preview_image = None
        self.animation = None
        self.animation_path = None
        self.group_map = {}
        self.current_config = read_json(ROOT / 'data/config.json')
        self._styles()
        header = tk.Frame(self, bg=INK, padx=24, pady=15)
        header.pack(fill='x')
        fish = tk.Canvas(header, width=82, height=60, bg=INK, highlightthickness=0)
        fish.pack(side='left', padx=(0, 16))
        fish.create_polygon(8, 30, 1, 12, 1, 48, fill='#58b7f1', outline='')
        fish.create_oval(9, 6, 78, 55, fill='#61baff', outline='')
        fish.create_oval(56, 18, 65, 27, fill='white', outline='')
        fish.create_oval(60, 20, 64, 25, fill=INK, outline='')
        fish.create_arc(45, 23, 67, 43, start=210, extent=100, style='arc', width=2, outline=INK)
        words = tk.Frame(header, bg=INK)
        words.pack(side='left')
        tk.Label(words, text='大肥鱼', fg='white', bg=INK, font=('Microsoft YaHei UI', 22, 'bold')).pack(anchor='w')
        tk.Label(words, text='接得住话，也记得住梗。', fg='#a9c5dc', bg=INK, font=('Microsoft YaHei UI', 10)).pack(anchor='w')
        tk.Label(header, text=f'WINDOWS  /  {VERSION}', fg='#a9c5dc', bg=INK, font=('Segoe UI', 10)).pack(side='right')
        self.tabs = ttk.Notebook(self)
        self.tabs.pack(fill='both', expand=True, padx=20, pady=(18, 12))
        self.home = self._tab('  运行概览  ')
        self.sticker_tab = self._tab('  表情字典  ')
        self.settings = self._tab('  连接设置  ')
        self.notes = self._tab('  档案与人格  ')
        self._home()
        self._stickers()
        self._settings()
        self._notes()
        footer = tk.Frame(self, bg=BG)
        footer.pack(fill='x', padx=24, pady=(0, 12))
        tk.Label(footer, text='关闭窗口后，后台会继续运行。停止回复请点「停止后台」。', bg=BG, fg=MUTED,
                 font=('Microsoft YaHei UI', 9)).pack(side='left')
        ttk.Button(footer, text='资料目录', command=lambda:self.open_path(ROOT)).pack(side='right')
        self._poll()
        self._animate()

    def _styles(self):
        style = ttk.Style(self)
        style.theme_use('clam')
        style.configure('.', font=('Microsoft YaHei UI', 10))
        style.configure('TFrame', background='white')
        style.configure('TLabel', background='white', foreground=INK)
        style.configure('Muted.TLabel', foreground=MUTED)
        style.configure('Title.TLabel', font=('Microsoft YaHei UI', 17, 'bold'))
        style.configure('TButton', padding=(13, 8), background='#eaf2f9', borderwidth=0)
        style.map('TButton', background=[('active', '#d9eaf7')])
        style.configure('Primary.TButton', background=BLUE, foreground='white')
        style.map('Primary.TButton', background=[('active', '#1767b2'), ('disabled', '#a7bcd0')])
        style.configure('TNotebook', background=BG, borderwidth=0)
        style.configure('TNotebook.Tab', padding=(12, 10), background='#e3ecf4')
        style.map('TNotebook.Tab', background=[('selected', 'white')], foreground=[('selected', BLUE)])
        style.configure('Treeview', rowheight=34, background='white', fieldbackground='white', borderwidth=0)
        style.configure('Treeview.Heading', padding=(6, 8), font=('Microsoft YaHei UI', 10, 'bold'))

    def _tab(self, title):
        frame = ttk.Frame(self.tabs, padding=22)
        self.tabs.add(frame, text=title)
        return frame

    def _home(self):
        top = ttk.Frame(self.home)
        top.pack(fill='x')
        self.state_label = ttk.Label(top, text='正在读取状态…', style='Title.TLabel')
        self.state_label.pack(side='left')
        self.stop_button = ttk.Button(top, text='停止后台', command=self.stop)
        self.stop_button.pack(side='right')
        self.start_button = ttk.Button(top, text='启动大肥鱼', style='Primary.TButton', command=self.start)
        self.start_button.pack(side='right', padx=10)
        self.group_label = ttk.Label(self.home, text='', style='Muted.TLabel')
        self.group_label.pack(anchor='w', pady=(10, 18))
        metrics = ttk.Frame(self.home)
        metrics.pack(fill='x')
        self.metric_labels = []
        for title in ('日常接话', '认真问题与记忆', '已理解的表情', '已加入微信表情'):
            card = tk.Frame(metrics, bg=BG, padx=16, pady=15)
            card.pack(side='left', fill='both', expand=True, padx=(0, 10))
            tk.Label(card, text=title, bg=BG, fg=MUTED, font=('Microsoft YaHei UI', 10)).pack(anchor='w')
            label = tk.Label(card, text='—', bg=BG, fg=INK, font=('Segoe UI', 17, 'bold'))
            label.pack(anchor='w', pady=(6, 0))
            self.metric_labels.append(label)
        ttk.Label(self.home, text='最近回复', font=('Microsoft YaHei UI', 12, 'bold')).pack(anchor='w', pady=(23, 9))
        self.last_reply = tk.Text(self.home, height=5, wrap='word', font=('Microsoft YaHei UI', 12),
                                  bg='#f8fafc', fg=INK, relief='flat', padx=14, pady=12, state='disabled')
        self.last_reply.pack(fill='x')
        self.details = ttk.Label(self.home, text='', style='Muted.TLabel', wraplength=880)
        self.details.pack(anchor='w', pady=(12, 8))
        self.error_label = ttk.Label(self.home, text='', foreground='#b24836', wraplength=880)
        self.error_label.pack(anchor='w', pady=(3, 8))
        ttk.Separator(self.home).pack(fill='x', pady=12)
        ttk.Label(self.home, text='工作方式', font=('Microsoft YaHei UI', 11, 'bold')).pack(anchor='w')
        ttk.Label(self.home, text='真实 @ 优先回应；普通消息顺着话题接话。认真问题后台查证，完成后 @ 原提问者。\n'
            '新表情自动收集和理解；不明确时交给 Codex 识图。群聊归纳与成员记忆保存在本机。',
            style='Muted.TLabel', justify='left', wraplength=880).pack(anchor='w', pady=8)

    def _stickers(self):
        toolbar = ttk.Frame(self.sticker_tab)
        toolbar.pack(fill='x', pady=(0, 14))
        ttk.Label(toolbar, text='学到的表情，都有来处。', style='Title.TLabel').pack(side='left')
        ttk.Button(toolbar, text='打开图文词典', command=lambda:self.open_path(ROOT/'data/stickers/index.html')).pack(side='right')
        content = ttk.Frame(self.sticker_tab)
        content.pack(fill='both', expand=True)
        listing = ttk.Frame(content)
        listing.pack(side='left', fill='both', expand=True, padx=(0, 20))
        self.tree = ttk.Treeview(listing, columns=('code', 'tags', 'state'), show='headings', selectmode='browse')
        for key, label, width in (('code','编号',75),('tags','含义标签',220),('state','状态',100)):
            self.tree.heading(key, text=label)
            self.tree.column(key, width=width, stretch=key=='tags')
        scroll = ttk.Scrollbar(listing, orient='vertical', command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side='right', fill='y')
        self.tree.pack(fill='both', expand=True)
        self.tree.bind('<<TreeviewSelect>>', self.select_sticker)
        preview = ttk.Frame(content, width=340)
        preview.pack(side='right', fill='both')
        preview.pack_propagate(False)
        self.preview_label = ttk.Label(preview, text='选择一张表情，查看画面与含义', anchor='center')
        self.preview_label.pack(fill='x', pady=10)
        self.sticker_description = tk.Text(preview, height=16, width=35, wrap='word', relief='flat',
                                          bg='white', fg=INK, font=('Microsoft YaHei UI', 10), state='disabled')
        self.sticker_description.pack(fill='both', expand=True)

    def _settings(self):
        ttk.Label(self.settings, text='连接你的微信和 AGY', style='Title.TLabel').pack(anchor='w')
        ttk.Label(self.settings, text='微信保持登录，并打开目标群（推荐独立群聊窗口）。修改连接前先停止后台。',
                  style='Muted.TLabel', wraplength=870).pack(anchor='w', pady=(8, 18))
        form = ttk.Frame(self.settings)
        form.pack(fill='x')
        form.columnconfigure(1, weight=1)
        ttk.Label(form, text='微信账号').grid(row=0, column=0, sticky='w', padx=(0, 20), pady=10)
        self.account = tk.StringVar(value=self.current_config.get('account',''))
        self.account_box = ttk.Combobox(form, textvariable=self.account, state='readonly', values=[self.account.get()] if self.account.get() else [])
        self.account_box.grid(row=0, column=1, sticky='ew')
        ttk.Button(form, text='检测账号', command=self.detect_accounts).grid(row=0, column=2, padx=(12,0))
        ttk.Label(form, text='目标群聊').grid(row=1, column=0, sticky='w', pady=10)
        initial = self.current_config.get('group_name','')
        if initial:self.group_map[initial] = dict(username=self.current_config['group_id'], name=initial)
        self.group = tk.StringVar(value=initial)
        self.group_box = ttk.Combobox(form, textvariable=self.group, state='readonly', values=[initial] if initial else [])
        self.group_box.grid(row=1, column=1, sticky='ew')
        ttk.Button(form, text='读取群聊', command=self.detect_groups).grid(row=1, column=2, padx=(12,0))
        ttk.Label(form, text='普通接话间隔').grid(row=2, column=0, sticky='w', pady=10)
        self.cooldown = tk.StringVar(value=str(self.current_config.get('ordinary_reply_seconds',4)))
        ttk.Spinbox(form, from_=2, to=60, textvariable=self.cooldown, width=8).grid(row=2,column=1,sticky='w')
        ttk.Label(form, text='秒；真实 @ 不受此间隔限制', style='Muted.TLabel').grid(row=2,column=2,sticky='w')
        ttk.Button(self.settings, text='保存连接设置', style='Primary.TButton', command=self.save_settings).pack(anchor='w', pady=18)
        self.settings_note = ttk.Label(self.settings, text='', style='Muted.TLabel', wraplength=870)
        self.settings_note.pack(anchor='w', pady=(0,18))
        found = dependencies()
        ttk.Separator(self.settings).pack(fill='x', pady=8)
        for key, label in (('agy','AGY CLI · 日常模型'),('codex','Codex CLI · 不确定图片识别')):
            ttk.Label(self.settings, text=f'{label}：'+('已安装' if found[key] else '未检测到'),
                      font=('Microsoft YaHei UI',10,'bold')).pack(anchor='w',pady=6)
        ttk.Label(self.settings, text='CLI 使用你自己的登录和额度，登录是否有效以实际调用为准。\n'
                  '本安装包不包含 AGY、Codex、微信账号或登录凭据。', style='Muted.TLabel').pack(anchor='w', pady=8)
        ttk.Button(self.settings, text='AGY 安装与登录说明', command=lambda:webbrowser.open('https://antigravity.google/docs/cli/')).pack(anchor='w')

    def _notes(self):
        actions = ttk.Frame(self.notes)
        actions.pack(fill='x', pady=(0,15))
        for label, relative in (('群聊归纳','data/群聊归纳.md'),('成员记忆','data/成员侧写.md'),('运行日志','.runtime/host.log')):
            ttk.Button(actions, text=label, command=lambda p=relative:self.open_path(ROOT/p)).pack(side='left', padx=(0,10))
        ttk.Button(actions, text='选择其他资料目录', command=self.choose_workspace).pack(side='right')
        ttk.Label(self.notes, text='大肥鱼的人格', font=('Microsoft YaHei UI',14,'bold')).pack(anchor='w')
        ttk.Label(self.notes, text='保存后下一轮回复生效；保存前会备份原文。', style='Muted.TLabel').pack(anchor='w', pady=7)
        self.persona = tk.Text(self.notes, wrap='word', font=('Microsoft YaHei UI',10), relief='solid', bd=1,
                              padx=12, pady=12, undo=True)
        self.persona.pack(fill='both', expand=True)
        self.persona.insert('1.0', (ROOT/'PERSONA.md').read_text(encoding='utf-8'))
        ttk.Button(self.notes, text='保存人格', style='Primary.TButton', command=self.save_persona).pack(anchor='e', pady=(12,0))

    @staticmethod
    def replace_text(widget, text):
        if widget.get('1.0','end-1c') == text:return
        widget.configure(state='normal')
        widget.delete('1.0','end');widget.insert('1.0',text)
        widget.configure(state='disabled')

    def _poll(self):
        while not self.events.empty():
            callback, value, error = self.events.get_nowait()
            if error:messagebox.showerror('操作未完成', error, parent=self)
            else:callback(value)
        status, entries = snapshot()
        running = status['host_running']
        starting = self.launcher is not None and self.launcher.poll() is None and not running
        state = ('正在接话' if status.get('reply_state')=='generating' else '后台运行中') if running else '已停止'
        if starting:state='正在启动…'
        self.state_label.configure(text=state, foreground='#16816d' if running else INK)
        self.start_button.configure(state='disabled' if running or starting else 'normal')
        self.stop_button.configure(state='normal' if running or starting else 'disabled')
        config = read_json(ROOT/'data/config.json')
        self.group_label.configure(text=f"目标群：{config.get('group_name','尚未配置')}    ·    资料保存在本机")
        values = ('Flash Low','Flash High',f"{status['stickers_understood']} / {status['stickers_total']}",str(status['stickers_favorited']))
        for label,value in zip(self.metric_labels,values):label.configure(text=value)
        self.replace_text(self.last_reply, status.get('last_reply') or '大肥鱼还没有发言。启动后会在这里显示最近的回复。')
        latency = status.get('last_latency',{})
        info = [status.get('last_decision','等待启动')]
        if running:info += ['Low '+('就绪' if status.get('reply_ready') else '预热中'), 'High '+('就绪' if status.get('reasoning_ready') else '预热中')]
        if 'model_seconds' in latency:info.append(f"最近生成 {latency['model_seconds']:.1f} 秒")
        if status.get('updated'):info.append('更新 '+status['updated'].replace('T',' '))
        self.details.configure(text='  ·  '.join(info))
        errors = [str(status[k]) for k in ('error','reply_error','research_error','sticker_send_error','sticker_error') if status.get(k)]
        self.error_label.configure(text='\n'.join(dict.fromkeys(errors)))
        self.catalog = {e['code']:e for e in entries}
        for code,entry in self.catalog.items():
            state = '已理解' if entry.get('understanding')=='clear' else '待理解'
            if entry.get('added_to_wechat'):state+=' · 已收藏'
            values=(code,' / '.join(entry.get('tags',[])),state)
            if self.tree.exists(code):
                if tuple(self.tree.item(code,'values'))!=values:self.tree.item(code,values=values)
            else:self.tree.insert('', 'end', iid=code,values=values)
        self.after(1200,self._poll)

    def _background(self, task, done):
        def run():
            import pythoncom
            pythoncom.CoInitialize()
            try:self.events.put((done,task(),None))
            except Exception as exc:self.events.put((done,None,str(exc)))
            finally:pythoncom.CoUninitialize()
        threading.Thread(target=run,daemon=True).start()

    def start(self):
        try:self.launcher=start_host()
        except Exception as exc:
            messagebox.showerror('暂时无法启动',str(exc),parent=self)
            self.tabs.select(self.settings)

    def stop(self):
        stop_host()
        self.state_label.configure(text='正在停止…')

    def detect_accounts(self):
        self.settings_note.configure(text='正在检测本机微信账号…')
        def done(rows):
            values=[r['account'] for r in rows]
            self.account_box.configure(values=values)
            if len(values)==1:self.account.set(values[0])
            self.settings_note.configure(text=f'检测到 {len(values)} 个账号。选择已登录的账号，再读取群聊。')
        self._background(accounts,done)

    def detect_groups(self):
        account=self.account.get()
        if not account:
            messagebox.showinfo('先选择账号','请先检测并选择微信账号。',parent=self);return
        self.settings_note.configure(text='正在读取所选账号的群聊，首次读取可能需要一点时间…')
        def done(rows):
            self.group_map={f"{r['name'] or '未命名群'}  ·  {r['username']}":r for r in rows}
            self.group_box.configure(values=list(self.group_map))
            for label,row in self.group_map.items():
                if row['username']==self.current_config.get('group_id'):self.group.set(label);break
            else:self.group.set('')
            self.settings_note.configure(text=f'读到 {len(rows)} 个群聊。请选择目标群，再保存设置。')
        self._background(lambda:groups(account),done)

    def save_settings(self):
        try:
            row=self.group_map.get(self.group.get())
            if not row:raise ValueError('请先读取并选择目标群聊。')
            seconds=float(self.cooldown.get())
            if not 2<=seconds<=60:raise ValueError('接话间隔请填 2 到 60 秒。')
            config=dict(account=self.account.get(),group_id=row['username'],group_name=row['name'],
                        poll_seconds=.35,ordinary_reply_seconds=seconds)
            save_config(ROOT,config)
            self.current_config=config
            self.settings_note.configure(text='设置已保存。回到运行概览即可启动。')
        except Exception as exc:messagebox.showerror('未保存设置',str(exc),parent=self)

    def save_persona(self):
        text=self.persona.get('1.0','end-1c').strip()
        if not text:
            messagebox.showerror('未保存','人格内容不能为空。',parent=self);return
        path=ROOT/'PERSONA.md'
        backup=ROOT/'data/persona-backups'
        backup.mkdir(exist_ok=True)
        shutil.copy2(path,backup/f'PERSONA-{datetime.now():%Y%m%d-%H%M%S-%f}.md')
        path.write_text(text+'\n',encoding='utf-8')
        messagebox.showinfo('已保存','原文已备份，新人格从下一轮回复开始使用。',parent=self)

    def choose_workspace(self):
        selected=filedialog.askdirectory(title='选择已有资料目录，或为新群选择空目录',initialdir=ROOT)
        if not selected:return
        subprocess.Popen(entry_command(Path(selected)),creationflags=subprocess.CREATE_NO_WINDOW)
        self.destroy()

    def open_path(self,path):
        if not Path(path).exists():
            messagebox.showinfo('尚无内容','后台运行后会生成这份资料。',parent=self);return
        os.startfile(str(path))

    def select_sticker(self,event=None):
        selection=self.tree.selection()
        if not selection:return
        entry=self.catalog.get(selection[0])
        if not entry:return
        path=Path(entry['path'])
        if self.animation is not None:self.animation.close()
        self.animation=Image.open(path) if path.exists() else None
        self.animation_path=path
        text=f"{entry['code']}  ·  {entry.get('classified_by','等待识图')}\n\n{entry.get('meaning','正在理解…')}\n\n"
        text+='画面：'+entry.get('description','')+'\n\n适合：'+'；'.join(entry.get('use_when',[]))
        text+='\n\n避免：'+'；'.join(entry.get('avoid_when',[]))
        self.replace_text(self.sticker_description,text)

    def _animate(self):
        delay=180
        if self.animation is not None:
            frame=self.animation.convert('RGBA')
            frame.thumbnail((310,210))
            self.preview_image=ImageTk.PhotoImage(frame)
            self.preview_label.configure(image=self.preview_image,text='')
            delay=max(50,self.animation.info.get('duration',180))
            try:self.animation.seek(self.animation.tell()+1)
            except EOFError:self.animation.seek(0)
        self.after(delay,self._animate)
