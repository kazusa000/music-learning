# Music Learning

本机 AI 音乐 DAW。可以从零编曲，或导入音乐进行分析，与 AI 讨论和修改音符、配器与混音，保存版本并导出。

## 已安装环境的启动

双击 `start.cmd`。`status.cmd` 查看运行状态，`stop.cmd` 关闭应用；音频异常时使用 `repair-audio.cmd`。

## 使用

- [工作台与播放](docs/guides/workspace.md)
- [选择音源与试听](docs/guides/instruments.md)
- [AI 对话与笔记](docs/guides/assistant.md)
- [工程、版本和导出](docs/guides/projects.md)

## 源码结构

```text
apps/web/             React / TypeScript 界面
src/music_learning/   工程服务、AI、播放控制和任务
services/             API、worker 入口和数据库迁移
native/               C++ 音频引擎、VST3 宿主、桌面壳
packages/             共享契约和 SDK 说明
requirements/         Python 环境输入及版本锁
config/               可提交的配置模板和依赖清单
resources/            风格资料、乐器展示资源
scripts/              启动、构建、音源管理和维护工具
tests/                自动化检查
docs/                 使用、架构、开发和验收文档
```

`data/`、`runtime/`、`plugins/` 是被 Git 忽略的本机目录。工程、音源、模型权重、登录信息和构建产物独立保存，不随源码提供。

## 开发与共享

[贡献与开发入口](CONTRIBUTING.md) · [文档导航](docs/README.md) · [目录职责](docs/development/repository-layout.md) · [API / SDK / CLI](docs/development/unified-api.md)

当前主要运行环境为 Windows x64、Python 3.11、Node.js 和原生 C++ 工具链。首次拿到源码还需要准备本地运行环境，见[源码准备](docs/development/source-checkout.md)；`start.cmd` 面向已经准备好的本机安装，不是完整安装器。

软件源码采用 [AGPL-3.0-only](LICENSE)；乐器图片与风格库的项目原创内容采用 [CC BY 4.0](LICENSES/CC-BY-4.0.txt)，均随源码公开。许可范围和署名见 [NOTICE.md](NOTICE.md)，共享与首次运行说明见[开源准备](docs/development/open-source-preparation.md)。第三方组件、模型及资源来源单独记录在 [THIRD_PARTY.md](THIRD_PARTY.md)，商用音源插件和采样库不在源码仓库中。
