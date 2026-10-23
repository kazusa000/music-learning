# 新音源全量入库与共享兼容恢复（进行中）

## 2026-10-23 自动跟进保持暂停

本次唤醒的末条约束明确要求保持暂停，只有用户明确恢复才启用；因此未启动新控制、验证队列、发布或预热。发现调度配置实际为 ACTIVE，已通过应用的 automation_update 将原 keyscape 跟进改回 PAUSED，保留原提示、周期与本音源导入聊天归属；未唤醒开发聊天。此前任务的完成不视为恢复授权。

本次只读核对：资源准备 metadata-refresh-01 已 completed；full-e 为 blocked_no_automatic_retry，正式表和固定文件终态比较均相同；Ample retry4 监督已 completed，20失败/1通过。原记录中的 PID17008、32840、49692、47024 均已退出，旧 locator 的 started/waiting 不代表当前运行。Kontakt 路径相关插件标识共享修复已提交 b5d27bd，51项专项通过；真实首开/空机架恢复尚待用户恢复后进行，不能计作新增入库。保留所有旧结果与资源，未访问 NAS/tmp、未加载采样器、未写正式工程或更改固定文件。

## 2026-10-23 04:27 清单修订审计与实际加载排查

当前准备 canonical locator 已改为 `metadata-refresh-01`，实际32840/ctime1792722161.8696787；运行时、CSS及CSS Solo已审计结果复用，Factory按新输入修订继续完整SHA审计。旧清单仍保留：两项 `.nkc` 大小不变但时间戳与原清单不同，原清单没有逐文件SHA，不能声称内容未变。修复 `56f37db` 只在全清单差异均为同大小普通NKC时，计算其当前SHA并建立独立E修订；主资源/其他大小变化成组拒绝，源文件不写入。新修订位于 `kf22/prep/inventory-revisions/metadata-refresh-20261023T022431.108684+0000/kontakt-factory.json`，SHA `54320cdd06fa3ebd99fc6b4fddc7f06d8fffcd61b0a9ae65a1437c8bf4fd1551`。19回归通过、Ruff通过；旧失败不改。04:27读回Factory审计1453/3699，未终态。

`full-e` 已于02:24:15 UTC终态退出，正式表与14项固定SHA相同。CSS7失败、CSS Solo11失败中，实际5项为源加载超时，13项已完成Lua转换但在下游报 `Plugin identifier not found`；不能把18项统称为未加载或音频失败。固定原始NKM/回执/执行报告保留。独立诊断将复用已核实转换结果，核对克隆后模块实际descriptor/identifier及严格class/版本/SHA，先证明首次/空机架恢复的真实声音再建新完整固定队列；不修改旧full-e或提前重跑992项。新的full-job准备器 `75a8412` 支持显式清单修订并冻结到独立run，计划从冻结文件读取路径与SHA；37测试与Ruff通过，不重写旧清单或隐瞒变化。

Ample retry4先前因IO暂缓实际退出（2失败/1deferred/18pending），已由 `fe26cc2`/`cb5e295` 的持久监督恢复同一固定队列：实际PID47024/ctime1792722001.751663，检查点 `20261023-ample-shared-fix/failed-only-source-profile-retry-4-supervision/supervision.json`。每次需重核PID与命令；终态项JSON哈希保持，累计24小时预算，H忙不启动新probe，不重试失败项、不新建批次。10项回归及Ruff通过。下一次跟进应看此监督检查点和retry4，而非只读已完成的retry3。原67通过和retry3新增14通过仍未发布，其他参数/Strummer演奏问题继续隔离排查。

## 2026-10-23 02:59 旧副本策略兼容修复

当前准备为 `transfer-compat-fix-01`（实际 supervisor41660/ctime1792717145.7087245），完整固定验证为 `kf22/full-e`（实际49692/ctime1792717161.6561122），仍从既有两个 canonical locator 定位。full-e计划SHA `8a09b5c5f243ebb2ddd7956ad0099ca1eabdfbeb4474fccfdf379e1720792167`，当前等待资源结果；未开始992项音频验证、未发布新Kontakt条目。进程身份须每次重新核对。

前次 `vendor-assets-fix-01` 因新策略期望硬链接、既有资源实际为旧规则创建的独立副本而拒绝；full-d 已终态，正式表和14固定SHA均相同，旧报告/文件保留。共享修复 `1019603` 核对打开句柄及命名路径签名、大小和源/目标完整SHA，匹配时保留并记录实际 independent_copy，不强行改链接、不替换或改属性；profile 与源同 inode 仍拒绝。3份完整历史计划的 E 预检确认29条兼容迁移记录，无 journal 一致性违规。11回归通过/1权限跳过、Ruff通过，夹具由统一runner清理。新任务继续遵守H预算，不用放宽发声/状态/身份规则处理存储问题。

Ample 已另建固定 `20261023-ample-shared-fix/failed-only-riffer-retry-3`，仅40个原失败ID，67个原通过不重复；4个HM2和1个T-Track参数不符独立保留，继续排查。02:59读回为1通过/1运行/38排队，尚非终态，不称入库。retry-1丢失factory SHA的适配失败及retry-2的真实参数不符均原位保留。本次只执行“音源导入”聊天已有跟进，未创建、转交或改变定时任务启停；不唤醒开发聊天。

## 2026-10-23 02:29 资源分类修复并续跑

