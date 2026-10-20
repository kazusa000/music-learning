# Keyscape 接入

音源安装根目录为 `H:\music\Sound Library\Spectrasonics`，实际插件为 `VST3\Keyscape.vst3`，采样位于 `STEAM\Keyscape`。下载源 `H:\music\tmp` 保持原样；没有恢复已删除的旧音色库，也没有访问 NAS。

## 安装与分类

复制工具 `scripts/copy_keyscape_library.py` 流式复制并分别读取源和目标校验 SHA-256，记录源大小/mtime，结束后再次核对源属性。遇到不相同的现有目标拒绝覆盖。源包附加的 `.db.txt`、`.db2.txt`、`.mlt_key.txt`、`.tun.txt`、`.velcrv.txt` 在目标中还原后缀；`zmap.index.xml` 还原为 `zmap.index`，正常的 `User Settings Factory.xml` 保留。

本次原复制循环产生的清单仍保留原记录；随后增加同摘要的 `zmap.index` 目标。安装依赖清单使用正确索引名及目标文件实际 mtime。没有改变二进制容器内容或插件二进制。

`keyscape_library.py` 只读取容器目录、原厂预设索引及小型 `.prt_key` XML，不解码样本。455 个预设按原厂 Type、Model、SubCategory 和明确声源映射归入 14 类乐器。复合预设可归属多个乐器；同名、来源和奏法仍用稳定预设 ID 区分。

分类包括声学钢琴、电钢琴、Clavinet、玩具钢琴、键盘贝斯、钢片琴、羽管键琴、钟键琴、簧风琴、击弦键琴、音叉键琴、Dolceola、合成键盘和颤音琴。分类数量有交叉，不能把各类数量相加当作预设总数。

`factory-inventory.json` 是完整准备清单。`catalog.json` 只接纳通过实际发声与状态恢复的预设；未测的变体不冒充已就绪。

## 预设与原生宿主

`keyscape_presets.py` 固定 Keyscape 1.3.3d 及其 VST3 class ID，用插件回读的完整八声部 Multi 作为模板，只替换第一声部的 SynthEngine，导出标准 VST3 Comp chunk。不使用默认音色替代，也不修改样本编码或授权机制。保存后校验原厂预设身份和实际声源集合，兼容源 XML 的 OSC/RELEASE_IM 与回读的 MS_IM_0/1 表示。

这个插件关闭辅助输出总线时曾发生访问违规。此版本适配启用全部九组立体声输出，处理缓冲区按真实通道数分配，当前工程仍只混合主立体声输出；辅助输出不成为新的 DAW 路由功能。其他插件保持既有立体声及 sidechain 约束。

Keyscape 后台加载采样，状态加载返回并不代表可马上开始离线处理。没有等待的首轮测试中，多个预设几乎静音；Rhodes 的独立对照在保留采样等待后恢复非零音频。当前该版本离线准备在 `prepareToPlay` 后泵送消息 10 秒，不推进音乐时钟。它是当前版本实测的兼容等待，并非通用加载完成信号或速度承诺；捕获仍要求首次及重开探测都发声。此应用尚非常驻实时 Keyscape 宿主，首次准备大音色仍需等待后台任务。

## 大库依赖与持久化

77.5 GB 样本保持安装在 H 盘，不逐工程复制进 E 盘 CAS。完整状态、标准预设及小型不可变资源清单仍进入工程；描述文件额外保存 `installed_library` 和预设声源身份。

`installed_libraries.py` 在捕获、素材组装及制作渲染时核对清单摘要、库 ID、安全本地路径、文件大小和 mtime。mtime 未变复用安装时的完整 SHA 证据；mtime 变化则读取实际内容 SHA，并检查读取期间属性稳定。清单本身改动、缺文件、内容变化或播放器定位指向另一套库均拒绝。没有每次遍历读取 77.5 GB；这不是对恶意保留相同大小/时间戳篡改的文件监控。

