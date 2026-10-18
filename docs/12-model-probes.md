# P0b 模型与整曲探测运行说明

这是独立进程的探测工具，尚不是 P1 工程服务、持久任务队列或正式前端。当前实际结果见 [P0 汇总](benchmarks/p0-summary.md)。所有输出保存在 Git 外，输入、模型原始输出和旧测量不覆盖。

## 存储与环境

本机 E 盘空间有限，模型环境、缓存、权重及整曲产物放在独立的 `D:\music-learning-runtime`。源代码仍在本仓库。位置记录于被忽略的 `runtime/model-runtime.json`，也可以通过进程环境变量 `MUSIC_MODEL_RUNTIME` 指定。不会修改系统 Python 或全局缓存设置。

```powershell
# 先按 docs/11-native-host.md 准备 P0a 工具与插件。
& runtime/tooling/Scripts/python.exe scripts/bootstrap_models.py --root D:\music-learning-runtime
& runtime/tooling/Scripts/python.exe scripts/model_runtime.py status
```

Windows x64 / Python 3.11：`requirements-transcription.lock` 固定 MuScriptor 0.3.0、Demucs 4.1.0 与 CUDA 版 torch/torchaudio 2.8.0；`requirements-sheetsage.lock` 单独固定 SheetSage2 官方依赖，避免 NumPy/Transformers/Hugging Face Hub 版本冲突。bootstrap 使用 `uv pip sync --require-hashes --torch-backend cu128`，已经在本机回放验证。

生成新锁文件时使用相应 `.in` 文件、Python 3.11、`uv pip compile --torch-backend cu128 --generate-hashes`，升级后重新跑真实模型。锁文件仅为当前 Windows 组合，不保证其他平台可直接复用。

## 权重下载与登录

`config/model-dependencies.json` 固定四个仓库的 commit、逐文件大小及 LFS SHA-256/普通 Git blob 摘要。下载后复核，推理前再次复核；不会跟随 `main` 自动更新。原始远程代码保存在模型目录，SheetSage2/MERT 按固定代码与权重版本执行。`verified.json` 是本次下载清单，不代替推理前验证。

```powershell
& runtime/tooling/Scripts/python.exe scripts/model_runtime.py download demucs
& runtime/tooling/Scripts/python.exe scripts/model_runtime.py download sheetsage
& runtime/tooling/Scripts/python.exe scripts/model_runtime.py download mert
```

2026-10-18 实测 SheetSage2 和 MERT 父模型可公开下载。MuScriptor medium 的权重是 gated；本机未授权下载返回 HTTP 401 / GatedRepo。需要用户完成以下步骤：