当前准备入口仍为 `runtime/kontakt-preparation-job.local.json`，已改用 `vendor-assets-fix-01`；当前固定完整验证为 `kf22/full-d`，入口 `runtime/kontakt-full-job.local.json`，实际 PID 6244/ctime 1792715354.8744364，计划 SHA `73aee0a1a952325bb80cc141bb0728d457a8083efce6182d98cb4ed6184bcf42`。应每次重核身份，PID 不是永久运行证明。full-d 仍等待准备审计结果，992 款音频验证尚未开始，未入库。自动跟进已纠正到“音源导入”聊天 `01a15b37-7167-78c0-9586-ecc83480fa09` 并保持 PAUSED，不得再唤醒“开发”聊天或擅自恢复定时任务。

旧 `readonly-copy-fix-01` 在实际完成 7321/8481 文件后停止：静态 NKX 卷积容器位于 Presets 树，原大资源分类没有覆盖该根。不是缺文件或新发声失败。共享修复 `94e02f5` 按 Presets 根和严格静态容器/音频后缀分类，不按单个文件名特判、不允许 UserData 硬链接、不放开任意大文件复制。对完整冻结 plan 的 8481 项做 E-only 检查，全部 4 个大文件已分类、0 未分类。8 项回归通过/1 权限跳过，Ruff 通过；自动测试 runner 已清本轮夹具并保留报告。新准备在同一目标逐项复核和复用已有资源，全部旧报告及原失败保留，旧 full-c 已退出，终态正式表与14项固定文件均相同。

Ample 原始链已全部终态：LP17通过/18失败、M15通过/17失败/1缺AGMPk、SC35通过/10失败，总计67通过/45失败/1缺采样。原批不改。独立失败来源清单在 `20261023-ample-failure-retry-1/retry-plan.json`；共同原因和单独固定重试继续处理，不能把该清单或67项通过称为发布入库。

## 2026-10-23 自主修复与续跑入口

用户要求发现问题直接处理，不逐次请求批准或只汇报阻塞；仅全部收尾或确需用户材料时反馈。当前 Kontakt 准备入口 `runtime/kontakt-preparation-job.local.json` 已切到 `readonly-copy-fix-01`，完整验证入口 `runtime/kontakt-full-job.local.json` 已切到独立固定 `kf22/full-c`，实际 PID 分别 26132、276（均须按命令和 creation_time 重新核实，不能把旧 PID 当运行证据）。Ample 仍由 `runtime/ample-factory-chain.local.json` 继续原队列，跨来源并行，H 繁忙时保持有界等待。

旧 `path-fix-01` 在 Runtime 文件 3686/8481 后遇到只读说明书的临时文件清理错误，旧 `full-b` 随之正常阻断，均已退出且全部原报告保留。共享修复 `169aa3d` 在复制元数据前先完成自有临时名解除，保留最终文件的只读属性、内容 SHA、来源签名和禁止覆盖检查；E 内先复现 WinError 5，修后相关 25 项通过、1 项权限跳过，Ruff/编译通过。新 attempt 在同一目标逐项检查已有文件并继续，未删除、改属性或移动旧 H 临时残留；该残留仅在新 attempt 的 `unmanifested-residue.json` 记录，不进入执行/安装清单。

`full-b` 的保护原比较为正式表不等、14 固定文件相同，不能写成全相同。同时间正式 API 确有 job/revision 事件；独立分析保存在 `kf22/reports/full-b-protection-assessment.json`，原比较保留，不恢复旧正式基线。新批仍不调用工程写入 API。

后续唤醒先核验这三个入口的真实进程和队列：运行正常就保持，不重开第二批、不改活动快照；异常停止则归因、修共享规则、增加原因回归，使用独立 attempt 继续未完成项。Ample 原始全部队列终态后才建立失败项独立重试，保留原通过和原失败。Kontakt 的 992 款验证固定输入、来源身份、完整状态、两次空机架恢复及实际发声；当前 `publish=false`，验证结束后重新核对发布计划，验证新分类/状态可由产品 API 和实际应用消费，再 additions-only 发布和预热，不把准备、排队或控制样本称为入库。发布/服务切换遵守正式工程和活动任务保护，不用正式工程测试。

MODO 仍按用户指示暂缓。NAS、tmp 原素材、旧固定验证器、旧报告和历史拒绝清理路径保持；音源资源仅在 H 指定 Sound Library。新单元测试按 AGENTS 的清理入口与目录规范执行，整个历史 instrument-intake 证据不清理。收尾更新文档并仅提交本任务文件；不混入并发维护任务。

## 2026-10-23 完整服务回归

当前源码完整 service 回归为 559 通过、2 跳过，耗时 202.69 秒。日志与 JUnit 保存在 data/maintenance/instrument-intake/kf22/reports/service-final.{log,xml}（仅维护目录）。子进程继承实际源码 PYTHONPATH；前轮并发编辑期间的旧失败记录保留，不覆盖为成功。此结果覆盖服务和共享规则，不等于 Kontakt/Ample 全量实际发声或目录发布完成。资源准备源码已独立提交 1c18844，原始准备失败与修正 attempt 均保留。

## 2026-10-23 并行后台已启动

最新授权允许跨来源并行。Ample 持续监督器继续同一原始队列；IO 暂缓时保持 24 小时有界等待，恢复 deferred/pending，不重测或改写已经失败/通过的条目。定位仍为 `runtime/ample-factory-chain.local.json`。00:12 只读快照为 LP 原批 17 通过/18 失败；AGM 3 通过、1 原失败、27 待测、1 IO 暂缓、1 缺 AGMPk，尚非整批终态。后续失败独立重试仍待原批结束。