渲染回写描述文件时保留上述依赖，不能被原生返回的插件描述覆盖丢失。备份包含工程状态及清单；外部安装的大库和插件需要另外保留，恢复环境须安装相同资源。既有冻结音频及历史输出仍可保留。

播放器用 Windows Common AppData 查找 `Spectrasonics\STEAM`。当前保留 `C:\ProgramData\Spectrasonics\STEAM` 指向 H 盘实际 STEAM 的目录联接；它是定位入口，不是另一份音色库。禁止递归删除它的父目录。插件和采样实际从 H 盘加载。

## 复测方法

使用新的证据目录，避免覆盖旧测试结果：

```powershell
data/envs/app/Scripts/python.exe scripts/index_keyscape.py `
  --library 'H:\music\Sound Library' `
  --bank 'H:\music\Sound Library\Spectrasonics' `
  --plugin 'H:\music\Sound Library\Spectrasonics\VST3\Keyscape.vst3' `
  --baseline data/benchmarks/keyscape-intake-v1/inspect-c-located.json `
  --copy-manifest data/benchmarks/keyscape-intake-v1/copy-manifest.json `
  --evidence data/benchmarks/keyscape-next-admission
```

默认验证各乐器代表及常用对照；`--preset '预设名'` 增补指定变体，`--all` 显式验证全部 455 个预设。工具保留此前已接纳条目和其他库；新条目只有音频、参数、声源身份恢复通过才原子发布。不可把准备清单直接复制成正式目录。

`verify_keyscape_workflow.py` 仅接受项目 `data/benchmarks` 下的新目录，使用独立数据库及端口 8768，通过真实 HTTP SDK 创建工程、捕获、换音色、渲染并重启回读；它不写正式歌曲。其短曲用于验证音源接入，不替代 P6 整曲听辨。

## 证据

被忽略的 `data/benchmarks/keyscape-intake-v1` 保存逐文件复制清单、原生请求、错误与修复对照、实际 WAV 及正式工程基线。REA 的只读二进制定位分析已导出 `rea-analysis.json` 并关闭会话：`ev_d97d35096f05908636858d877b9f7cea7c90a2bb3e01f8746e5f73e9e08661d1` 对应索引命名字符串，`ev_60fe276623b9401c4317c4e0a0f69412ebafea3276d746500ee70114b47bec30` 对应 STEAM 路径调用；Common AppData 路径来自 `SHGetFolderPathA(..., 0x23, ...)` 的后续只读反编译。没有通过 REA 修改插件。

首次不等待采样的 `keyscape-admission-v1` 有真实失败记录，不能作为最终通过数量。修复后的测试与最终入库数量见同目录后续验收记录。

## 本次最终验收

源 150 个文件共 77,528,440,122 字节；全部目标文件完成 SHA-256 比对，结束复核源大小和 mtime，0 变化。455 个原厂预设完成分类及标准预设准备，20 款通过真实发声和重开验证后发布。其余 435 款没有进入可选目录。

| 乐器 | 可选预设数 |
| --- | ---: |
| 声学钢琴 | 2 |
| 电钢琴 | 4 |
| Clavinet | 2 |
| 玩具钢琴 | 2 |
| 键盘贝斯 | 2 |
| 钢片琴、羽管键琴、钟键琴、簧风琴、击弦键琴、音叉键琴、Dolceola、合成键盘、颤音琴 | 各 1 |

复合 `Duo - Pianet Vibrabell` 同时出现在电钢琴和颤音琴下，因此表内总和有交叉。常用入口包括 LA Custom C7 Natural/Studio、Rhodes Classic、Wurlitzer 200A、Clavinet C、Celeste Dry、Rhodes Piano Bass Solid/Dark、Toy Piano Classic。增量接纳保留此前通过的收据，并为每类纯乐器维持一个默认预设；跨乐器复合音色不自动映射成单一乐器默认。

