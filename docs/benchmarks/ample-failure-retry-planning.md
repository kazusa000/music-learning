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