Kontakt 资源监督器 `path-fix-01` 已实际启动，定位 `runtime/kontakt-preparation-job-path-fix-01.local.json`；正确来源为 H 库旧 `.staging/20261021-kontakt-runtime`，新目标 `Kontakt/Runtime`，三库为 `Kontakt/Libraries/{css,css-solo,kontakt-factory}`。00:12 记录已完成运行时文件 2,176/8,481，因 H IO 压力等待，不能算成完成资源 SHA 审计或完成音色验证。进度仅为该时点快照，后续须读 E 检查点与实际进程，不拿 PID/旧 locator 当存活证明。

完整 992 款来源对应 163 个已核对型号，静态双语/型号一致性无冲突。固定全量监督器 `kf22/full-b` 已启动，定位 `runtime/kontakt-full-job.local.json`，当前等待资源准备结果；依次处理 CSS 7、CSS Solo 11、Factory 974，尚未开始逐款发声验证。该批先保存完整验证结果，目录发布和正式 API 部署需在结果核对后执行；不能提前称已入库。代码、预设/空机架输入及原生宿主 SHA 已固定，运行中不修改此快照。

首次资源准备存在 Python 路径转义错误，旧 `runtime-fail.json` 与 `kf22/full-a` 的阻塞报告完整保留；full-a 没有运行逐款验证、没有写入 catalog，正式表与 14 项固定文件前后相同。修正使用独立 attempt；full-b 只忽略启动前已固定 SHA 的那份历史准备失败，当前 attempt 新失败仍会阻断，不抹掉失败记录。

新增恢复检查固定整份 plan 与代码 pin 清单，逐库对照全部来源路径，重放原始 Lua 回执与执行记录并复算 NKM SHA；候选、WAV、回执路径限于自有证据根且拒绝目录联接。固定 Kontakt 版本的全部 4,145 个参数 ID 集合也参与校验。发布恢复需要 catalog 与原已核对 additions 逐项相同才识别已发布，不覆盖外部变更。外部入库后的完整状态已经通过实际 `task.execute → import_admitted` 夹具回归，Ample Riffer 与 Kontakt MIDI 上下文均不回退旧验证器；这些是软件回归，不冒充实际新库发声验收。

## 2026-10-22 继续：原生控制通过、全量队列准备与并行授权

用户确认 N7 已结束后恢复本任务，随后明确要求“不要串行，可以并行”。Ample 与 Kontakt 可以并行推进；每个来源使用独立进程、资源准备目录、固定输入及报告。H 盘繁忙仅推迟新的大文件读取，不中断已有条目。MODO 仍按用户要求暂缓；正式工程、NAS、旧报告、固定验证实现和清理拒绝目录不动。N7 已部署，不能再按旧长测 locator 等待。

Kontakt `km22/deferred-controls-b` 的空机架、长名称 Clavinet、Grand Piano + Clavinet 三组实际进程均零退出。首次/从独立空机架重开保持完整 4,145 项参数与实际组件身份；单款两次 RMS 约 0.00542/0.00556，双乐器组合约 0.00922/0.01260，削波 0。原始请求、响应、完整状态、WAV 和执行记录的 201 项独立复核通过，原目录保留。其前后正式表、14 项固定文件和目录摘要一致。这里的三组是控制证据，不能计作 992 款已入库。

新增全量流程：从官方 Lua 加载/保存并读取真实通道、音域、力度、静音和独奏→独立源状态捕获→固定候选、完整 VST3 预设和分类→两次均从空机架加载捕获状态→核对实际组件身份、所有参数和解码音频→保留失败→CAS 追加入库。Kontakt 的 bank 数量与稀疏 instrument indices 数量分别保存，不能假定两者相等。

共享 `performance_profile` 区分普通音符上下文和 Ample Riffer 的播放触发规则；型号、变体与双语标签沿用既有选择过程。资源布局为 `H:\music\Sound Library\Kontakt\{Runtime,Libraries,Presets,Manifests,Admissions}`；旧 `.staging` 不迁移覆盖。只把小型预设和清单放入工程 CAS，大采样库及运行时按已安装清单原位检查。外部验证得到的完整状态将以内容寻址记录接回应用流程，避免首次应用退回不理解原预设演奏方式的旧试音流程。

新增入口 `scripts/instruments/{prepare_kontakt_full_job,run_kontakt_full_intake}.py`。准备器固定 E 内代码与队列，运行器保存逐项结果、失败明细、正式表/固定文件前后核对；不隐式重试失败项，不修改已结束的原批次。当前全量尚未开始，资源准备与新规则回归正在进行。42 项转换、限量续队、空机架恢复、演奏上下文及完整状态缓存专项通过；实际最终库路径和全量结果待核对后另记。

用户要求完成 Ample、MODO BASS、Kontakt 音源的全量入库，允许重写适配器和独立逆向诊断。上一轮 `instrument-intake-idle-retest.md` 的暂缓结论保留为历史，不能据此跳过本轮工作。

## 边界与运行位置

正式工程与正式服务不用于验证、不写入；源包和NAS保持只读。资源只进入 `H:\music\Sound Library`，独立证据统一在 `data/maintenance/instrument-intake/20261021-all/`。本轮开始的正式九表/head、14项固定宿主与验证文件、旧1252项catalog摘要保存在 `before.json`。不改并发本地引擎验收文件、不替换旧固定宿主，不改历史报告或清理拒绝路径。

Ample 全量队列113项（M33、LP35、SC45）分别执行首次加载、重开、完整状态与真实音频检查。初期采用跨来源串行，现已由上面的用户并行授权替代。H盘队列或吞吐繁忙时推迟新探针，不中断活动条目。进度以队列实际终态为准，不把已准备库存计作入库。

## 共享发布器

