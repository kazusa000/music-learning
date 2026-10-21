# 项目开发约定

最新本地DAW重构规划（2026-10-21）：用户选定本地C++设备引擎，先整理方案和开发顺序。本轮N0完成，N1–N7未开发；先读docs/architecture/native-daw.md、docs/plans/native-daw-migration.md、docs/development/repository-layout.md。设备时钟/音频/MIDI/VST3/混音由新ml_engine掌握，UI只控制与展示，Python共用Service/API/SDK/CLI及EngineBroker，不再让HTTP/WebAudio承担工程供声。新native/engine、plugin-worker及runtime/build/engine-v1隔离，旧固定宿主/静态库/Pianoteq实现不覆盖。保留SQLite修订/CAS为唯一持久权威，会话manifest仅派生；删除屏障、校准、原曲参考/重建、预览/混音语义保留。先设备→音频文件→隔离VST3→前端/AI→生命周期→完整图导出→默认切换，实际设备/回环与冷暖性能分别验收。先前未部署浏览器PCM实验9文件已存Git stash 542936842326d8f47c72203fea0c925213048128，当前源码回已提交基线，不整份apply混入重构。README精简、历史存docs/history/README-before-native-daw.md，docs/README.md为导航；其余目录只明确职责和渐进迁移，未搬数据/模型/库/固定文件、未重启服务或新建工程、未触碰拒绝清理路径。分支保留feat/user-instrument-intake。

最新播放缓存修复（2026-10-21）：先读docs/51-playback-buffer-ownership.md。397秒重建duration空值来自解码完成后voice重新读取可回收缓存，warm与1GiB回收均可删除条目。play持有本次AudioBuffer并直接传首次/preserve换源voice，不提高缓存上限，保留流式预取及代次取消。f2a26a7独立重现相同异常；397秒8轨元数据回收/真实warm交错/换源取消与7组相关单元、真实Edge8轨PCM及播放中监听/循环/A-B/重播通过，构建已由8765提供。无正式API写入、模型/插件调用、H库/NAS/Pianoteq/原生或历史拒绝路径变更。

最新原曲／重建双编排（2026-10-21）：先读docs/50-original-and-reconstruction.md。用户授权SheetSage2+MERT底座、Leap Xe及伴奏MuScriptor流程。原曲3轨（人声音频/静音人声MIDI/伴奏音频）或纯音乐1轨；原曲和弦/段落、波形、即时监听，修正不改伴奏MIDI。重建为真实人声+伴奏多乐器MIDI。9阶段实际305秒/61块/8,686音符/7 VST3+1音频通过；32秒纯音乐完整成功且Leap未调用。转换代码不依赖数据库，扩展名为空的CAS先wave_alias再交原生读器，固定宿主/插件未改。source_reference持久保存，reference.get/update及music.query双视图，共80操作；修正retarget试听，波形SHA缓存，引用带view。讨论workspace/reference_track_id跟随界面，仅为焦点而非权限上限；真实Codex两视图与最新MIDI/原曲上下文通过。272完整后端（18新专项）、4K/窄界面真实浏览器/上传/播放中混音/切换/参考修正及构建/Ruff通过。正式8765已部署，1工程/3修订/2任务/7轨/7,098音符及九表/head摘要一致，Pianoteq固定11 SHA一致，NAS无访问、H库无写入。旧工程可在原曲页按新流程建立副本，不自动改旧工程。独立8792的4工程/157 CAS项已按删除接口清掉，8792已停；此后17个新测试目录/文件清理被blocked by policy拒绝，命令未执行，仍在且禁止换工具绕过，精确路径见文档50及忽略的cleanup-blocked.json。历史拒绝目录均不动。

最新主页永久删除（2026-10-21）：先读docs/49-permanent-project-deletion.md。主页工程行与详情提供删除/确认，用户要求不创建备份。projects.delete在原版本/幂等下取消活动任务/对话，持久保存仅含ID和计数的清理意图，再删除全部工程记录/含快照的旧回执与专属CAS/工作目录；共用素材、外部原曲和音源库保留，物理清理完成后才显示完成，失败显示并可重试。新增projects.deletions.list回读，共78操作，无迁移或新增数据库备份；状态在events中持久，worker重启续清理。上传发布/登记与删除共用写锁且在线程执行；普通GC不接管失败删除。实时会话有project/API身份及cleanup_ready屏障，防止加载线程在删除后写回。13专项、完整254后端、独立8791真实浏览器归档/取消/删除/刷新/共用文件/最后空库与4K确认框、构建/Ruff通过；正式8765已部署，3工程/6修订/5任务及快照SHA一致，正式浏览器3删除按钮/0JS错误，8791已停。原生源码/音乐模型/H库/Pianoteq固定11文件/旧拒绝目录不动。本次10个测试目录清理被blocked by policy拒绝，未执行且禁止换工具绕过，路径见文档49。

最新 large/Beat This 导入改造（2026-10-21）：先读 docs/47-large-beat-import.md。用户授权清空所有旧工程并替换旧模型；正式 projects.delete 事务已清空1工程/32修订/23任务，正式列表0。当前唯一导入流程 normalize→beat_this→muscriptor large→assemble→render→calibrate→validate，删除和弦/曲式显示及Demucs/SheetSage2/MERT执行入口，保留速度小节网格和原始音符时序。两模型真实RTX5080整曲305秒成功，61块/9670音符/8真实VST3轨均非零；Windows整文件权重映射1455经逐张量直接写入既有模型storage解决，原精度不变，GPU峰值约9.12GiB。新轨默认-12，固定原曲参考监听增益及独奏增益，压缩/过采样软限幅与1dB峰值余量；实际浏览器混音差0.13LU、鼓组2.48LU、其余独奏≤0.48LU，8轨无削波/dBTP<0，推子/静音/独奏不生成渲染。241后端+3权重读取+3前端单元/构建/Ruff及独立整曲/4K浏览器通过。初始publication尾音兼容从校验WAV读取，Keyscape必须有共享路由指纹及执行证据才复用。Pianoteq既有763批次与11项固定摘要保护不变，H库只读、NAS无访问。物理删除5类被blocked by policy拒绝：旧4模型约4.07GB、正式旧CAS2782对象约1.909GiB、data/envs/sheetsage、旧备份projects-1792520318088701500.sqlite3、以及本次accept和4个明确临时测试目录；全部仍在，禁止换工具/回收器绕过，不能声称全清理或目标已完成。历史被拒目录也不动。

