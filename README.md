# Music Learning

以音乐工程为共同上下文、由 AI 直接操作的 DAW，用于研究、改编、积累并创作音乐，支持 Windows x64 VST3 音源与效果器。

Pianoteq 9.1.2 已接入：797 款完整状态已准备，34 款经真实 VST3 验证后可选、覆盖 22 类乐器，与 Keyscape 455 款共同按乐器查询。钢琴/电钢琴/吉他实际 SDK 切换和各 305 秒常驻供声通过，换音色不生成整曲 WAV。状态适配、剩余库存与证据见 [Pianoteq 接入](docs/41-pianoteq-integration.md)。

连续供声、Keyscape播放模式及音色完整状态复用已修正；AI支持同轮连续修改，不再限制每轮一次编辑或32次工具调用。实际速度、冷加载成本和验证见[音色切换与连续供声](docs/40-preset-loading-and-streaming.md)。

播放尾音越界、音色小任务空间预算、对话完成状态及 E 盘存储实查见[反馈与存储](docs/38-feedback-and-storage.md)。本次未清理或迁移历史数据。

最新交互：编曲与混音分开。编曲只提供临时独奏/静音，混音另页手动保存、最终显式导出；首页和工作台不自动匹配响度。播放启动与隔离规则见[编曲监听与独立混音](docs/37-arrangement-monitoring-and-mixing.md)。

主要流程：**主页选择音乐 → 整曲预处理 → 专用工作台 → 全曲或局部讨论 → 修改与试听 → 保存经验 → 跨工程创作**。

主页右上角“设置”管理默认 AI 模型与登录方式。工作台“保存版本”只在点击时记录快照，普通编辑自动保存工作状态。音符更新常驻 VST3 音轨，换音色只装载对应轨，不自动生成整曲试听。常驻播放接口与路由边界见[设置与常驻音轨](docs/36-settings-and-live-tracks.md)，混音隔离以最新文档 37 为准。

主页和工作台现已统一为参考 Suno Studio 2 的近黑制作界面，重做字体、控件、SVG图标、音轨色彩、MIDI缩略图、卷帘、混音台和聊天样式；既有操作布局保留。设计与实际页面检查见[统一视觉规范](docs/32-studio-visual-system.md)。

当前入口已去掉宣传横幅和开发编号，验证工程归档，默认显示实际歌曲。start.cmd 打开独立应用窗口；空格播放/暂停，原曲/重建可切换，声部可独奏和编辑。界面与数据整理的实际验证、桌面技术边界和历史磁盘容量问题见[前端整改](docs/24-usable-studio.md)，本轮迁移、缩放与试听修正见[使用修正](docs/25-local-runtime-and-audition.md)。

AI 讨论已支持独立会话、实际可用模型选择与 Markdown，日常回复不再反复显示审核声明，见[对话使用](docs/28-conversation-usability.md)。

直接说想怎么改即可执行，不需要切改编模式或勾结构权限。默认讨论整曲，试听循环和引用定位不会自动限制改编范围，见[自动对话操作](docs/31-automatic-actions.md)。

讨论侧栏采用紧凑工具条、自动填满的回复区和底部输入框；可拖高输入框或一键放大讨论，范围/权限/笔记收进菜单。Enter发送、Shift+Enter换行。操作与布局验证见[讨论布局](docs/30-chat-layout.md)。

音色推荐先查询实际目录，讨论与改编显示具体音轨；编辑格式失败可按契约提示修正，见[音色工具规则](docs/29-timbre-tool-rules.md)。用户于2026-10-20取消此前额度停止限制。

音色与效果支持手动选音轨、选乐器并直接点击音色切换，显示真实加载进度；规则和验证见[手动换音色](docs/42-manual-instruments.md)。

右侧按“讨论 → 音色与效果 → 版本 → 分析”排列。“音色与效果”合并原音源与乐器页，可选音轨、查看当前插件链，再按乐器选择新音色，见[侧栏说明](docs/27-toolbar-and-sidebars.md)。

顶部工程栏和播放栏支持自动收起与锁定，侧栏和对话布局已整理，见[工具栏与侧栏](docs/27-toolbar-and-sidebars.md)。

侧栏和制作区域现在支持拖动边界调整大小，卷帘可用普通滚轮上下移动、Alt＋滚轮纵向缩放；操作和验证见[可调整面板](docs/26-resizable-panels.md)。