`src/music_learning/instrument_intake_publication.py` 消费独立验证器的原始证据，不自己调用插件、修改工程或复制大音色库。统一过程为：建立候选队列→逐项记录首次/重开回执或失败→重新核对证据生成发布计划→显式CAS原子合并catalog。

- 候选固定产品、型号、变体、乐器类别、原始资源、插件与全部预期参数/状态身份；型号、变体中文显示沿用现有 labels API，产品名保持英文原名。
- 回执必须包含真实请求、响应、零退出执行记录、完整捕获状态及两次WAV的相对路径、字节数和SHA。插件专用解析器从状态读取身份；不能把预设名或通过标志当证据。
- WAV按RIFF编码解码，支持PCM与IEEE float及extensible；逐项复算RMS、峰值、削波，与宿主读数比对。零音频、非有限值、削波、参数/身份不符均不发布。
- 核实磁盘实际插件模块、已安装资源清单与候选资源SHA，稳定文件签名缓存避免同一批反复读取大包。VST3 bundle按唯一Windows x64模块解析。
- 发布计划从重新验证的候选和回执生成，不信任队列内可改写的admission；复用现有 `.verification.lock`，保留旧条目、拒绝重复ID及陈旧catalog SHA。新增条目进入既有型号/变体选择与试听预热流程。

独立专项22项通过，Ruff通过。真实 Ample SC 5秒float WAV的解码RMS/峰值与原生记录差异小于 `5e-16`。这验证共享发布规则，尚不代表新音源已全部发布。

## Kontakt 运行时恢复

已从NI官方 Native Access 安装包离线提取真实 `ni-platform.dll`，没有安装或启动 Native Access/NTKDaemon。原始安装包和DLL的Authenticode均为有效Native Instruments签名。DLL只放在H库的独立Kontakt运行时 `Native Instruments/NTK/`，没有伪造同名空DLL或修改系统目录。

原始安装包 SHA256：`82d7b7977d4fbc19db32aee8778c75906f6ddf935bcf2958f2d8d45141b2b2cc`。
DLL SHA256：`52db2f30d1a75efcf69710918e25050316654f60759604ad2248b87cbcd0a575`。
提取链和签名证据见 `kontakt/downloads/`、`kontakt/platform-copy.json`。

缺DLL错误消失后，自有独立程序的正常启动栈与内部参数证明它停在 `Audio and MIDI Settings`。NtCreateFile追踪确认读取独立Settings.cfg，其 `AB2 AudioDevice` 为空，Lua尚未打开。用临时不匹配的sentinel及自有进程硬件断点读取了21个设备的规范身份字符串；探针结束精确恢复配置。随后在该独立配置选择枚举得到的 `WASAPI (Shared Mode) NVIDIA High Definition Audio`，正常隐藏启动约1.797秒完成Lua：`Kontakt=table`、`load_instrument=function`，日志明确脚本执行成功。没有接管用户界面或修改系统音频设置。证据：`kontakt/audio-config-static/`、`kontakt/wasapi-lua/`；语言资源warning仍在，但没有阻止本次API执行。

空NKM/NKI包装成VST3状态时宿主返回接受，但没有真实乐器身份/发声证据，不能据此入库。该负面证据保留；后续必须完成真正的NKI加载、保存状态、首次及重开发声。

### 实际Grand Piano通路与三库识别

官方 `Kontakt.get_file_info` 最初对Factory2、CSS、CSS Solo均返回 `??? (not registered)`。根因之一是把NICNT的 `ProductHints/Product` 直接当作运行时 `LibraryHints.xml`；该运行时查找的是 `LibraryHints/Library`。`kontakt_registration.py` 现在保留原厂Product字段，只转换容器，合并现有库并拒绝冲突标识，不生成授权记录或替代产品ID。独立配置中的设备设置不变。三库的真实API读回分别为 `Kontakt Factory Library 2`、`Cinematic Studio Strings`、`Cinematic Studio Solo Strings`，见 `kontakt/file-info-three-registered/`。CSS Solo内容版本没有确证，保持缺省，不猜版本。

真实 `Grand Piano.nki` 由官方Lua加载并保存Multi，约14.437秒完成；getter读回1个乐器、Grand Piano、MIDI通道0。直接把NKM当成IComponent状态会失败，不能混用两种容器。正确路径是Lua保存NKM→仅在独立私有profile临时安装startup default→全新VST3实例捕获完整状态→重开该状态。`kontakt_default_multi.py` 用互斥锁和持久恢复日志保护这个过程，原default在自有进程退出后按原字节/mtime恢复，外部修改保留并报冲突。

该真实钢琴的首次/重开音频一对合计约5.75秒：RMS `0.0087826594`，重开RMS `0.0087867594`，峰值 `0.13513878`，无削波，完整参数恢复一致，私有default精确恢复。原始请求、响应、执行记录、短WAV及状态见 `kontakt/grand-native-roundtrip/`。尚需把原厂乐器身份和真实捕获的NI状态连成可复核的解析链；这些音频结果不能先当作全库入库完成。

### 批次与资源共享规则

`kontakt_import.py` 的队列v3以库ID+相对路径生成稳定预设ID，防止不同库的同名路径覆盖。官方Lua保存后原子发布getter回执，正常返回脚本；不再调用会导致该程序fail-fast的 `os.exit`。`kontakt_runner.py` 通过自有Job Object按项串行执行，核对回执和NKM SHA后终止自有程序，并等待整个进程树退出才推进下一项。超时、崩溃及失败回执分别保留，不能写成转换成功。跨队列共享同一runtime锁；H盘繁忙时在claim前退出为 `deferred_io`，条目仍pending、不增加失败。CLI为 `scripts/instruments/run_kontakt_batch.py`，转换成功只标 `converted_pending_validation`，后续仍须完整身份与首次/重开发声。

