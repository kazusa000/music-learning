# 新音源全量入库与共享兼容恢复（进行中）

## 2026-10-23 14:08 UTC：诊断交接固定、实际默认版本与源正例

Goal 继续 ACTIVE。`goal-progress-20261023-140848.json` 实查原 full-h wrapper47740/worker46344 的 PID、出生时间和 plan 命令均匹配，仍为145通过/7失败/840待测，正在等待 H 盘空闲；不启动第二份 Kontakt 批次。正式九表/head 与 E 内12固定文件再次相同，H 两pin未在忙盘期重读。此时仅见后来新启动的桌面界面，没有实际 engine/plugin-worker/audio-host 残留，不能把新界面当成此前待清理的旧宿主。

`9fd5a10`/`d4c98ff`/`4e38a19` 完成独立 MIDI-delivery/readiness 诊断代码和源正例绑定，16项专项及 Ruff 通过。首开/重开都使用原先实际非零的 LP Old School Solo/native Default 完整状态，音符48、力度0.35，记录真正交给 processBlock 的 MIDI、逐块输出和三次只读加载状态。最后一次 readiness 采样按真实跨越项目末尾的音频块尾端核验（220672），不会把原始位置钳成220500。执行交接文件 `midi-delivery-stage-plan-01.json` 的 raw SHA 为 `7d17fd47f5bace28ad07355132afaf51f462b19712bea4252ea590f6d30686a1`，唯一短输出根为 `data/maintenance/instrument-intake/q/md07`。这仍是准备好的诊断，不是已经发声通过。

当前441控制 execution-04不变；后续单一耐久协调器按441完成并释放进程/配置、当前同环境512基线、note48诊断、六项 World on Fire 触发保持对照依序推进。`5fc8596` 的2项回归验证协调器实际 argv 不会被旧441冲突规则误认作配置持有者；`eadc170` 的3项回归固定512输入与原请求仅输出路径不同。六项 Riff 为保持1/512/486619采样的首开/重开，不能混用旧 Gotten/80s 失败。

默认发布已由并行风格库任务切到 `11f0aa5`，前端 `4ade6eeb...`，原生仍为 f056、桌面仍为 aa81。本诊断显式使用归档V8 Python源作为原因对照，同时核验原生四文件与当前默认相同；不回滚、重启或接管风格库发布。`active-release-drift-20261023-01/assessment.json` 记录该差异。当前79款指纹仍为19ready/8failed/52cancelled；52项让路试听必须在共享修复与真实验收之后续回。`7332324` 的未来失败证据保留修复尚未部署，不称旧失败原始录音已找回。

新增源代码对应线索：旧 render 循环每64块派发一次消息，512块长下恰为32768/65536帧，与132/134份录音的最后活动边界吻合。这是值得检验的对应关系，尚不能认定消息派发或异步恢复就是根因；不删消息泵、不添加任意等待、不放宽发声门。`2f73237` 的 Kontakt 失败项执行桥已完成19项回归，root审查另发现启动器/冻结runner传入repo而非full-h根的两处门控不一致，正在增加真实根路径回归后修正；原活动 full-h 不改，也未启动失败重试。

## 2026-10-23 13:44 UTC：固定PCM边界、对象映射纠正和失败续跑准备

对67款普通源的first/reopen共134份原WAV重新逐字节SHA校验并求最后非零帧：123份为32767、9份65535，另外2份AGM为4409。LP/SC/M均出现固定边界，132/134恰为32768倍数前一帧。`source-pcm-cutoff-audit-01/report.json`记录每份音频及产品分布；这是固定活动边界的直接证据，不能继续只解释为自然释音，但尚不能据此确认流式采样、缓存或时间计数哪一个是根因。未运行新音频、访问H或更改原阈值。

`block-alignment-control-01/execution-04`已真正首开：相同原三音、原宿主SHA`3d044b3b...`、最终bank、仅block从512改441，进程退出0、实际WAV精确全零、三起音窗全零。独立reopen仍在H门控，监督5276（13:20:31 UTC）身份健康；不能拿首开代重开，也不能凭历史512阳性与当前441阴性直接归因为offset。下一步是当前同环境512对照，之后再安排触发保持、note48实际worker输入/逐块输出和loader观察。`e0a8b03`监督器6专项通过；启动器两次早期身份错误均发生在H/host调用前，原失败保留，修正依据实际venv/base-image观测而非放宽命令归属。

原LP构造器汇编纠正了只读readiness阻塞的一个分析错误：四个不同地址都显示同名`Aglp3AudioProcessor::vftable`，此前误选的`0x1851f8770`其实写入processor+0x378，其RTTI偏移与+0x210并无矛盾。正确主表为`0x1851f90d0`，+0x210次接口表为`0x1851f8a90`。路径必须先从已核验IComponent wrapper的+0x38读取指针，核验次表，再减0x210并核验主表。`aglp-loading-readiness-rea-01/component-primary-mapping-correction-01.json`绑定原snapshot SHA、constructor query/evidence及八条精确LEA/MOV；独立reader和夹具随后按该路径修正。实际回读尚未进行，不能宣称已经观察到Success或解决静音。本轮新REA查询未取得新结论，已关闭会话并实查open=false；以上纠正完全来自保留的原汇编。

