# 新音源全量入库与共享兼容恢复（进行中）

用户要求完成 Ample、MODO BASS、Kontakt 音源的全量入库，允许重写适配器和独立逆向诊断。上一轮 `instrument-intake-idle-retest.md` 的暂缓结论保留为历史，不能据此跳过本轮工作。

## 边界与运行位置

正式工程与正式服务不用于验证、不写入；源包和NAS保持只读。资源只进入 `H:\music\Sound Library`，独立证据统一在 `data/maintenance/instrument-intake/20261021-all/`。本轮开始的正式九表/head、14项固定宿主与验证文件、旧1252项catalog摘要保存在 `before.json`。不改并发本地引擎验收文件、不替换旧固定宿主，不改历史报告或清理拒绝路径。

Ample 全量队列113项（M33、LP35、SC45）串行执行首次加载、重开、完整状态与真实音频检查。当前大采样/发声通道由该队列独占；其他来源只做低优先级、有界、缓存命中的控制诊断。H盘队列或吞吐繁忙时推迟新探针，不中断活动条目。进度以队列实际终态为准，不把已准备库存计作入库。

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