前端采用紧凑制作布局，包含手动卷帘、片段编排与混音台，用户和 AI 共用同一编辑/版本接口。使用方式与验证见[制作工作台](docs/22-fl-workbench.md)；混音即时播放，音符或音源变化自动准备声音。

启动与讨论加载已优化：事件触发刷新、生成中只更新活跃回复、按需展开插件/工具详情，并记住用户选择的登录方式。现状与本机实测见[加载与讨论响应](docs/23-responsive-loading.md)。

用户具备较好的乐理基础，通过讨论、查看和试听提供意图与评价；AI 通过 API/SDK/CLI 操作工程中的全部已实现能力。前端用于展示与讨论，核心制作流程无需点击界面；不需要 MIDI 键盘。

## 当前状态

2026-10-18：P0a 原生 VST3 原型已实现。AI 可通过 JSON/CLI 扫描插件、读取参数/状态、写入 MIDI、混合音频轨、执行阶跃自动化、渲染及恢复快照。真实 Surge XT、CHOW Tape、sfizz 和自制 VST3 夹具的 13 项测试通过。

P0b 技术探测已完成：194 秒原创真值夹具 + NAS 只读复制的 305 秒真实曲目，跑通 MuScriptor medium、Demucs、SheetSage2、真实 VST3 整曲/逐轨渲染和浏览器试听。Codex App Server 已用既有 ChatGPT 登录通过真实工具/流式/错误/中断探测。27 项回归通过，详情见 [P0b 最新报告](docs/benchmarks/p0b-real-song-and-codex.md)。

P1 已完成：工程、内容寻址素材、不可变 revision、分析来源、版本绑定笔记、租约任务和事件 cursor 可跨重启恢复；23 个命令/查询共用 API/SDK/CLI 契约，最小主页支持完整原曲导入、真实任务状态与原曲试听。P1 26 项测试及原 P0 27 项回归通过，实际保存了 305 秒曲目、6 声部、9,378 音符并完成服务重启核对，见 [P1 报告](docs/benchmarks/p1-persistence.md)。

2026-10-19：P2 已接通七阶段持久流水线，305 秒真实本地副本与 194 秒原创曲完成三模型、时间对齐、真实 VST3 混音/逐轨重建、完整状态/资源保存及质量检查。主页自动处理后开放只读工作台，含波形、段落/和弦候选和钢琴卷帘；37 项后端测试 + P0 27 项回归通过。详见 [P2 报告](docs/benchmarks/p2-pipeline.md)。

P3 已完成：38 项统一操作覆盖范围编辑、轨道/片段/CC、插件/路由、自动化、真实 VST3 制作、冻结、测量、导出和版本恢复。前端已重设计，支持可缩放卷帘、同步多轨试听、循环和版本 A/B；79 项测试及整曲/浏览器/停启验收通过，见 [P3 报告](docs/benchmarks/p3-daw.md)。

P4 已完成：真实 Codex 整曲/局部讨论、受限工具查询、有效引用、笔记、停止/断线和服务重启验证通过；84 项回归通过，见 [P4 报告](docs/benchmarks/p4-dialogue.md)。P5 已完成：四组真实自然语言改编及整曲 VST3 渲染、贝斯混合、导出/版本引用和停启通过；94 项回归通过，见 [P5 报告](docs/benchmarks/p5-arranging.md)。P6 自动工程验收已完成：105 项回归、305 秒完整当前流程、12 工程/639 对象归档与独立恢复通过。**人工听辨待用户反馈，P6 与第一阶段尚未标记全部完成**，见 [P6 报告](docs/benchmarks/p6-delivery.md)。当前 `ready` 表示技术材料或指定版本音频齐备，识别结果仍待核对；早期卷帘只读；当前已开放手动编辑，与 AI 共用领域接口。直接音乐音频理解与人工听辨尚未完成。连续开发按 [周额度停止条件](docs/16-continuous-goal.md)执行。

第一阶段目标：一首完整音乐完成预处理后，AI 能定位、讨论和改编任意片段，程序化操作轨道、MIDI、VST3、混音与自动化，生成可试听版本；用户在前端查看、讨论和比较，重启后继续工作。

## 文档入口

