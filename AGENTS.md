# 项目开发约定

## 任务入口

先读 README、docs/01-product-plan.md、docs/05-development-plan.md。实现数据、编辑或播放前，再读 docs/03-architecture.md、docs/04-data-and-ai-contracts.md 与 docs/06-acceptance.md。

用户已确认的产品流程是：主页选音乐，完成整曲预处理，进入专用前端，与 AI 深入讨论整曲或局部，改编并试听；后续跨工程积累经验并创作。用户有较好的乐理基础。软件的主要操作者是 AI，支持 Windows x64 VST3；不需要 MIDI 键盘。还须阅读 docs/09-agent-daw-contract.md 和 docs/10-free-vst3-candidates.md。

## 实施要求

- 本应用所有已实现工程操作必须有可发现、可回读的 API/SDK/CLI，核心制作流程无需点击前端或插件窗口。用户前端以展示、讨论和试听为主。
- 原生宿主负责插件处理与渲染；Python/AI 不进入实时音频回调。无界面操作能力须按插件实测，VST3 格式支持不等于私有菜单全部可控。
- 保留插件完整状态、资源依赖、参数与版本；参数列表不可代替完整状态。不能用内置音源冒充真实 VST3 验收。
- 保留整曲处理与验收要求。短片段可用于调试，不能替代完整用户流程。
- 原录音和模型原始输出不可覆盖；人工修订、分析重跑与改编通过独立版本保存。
- 区分音频 stems 与 MIDI 声部，区分原曲、重建和混合试听。
- AI 编辑走统一的类型校验、范围约束、幂等与版本冲突处理；失败不得伪装为成功。
- 音乐数据、讨论引用和渲染绑定具体 revision，升级模型不能覆盖用户修订。
- 代码和文档进 Git；用户音乐、凭据、权重、数据库、渲染和日志放入被忽略的数据目录。
- Windows 使用 PowerShell，保持环境隔离。启动的后台进程记录归属，退出或取消时清理本项目子进程。
- 变更技术方案、验收范围或依赖后同步文档。没有实测的速度、音质、GPU 能力和 AI 理解能力明确标为待验证。
- 依照功能风险运行检查；报告区分构建、夹具、真实模型、真实对话、浏览器播放和听辨证据。

## 当前阶段

P0a 原生 VST3 原型已通过 13 项测试；运行说明见 docs/11-native-host.md，证据见 docs/benchmarks/p0a-native.md。P0b 部分完成：194 秒原创曲已实测 Demucs、SheetSage2、VST3 渲染/重开与浏览器探测；新增 7 项检查通过。继续前阅读 docs/12-model-probes.md 和 docs/benchmarks/p0-summary.md。

本机模型环境/产物在 D:\music-learning-runtime，位置保存在被忽略的 runtime/model-runtime.json。不要把大模型再默认安装到空间不足的 E/C 盘。MuScriptor medium 尚待 Hugging Face 授权；真实对话 API 和目标录音未配置。采用 whole-song-v2 的音频与独立 truth-v3.json，旧真值有已记录的和弦标签错误；不可覆盖原音频、模型输出或旧测量。SheetSage2 结构/音符结束时间有明显缺口，Surge 默认音色重开波形非确定；均保留在汇总中。

不要将探测 CLI、只读试听页和原生处理原语当成 P1 工程数据库、正式前端或完整 DAW。任务清单只有满足相应退出条件后才勾选完成。
