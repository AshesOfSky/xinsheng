# 微信群聊连接测试版 0.1.0

面向 Windows 11 x64 的前置验证工具，还不是 AI 聊天机器人。

## 另一台电脑怎么用

1. 解压整个发布包到普通文件夹，运行 WeChatProbe.exe，无需安装 Python。
2. 微信客户端登录测试小号。建立一个名称唯一的测试群，将小号加入。
3. 填写完整群名和小号在群里的昵称，点击“开始只读测试”。
4. 连接库可能修改辅助功能设置并重启微信；只在专用测试电脑运行。
5. 用另一个账号发普通文字、真正的 @ 小号消息，再由小号手动发一条消息。
6. 核对界面显示的群名、消息及 @ 标记。切换窗口后再发一条，确认能否继续读取。
7. 点击“停止监听”。可以保存诊断 JSON，含群名和消息，不自动上传。

“窗口连接成功”不代表新消息读取成功。没有消息时请检查群名、登录状态及 UI 可见性。

## 已确认的能力边界

- wx4py 0.2.1 的 MessageEvent 没有发送者、方向或稳定消息 ID 字段。
- 不能可靠区分小号手动发出的消息；不能据此实现成员记忆或人工接管识别。
- @ 标记依据文本与群昵称匹配，不是微信协议级提及信息。
- 本版只监听，不发送消息、不调用 AI；真正收发及长期运行尚待目标机实测。
- 停止监听会结束本工具的工作进程，不退出微信；辅助功能改动不会自动还原。

## 开发与打包

Python 3.10 x64：

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
.\.venv\Scripts\python -m unittest -v
.\.venv\Scripts\python probe.py
.\.venv\Scripts\python -m pip install pyinstaller==6.22.3
.\.venv\Scripts\python -m PyInstaller --noconfirm --onedir --console --hide-console hide-early --name WeChatProbe --collect-all wx4py probe.py
```

上游：https://github.com/claw-codes/wx4py 。其声明为 AGPL-3.0 并附加商业使用限制，本项目用途为个人非商业测试；随包保留第三方许可及本工具源码。