最新 Pianoteq 剩余全量授权（2026-10-21）：用户要求剩余763款全部自动验证入库，通过项加入现有型号/变体/双语与试听预热。先读docs/41后续段、docs/benchmarks/pianoteq-full-verification.md，运行请求定位runtime/pianoteq-full-job.local.json。run_pianoteq_full.py --start 启动单一隐藏监督/低优先级验证器，固定输入/宿主/代码，持久原队列续跑，异常退出最多恢复一次，不自动循环重试失败。intake --remaining不重测原34款、不重写797库存，首次/重开发声及完整程序/参数/资源身份检查后逐项原子发布，跨家族重复只拒当前项。新词表覆盖797款/52型号/0待分类；产品原名，其他中文（原名）。227完整后端通过；本机试听缓存已设2GiB，服务已部署，253既有就绪缓存全部保留、正式九表/head前后不变。实际通过/失败/预热数量读队列，未终态不得声称763全部完成。运行中不得修改固定验证实现/宿主/插件/样本；结束监督核对旧目录项、通过入库和预热入队，输出失败JSON/CSV与九表对比，只在分支/结果文档未外改时单独提交结果。H指定库的目录与本批证据写入在本次授权内，NAS/正式音乐/旧报告/旧清理拒绝路径不动，不开第二批。

最新名称显示（2026-10-21）：用户明确 Keyscape、Pianoteq 等产品/来源库名称不汉化；型号、变体、当前音色及参数用「中文（英文原名）」直接显示，沿用共享 labels/目录 API，后续导入同规则。旧 labels_zh.library 不替换产品名，中文映射只存翻译文本，重复导入不嵌套括号，不改原名/ID或H库。18相关后端、26项独立真实浏览器目录/窄栏/确认检查、全489目录核对及构建/Ruff通过。服务停启已部署，223份既有就绪缓存全部保留，后台队列恢复，无新发声验证或整曲任务；正式projects/revisions/jobs全表摘要不变，详见docs/45后续段。

最新过期试听修复（2026-10-21）：先读docs/46-expired-playback-sources.md。主页assets record not found来自playback.get误把已回收历史Render当可复用音频，非原曲丢失。shared sources先核对声部及共同mix的登记/项目关联/文件，过期视为未命中继续祖先，无有效音频返回pending供既有常驻路径；SHA损坏、内容/路由/宿主指纹仍严格拒绝，单次计划内只验同一SHA一次。加载错误独立于操作错误，成功刷新后清旧红条。直接引用2697素材均登记/文件/SHA通过；219后端首轮218过+旧桩1败，补齐登记桩后29相关通过；主页真实Edge只读故障恢复与构建/Ruff通过。正式API/worker已停启部署，未操作窗口。head2667f2a5b96f483c8c616e3cf00e33ac/32修订/9378音符/23任务及9个核心表摘要不变，无正式渲染/live准备/模型/NAS/H库写入。data/t/playback-expired-20261021、playback-expired-v2-20261021、pb46、pb46v3、pb46-final五份本次夹具删除被自动审批blocked by policy拒绝，命令未执行，禁止换工具绕过。

最新中文/试听（2026-10-21）：先读 docs/45-chinese-instrument-previews.md。489 可选预设/53型号显示中文，保留原ID/英文详情/双语检索；SoundPanel与轨道副标题/参数同一labels API。型号代表与具体变体各有试听，独立WebAudio/音量，只压低本机工程总监听、保持时钟和轨道值，停止/结束/换轨恢复，确认先停试听再应用。独立库级preview.prepare/get/cancel/stats，幂等/去重/generation/进程锁/超时恢复，76操作无工程迁移；缓存键含宿主/插件/依赖/声源/短句版本，标签不入音频键，512MiB限额。后续导入共用labels_zh与prepare_entries预检，词表缺项先补中文映射，发布后自动先型号代表再变体预热，点击优先，不自动重试失败。214后端、四组前端单元/构建、6款真实VST3不同非零PCM+真实取消进程退出、独立Edge36项通过。6款约7.4MB测试成品核对后移入E正式试听缓存，仅一份；其余后台准备，未全库新听辨。正式head2667f2a5b96f483c8c616e3cf00e33ac/32修订/23任务全表摘要前后一致，NAS/H源/固定宿主/旧拒绝目录不动。缓存是E运行产物，不能当用户音乐素材或原验证报告清理。

最新型号/变体（2026-10-20）：先读 docs/44-instrument-models-and-variants.md。统一乐器类别→型号→变体→确认应用，只有确认才调用原捕获/编辑/常驻加载；返回/换类别取消，换轨或插槽重建选择器，保留当前标记及三阶段重试。新增只读 instruments.models、list.model_id、production.query 型号查询及 SDK/CLI/生成契约；后续导入共用 model_spec/variant_name，稳定库内型号 ID，跨库重名隔离，未知旧资料不猜合并。兼容视图不改 H 库或旧 preset_id/工程。489 可选预设=Keyscape455/36组+Pianoteq34/17组；尚未验证库存仍不发布。205后端、独立真实HTTP/SDK/CLI489唯一项、真实组件浏览器22项通过，未在正式工程应用音色，未调用渲染/模型/NAS，原生宿主未重建。

