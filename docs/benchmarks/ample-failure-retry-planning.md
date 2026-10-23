# Ample 失败归因与重试计划

2026-10-23。此文记录固定原批次的已知失败类别和下一轮重试门槛。原队列、固定 pin、原报告与正在运行的 chain 都保留；本次只改可供后续独立队列使用的源码和 E 盘规划逻辑，没有启动 Ample/VST3 探针，也没有写正式工程、正式音源库或 NAS。

## 已观察到的失败类别

原 AGLP 35 项队列已终态，记录为 17 通过、18 失败。现有失败报告可归为三类：8 项使用普通长音音符测试 Riffer 时静音或削波；3 项完整参数读回时 `Pedal-HM2-Dist` 请求值为 10、观察值为 0；7 项没有探针结果，日志共同指向嵌套 pin 运行时把仓库根误判为 pin 目录，因输出路径保护拒绝写入。`November Rain` 的多次尝试仍以最后一次结果归类，旧尝试历史保留。

AGM 当前批次的最后已知快照为 33 项中 14 通过、15 失败、2 待 staging、1 I/O deferred、1 因缺少 `AGMPk` 采样组而 blocked。失败中 12 项是普通长音测试下 Riffer 静音或削波，3 项在共享宿主的插件扫描阶段以 `crashed` 结束，诊断共同显示 `Cannot write JSON`。缺少 `AGMPk` 的 `Planet Caravan` 继续保持 blocked，不替换 DBName、不伪造发声。AGSC 原队列准备了 45 项；只有完整终态后才进入失败汇总。

静态的 `riff-profile-analysis-20261022-a/profiles.json` 覆盖三款产品的 113 个工厂状态，每个都含一个 Riff 节点。AGLP 与 AGSC 节点还区分 `Riff`、`ARP`、`Fill`、`Strum`，而共享 profile 校验器此前只接受 `Riff` 并把返回类型硬写为 `Riff`，会拒绝这些原厂类型或丢失类型信息。校验器现在接受这四个已观察类型并保留原值。工厂节点能提供源 SHA、类型、节奏和拍号；它本身没有证明正确 MIDI 触发键。针对 `8/4Tri` 这样的拍号表达，现有简单整数解析也会拒绝，因此不能自动转成普通 `8/4`。

## 共享规则修正

- Ample runner 和探针在嵌套 pin 中统一通过 `MUSIC_LEARNING_REPO_ROOT` 或向上查找仓库标记定位项目根。未来创建的 pin 会包含这个 resolver；活动固定批次的 pin 不变。
- 新 probe 收到 event profile 时，先调用公共严格校验器，并核对 profile 的工厂 SHA 与该队列项的 `factory_sha256` 一致，再构造 trigger/transport 音符。参数读回、真实首次与重开发声门槛没有放宽。
- `riff_toggle` 公共 profile 按源保留 `Riff`、`ARP`、`Fill` 或 `Strum`；类型也进入 profile 指纹和无名称试听说明。
- 新的 `prepare_ample_failure_retry.py` 只在 chain checkpoint 已完成、LP/所有子队列按 item 状态完整终态、固定 ID/SHA 匹配、无暂停请求、无活动 Ample 进程时，才能生成 E 盘失败清单。生成前后重新核对来源 SHA；只收集失败项，保留源队列、源 item、原 attempts 的摘要，不改原文件。
- 规划器不会生成可运行 queue，也不会猜测 trigger note。每个失败项都需要 source-pinned profile 与实际首次/重开发声证据后，才能进入独立重试队列。宿主 scan crash、参数读回和普通音符/Riffer 的问题继续按共享规则归类，不做逐预设忽略或“通过”覆盖。

## 验证与待办

通过仓库测试入口运行 `tests/test_ample_runtime_paths.py`、`tests/test_ample_retry_planning.py` 与 `tests/test_instrument_performance_profiles.py`：25 项通过。测试覆盖 pin 根回退、失败类别分组、终态门槛、进程门槛、输出路径隔离，以及四种 Riffer 类型的保真；没有调用音源模型或 VST3。

下一步必须等原 LP/AGM/AGSC 批次全部终态，再运行 E-only 规划器。其结果仍需先补齐经实际验证的 Riffer 触发 profile、共享 HM2 参数恢复序列与 scan 崩溃修复证据，之后才建立新 pin 和独立失败队列。已通过项和原报告不重测、不覆盖；失败重试不得降低非静音、完整参数、插件/工厂身份、首次/重开音频要求。

## 2026-10-23 原批终态与共享修复候选

原 chain 已于 2026-10-23 02:14 UTC 进入 `completed`，三条固定队列合计 67 通过、45 失败、1 缺素材：AGLP 17/18、AGM 15/17/1 个 `AGMPk` 缺组、AGSC 35/10。45 个失败身份的原队列、报告、attempt 与通过项均保留在各自源目录。`Planet Caravan` 仍为 `blocked_missing_sample_group`；不替换 DBName、不借其他鼓组/吉他采样冒充。