`kontakt_library.py` 提供仅准备资源的接口：可重写的NKI/NKM/索引/元数据复制为独立文件，采样及只读资源容器在指定H库内用硬链接，原 `.nicnt.txt` 只在目标规范为 `.nicnt`。先验完整库存大小/mtime，已有冲突文件不覆盖。大容器SHA明确pending，不生成installed manifest或发布条目。当前只完成独立回归，尚未运行三套库的全资源准备。

本组Kontakt规则44项通过，1项Windows目录symlink权限不可用而跳过；Ruff通过。实际子进程回归覆盖原子ACK、超时、崩溃、完整树退出、I/O推迟；近MAX_PATH下的恢复journal与原子文件名已修复并实际测试。这里的单元/生命周期测试不替代真实音色批次。正式九表/head与原1252项catalog在本轮核对保持一致。

## 仍在推进

Ample完整批次与失败共同原因、MODO型号字段被加载器读回默认值、Kontakt捕获状态的语义身份及992款（Factory2 974、CSS 7、CSS Solo 11）的逐项通路仍在推进。新的全量结果和入库数量将在原队列完成及实际发布后补录；不以代表项通过替代全部结果。

## 用户要求暂停

用户因Token额度不足明确要求暂停。已停止所有音源入库/验证/逆向工作，不自动恢复。LP队列在Em9(Strum)后安全暂停：10通过、3失败、22待测、0运行，状态`paused_at_boundary`；队列与`pause.request.json`保留在`ample/lp-factory-fix-20261022-a/`。MODO/Kontakt自有探针均已退出，Kontakt全量批次尚未启动。未完成的NI状态身份解析器及其他未提交代码保留原位，不用于发布。恢复入口和实际状态持久保存在忽略的`runtime/instrument-intake-pause.local.json`；只有用户明确要求恢复才清除暂停请求并续原队列，不另开重测批次。

## 2026-10-22 用户明确恢复

本次恢复前重新核对正式九表/head、14项保护SHA及1252项目录，均与暂停前一致，独立基线保存于`data/maintenance/instrument-intake/20261022-resume/before.json`。原LP队列经现有恢复入口继续剩余条目，固定8文件与宿主指纹不变、没有第二个批次。发现`status.json`派生计数与队列不一致，当前以单次读取`queue.json`的35个唯一ID为统计权威；保留原始错误计数，固定验证器运行中不改，结束后修共享恢复统计规则。

新增`instrument_resource_manifest.audit_resources`供各来源共用：显式文件清单逐文件读取完整SHA，按源大小/mtime、实际文件身份及审计代码/计划指纹记录独立E盘checkpoint；仅已完整读取且未改变的项可在恢复时复用。磁盘忙在文件边界推迟，失败不创建installed manifest；全部完成后复查所有成员，再原子发布内容寻址manifest，不写目录或工程。7项真实文件回归/Ruff通过，涵盖跨读取块完整SHA、恢复不重读、源变更拒绝、失败时无半份manifest。尚未启动H大容器审计，避免与LP样本加载竞争。

Kontakt库准备入口为`scripts/instruments/prepare_kontakt_batch.py`，限定H指定目的地及E维护目录，生成真实源SHA绑定的v3队列；只是资源准备/转换入口，不把它称作全量发声通过。后续仍须解析状态身份、首次/重开音频及型号/变体/双语/试听发布。

### Kontakt 重开验证纠正（恢复轮）

复核旧 `grand-native-roundtrip` 的两次启动发现，首次和重开都预装了同一份Grand Piano Multi。这能证明该启动方式发声，却不能排除`setState`失败后仍由预装音色发声；上文“重开完整状态”不能当作干净机架恢复已通过。旧报告和WAV保留，不发布到catalog。

新 `kontakt_audio_probe.py` 强制分离首次/重开两个startup context：首个context退出后，第二次由调用方安装独立验证的0乐器Multi，再加载第一次真实捕获状态。先验证状态字节/SHA和参数完整性，再比较两次完整参数、模块身份、实际解码音频和原生读数。多声道短句保持单音最少约333ms，超过6个音符的机架须拆分通道探针，不把大量音符压成几毫秒。7项生命周期/证据回归通过，包含首次退出早于空机架进入、坏状态/无声重开/参数恢复失败均不产生通过索引；这些夹具不是实际Kontakt验收。

实际新控制保存了0乐器 `empty.nkm` 与Lua回执，但新的首开请求在60秒超时，尚未进入重开。自有进程均退出，私有default SHA恢复一致；栈位于插件初始化/默认Multi读取，此请求没有`state_base64`，不能把它归因于恢复状态失败。原始证据在 `data/maintenance/instrument-intake/20261022-resume/kontakt-clean-rack/`。下一步对照旧最小请求和新增模块绑定字段，并以官方Lua读回原始/派生Multi身份；NI容器结构等形和字节保留暂不等于语义可加载。

### 发布后试听与外部素材边界

`instrument_intake_previews.enqueue_published_previews`复用既有SDK `prepare_instrument_preview`：只在计划完整且新增条目已逐字匹配实际catalog后派发，先各型号代表再其余变体。E维护checkpoint保存固定幂等键，进程在API返回前中断可用同键续接；API错误只在显式重试时再派发，不自动重跑失败音频。返回的submitted仅表示交给既有试听队列，另列queued/loading/ready/failed/cancelled，不能把预热排队说成音频已就绪。4项回归通过，覆盖发布前拒绝、中断同键恢复、错误不循环和型号顺序；尚无本轮新目录实际发布，所以未对正式服务派发试听。

