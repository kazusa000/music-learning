# P0b 完整真实曲目与 Codex 验证

2026-10-18，源码提交 `ebb50ef`。P0b 的整曲模型、原生渲染、试听和真实对话技术探测已完成；P1 尚未开始。首次夹具结果保留在 [P0 首轮报告](p0-summary.md)，操作说明见 [模型探测](../12-model-probes.md)与 [Codex 接入](../13-codex-integration.md)。

## NAS 只读取样

用户授权从 NAS 音乐库任选一首复制，明确禁止修改 NAS。实际选择《届かない恋 '13》（上原れな），FLAC / 44.1 kHz / stereo，305 秒，37,044,317 bytes。文件标签来自本地副本，不通过外部曲库猜测版本。

只以 rb 打开远端选定文件，流式复制到独立的 `D:\music-learning-runtime\references\nas-Mu000001-v1`；ffprobe、模型、渲染和日志全部使用本机文件。源路径仅保存在本地忽略的 import.json，未遍历复制整库，不更新 NAS 标签/歌词/封面/清单或任何源文件。

输入 SHA-256：`a870febc293b50604bb68ce7d50a536593ff61c18bbad4cbb9efebc36e62db43`。全流程结束后再次只读计算远端文件摘要，SHA-256、大小和修改时间均与导入时一致。相关自动检查还验证源文件只用 rb 打开、目标必须位于本地受控运行目录、已有输出不覆盖。

## 三模型完整曲目实测

用户完成 Hugging Face 许可及本机登录后，固定 MuScriptor medium 的全部文件下载并通过摘要验证。使用既有两套 Python 3.11 / torch 2.8.0+cu128 环境，RTX 5080 上实际 CUDA 运算与推理，未降级 CPU。

| 模块 | 305 秒完整曲进程耗时 | CUDA allocated 峰值 | 0.5 秒采样进程树 RSS 峰值 |
| --- | --- | --- | --- |
| Demucs 4.1.0 / HTDemucs | 12.31 秒 | 551.3 MiB | 3,504.6 MiB |
| SheetSage2 固定版本 | 45.00 秒 | 3,380.3 MiB | 3,614.0 MiB |
| MuScriptor 0.3.0 / medium | 236.42 秒 | 2,351.2 MiB | 1,818.3 MiB |

包含进程启动/加载/推理/导出，使用已有缓存，不含下载。数字描述本次输入/配置，不承诺其他歌曲速度或冷启动性能。

Demucs 四条音轨各 13,450,500 samples / 44.1 kHz，均完整 305 秒。四轨相加对输入的简单能量 SDR 为 29.88 dB；没有干净真值 stems，不能由此宣称单轨干净、贝斯可无损移除或串音准确率。

SheetSage2 使用两个重叠窗口：接受区间 `[0,200)` 和 `[200,305)` 连续覆盖整曲；1,014 个事件延伸到 304.66 秒，193 个和弦事件，616 个旋律音符（313 vocal、303 instrumental），没有 token-limit 警告。候选结构包括 25.21 秒 verse、53.65 秒 pre-chorus、67.88 秒 chorus、213.65 秒 bridge、259.88 秒 outro 等，属于模型输出，尚未人工校对。ABC 导出对弱起/末小节有三条 padding/inferred-meter diagnostics，原始记录保留。

MuScriptor 全部 61/61 个 5 秒分块完成，原始事件与 MIDI 均保存。MIDI 共 9,378 音符，六类候选：drums 2,095、distorted electric guitar 4,545、electric bass 1,129、voice 620、string ensemble 166、acoustic piano 823。首音 6.8198 秒、末音结束 300.9896 秒；处理全部分块不代表开头/结尾音符必然识别完整。MIDI 桥接无同音重叠/未闭合/未知事件；当前导出力度统一为 100/127，CC=0，不能恢复原演奏动态、踏板或弯音。

## 已量化的模型错误

同一固定 medium 另跑完整 194 秒原创夹具，39/39 分块完成，52.55 秒。采用此前独立更正的 truth-v3，保留演奏时间，不做节拍校正或起音偏差补偿。

| 音符组 | 真值 / MIDI 预测数 | 起音 precision / recall / F1 | 同时要求 offset 的 F1 |
| --- | --- | --- | --- |
| 有音高声部合并评分 | 1,032 / 1,040 | 0.8375 / 0.8440 / 0.8407 | 0.4035 |
| 鼓 | 840 / 212 | 0.9292 / 0.2345 / 0.3745 | 0.0152 |

