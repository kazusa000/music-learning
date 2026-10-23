# 项目开发约定

## 当前任务与授权

- 继续 Kontakt 原 full-h 未完成验证、通过项增量入库和已运行的试听跟随。原验证失败项暂不分析、修复或重试。
- 唯一入口是 `runtime/kontakt-full-job.local.json`、`runtime/kontakt-publication-job.local.json`、`runtime/kontakt-preview-job.local.json`。每次核实 PID、creation_time、实际命令与轻量 progress；旧 started 字段不证明运行。
- 2026-10-24用户明确恢复资源中断：当前验证wrapper27100沿用pending-only-resume-02固定实现/原plan；入库wrapper30268指向`runtime/maintenance/kontakt-resource-resume-20261024/incremental-publication-05`；试听wrapper57144指向同根`incremental-previews-03`。旧错误、入口、progress和三队列已保存；仅84个未完成项继续，891通过/17失败不重验。维护根保存保护快照与后续实核结果，勿按旧PID恢复重复批。
- 健康任务不重启、不另开批次、不移动活动目录；通过/失败检查点直接跳过，不重读全部旧音频。仅为阻止当前任务推进的执行故障做最小恢复。
- Ample 已退库，验证、排查与旧52项预热均取消；MODO后续导入取消，不探测，不按旧记录恢复。
- 此音源跟进只属于聊天 `01a15b37-7167-78c0-9586-ecc83480fa09`，不得转交或唤醒开发聊天 `01a1592c-aa2b-77a3-89df-3022241ae091`。
- 正常后台推进保持安静；原验证、入库及当前试听收尾后一次汇报并暂停跟进。用户明确暂停时保存状态并停止，不自行恢复。

## 当前 Kontakt 边界

- `data/maintenance/instrument-intake/kf22` 的现有数据、来源证据、固定源码、宿主、计划、输入与原失败报告不搬动、不覆盖。共享规则可以改，新实现独立固定；不得修改活动冻结副本。
- 真实音频、完整状态、参数/来源身份和两次独立空机架恢复标准不放宽。可信完成检查点绑定 candidate/receipt/validator/source，只读必要状态后增量发布，不重复验证音频。
- catalog 使用共享目录锁、CAS与追加语义；旧条目不覆盖，failed/running/pending不入库。当前固定宿主和计划的134输入、14保护路径以原 full-h plan/pins 为准。
- 试听跟随器的 publication_run 以当前plan实查，新03已跟随本次publisher05；旧02引用过时，不能用旧目录判断当前完成。不得因仍报 warming 重启健康跟随器。终态后核验无音频子进程，才关闭仅入队跟随器。
- Lua 来源/键位查询不是 VST3 状态恢复；identity tag 不是独立宿主证明。未运行的设计、mock、静态分析与真实发声分别记录。
- 当前任务记录保持原路径：`docs/benchmarks/instrument-intake-full-recovery.md`；入库规则见 `docs/development/instrument-intake.md`。

## 工程、资源与发布

- 正式 SQLite/CAS 是工程权威。测试不写正式工程、不调用正式渲染或音乐模型；核对正式九表/head与固定文件。并发用户/API变更独立记录，不恢复旧基线。
- 音源只在用户指定的 `H:\music\Sound Library`。NAS及 `H:\music\tmp` 只读；不擅自放到其他盘，不搬动活动资源。
- AI、API、SDK、CLI、界面共用服务与事务。音频回调不等待 AI/Python/HTTP/数据库；空MIDI轨保存音色状态，但有内容前不启动静音宿主。
- 当前发布从 `runtime/application-release.local.json` 现场读取；不凭旧提交号断言已部署。不可变 source/web/native/desktop 包及原链接库不覆盖。
- 播放、音量、静音与独奏由原生引擎执行；变更的代码、协议、测试、渲染与实际发声证据分别说明。
- 乐器→型号→变体→确认应用；产品名保留英文，其余显示中文（英文原名）；旧ID/状态身份不因翻译改变。
- 不使用 Computer Use。UI须验证时优先现有自动化/API，不能冒充实际视觉或音频验收。

## 开发、清理与 Git

- 先用 rg 查调用者，再修改共享规则。文档导航见 `docs/README.md`；旧阶段日志与旧编号文档只留 Git 历史，不再持续堆入本文件。
- 测试走 `scripts/maintenance/run-tests.ps1`，临时夹具在 `data/tmp/tests/<run-id>`，退出默认清理；小报告在 `runtime/test-reports`，obj/pdb在 `runtime/build/<target>`。
- 维护记录集中在 `runtime/maintenance/<task>`。不为清理重跑长测，不清模型/环境/正式素材/当前试听缓存或活动任务。
- 删除/移动前验证精确绝对路径与归属，不跟随链接，不改只读属性。保留固定 N2、N7最终验收；历史拒绝路径 `data/t/manual-nz9w4u0m` 与 `runtime/build/codex-protocol-controls` 不绕过。
- Git只暂存本任务文件，不混入其他开发变更。提交者配置只写本仓库，不配置全局身份。
