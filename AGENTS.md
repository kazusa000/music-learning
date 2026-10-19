# 项目开发约定

最新响应优化（2026-10-19）：用户将复杂音乐核对闭环后置，优先 AI 易用和加载速度。先读 docs/23-responsive-loading.md。启动器使用轻量 session helper；UI health 连接、单 SSE 的 live_only 初次订阅/断线 cursor 补发、事件刷新+15 秒兜底，生成中仅900ms回读活跃轮次，登录模式选择后本机记住，插件/工具详情按需展开。Service 只读命令 transaction(write=False)，所有写入和worker租约仍默认BEGIN IMMEDIATE，不削弱幂等/fencing。88项后端、真实浏览器13组编辑/原生渲染、模拟对话浏览器与真实服务断网停启恢复通过；原生/模型格式36项是前次保留证据，本次未重跑模型或Codex provider。已有整曲编辑器401ms、讨论输入411ms，空闲6秒36次查询降至1次，服务启动2681.1ms，均为本机单次观测非承诺。正式工程head未变，NAS未访问，所有自有实例/浏览器已停止；当前周额度剩余40%，未使用重置卡。P6听辨仍未通过。

最新前端（2026-10-19）按用户新要求扩展为FL风格制作界面，用户可手动操作卷帘与音轨，覆盖此前只读范围。先读docs/22-fl-workbench.md。DawEditor.tsx/dawEditing.ts提供单声部画入/拖动/改时值/力度/多选/复制删除、MIDI与音频片段编排、音轨增删排序命名与混音，全部写入仍走现有edits.apply/revisions.restore/Service；51操作/11任务无迁移。手动时间修改显式转sample锚定并清理旧记谱绑定，旧版本/源录音保留；拖动只是预览，取消不写入，历史只读，保存使旧音频失效，需实际离线渲染才可试听。撤销栈会话内有效，跨重启可用历史版本恢复。122项回归、独立真实sfizz夹具浏览器13组交互/重启重渲染与完整305秒9,378音符视图回归通过；正式音色工程head与snapshot不变。当前额度实查剩余43%，无重置卡；正式和夹具实例/自有浏览器已停止，NAS未访问，无音乐模型或新Codex provider调用，P6人工听辨仍未通过。

最新专项（2026-10-19）：统一乐器目录已实现，乐器为主入口、库为后台来源。先读docs/21-instrument-library.md的统一目录段。instrument_taxonomy.py定义稳定ID/父类/别名和明确映射；catalog每条可多instrument_ids，跨库汇集、同库拆分、同名靠唯一preset_id区分，重复ID拒绝。旧catalog仅兼容读取、不覆盖；新build写出分类，未知标unclassified，不能按文件名硬猜。当前3031条中23精选明确映射+2914原库类别映射+94待分类，已发声验证仍28；52分类节点不等于52个已安装乐器。新增instruments.categories，instruments.list的instrument_id/audio_verified和production.query的instrument_id/include_instrument_categories共用Service/API/SDK/CLI，当前51操作/11任务。前端“乐器”先选乐器交给AI选音色，来源可展开；只生成绑定revision/track的改编草稿，发送后才执行。原范围/幂等/状态/资源捕获约束保留。本次122项回归（86后端+36原生/模型格式）、真实目录API/CLI/停启与浏览器通过，无新的真实provider推理或歌曲渲染；正式工程head不变，NAS未访问，所有自有实例/浏览器停止。P6听辨仍未通过。

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

统一乐器目录专项收尾周额度实查剩余48%（已用52%，10080分钟窗口），未触发停止条件，未使用重置卡；所有自有实例/浏览器已停止。以下50%等数字为此前阶段记录。

P0a/P0b、P1、P2、P3、P4 与 P5 已完成，P6 自动工程验收已完成，人工听辨及有限参考待反馈。先读 docs/16-continuous-goal.md、docs/20-local-delivery.md 与 docs/benchmarks/p6-delivery.md，再参考各阶段记录。当前后端 78 项 + 原生/模型格式 36 项通过；P3 的 305 秒真实 VST3 制作和 300 秒脉冲同步证据继续保留，P6 补充完整当前流程及独立备份恢复。

代码在 src/music_learning（权威契约/领域服务、HTTP API、SDK/CLI、任务执行、pipeline/musical），scripts/pipeline_adapter.py（隔离模型/渲染阶段），services/api/migrations（冻结 0001 至 0005）、apps/web、packages/contracts。数据在 D:\music-learning-runtime\app-data，独立 Python 环境在 envs/app。通过 scripts/app.py start/open/status/stop 管理隐藏的服务及其归属。更新后先 stop/start，旧进程不会热更新。当前从源码 checkout + PYTHONPATH=src 运行。契约变更后运行 scripts/export_contracts.py，提交 schema/TS 并检查漂移。

所有工程写入走 Service；worker 发布使用同一领域服务和 SQLite 事务。幂等键、expected_revision、素材 SHA、事件 cursor 和 attempt fencing 必须保留。快照及实体投影同时提交；素材先本地原子发布、后提交引用，孤儿文件不当成功，不自动清理。Windows Job Object/gate 用于强杀清理；不要退回只有内存的 BackgroundTasks 队列。

