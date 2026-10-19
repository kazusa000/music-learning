# Music Learning

以音乐工程为共同上下文、由 AI 直接操作的 DAW，用于研究、改编、积累并创作音乐，支持 Windows x64 VST3 音源与效果器。

主要流程：**主页选择音乐 → 整曲预处理 → 专用工作台 → 全曲或局部讨论 → 修改与试听 → 保存经验 → 跨工程创作**。

用户具备较好的乐理基础，通过讨论、查看和试听提供意图与评价；AI 通过 API/SDK/CLI 操作工程中的全部已实现能力。前端用于展示与讨论，核心制作流程无需点击界面；不需要 MIDI 键盘。

## 当前状态

2026-10-18：P0a 原生 VST3 原型已实现。AI 可通过 JSON/CLI 扫描插件、读取参数/状态、写入 MIDI、混合音频轨、执行阶跃自动化、渲染及恢复快照。真实 Surge XT、CHOW Tape、sfizz 和自制 VST3 夹具的 13 项测试通过。

P0b 技术探测已完成：194 秒原创真值夹具 + NAS 只读复制的 305 秒真实曲目，跑通 MuScriptor medium、Demucs、SheetSage2、真实 VST3 整曲/逐轨渲染和浏览器试听。Codex App Server 已用既有 ChatGPT 登录通过真实工具/流式/错误/中断探测。27 项回归通过，详情见 [P0b 最新报告](docs/benchmarks/p0b-real-song-and-codex.md)。

P1 已完成：工程、内容寻址素材、不可变 revision、分析来源、版本绑定笔记、租约任务和事件 cursor 可跨重启恢复；23 个命令/查询共用 API/SDK/CLI 契约，最小主页支持完整原曲导入、真实任务状态与原曲试听。P1 26 项测试及原 P0 27 项回归通过，实际保存了 305 秒曲目、6 声部、9,378 音符并完成服务重启核对，见 [P1 报告](docs/benchmarks/p1-persistence.md)。

2026-10-19：P2 已接通七阶段持久流水线，305 秒真实本地副本与 194 秒原创曲完成三模型、时间对齐、真实 VST3 混音/逐轨重建、完整状态/资源保存及质量检查。主页自动处理后开放只读工作台，含波形、段落/和弦候选和钢琴卷帘；37 项后端测试 + P0 27 项回归通过。详见 [P2 报告](docs/benchmarks/p2-pipeline.md)。

P3 已完成：38 项统一操作覆盖范围编辑、轨道/片段/CC、插件/路由、自动化、真实 VST3 制作、冻结、测量、导出和版本恢复。前端已重设计，支持可缩放卷帘、同步多轨试听、循环和版本 A/B；79 项测试及整曲/浏览器/停启验收通过，见 [P3 报告](docs/benchmarks/p3-daw.md)。

P4 已完成：真实 Codex 整曲/局部讨论、受限工具查询、有效引用、笔记、停止/断线和服务重启验证通过；84 项回归通过，见 [P4 报告](docs/benchmarks/p4-dialogue.md)。P5 已完成：四组真实自然语言改编及整曲 VST3 渲染、贝斯混合、导出/版本引用和停启通过；94 项回归通过，见 [P5 报告](docs/benchmarks/p5-arranging.md)。P6 自动工程验收已完成：105 项回归、305 秒完整当前流程、12 工程/639 对象归档与独立恢复通过。**人工听辨待用户反馈，P6 与第一阶段尚未标记全部完成**，见 [P6 报告](docs/benchmarks/p6-delivery.md)。当前 `ready` 表示技术材料或指定版本音频齐备，识别结果仍待核对；卷帘保持只读，编辑由 AI 接口执行。直接音乐音频理解与人工听辨尚未完成。连续开发按 [周额度停止条件](docs/16-continuous-goal.md)执行。

第一阶段目标：一首完整音乐完成预处理后，AI 能定位、讨论和改编任意片段，程序化操作轨道、MIDI、VST3、混音与自动化，生成可试听版本；用户在前端查看、讨论和比较，重启后继续工作。

## 文档入口

音色库补齐更新：已安装六套免费采样库，并索引 3,008 份 Surge FXP；28 个精选预设从独立打包副本完成实际发声与状态恢复。新增预设查询、建议、持久 capture 与按声部应用，产品流水线不再统一使用 INIT；同曲另建音色分叉，9,378 音符保持不变。说明和接口见[音源与映射](docs/21-instrument-library.md)，新版试听在 `H:\music-learning-delivery\listening-instruments-v1\index.html`；音乐质量仍待实际听辨。

