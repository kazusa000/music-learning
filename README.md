# Music Learning

以音乐工程为共同上下文、由 AI 直接操作的 DAW，用于研究、改编、积累并创作音乐，支持 Windows x64 VST3 音源与效果器。

主要流程：**主页选择音乐 → 整曲预处理 → 专用工作台 → 全曲或局部讨论 → 修改与试听 → 保存经验 → 跨工程创作**。

用户具备较好的乐理基础，通过讨论、查看和试听提供意图与评价；AI 通过 API/SDK/CLI 操作工程中的全部已实现能力。前端用于展示与讨论，核心制作流程无需点击界面；不需要 MIDI 键盘。

## 当前状态

2026-10-18：P0a 原生 VST3 原型已实现。AI 可通过 JSON/CLI 扫描插件、读取参数/状态、写入 MIDI、混合音频轨、执行阶跃自动化、渲染及恢复快照。真实 Surge XT、CHOW Tape、sfizz 和自制 VST3 夹具的 13 项测试通过。

当前尚无完整应用前端、工程数据库、整曲扒谱流水线或产品内对话。下一步为 P0b；阶段边界见 [P0a 验证记录](docs/benchmarks/p0a-native.md)。

第一阶段目标：一首完整音乐完成预处理后，AI 能定位、讨论和改编任意片段，程序化操作轨道、MIDI、VST3、混音与自动化，生成可试听版本；用户在前端查看、讨论和比较，重启后继续工作。

## 文档入口

| 文档 | 用途 |
| --- | --- |
| [项目规划](docs/01-product-plan.md) | 产品定位、已确认需求、阶段边界、长期创作方向 |
| [交互与工作流](docs/02-workflows.md) | 主页、预处理、音乐工作台、讨论、编辑与版本 |
| [第一阶段技术方案](docs/03-architecture.md) | 技术选型、模型流水线、运行方式、音频播放与渲染 |
| [数据与 AI 操作约定](docs/04-data-and-ai-contracts.md) | 时间基准、工程模型、上下文、工具、编辑事务与经验记录 |
| [开发顺序](docs/05-development-plan.md) | 开发依赖、任务清单、每一步交付与进入条件 |
| [验收方案](docs/06-acceptance.md) | 自动检查、完整用户流程、听辨与完成标准 |
| [开发与 Git 约定](docs/07-development-guide.md) | 目录、依赖、数据保存、Git、运行与交付约定 |
| [选型依据与待验证项](docs/08-references-and-decisions.md) | 官方来源、决策理由、本机环境、待验证风险 |
| [AI DAW 操作约定](docs/09-agent-daw-contract.md) | 全操作 API、原生宿主、插件可控性、状态恢复与制作循环 |
| [免费 VST3 候选](docs/10-free-vst3-candidates.md) | 音源/效果器清单、官方来源、适配优先级与验收 |
| [原生宿主运行说明](docs/11-native-host.md) | 当前可运行命令、原型请求格式、快照和已实现边界 |
| [P0a 验证记录](docs/benchmarks/p0a-native.md) | 真实测试、创作实验、锁定环境与宿主选择 |

阅读顺序：项目规划 → 交互与工作流 → 技术方案 → 开发顺序；实现前同时阅读数据约定与验收方案。

## 第一阶段技术基线

- Windows 本地运行，浏览器访问专用前端。
- React、TypeScript、Vite；结果时间线、事件展示、讨论和试听；浏览器播放原曲或已渲染产物。
- C++20 原生 VST3 宿主与处理图，P0a 已选择 JUCE 9.0.4 headless。制作优先离线渲染，不依赖浏览器处理插件音频。
- Python 3.11、FastAPI、Pydantic；SQLite 元数据和磁盘文件；独立任务进程。
- MuScriptor 多乐器转录、Demucs 分轨、SheetSage2 和声及结构分析，分别通过可替换适配器接入。
- 原生处理图统一执行 VST3 乐器、效果链、自动化和渲染；FluidSynth 仅作可选参考后端，FFmpeg 负责媒体转换。
- AI 通过统一命令服务读取并操作工程、回读结果、渲染和测量；API/SDK/CLI 使用同一契约，具体 AI 供应商可替换。

原生环境与三款插件已锁定版本和下载摘要；模型兼容性、转录质量、耗时与对话服务接入在 P0b 验证。

## 运行当前原型

本机已准备环境，可直接运行：

```powershell
& runtime/tooling/Scripts/python.exe -m pytest tests/native -q --basetemp=runtime/pytest-p0a
& runtime/tooling/Scripts/python.exe scripts/make_native_demo.py
```

从空环境重建、Agent 调用与当前限制见 [运行说明](docs/11-native-host.md)。第三方许可与工具来源见 [THIRD_PARTY.md](THIRD_PARTY.md)。

## 仓库与音乐工程

Git 管理源代码、开发文档、契约和小型测试数据。用户音乐、模型、分轨、渲染音频、数据库、对话与个人经验保存在被忽略的 `data/`、`models/`、`runtime/` 中，并由应用自己的工程版本与备份机制管理。

本地 Git 仓库不等于远程备份；音乐工程也不依赖 Git 提交来保存。
