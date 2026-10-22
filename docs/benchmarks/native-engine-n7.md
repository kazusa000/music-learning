# N7：默认发布前验收

## 2026-10-22 恢复执行

### 已默认部署与最终收尾

N1–N7已完成并默认发布。描述`runtime/engine-release.desktop-final.local.json`已激活为`runtime/application-release.local.json`：源码d4852c6、原生f0563ad2、前端658f639e、桌面aa81f952。`start.cmd`经固定源码启动服务，实际解释器的cwd/PYTHONPATH均为发布源；默认SDK/CLI及正式主页只读检查成功，240DPI/DPR2.5。当前端口18765；发布先持统一锁写同描述barrier，再原子更新项目端口、由apply完成指针，未改Windows设置或数据库版本。

`endurance-v8/report.json`最终succeeded：参考305秒（306秒观测）、当前395.64秒加尾音（398.64秒）、1800秒循环，三段源时钟误差均0，回环gap/断续/时间戳/逆序/非有限、设备欠载/xrun及全部六插件missed均0。20次原生回调观测P9515.86ms，不冒充端点首声；端点延迟沿用`latency-packaged`完整20次数据。H本次冷装载28.52秒，三次暖切回65–67ms，六个worker PID保持到完整曲及循环结束。所有独立验收进程已退出，旧v1–v7失败及locator原报告保留，不继续开批。

完整后端510通过/1权限跳过，实际桌面包四组、4K UI八组、真实AI、设备恢复均已归档。初始50ms监听响应目标仍有约5–7ms差距，failed原报告保留；不声称全库音色人耳质量或物理设备拔插已验。

默认启动观察首轮误将venv launcher的Temp cwd当真实解释器、又把原日文字形綾硬编码到显示名绫，分别在实际子解释器/PYTHONPATH核对与稳定DOM控件核对中修正观察，未改产品。原始观察保留；`default-startup/report.json`成功。该轮foreground标志false如实保留，不称该轮前台未变化；非激活桌面四组的证据独立来自`desktop-package`。

**正式数据边界：**v8前后九表/head和14pin相同；默认启动读回仍为原head和两项原任务。随后API于本次收尾期间记录旧工程删除、重新导入（失败）、再删除，最后正式列表0。`default-final-protection.json`及v2的formal_equal=false、pins_equal=true全部保留，`default-final-state-assessment.json`保存事件和边界；本任务没有调用这些音乐写操作，也未恢复被删除工程。后续重新导入的失败未纳入本轮引擎验收，任务已被删除，没有重跑模型来替换结果。

**临时夹具收尾：**仅本次27个精确pytest根，31041文件/26878目录已删，33份小诊断保留；9个reparse及36个父目录跳过，0失败，不改属性/不跟随链接。精确记录`cleanup-resumed-final.json`。E空闲在窗口内净增2.144GiB，期间另有上述API删除，因此不能全部归因于本任务夹具。原报告/发布包/旧N2/历史拒绝路径不动；本任务H库只读、NAS无访问，Pianoteq固定实现14pin相同，其他工作保留。

### d485固定包与当前唯一v8

`runtime/engine-release.desktop-final.local.json`固定d4852c6源码、f056生产bundle、658f639e前端和已测桌面五文件。`full-desktop-final`完整后端510通过/1权限跳过/0失败（230.20秒）；`desktop-package`实际不可变目录EXE四组通过，240DPI/DPR2.5/foreground保持。`device-recovery-packaged.json`再次真实设备恢复通过，范围仍为模拟stop回调和真实不存在设备拒绝，不声称物理拔插。

`latency-packaged`各20次端点回环，P95冷文件60.25ms、播放58.04ms、定位59.59ms、音量57.41ms、静音55.72ms、独奏55.39ms。后三项未达首次50ms目标，整项状态保留failed；无包断续/时间戳错误，未更换系统设备或放宽阈值。

唯一新长测`endurance-v8`于20:38:56 UTC开始，wrapper19320、实际Python43616，源码/导入身份在`endurance-v8-code.json`。先reference305-full，再一次采样器装载、20次播放定位/三次A-B、current-full和1800秒循环，严格PID复用/时钟/缓存/丢块检查不变。启动H三次只读性能计数busy0/1/1%、queue0，正式前九表14pin一致。当前未终态，禁止改固定源码/bundle/原报告或并发开第二批；正式尚未激活。

### 实际AI已通过与桌面包

`ai-process/report.json`在57dc8bd固定源码及f056生产bundle上实际通过。真实gpt-6.1-sol将唯一音符93改105，新head/applied/desired一致，0render；读回旧新快照，仅pitch变化。进程隔离流的1760/3520背景均0，修改前1760幅0.0017089，修改后1760仅3.53e-7、3520为0.0016822，保留原声学阈值。捕获包/QPC/非有限错误0；不把虚拟devicePosition0当硬件时钟。原端点失败及中间实验保留。