`324e1ae`增加一次性Kontakt terminal-only计划/核门CLI，15项E夹具回归通过。它复用固定full-h的转换/音频/完整状态回调，正确区分语义指纹和原文件SHA；冻结runner按精确文件导入，不误用工作树同名模块，嵌套code路径不再误判工作区。实际失败重试未启动，共享执行桥继续开发；原992队列仍固定不动。13:44实查full-h为145通过/7失败/840未完成，Kontakt正式入库0。`catalog-projection-preflight-01`对992份元数据完整覆盖，归163型号且无冲突；当时144个通过候选满足当前型号/变体/中文加英文和品牌原文投影合同。这是E数据预检，不能冒充实际API、试听或正式发布。

`protection-after-qa07-e-only.json`保存正式九表/head和E内12固定文件与前基线逐项相同，H两pin未在繁忙期重读。总持久入口已同步真实控制、队列和52项未续试听，Goal始终ACTIVE。开发任务的StyleLibrary文件保留，不混入本任务Git提交。

## 2026-10-23 13:18 UTC：QA07真实发声失败，独立控制继续

`data/maintenance/instrument-intake/q/07/supervision.json`于13:11:02 UTC终态failed。前三次attempt只有H磁盘压力defer；attempt004实际使用V8提交`297929076`、固定源清单和f056原生bundle，首次LP私有轨已经进入设备播放，plugin ready、desired_revision等于applied_revision、missed_blocks/underruns均0，但设备peak和轨道meter都是0。它不是先前合约或MAX_PATH基础设施失败，也不等于已定位静音原因。该次引擎23924、插件57004及监督428/24516均已实查退出，原attempt报告不改；canonical07 locator已从陈旧running_attempt同步为终态，旧字节及assessment保存在`instrument-release-engine-qa-07`。

`block-alignment-control-01`只把相同三音请求的block_size从512改为441，使全部note-on/off落在块边界；`riff-trigger-timing-control-01`只改变原94触发键的保持时长。两个都是独立原因控制，不是入库验收，也不要求QA07成功后才运行；在各自进程/配置/固定bank与H空闲门控通过后自动推进，不再向用户索要常规许可。首个block监督启动在任何H/音频访问前发现Windows venv进程image为base executable，原失败保留，新监督修正精确身份校验并回归后换独立execution。不得把零调用启动错误归因插件。

`a277df4`新增独立Ample只读readiness诊断与构建器，C++夹具、Python构建器3项和Ruff通过。它只绑定LP4精确模块SHA及已证IComponent vtable；由于processor主对象调整仍有静态证据矛盾，实际配置返回`unsupported_component_mapping`，不解引用未经证明的loader字段。不能把独立宿主编译成功称作加载就绪或音频修复。REA后续已证明94映射内部0x4e并保留力度，普通事件的偏移字段被累计保存；0x4e到R-Play consumer仍未证实，诊断不依赖臆造该结论。

`goal-current-20261023T131817.json`按实际PID/creation_time/完整命令核对Kontakt full-h的47740/46344仍运行，队列为140通过/7失败/845未完成，Kontakt正式入库仍0。该数字仅为13:18快照。原批固定文件、报告和队列未修改。总持久入口`runtime/instrument-intake-pause.local.json`已同步真实QA07与当前批次，Goal ACTIVE；历史pause文件名不构成停止指令。52项让路试听仍列为必须续回，未伪称预热完成。宿主关闭请求已处理，不停止健康验证进程或其他应用。

## 2026-10-23 后续：先证明多音符演奏，再续试听预热

V8新宿主的两个当前ready Riff实际WAV已在E复算：World on Fire的25个0.5秒窗、The Silence of Faling Flower的55窗都只有前2窗非零。报告`product-preview-new-host-window-review-01/report.json`保持当前音频SHA，不把预览ready说成完整乐句通过。进一步对79条目中67个无profile的原first/reopen收据、请求、状态与WAV逐项只读审计（`unprofiled-three-note-source-audit-01/report.json`）：原MIDI固定48/60/72于0/1/2秒，每音0.5秒；全部67项两轮的note72后0.5/1秒窗均没有能量，note60仅first3项/reopen6项的1秒窗有能量，没有任何条目证实三次独立起音。Autumn Leaves(Blues)两轮同一WAV SHA也仅首音48非零。因此原收据只证明原非静音门通过，不能把它扩展成完整音区/三音演奏验收。原结果不改；继续区分触发MIDI数字、NoteOff、非零block offset和样本加载就绪等共同原因，用独立单因素控制验证。