| 文档 | 用途 |
| --- | --- |
| [音源与映射](docs/21-instrument-library.md) | 已安装采样库、预设查询/捕获、声部选择、实际音色版本与试听 |
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
| [模型与整曲探测说明](docs/12-model-probes.md) | 模型环境、授权、完整曲 CLI、渲染及浏览器探测命令 |
| [P0 汇总](docs/benchmarks/p0-summary.md) | P0b 整曲实测、质量错误、资源测量与退出缺口 |
| [Codex 接入方案](docs/13-codex-integration.md) | 官方 App Server、真实工具往返、登录与后续工程接入 |
| [P0b 最新验证](docs/benchmarks/p0b-real-song-and-codex.md) | NAS 只读、305 秒三模型、完整 VST3、Codex 与模型误差 |
| [P1 工程服务](docs/14-project-service.md) | 启停、迁移、素材/版本/任务、统一 API/SDK/CLI 与安全边界 |
| [P1 验证记录](docs/benchmarks/p1-persistence.md) | 整曲数据保存、真实插件回读、取消/强杀恢复、主页及重启 |
| [P2 整曲流水线](docs/15-preprocessing-pipeline.md) | 七阶段、阶段缓存/恢复、候选版本保护、完整性门槛与结果工作台 |
| [P2 验证记录](docs/benchmarks/p2-pipeline.md) | 两首整曲、实际中断重启、模型缓存、素材回读与浏览器 |
| [P3 制作服务与工作台](docs/17-daw-implementation.md) | 原子编辑、原生 DAG、自动化、冻结、导出与 Web Audio 试听 |
| [P6 本地交付](docs/20-local-delivery.md) | 启停/诊断/统一 CLI、完整备份/恢复、试听与能力边界 |
| [P6 验收记录](docs/benchmarks/p6-delivery.md) | 105 项回归、完整产品流、独立恢复与待完成的人工听辨 |
| [P5 对话制作](docs/19-conversation-arranging.md) | 范围授权、原子编辑/渲染、真实插件、贝斯混合与导出 |
| [P5 验收记录](docs/benchmarks/p5-arranging.md) | 四组真实改编、独立 PCM/MIDI 回读、浏览器与停启 |
| [P4 工程讨论](docs/18-music-dialogue.md) | 真实 Codex、固定上下文、分页查询、引用/笔记与持久化 |
| [P4 验收记录](docs/benchmarks/p4-dialogue.md) | 整曲/局部讨论、真实停止/断线、浏览器与重启 |
| [P3 验收记录](docs/benchmarks/p3-daw.md) | 305 秒制作、300 秒脉冲、版本 A/B、状态/导出回读与停启 |

阅读顺序：项目规划 → 交互与工作流 → 技术方案 → 开发顺序；实现前同时阅读数据约定与验收方案。

## 第一阶段技术基线

- Windows 本地运行，浏览器访问专用前端。
- React、TypeScript、Vite；结果时间线、事件展示、讨论和试听；浏览器播放原曲或已渲染产物。
- C++20 原生 VST3 宿主与处理图，P0a 已选择 JUCE 9.0.4 headless。制作优先离线渲染，不依赖浏览器处理插件音频。
- Python 3.11、FastAPI、Pydantic；SQLite 元数据和磁盘文件；独立任务进程。
- MuScriptor 多乐器转录、Demucs 分轨、SheetSage2 和声及结构分析，分别通过可替换适配器接入。
- 原生处理图统一执行 VST3 乐器、效果链、自动化和渲染；FluidSynth 仅作可选参考后端，FFmpeg 负责媒体转换。
- AI 通过统一命令服务读取并操作工程、回读结果、渲染和测量；API/SDK/CLI 使用同一契约，具体 AI 供应商可替换。

原生环境、插件、模型与代码/权重、Codex 四组件及媒体工具的版本/摘要已固定，三个模型与对话接口均已完成 P0 实测。

## 运行应用

双击 `start.cmd` 启动本机 API/worker 并打开主页；`stop.cmd` 停止。工程数据位于 `D:\music-learning-runtime\app-data`，不在 NAS。也可只启动服务给 AI 使用：

```powershell
& runtime/tooling/Scripts/python.exe scripts/app.py start
& runtime/tooling/Scripts/python.exe scripts/app.py status
& runtime/tooling/Scripts/python.exe scripts/app.py stop
```

首次恢复环境运行 `scripts/bootstrap_app.py`，详细 SDK/CLI 和配置命令见 [P1 运行说明](docs/14-project-service.md)。

## 检查与 P0 探测

本机已准备环境，可直接运行：

```powershell
& runtime/tooling/Scripts/python.exe -m pytest tests/native -q --basetemp=runtime/pytest-p0a
& runtime/tooling/Scripts/python.exe scripts/make_native_demo.py
& D:\music-learning-runtime\envs\app\Scripts\python.exe -m pytest tests/service -q --basetemp=D:\music-learning-runtime\tmp\pytest-p1
& D:\music-learning-runtime\envs\transcription\Scripts\python.exe -m pytest tests/native tests/models -q --basetemp=D:\music-learning-runtime\tmp\pytest-p0-regression
& D:\music-learning-runtime\envs\app\Scripts\python.exe scripts/export_contracts.py --check
pnpm --dir apps/web typecheck
pnpm --dir apps/web build
```

从空环境重建、Agent 调用与当前限制见 [运行说明](docs/11-native-host.md)。第三方许可与工具来源见 [THIRD_PARTY.md](THIRD_PARTY.md)。

## 仓库与音乐工程

Git 管理源代码、开发文档、契约和小型测试数据。用户音乐、模型、分轨、渲染音频、数据库、对话与个人经验保存在被忽略的 `data/`、`models/`、`runtime/` 中，并由应用自己的工程版本与备份机制管理。

本机大文件和应用数据配置到独立的 `D:\music-learning-runtime`，同样不进入源码 Git。P1 的 SQLite backup API 仅备份元数据；P6 已提供数据库与全部 CAS（包括音频、插件状态及采样资源）的完整备份和独立恢复，实际演练通过。模型、已安装插件与运行时由固定依赖清单另行恢复，详见[本地交付](docs/20-local-delivery.md)。

本地 Git 仓库不等于远程备份；音乐工程也不依赖 Git 提交来保存。
