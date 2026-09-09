# QQ / NapCat 运行环境

本仓库仅提供助手源码，不附带 QQ 安装包、NapCat 程序、登录态或聊天资料。

## 准备目录

- 下载兼容的 NapCat Shell，解压到项目的 runtime/napcat。
- 准备兼容的 QQ 运行目录副本，放到 runtime/qq，确保其中存在 QQ.exe、versions/config.json 和完整版本文件。
- 查看 QQ 实际版本时，应核对 versions/config.json 及版本目录中的 package.json；引导程序文件版本可能不同。
- 第三方组件来自各自官方发布，兼容范围以相应版本说明为准。项目曾针对 NapCat 4.18.19 与 QQ 9.9.28-46928 开发，不自动更新或捆绑这些组件。

官方参考：[NapCat Shell](https://napneko.github.io/guide/boot/Shell)、[NapCat 发布](https://github.com/NapNeko/NapCatQQ/releases)、[QQ 下载](https://im.qq.com/index/#/windows)、[OneBot WebSocket 规范](https://github.com/botuniverse/onebot-11/blob/master/communication/ws.md)。

## 备份现有 QQ 资料

从 QQ 设置或系统公共文档目录中的 Tencent/QQ/UserDataInfo.ini 确认自己的资料位置。先从托盘正常退出 QQ，再执行备份；下面的占位符须替换为实际目录：

~~~powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/backup-qq.ps1 -DataPath "<QQ_DATA_DIR>"
~~~

脚本保留原目录，在 backups 中建立带时间的副本，写入本地完成记录。启动脚本要求存在 data/qq-backup.json。运行目录副本并不等于空白聊天资料，NapCat 仍可能通过 QQ 原生接口使用现有资料。

## 启动与连接

运行“启动QQ连接.cmd”，完成 QQ 登录，再运行“启动心声.cmd”。本机服务检查登录账号与所配置账号相同，且已选择联系人后才就绪。

默认端口：工作台 23210、OneBot WebSocket 3001、NapCat WebUI 6099。均监听 127.0.0.1。启动脚本生成本机令牌，并将其写入运行配置；账号专用的 onebot11_<QQ_ID>.json 在本地生成，不进入仓库。

OneBot 消息格式使用 array，开启 reportSelfMessage，心跳使用 heartInterval。源码使用持久 WebSocket 和唯一 echo 匹配请求响应，区分来信、本人消息、心跳和接口回执。

当前采用 Shell 后台接入，同号原版桌面 QQ 并行使用不作为支持前提。手机 QQ 可作为人工接管入口。需要切回原版桌面 QQ 时，先正常退出配套 QQ / NapCat。

“停止心声.cmd”只停止回复服务，不强制结束 QQ。停止、休眠或退出系统期间不生成或发送回复。项目不自动设置开机自启。

日志与故障详情保存在本地 data 目录。分享故障报告前，应移除账号、令牌、消息正文和个人路径。
