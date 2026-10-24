# 早期人工验收工具

这里保留 P0–P6 阶段的模型、SDK、重启、对话与备份验收工具，保留原文件名以对应 Git 历史。它们不是日常启动程序，也不是已安装环境的健康检查入口。

在仓库根目录使用 Python 执行新路径，例如：

```powershell
python scripts/probes/legacy/p2_acceptance.py --help
```

这次迁移仅检查命令能从任意工作目录显示帮助，没有运行实际验收流程。这些旧工具中有会提交任务、重启服务或恢复备份的操作，实际使用前必须检查对应参数及隔离数据位置。新的默认验证使用 `scripts/maintenance/run-tests.ps1`；原生专项诊断优先使用相邻的 `engine` 目录。