新产品的5份`Preview is silent`失败目前只证明原阈值未过；旧finally已清理这些raw WAV/response，不能反推精确RMS或把它们都写成exact-zero。`7332324`新增所有未来试听尝试的preview_id/preset_id/generation绑定，并在失败时保留原始native response，成功仍清response，大体积重复录音照常清理。41项相关回归/Ruff通过；**这项代码还未发布，默认仍V8**，不宣称已为旧失败补回不存在的证据。

为了让真实音轨QA05和共享故障对照取得音频窗口，本轮79当前缓存中尚未开始的52个queued项在单次SQLite条件事务里让路：只更新status=queued且generation相同的行，不改loading/ready/failed，不停止任何host或Kontakt验证。before/state及后续恢复入口在`preview-qa-yield-01`，locator为`runtime/instrument-preview-yield.local.json`。Goal始终ACTIVE；共享修复/实际验收完成后，必须通过`resume_previews.py`和产品API以新幂等键、当前指纹续完这52项，不能遗忘或重放旧发布回执。内部resume-ready证据由本目标控制器生成，不要求用户再次确认。QA05原waiter仍有效，attempt001只有磁盘门控defer，不是原生音频失败。

用户关闭宿主后再次核对：MusicLearning/音频host/engine/worker等均已退出，无残留需要停止；API仍ready，Kontakt原监督保持。H仍繁忙时保留原进度等待，不把磁盘压力当全部音频问题的已证根因，不停止其他应用或固定验证任务。

## 2026-10-23 12:32 UTC：V8与实际新试听宿主已接入

V7 `705664d`的完整冻结回归原结果为824通过/3跳过/1失败，唯一失败是PresetId命名空间规则未同步到生成的JSON Schema。`2979290`重新生成合约，`export_contracts --check`和前端typecheck通过；新V8固定提交`2979290760419fd68cd74207aeacc01b20bb7ddd`，完整service **827通过/3跳过/0失败，270.42秒**。报告`runtime/test-reports/20261023-122531-316f78ea`；6118文件/7345目录/1自有联接临时夹具自动清理，0错误。V7原失败保留。

共享总线修复`064b56a`保留原stereo/sidechain验收边界，在独立目录协商主总线布局并补充逐bus诊断。新host SHA `a803e794fa74b1373a48b7cbf23d1ea5137b6b9e8869a01d14afc179b92c0a41`；真实`product-preview-full-state-ab-06/supervision-01/state.json`已completed。AGM同一预设的factory-preset和admitted-full-state均实际输出749700帧、44.1kHz立体声，RMS 0.004907539、peak 0.179005265、65290非零采样、0削波，二者WAV SHA完全相同`600eec67cd47eae64b8af5b3fcd1347ba6f5d04a0964890cbc524d5be829734a`，完整2320参数及原生身份通过，配置SHA恢复。旧host拒绝与早期零attempt报告保留，不能覆盖。

正式引擎无会话、无活动jobs/对话后，通过已有stop.request平滑退出并激活V8；配置只添加`admitted_preset_host`指向E内容寻址的上述新host。旧native_host及web3b9d/nativef056/desktopaa81逐项相同，旧Keyscape/Pianoteq1252缓存指纹保持。`product-preview-host-activation-03/{activation-before,activation-after}.json`保存正式九表/head逐项相同证据。没有写正式工程、运行正式渲染或音乐模型。最新`goal-progress-20261023-123242.json`再次核对正式表/E内12固定SHA相同，H两项未繁忙重读，最近完整14项仍为full-g-transition-01。

新后端已经自动为79个已发布条目建立当前指纹试听：**1ready/1loading/77queued**。这是12:32:42 UTC快照，不是全库音频验收；原历史行保留，全局ready不可当79款当前ready。实际产品加载/换音色设备QA05另做，QA04的`Contract validation failed`发生在jobs.submit前，没有原生音频失败证据。

两条已审查的源文件标题通过presentation-only CAS附加到H指定库catalog，E独立证据`presentation-title-publication-02/{plan,catalog-before,catalog-after,publication}.json`。API实际返回“金属风格·E 小调·100（Metal-Em-100）”与“老派独奏（Old School Solo）”，产品名Ample Guitar LP保持英文。除presentation_title外全目录逐字段相同，native Default身份、默认语义和完整状态不变，目录仍1331/Ample79。

Kontakt full-h真实wrapper47740/worker46344及ctime/命令仍匹配；当前CSS6过/1败、Solo5过/6败、Factory67过/907待测，合计**78过/7败/907待测，Kontakt正式入库0**。不修改原固定批，全部终态后再用独立失败队列；`476ed06`修正失败重试计划区分candidate/receipt语义指纹与原文件SHA，9回归通过，不把raw SHA冒充语义绑定。

