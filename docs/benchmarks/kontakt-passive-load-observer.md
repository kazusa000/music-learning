# Kontakt 固定队列被动加载观察器

`scripts/instruments/observe_kontakt_full_job.py` 为固定 `kf22/full-h` 提供额外的只读进度诊断。观察器在独立的 `data/maintenance/instrument-intake/kf22/loading-diagnostic-02/full-h-observer` 保存自身计划、进度和 JSONL 采样；它不改 full-h 的计划、队列、计时、回执或既有报告。

观察器在启动时固定 full-h plan SHA、locator 中的 wrapper PID/创建时间/命令，以及 progress 中 worker PID/创建时间/实际解释器路径。只有 progress 明确处于 `validating` 并给出当前条目、且当前条目与 E 盘转换和验证队列相符时，才核对 worker 自有进程树。只采集精确路径匹配的 Kontakt 8 或 `ml_kontakt_probe.exe` 后代的存活状态、累计 CPU 时间、OS 读写字节和 working set；每个进程最多 40 行、总行数最多 100,000。没有匹配的活动项时每 20 秒仅读 E 盘 progress 并等待；活动项时每 5 秒采样。

观察器不读取 H 盘音源内容，不挂调试器、不暂停/终止任何进程，也不写正式工程、服务或目录。CPU/IO增长只能说明操作系统进程活动，不能代替状态、身份、首开/重开音频或入库验收。原固定 full-h 结果仍是唯一验证依据。
