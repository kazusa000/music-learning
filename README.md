# Music Learning

本机 AI 音乐 DAW：导入音乐，查看原曲与重建工程，与 AI 讨论和修改编曲，保存版本并导出。AI 与界面共用 API/SDK/CLI；支持 Windows x64 VST3，不需要 MIDI 键盘。

**本地 C++ 引擎的设备、文件播放、隔离VST3和前端接线已通过N1–N4独立验收；N5生命周期、N6完整路由与N7最终部署继续推进。目前正式已部署版本仍使用 WebAudio。**

- [本地 DAW 架构](docs/architecture/native-daw.md)
- [开发顺序与验收指标](docs/plans/native-daw-migration.md)
- [目录职责与整理顺序](docs/development/repository-layout.md)
- [当前文档导航](docs/README.md)

## 启动

双击 `start.cmd` 打开本机桌面窗口；`status.cmd` 查看状态，`stop.cmd` 停止。已有部署仍可使用，本轮规划没有重启服务或切换音频引擎。

正式数据在 `data/app-data/`，本机配置在 `runtime/app.local.json`。模型、环境、运行产物留在本项目 E 盘目录；用户音源库保持 `H:\music\Sound Library`。开发前先读 [AGENTS.md](AGENTS.md)，保护现有工程、固定验证任务和音源资源。

## 当前功能

- **导入：** MuScriptor large＋Beat This＋SheetSage2；有人声时使用 Leap 分离，MuScriptor 转录伴奏。自动／有人声／纯音乐可选，有真实阶段进度。
- **原曲：** 人声波形、静音旋律 MIDI、伴奏波形；纯音乐一轨。显示和弦／段落并支持参考修正。
- **重建：** 真实人声＋伴奏乐器 MIDI，可修改音符和音色，使用音量、静音、独奏；日常编辑不自动导出整曲。
- **讨论：** 会话、模型选择、Markdown、原曲参考与当前 MIDI 查询及 AI 编辑；用户通过既有本机登录接入。
- **音色：** 按乐器→型号→变体选择，中文／原名并列，产品库名保留；可试听后应用。
- **工程：** 不可变内部修订、手动版本、独立混音、显式导出和永久删除（不创建备份）。

当前详细行为与实测范围见[双编排](docs/50-original-and-reconstruction.md)、[播放缓存修复](docs/51-playback-buffer-ownership.md)和[永久删除](docs/49-permanent-project-deletion.md)。设备级本地播放与实时完整路由仍属于后续重构，不能以已有离线能力代替验收。

## 开发入口

| 目录 | 用途 |
| --- | --- |
| `apps/web/` | React 界面与前端验证 |
| `native/` | 桌面外壳、原生宿主；新增引擎将独立构建 |
| `src/music_learning/` | 工程领域、统一接口、AI 与任务编排 |
| `services/` | API／worker 入口与数据库迁移 |
| `packages/contracts/` | 生成的 JSON Schema／TypeScript 契约 |
| `scripts/`、`tests/` | 启动、构建、工具、探针及回归 |
| `config/` | 固定依赖与配置样例 |
| `docs/` | 当前设计、开发计划与历史验收 |
| `data/`、`runtime/` | Git 忽略的本机数据、环境和构建，不作源码大搬家 |

编译与依赖操作沿用现有固定版本；新引擎使用隔离目标，不覆盖旧宿主或并行验证的静态库。具体操作顺序见[迁移计划](docs/plans/native-daw-migration.md)，许可与来源见[第三方说明](THIRD_PARTY.md)。

旧阶段、旧计数与历次改造说明已收至[历史 README](docs/history/README-before-native-daw.md)，旧编号文档仍保留原路径。需要当前队列／目录数量时现场回读，历史报告不代表实时状态。