Retry08两款完整Riff均真实精确静音、原生调用成功，原报告保留（efe3b73）。额外E窗口统计发现原部分非零Riff只在开始约1秒有活动，不能把首击非零说成完整多小节演奏。当前线索是共享触发键只保持1个采样；将按官方Riffer语义做1/512/完整乐句保持的单因素独立控制，确认后才修改共享profile，不先加任意延迟或放宽音频检查。HM2-07诊断目录加UUID达273字符而失败，配置已恢复、宿主未启动；其路径修复和真实NativeHost集成回归与新独立控制另行推进。Goal和本聊天跟进继续ACTIVE，常规bug自主修复，MODO仍按用户暂缓、不探测。

## 2026-10-23 11:56 UTC：完整状态试听专用宿主路由与V6候选

`bf7cd00`新增通用可选设置`admitted_preset_host`。所有带`native_admission`协议的音源试听统一选择它，并将实际所用宿主SHA纳入缓存键；未配置时保持原合同，配置后文件缺失或发声/状态检查失败也不会退回旧host。普通Keyscape/Pianoteq条目仍使用原`native_host`，原缓存ID不变，避免一次新宿主修复触发1252份旧库重复预热。完整状态、全部参数、原生身份、削波和静音检查未放宽。75专项通过，后加普通Riff实际命令选择断言后相关39项通过，Ruff通过；这里的命令/音频测试使用E夹具，不冒充真实VST3结果。

V5固定0175146完整service802通过/3跳过（218.68秒，`runtime/test-reports/20261023-114726-3c881d88`）；包含上述宿主路由的最新V6为`bf7cd0017b7d4f95400719dff51e58b8a2b81b19`，描述符`runtime/application-release.intake-preview-20261023-v6.local.json`，显式冻结pyproject完整service808通过/3跳过/0失败（245.48秒，`runtime/test-reports/20261023-115133-6f234d4e`）。V6临时夹具4080文件/4329目录/1自有联接自动清，0错误；只读发布验证通过，尚未激活，正式仍0b79f96，3b9d/f056/aa81及固定14项不变。新离线host在独立目录构建；旧A/B失败与v1零attempt记录保留，只有真实新host对照通过后才考虑配置和发布。

此时full-h为CSS6过/1败、Solo5过/6败、Factory13过/961待测，即24通过/7失败/961待测。QA04已有1次在读取catalog前因H压力正常defer，0实际发声结果，不能继续按旧0attempt或新1attempt称验收。原队列继续，常规修复和入库Goal仍ACTIVE。

## 2026-10-23 11:44 UTC：Factory续队、真实宿主拒绝与监督收尾修复

`full-h`仍为原固定plan，wrapper47740/worker46344按实际命令和creation time核实。全部992项队列已建立：CSS6通过/1源加载超时，CSS Solo5通过/6失败，Factory1通过/973待测，共12通过/7失败/973待测；Kontakt仍0正式入库。五项Classic Legato首开精确静音与另两项来源转换超时分开保留；f3acdc6新增显式重叠音符演奏profile，82相关回归通过，但尚未实际证明能修复这五项，不修改运行中的原批或提前重标通过。后续仅在原批终态/profile租约释放后进行共同原因控制和独立失败项重试。

LP `phrase-duration-retest-02`两项完整乐句均已真实终态失败，首开WAV精确全零；Gotten(Fingerstyle)/80s Solo与Retry06的Sweet Child/Used to Love Her是不同ID，不混合统计。完整时长修复解决协议截短，但不足以证明全部静音根因；E原请求/预设/完整状态/参数和采样索引对照见`riff-silent-lineage-01/lineage-comparison.json`。原监督收尾另被Windows PID复用误导：两个无关程序早于新runner出生，旧PPID闭包仍将它们纳入观察子树。8e96fa9/e25ba5b在每条父子边核对出生顺序，并对已观察孤儿核对PID/ctime/命令；未知身份仍阻断，20回归及Ruff通过。独立`phrase-duration-retest-02/supervision-02-lineage-assessment/assessment.json`确认真实自有进程已退出，原队列/原checkpoint字节不改，无关tail/OneDrive进程未停止或改动；canonical locator现为`completed_with_failed_items`，旧locator另存原字节。

旧固定host的实际同MIDI A/B已完整结束：factory-preset于11:24、admitted-full-state于11:38均返回`Expected stereo output and optional stereo sidechain`，三个来源/完整状态导入证明不能替代该实际调用结果。两次AGM InstDir SHA一致，原request/stderr/状态保留于`product-preview-full-state-ab-01/supervision-01`。新共享总线适配及诊断将在独立host构建验证，不覆盖固定14项或f056，不以关闭检查伪造通过。QA04仍为0attempt的等待器；V4未激活，完整791项通过只属于原固定候选。

预热outbox新增一项实际合同修复：先发布音色、随后附加审查后的中英文来源标题时，原整条相等判断会拒绝合法的presentation_title。E内先复现失败，再仅允许该已验证字段附加；其来源SHA、原生身份和候选字段绑定必须通过，其他全部字段（包括完整状态和音频通过标记）仍逐项相同，原发布plan和幂等键不改。36项预热/标题专项及Ruff通过，原失败测试报告保留；未向H目录应用标题、未新调插件或正式渲染。