用户明确没有额外AGMPk采样。Ample M的Planet Caravan保留`blocked_missing_sample_group`，不把其他采样冒充AGMPk，不无限重试；其余项目继续。H繁忙时不加载新样本，当前LP队列由单次`queue.json`统计为35项：15通过、9失败、1延后、10待测。LP的新2项HM2参数错读推翻了此前“仅SC重置、LP全部保留”的概括，后续须比较实际状态块及处理前后变化，不能仅按产品版本归因。所有失败原报告保留。

### 已恢复后台串行批次

新串行入口 `scripts/instruments/run_ample_factory_chain.py` 已隐藏启动，locator为忽略的 `runtime/ample-factory-chain.local.json`。2026-10-22 19:58:56 +02:00实际chain PID33888、包装PID38992；它在等待原LP supervisor PID42772（父14904、19:49:18创建），没有重复开启LP。独立核对checkpoint为`waiting_for_lp`，尚未启动子批次。LP仍35唯一项：15通过/9失败/1延后/10待测；之后使用已有固定M33（32待测+1缺采样）和SC45（45待测）队列，按顺序自动运行。

chain以PID、创建时间、命令行、队列真实终态和固定pin核对交接，持有跨进程锁；CIM超时不当作进程退出，人工暂停/异常中断会阻塞链而非另开批次。7项实际进程/队列夹具回归与Ruff通过。各批沿用H盘空闲预算和原始报告，不自动重试失败项，不写正式catalog/工程。Kontakt与MODO后续真实控制等串行链结束或明确的独占窗口，当前控制准备不等于已执行。

本轮代码收口再次只读核对正式九表/head、14项保护文件和1252项catalog，均与恢复前一致；证据为`20261022-resume/formal-after-code.json`及`pins-after-code.json`。本轮还没有新增入库条目。源码规则、探针和串行器已按主题提交；并发本地引擎与AD2 obj/pdb不混入。

Riffer诊断还暴露了共享产品链缺口：验证用特殊触发事件时，当前普通目录/试听短句/AI查询不会自动携带这些事件。Ample发布适配器因此拒绝将带`event_profile`或`performance_profile`的诊断项丢弃演奏信息后当作普通MIDI音色发布；原始验证仍可继续。后续需把经实际证明的演奏profile统一贯穿候选指纹、目录、试听与AI查询，并核对Riff内容本身的状态恢复，才发布该类预设；单次触发发声不能替代这条产品链。

## Kontakt / MODO 优先恢复窗口（2026-10-22）

用户明确要求继续修复Kontakt与MODO。为避免并行采样竞争，LP在原队列安全边界暂停，35个唯一项实际为17通过、11失败、7待测、0运行；原暂停请求保留。原串行链因LP尚未终态成为`blocked/lp_exited_before_terminal_queue`，没有启动AGM或SC；此前记录的LP/chain PID均已退出，不再按旧locator称运行。后续归还通道时仍续同一队列和固定证据。

### Kontakt：空机架恢复与实际音频已通过，语义身份仍待接通

新证据集中在`data/maintenance/instrument-intake/km22/`。空机架的最小请求与含模块绑定字段的请求分别约6.58秒、1.41秒零退出，说明旧60秒初始化超时并非必现，也不能据此认定模块绑定字段造成失败。

`clean-audio-a/audio/evidence-index.json`记录了实际两阶段控制：首次装入官方Lua保存的Grand Piano Multi并捕获完整VST3状态；第一个profile租约退出后，第二阶段安装独立Lua验证过的0乐器Multi，再恢复首次捕获状态。两次原生执行均退出0，实际解码均为44.1kHz、双声道、220500帧（5秒）；首次/重开RMS分别`0.008325974238380968`、`0.008291844125151175`，峰值`0.1324634552001953`、`0.13245928287506104`，削波均0，完整参数图一致。私有default字节已恢复且没有残留自有进程。这修正了旧双阶段预装同款音色的验证缺口；本控制状态仍为`audio_passed_pending_factory_identity`，没有入库。

官方Lua直接读取源NKM和由源NKM槽位生成的控制文件，均读回1个Grand Piano、索引0、MIDI通道0。但是由真实IComponent槽位移植生成的NKM，在60秒观测控制中实际弹出`This patch is corrupted and cannot be loaded!`，没有Lua ACK。该候选明确无效，不能继续把它仅归因于磁盘慢，也不重试未变输入。原始证据位于`20261022-resume/kontakt/slot-getter-controls/component-slot-20261022d-observed/`；整个自有树已退出，约190.6MB为逻辑进程树I/O计数，不能称作物理H盘读取上限。

NI容器外壳可解析并不证明项目态与NKM文件语义兼容；实际程序子树仍不透明。现有IUnitInfo只有MIDI通道/通用程序编号，无法确认当前NKI。官方Lua源回执与profile捕获可证明来源链，但不冒充原生组件语义getter，也不替代现有身份检查。继续研究插件实例内真实只读身份通路，992项全量转换/发声/入库尚未启动。

### MODO：确认实际恢复入口，收窄追踪

原输入包含目标`'70s J-Bass`，恢复回调和模型加载器两次都读取默认`Y60sPBass`，随后保存状态仍为`'60s P-Bass`。改用内部资源ID及FPCh容器的旧控制也没有改变结果，不能简单归因为显示名转换、缺Controller状态或bank程序索引。尚无本机edition/授权状态证据，不猜许可原因。

两次setter追踪只保留前128条，而第一轮就有481次写入，因此早期索引113的默认值只能证明该次调用，不能断言目标从未在后续写入。第二轮探针约4.2秒、19.5MB逻辑I/O、所有自有进程退出，但结束码`0x80000004`属于未解决的探针单步异常，不能当作干净生命周期通过或产品故障。