修复后的 `keyscape-admission-v2` 接纳 18 款；JD-800 有一次进程退出 0 但没有响应文件，未当作成功。本次没有确证原因。之后 `keyscape-admission-v3` 单独验证 JD-800 及 Toy Piano Classic，两者首次与重开发声、声源身份和参数均通过；合并后的目录为 20 款。没有自动重试消耗模型额度。

`keyscape-workflow-v1` 与 `keyscape-workflow-v2` 使用两个独立 HTTP 工程，分别以相反顺序在钢片琴与 Clavinet 间切换。捕获、统一编辑、新版本、实际制作渲染、资源回读及服务重启均通过；各自音符不变，音频 SHA 不同，外部库依赖在重新渲染后仍保留。第二轮使用最新 CAS 清单回读代码。它们没有出现在正式主页工程列表中，8768 实例已停止。

137 项完整后端回归通过，再新增的 1 项增量入库收据/默认选择策略测试通过，共 138 项不同后端测试；23 项原生回归、Ruff 和契约漂移检查通过。新增测试覆盖外部库内容变化、清单篡改、路径与联接、定位错误、正常触碰、CAS 清单回放、预设假改名、状态编码边界及渲染后依赖保留。没有前端代码变更，没有重跑音乐模型或 Codex 对话服务。

正式项目 `4012a10f50574b2395d4ec4a9d8f27b5` 的 head 仍为 `6d12b49aa72840429d79ce93294e1dc6`，快照 SHA `4591d13714931fce665e7f156a66a551487078233d2645c80b0167d19c8ceb5c`，9,378 音符及 15 条任务保持，未给正式音乐增加渲染。audition 的 desired 和 sequence 保持，重开应用只更新 observed 的报告时间。正式服务及原先打开的应用窗口已恢复。P6 整曲听辨结论没有改变。

C 盘 5 个旧程序文件共 92,984,640 字节已在 H 盘核对备份；删除均被 Windows ACL 拒绝，实际删除 0 文件、释放 0 字节。没有修改 ACL、接管所有权或提升权限。旧文件还在，但项目只允许并实际加载 H 盘的 Keyscape 路径。清理计划与实际结果在 `keyscape-intake-v1/c-plugin-cleanup*.json`；STEAM 定位联接保留。

最终源复核、当前可选目录、依赖清单、正式工程前后回读见 `keyscape-intake-v1/summary.json`、`live-catalog-final.json`、`formal-before.json` 与 `formal-after.json`。运行数据、原厂预设、样本、插件及 WAV 均不进入 Git。

## 自动增量验证

以后无需手填插件、模板、源复制清单等六个路径，直接使用已经准备的库存：

```powershell
# 自动处理完整库存，已通过且指纹/实际依赖/证据未变的直接复用
data/envs/app/Scripts/python.exe scripts/verify_keyscape.py

# 自动找到最近未完成队列并续跑；失败条目仅在明确要求时重试
data/envs/app/Scripts/python.exe scripts/verify_keyscape.py --resume
data/envs/app/Scripts/python.exe scripts/verify_keyscape.py --resume --retry-failed

# 也可每次只处理 10 款新音色，或单独指定预设
data/envs/app/Scripts/python.exe scripts/verify_keyscape.py --limit 10
data/envs/app/Scripts/python.exe scripts/verify_keyscape.py --preset 'Toy Piano - Classic'
```

`--limit` 后继续使用 `--resume`，不会重新开始。`--force` 对新队列强制重测，不能与续跑同时使用。普通退出、崩溃或中断后，已经完成的测试和音频仍在；恢复时核对原队列输入，依赖变化则要求建立新队列，不能把旧结果绑到新音色上。

共享的 `preset_verification.py` 负责队列、验证指纹、证据缓存与进度落盘；当前提供 Keyscape 一键入口，其他音源的适配器可复用同一流程。指纹覆盖宿主、验证实现、插件、实际预设资源、声源身份和样本清单。缓存命中前仍检查实际依赖和原始首次/重开音频、结果文件的 SHA；文件缺失或变化不能命中。旧目录的 `audio_verified` 标记本身不是缓存凭据，新流程第一次仍会产生完整证据。

