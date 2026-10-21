# 文档入口

当前产品是本机AI音乐DAW。**本地引擎重构N0–N5已独立验收，N6–N7继续，尚未切正式默认。**

## 当前设计与开发入口

- [本地DAW架构决策](architecture/native-daw.md)：设备时钟、原生插件、控制协议与工程权威。
- [N0–N7开发顺序和验收](plans/native-daw-migration.md)：从设备发声到默认切换。
- [N4产品接线验收](benchmarks/native-engine-n4.md)：本地播放、AI/SDK共用、原生试听与4K流程。
- [N5生命周期验收](benchmarks/native-engine-n5.md)：关闭、重开、删除屏障、临时租约、缓存与显式冻结。
- [目录职责和整理顺序](development/repository-layout.md)：当前位置、目标目录、受保护路径。
- [原曲／重建双编排](50-original-and-reconstruction.md)：当前模型、原曲参考和AI双视图。
- [工程持久化](14-project-service.md)、[统一DAW契约](09-agent-daw-contract.md)、[AI接入](13-codex-integration.md)。

## 当前功能与边界

| 主题 | 文档 |
| --- | --- |
| 原曲、分离、转录、和弦/段落、旋律与参考修正 | [50 双编排](50-original-and-reconstruction.md) |
| 现行播放与缓存问题、已提交修复 | [51 缓存生命周期](51-playback-buffer-ownership.md)；重构前工程音频仍由WebAudio输出 |
| 实时编辑、保存版本、临时监听和独立混音 | [36 常驻音轨](36-settings-and-live-tracks.md)、[37 混音](37-arrangement-monitoring-and-mixing.md)、[39 素材保留](39-current-project-retention.md) |
| 桌面外壳、4K、鼠标与固定工作区 | [43 桌面与鼠标](43-desktop-workspace-and-mouse.md) |
| 音源分类、型号/变体、中文、试听 | [44 型号](44-instrument-models-and-variants.md)、[45 试听](45-chinese-instrument-previews.md) |
| Keyscape / Pianoteq与验证证据 | [35 Keyscape](35-keyscape-integration.md)、[41 Pianoteq](41-pianoteq-integration.md)、[benchmarks](benchmarks/) |
| 对话会话/模型/Markdown与自动编辑 | [28 对话](28-conversation-usability.md)、[31 自动操作](31-automatic-actions.md) |
| 永久删除、无备份与清理屏障 | [49 永久删除](49-permanent-project-deletion.md) |

## 历史资料的优先级

编号文档记录当时设计与实测，不代表所有段落都仍适用。[整理前README](history/README-before-native-daw.md)保存旧阶段状态。当前架构方向以architecture/native-daw为准；当前**已实现**模型/产品行为以50和后续实测修复为准；旧03/05/07保留为历史和兼容入口。历史47中移除SheetSage等描述已被50覆盖，不应据此再次删除当前模型。

工程数量、队列进度、可选音色数和进程状态需要现场回读，不能把历史验收数字当成当前实时状态。开发与目录整理必须遵守仓库AGENTS中的固定宿主、H库、NAS及拒绝清理路径边界。