通过只读RTTI和小段反汇编，定位实际`IK::Products::MB2::AudioProcessor`主vtable为`0x180e18868`，`setStateInformation`槽位`+0x110`为`0x180935c00`；不是此前的构造器`0x180920110`。恢复入口解析树后经`0x180961420`按schema逐属性合并，属性转换回调在写入`0x1809626f0`之前运行。下一次定向探针应记录BassModel完整转换前后及写入顺序，同时修正自有单步处理；不再依赖通用前128条采样。静态定位证据在`km22/modo-static/vtable.json`。

本阶段只读复核`km22/protection-after-controls.json`：正式九表/head、14项固定文件摘要与恢复前一致，catalog仍1252项且SHA未变。没有正式工程/服务/渲染/音乐模型写入，没有NAS访问；源包、固定Pianoteq验证和历史拒绝路径保持不动。并发N7长测在运行时不额外冷开采样器，只按当前进程和报告实际状态安排控制窗口，不按历史locator推断空闲。

### 精确状态控制（2026-10-22 晚间，仍未入库）

MODO的定向转换控制02/03均退出0：`BassModel`原厂显示名`'70s J-Bass`转换为`Y70sJBass`，schema验证前后保持目标，实际写入参数也保持目标。控制04进一步在真实JUCE SharedObject写入前后读取同一属性：`Y60sPBass`确实变为`Y70sJBass`，恢复回调入口仍为目标；但紧随其后的型号命令和最终getState又读回默认。证据位于`20261021-all/modo/callback-trace/converter-trace/run/`。不能再把问题描述为转换器没有生成正确型号。

最初的队列清理追踪虽正常退出，但前64条容量在恢复回调之前已经耗尽，因此不能证明回调中没有队列任务或没有覆盖。下一版按回调线程、精确返回地址和栈窗口过滤，无关getter不占用容量；溢出视为证据失败，不靠增大前N条掩盖问题。探针自身早期访问越界、单步处理错误的原报告均保留，不计作插件缺陷。

独立`km22/modo-lifecycle-a/`把插件prepare后先处理128块，再通过标准VST3入口重新应用相同原厂状态，随后实际播放5秒合成短句。执行退出0、约7.42秒、约137.9MB逻辑Job I/O，自有进程退出，插件/源预设SHA不变；prepare、重新应用以及实际音频后的状态仍为`'60s P-Bass`。RMS约`8.41e-6`、峰值`5.09e-5`，没有据此判定音色通过。简单预热或开始process再重应用并未解决型号恢复。

Kontakt旧通用程序列表控制实际上来自空机架，不能外推所有已装载状态。新增`km22/grand-units-a/`从独立空机架恢复真实Grand Piano状态后，读取全部16个IUnitInfo单元及2048个程序名：仍全部为MIDI通道/程序编号，没有真实乐器名。约7.22秒、28.7MB逻辑I/O、退出0，私有default恢复且自有树退出。此结果才覆盖已加载Grand Piano的公开单元名称路径。

另一次`km22/context-state/control-a/`用标准IStreamAttributes对照普通IBStream，Kontakt两次getState均返回0/78042字节，但属性接口查询次数为0，未读取StateType=Default。该SDK实验在已记录terminate后以`0xC000000D`退出，故只作为“本次未读取属性”的诊断观察，不计生命周期通过。没有把不同状态字节或上下文标记当作NKM兼容/身份成功。

官方Lua的空机架只读introspection已实际正常完成，约3.80秒、190.1MB逻辑Job I/O、rack_count=0、自有树退出；没有加载NKI/NKM或处理音频。四个getter均为带两个upvalue的C函数，userdata类型明确指向`ni::kontakt::lua::api::bindInstrument(..., BEngine&)`及`bindFileInfo`的Sol lambda绑定。首次观察时进程已自然退出，没有取得模块映射，因此函数tostring给出的堆地址尚不能当作代码地址。下一步只在自有实例读少量closure/userdata与模块映射，定位真实只读getter；该步骤仍不等于VST3身份检查已接通。证据为`20261022-resume/kontakt/lua-introspection-20261022/run-20261022-01/`。

### 实际组件身份与稀疏索引（2026-10-22 晚间，未全量入库）

`km22/component-grand-c/`实际从全新VST3实例的独立空机架恢复先前捕获的Grand状态。通过公开IComponent指针及真实getState实现，确认`component+0xAC0 → receiver+0x140 → BEngine`，并核对core vtable/COL/`.?AVBEngine@@`类型。读取器沿已确认的名称表路径读回`Grand Piano`，不是在堆中搜索标签，也不是从候选文件名返回名称。该控制退出0、约1.77秒、约26.8MB逻辑Job I/O，仅1992字节定向内存读取；私有default恢复且自有进程退出。

实际加载core `Kontakt 8.vst3plugin`的完整SHA为`6e6abad7518574e4f4e5e20779860f7c789041df63b75e0f03c29a18d701aeab`；旧`75cdaa…`识别的是portable wrapper，不能代替core。新`native/include/KontaktIdentity.h`按core/version/组件vtable及BEngine RTTI绑定，使用可注入、有限读取回调。名称支持MSVC SSO及堆字符串、严格UTF-8；索引按原厂standalone已确认的64 bank、signed-int16向量和全局重映射规则读取，不生成`[0,count)`。活动bank计数与记录索引长度是不同数量，不强加代码没有保证的等式。损坏指针、向量、RTTI、重复索引和越界读取仍拒绝。core中对应indices helper的精确RVA尚待空闲窗口核对，不能声称已经定位该core函数或直接调用了官方getter。

