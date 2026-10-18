# P0 技术探测汇总

2026-10-18 首轮历史记录。后续已完成授权、真实 NAS 曲目三模型与 Codex 探测，P0 当前技术结论见 [最新 P0b 报告](p0b-real-song-and-codex.md)。以下保留首次夹具测量与当时的缺口；P1～P6 未开始。

本轮实现提交：`a40deef`（模型环境/整曲桥接）、`2dda32e`（完整渲染与浏览器探测）。报告与规划另行提交。源码和脱敏文档进 Git，权重、凭据及音频不进 Git。

## 本次完成的链路

原创完整器乐夹具 → 真实 Demucs 四分轨与 SheetSage2 旋律/和弦/节拍/调性/结构候选 → 完整 MIDI 归一化 → 真实 Surge XT VST3 重建 → 新进程读取完整快照再渲染 → 夹具真值贝斯的局部改编与 stems 混合 → 浏览器整曲试听探测。MuScriptor 未获得权重授权，产品内真实对话服务未配置；该链路不是三个模型的完整验收。

## 素材与可追溯性

- 原创合成器/铃音/鼓器乐曲，80 小节、100 BPM，192 秒正文 + 2 秒尾音，共 9,312,000 samples / 48 kHz / stereo。
- 1,872 个明确 MIDI 音符；Surge XT 和 sfizz 实际渲染。采样由脚本生成，无第三方歌曲/采样。
- 最终音频：`whole-song-v2/mix.wav`，SHA-256 `5413cc2a8faa8a7a0ee186cf58a5654311ac44406289ac261e72aa6c88da2131`。
- 采用的作曲真值为独立 `truth-v3.json`，SHA-256 `292bda2891bc97b7294aa6024ef5adf810050f337bcdd7244e94168c4a21acec`。
- 第一版夹具有同音重叠贝斯，MIDI 桥接器检测并拒绝，v2 缩短相邻音符后重新渲染并重跑模型。另发现非 bridge 的 F/A/C/A 被误标为 F:maj7；v3 将标签更正为 F:maj，音频和音符未变，旧真值与旧测量保留。新增和弦构成音测试防止同类真值错误。
- 这是简单固定速度的合成器乐诊断曲；没有人声、现场演奏或目标曲风代表性。所有高分只描述该夹具。

本机原始证据位于 `D:\music-learning-runtime\benchmarks`：`whole-song-v2`、`cuda-v1`、`demucs-v2`、`sheetsage-v2`、`sheetsage-native-v1`、`render-probe-v1`、`browser-v1`、`evaluation-v3.json`。旧 v1、旧真值和失败结果未覆盖。不提交音频、完整模型输出或个人配置到 Git。

## 模型与 GPU 实测

RTX 5080 / sm_120、驱动 617.14、Python 3.11.15、torch/torchaudio 2.8.0+cu128。matmul、conv1d、STFT 均在 CUDA 上执行并得到有限数值。探测开始前其他进程已占用约 4,469 MiB 显存；未停止它们。

| 模块 | 固定版本 | 最终整曲进程耗时 | CUDA allocated / reserved 峰值 | 进程树 RSS 采样峰值 |
| --- | --- | --- | --- | --- |
| Demucs | 4.1.0，HTDemucs `cbc8a9b1a870`，`955717e8` safetensors | 9.25 秒 | 551.3 / 790 MiB | 2,558 MiB |
| SheetSage2 | `b83c3d79a6d9`，父 MERT `d8ba1c745e73` | 51.30 秒 | 3,378.5 / 3,450 MiB | 2,770 MiB |
| MuScriptor medium | 0.3.0，权重固定 `f32236969308` | 未运行 | 未测 | 未测 |

耗时包含 worker 启动/加载/推理/导出，不含首次包和权重下载。最终测试已有磁盘/驱动缓存，不能推断冷启动或其他歌曲速度。v1 首次 Demucs 24.58 秒、SheetSage2 55.78 秒，均留存。CUDA 峰值是 PyTorch allocator，不是整张卡总占用；RSS 每 0.5 秒采样，不是严格瞬时峰值。

MuScriptor 的公开仓库元数据可读，但实际固定文件下载得到 GatedRepo / 401；未伪装为模型加载成功。权重仍需用户在本机授权，适配代码也仍待实测。

SheetSage2/default 使用 FFmpeg 9.0 CLI 完成整曲解码。torchaudio 实际只列出 soundfile backend，强制 `backend='ffmpeg'` 失败。当前不开放依赖 FFmpeg 6.1 共享库的 paper 预设，不宣称 Python FFmpeg 绑定通过。

## 音乐质量与覆盖

Demucs：四条 float WAV 均为 8,555,400 samples / 44.1 kHz，恰好 194 秒。中间 12 秒窗口、±100 ms 搜索范围，bass/drums/other 相对真值的最大互相关 lag 都为 0 samples；这不是全曲所有瞬态的逐个测量。

| 真值声部 | 不校正增益/对齐的能量 SDR | SI-SDR |
| --- | --- | --- |
| bass | 12.00 dB | 11.76 dB |
| drums | 17.86 dB | 17.80 dB |
| other | 18.83 dB | 18.77 dB |

四分轨相加对原混音 SDR 32.09 dB；无真值人声时 vocals 能量约为输入的 0.00204%。移除 estimated bass 后，对真实非 bass 混音 SDR 20.02 dB，说明仍有残留/误减，不能当成干净分离。以上是本项目简单能量诊断，不是 MUSDB 基准或人工听感评分。

