# P2 整曲预处理与初始工程

日期：2026-10-19。P2 将 P0 的真实模型/原生宿主接入 P1 的持久工程服务。使用同一 API/SDK/CLI，原录音、模型原始输出及旧版本均不覆盖。验收结果见 [P2 报告](benchmarks/p2-pipeline.md)。

## 正常使用

更新代码或构建后先停止旧服务，再启动新版本；已有 Python 服务不会自动热更新。

```powershell
& runtime/tooling/Scripts/python.exe scripts/app.py stop
& runtime/tooling/Scripts/python.exe scripts/bootstrap_app.py
& runtime/tooling/Scripts/python.exe scripts/app.py start --open
```

主页选择完整音乐后自动排队：本机上传 → 原曲导入/完整解码 → 七阶段预处理。全部通过后显示“打开音乐工作台”。没有实际进度的阶段显示运行状态；MuScriptor 显示实际分块计数，不按计时器生成百分比。

音频、数据库、模型环境和产物仍在本机 `D:\music-learning-runtime`；NAS 只允许客户端以 rb 读取/复制，任何分析、sidecar 和渲染都写本机。本次开发只用 P0 的本机副本，没有再次连接 NAS。

## 接口

原有 23 个操作保留，新增 `jobs.retry`、`jobs.stages`、`preprocessing.get`、`preprocessing.adopt`，共 27 个。`describe_capabilities` 返回版本化参数/结果 schema、任务类型及对应 payload schema。

新增任务 `project.preprocess`，客户端 payload 为 `{"use_cache":true}`，不接受模型命令、任意输入/输出路径或内部 revision ID。服务分配稳定目标 revision 和各模型 AnalysisRun ID，执行令牌仅由 worker 持有。

```powershell
$env:PYTHONPATH = 'src'
$python = 'D:\music-learning-runtime\envs\app\Scripts\python.exe'
# 先创建工程，从返回值取得 ID 与 head；下面占位符须替换。
& $python -m music_learning.cli import-audio 'D:\path\song.flac' --project <project_id> --expected-revision <head> --key song-import-001 --preprocess
# 等待导入任务。其 result.preprocess_job_id 是后续整曲任务 ID。
& $python -m music_learning.cli wait <import_job_id>
& $python -m music_learning.cli wait <preprocess_job_id> --timeout 1800
# 所有查询与重试也能通过 call + JSON 文件或 stdin 使用。
```

```python
from pathlib import Path
from music_learning.sdk import Client

with Client() as music:
    project = music.call("projects.create", {"title": "完整音乐研究"})
    task = music.import_audio(Path(r"D:\path\song.flac"), project_id=project["id"],
                              expected_revision=project["current_revision"], preprocess=True)
    imported = music.wait(task["id"])
    finished = music.wait(imported["result"]["preprocess_job_id"], timeout=1800)
    stages = music.call("jobs.stages", {"job_id": finished["id"]})
    results = music.call("preprocessing.get", {"project_id": project["id"]})
```

SDK `import_audio` 的 `preprocess` 默认 False，保留 P1 导入语义；主页默认 True。已有原曲工程可用 `Client.preprocess(project_id, expected_revision=head, use_cache=True)`，或 `jobs.submit`。原曲导入与预处理是两个可独立回读的持久任务。`wait` 超时只停止客户端等待，任务 ID 和结果仍保存。

## 七阶段及校验

| 阶段 | 产物与约束 |
| --- | --- |
| normalize | 原采样率的 stereo float WAV、多级原曲波形；不增益、不量化、不裁前奏；帧数必须对应完整解码结果 |
| demucs | HTDemucs 的 vocals/drums/bass/other 原始输出，以及回到原曲采样率的四条全长音频；零点/长度校验 |
| muscriptor | medium 的原始事件 JSONL、进度、原始 MIDI、样本时间归一化事件；所有 5 秒块须完成 |
| sheetsage | default/bf16 双窗口或单窗口的原始 token、事件、候选与记谱输出；接受窗口连续覆盖整曲，EOS/缺失检查 |
| assemble | 稳定声部/事件 UUID、stems、拍点/和弦/调性/段落候选、TimingMap、音色映射与受控资源 |
| render | 真实 Surge XT / sfizz 混音及每轨 WAV、完整插件状态、公开参数/描述、资源映射、串行路由、原生快照及波形 |
| validate | 所有必需产物、长度、采样率、非静音/削波、来源及版本绑定检查，生成不可变完整性报告 |