音色库补齐更新：已安装六套免费采样库，并索引 3,008 份 Surge FXP；28 个精选预设从独立打包副本完成实际发声与状态恢复。新增预设查询、建议、持久 capture 与按声部应用，产品流水线不再统一使用 INIT；同曲另建音色分叉，9,378 音符保持不变。说明和接口见[音源与映射](docs/21-instrument-library.md)，新版试听在 `H:\music-learning-delivery\listening-instruments-v1\index.html`；音乐质量仍待实际听辨。

| 文档 | 用途 |
| --- | --- |
| [制作工作台](docs/22-fl-workbench.md) | FL 风格布局、手动卷帘/音轨/片段/混音、撤销和版本保护 |
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

统一乐器目录已接入：声学钢琴、电钢琴、电吉他、原声吉他等分别归类，同一乐器下汇集各库音色。工作台“音色与效果”按乐器归类，可直接手动切换，也可在讨论中请 AI 更换；库/插件来源在详情中回读；API/SDK/CLI 与内置 Agent 共用分类。现有 3,031 条预设中 2,937 条有明确或原库目录归类，94 条待分类，发声验证仍为 28 条。122 项回归与真实浏览器/重启验证通过，见[统一乐器目录](docs/21-instrument-library.md#统一乐器目录)。

- Windows 本地运行，浏览器访问专用前端。
- React、TypeScript、Vite；结果时间线、事件展示、讨论和试听；浏览器播放原曲或已渲染产物。
- C++20 原生 VST3 宿主与处理图，P0a 已选择 JUCE 9.0.4 headless。制作优先离线渲染，不依赖浏览器处理插件音频。
- Python 3.11、FastAPI、Pydantic；SQLite 元数据和磁盘文件；独立任务进程。
- MuScriptor 多乐器转录、Demucs 分轨、SheetSage2 和声及结构分析，分别通过可替换适配器接入。
- 原生处理图统一执行 VST3 乐器、效果链、自动化和渲染；FluidSynth 仅作可选参考后端，FFmpeg 负责媒体转换。
- AI 通过统一命令服务读取并操作工程、回读结果、渲染和测量；API/SDK/CLI 使用同一契约，具体 AI 供应商可替换。

原生环境、插件、模型与代码/权重、Codex 四组件及媒体工具的版本/摘要已固定，三个模型与对话接口均已完成 P0 实测。

## 运行应用

双击 `start.cmd` 启动本机 API/worker 并打开主页；`stop.cmd` 停止。工程数据位于 `E:\Intrest\music-learning\data\app-data`，不在 NAS。也可只启动服务给 AI 使用：

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
& E:\Intrest\music-learning\data\envs\app\Scripts\python.exe -m pytest tests/service -q --basetemp=E:\Intrest\music-learning\data\tmp\pytest-p1
& E:\Intrest\music-learning\data\envs\transcription\Scripts\python.exe -m pytest tests/native tests/models -q --basetemp=E:\Intrest\music-learning\data\tmp\pytest-p0-regression
& E:\Intrest\music-learning\data\envs\app\Scripts\python.exe scripts/export_contracts.py --check
pnpm --dir apps/web typecheck
pnpm --dir apps/web build
```

从空环境重建、Agent 调用与当前限制见 [运行说明](docs/11-native-host.md)。第三方许可与工具来源见 [THIRD_PARTY.md](THIRD_PARTY.md)。

## 仓库与音乐工程

用户指定的新音源库位于 `H:\music\Sound Library`。两套音源的接入状态见 [音源接入记录](docs/34-user-instrument-intake.md)；Keyscape 的乐器分类、预设映射与实际发声验证见 [Keyscape 接入](docs/35-keyscape-integration.md)。

Git 管理源代码、开发文档、契约和小型测试数据。用户音乐、模型、分轨、渲染音频、数据库、对话与个人经验保存在被忽略的 `data/`、`models/`、`runtime/` 中，并由应用自己的工程版本与备份机制管理。

本机应用数据位于 `E:\Intrest\music-learning\data`，不进入源码 Git；音源库使用上述用户指定的 H 盘目录。P1 的 SQLite backup API 仅备份元数据；P6 提供数据库与全部工程 CAS 的完整备份和独立恢复。Keyscape 的大型外部样本库只随工程保存不可变资源清单，样本本体和已安装插件需要另行保留；模型与运行时同样另行恢复，详见[本地交付](docs/20-local-delivery.md)。

本地 Git 仓库不等于远程备份；音乐工程也不依赖 Git 提交来保存。