原曲导入状态是 imported，不能直接标 ready。P2 七阶段齐备才发布初始 ready。P3 编辑创建新版本并使音频/冻结失效，完整 project.render 才发布该版本 ready；局部产物不可冒充整曲。revisions.restore 创建新版本，不能改标旧音频。默认初版与研究样本保留；P1 SQLite 元数据备份不包含 CAS，P6 application.backup/restore 已提供数据库与全部 CAS 的完整归档及独立恢复，使用边界见 docs/20-local-delivery.md。

Pipeline stage/cache 只能在有效 fencing 下提交。缓存包含代码/模型/包版本/宿主/插件/资源与输入摘要，复用前检查文件；当前保守地使共享领域代码变化也失效。更新 backend 代码期间不要把混合代码版本的执行标成功。已存在声部或运行期间 head 改变时，新分析保存独立候选，不能覆盖人工修订；preprocessing.adopt 是显式操作。渲染/波形与 ready 绑定精确 revision。

P2 早期 NoteStart 类型诊断错误已修正并重跑，最终原始起音/MIDI 均 9,378；194 秒原始起音 1,257 / MIDI 1,252，5 个零时长事件保留。P3 原生与新流水线支持负 PPQ/弱起及后级插件延迟补偿，历史产物不重写；候选拍点仍待核对。卷帘只读，音符编辑由 edits.apply 执行；版本 A/B、P4 真实产品讨论和 P5 对话编辑均已验证。

P3 代码在 daw/graph/rendering/audition/midi.py、scripts/daw_adapter.py 和冻结的 migrations/0003；38 项 API/SDK/CLI。原生执行 stereo DAG、post-fader send、基础立体声 sidechain、sample-aligned step/linear 插件及 mixer 自动化。插件生成 MIDI 路由、多声道/反馈/实时制作和私有菜单未支持。既有源时间不可改变，beat-timed 音符显式绑定 TimingMap。scope 同时检查旧/新事件；全轨插件/自动化要求 structural 全时段授权。冻结只接受当前版本的完整渲染，任何后续编辑保守失效。

最终正式 P3 工程为 76d5a25824704f5192c3f72caaec44a6，head 60303a60cca2452e8a79695e59392b4b；源 P2 工程 bbcdc48bcf6841b6adf221a895473234 未改。P3 早期分叉 8087e500d22c4e38a0adbaa4484bfe77 专用于浏览器恢复测试，当前是新建的恢复版本，未冒用旧音频。所有正式任务终态；阶段收尾停止自有服务/浏览器，后续按需启动。

本机模型环境/产物在 D:\music-learning-runtime，位置保存在被忽略的 runtime/model-runtime.json。不要把大模型再默认安装到空间不足的 E/C 盘。用户已完成 Hugging Face 许可/本机登录，三个模型均跑完 NAS 授权选取的 305 秒歌曲。NAS 只允许读取/复制，禁止写标签/封面/清单、移动、删除或在原目录生成产物；所有处理走本地 references 副本。源摘要/大小/mtime 已回读一致。

Codex App Server 已用既有 ChatGPT 登录验证；运行时固定 0.163.0-alpha.10.2 和四个组件，当前版本需要 code-mode host 路由动态工具。正式应用默认独立 codex-home（当前尚未独立登录），不提取或复制用户 Token；既有登录在 P4 已扩展为显式 login_mode=existing 的正式讨论模式；默认仍独立登录。shell/apps/MCP/多 Agent 等受控，不能把 P0 合成 music_probe 当成已存在完整音乐领域工具。

夹具采用 whole-song-v2 音频与独立 truth-v3.json，旧真值有已记录错误；不能覆盖原输出。MuScriptor 存在开头漏检/鼓漏音/声部合并，SheetSage2 存在时值和夹具结构问题；真实曲目的候选结构已有完整输出。+12 dB 增益实验反复无 EOS 后已终止，不作为成功配置。Surge 默认音色重开波形非确定，保留已生成 Render。

P0 探测 CLI/只读试听页继续独立保留。不要把 P2 初版结果工作台当成完整 DAW 或产品内 Codex 讨论。任务清单只有满足相应退出条件后才勾选完成。

P4 讨论在 dialogue.py/dialogue_worker.py/codex.py、冻结迁移 0004 和 Discussion.tsx；45 项统一操作，50 项后端 + 34 项原生/模型格式回归。先读 docs/18-music-dialogue.md 与 docs/benchmarks/p4-dialogue.md。讨论独立租约通道，不占音频 worker；原版本/区间固定、结果持久化，provider thread ephemeral，连接丢失不自动重试消耗额度。引用由服务生成且完成时校验；P4 不发送音频、不编辑。正式 P3 工程 head 未变，新增 7 轮讨论和 1 条草稿笔记，实际停启回读一致；阶段收尾已停止所有自有服务。

