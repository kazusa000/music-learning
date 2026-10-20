# AI 操作优先的 DAW 设计

2026-10-20制作反馈更新：混音使用`mixer.set`并回读`playback.get`，有兼容逐轨声音即能试听，不为单纯推子修改排整曲渲染。音源/音符编辑自动准备变化声音，工具回执区分`playback_available`与可选`render_job_id`；最终文件通过`project.export`生成。仍未实现常驻实时VST3宿主，复杂图需原生处理；见[即时混音](33-live-mixing.md)。

后续明确需求更新：用户希望 FL 风格前端，并可手动操作卷帘/音轨。现有编辑接口已开放给手动界面；AI 仍无需点击即可制作，不需要 MIDI 键盘。早期“用户只查看/讨论/试听”是历史范围，当前以 docs/22-fl-workbench.md 的直接操作与同源事务约束为准。

日期：2026-10-18。用户最新确认：软件的主要操作者是 AI，支持 VST3，所有工程操作应可程序化完成；不需要 MIDI 键盘。用户通过讨论、查看和试听提出音乐意图与评价。该定位取代初版以手工工作台编辑为中心的设计。

## 角色和第一阶段边界

AI 负责建立和管理工程、处理整曲、查询音乐、编辑音符、编排片段、选择音源、配置路由和效果、写自动化、渲染、比较与保存。用户的入口保留选音乐、讨论、查看结果和试听，不要求用户熟悉传统 DAW 操作。

第一阶段以离线渲染和可回读操作为优先，支持整曲、局部试听和实际 VST3 效果链。超低延迟现场演奏、MIDI 键盘、麦克风/吉他录音、多轨录音与复杂鼠标编辑均不作为第一阶段目标。工程仍保存完整的轨道、片段、路由和插件状态，能够继续编曲。

第一阶段插件格式为 Windows x64 VST3，包含乐器与效果器。VST2、32 位桥接、AU、AAX、CLAP 后续按需求评估。

## 操作完整性

本应用提供的每一项工程能力必须有类型明确、可发现的 API，并可从 SDK/CLI 调用。前端和 Agent 都调用同一个领域服务。核心流程必须在没有点击前端、没有操纵插件窗口的情况下执行完成。

外部 AI 助手可直接调用本地 CLI/SDK，内置讨论 Agent 使用同一服务；后续 MCP 适配只做协议转换，不增加第二套操作语义。操作能力不绑定某一家对话模型，也不要求先从聊天界面发起。

内部宿主仍需按 SDK 要求提供控制器、消息循环和必要平台设施；“不靠界面操作”不等于插件可以在任意无桌面服务环境运行。

| 能力 | 典型操作 |
| --- | --- |
| 发现与诊断 | describe_capabilities、query_state、query_jobs、query_problems |
| 工程与素材 | create/open/save/export/restore project、import_asset、start/retry/cancel analysis |
| 轨道与片段 | create/remove/reorder track、insert/move/split/duplicate/trim clip、set loop |
| 音乐事件 | query/insert/update/delete notes、edit controllers、edit tempo/meter/chords |
| 插件 | scan/list/instantiate/remove/reorder/bypass、inspect parameters/buses、capture/restore state |
| 音色 | list available host-managed presets、load supported preset/state、resolve sample dependencies |
| 混音 | set gain/pan/mute/solo、create bus/send、connect sidechain、inspect routing |
| 自动化 | create/update/remove lane、write time/value points、query interpolated value |
| 产物 | render excerpt/full mix/stems、freeze/unfreeze、measure audio、compare renders |
| 版本与研究 | diff/revert/branch、query conversations、add anchored note、inspect references |

所有批量修改支持事务、版本前置条件、幂等键和变更摘要；查询支持范围和分页。接口返回执行结果与实际状态，而非仅“命令已发送”。核心操作覆盖率按这张能力表逐项验收。

## 插件可控性的界限

VST3 兼容不代表插件的每个私有菜单都能通过统一接口访问。公开参数、程序、状态与素材加载方式分别探测，记录 supported/unsupported/untested，不能伪造成功。

参数使用稳定 param_id，并记录名称、单位、显示文本、归一化范围、离散步数、默认值、只读与自动化能力。显示单位的输入通过插件转换或经验证的映射转换，写入后回读。不要把归一化值 0.5 当成所有参数的物理中点。

可自动化参数通过音频处理参数队列按时间执行；不支持自动化的状态切换在停止/重建图阶段完成。预设目录、采样器文件加载、奏法映射与部分内部调制可能需要插件专用适配器，通用参数列表不能冒充完整声音状态。

某个插件若关键操作只能靠手工窗口完成，就暂不进入默认自动化工具箱；可以保留为扩展候选。开源插件便于研究和适配，但仍须经过真实宿主测试。

## 原生引擎与唯一工程状态

领域服务持有权威工程 revision。C++20 原生宿主接收不可变工程快照和渲染请求，加载对应素材、MIDI、路由、插件状态与自动化，返回带版本的产物和测量。引擎内部处理图是快照的派生运行状态，不建立第二套独立工程真相。

实时处理回调不等待数据库、HTTP、AI 或 Python。原生宿主在消息/控制线程准备资源后发布处理图。修改正在播放的工程时生成新 revision，再在安全边界切换；离线渲染一直绑定请求时版本。

P0a 已选用 JUCE 9.0.4 headless 宿主基础，自建受限图和工程桥接。Tracktion Engine 的高层工程模型暂不引入，范围/接口评估与验证见 [P0a 报告](benchmarks/p0a-native.md)。后续复杂路由的成本若改变此取舍，另做实测与决策更新。

## 插件状态和恢复

PluginInstance 保存 plugin class ID、厂商、版本、二进制摘要、总线布局、参数快照、component/controller 状态、预设/采样依赖及宿主配置。参数快照用于检查和差异，完整状态块用于恢复，两者不可互相替代。

恢复先解析插件和资源，按协议恢复状态并回读公开参数，随后做验证渲染。插件缺失或版本不符时保留占位节点和原始状态，提供冻结音频；禁止静默换成另一个音色后标记还原成功。

冻结音频与插件状态都绑定 revision。随机振荡器、噪声和调制可能令同一设置不产生逐样本相同输出：确定性夹具做哈希/样本比较，随机插件记录种子（若支持）、容差和固定已渲染版本。相同设置不自动宣称位级可重复。

## 运行隔离

插件扫描使用独立子进程，超时或崩溃后标记隔离，并保留可重试记录。渲染 worker 与工程服务隔离；一个插件崩溃时本次渲染失败，工程和已发布版本仍在。第一阶段不承诺同一渲染 worker 内每个插件独立隔离。

导出使用统一信号图：采样时间、速度/拍号、MIDI、自动化、路由和插件延迟补偿一致。处理动态延迟、前置音符/控制器和效果尾音。具有实时处理要求的插件可采用受控实时速率渲染，能力表记录差别。

## AI 制作循环

查询确定的工程状态 → 生成有范围的修改 → 校验并创建新版本 → 渲染 → 测量/按能力分析音频 → 比较版本 → 保存结论。

测量工具至少提供峰值、削波、响度、时长、静音比例和分段频谱摘要，后续增加更丰富的音乐评价。测量不等于审美判断；AI 没有实际音频输入能力时，应说明基于事件和信号指标分析，用户仍可试听评价。

每轮保存目标、操作、实际结果、音频引用和评价。让 AI 能反复试验同一段的和声、配器和效果设置，并为后续创作留下可追溯经验。