最新桌面/鼠标与4K（2026-10-20）：先读 docs/43-desktop-workspace-and-mouse.md。默认原生无边框WinForms/WebView2壳，顶栏合并、循环/和弦上移，轨道命令内置菜单，标尺固定、原生滚动条隐藏；右键擦除、中键双轴平移、中+右工具、Ctrl+右整曲。正式写入仍用既有编辑/撤销接口。用户4K/250%曾实测96DPI/DPR1导致Windows拉伸，已补Win10清单+.NET4.8目标+PerMonitorV2，实际240DPI/DPR2.5/Zoom1、3510×1965捕获通过；画布挂载/显示比例变化重绘。8组前端单元、26项独立真实Edge/sfizz交互、构建/Ruff通过；未重跑音源全库。Computer Use被用户Escape停止后未再接管，最终DPI诊断透明不激活、foreground保持；中途窗口状态诊断焦点失败报告保留，已从入口移除。正式headd72d45a522044d7fb7e0036f29448976/原始snapshot SHA45e60429…/21任务/7轨/9378音符不变，无正式渲染/模型/NAS/H库修改。初次对比误用数据库规范摘要已在同算法比较中核对，旧基线未改。构建scripts/build_desktop.py，已开旧窗口须关闭重开；不依赖旧Edge app窗口启动。 最终仅暂存源码独立构建、browser-v8复核26项通过，未混入并发音色目录变更。8777已停。runtime/desktop-ui-verify-20261020（含node_modules联接）、data/t/ui-shell-mouse-20261020及data/maintenance/ui-shell-mouse-20261020/profile三处本次临时目录删除被自动审批blocked by policy拒绝，均仍在、释放0，禁止换工具绕过。

最新手动音色（2026-10-20）：先读 docs/42-manual-instruments.md。音色与效果可选音轨/乐器后直接点音色，不经过 AI；删除引导段落/验证数量/已验证筛选/AI入口。真实三阶段加载条，失败原地重试、应用后只重试加载；共享自动预加载去重。完整状态与资源替换保留音符/推子/效果，空 MIDI 轨存音色不启动无声进程，添加内容后加载。沿用统一 jobs/edits/playback API，无新迁移/权限/整曲render/手动版本。六组前端回归、18相关后端及独立真实浏览器播放中换音色/推子/静音独奏/失败重试/空轨通过。正式工程未用于修改，NAS未访问，H库只读，常驻宿主及并行Pianoteq文件未改。

最新 Pianoteq 入库（2026-10-20）：先读 docs/41-pianoteq-integration.md。原厂公开 RPC 保存完整 FXP，再由 pianoteq_presets.py 装入已验证 9.1.2 单程序 bank，保留路由/MIDI 映射，首次及重开按完整程序 SHA 检查身份；不改插件或原生宿主。H 指定库内 797 款完整状态已准备/22 类/无待分类，34 款实测可选、其余763未逐款发声验证，正式 HTTP SDK 回读34+Keyscape455=489唯一条目。原25队列24通过/1无声保留；Glockenspiel Original双向实际扫描77–108，音域修正与Syngular细分后独立v2队列34/34通过。SDK+ASGI+真实常驻宿主独立工程钢琴->电钢->吉他各305秒不同非零PCM、3捕获/0渲染/0WAV，捕获约0.75–0.84秒；无人耳音质验收。daw参数校验保留插件自身原生离散默认值，新写入仍校验步骤，避免MKI原值0.5但报告2步骤被错拒；通用回归覆盖，完整后端197项通过。项目配置仅追加H新VST3路径，服务已重启，正式head1c71ff13ccb3415ea5677785dd5ddfd2/快照SHA/20任务前后相同；NAS未访问、旧库未恢复、正式常驻链接库未换、Keyscape自动跟进保持暂停。本次新建E目录pianoteq-state-v2、两份pianoteq-live夹具和两份扫描WAV约79.3MiB清理被自动审批拒绝，全部仍在，禁止换工具重试；此前拒绝目录均保留。

最新 Keyscape 全量收尾（2026-10-20）：原435批次415通过/20失败；共享BusInfo初始化与UTF-16结束符修复后，独立新指纹重试20/20通过、失败0，现有455款全部已验证并可选、14乐器类。正式HTTP SDK回读455唯一条目，20项首次/重开发声、参数/声源身份及干净总线名称均通过。2 CTest+23原生+19相关后端通过。修复7f0c475、原报告fe244c2、重试报告8e4ded8；详细收尾见docs/benchmarks/keyscape-failure-retry.md。监督进程均退出，本自动跟进收尾暂停，不再开批。重试前后及收尾正式head1c71ff13ccb3415ea5677785dd5ddfd2/快照SHA/20任务一致；原批对比false含授权外部变化，旧基线不改。原失败证据、缓存必需音频和完整状态保留。data/t/ks-bus-svc及ks-bus-native共约117MiB删除被自动审批拒绝，仍在、禁止换工具绕过；其他旧拒绝目录未动。没有更换正式常驻链接库、写正式工程或访问NAS。
最新连续供声与音色切换（2026-10-20）：先读 docs/40-preset-loading-and-streaming.md。浏览器提前供应1秒PCM但首块立即启动，记录缺声计数；空MIDI轨不启动静音原生进程。只在常驻PCM宿主中对Keyscape1.3.3d使用完成每块采样工作的模式，不改原离线宿主。instrument.capture复用当前/手动版本的原厂完整状态或库内已入库验证状态，不重跑两轮音频验证、不生成WAV；缺失完整状态仍走原首次捕获。AI取消每轮一次编辑/32次调用限制，批次顺序推进head并保存batches，自动scope包含新增轨道，外部head变化/SDK显式只读与范围/幂等/fencing/素材校验保留。70操作/11任务无迁移，187后端、真实Wurlitzer/C7和500ms停顿浏览器证据见文档。Keyscape全量批次已自行完成415通过/20失败，保护报告自动提交fe244c2，Pianoteq并发文档b19a906已保留。 正式常驻构建按runtime/live-host-build.local.json固定已验证链接库（E内硬链接），不要直接用并发更新后的共享库覆盖；已有一份新共享库产物输出近零，固定库重建后正式API RMS0.03606/peak0.19946通过。Keyscape20项重试PID27264为其他授权任务，只读观察。当前工程26修订/20任务/7轨/9378音符前后不变；本轮约57.4MiB临时文件清理被自动审批拒绝，详见docs/40，不得换工具绕过。