导入通过 FFmpeg 流式完整解码并计数 PCM 样本，容器时长另行保留；MP3 等带编码延迟/填充的文件不以容器名义时长定义采样时钟。处理文件始终是本机 CAS 对象。分轨回到源采样率后若帧数不匹配，会明确失败，不悄悄裁剪或平移。

MuScriptor 保留原始演奏时刻，完整模型覆盖不代表每个音符识别正确。原始 `NoteStartEvent` / `NoteEndEvent` 与 MIDI 数量差、零时长事件和未支持事件独立记录；不伪造力度、踏板或声部。未闭合、歧义 MIDI、越界事件拒绝发布。

SheetSage 的拍点和和声是模型候选。规则转换只在严格单调、拍号及小节跨度可解释时产生有理数 TimingMap；无法解释时保留原始拍点注释并按样本定位，不强制恒定速度。首个短下拍跨度可推断弱起，但明确待核对；末端不足一个拍间隔的投影锚点有 `estimated=true`，并写入问题清单。

原生初版重建使用候选速度段/拍号和原始音符采样时刻；不可建立 TimingMap 时，插件 transport 的固定 120 BPM / 4/4 明确作为回退。当前原生 PPQ 从音频零点开始，弱起小节标签仍属于候选记谱坐标，完整弱起 transport 控制留在 P3。不会因此平移或量化音符。

## 重建与资源

`config/pipeline-profile.json` 固定 P2 默认方案：Surge XT 1.3.4 初始音色；sfizz 1.2.3 + 自制 35～81 键诊断鼓；block 512，显式 2 秒尾音。检查实际插件二进制摘要、本机宿主摘要及采样库文件摘要。它用于核对写法，不能称为原曲音色或歌声恢复。

每个 PluginInstance 保存完整 opaque state asset、descriptor asset、参数、二进制/版本，以及资源摘要、相对路径和入口。自制 SFZ/采样只读验证后复制到本机受控目录，原生重开逐轨渲染时回读相同状态摘要。参数列表不代替完整状态。文件原路径、打包可移植性和私有插件适配仍按后续交付验证。

混音与每轨文件均绑定目标 revision；随机插件逐轨单独恢复后，波形不保证与混音中的对应声部逐样本相同，保存实际冻结产物，不以重新渲染的 WAV hash 判断状态恢复失败。

## 阶段持久化、缓存与重试

Alembic `0002` 新增 stage_cache、job_stages、preprocessing、stems、control_events；不修改冻结的 `0001`。迁移前通过 SQLite backup API 保存含 WAL 的一致备份；P1 旧快照与摘要不重写。

每个阶段成功后：校验输出 → 发布本机 CAS 文件 → 在持有有效租约/fencing token 的短事务中提交阶段记录、缓存和事件。API/SDK/CLI 都能查询真实状态。文件先生成但没有事务引用时，只是孤儿诊断产物，不算成功，也不自动删除。

缓存键包括输入摘要、上游 manifest、配置、模型/代码/依赖锁、实际 Python 包版本、宿主/插件/采样版本。当前采取保守策略：共享领域代码变化也使缓存失效。复用前重新验证文件大小/摘要；未完成的模型阶段不作为缓存复用。目标 revision/AnalysisRun ID 仅从 assemble 起进入缓存键，独立工程可复用相同录音的已验证模型结果。