恢复新入口包括`runtime/ample-final-bank-riff-retry-06-attempt-02-job.local.json`（原preparation失败保留，后续独立attempt修复）及`runtime/ample-hm2-version-index-controls.local.json`（新source-pinned控制，H忙和实际占用门控）。每次需查真实进程与checkpoint，不根据locator旧status称运行。完整当前只读记录`goal-progress-20261023-114419.json`确认正式九表/head与E12固定SHA同基线，H两pin未在压力期重读。Goal/本聊天自动跟进继续ACTIVE，不唤醒开发聊天、不访问NAS或tmp原素材。

## 2026-10-23 当前指纹、真实进程与剩余故障分类

11:21 UTC新窗口中，full-h实际队列为CSS4passed/1原source超时/1running/1pending：2nd Violins、Basses、Cellos、Full Ensemble均通过，Lite Ensemble正在运行。新快照 `kf22/reports/full-h-progress-20261023T112106.json`保存原队列SHA及逐phase音频、身份、4145参数计数和完整状态摘要；992整批尚未完成、Kontakt仍0正式入库。前两款的NKI/NKM/原生身份和四份WAV/PCM SHA已独立对照，各自互异，未发现跨乐器复用声音或状态。下面早先waiting_storage记录只是当时快照；持续读取实际current、队列及PID，不根据旧storage_budget推断活动状态。

最新固定后端候选为7b621fe/V4，`runtime/application-release.intake-preview-20261023-v4.local.json`。显式使用冻结pyproject的完整service回归791通过/3跳过/0失败（211.14秒），记录 `runtime/test-reports/20261023-110828-a2fdd23c`；4035文件/4293目录/1自有联接的临时夹具自动清理，0错。只读发布验证通过，尚未激活，默认仍0b79f96；web3b9d/nativef056/desktopaa81不变。通用presentation_title已实现来源SHA/原生身份/原候选指纹与来源文件名绑定、中文加英文显示和独立CAS发布；原name/variant/default/完整状态不改。82专项及实际load_admitted夹具合同通过，真实H目录尚未附加该元数据，E审查赋值保存在 `presentation-title-plan-01/reviewed-assignments.json`。

独立实际设备QA04已建立耐久监督，入口 `runtime/instrument-intake-engine-qa.local.json`，启动时wrapper45312/child24420，0次尝试，避开A/B和LP实际任务。c4aac99进一步修正未知key_range被当作不可弹的选择错误，明确范围仍严格检查；比对完整candidate_fingerprint及native_admission/state_sha256，只允许展示元数据附加，41专项通过。7份E计划已预检选出三个产品6普通款和4完整演奏profile（3产品Riff及Strummer）并构造全部乐句，尚非实际设备发声通过。QA03在零attempt/无宿主时核对自身PID/ctime/命令后安全结束，旧报告保留。2cd414c先前修正QA将规范JSON指纹误当文件SHA的问题，原计划内容未改；旧无效50004亦保留为failed_preflight。新进程每次仍须重核身份。

Goal和本聊天跟进继续ACTIVE。新只读核对保存于 `20261023-ample-shared-fix/goal-progress-20261023-1111.json`：full-h47740及子46344、Riff retry02的31092及监督44504、A/B9452/22936、被动观察器26568及子6260均按实际命令和creation_time核实，不能将locator的started文字单独当作运行证据。full-h仍current=null/waiting_storage，没有新增Kontakt原生验证结果；正式engine停止/无session，正式九表+heads和E内12固定SHA与上次激活相同。H两项未在忙盘窗口重读，最近完整14项仍引用full-g-transition-01。未停止其他应用或其他聊天任务。

试听统计必须按当前发布和指纹计算。`product-preview-recovery-01/current-fingerprint-status-20261023-1113.json`重放0b79f96的真实fingerprint函数，对79已入库项为71failed/8queued；此前3ready的Riff缓存属于旧phrase指纹，不能继续称为当前已就绪。全局1253ready/81failed/8queued包含历史行，不代表当前79款可试听。711af51完整状态候选尚未激活；激活后所有新native_admission项会使用新的共享状态策略指纹，原记录保留，不能将排队当完成。

剩余故障进一步分离：5个LP来源的原attempt曾在进入probe前被output目录契约拒绝，110f4e0已修共享根校验；后续真实retry3/4中三项为HM2参数不符、两项为silent，不能全部改称路径故障。精确5项retry05仅prepared保留，不盲跑；三HM2并入共享原因，两silent只有最终H bank相对旧staging有可证环境差异，将另建2ID控制，不与当前完整Riff两项重复。SEQ/HM2/T-Track仍按共同原因处理；T-Track源嵌入外部GP5路径和实际参数注册证据见 `ample-ttrack-investigation.md`，未访问D盘或忽略参数。