最新 Pianoteq 9.1.2 检查（2026-10-20）：先读 docs/benchmarks/pianoteq912-intake.md。用户新包已独立提取至 H:\music\Sound Library\Pianoteq 9.1.2，VST3 扫描9.1.2、797原厂预设完整导出成功；自带程序钢琴/电钢/吉他输出不同PCM，但当前VST3预设与IComponent加载仍回读同状态，未入库。实际模块均来自新H目录，不再要求用户换第三个包，下一步查共享加载兼容。未修改运行中的Keyscape验证或宿主。正式head/快照前后相同，任务19->20为并发instrument.capture，不能报全库一致。本次新目录临时文件清理亦被自动审批拒绝，未执行、禁止换工具绕过；与此前两处清理拒绝均保留真实记录。

最新即时音量与素材保留（2026-10-20）：先读 docs/39-current-project-retention.md。本次用户明确要求拖动音量不保存：编曲推子只改本地 gain，松手/失焦/离开不请求保存，不创建音频；仅手动 versions.save(track_levels) 原子记录音量与 checkpoint。独奏时其他轨有效静音按钮亮起，取消恢复手动静音。0010、70操作/11任务，storage.plan/collect/status 共用 Service/API/SDK/CLI；素材只按当前 head+手动 checkpoint 及其依赖保留，内部历史/回执不再永久保活旧音色/试听，活动工作阻止回收，短期未绑定素材到期复核。不得触碰 H 音源、NAS、Keyscape 受保护批次或此前清理被拒的两个目录。173后端及独立双轨真实浏览器通过，正式已部署0010，清掉860个旧CAS对象约3.66GiB，保留2830对象约2.57GiB并全部SHA通过；head/24内部修订/19任务/9378音符不变。Keyscape原PID及11项保护摘要不变。当前data/app-data/live下三个旧会话目录和data/t/u-b8lb7smo夹具删除也被自动审批拒绝，不得换工具绕过，详见docs/39。

最新钢琴通道修复（2026-10-20）：docs/36-settings-and-live-tracks.md 新增段为准。Keyscape1.3.3d单有效声部/单来源通道按保存状态路由播放Note/CC，原工程MIDI通道不变；多通道/多声部不折叠。图编译与常驻增量共用，旧未含适配指纹的Keyscape音轨缓存失效，其他轨继续复用；没有自动整曲任务。20项回归及正式C7逐轨非零PCM通过，head b7e515f01fbd48bfa635aae992f4c459/24内部修订/19任务未改，Keyscape后台保护不变。piano-channel-20261020下两份本次测试临时目录清理被自动审批策略拒绝，禁止换工具绕过，不声称已清理。

最新清理授权与结果（2026-10-20）：用户明确要求清除其余历史测试工程和废弃产物。已保留唯一正式工程4012a10f50574b2395d4ec4a9d8f27b5，删除12个验证工程、测试目录、终态任务工作目录及无关CAS；23修订/12轮对话/9378音符保留，3686素材SHA通过，实际释放约51.67GiB，见docs/38-feedback-and-storage.md。后续验证结束清理本次生成的临时夹具和重复工作副本，默认只留小型报告，不能再长期囤积大测试音频。当前工程必需原始音频/分析输出/插件状态和H音源保留。后台Keyscape继续运行，其最终全库工程对比会看到本次授权清理产生的外部变化；不得改固定验证代码/报告或伪造旧基线。

最新反馈与存储（2026-10-20）：先读 docs/38-feedback-and-storage.md。播放持久化用歌曲时钟，尾音不能作为源位置；换较短 head 时夹紧位置/循环。instrument.capture 调度采用独立小任务预算，实际依赖仍计数，不改后台 Keyscape 固定验证实现。对话结束不等于改音色成功。E 盘历史任务/CAS/环境/测试存在硬链接重复计数，本次只读审计、未清理迁移，自动保留回收策略仍未实现。

最新用户要求（2026-10-20）：先读 docs/37-arrangement-monitoring-and-mixing.md。编曲与混音隔离；编曲页仅临时独奏/静音，不提交mixer.set、不写音乐revision或混音配置。推子/声像仅独立混音页，mixing.get/save用独立version，手动保存；0009新增project_mix_settings。最终WAV显式固定mixing_version，冻结配置、单独制作快照，不发布为编曲Render。首页/工作台不自动电平匹配。播放首块预取后共同时钟启动，暂停缓存重用，不重复resume/不因慢块无限丢弃。旧SDK mixer.set兼容保留；新页面不调用。67操作/11任务，155完整后端+新增原生导出及浏览器305秒9378音符通过。Keyscape后台仍按既有保护继续，未改验证实现/库/固定宿主/报告。

播放后续修正：常驻PCM由独立AudioWorklet时钟唤醒，240ms预取，避免后台窗口timer限流；曲末停止和按钮状态不依赖requestAnimationFrame。参考源按不可变revision缓存、服务重连失效。真实浏览器模块加载/连续块进度/实际非零电平/A-B/暂停重播/305秒停止通过，见文档37及忽略的worklet.json。没有重新编译原生宿主，Keyscape保护不变；正式混音导出仍保留3GiB空间门槛。

最新交互契约（2026-10-20）：先读 docs/36-settings-and-live-tracks.md。主页设置与讨论共用持久默认模型/登录/电平；0008仅新增偏好和手动快照。版本列表用versions.list/save，自动保存的内部revision仍不可变但不刷入用户保存版本。日常音符/音色/加轨/mixer/AI改编禁止自动排队整曲project.render；直达主输出音轨用独立常驻ml_track_host，小块PCM进入现有实时混音，普通音符用增量、资源变化只开该轨。整曲WAV留给显式导出。generation/当前head/幂等/fencing/素材和插件完整状态校验保留。A/B可直接播放无完整Render的快照。153项后端、305秒9378音符真实sfizz与浏览器连续流程通过，新增/修改文件Ruff及契约/前端构建通过。65操作/11任务；复杂总线/发送/侧链仍属离线图，不能伪称常驻支持。Keyscape全量批次仍运行，不改验证实现、分支名、固定宿主、报告或库文件，不开第二批；本专项sfizz/夹具验证不得被表述为Keyscape实时验收。详见新增文档及后续收尾证据。