worker 崩溃或服务停止后，Windows Job Object 结束整个子树；租约到期的新 attempt 使用新令牌，从已验证阶段缓存恢复。模型异常/取消则明确保留终态，用 `jobs.retry` 提交新任务继续。原始输入与已提交版本不受影响。旧 attempt 或取消后的晚到输出不能发布。

## ready 与重新分析

`ready` 表示完整材料和可试听产物齐备、通过技术检查，**不表示音符/和弦准确或已经人工校对**。缺阶段、空转录、超出时长预算、模型不完整、渲染静音/削波、文件损坏均不发布 ready。模型质量、分轨串音、弱起推断和音色替代保留在质量报告中。

第一次空白原曲工程成功时，短事务同时保存分析基线、不可变初始 revision、实体投影、渲染/波形清单、任务结果并推进 head。若运行中 head 改变，或原工程已有声部/修订，完成结果只成为独立候选 revision，旧 head 不变。

`preprocessing.get(project_id, revision_id?)` 仅对指定版本返回已发布材料，不能把旧版本音频当新版本试听。明确采用候选时调用 `preprocessing.adopt`，要求当前 head 和幂等键，并核对素材摘要；旧版本/讨论仍保留。普通快照提交产生的新版本不会继承旧 ready 状态。

## 前端范围

主页展示工程与七阶段状态、实际分块进度、重试/取消；全部通过后开放只读工作台。工作台提供段落跳转、原曲波形、MIDI 钢琴卷帘、多声部显示、当前和弦候选、原曲/混音/逐轨试听、音频 stems 下载及问题清单。音频 stems 与符号声部清楚区分。

这是 P2 的结果浏览入口。精细缩放与音符编辑、完整音频图操作、Web Audio 同步/循环/A-B、插件制作界面在 P3；真正产品内 Codex 讨论和对话改编在 P4/P5。本阶段不会用占位聊天回复或只改和弦标签来冒充修改了音乐。

## 配置与验收入口

`config/app.example.json` 新增 model_runtime、pipeline_stage_timeout_seconds（默认每阶段 1800）、pipeline_max_duration_seconds（默认 600，与原生正文预算一致）、pipeline_min_free_bytes（默认 3 GiB）。已有本机配置可不改，默认继续使用 runtime/model-runtime.json 的 D 盘位置。设置不会更改系统 Python、缓存或 NAS。

```powershell
& D:\music-learning-runtime\envs\app\Scripts\python.exe -m pytest tests/service -q --basetemp=D:\music-learning-runtime\tmp\pytest-p2
& D:\music-learning-runtime\envs\transcription\Scripts\python.exe -m pytest tests/native tests/models -q --basetemp=D:\music-learning-runtime\tmp\pytest-p0-regression
& D:\music-learning-runtime\envs\app\Scripts\python.exe scripts/export_contracts.py --check
pnpm --dir apps/web build
```

`scripts/p2_acceptance.py start <local-audio> --output <new-local-directory>` 通过 SDK 一次导入并排队；`verify <owner.json> --output <new-summary.json>` 核对整曲 ready、所有关联素材通过 HTTP 的大小/摘要和阶段状态。

`scripts/p2_recovery_probe.py <owner.json> --output <new-report.json>` 专用于验收工程：等待首次真实转录进度后停止/重启本项目服务，验证子树退出、租约恢复和前两阶段缓存复用。会中断本项目服务，正常使用不需要运行该探测。

前端验收使用已有固定 Playwright Chromium，`apps/web/e2e.mjs` 通过真实文件选择导入整曲，等待 ready，打开工作台，核对原曲/重建播放、卷帘和重载。报告与截图均留本机，不提交音乐、数据库、Token 或私有日志。

相关官方依据：[FFmpeg filters](https://ffmpeg.org/ffmpeg-filters.html)、[Alembic migrations](https://alembic.sqlalchemy.org/en/latest/tutorial.html)；模型、VST3 与依赖来源继续以 P0 固定清单为准。