起音容差 50 ms、音高容差 50 cents；offset 容差取 20% 或 50 ms 较大者。代表区间 0～20 秒、85～105 秒、172～192 秒的有音高 F1 分别为 0、0.9610、0.8410。模型首个原始 NoteStart 在 20.09 秒，开头是真实漏检，未通过裁掉前奏掩盖。

三个合成有音高声部被合并标成 acoustic piano，不能据此得到原本四条独立制作轨。原始事件 1,257 个 NoteStart，5 个零时长 NoteEnd 在上游 MIDI 导出中未形成有效音符，最终 MIDI 1,252 音符；两个版本都保存。真实歌曲没有人工音符真值，不套用夹具分数为歌曲准确率。

另做一次保留原音频的 +12 dB 派生输入实验，反复达到每块 2,000-token 限制且无 EOS；在完成 14/39 块时终止已核对身份的自有 worker，保留 failed/stop-reason 和原日志，未选作默认预处理。不能用该实验声称修复漏检。原始音频未覆盖。

## 完整 VST3 重建与浏览器

MuScriptor 六声部通过实际 Surge XT / sfizz 导出 307 秒整曲及六条逐轨音频（305 秒 + 2 秒尾音），均无削波。整曲 13,538,700 samples / 44.1 kHz，peak 0.1880，渲染约 22.33 秒；各轨单独从完整状态加载渲染，分别约 4.2～6.0 秒。

有音高轨使用 Surge 初始音色，人声轨也是音符合成器重放，没有歌声/歌词合成。鼓使用自制诊断性 SFZ；35～81 共 47 键全部经真实 sfizz 逐键测得非静音，并保存样本/SFZ/插件摘要与音色替代说明，实际本曲使用其中 12 个键。该工具箱用于检查事件与编配，音色差异不标成原曲音源恢复。

SheetSage2 三声部 lead-sheet 同样生成完整 307 秒试听，约 13.10 秒，无削波，与 MuScriptor 输出分别保存，不覆盖或强行合并。

Chromium 141.0.7390.37 的真实浏览器探测通过完整 FLAC 原曲 305 秒与 MuScriptor 重建 307 秒解码、开头/中间/近结尾定位、播放/暂停和循环、Range 206。原生脉冲解码及离线 seek/loop 仍为 0 samples 误差；requestAnimationFrame 更新的显示时钟相对 AudioContext，60 次抽样最大滞后 21.33 ms。缓存原曲、重建和 194 秒脉冲三份 PCM 为 309,504,000 bytes，里程碑 RSS 最大约 578.2 MiB。没有执行扬声器/人工听辨，正式工作台和硬件延迟仍属于后续验收。

## 真实 Codex 接入与恢复基线

既有 ChatGPT managed login 已实测 App Server 与 `gpt-6.1-sol`：随机校验码工具往返成功、流式文本成功、工具错误原样回报、取消返回 interrupted、未知 RPC 正确报错。没有发送歌曲音频；探测使用合成工程数据。独立 Codex home 的 needs_login 状态也已实测，正式应用不会假装自动继承登录。具体配置/运行边界见 [Codex 接入方案](../13-codex-integration.md)。

已有原生/插件锁、两套 Python hash locks、四个模型仓库的固定 commit/逐文件摘要、Codex 主程序及三个 sidecar 摘要；补充 FFmpeg/ffprobe/Node/uv 的实际版本和二进制摘要检查。Codex 本地 bootstrap 四文件校验通过，媒体工具校验通过；后续变更须重新探测，不自动追最新。完整安装/授权的一键跨机发行和工程备份仍在后续交付阶段。

模型支持显式 cancel.request，实际运行的 MuScriptor worker 已验证 cancelled 后退出；额外增益实验失败保持失败。最新 27 项回归通过（原生 13 + 模型/只读导入/协议/资源校验 14），整曲浏览器探测单独通过。所有自建测试服务、模型 worker 与浏览器均已结束。

## 本地证据与后续工作

证据均位于 `D:\music-learning-runtime`：参考录音 `references/nas-Mu000001-v1`；`benchmarks/nas-{demucs,sheetsage,muscriptor}-v1`；`nas-muscriptor-native-v1`、`nas-muscriptor-stems-v1`、`nas-muscriptor-browser-v2`、`nas-reference-summary-v1.json`；夹具 `fixture-muscriptor-v1` / `fixture-muscriptor-evaluation-v2.json`；对话 `codex-v4`。音频、权重、账户/对话信息与详细日志不进 Git。

下一步进入 P1：权威工程/版本/素材/任务持久化与统一命令服务。P2/P3 明确显示识别缺失、声部合并、演奏/记谱时间差、音色替代和原曲/重建/混合模式；P4/P5 接入真实工程工具和对话编辑事务。人工音乐核对、听辨与最终完整应用流程仍保留在后续验收中。