P5 已完成，先读 docs/19-conversation-arranging.md 与 docs/benchmarks/p5-arranging.md。48 项统一操作，冻结迁移 0005；arrangements.apply 原子保存限定范围的新版本和完整渲染 job，独立收据保留，模型失败不撤销已提交事务。P5 操作联合按 op 判别，生产查询提供真实参数和已有自动化 ID；patch_plugin 支持捕获音色和参数。59 项后端 + 35 项原生/模型格式通过。正式 P5 工程 75f51cbf82844ff4a1d1fc2f1ea5dda3，head 83979852227f4318a0a242644c67dd30，四组真实改编完成，最终 render 4cedd30f25bd4f50b58f3a79b7e262ab；9368 音符。hybrid export 79bc5a0f89f1423d821129e15970cac3，60–90 秒移除原 bass，加新贝斯，区间外 PCM 差 0；输出/原始报告均在 D 的 p5-production-v4。P5 restart 回读 8 轮讨论与收据、笔记、版本/渲染一致，所有自有服务停止。人工听辨未完成。

P6 自动工程部分已完成；先读 docs/20-local-delivery.md 和 docs/benchmarks/p6-delivery.md。用户已反馈结尾配器缺失；按新授权补齐音色库和声部映射，仍须新版实际听辨，第一阶段不要虚假标 complete。已通过105测试（70后端+35原生/模型格式），整曲393.263秒61/61块，P6工程 ab11b3e05e464363a7e1d8d358fe996f，head c7cfe0ddb9004b4e8e8816ab7343aa99，完整render34ee6304fa7045fa9b657c6fab68d1c1。首次unsupported patch_note被拒绝，正式新增compact patch_note并回归后在同一项目恢复通过；旧原输出未覆盖。
P6完整备份 H:\music-learning-delivery\backups\p6-v1\music-learning.zip，17404910458字节、639 CAS、12工程，SHA d227102e31143caf70e056d46403b9ec790dc73a152dad035582526cdf19d960。H是已核实USB本地盘，NAS未访问。独立恢复 H:\ml-restore-p6-v1（端口8766）元数据一致、全新会话、真实Surge/CHOW/sfizz资源重定位发声/停启通过；正式D和恢复H服务/自有浏览器都已停止。新application.backup/restore/diagnose任务共用jobs API/SDK/CLI，48领域操作/10job kinds；恢复只到新目录、不复制凭据，未完成任务标失败需显式重试，不能开备份自动消耗额度。
人工听辨入口 H:\music-learning-delivery\listening-p6-v1\index.html，6完整音频+24片段，candidate-review.csv修正列为空。已收到用户对290–305秒配器缺失的负面听辨反馈，已定位统一INIT映射缺陷；用户明确要求先补齐免费音源/音色库、预设选择与声部映射，当前实现及验证见docs/21-instrument-library.md。不能把负面反馈或新版技术验证当听辨通过；不要重复问账号/许可。此前等待反馈的goal已blocked，当前按新的具体任务授权推进；额度停止条件继续有效，禁止使用任何重置卡。

P6最后前端修正：版本变化保留并按源时钟夹紧选区，避免60–75秒被自动扩到整曲末尾；apps/web/p6-selection.mjs真实浏览器通过，不触发推理。最终UI截图 D:\music-learning-runtime\benchmarks\p6-selection-v1\workbench.png；试听页浏览器验证在p6-listening-browser-v1（仅媒体技术播放，非听辨）。Source当前自有服务停止；用户用start.cmd可打开正式D数据，music.cmd统一调用CLI。

音色库更新：H:\music-learning-instruments\libraries 为本机新音色目录，六套下载固定commit/摘要/许可，资源不进Git。3,031条预设（含3,008个Surge FXP文件），28个精选预设从依赖副本验证。instruments.list/recommend、instrument.capture持久任务、patch_plugin.preset_capture_job_id共用API/SDK/CLI；内置对话可用preset_capture，禁止把资源清单塞进模型上下文。新预处理必须有库，未知映射明确失败，不回退INIT；旧P0诊断单独保留。所有改音色仍需全时段structural授权，同工程成功capture，保持音符/原工程不变。sfizz set_cc需初始化并状态回读后保存，SFZ同一行include/宏依赖必须收齐；Surge适配严格固定1.3.4。正式音色分叉4012a10f50574b2395d4ec4a9d8f27b5，head473651f138a34e42a5a7c27d8eecd1ed，整曲219822ad67bd44adb3b0822530b5708d；9,378音符未改，原head未动，新试听H:\music-learning-delivery\listening-instruments-v1\index.html。

专项最终回归114项（78后端+36原生/模型格式）通过。Windows任务路径用单调attempt和短token提示，完整fencing仍存SQL/input.json；解码源临时名按排序索引缩短，旧任务路径不移动。未知音色或音域越界明确失败，不移调/删音掩盖。新组装/渲染适配器在H独立CAS回放既有模型原输出通过，没有重新跑三模型，也未发布新的预处理DB任务。正式音色版停启与60–64秒重开渲染通过，所有自有实例/浏览器已停止；额度最后实查剩余50%，无重置卡使用。