桌面发布新增五个已测文件的不可变目录与逐文件SHA校验，描述符同时固定source/web/native/desktop；默认启动从发布包取EXE，兼容未发布的开发入口。拒绝缺文件、部分文件集、外部路径及篡改，11项发布回归通过。尚需实际包四组桌面、完整固定后端、最新设备/延迟及三段长测，然后才激活默认。

### 按进程隔离声学核对

`ai-reuse-v2`的真实Codex事务、音符105和新applied_revision已通过，并检测到倍频14700Hz；但7350Hz残留高于原阈值，整项仍为失败。仅事前选择背景弱载波不能解释30秒后的残留，原报告/WAV保留；未提高音量/放宽阈值，不能断言残留来自工程或其他应用。

新增仅诊断的`ml_process_loopback`，按[微软进程回环接口](https://learn.microsoft.com/en-us/windows/win32/api/audioclientactivationparams/ne-audioclientactivationparams-audioclient_activation_type)过滤目标engine进程树；激活/生命周期参考[官方示例](https://github.com/microsoft/Windows-classic-samples/tree/main/Samples/ApplicationLoopback)，实现独立、有界（0.2–5秒）、只允许ml_engine目标并由Python再次核对当前自有PID/实际EXE身份。构建显式`build_engine.py --process-capture`，默认目标不含它；不重写生产四文件bundle。诊断源码/构建器SHA及EXE发布到独立不可变目录，调用前复核，拒绝覆盖证据。没有新建产品操作、改设备/系统音量或控制其他应用。

初次真实虚拟流的device position每包返回0，不能当硬件帧位置使用；原始零值/逆序观察保留，进程诊断只按包QPC连续性和系统标志检查。端点长测/延迟仍使用原`ml_engine_loopback`及其device-position严格检查，未替换或放宽。初版PCM16转换在暂停时有约1.04e−5 RMS量化噪声，改float32保持原1e−6静音阈值；原记录保留。

`process-isolation-v2`四组真实双WASAPI进程通过：目标9973Hz幅度0.00177634，兄弟进程7919Hz仅2.53e−7；暂停目标后RMS/两频幅度均精确0，独立端点正对照仍检测到兄弟进程；现有证据不可覆盖。进程捕获discontinuity/时间戳错误/包QPC丢帧逆序/非有限样本全0。没有读取H库或正式工程；所有自有engine/worker退出。生产bundle仍为f056，四文件SHA不变。

AI探针回到标准A6/A7（1760/3520Hz），采集当前私有engine进程树；原3倍背景、最小幅度和旧频消失检查保留，新增float/QPC完整性检查。这个诊断证明Windows应用渲染流，不冒充最终端点/声学延迟；后者仍由独立端点长测和20次延迟记录承担。须用新固定源码真实跑完AI，再进行新长测和部署。

### 最终控制复核与声学探针背景

05c80df候选的应用源码/后端测试范围与c13f逐项相同，只有桌面QA、固定探针入口及文档变化；native/web身份相同，`reuse-final-code-equivalence.json`保存完整差异。`desktop-reuse-v2`实际透明WebView2四组通过，240DPI/DPR2.5/Zoom1/foreground保持。`release-sdk-cli-reuse`真实HTTP/默认SDK/CLI五组通过，0render，未改正式工程。

`ai-reuse`在调用模型前的声学前置失败：暂停本测试源时，系统端点1760Hz（MIDI93标准频率）背景幅度0.00220，播放本源后0.00196，不能通过原3倍对比；保留原始报告/WAV，不视为AI编辑或当前引擎发声成功。系统端点混有其他音频，未停止别的应用或改系统音量。

新的通用测试源先在本源发声前测量一组整数周期标记频率及其倍频，选背景最弱的一对，再以固定原幅度构造SFZ采样标记。MIDI仍由真实模型将唯一音符93改105，实际频率必须翻倍；标记本身不宣称为等温律A6。3倍背景/绝对幅度及旧频消失阈值保持，并增加回环帧/时间戳完整性断言。所有候选背景幅度、选择依据和实际标记频率保存在报告，不通过提高音量、降低断言或覆盖失败来过关。需固定新QA快照后实测。

### c13f固定回归与工作台计时

c13f96a固定源码配套f056原生bundle，`full-reuse` **505通过、1权限跳过、0失败**，197.61秒。`ui-reuse`4K八组再次通过，Browser音频/PCM/旧plan/JS0。正式未部署，当前没有长测运行。

`metadata-v1/metadata-report.json`为7轨、6685音符、396秒E内MLFixture工程；音源目录明确指向独立E空库，不加载H采样器。20次全新renderer context/空HTTP缓存（服务和OS缓存暖）：轨道可见P95 **456.93ms**，点击最后轨并确认选中P95 **549.41ms**；最大交互644.85ms。native ready单列P951815.63ms，不把它算入/冒充元数据可交互，更不代表重型插件装载。实际API和原生设备，没有mock；0音乐修订、0render、Browser音频/PCM/JS0。

实际桌面`desktop-reuse`第一轮停在探针错误：过早保存原曲monitor的ID，后续重建独奏查询不存在ID导致TypeError，产品页面error数组为空。新探针等待arrangement且DOM所有轨道在当前monitor中，从目标DOM读取稳定ID，并在过渡状态继续有界等待；不按某个乐器名或索引假定后端字典顺序。原失败保留，须新候选配套重跑实际桌面。

固定源码探针入口新增可选`--probe ai`，默认acceptance兼容，实际导入Service包、源码清单、脚本SHA和native身份照常验证，仅资源ROOT指向原库；用于最终真实Codex复核。正式试听队列只读为1250 ready/2既有failed/0queued，key中的固定离线宿主和policy未因本次engine控制改变；后续部署不重置现有缓存或失败项。

### 同一完整执行清单的重开修复

控制线程现在只在完整JSON执行清单逐内容相同、图没有错误且无failed节点时复用同一GraphSession，既支持已就绪，也支持初始装载中的重复请求。重新打开仍应用请求的位置/循环、推进generation和转移owner，保留监听值及错误计数；不重建已验证插件、不重读采样库。完整清单含工程/修订/时钟/程序/资源/路由等，不能仅凭一个相同graph_hash复用。内容改变、失败图和不同工程仍按原路径准备；旧窗口close不能关闭新owner的图，非法位置也不改变现有播放状态。

原版两项真实独立工程均复现同图open换PID，`identical-reopen-before`保留。修后新原生bundle **f0563ad22e55a09049379daea6b6a61e74a8d54c22e4fea56d68e92166fbad6d**，只改变engine控制；worker/runner/loopback SHA与ce4876相同。播放/自然结束重开、保持监听、重新定位后真实输出、非法定位回滚、旧owner关闭隔离、构造器实际门控期间重开、相同伪造hash但不同工程，以及正常/卡死双worker关闭6项通过，`identical-reopen-six.xml/log`。同组首次相关60项中59过，唯一新断言错将定位后的generation与定位前比较，修正比较基线后专项全过；原日志保留，不放宽generation隔离。

关闭回归曾只看到一个共享appendText标记。跨进程共享文件追加存在竞争，当前独立DynamicFixture用每实例独立标记，并保留每个真实worker进程句柄核对退出码：正常均0且两份释放证据，卡5秒均1且4秒内退出，不再以PID立即消失代替真实退出。测试构造器门控只读取E内临时文件、最多5秒；不修改原Fixture、旧宿主、旧静态库或固定音源验证。

`identical-reopen-protection.json`正式九表/head及14pin相同，Ruff/独立构建通过。完整新固定快照、界面交互计时、配套前端/实际桌面与最新整曲/30分钟仍待验证，不部署旧bundle或把v7两段通过改成整批成功。

### v7结果与同图重开

v7现已终态失败，所有自有进程退出；旧locator仅指向原报告。参考305秒与当前398.64秒（含尾音）完整通过，源时钟误差0、回环gap/discontinuity/错误0、所有六插件missed0、设备underrun/xrun0。失败不是这两段音频丢块：A/B后六个PID16012/47080/41952/50112/26132/41260，在`play_phase`再次open同一manifest时换成49900/5480/40424/16536/25576/36148。整曲结束后严格PID断言触发，因此30分钟没有开始。

这暴露了完整路由图对同一编排open仍整图重建的通用规则缺口，会额外重读H采样库。后续修复必须在完整清单一致、图健康时复用现有节点，同时保留定位/循环/监听和owner/generation隔离；不能删PID断言、仅换参考PID或跳过同图重开来把失败写成通过。v7固定文件与14pin不改，前后正式九表/head14pin一致。

另补齐计划中轻量工作台交互的20次P95实测入口：`prepare_ui --track-count 7 --notes-per-track 955 --duration-seconds 396`，`metadata.py`配套实际Edge测试。每次全新renderer/HTTP缓存，等7轨可见且点击最后一轨已生效，插件ready单列；真实API和新引擎，不mock。E内独立MLFixture工程和空音源目录，避免加载H目录；不播放/编辑正式音乐。当前只有Python/Ruff/JS静态检查，实际测试待串行运行，不能先称P95合格。

### 当前唯一长测v7（启动时记录，现已结束）

2bc6e4a固定源码按完整后端范围（service+engine+release）重跑，`full-io-v2` **501通过、1权限跳过、0失败**，224.44秒；前面的环境收集失败及旧测试失败原日志保留，不覆盖。候选`runtime/engine-release.io-aware-v2.local.json`仍未激活，原生保持ce4876，产品源码/web/native与03f325d相同，仅QA/文档与退出断言更新。

**当前唯一长测`endurance-v7`于2026-10-22 18:59:34 UTC启动**。复用维护用`instrument_probe_budget.loading_budget('H:')`先连续三次只读性能计数：busy0/1/1%、queue0，均空闲，证据`endurance-v7-storage.json`。定位文件`runtime/engine-acceptance-job.local.json`，源码/导入位置/脚本摘要在`endurance-v7-code.json`。实际Python47212（启动wrapper7556），已观察`reference305-full`实际ready/playing、设备欠载/xrun0；后续采样器、整曲及30分钟尚未完成，不能先称通过。开始前正式九表/head和14pin一致。

不修改本run固定源码/bundle/原始报告，不再启动第二批；所有重负载验证串行。H负载会变化，启动时空闲不保证整段无竞争；若失败继续保留实际诊断，不将加载超时写成音色损坏或放宽期限。正式部署、桌面最终固定文件安装和收尾仍待完整长测通过。

### H盘负载与当前验收（2026-10-22）

用户明确H盘读写能力差、经常满载。只读性能计数曾看到H当前排队20个请求，后续回落至2和0；这说明存在明显竞争，但不足以证明所有超时的原因。库继续原位，避免额外并行冷加载、重复扫库/校验和擅自搬盘；不停止另一项已授权的音源验证。当前不修改插件、样本、固定验证器或音频期限来掩盖加载问题。

固定候选03f325d完整后端`full-portable` **501通过、1权限跳过、0失败**（189.08秒）；`ui-staged-final`8组、`desktop-staged-final`实际桌面4组（DPR2.5、foreground保持）通过，浏览器音频/PCM/JS0。`release-sdk-cli-default`5组包括实际默认SDK/CLI读取独立配置端口；`ai-20261022`真实Codex编辑、applied修订及回环变化通过，0render。当前设备U27G4R软件回环20次P95：播放57.15ms、定位60.16ms、音量58.16ms/静音58.02ms/独奏57.20ms；后三项仍未达到初始50ms目标，保留失败统计，不改标准。实际设备恢复独立探针通过。

`endurance-v6`使用03f325d固定源码和ce4876原生bundle，**初始加载失败、0个播放阶段**。等待130秒仍未就绪，Keyscape/Pianoteq加载超时；不是整曲或30分钟通过。监督/引擎已结束，`runtime/engine-acceptance-job.local.json`仅定位这个终态失败，不按旧PID说运行。`before.json/after.json`九表/head与14pin均相同，原始报告保留。

后续验收顺序调整为参考305秒→一次采样器冷开→20次定位播放/3次同工程A/B→当前整曲→30分钟循环。当前整曲和循环之间核对worker PID不变，减少一次不必要的H采样库重载；音频/时钟/迟到/欠载/身份/预算检查逐项保留。独立`reopen-final-v7`三次真实冷开仍是分开的生命周期证据。此顺序变更仅用于下一独立run，不编辑v1–v6固定文件或报告；尚未开始的新run不能称通过。

前端/桌面夹具JSON显式UTF-8，防止系统默认GBK误解中文路径；固定发布验收入口校验源码清单、实际导入包位置、native bundle与代码摘要，只将资源路径指向原仓库，不把运行时代码切回可变工作树。QA代码静态检查通过；正式仍未部署，整体目标active。

f4bb46c将上述QA顺序和固定发布入口存档。应用src/services/native/web与03f325d逐项manifest相同，native/web SHA相同，证据`io-aware-code-equivalence.json`；每段播放检查体AST逐项相同，证据`phase-check-equivalence.json`。首次误将模型测试也交给app环境，因该隔离环境不含mido/torch而收集失败，原`full-io-aware`日志保留，不安装/调用模型。随后服务套件453通过/1权限跳过/1失败：Kontakt进程树测试的wait已返回，但测试错误地只接受NoSuchProcess异常。Windows退出对象尚可查询不是活进程；现接受成功wait或PID已消失，活进程仍因3秒TimeoutExpired失败，不改runner/JobObject或固定验证器。四项`child-termination`回归通过。新固定快照还需完整后端范围（service+engine+release）重跑，原失败不覆盖。

### 最新装载收口

8be052a固定源码最终 `full-final-short` **500通过、1符号链接权限跳过、0失败**，187.45秒。先前一次为新Ample标签测试使用相对运行队列路径，一次为较长basetemp触及Windows路径上限；保持固定源码、在项目运行目录使用短临时根后整套通过，未修改固定验证器或放宽断言。

实际HTTP验收前发现Windows当前TCP保留范围包含8761–8860，8765/8794/8796/8797实际bind均返回10013；18765/18794/18796/18797实测可绑定。未改系统保留范围、驱动或防火墙。测试改用18794/18796/18797，正式计划激活时仅将本项目端口改18765。SDK/CLI默认地址现共用本机配置，显式URL优先；7项HTTP/默认与显式端口回归通过。新增UI/桌面探针对固定发布源服务，校验夹具web/native与候选一致、JS验收脚本SHA一致；新源码还需完整回归后发布。

`reopen-final-v7` 三轮真实七轨冷开/播放/关闭全部成功，固定bundle `ce4876de9e3aade76f43be1c6431208228d8c1e1249ff468df8f4e3a5ed0b806`。冷开17157/15437/15172ms，关闭1968/782/1047ms；每轮六个worker退出码均0，无强制回收、欠载/xrun/迟到。窗口内未进行线程抓取。这是三次操作证据，不当作20次P95或30分钟通过。

最终关闭窗口为全图共用的3秒绝对期限，各节点同时收到退出通知，不逐个累积等待。两节点正常析构均留下实际释放标记；两节点各卡5秒时仍在4秒内全部退出，2专项通过。`close-deadline-regression`53项通过。旧750ms在实际约2秒的正常释放前会触发回收，因此已替换。

固定JUCE的SHA256(InputStream)逐64字节读取；新worker用64KiB有界缓冲进行**相同完整SHA校验**，不改变旧宿主/静态库。真实Surge读取操作从约33万降至约5千，其他插件同样减少；这不是隔离磁盘条件的性能P95。参数恢复原本对每个覆盖值重新遍历数千个参数ID，现一次建索引，保留全部值/未知ID拒绝与状态恢复；`parameter-index-regression`26项通过。两项优化均为通用规则，不按曲名或轨道名特殊处理。

`reopen-buffered-v6`前两轮通过、第三轮Keyscape超时，原报告保留。`reopen-stack-v4`首次加载后探针误用底层close命令再复用已关闭管道，后续Errno22是探针错误；已改Broker完整close并重新配置。其线程暂停诊断会扰动耗时，118秒不作为正常加载性能。正式服务与发布指针依然未变，下一步对当前提交做完整回归，再重跑完整长测。

最新进度：`endurance-v5` 已失败终止。两首整曲均通过（时钟误差、回环丢帧/不连续、原生欠载及声部迟到均0），但回到重建图时 Pianoteq 9.1.2 在装载阶段超时，1800秒段未开始。随后独立冷开又在 Keyscape 装载复现，不能将其归为同一个已确认插件缺陷或把两首通过当作全轮通过。

后续修正仅在新引擎/worker：节点先正常退出并释放资源，750ms后才强制回收；全图先同时通知各节点，避免串行关闭延迟。增加加载阶段与实际进程读取/CPU诊断。已验证描述可避免重复VST3类扫描，仍检查路径/标识/版本、实际DLL SHA、实际装载描述与完整状态；旧程序无描述时保持扫描。五类伪造描述/版本/二进制/状态专项均拒绝，未修改固定离线宿主或库。

加载等待与播放DSP分开：原45秒无进展失败保留；仅实际子进程读取或CPU前进可继续，45秒后超过5秒无进展失败，所有情况120秒硬上限。准备线程仍可取消，播放块期限/2块/256MiB、发声及入库身份标准不变。`LoadBudgetProbe`验证闲置、真实进展、假进展及绝对上限边界。`load-budget-regression`59项通过，51.59秒；初次析构夹具误将reset中的releaseResources当关闭，已改真实析构，原失败保留。

真实重载仍在定位，`reopen-grace-v1`、`cached-discovery-smoke`、`reopen-resource-progress-v2`保留失败；`reopen-stack-v3`因诊断工具枚举目标符号失败退出，不作为产品失败或通过。只对独立自有worker使用有界线程栈探针，不写库、不向进程注入代码、不修改插件。符号为模块导出附近名称，不能据此断言私有函数语义。当前 `reopen-stack-v4`尚未终态；不开始新的长测或正式部署。

用户撤销暂停后恢复原目标。固定源码 `cec23c4`（包含 `a257a5b` 消费期限修复）的完整回归为 **451通过、1跳过、0失败**，193.22秒，证据 `n7/full-resume.log/xml`。跳过为Kontakt目录符号链接测试缺少创建权限，未放宽产品路径检查。该快照含另一项工作已经提交的Kontakt代码，未收入其未提交Ample文件或构建产物。

新的唯一长测为 `n7/endurance-v5`，固定原生bundle `6381ced35b93da581e1a587d297997bbadf6195dced40cad5e0c51c88b69cec6`；`runtime/engine-acceptance-job.local.json`保存本次进程及目录。依次执行两首整曲及1800秒循环，结果未终态前不得宣称通过。v1–v4失败证据保持原样；正式服务和发布指针仍未变更。

恢复前正式九表/head和14个固定文件SHA均与N1保护基线相同，见 `n7/resume-20261022-before.json`。本次候选描述为 `runtime/engine-release.resume-20261022.local.json`，只是暂存，尚未激活。

2026-10-21。**进行中，尚未部署正式默认；整体目标保持 active。** N0–N6阶段已完成，见[计划](../plans/native-daw-migration.md)。此文随最终实测补齐，不将构建或短测当作整曲验收。

## 当前实现与修复

设备意外停止时立即回读 stopped/playing=false，并使声音版本失效；主动拆卸回调用单独的 expectedStop 区分，预览或切换图不会误清正常 transport。重新打开由既有控制层配置设备，旧播放状态不能冒充实际输出。

完整图的首个 DSP 块使用后台准备期限，不用播放中的短期限判其迟到。部分采样器在状态装载结束后还会首次初始化处理器；原先实测出现空首块和空 applied_revision。准备仍限时，设备回调不等待插件；只有实际完成的音频和非空版本才能报告 ready。新增独立动态夹具以75ms首次DSP延迟重现并验证，实时捕获和严格导出PCM一致。

实际整曲第一轮在约190秒记录Keyscape迟到块，设备underrun/xrun为0，原失败保留。新增线程局部Windows MMCSS Pro Audio调度，分别用于后台图生产器和插件处理线程，不改变系统默认输出、主音量、驱动或其他进程。重新开始完整验收，不能把旧失败报告改成成功。

`Loopback.cpp`保留短WAV捕获并增加有界 `--meter` JSONL模式，长测不积累整曲PCM。每窗记录有限性、峰值、RMS、设备帧连续性和QPC时间戳。新的脚本位于 `scripts/probes/engine/{acceptance,latency,ai,desktop}.py`；原N2固定宿主/报告不修改。

## 已完成实测

- `n7/device-recovery-final.json`：真实WASAPI输出、模拟停止回调、实际不存在设备拒绝及重新连接恢复通过；Pro Audio线程注册成功。不是实际拔插硬件试验，也没有切换系统默认设备。
- `n7/graph-regression.log`：8项完整图回归通过，包含新增的75ms首次DSP初始化。
- `n7/ai-v3/report.json`：真实Codex将独立sfizz工程唯一音符从MIDI93提高至105，事务提交、新声音版本、持续播放和设备回环频率变化通过，0整曲渲染任务。先前两轮频率探针未通过，原报告保留；最后使用采样循环周期精确的测试音，并保存暂停时端点背景基线，避免把混合端点上的其他声音当作本轨。
- `n7/native-ui-v2/native-window.json/png`：真实WinForms/WebView2窗口的播放、现场独奏、原曲/重建切换、暂停4组通过，0新增渲染任务；240DPI、DPR2.5、Zoom1、3510×1965捕获。窗口透明且不激活，foreground_unchanged=true。第一轮探针错误地用页面已移除的URL令牌覆盖cookie，已修正探针，产品认证未改。
- TypeScript、原生transport单元和本次Python Ruff通过。最终完整后端正在重跑，结果尚不在此宣称通过。

## 回环响应

`n7/latency-full-v1/report.json`每项20次，实际设备为LG TV SSCR2 / NVIDIA High Definition Audio，48kHz、480帧。输入为小音量不同频率标记，按命令提交QPC与回环标记跨阈值时间比较，5ms分析窗；所有时间戳错误、丢帧和不连续计数均0。测量是Windows软件端点回环，不能当作扬声器/麦克风声学延迟。

| 操作 | 中位数ms | P95 ms | 初始目标 |
| --- | ---: | ---: | --- |
| 新文件句柄/原生图冷开并播放 | 58.04 | 60.95 | ≤1000，通过；OS磁盘缓存未主动清空 |
| 已就绪播放 | 52.99 | 57.95 | ≤150，通过 |
| 定位至不同频率标记 | 54.77 | 60.87 | ≤250，通过 |
| 音量 | 55.98 | 57.84 | ≤50，**未达到** |
| 静音 | 53.61 | 57.56 | ≤50，**未达到** |
| 独奏 | 53.59 | 57.17 | ≤50，**未达到** |

256帧请求仍被当前设备协商为480帧；实际尝试低延迟共享模式也仍为480帧，未改善这一结果，正式默认保持普通WASAPI共享。没有通过放宽断言把50ms写成通过，也没有修改用户系统音频设置。控制回执与实际回环时间分开保存；数毫秒回执不是首声证据。

时间戳依据：[IAudioCaptureClient::GetBuffer](https://learn.microsoft.com/en-us/windows/win32/api/audioclient/nf-audioclient-iaudiocaptureclient-getbuffer)的QPC单位为100ns；错误标记按[官方定义](https://learn.microsoft.com/en-us/windows/win32/api/audioclient/ne-audioclient-_audclnt_bufferflags)保留。低延迟共享行为参考[IAudioClient3](https://learn.microsoft.com/en-us/windows/win32/api/audioclient/nn-audioclient-iaudioclient3)与仓库固定JUCE实现。

## 后续修复与阶段复核

复杂路由原曲/重建切换现在保留已加载的整个编曲图；缓冲计入同一256MiB预算，保留程序路径供租约回收判断。返回时更新到最新修订并定位，不重建插件；关闭、删工程、换设备或换工程统一释放。`n7/switch-smoke-v1`在实际7轨/6插件工程连续三次切换，6个PID全程不变，重建就绪约46–47ms；`endurance-v3`重跑同一检查约67ms。测试另外覆盖在原曲视图修改MIDI后返回，应用新声音版本且保留worker。

`native_audio_bundle`本机配置可固定已发布不可变bundle；其完整manifest一并保存，设备与显式混音导出使用同一配置。不因开发构建而在下次启动偷偷换worker，也可恢复到已验版本；不复制或回退数据库。设备 stopped 后维护器先退出自有进程树，再释放派生文件/租约，防止仍持文件的worker与清理交错。

- `warm-regression`52项通过，含复杂图切换、原曲中编辑、生命周期和原生内核。
- `release-regression`23项通过，含发布bundle重开、独立混音导出、路由与生命周期。
- `device-cleanup-regression`13项通过；Ruff通过。
- 第一轮完整后端374过/5败：一项冻结任务遇到源码变动，正确返回implementation_changed；另四项属于并发新增的未提交音源入库代码，其解析器也在运行中被修改。原日志保留，未放宽指纹。最终将用固定源码快照重跑当前已提交范围，不混入另一项工作尚在编辑的测试。

第二轮整曲中396秒本曲原生输出和回环均通过；305秒本地FLAC原生欠载/xrun为0，但回环采集器在并发负载中记录6次不连续/11040丢帧，此轮**未通过**。采集器改用线程局部MMCSS及1秒有界采集缓冲（不增加输出延迟），QPC仍来自原始包，不放宽检查。`endurance-v3`重新从头执行，不能把第二轮的部分通过当作最终结果。

第三轮回环采集连续，但约160秒仍记录插件迟到。后续修正生产器满队列的Sleep轮询为消费后的WaitOnAddress/WakeByAddressSingle唤醒；worker空闲消息改为非睡眠的有界待处理消息泵，避免将Windows定时睡眠混进下一块DSP期限。设备回调只发唤醒，不等待或取锁。补充20次空闲唤醒的真实节点PCM/进程及响应回归；当前 `event-wakeup-regression-v2`42项通过。其首轮另外暴露了新资源已装载但成功音频仍属旧版本时误报ready，已要求publishedRevision与desiredRevision严格相同；原失败保留。

固定源码快照 `814c954` 完整回归373过/1败，唯一失败为动态夹具测试临时追加的插件白名单路径没有resolve，目录联接下被正确拒绝；只修测试规范化，不放宽产品白名单。新修复提交后继续完整快照回归与最新长测。

新增根目录 `repair-audio.cmd` / `scripts/app.py repair-audio`，只调用既有SDK engine操作，退出自有音频进程并按原工程/修订/视图/源位置/循环/监听重开，保持暂停。0新增操作/音乐修订/渲染；真实HTTP SDK恢复与空工程重复恢复2项通过。正常启动先校验发布bundle，缺失或SHA异常明确失败；不静默换版本或转回浏览器供声。

## 整曲、发布及保护

`runtime/engine-acceptance-job.local.json`定位当前唯一N7长测；第三轮 `n7/endurance-v3` 使用不可变bundle，当前396秒真实工程、305秒本地FLAC、30分钟循环依次执行，期间现场调音量/静音/独奏，不通过定位或建图清空计数。正式数据库用SQLite mode=ro读取；程序/状态和必要本地音频硬链接仅放本次独立根，H库只读，NAS无访问。原始音频不传浏览器。

Pianoteq既有批次实际status.json已回读completed、verified763、failures0，相关原监督PID已退出；旧locator的starting不作为当前状态。没有修改或重新启动该批次。正式九表/head与14个固定摘要持续用N1基线核对；发布/清理前后还需再次核对。

尚需：完整长测及当前后端失败排查、最终UI回归、正式默认切换、恢复入口与本次临时产物收尾。正式服务仍旧前端，不据此邀请用户验收或结束目标。

## 最新固定候选

`eba903a`源码快照完整后端377项通过，187.77秒；追加发布入口四项单元通过。`ui-accepted`八组4K流程通过，浏览器音频/PCM/JS均0；`native-ui-final-v2`实际桌面四组通过。透明窗口首轮暂停探针在切换中的禁用按钮上直接click被忽略，已像浏览器驱动一样等待按钮可用再操作，产品按钮规则未改。`ai-accepted`再次完成真实Codex编辑及回环频率验证，0render。

当前唯一长测为 `endurance-v4`，固定bundle `742667abc6d0207538662c390084dfd8c62cef151d6b82a90e90f6a8918cbf4c`，不要改其EXE/worker/采集器或原始报告。前三轮均已终止并保留失败。第四轮增加停止后稳定的源位置/设备消费帧有理数核对，要求误差≤1源样本；进行中不能宣称最终通过。

已清理本轮结束的独立测试文件7718个、目录5792个，逻辑路径字节约2741MiB，但含大量硬链接；E空闲实测增加约0.913GiB，不能把逻辑大小当释放量。两个reparse夹具及其8个父目录保留，没有跟随联接或改属性；无策略拒绝后换工具。清理前后正式九表/head及14保护SHA均一致。源码快照、当前长测、正式素材、H库、NAS、旧N2固定run和历史拒绝目录不动。

发布采用 `runtime/releases/<Git commit>` 独立源码副本、`runtime/web-releases/<内容SHA>`前端和不可变原生bundle，启动器校验源码清单后进入发布副本。开发目录与其他并发工作保持原样；正式数据仍为原SQLite/CAS，不复制成第二份工程库。运行资源仅用明确runtime/plugins联接；源码清单不递归这些联接。此处只是候选构建，尚未更改正式指针或重启8765。

## 消费期限修正

第四轮两首整曲分别通过，时钟误差与回环丢帧均0；其30分钟段在约543秒记录一个声部3个迟到块，设备underrun/xrun仍0，原报告保留为失败。原因是节点期限固定为一个源量子时长的80%，没有利用设备尚未消费的输出块，音频仍来得及输出时就被提前清零。

现按设备实际消费QPC、输出序列及采样率计算整块绝对期限，同一期限覆盖全部拓扑层和重采样请求，保留1ms收尾余量；准备阶段保持原有限时规则。**输出仍2块、总音频预算仍256MiB，没有增加缓存或抹去迟到计数。** 首次及暂停替换准备两块后才ready。实际超期仍清该路径并计数，设备欠载独立记录；新增每节点最大DSP/往返时间便于区分计算与调度。

独立夹具在第20块实际睡眠12ms，节点最大DSP耗时确认≥12000µs，真实设备输出仍无声部迟到/欠载，旧固定期限会提前拒绝此块；另验证装载中切原曲再返回，不保留已取消的图。`deadline-metrics-regression`50项通过（含发布选择器6项）；最新完整快照与长测仍需完成。

主页不再请求旧playback.get来核对音频缓存；直接由音乐内容决定是否提供重建入口，实际设备/音源状态仍由native engine报告。`ui-no-legacy-plan`八组通过，音频/PCM/旧播放计划请求及JS错误均0。

发布构建器已实际准备515份Git源码、3份web文件与固定bundle；独立已发布源码服务的HTTP/SDK/CLI四组通过，工程修订保持、0render。正式指针仍未激活。新增发布校验/激活脚本，只有应用停止后才写指针和配置，使用同一启动锁和中断标记；不降级或复制数据库。模型、音源及原始音乐路径经配置继续指向原位置，不搬资源。最终需要用本次新引擎重新构建候选，不部署早期742667候选。

早期各阶段已结束pytest夹具另外清理20745文件/18227目录，E空闲实增约2.370GiB；9个reparse夹具及36父目录保留。小型日志/JUnit/原始实测报告保留，旧N2固定run不动；清理前后正式九表/head+14pin一致。
