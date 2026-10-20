# Pianoteq 剩余预设全量验证

用户于 2026-10-21 授权剩余 763 款全部自动验证，通过项立即入库并加入试听预热，保留原 34 款和 Keyscape 455 款。

本文件为本批结果位置；完整结束后，归属明确的监督进程会在分支与本文未被外部改动时写入真实统计并单独提交。未得到终态前，不能把 763 款写成全部通过。

当前请求入口为忽略的 `runtime/pianoteq-full-job.local.json`，其中 `request` 指向 H 指定库内 `verification-jobs` 的唯一请求。运行状态、PID/创建时间、心跳、失败 JSON/CSV、正式九表快照与 Git 结果保存在同一任务目录；实际逐款状态读取请求所指 `verification-runs` 的 `queue.json` 和 `progress.json`。

维护入口、恢复规则、双语与预热衔接见 [Pianoteq 接入文档](../41-pianoteq-integration.md) 的后续全量段。