`km22/source-controls-b/`通过官方Lua真实装载长名称音色`Clavinet Stereo Auto Wah`，以及`Grand Piano + Clavinet Stereo Auto Wah`双乐器组合；源接口分别返回count1/index0与count2/indices`0,128`，已保存独立NKM及原始回执。约5.55秒、约197.4MB逻辑Job I/O，源NKI/程序SHA不变，自有树退出。此前`source-controls-a`只在进程冲突预检阶段延期，没有启动Kontakt；原目录保留。上述成功只证明官方源装载，不冒充新读取器的长名/双款VST3发声验收。

新增独立`native/instrument-probe/KontaktProbe.h`、`scripts/instruments/build_kontakt_probe.py`及`scripts/probes/instruments/kontakt_identity.py`，在新E构建目录复制并严格挂接快照读取，不覆盖固定宿主/链接库。每个新进程顺序缓冲校验实际core SHA，前后快照复用本进程仍未变化的文件pin；快照读取内存上限64KiB。首次原厂Multi/空机架重开分别捕获真实身份，音频后再次核对；空机架另有零身份控制。`kontakt_native_identity.py`从同实例读回内容取名称，候选源哈希只承担独立来源链，不用候选标签冒充观测。共享publisher同时核对response与保存state中的native identity，防止只替换语义字段而保留相同状态/参数。

原生离线夹具及44项相关Python回归通过；另2项真实子进程测试验证非零退出保留与超时后自有孙进程退出。独立probe构建`kontakt-reader-b`成功，SHA为`470fb1cdc57e89ea7c78e2eb5fc23f1772cd303ff9a60c4891b4401145739d69`，Ruff通过。实际新空架/长名/双款控制因其他任务N7 endurance-v8的真实engine/六worker仍活动而延期，没有启动第二个采样器。新增有界一次性控制监督入口`run_kontakt_identity_controls.py`可等待原生通道，按固定输入串行执行，实际失败不自动重试；它不是全库验证器，也不发布目录。992项全量转换/资源验证/发声/入库仍未启动。

### MODO实际可用集合与用户激活说明

`20261021-all/modo/callback-trace/prelude-trace/run/model-set-20261022-01/`记录两次真实恢复：目标`Y70sJBass`已经写入，但查询的完整unordered_set只有`Y60sPBass`，declared_size1、链完整，contains(target)=false，随后程序自身重置到默认。该探针退出0、约0.94秒、约11.9MB逻辑Job I/O，插件和源预设SHA不变，所有自有进程退出。之前“异步队列覆盖”已被后续精确窗口排除，不能继续按旧推测修补setter。

用户明确表示已付费激活，因此不能从这个单型号集合反推其账户或授权状态。只读核对VST3、moduleinfo和standalone均2.0.4；四个资源注册路径存在并指向同一H包目录，尚无简单版本错配/资源路径缺失证据。继续比较standalone与VST3实际初始化后的型号集合，检查合法安装/宿主差异；不修改授权检查、不强行插入型号、不修改插件二进制或把未发声项入库。独立standalone的布局与VST3不同，使用唯一代码特征匹配，不能套同RVA。该对照准备和失败均保留在`20261021-all/modo/standalone-compare/`，实际运行需等N7通道空闲。

代码收口时`km22/protection-before-deferred-controls.json`只读复核正式九表/head和E侧12项保护SHA，均与恢复前一致。另2项H固定文件及catalog最后完整核对仍是`protection-after-native-introspection.json`；本次因v8活动没有额外重读H，不能把旧核对时间写成新的14项全验。实际后续控制结束后再作完整前后检查。

2026-10-22 23:03 +02:00已启动一个隐藏、一次性的串行诊断监督，实际Python PID50844/创建时间`1792702992.3662205`，launcher39600；新locator为`runtime/kontakt-modo-followup.local.json`，实际状态在`km22/deferred-controls-a/followup-progress.json`。启动核对为`waiting_native`，N7 v8的engine27260及六worker仍活动，尚未运行新Kontakt/MODO控制。监督固定27个E侧输入/实现/EXE，最长等候2小时：通道与H空闲后先完整保护基线，再空机架、单长名、双乐器三项Kontakt控制，随后一次MODO standalone观察，最后正式九表/14pin/catalog前后核对。真实失败保留且不自动循环重试，终态只是`diagnostics_finished_pending_review`，不代表音源已全部修好、全库已验证或入库。未创建新的定时自动化；Ample原队列保持安全暂停。

23:07 +02:00用户将MODO改为可放弃项，要求优先完成其他音源。先确认旧50844仍`waiting_native`、没有任何自有子进程/控制结果，再按PID+creation_time+完整命令核对仅终止该自有等待进程；N7未动，旧A计划与输入保留，状态记录`cancelled_before_any_probe`而非插件失败。随后新建Kontakt-only `km22/deferred-controls-b`，固定19个输入/实现，实际PID51968/创建时间`1792703253.6552918`，launcher50136；最新locator为`runtime/kontakt-followup.local.json`，仍在等N7，没有MODO执行步骤。MODO分支为`optional_deferred_by_user`。后续串行优先级是Kontakt三控制及必要共享修复→Ample原LP剩余/AGM/SC及分组失败修复→Kontakt最终资源准备/992款全量路线；两边只并行E侧分析，H/native必须明确交接。缺AGMPk仍单列，不伪造通过；已验证的Keyscape/Pianoteq不重复跑。Ample受限恢复入口先按原计划身份和状态机补齐回归，不直接手改旧队列伪造终态。
