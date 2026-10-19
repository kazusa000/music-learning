# 本地交付、完整备份与恢复

应用为 Windows x64 本地预览版。源码在 `E:\Intrest\music-learning`，正式数据/隔离环境/模型在 `D:\music-learning-runtime`；原始 NAS 音乐只使用先前只读复制的本地副本。前端是查看、讨论和试听的工作台，AI 的制作操作继续通过统一 API/SDK/CLI 执行。

## 启动与诊断

双击项目根目录 `start.cmd` 启动并打开主页；`stop.cmd` 只停止本项目归属的服务树，`status.cmd` 查看运行状态。更新代码后先停止再启动，不使用旧进程热更新。服务仅监听 `127.0.0.1:8765`，启动器管理本机会话；不要把会话凭据发到聊天或提交 Git。

`doctor.cmd` 生成本地诊断报告；需要显式检查既有 Codex 登录时运行：

```powershell
& runtime/tooling/Scripts/python.exe scripts/doctor.py --use-existing-login
```

诊断包括网页构建、真实原生宿主、固定媒体工具/插件摘要、模型文件尺寸、两套环境中的实际 CUDA 矩阵运算、Codex 官方登录状态。尺寸检查不等于全权重 SHA 检查；登录状态不等于真实模型调用权限。诊断不会发起音乐模型或 Codex 推理，未登录的独立 Codex 目录需本机登录，或在讨论区显式选择已有登录。

正式操作仍由 `describe_capabilities` 发现，48 项领域操作与 10 类持久任务共用 HTTP `/v1/commands`、SDK 和 CLI。项目根目录 `music.cmd` 使用固定 app 环境运行同一个 CLI，无需全局安装；`music.cmd call jobs.submit --args <本地JSON> --expected-revision <head>` 可调用下列任务。SDK 提供 backup、restore_backup、diagnose 便利方法，不维护另一份工程状态。

| kind | payload | 结果 |
| --- | --- | --- |
| application.backup | destination：一个全新的本地目录 | archive_path、archive_sha256、对象数量、各工程 head |
| application.restore | archive、destination、expected_sha256（推荐）、port（默认 8766） | 新 data_root、config_path、完整性与恢复报告 |
| application.diagnose | use_existing_login（默认 false） | 检查结果与明确的 status/checks |

任务结果为 persisted job；诊断任务的 succeeded 表示报告已完成，健康情况需读 result.status/checks。所有 application.* 任务最多自动尝试一次；备份/恢复失败后需选择新目标目录重新提交，不覆盖已有输出。

## 完整备份的范围

这是**全应用工程目录**的点时刻归档，任务所绑定的工程用于命令版本检查，不将归档限制成一个工程。归档包含 SQLite 一致性快照和全部本地 CAS 对象，包括未使用但仍保存的素材；原始音频、分析原输出、全部版本/实体、插件完整状态/采样资源、讨论、引用、笔记、任务和已发布音频共同保存。

归档内有版本化 manifest、数据库/每个对象的 SHA-256/长度、工程 head 和代码提交标识。备份时检查数据库完整性/外键、所有已注册素材/已上传对象是否存在，并逐对象验证摘要。文件仅在完整 ZIP 写入后改为正式文件名；失败/取消可能留下自有 partial 目录，不能当作成功。

凭据文件、Codex 登录、模型权重、VST3 安装包、Python/C++ 运行时和日志不打进工程归档；使用 Git 中固定的依赖/模型/插件清单恢复相应环境。源码由 Git 管理，可另外导出 Git bundle。工程归档不是整台电脑或所有外部依赖的镜像。

NAS/网络目录始终不能作为备份或恢复目的地。本机演练使用已核实的 USB 固定磁盘 H: 上全新目录，避免挤占 D: 的模型/制作空间；不遍历或改动 H: 的其他资料。

## 独立恢复

恢复只允许全新的本地目录，拒绝覆盖现有目录、落在活动数据目录内、反向包含活动目录、路径越界、重复 ZIP 项、额外文件、链接、摘要/尺寸不符、空间不足或未知数据库版本。即使归档校验失败，也不会替换活动工程。

所有文件经流式长度/摘要校验和 SQLite integrity/foreign_key 检查后，才写出 `restored-app.local.json` 与恢复报告。归档中已经完成或取消的任务及历史阶段保留；历史快照中尚未完成的任务/讨论标为 failed/restored_requires_retry，清除租约，要求显式操作，防止打开备份就重新花费模型额度。新实例生成独立 HTTP 会话凭据，不复制原实例的凭据，不自动切换正在使用的数据目录。

恢复输出的 config_path 可通过子进程环境 `MUSIC_APP_CONFIG` 指定；验证脚本 `scripts/p6_backup_probe.py` 会独立用 8766 端口启动、查询、渲染、停止及重启恢复实例。原正式实例仍用 D: 目录和 8765；不修改用户全局配置，不启动任何 NAS 写入。

## 试听与人工验收

`scripts/p6_listening_pack.py` 生成 Git 外的试听包：原曲、当前模型初版、当前局部改编，以及独立 P5 制作实验的改编前/后/贝斯混合。两组基准不同，页面明确分别比较；所有完整音频带原有素材摘要和 revision，片段没有悄悄归一化/加限幅。

本机试听入口为 `H:\music-learning-delivery\listening-p6-v1\index.html`，含 6 条完整音频和 24 条片段。`candidate-review.csv` 是尚未核对的候选事件，人工修正列为空，不当作真值。15 秒片段仅用于定位，实际四到八小节边界需人工核对。

人工听辨须检查开头、中段、结尾的漏音/多音、八度、延音、拍点、和弦歧义、乐器误判、分轨串音、替代音色与改编接缝，给出有限可定位的修正/判断。未收到实际试听反馈前，该项保持 pending，不能以全部测试通过或模型的文字评价代替。

## 能力边界

整曲结果仍是可校正的模型候选，音色采用已验证的替代方案；不承诺任意歌曲精确还原。Codex 依据事件、注释与存储测量讨论，直接音乐音频理解未验证。卷帘展示只读；MIDI 键盘、实时制作、插件生成 MIDI、任意厂商私有预设菜单、多声道/反馈路由仍未提供。离线工程操作、真实 VST3、版本/音频绑定和混合试听以实际接口及报告为准。