当前全量后台验证（2026-10-20）：用户明确授权跑完剩余435款、失败独立记录且不动正式工程。已启动独立监督进程 scripts/run_keyscape_full.py；请求定位在忽略的 runtime/keyscape-full-job.local.json。任务为 H:\music\Sound Library\Spectrasonics\verification-jobs\20261020-160728-full-4ae3f058，实际队列同名位于 verification-runs。不要再并行启动第二批。监督进程的 status.json 记录实际PID/creation_time/心跳，Windows Job Object拥有验证器和原生子进程；已复制原生宿主到该任务host目录固定SHA，禁止在运行时替换插件/样本或验证实现。20已验项目未重新加入，本批435。失败细节实时存queue逐项error，结束导出failures.json/csv；只读SQLite回读所有正式head/快照/任务数。结束自动更新并仅提交docs/benchmarks/keyscape-full-verification.md，分支或报告被用户改动时保留证据并记录Git阻塞，不覆盖用户修改。其他通过项不会被单项跨乐器相同音频拒绝连带丢弃。查询进度应读实际progress.json，不把目录可选总数20当本批处理进度。后续验收以最终status/report为准，目前运行中，不能声称435已完成。

最新自动验证（2026-10-20）：docs/35-keyscape-integration.md末段。scripts/verify_keyscape.py 一键使用已准备库存；默认完整批次，--limit限制新测试数，--resume自动续最近未完成队列，--retry-failed显式重试失败，--force仅用于新队列。preset_verification.py通用持久队列/宿主+代码+插件+资源指纹/实际依赖与首次及重开WAV证据SHA缓存；不得只凭audio_verified标记复用。旧index_keyscape与新入口共用库文件锁；结果原子合并，失败独立保存，模型调用0，正式工程不改。队列/缓存/原生请求和音频均在H指定库的Spectrasonics/verification-runs及verification-cache。单款首次34.5秒，缓存复检0.203秒（不含启动），不外推全库首测速度；现有20音色仍可选。该入口为库维护CLI，并非新增音乐工程任务或API迁移。

最新 Keyscape 接入（2026-10-20）：先读 docs/35-keyscape-integration.md。插件实际使用 H:\music\Sound Library\Spectrasonics\VST3\Keyscape.vst3，样本在该目录 STEAM\Keyscape；150 文件 77,528,440,122 字节逐文件复制/SHA 校验，源结束复核 0 变化。455 原厂预设完成 14 乐器类映射，20 款通过首次/重开音频、参数及声源身份验证并可选；其他变体只算库存。新增 Keyscape 1.3.3d 八声部 Multi->标准 VST3 预设适配，启用9组立体声总线防访问违规，当前只混合主立体声；后台采样加载需离线准备后等待10秒，不推进音乐时钟，仍非常驻实时宿主。installed_library 的不可变清单/文件大小mtime/变化后SHA/STEAM定位检查贯穿捕获、组装和渲染，CAS只保存状态、预设和小清单，不复制77.5GB；描述回写保留依赖，历史渲染可使用CAS清单。C:\ProgramData\Spectrasonics\STEAM 是指向 H 的必要定位联接，严禁递归删除父目录。C盘5个旧程序文件已核对H备份，但Windows ACL拒绝删除，实际释放0字节，未改权限。137项完整后端回归+新增1项增量入库策略、23原生、Ruff/契约漂移通过；独立真实HTTP SDK两轮捕获/切换/渲染/重启回读通过。JD-800曾无响应退出，独立复测通过，原因未确证；不自动忽略原生失败。正式head6d12b49aa72840429d79ce93294e1dc6、快照SHA、9378音符/15任务及audition desired/sequence保持，仅重开窗口更新observed时间戳；无正式渲染/推理/NAS访问。正式服务和此前打开的应用窗口已恢复，8768测试实例已停止。Pianoteq仍未通过入库，不恢复旧删除库。

最新用户音源接入（2026-10-20）：先读 docs/34-user-instrument-intake.md。用户明确指定 H:\music\Sound Library 为新库，授权检查 H:\music\tmp 两套包；旧删除库仍不得擅自恢复。Pianoteq 922 源文件复制及 SHA 一致，源结束复核 0 变化；导出797内置预设、15乐器类，但当前包不同乐器预设返回相同状态/音频，拒绝入库，需要正常切换预设的 Windows x64 VST3。Keyscape只有STEAM，缺 Windows x64 VST3 播放器，用户会提供；未复制77.5GB数据或改源后缀。新库配置已生效，当前0可选预设，库存不等于就绪。preset_spec统一sfz/surge-fxp/vst3-preset，未知拒绝；runtime_dependencies核对侧车引擎；index_pianoteq必须验证且只发布已测条目，不同乐器相同状态拒绝整次入库。114后端、22原生及新增1条标准VST3状态/音频回归通过。正式head6d12b49aa72840429d79ce93294e1dc6、9378音符、15任务保持，无正式渲染/推理或NAS访问。

最新完整链路回归（2026-10-20）：先读docs/benchmarks/playback-mixer-workflow.md。真实sfizz持续发声独立工程验证播放/暂停/推子/静音/多轨独奏、两处控件、SDK、旧筛选、刷新/离开时保存、A/B、自然结束/重播与服务重启。修复曲末desired仍playing造成轮询复活，结束时保存暂停、显式重播从0开始；电平匹配须先读共同mix参考，禁止冷启动各stem各自匹配。最终25观察点通过，总55点包含修复前故障；110后端与前端单元/构建通过。fixture脚本prepare_mixer_workflow.py的seed不得对正式工程运行；evidence检查器只分析已记录实际浏览器数据，不冒充可自动重跑的浏览器驱动。未改正式音乐、未访问NAS/推理/恢复音色库。

