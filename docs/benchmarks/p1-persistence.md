# P1 工程、任务与统一接口验收

2026-10-18，Windows / Python 3.11.15。P1 骨架已完成，下一步 P2。完整用法和限制见 [工程服务](../14-project-service.md)。P0 的模型质量、音色和人工听辨限制继续有效。

## 自动验证

| 层次 | 结果 | 证明范围 |
| --- | --- | --- |
| P1 pytest | 26 passed，14.97 秒 | 元数据迁移/备份/重开、并发幂等、乐观锁、回滚、CAS/损坏、来源、实体投影、时间映射、HTTP/SDK、队列与子进程 |
| P0 回归 | 27 passed，15.15 秒 | 原生 13 + MIDI/导入/Codex 协议/资源 14；不重跑 GPU 推理 |
| 后端静态检查 | ruff 通过；生成契约 `--check` 通过 | Python 检查及 schema/TS 无漂移 |
| 前端 | TypeScript 严格检查、Vite production build 通过 | React 主页可构建 |
| 整曲浏览器 | Chromium / Playwright 实际通过，无 page error | 文件选择、真实上传与任务、305 秒原曲、定位播放、刷新恢复、截图检查 |
| 真实服务重启 | API/worker 实际 stop/start 后核对通过 | 工程 head、6 声部、9,378 音符、素材、插件完整状态、笔记和事件 cursor |
| 真实 CLI 批处理 | 同一批请求执行/重放两次结果一致 | 持久回执生效，只追加一条笔记；head 不变 |

P1 测试包括：原曲不变、失败不产生成功回执、同 key 不同内容冲突、同 head 并发只有一个写入、跨工程引用拒绝、未知操作/额外字段拒绝、上传上限、Host/Origin/Token、只以 rb 打开源文件、拒绝网络数据根/路径越界。

时间夹具独立覆盖：弱起 bar 0、120→90 BPM 的变速锚点、4/4→6/8、三连音 `1/3`、跨查询边界的延音；sample↔seconds 和有理数 beat 往返精确，舍入不超过半个采样点。没有 timing map 时明确拒绝 beat 转换。

任务夹具执行了真实 VST3：正常扫描/完整状态回读，正在运行的挂起插件取消、超时、强杀实际 worker 后整个子树退出。任务在数据库中保留 running，租约到期重启后第 2 次 attempt 成功；旧 attempt 无法续租或发布结果。另在素材登记/版本写入后、事务提交前注入故障，确认素材关联、版本/head、投影均回滚，任务明确 failed。原子发布留下的无引用 CAS 文件不算成功，不自动清理。

26 项测试有一条 Starlette TestClient 提示：当前 httpx 适配将来弃用。当前固定组合的测试和真实网络接口均通过，未忽略失败或更改 warning 过滤来制造通过。P0 首次误用 tooling 环境收集模型测试时缺少 mido，改用原有 transcription 环境后 27 项通过，未向系统 Python 临时装包。

## 真实完整曲目保存

本轮使用 P0 已有本地 `references/nas-Mu000001-v1/source.flac`，305 秒 / 44.1 kHz / stereo。**未访问 NAS，也未写入 NAS**；本地源大小、mtime 和 SHA-256 前后不变。SHA-256 与 P0 一致：`a870febc293b50604bb68ce7d50a536593ff61c18bbad4cbb9efebc36e62db43`。

真实 CLI 新建工程，SDK 流式上传，持久 worker 用固定 ffprobe + ffmpeg 完整解码后发布。源素材去重保存，工程状态为 `imported`。P0b MuScriptor 原始 events.jsonl、normalized.json 保存为独立不透明素材，记录固定模型/权重/代码来源，原输出不覆盖。

同一工程保存 6 个 MIDI 声部、9,378 音符，原始采样时刻和持续时间保留；稳定事件 UUID 从工程 namespace 与原始事件 ID 派生。没有把 MIDI 默认 120 BPM 当成真实乐曲 timing map，未虚构和弦或节拍。该步骤仅验证持久化，**没有重跑模型、提高准确率或接通 P2 流水线**。

真实 worker 检查 Surge XT 1.3.4，回读 2,855 个参数及完整 JUCE/VST3 opaque state。状态单独存入 CAS 并通过 API 读回验证 SHA-256：`f3d1894be8d0648906a36de09cc6e6b41ec6d67f81ce16d49819e74afeeb5a72`。不以参数列表代替状态；未宣称此操作已装载成工程处理图或产生新的重建音频。

实际停止并重启 API 与 worker 后，head 与快照摘要不变，原曲和插件状态内容摘要正确，版本绑定的笔记恢复，SSE 从旧 cursor 续读只得到新增笔记事件。SQLite backup API 在服务运行时生成元数据备份并通过 integrity_check；音乐/插件资源的完整打包恢复尚待 P6。

## 主页与边界

真实浏览器通过文件选择再次导入同一完整曲目的本地副本，显示排队/执行/完成记录、工程版本与长度，原曲 duration 为 305 秒；最终定位播放结果 150.28543 秒，刷新后仍选中同一已保存工程。页面错误为零，已查看整页截图。另人为延迟旧工程响应 500 ms 后快速切换，确认晚到响应不会串入新工程的版本详情。首次未配置 P0 浏览器目录时未找到 Chromium，随后指定 `PLAYWRIGHT_BROWSERS_PATH` 使用已有固定浏览器通过；无额外浏览器下载。

页面明确显示“整曲分析尚未开始”，不开放伪造 ready 工作台。整曲模型任务、候选节拍/和弦、工程重建与制作、正式 Codex 对话仍在 P2～P5。路由、插件和自动化目前可作为经过校验的版本数据保存；P1 不执行复杂图或声音修改。

私有完整证据保存在 `D:\music-learning-runtime\benchmarks\p1-persistence-v1`（summary / restart-verification / cli-verification / cleanup）、`p1-homepage-v3`（summary / homepage.png / selection-race-verification）；早一轮截图在 v2。应用数据位于 `D:\music-learning-runtime\app-data`。这些文件、音频、数据库、Token 和日志不进入 Git。验收结束停止本次启动的服务及浏览器；需要使用时通过 start.cmd 启动。

## 下一步

P2 将三模型和现有原生重建适配器接到这些持久任务/素材/revision 契约上，实现阶段缓存、完整性报告和真正的整曲 ready 门槛。继续保留模型漏检、声部合并、时值错误、原始输出及校正版本；NAS 维持只读。