所有 45 个失败工厂状态都有唯一 `<Riff>`，现在可按官方 Riffer 控制语义生成 source-pinned profile：官方 [Guitar Riffer Panel Manual](https://www.amplesound.net/en/Riffer_Panel_Manual.pdf) 说明 A#6 为 Riff 播放开关，高 velocity 开启、低 velocity 关闭；代码以 MIDI 1.0 的 A4=69 推导 A#6=94，并使用 MIDI velocity 127/1 两端值。每个 profile 都绑定该预设完整 factory XML SHA、Riff 类型、节奏/拍号与小节数，映射来源/原拍号/量化也写入 evidence；未知产品、Riff 类型、拍号后缀及工厂 SHA 变化均拒绝。AGM 的 17 个 profile 原先因官方 `8/4Tri` 格式未被识别；新解析保留该原字符串与 `Tri` 三连音限定，同时将 `8/4` 作为宿主拍号。不能把该限定静默丢掉。profile 目前是生成结果，仍须新独立宿主完成首次/重开真实发声才能用于入库。

新增 `ample_retry_queue.py` 会重新读取终态 checkpoint、三款产品队列和原始 113 项队列，要求所有源队列哈希/身份/状态计数不变，并验证 retry plan 恰好覆盖全部 45 个原始失败身份；只构造失败项的新队列，所有 67 个已通过身份均不重测。`prepare_ample_failure_retry.py` 将新队列、状态与固定 source/runtime pin 写到新的 intake run，不启动插件。

共享 runner 另有两类已做代码级修复：每条尝试的原生调用记录改用较短的 run-level `native-calls` 根，避免深层路径下 JUCE 写 response JSON 失败；workspace resolver 纳入新的 pin 文件，让嵌套运行目录不会被误当仓库根。原报告中 4 条扫描/写回崩溃和 7 条 runner/output-root 失败仍保留，实际新宿主验证尚未进行。

HM2 参数诊断在独立目录 `data/maintenance/instrument-intake/20261023-ample-shared-fix/hm2-phase-agsc`。原始 AGSC 工厂 XML 的 472 个参数与宿主当前状态 473 个参数相比，只有 `Pedal-HM2-Dist` 与 `Pedal-HM2-Level` 两个值不一致；在该独立探针中，`setPreset` 后读回已经为 0，`prepareToPlay` 后仍为 0，等待并再次加载同一完整 preset 后也仍为 0。因此“prepare 后异步重置”没有得到证据支持。`ample_presets.prepare` 现在在 factory 参数仍由源状态决定的前提下，从当前插件状态按参数名复用 native 参数 `Index`；静态新 `.vstpreset` 已包含全部 472 个索引且保留 HM2 源值，但这是共享 state-serialization 修复候选，尚未用真实首次/重开发声和完整参数回读验收，不应标成已修好。独立证据目录为 `data/maintenance/instrument-intake/20261023-ample-shared-fix/state-serialization-indexed-v1`。4 条 HM2/参数失败还不能通过。

回归命令经 `scripts/maintenance/run-tests.ps1` 运行：本轮 Ample mapping、retry queue、参数准备、路径隔离、原计划/runner以及通用 performance-profile/publication专项共 71 项通过，Ruff 与 `git diff --check` 通过。**本轮未运行 VST3 扫描、采样加载或真实声音重试**：H 盘预算先后实测 busy 100%/队列14、busy 88%/队列1，故等待空闲窗口；这不是音源验证失败。已构建并 pin 独立 probe host `d922eb9e89ba745f757aecf2b849c3d085a32f43456edfeae81a6bffa8587a0b`，准备了 45 项 failed-only retry queue，当前状态 `prepared`，未启动。队列顺序先放 4 个共享参数读回失败，以便第一轮通过 `--max-items 1` 控制验证共享 state adapter；如果首个共享控制不通过，不盲目消耗其余采样加载。后续仍须在每项启动前通过 H 盘 budget gate，继续使用同一 source-pinned queue；身份、完整参数、首次与重开发声条件不变，原报告和 67 个原通过条目不能被覆盖。

## 2026-10-23 首个共享重试的适配器回归

H 盘预算随后通过三次观察（忙碌率 15%/15%/4%，队列 0/1/0），因此只启动新 retry run 的第一项 `AGLP Rock-Cm-120`，并由 runner 在项目开始前再次执行 H 盘门控。该项没有进入实际发声验证：runner 生成给 probe 的 manifest 时漏传了队列中的 `factory_sha256`，probe 因缺少工厂状态身份哈希而按严格规则拒绝 Riffer profile。该诊断不是静音、样本或参数验证结果，也没有产生首次/重开音频证据；run `failed-only-profiled-retry-1` 中该项的失败记录保留。

共享 runner 现在通过纯函数 `_probe_manifest_item` 把准确的 `factory_sha256` 与 profile/evidence 一并传入 manifest，并在哈希缺失或格式错误时于 probe 启动前拒绝。两项回归分别验证哈希被传递和缺哈希被拒绝；Ample 定向测试 26 项及 Ruff 通过。不要编辑该 run 的队列/原 attempt 来改写失败；修复需 pin 入新的独立 retry run，再从原始 45 个失败身份开始验证。实际首次/重开发声、完整参数读回和通过项发布仍未证实。