最新试听修复（2026-10-20）：docs/33-live-mixing.md末段。旧audition tracks筛选曾排除电吉他，已恢复正式mix；不能只看mute/solo判断实际参与播放。混音控件/Service mixer.set的音频字段操作清tracks筛选（含无音乐变化的取消静音），改名保留；SDK显式tracks试听有常驻提示和恢复完整混音入口。混音/试听设置共用写入序列，试听明确版本冲突才有限刷新重试，历史目标不重绑、观察superseded不暂停、网络失败不重放。17相关后端及客户端/真实浏览器通过；用户自行调混音后head638e771cf2b7402eb845cbc7ec67cd08保持，音符/音色未改、未正式渲染。

最新混音（2026-10-20）：先读docs/33-live-mixing.md。新增playback.get/mixer.set，共57操作/11任务、无迁移。音量/声像/静音/独奏直接调逐轨播放并400ms合并保存；统一Service新版本/幂等/冲突保留，兼容祖先音频按内容签名生成目标revision播放计划，不能冒充新Render。更换乐器只准备变化的独立声部，复杂处理图回原生准备；尚非常驻实时VST3宿主。preview=true准备可被更新预览取消，显式渲染/导出不取消。顶部导出混音使用project.export，未有完整混音时在导出任务内准备。108后端、Transport契约单元、构建、真实浏览器即时混音及sfizz增量换音色通过；正式音乐head未改，音色库恢复仍暂停，NAS未访问。

最新视觉（2026-10-20）：按用户要求参考Suno Studio 2，统一主页/工作台的视觉系统，先读docs/32-studio-visual-system.md。style.css为共享基础，workbench/daw/discussion.css分管制作界面；旧studio.css已移除，不再叠加旧金属主题。Icon.tsx统一SVG图标，visual.ts共用音轨/画布色板；未编组MIDI显示真实音高/时值缩略路径，显示网格按比例抽稀而不改吸附。构建、既有编辑/电平单元及真实浏览器桌面/中等/窄窗口、整曲播放/空格暂停通过。正式head/快照/9,378音符/15任务与试听desired/sequence保持，未新推理或渲染。用户已删除H盘音色库且暂停恢复；禁止据此任务恢复、复制或迁移音色文件。

最新对话规则（2026-10-20）：用户要求删除手动权限需求。先读docs/31-automatic-actions.md。UI无讨论/改编/结构权限开关；dialogue.submit默认edit_mode=auto，Service在context计算本工程整曲制作scope，模型按语义讨论或执行，不再要求用户勾选权限。音轨/片段为上下文而非自动模式的授权上限，默认整曲、引用不改循环。引用可选，未知链接不激活，不能因无引用或正常未编辑而使自动对话失败。SDK显式read_only/edit_scope及原版本/幂等/fencing/校验保留；真实修改仍新版本+渲染。只读浏览器测试必须沙箱化audition状态，不得污染正式循环。101项后端、相关浏览器与真实Codex换Shiny Acoustic通过；新head及渲染见最新文档。

最新讨论布局（2026-10-20）：先读 docs/30-chat-layout.md。会话/模型紧凑单行，回复区自适应剩余高度、输入框固定底部并可拖高；范围/权限/登录为弹层，笔记/刷新模型在菜单；可放大/还原讨论。Enter发送/Shift+Enter换行，保留中文输入法，讨论控件空格不播放。其他侧栏独立滚动，中等/窄窗口不再把输入框顶出屏幕；仅视图变更、无API/迁移。前端构建和相关浏览器回归通过，正式音乐head不变。

最新授权（2026-10-20）：用户明确回复“现在不要求额度问题，不用管了”，取消此前周额度阈值与暂停条件；后文29%停止记录为历史，不能继续据此拦住当前授权任务。当前任务是压缩讨论侧栏控件、扩大回复和输入区，长期 P6 听辨状态仍按既有证据记录。

历史停止记录（后续已撤销额度限制）：周额度实查剩余29%，已触发用户停止条件；不再开发/推理/验收，不自动恢复，不用重置卡。本线程 goal 工具返回无目标，paused 更新被接口拒绝，因此不新建目标，持续工作记为暂停。已有音色工具规则修正和99项回归保存，见 docs/29-timbre-tool-rules.md。production.query 支持 track_id，讨论也可只读发现真实音色；编辑错误返回契约生成的修复说明，保持原范围和事务校验。正式工程 head/音符不变，自有服务已停止。

最新侧栏（2026-10-20）：讨论、音色与效果、版本、分析四页签；SoundPanel.tsx 合并原音源/乐器页，音轨选择与左侧同步，当前插件链和更换音色同页。仍只生成绑定声部/版本的改编草稿，发送后执行；API/数据无变化。说明和浏览器证据见 docs/27-toolbar-and-sidebars.md 的最新段。

最新对话（2026-10-19）：先读 docs/28-conversation-usability.md。新增冻结迁移0007保存dialogue_sessions及轮次session_id，旧会话归历史讨论、不改旧文本/音乐；worker历史严格按项目+会话隔离。dialogue.sessions.list/create、dialogue.models 共55操作/11任务，统一API/SDK/CLI；模型列表按需读取固定本机App Server model/list，无推理，支持缓存及显式刷新；所选模型传thread/start，不可用明确失败不回退。React Markdown+GFM渲染对话/笔记，保留音乐引用定位；新提示词不再反复输出候选/未经审核声明，内部来源仍保留。93后端通过及新会话/模型/Markdown/流式浏览器通过，未新调用真实生成或音乐模型，工程head不变。

