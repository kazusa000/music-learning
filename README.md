# Music Learning

本机 AI 音乐 DAW。可以从零编曲，或导入音乐进行分析，与 AI 讨论和修改音符、配器与混音，保存版本并导出。

## 启动

双击 `start.cmd`。`status.cmd` 查看运行状态，`stop.cmd` 关闭应用；音频异常时使用 `repair-audio.cmd`。

## 使用

- [工作台与播放](docs/guides/workspace.md)
- [选择音源与试听](docs/guides/instruments.md)
- [AI 对话与笔记](docs/guides/assistant.md)
- [工程、版本和导出](docs/guides/projects.md)

## 开发

[文档导航](docs/README.md) · [目录说明](docs/development/repository-layout.md) · [API / SDK / CLI](docs/development/unified-api.md) · [开发约定](AGENTS.md)

正式工程在 `data/app-data/`，本机配置在 `runtime/app.local.json`，用户音源库在 `H:\music\Sound Library`。依赖许可与来源见 [THIRD_PARTY.md](THIRD_PARTY.md)。