SheetSage2：单个 300 秒推理窗口，实际 accept `[0,194)`，无 token-limit 警告；596 个事件，时间 0～193.8 秒，ABC/MIDI 与 timed labs 已输出。

- 对更正后的真值，正文每 100 ms 网格的和弦精确标签一致率 100%；320 个预期拍点全部在 70 ms 内，固定拍点误差近 0。不能外推真实音乐。
- 前景旋律区间 19.2～182.34 秒，408 个真值音符，对应 408 个预测音符，50 ms 起音/50 cent 容差的 precision、recall、F1 均为 1.0。
- 同时要求结束时间符合 20% / 最小 50 ms 容差时 F1 为 **0**。记谱时值/声学尾音与 MIDI 键长差别明显，不能把该记谱 MIDI 当作原始演奏时值。需要保留独立 performed/notation 数据并人工校正。
- 调性只输出 C:major；相对大小调区分有解释歧义，此处不打准确率。
- 结构只输出从 0 秒起的 `intro`，**没有识别后续段落**。作曲结构本身也不是唯一声学解释；当前结果至少不能直接支撑整曲段落讨论。后续在用户目标曲上复核，若仍失败，须增加独立结构候选/校正方案并记录来源。
- 不对合成音色进行 GM 乐器分类评分，MuScriptor 的乐器混淆和多声部错误仍未测。

## 原生渲染、重开与改编

完整 SheetSage2 lead-sheet MIDI（旋律 + 和弦两条轨）经有理数 tempo 积分映射为样本事件，以真实 Surge XT 生成 196 秒重建（194 秒范围 + 显式 2 秒尾音），无削波。它不是 MuScriptor 的多乐器重建；初始合成器音色也不是原曲音色恢复。

在新原生进程恢复完整快照后仍为 196 秒、无削波；两实例各 2,855 个参数回读完全一致，状态 SHA-256 和插件二进制 SHA-256 相同，音符相同。波形最大样本差 0.11856；不能宣称逐样本确定性。默认振荡器 Retrigger=Off，官方手册说明 retrigger 对起始相位的作用，这与差异一致，但本次没有完成固定相位的对照实验来证明唯一原因。[Surge XT 手册](https://surge-synthesizer.github.io/manual-xt/)

夹具真值贝斯的 bridge 范围 `[6,451,200, 8,294,400)` 由 64 音符改为 32 音符，范围外事件逐项不变；完整贝斯和“Demucs drums+other+vocals + 新贝斯”的混合版均为 194 秒，无削波。改编来自明确真值，不是自动扒谱贝斯，也不是自然语言改编验收。

194 秒脉冲经过真实 VST3 夹具的 128 样本延迟与宿主补偿，在 5,904 / 85,296 / 5,765,904 / 9,288,000 样本精确回读。原生输出 0 样本误差。整组 render probe（包括 Python、数组、原生子进程）50 ms 采样 RSS 峰值约 491 MiB。

## 浏览器与故障验证

Playwright 1.56.1，Chromium 141.0.7390.37，独立 headless 浏览器与只读 loopback server。已验证：

- 原曲 194 秒、重建 196 秒、混合版 194 秒的完整 Web Audio 解码；0 / 97 / 193.5 秒定位，120 秒开始播放，暂停位置保持，0.5 秒循环跨越两次，切换音频。
- HTTP Range 206，读取指定 64 字节。
- 实际解码脉冲位置和原生一致；OfflineAudioContext 从 120 秒定位、1.7～1.9 秒循环的脉冲均为 0 样本误差。共享同一 source 调度函数，未使用 JS 定时器生成音频事件。
- 同时缓存原曲、重建、混合、脉冲四份 AudioBuffer，PCM 298,752,000 bytes；RSS 里程碑从约 265 MiB 增至约 564 MiB。这是抽样而非连续峰值，尚未实现大曲库缓存淘汰/流式策略。
- 浏览器无 pageerror，完成后自有 browser/server 关闭。没有执行扬声器输出、人工听辨、正式时间线光标与硬件延迟验收。

超时探测：仅对本次创建的 CUDA worker 设 0.1 秒 timeout，记录 timeout、终止其进程树，并回读确认 PID 已退出。失败不生成 completed 结果。原生 13 项回归 + 新增 7 项 MIDI/摘要/真值检查通过；浏览器探测单独通过。

## 下一步与 P0 退出缺口

1. 用户本机登录 Hugging Face 并接受 MuScriptor medium 许可，跑同一首完整曲的 medium，检查逐块覆盖、音符和乐器候选，重建全曲及逐轨输出。
2. 获取用户目标曲目路径/曲风，复核分轨串音、乐器与音符错误、SheetSage2 的时值/结构问题；完成代表片段人工听辨。合成曲高分不能代替这一项。
3. 确定产品内真实对话服务，配置本机凭据，实测工具往返、流式响应、取消、失败和音频输入能力。当前没有 mock 服务计入验收。
4. 完成最终完整环境清单；模型版本已固定，但 MuScriptor 权重未实际下载核验，系统 FFmpeg/Node 的跨机恢复流程尚未完全验证。

以上是首轮时的退出缺口，已由[第二轮报告](p0b-real-song-and-codex.md)更新。P1 工程数据库、正式前端/任务服务和产品内 AI 编辑尚未实施。