最新顶栏和侧栏（2026-10-19）：先读 docs/27-toolbar-and-sidebars.md。用户随后要求直接删除重复的工程标题栏；歌曲名/返回只在窄条，生成试听并入播放栏。AutoHideToolbar 只包含播放栏，默认折成32px窄条；悬停/键盘聚焦展开、离开450ms收起、Escape关闭，锁定常驻并本机记忆；折叠内容hidden/inert但不卸载。窄条保留快速播放。音轨列表压缩、原曲分轨默认折叠；AI范围/草稿/发送为主，登录设置收起仍可访问，快捷建议只填草稿。保持原52操作/11任务，无数据迁移和音乐编辑；真实浏览器顶栏/侧栏/拖拽及模拟对话回归通过，未访问NAS/未调用provider或模型。

最新面板交互（2026-10-19）：先读 docs/26-resizable-panels.md。主页列表、声部栏、AI侧栏宽度，以及制作区、卷帘、力度栏和AI历史高度可拖动 separator 调整并在本机记忆；双击恢复、方向键微调、取消恢复。普通滚轮上下音区，Alt+滚轮纵向缩放，Ctrl+滚轮时间缩放；右侧音高定位条/上下按钮与适配音域可用。纵向锚定在 useLayoutEffect 后恢复，避免并发绘制前被旧滚动高度夹紧。仅视图偏好，不改音乐/API/数据库；前端构建与真实浏览器面板回归通过，未调用 provider/模型、未访问 NAS。

最新迁移与试听修正（2026-10-19）：先读 docs/25-local-runtime-and-audition.md。正式运行根目录为 E:\Intrest\music-learning\data（忽略入 Git）；旧 D 路径为历史证据位置，已不运行。保留硬链接与逐文件 SHA 校验后切换；D 旧目录与临时目录的删除被自动审批拒绝，仍有残留，不得声称已释放 D 空间。PowerShell Move-Item 曾沿缓存 junction 移走前端部分文件，418 文件已校验恢复、依赖按原锁重装、重新构建；以后禁止递归移动含 junction 的目录，任何清理先处理联接本身。当前正式用户手动音符修订已保留，8 dB 电平修正创建新版本并整曲真实渲染，不覆写旧版。P6 听辨仍未通过。

最新用户整改（2026-10-19）：用户拒绝宣传大字、默认验证工程和绿色表面，要求检查真实使用流程。先读 docs/24-usable-studio.md。中性深灰/紧凑功能界面，主页原曲+重建自定义播放器、空格播放防滚动且输入框保留、离开主页暂停、卷帘无选轨时选择实际钢琴。52操作/11任务，新增冻结迁移0006 project_library；projects.organize/archive/display_title不改变音乐head，默认隐藏归档，SDK整理12个旧验证工程，当前音色项目显示实际歌曲名，全部原数据保留。start/open默认Edge app独立窗口和独立profile，并非原生桌面壳；stop关闭自有窗口，--browser显式调试。89项不同后端、真实应用窗口播放/快捷键、H盘独立sfizz与13组编辑渲染通过。D盘约3GiB导致新音色准备容量拒绝，H本机USB重做通过，未降低保护，未迁移正式数据；错误已可读。NAS未访问，音乐模型/provider未新调用，P6听辨未通过。

上述专项最终额度剩余34%（已用66%），未触发停止条件，无重置卡。usable-desktop-v6和H:/ml-reconstruction-workflow-v2通过；修复新revision静默退回原曲的问题，已有音符默认重建，未渲染播放明确提示，生成完成继续重建。左侧声部双击进入卷帘。8767回归和临时调试窗口已停止；用户正式应用通过start.cmd可重开，后续先实查状态，不将历史停止记录当作当前状态。

最新响应优化（2026-10-19）：用户将复杂音乐核对闭环后置，优先 AI 易用和加载速度。先读 docs/23-responsive-loading.md。启动器使用轻量 session helper；UI health 连接、单 SSE 的 live_only 初次订阅/断线 cursor 补发、事件刷新+15 秒兜底，生成中仅900ms回读活跃轮次，登录模式选择后本机记住，插件/工具详情按需展开。Service 只读命令 transaction(write=False)，所有写入和worker租约仍默认BEGIN IMMEDIATE，不削弱幂等/fencing。88项后端、真实浏览器13组编辑/原生渲染、模拟对话浏览器与真实服务断网停启恢复通过；原生/模型格式36项是前次保留证据，本次未重跑模型或Codex provider。已有整曲编辑器401ms、讨论输入411ms，空闲6秒36次查询降至1次，服务启动2681.1ms，均为本机单次观测非承诺。正式工程head未变，NAS未访问，所有自有实例/浏览器已停止；当前周额度剩余40%，未使用重置卡。P6听辨仍未通过。

最新前端（2026-10-19）按用户新要求扩展为FL风格制作界面，用户可手动操作卷帘与音轨，覆盖此前只读范围。先读docs/22-fl-workbench.md。DawEditor.tsx/dawEditing.ts提供单声部画入/拖动/改时值/力度/多选/复制删除、MIDI与音频片段编排、音轨增删排序命名与混音，全部写入仍走现有edits.apply/revisions.restore/Service；51操作/11任务无迁移。手动时间修改显式转sample锚定并清理旧记谱绑定，旧版本/源录音保留；拖动只是预览，取消不写入，历史只读，保存使旧音频失效，需实际离线渲染才可试听。撤销栈会话内有效，跨重启可用历史版本恢复。122项回归、独立真实sfizz夹具浏览器13组交互/重启重渲染与完整305秒9,378音符视图回归通过；正式音色工程head与snapshot不变。当前额度实查剩余43%，无重置卡；正式和夹具实例/自有浏览器已停止，NAS未访问，无音乐模型或新Codex provider调用，P6人工听辨仍未通过。