1. 登录 Hugging Face，在 [MuScriptor medium 页面](https://huggingface.co/MuScriptor/muscriptor-medium) 阅读并接受权重许可。
2. 在自己的交互式 PowerShell 运行下列登录命令，按提示输入有模型读取权限的 Token。不要把 Token 发到聊天或写进命令行参数。
3. 下载固定版本；登录不等于已经获得 gated 仓库权限。

```powershell
& runtime/tooling/Scripts/python.exe scripts/model_runtime.py login
& runtime/tooling/Scripts/python.exe scripts/model_runtime.py download muscriptor
```

凭据保存在该项目模型运行目录下的 Hugging Face 凭据文件，不添加到 Git credential。`status` 只报告凭据是否存在，不输出内容。原始下载/模型日志可能含本机路径或临时下载地址，仅保存在本地，不提交。

## 完整曲目与模型运行

以下目录必须选择尚未存在的新名称。探测器拒绝覆盖已有结果。生成器创作 80 小节、100 BPM、192 秒正文加 2 秒尾音的合成器/铃音/打击乐器乐曲，保存实际 VST3 渲染、四条真值声部、MIDI 与作曲标注。它不是用户目标曲风的真实录音。

```powershell
$modelPython = 'D:\music-learning-runtime\envs\transcription\Scripts\python.exe'
$analysisPython = 'D:\music-learning-runtime\envs\sheetsage\Scripts\python.exe'
$bench = 'D:\music-learning-runtime\benchmarks'
& $modelPython scripts/make_whole_song.py --output "$bench\my-whole-song"
& $modelPython scripts/p0_benchmark.py --model cuda --output "$bench\my-cuda"
& $modelPython scripts/p0_benchmark.py --model demucs --input "$bench\my-whole-song\mix.wav" --output "$bench\my-demucs"
& $modelPython scripts/p0_benchmark.py --model sheetsage --input "$bench\my-whole-song\mix.wav" --output "$bench\my-sheetsage"
# 获得授权并下载后再运行，不会自动改用其他模型。
& $modelPython scripts/p0_benchmark.py --model muscriptor --input "$bench\my-whole-song\mix.wav" --output "$bench\my-muscriptor"
& $analysisPython scripts/evaluate_whole_song.py --fixture "$bench\my-whole-song" --demucs "$bench\my-demucs" --sheetsage "$bench\my-sheetsage" --output "$bench\my-evaluation.json"
```

模型任务逐条执行，当前没有 P1 的持久队列/跨命令 GPU 调度锁，不要同时启动多个 GPU 探测。每次启动先验证 CUDA 上的 matmul、conv1d、STFT，强制 CUDA，失败不会退回 CPU。worker 保存输入摘要、包版本、模型产物摘要、CUDA allocator 峰值；外层记录状态、退出码、0.5 秒采样的进程树 RSS、超时与 PID。CUDA allocator 数字不包括驱动/其他进程占用。

默认超时 3600 秒，可用 `--timeout` 调整。Ctrl+C/超时会终止本次自建 worker 子进程树；已实测超时后 worker 退出。失败保留本次输出作诊断，不把残留文件视为成功。`completed` 表示推理进程结束，质量、全曲覆盖和结构缺失仍须审阅结果，不能直接据此把工程设为 ready。

Demucs 使用固定 HTDemucs 单模型 safetensors，seed 20261018、shifts=1、split、overlap=0.25，输出 44.1 kHz float WAV。不通过自动下载的未固定模型包加载。

SheetSage2 使用本地固定父模型、`trust_remote_code=True`、SDPA、bf16/default，全曲 `max_seconds=None`。当前官方 default 解码通过 FFmpeg CLI；本机 FFmpeg 9.0 已实际用于整曲推理。torchaudio 2.8 的 FFmpeg 共享库 backend 在本机不可用，`paper` 预设没有验证，当前 worker 不暴露该选项。这是已测的独立路径，不宣称 FFmpeg Python 绑定已通过。

MuScriptor 计划使用固定 medium 本地权重、逐块流式转录并保存事件、进度和 MIDI；先保留原始演奏时间，不执行量化、自动删前导或隐式下载另一个 beat 模型。该适配代码尚未通过授权权重验证。

## MIDI、重建、修改与重开

```powershell
& $modelPython scripts/midi_to_native.py "$bench\my-sheetsage\analysis\transcription.mid" --source-audio "$bench\my-whole-song\mix.wav" --output "$bench\my-reconstruction" --render
& $analysisPython scripts/p0_render_probe.py --fixture "$bench\my-whole-song" --demucs "$bench\my-demucs" --reconstruction "$bench\my-reconstruction" --output "$bench\my-render-probe"
```

MIDI 桥接支持 PPQ type 0/1，用有理数积分 tempo 跨段时间，再一次舍入到样本。记录音符、CC、program/轨道来源、拍号与未支持事件；未闭合音符、同音重叠、冲突 tempo、越界、未适配的 pitch bend 等明确拒绝渲染。它不替代 P1 的正式领域数据或 P3 的完整 MIDI 编辑器。

当前音色映射将旋律/和弦等有音高轨映射到 Surge XT 初始音色，**不恢复 GM 原音色**；`mapping.json` 明确记录替代。鼓必须显式提供已验证的自制 SFZ，目前仅 36/38/42 三个键，其他键报错。控制器、音源/奏法兼容性继续按真实模型输出测试。

render probe 用完整插件快照在新原生进程重开模型重建；另以夹具真值贝斯创建 57～72 小节的稀疏节奏版本，生成独立完整贝斯，以及剔除 estimated bass 后加入新贝斯的混合版。后者验证分轨替换的工程路径，**不是 MuScriptor 扒出的贝斯**，并保留 Demucs 串音。最后生成 194 秒脉冲并验证插件延迟补偿。

Surge 默认音色的重开状态摘要、全部公开参数可以一致，但波形不保证逐样本一致；已有音频必须作为不可变 Render 保存。不同音色/插件的确定性须单独测量，不能只按 WAV hash 判断恢复失败或修改生效。

## 浏览器探测

`probes/player` 为纯 JS 验证页，不代表 React 产品前端已经实现。`window.probe` 提供 load/play/pause/seek/setLoop/state/close；播放位置由 AudioContext 时钟推导。只读服务器仅开放 manifest 的音频与三个静态文件，绑定 127.0.0.1，支持单段 byte range。

```powershell
pnpm --dir probes/player install --frozen-lockfile
$env:PLAYWRIGHT_BROWSERS_PATH = 'D:\music-learning-runtime\playwright'
pnpm --dir probes/player exec playwright install chromium
node probes/player/probe.mjs $modelPython "$bench\my-render-probe\player-assets.json" "$bench\my-browser"
# 如需自己试听，前台启动；Ctrl+C 退出。
& $modelPython scripts/serve_player_probe.py "$bench\my-render-probe\player-assets.json"
```

自动脚本检查完整音频解码、定位、播放时钟、暂停、循环、切换、range、真实脉冲解码与 OfflineAudioContext 的 seek/loop，记录 Chromium 版本、解码 PCM 字节数和里程碑 RSS，最后关闭自有浏览器与服务器。它不证明扬声器输出或听辨，也不证明正式前端的光标绘制/设备延迟符合 P3 验收。

## 检查与尚需输入

```powershell
& $modelPython -m pytest tests/native tests/models -q --basetemp=runtime/pytest-p0b
```

当前需用户提供 Hugging Face 本机授权，以及后续产品内对话服务的服务商/模型选择与本机密钥配置；不通过这个开发聊天的登录状态冒充产品 API。目标曲目的本机路径/曲风可以随后提供，完整真实录音与人工听辨仍是必需验收。
