# 项目开发约定

## 任务入口

先读 README、docs/01-product-plan.md、docs/05-development-plan.md。实现数据、编辑或播放前，再读 docs/03-architecture.md、docs/04-data-and-ai-contracts.md 与 docs/06-acceptance.md。

用户已确认的产品流程是：主页选音乐，完成整曲预处理，进入专用前端，与 AI 深入讨论整曲或局部，改编并试听；后续跨工程积累经验并创作。用户有较好的乐理基础。软件的主要操作者是 AI，支持 Windows x64 VST3；不需要 MIDI 键盘。还须阅读 docs/09-agent-daw-contract.md 和 docs/10-free-vst3-candidates.md。

## 实施要求

- 用户于 2026-10-19 授权设立持续目标，逐阶段推进 P3 至 P6，并重设计、美化前端。每完成一个 P、进入下一阶段前，必须通过 Codex desktop 的只读 get_usage_limits 核对账号周额度（windowDurationMins=10080，remaining=100-usedPercent），记录实际值。起点实查剩余 61%（用户估计 60%）。剩余严格小于 30%，或回到 100% 表示重置时，立即将目标设为 paused 并停止所有后续任务；不得自动恢复或等待重置后继续。绝不调用重置额度/消费重置卡的功能。无法可靠读取额度时同样停止，不能估算后继续。安全收尾只保存已有工作、记录状态和停止自有进程，不启动新开发/推理/验收。

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

P0a/P0b、P1、P2 与 P3 已完成，下一步 P4。先读 docs/16-continuous-goal.md、docs/17-daw-implementation.md、docs/benchmarks/p3-daw.md，再参考 P2/P1/P0。当前后端 45 项 + 原生/模型格式 34 项通过；P3 在本地 305 秒音乐上完成真实 VST3 制作、导出、冻结及恢复，300 秒脉冲的原生/浏览器同步通过。

代码在 src/music_learning（权威契约/领域服务、HTTP API、SDK/CLI、任务执行、pipeline/musical），scripts/pipeline_adapter.py（隔离模型/渲染阶段），services/api/migrations（冻结 0001 与 0002）、apps/web、packages/contracts。数据在 D:\music-learning-runtime\app-data，独立 Python 环境在 envs/app。通过 scripts/app.py start/open/status/stop 管理隐藏的服务及其归属。更新后先 stop/start，旧进程不会热更新。当前从源码 checkout + PYTHONPATH=src 运行。契约变更后运行 scripts/export_contracts.py，提交 schema/TS 并检查漂移。

所有工程写入走 Service；worker 发布使用同一领域服务和 SQLite 事务。幂等键、expected_revision、素材 SHA、事件 cursor 和 attempt fencing 必须保留。快照及实体投影同时提交；素材先本地原子发布、后提交引用，孤儿文件不当成功，不自动清理。Windows Job Object/gate 用于强杀清理；不要退回只有内存的 BackgroundTasks 队列。

原曲导入状态是 imported，不能直接标 ready。P2 七阶段齐备才发布初始 ready。P3 编辑创建新版本并使音频/冻结失效，完整 project.render 才发布该版本 ready；局部产物不可冒充整曲。revisions.restore 创建新版本，不能改标旧音频。默认初版与研究样本保留；SQLite 元数据备份不包含 CAS，P6 全工程备份仍待实现。

Pipeline stage/cache 只能在有效 fencing 下提交。缓存包含代码/模型/包版本/宿主/插件/资源与输入摘要，复用前检查文件；当前保守地使共享领域代码变化也失效。更新 backend 代码期间不要把混合代码版本的执行标成功。已存在声部或运行期间 head 改变时，新分析保存独立候选，不能覆盖人工修订；preprocessing.adopt 是显式操作。渲染/波形与 ready 绑定精确 revision。

P2 早期 NoteStart 类型诊断错误已修正并重跑，最终原始起音/MIDI 均 9,378；194 秒原始起音 1,257 / MIDI 1,252，5 个零时长事件保留。P3 原生与新流水线支持负 PPQ/弱起及后级插件延迟补偿，历史产物不重写；候选拍点仍待核对。卷帘只读，音符编辑由 edits.apply 执行；已验证版本 A/B，真实产品对话仍须 P4。

P3 代码在 daw/graph/rendering/audition/midi.py、scripts/daw_adapter.py 和冻结的 migrations/0003；38 项 API/SDK/CLI。原生执行 stereo DAG、post-fader send、基础立体声 sidechain、sample-aligned step/linear 插件及 mixer 自动化。插件生成 MIDI 路由、多声道/反馈/实时制作和私有菜单未支持。既有源时间不可改变，beat-timed 音符显式绑定 TimingMap。scope 同时检查旧/新事件；全轨插件/自动化要求 structural 全时段授权。冻结只接受当前版本的完整渲染，任何后续编辑保守失效。

最终正式 P3 工程为 76d5a25824704f5192c3f72caaec44a6，head 60303a60cca2452e8a79695e59392b4b；源 P2 工程 bbcdc48bcf6841b6adf221a895473234 未改。P3 早期分叉 8087e500d22c4e38a0adbaa4484bfe77 专用于浏览器恢复测试，当前是新建的恢复版本，未冒用旧音频。所有正式任务终态；阶段收尾停止自有服务/浏览器，后续按需启动。

本机模型环境/产物在 D:\music-learning-runtime，位置保存在被忽略的 runtime/model-runtime.json。不要把大模型再默认安装到空间不足的 E/C 盘。用户已完成 Hugging Face 许可/本机登录，三个模型均跑完 NAS 授权选取的 305 秒歌曲。NAS 只允许读取/复制，禁止写标签/封面/清单、移动、删除或在原目录生成产物；所有处理走本地 references 副本。源摘要/大小/mtime 已回读一致。

Codex App Server 已用既有 ChatGPT 登录验证；运行时固定 0.163.0-alpha.10.2 和四个组件，当前版本需要 code-mode host 路由动态工具。正式应用默认独立 codex-home（当前尚未独立登录），不提取或复制用户 Token；既有登录只用于 --use-existing-login 探测。shell/apps/MCP/多 Agent 等受控，不能把 P0 合成 music_probe 当成已存在完整音乐领域工具。

夹具采用 whole-song-v2 音频与独立 truth-v3.json，旧真值有已记录错误；不能覆盖原输出。MuScriptor 存在开头漏检/鼓漏音/声部合并，SheetSage2 存在时值和夹具结构问题；真实曲目的候选结构已有完整输出。+12 dB 增益实验反复无 EOS 后已终止，不作为成功配置。Surge 默认音色重开波形非确定，保留已生成 Render。

P0 探测 CLI/只读试听页继续独立保留。不要把 P2 初版结果工作台当成完整 DAW 或产品内 Codex 讨论。任务清单只有满足相应退出条件后才勾选完成。