最新专项（2026-10-19）：统一乐器目录已实现，乐器为主入口、库为后台来源。先读docs/21-instrument-library.md的统一目录段。instrument_taxonomy.py定义稳定ID/父类/别名和明确映射；catalog每条可多instrument_ids，跨库汇集、同库拆分、同名靠唯一preset_id区分，重复ID拒绝。旧catalog仅兼容读取、不覆盖；新build写出分类，未知标unclassified，不能按文件名硬猜。当前3031条中23精选明确映射+2914原库类别映射+94待分类，已发声验证仍28；52分类节点不等于52个已安装乐器。新增instruments.categories，instruments.list的instrument_id/audio_verified和production.query的instrument_id/include_instrument_categories共用Service/API/SDK/CLI，当前51操作/11任务。前端“乐器”先选乐器交给AI选音色，来源可展开；只生成绑定revision/track的改编草稿，发送后才执行。原范围/幂等/状态/资源捕获约束保留。本次122项回归（86后端+36原生/模型格式）、真实目录API/CLI/停启与浏览器通过，无新的真实provider推理或歌曲渲染；正式工程head不变，NAS未访问，所有自有实例/浏览器停止。P6听辨仍未通过。

## 任务入口

先读 README、docs/01-product-plan.md、docs/05-development-plan.md。实现数据、编辑或播放前，再读 docs/03-architecture.md、docs/04-data-and-ai-contracts.md 与 docs/06-acceptance.md。

用户已确认的产品流程是：主页选音乐，完成整曲预处理，进入专用前端，与 AI 深入讨论整曲或局部，改编并试听；后续跨工程积累经验并创作。用户有较好的乐理基础。软件的主要操作者是 AI，支持 Windows x64 VST3；不需要 MIDI 键盘。还须阅读 docs/09-agent-daw-contract.md 和 docs/10-free-vst3-candidates.md。

## 实施要求

- 用户于2026-10-20明确取消周额度检查及阈值暂停条件；此前阶段额度记录仅作历史，不据此阻止当前任务。仍不得调用重置卡功能。长期阶段的音乐验收标准不变，当前按用户具体任务推进。

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

代码在 src/music_learning（权威契约/领域服务、HTTP API、SDK/CLI、任务执行、pipeline/musical），scripts/pipeline_adapter.py（隔离模型/渲染阶段），services/api/migrations（冻结 0001 至 0005）、apps/web、packages/contracts。数据在 E:\Intrest\music-learning\data\app-data，独立 Python 环境在 envs/app。通过 scripts/app.py start/open/status/stop 管理隐藏的服务及其归属。更新后先 stop/start，旧进程不会热更新。当前从源码 checkout + PYTHONPATH=src 运行。契约变更后运行 scripts/export_contracts.py，提交 schema/TS 并检查漂移。

所有工程写入走 Service；worker 发布使用同一领域服务和 SQLite 事务。幂等键、expected_revision、素材 SHA、事件 cursor 和 attempt fencing 必须保留。快照及实体投影同时提交；素材先本地原子发布、后提交引用，孤儿文件不当成功，不自动清理。Windows Job Object/gate 用于强杀清理；不要退回只有内存的 BackgroundTasks 队列。

原曲导入状态是 imported，不能直接标 ready。P2 七阶段齐备才发布初始 ready。P3 编辑创建新版本并使音频/冻结失效，完整 project.render 才发布该版本 ready；局部产物不可冒充整曲。revisions.restore 创建新版本，不能改标旧音频。默认初版与研究样本保留；P1 SQLite 元数据备份不包含 CAS，P6 application.backup/restore 已提供数据库与全部 CAS 的完整归档及独立恢复，使用边界见 docs/20-local-delivery.md。

Pipeline stage/cache 只能在有效 fencing 下提交。缓存包含代码/模型/包版本/宿主/插件/资源与输入摘要，复用前检查文件；当前保守地使共享领域代码变化也失效。更新 backend 代码期间不要把混合代码版本的执行标成功。已存在声部或运行期间 head 改变时，新分析保存独立候选，不能覆盖人工修订；preprocessing.adopt 是显式操作。渲染/波形与 ready 绑定精确 revision。

P2 早期 NoteStart 类型诊断错误已修正并重跑，最终原始起音/MIDI 均 9,378；194 秒原始起音 1,257 / MIDI 1,252，5 个零时长事件保留。P3 原生与新流水线支持负 PPQ/弱起及后级插件延迟补偿，历史产物不重写；候选拍点仍待核对。卷帘只读，音符编辑由 edits.apply 执行；版本 A/B、P4 真实产品讨论和 P5 对话编辑均已验证。

P3 代码在 daw/graph/rendering/audition/midi.py、scripts/daw_adapter.py 和冻结的 migrations/0003；38 项 API/SDK/CLI。原生执行 stereo DAG、post-fader send、基础立体声 sidechain、sample-aligned step/linear 插件及 mixer 自动化。插件生成 MIDI 路由、多声道/反馈/实时制作和私有菜单未支持。既有源时间不可改变，beat-timed 音符显式绑定 TimingMap。scope 同时检查旧/新事件；全轨插件/自动化要求 structural 全时段授权。冻结只接受当前版本的完整渲染，任何后续编辑保守失效。

最终正式 P3 工程为 76d5a25824704f5192c3f72caaec44a6，head 60303a60cca2452e8a79695e59392b4b；源 P2 工程 bbcdc48bcf6841b6adf221a895473234 未改。P3 早期分叉 8087e500d22c4e38a0adbaa4484bfe77 专用于浏览器恢复测试，当前是新建的恢复版本，未冒用旧音频。所有正式任务终态；阶段收尾停止自有服务/浏览器，后续按需启动。

本机模型环境/产物现已迁回 E:\Intrest\music-learning\data，位置保存在被忽略的 runtime/model-runtime.json。用户已清理 E 盘并明确要求迁回工作目录；后续先核对空间，不再默认写 D。用户已完成 Hugging Face 许可/本机登录，三个模型均跑完 NAS 授权选取的 305 秒歌曲。NAS 只允许读取/复制，禁止写标签/封面/清单、移动、删除或在原目录生成产物；所有处理走本地 references 副本。源摘要/大小/mtime 已回读一致。

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