`runtime/instrument-intake-pause.local.json`沿用历史文件名，但当前status/auto_resume/goal均active；本轮将Kontakt主字段从历史full-e阻断状态纠正为full-h真实运行等待，原值留在recovery_history并另存E备份，避免后续只读到旧段再次误判暂停。

## 2026-10-23 full-h续队与实际产品后端更新

后续固定预览候选711af51完整service为738通过/3跳过/0失败（240.95秒，`runtime/test-reports/20261023-102950-18207491`），目前仍staged，默认0b79f96未变。真实旧host同MIDI的factory/full-state A/B已由 `runtime/ample-preview-state-ab-job.local.json` 持久监督，等待H三点idle、无实际插件宿主/同产品配置冲突及正式engine空闲后逐个运行；不能按两份请求准备成功称音频通过。启动时PID9452/shim22936，state在 `product-preview-full-state-ab-01/supervision-01/state.json`，每次须重核身份。Kontakt最终派生索引监督已提交8935bd6，入口见 `docs/benchmarks/kontakt-derived-index-postload.md`，h终态/所有Kontakt退出/队列完整前保持E-only。`CSS 2nd Violins`同源在旧e超时、g实际125.883秒完成；resources_ENG/lib_ENG日志也出现在成功项，不能把这些警告或180秒超时直接认定为坏样本。

`full-g-transition-01` 已捕捉到无活动条目/插件且profile锁持有的安全窗口，仅停止已核验worker50676，wrapper52468自然退出。原g报告/失败不改；stop采集器在进程退出后误用psutil.returncode的异常单独记录，随后补录进程缺席/profile原字节与mtime/无journal/正式表及14pin一致。`full-h` 已从固定cd13f0b启动，plan SHA `fe099852116f8d05e1c385d60d364250ddc1b5de32577c14969dc786a0f1a0b5`、134源码/输入pin，publish/preview均false。11份完成的Lua转换经原始请求/回执/执行及NKM SHA重放后复制到新run（CSS5/Solo6），981份仍需转换；全部992份原生验证队列重新建立，不能把来源复用称作音频通过。locator现指向h，wrapper47740/worker46344只是启动时身份，须持续重核命令及creation_time。

`0b79f96` 不可变产品后端完整service回归732通过/3跳过/0失败（259.16s，`runtime/test-reports/20261023-101908-b5991bdc`），先前cd13的5个旧夹具失败原日志保留；修的是冻结测试的证据根与新AI字段/工具合同，产品逻辑没有为了测试放宽。正式引擎无会话/播放、jobs及对话无活动后，经stop.request平滑退出并部署该后端，保留最新3b9d前端/f056原生/aa81桌面及三个Ample精确插件路径。`product-preview-recovery-01/activation-*.json` 与保护报告记录正式十项摘要（九表+heads）前后相同、E内12pin相同；H两pin未在繁忙窗口重读，上述g→h窗口的14项独立完整核对保留。

原79个试听行目前3ready/76failed的E-only诊断已归档，不能当作79款音源失效：66是旧key_range错误、1是早期allowlist、6是实际silent、3个AGM由旧宿主输出布局门控拒绝。原request/failure/stderr保留；旧raw.wav/response已被原预览清理流程移除，不能伪称复算过这些WAV。进一步发现预览只载入factory .vstpreset，而音轨复用完整native_admission状态；`711af51` 将所有带该共享协议的未来音源试听统一为load_admitted、完整state_base64和加载后身份/完整参数检查，不回退原始preset。缓存键只给新native_admission加入状态引用与策略版本，旧1252份缓存键不改。40项专项通过，独立v3固定快照已准备；实际同MIDI的preset/full-state音频对照及最终发布仍继续，尚不把此代码通过称作非静音验收。

Ample两款完整Riff重试入口为 `runtime/ample-phrase-duration-retest-02-job.local.json`；仅2个ID，原短句失败保留。剩余SEQ/HM2/T-Track等按共同原因继续隔离，活动固定诊断不改。跟进仍ACTIVE且仅绑定音源导入聊天，已更新为上述持久入口，不唤醒开发聊天。

## 2026-10-23 首批79款已发布与产品试听收口

用户明确要求目标持续 active；常规故障自行修复并续跑，不按旧暂停记录停止。`admission-publication-02/publication-state.json` 已 completed：AGLP22、AGSC39、AGM18，共79款 additions-only 正式入库，总目录1331（原Keyscape455、Pianoteq797未改）。每组重放原始完整状态/音频/来源回执，实际不可变产品后端在E隔离目录走 `import_admitted`，没有旧捕获回退、新录音或正式工程写入。79不是113全部完成；2款Riff需完整乐句补测，31款原失败和1款确缺AGMPk继续独立处理，原报告保留。

三个Ample最终bank的Default首开/空机架重开控制均通过。`install_bank_path` 只把各自配置的InstDir接到H库最终bank，E保留原字节备份；插件仍使用已安装的C盘VST3，不删除。`plugin-root-allowlist-20261023T095609Z` 保存严格已入库来源审查及CAS配置变更，只注册AGLP/AGM/AGSC三个确切bundle，没有开放整个C盘插件目录。应用空闲时已平滑重载，SDK读取配置确认新路径，原生/桌面发布未改。