同一库的旧准备器和新验证器共用操作系统文件锁，防止并发覆盖目录。每款失败单独保存，不阻止后续条目；批次最后原子合并通过项，保留其他库和此前已接纳条目。失败不会自动成为可选音色；命令遇到失败返回非零退出码。运行本身不调用音乐模型或 Codex，不修改正式工程。

进度、逐次调用报告及探测音频存放在 `H:\music\Sound Library\Spectrasonics\verification-runs`，缓存收据在同目录的 `verification-cache`，不会另占 E 盘大块空间。每个队列有 `queue.json`、`progress.json`、最新 `report.json` 和不可变 `invocations/*.json`；原生请求、音频与失败记录保留。

本机单款 Toy Piano Classic 实测：首次完整发声及重开验证本身 34.5 秒，同一队列复用缓存 0.203 秒，均不含命令启动时间。完整库首次仍需真实加载采样和重新打开，不能据此声称全库首次只需几秒；后续只重测新增、变化或丢失证据的项目。

自动流程专项的 144 项后端回归、Ruff 和契约漂移检查通过，包含缓存失效、证据丢失、分批续跑、失败显式重试、输入变更拒绝、库互斥和中断恢复。真实缓存复跑没有新建原生进程；正式歌曲的 head、快照 SHA、9,378 音符、15 任务与当前 20 可选音色保持。没有修改原生 DSP，未重复原生回归或整曲听辨，也没有自动启动剩余 435 款的长时间验证。

2026-10-20 用户随后明确授权剩余435款全量运行。独立监督任务见忽略的 `runtime/keyscape-full-job.local.json`，队列为 `verification-runs/20261020-160728-full-4ae3f058`。监督进程拥有验证器及全部原生子进程的 Windows Job Object，使用校验后的专属宿主副本；本批不重测已接纳20款。完成后自动导出失败 JSON/CSV、只读核对正式数据库，并只提交[全量报告](benchmarks/keyscape-full-verification.md)。Git分支或报告被用户修改时不覆盖，实际结果仍保留在任务目录。入库时发现单项跨乐器相同状态/音频只拒绝该项，不连带取消其他通过项。运行未结束前以实际队列计数为准。

原批最终完成 435/435：415 通过、20 失败，目录合计 435 款。失败全部来自九组输出总线名称包含未初始化内容，导致严格 UTF-8 解码失败；没有进程崩溃、缺响应或其他失败类型。宿主共享 BusInfo 读取现先清零结构，并给固定长度 UTF-16 名称保留结束符、避免截断代理对；不改插件文件、不容错解码、不降低发声/状态/声源身份要求。固定 JUCE 源码补丁可由 CMake 重复配置，并拒绝覆盖未知源码。新增原生回归覆盖缺结束符、Unicode、满长度及失败返回；两项 CTest、23 项真实原生及 19 项目录/队列/监督相关回归通过。

仅原 20 项进入独立 `20261020-bus-fix-retry-v1` 队列，使用新宿主 SHA `258cc9f1224b95405db6c64a59abcdae1c99ab8b89bb21738ceabc6a9b596e86`，旧批宿主和证据保留。任务定位 `runtime/keyscape-retry-job.local.json`；新基线只读记录，不动正式工程。重试完成前不将 20 项宣称可用或加入目录；实际收尾见 [重试报告](benchmarks/keyscape-failure-retry.md)。

最终重试 20/20 通过、失败0，455个原厂预设全部已验证并发布。正式 HTTP SDK 回读455个唯一条目，14乐器类，重试总线名称全部干净，正式工程 head/快照/20任务在本轮前后及收尾一致。原批全库外部变化记录保留。自动跟进在本轮收尾暂停。两份本次临时测试工作目录约117MiB的清理被自动审批拒绝，仍保留，未换工具重试；缓存依赖音频、完整状态及原失败证据保留，详见重试报告。
