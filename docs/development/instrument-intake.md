# 音源入库规则

资源只安装到用户指定的 `H:\music\Sound Library`；`H:\music\tmp` 与 NAS 作为来源只读。当前 Kontakt 工作继续验证、增量入库和已运行的试听跟随；Ample 与 MODO 后续工作已取消。

## 正式条目

每个预设保留稳定 ID、产品来源、乐器分类、型号、变体及原始英文名。中文仅作为展示投影；产品名保持英文。新导入也沿用乐器→型号→变体→确认应用，不按采样文件名创建大量独立乐器。

正式入库需要真实音频、完整状态、参数恢复与来源身份依据。不得用非零首音代替完整要求，不把元数据准备、队列排入或模型推荐当成验证完成。Kontakt 保持两次独立空机架恢复检查。

已完成的可靠检查点用于恢复进度，不在每次重启时重新读取所有已通过音频。通过项按目录锁和 CAS 追加目录，保留既有条目；失败、运行中和待测条目不发布。检查点复用需要绑定候选、回执、验证实现和来源身份。

## 当前入口

- 原验证：`runtime/kontakt-full-job.local.json`。
- 增量入库：`runtime/kontakt-publication-job.local.json`。
- 试听跟随：`runtime/kontakt-preview-job.local.json`。
- 实际任务数据：`data/maintenance/instrument-intake/kf22`。
- 共享规则：`src/music_learning/kontakt_*`、`instrument_checkpoint_publication.py` 和 `scripts/instruments`。

运行状态须核对 PID、出生时间、实际命令及进度；旧定位文件的 started 字段不证明进程存活。健康任务只轻量观察，不重启、不另开重复批次、不搬动目录；正式工程不作为入库试验对象。

试听是独立阶段，只有实际缓存 ready 才算准备完成。当前失败音色暂不分析或重试；后续是否恢复由用户安排。

来源键位查询与 VST3 状态恢复有不同语义，详见[Kontakt 来源查询](../architecture/kontakt-source-keymap.md)。当前任务细节与过程边界保留在[恢复记录](../benchmarks/instrument-intake-full-recovery.md)，具体固定文件以对应任务 plan/pins 为准。