实际试听handoff共请求79款，12次API回执成功、67次500；这不是67款音源损坏。原始API栈证明普通试听错误地强制读取可选的key_range。`8664384` 修复共享普通短句：未声明物理音域时沿用乐器类别的标准音区、不向目录伪造音域；显式范围仍严格校验。后台预热在忙盘时保持queued，20秒后重新检查，不启动额外采样器；采样不占SQLite写锁，期间取消无法被晚到的加载覆盖。28项预览专项及Ruff通过，需在新不可变产品后端部署后复核实际ready/failed及实际音轨加载，不能把提交/排队算作预热完成。

Ample Riffer 的完整源乐句规则修复：先前 30 秒预览上限会把 8/10 小节 factory Riff 缩到 7 小节，提前发送 stop note；旧成功请求完整播放分别约 30.968 秒和 40 秒，新短请求首开均得到精确全零。E-only 对照记录在 `20261023-ample-shared-fix/phrase-duration-retest-01/diagnostics/strict-duration-compare-01.json`，确认 host、preset、加载状态、节拍/拍号和最终资源清单一致，原始失败不覆盖。共享规则现让 Riff 始终按完整 source bars 触发，最多 120 秒；超过上界直接拒绝，不截短 trigger。产品试听缓存按新 phrase policy 版本换键，WAV 检查上界涵盖 120 秒 phrase + 1 秒尾音，未放宽非 profile 音频原有 5 秒限制。108 项相关回归通过。`phrase-duration-retest-01` 两项均已终态失败并保留；下一步在独立 `retry-02` 中用最终 AGLP bank 仅重测这两个 ID，first/reopen 和声音/状态/来源阈值不变。

Kontakt `full-g` 已真实进入来源转换，但模块间descriptor归一化丢失is_instrument，导致下游二次验证拒绝；`39976b1` 保留该身份字段，54专项通过。旧批及失败保留，`a928940` 新E-only续队工具对已转换NKM重新复核Lua请求/原回执/执行及输出SHA后复制到独立attempt，失败/待处理项仍需重做；旧音频验证不复用。需在旧批无活动条目/插件、profile完整恢复后才切新固定批次，PID与实际命令每次核验。派生NKC索引与固定静态资源分离记录，但不修改受保护的installed_libraries验证器；最终批结束后还需无Kontakt进程时双快照与静态清单复核。

## 2026-10-23 固定源码、真实控制与严格发布桥接

目标继续 active；不根据历史暂停段或旧 locator 停任务。Kontakt 标准 NativeHost 180 秒边界的独立空机架首次/重开已真实输出非静音、无削波音频。`run-06` 的原失败保留；`e458a99` 修复通用试听控制状态比较：全部 4,145 个参数 ID 仍必须完整，非试听参数精确相等，明确发出的 CC1/CC11 严格核对对应标准归一化值。58 项专项通过，E 内新 assessment 重放原请求/状态/退出码/录音，不覆盖原报告。`api-compat-run06/result.json` 另记录实际产品 `import_admitted` 接入 E 隔离目录，完整状态 SHA、插件类和参数一致、无新录音及正式工程写入。

`fb7a2a4` 增加 full-job `--source-release`，先验证不可变 Git release 再冻结代码及分类清单，显式输入清单仍单独固定；脏工作树不会混入后台批次，53 项专项通过。新的 `kf22/full-f` 实际启动后在 CSS 的 `Libraries/css/Samples/CSS.nkc` 内容校验处终态，原 `progress.json` 和保护报告保留，不能把旧 PID2640/36560 称为运行。正在成组核对三库派生索引变化及清单修订；不放宽样本 SHA 或反复启动同一失败批次。后续恢复必须使用新的独立 attempt，并实查 PID、创建时间、命令与队列。

Ample 三个最终 bank 的完整新资源审计均 audited：AGLP363、AGM136、AGSC363 个文件；不是音色入库数量。E 内 `strict-publication-preflight-04` 重放原音频和完整状态，113 项为81原队列通过，其中73项严格可准备、8项需补证、31原失败、1缺AGMPk。AGM 数值 wire-format 的共享兼容修复及新报告另行保留，不按容差或只看 status 放行。

最终路径 Default 控制的 AGLP/AGSC 已真实首开及独立重开非静音、无削波，完整主机参数及原始状态链可核。监督器额外规则错误地要求首次加载 preset 的请求也含 inline state，并误读已结束 offset locator，另有 audit SHA 被空值覆盖。原报告不改；已核对 worker45444 创建时间/命令、无子进程/活动 attempt 且三配置原始 SHA 恢复，停止的仅是该自有空等待器，记录 `final-bank-supervisor-gate-repair-01/stopped-waiter.json`，没有停止插件或正式引擎。共享门控修复、新离线 assessment 和仅剩 AGM 最终路径控制继续推进，不重复录制已闭合两库。

新增严格物化/发布桥接 `631b639`：只从固定 preflight 选取完整证据通过项，按产品和固定 validator 指纹分组，小型 preset 原子追加且拒绝覆盖，正式发布前重放 `verify_receipt` 和真实产品导入；45 项相关回归通过。`9dbf8d8` 另补最终采样路径安装：当前 LP/SC 配置为 NOT FOUND、AGM 为旧 tmp 路径；须等最终控制释放配置后，用原字节 E 备份与 CAS 把唯一 InstDir 接到对应 H Sound Library bank，保留其他配置，4 项专测通过。准备、原始 probe 通过及排队均不等于发布完成；catalog/API/试听预热另核验。

完整固定 b159146 service 回归631通过/2跳过/0失败，报告 `runtime/test-reports/20261023-085805-b1d9b8bc`，不能替代全库发声。默认产品由并行开发独立推进至8edf924，web fc05、native f056、desktop aa81；本任务不回退默认或停用户引擎。恢复以来正式表/head与14固定文件最新比较独立保存；若并发 API 造成变化如实记录，不恢复旧基线。根目录工具、历史证据及他人文件均不混入本任务提交。

## 2026-10-23 用户明确恢复目标与全部剩余入库

用户明确纠正此前暂停判断，要求设立 goal、全部剩余入库前持续推进、常规 bug 自主修复且不逐项询问。本聊天 goal 已 active，原 keyscape 跟进恢复 ACTIVE，目标仍仅为音源导入聊天；旧暂停文字已撤销，历史暂停记录只留作经过。MODO 仍沿用用户暂缓范围，AGMPk 缺失如实保留，不伪造资源或通过结果，也不因此停掉其余工作。

最新Ample Riff复查（2026-10-23）：`phrase-duration-retest-02` 最终为0通过/2失败，确切为 Gotten(Fingerstyle) `8256c94444ba252a98278476` 与 80s Solo `85c1f3d98a2a580ba7f13ccf`；两项最终安装库首开WAV精确全零，原retry3 staging库首开与独立重开均非零。E-only报告 `20261023-ample-shared-fix/riff-silent-lineage-01/lineage-comparison.json` 已直接比较请求、全状态、2,298参数、VST3身份、预设字节和五组采样索引。每对的完整乐句/事件完全等价（唯一任务标签`revision_id`和run-local预设路径除外），但最终bank root/manifest变化且native调用耗时更长；无插件sample-ready时间证据，因此根因仍未确证，不能按当前材料把静音项重标通过。精确这两款和 retry06 的 Sweet Child/Used to Love Her 是不同队列，不混项。

retry06的首个outer attempt在probe队列创建前因只读preparer自身`--source-run`出现在命令行而匹配到源run扫描，已以精确当前PID/预期preparer可执行路径/匹配source路径规则修复，且保留其他实时supervisor阻断与回归。LP2原checkpoint的PID复用已由共享birth-order helper修正；仅排除被当前命令、PID、creation time证明早于runner的旧别名，真实child和未知身份仍阻断。旧attempt、locator、checkpoint、报告不覆盖；下一次使用独立attempt/locator/checkpoint，并保留三点H存储gate和全套发声/状态/恢复门槛。

恢复时按各轮队列的最新同 ID 结果合并，Ample 113 项为81通过/31失败/1缺采样，尚未发布。新增共享资源审计 `f7daee7` 复用稳定文件签名、完整 SHA、分块 IO 预算与断点续审机制，不信任旧样本哈希；从原始准备日志核对全部 ASCF/DBConfig 覆盖与硬链接/独立副本关系，产出产品 bank 相对的内容寻址 installed manifest。8项新回归及9项已有资源审计回归全部通过，Ruff通过，测试夹具由统一入口清理。

不可变资源审计已启动于 `20261023-ample-shared-fix/fresh-resource-audit-01`，定位 `runtime/ample-resource-audit-job.local.json`，plan SHA `46262d918af7afcc1bf917c183bf43a96166eca32c20552b0e325a9a1da9d601`。启动包装 PID35592，实际 Python43428/ctime1792743769.618822（每次必须重核命令和 creation_time）；H忙分块暂缓，不改旧报告/清单/采样。产品结果写入该run的 `{AGM,AGLP,AGSC}-result.json`。实际发声适配与严格入库证据桥接跨来源独立推进，不把这份资源审计当成音频通过。

Kontakt 已完成资源准备审计；下一步为 b5d27bd 实际路径 descriptor 修复的首开/独立空机架恢复控制，通过后再新建 full-f 固定验证，使用显式 Factory inventory revision，保留 full-e 全部原始失败。恢复前正式九表/head及14固定SHA快照在 `20261023-goal-resume/protection-before.json`；不停止用户正式引擎、不在正式工程验证、不触碰 NAS/tmp 原素材。

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
