# 测试与临时文件清理

开发测试统一使用：

```powershell
& scripts/maintenance/run-tests.ps1
& scripts/maintenance/run-tests.ps1 -PytestArgs @('tests/test_release_runtime.py','-q')
```

- 夹具、临时工程、录音及 pytest 缓存只写 `data/tmp/tests/<run-id>`，退出默认清理。
- 小型日志、JUnit、退出码和清理结果写 `runtime/test-reports`。不要为清理重跑长测。
- 明确调查需要时才使用 `-KeepFixtures`，调查结束后跟进清理；失败录音不默认永久积累。
- obj/pdb 与构建中间文件写 `runtime/build/<target>`，不写项目根目录。
- 一次性维护清单和前后核对写 `runtime/maintenance/<task>`。

删除前核对精确绝对路径、活动进程、固定文件和链接边界。清理器不跟随 reparse/junction，不修改只读属性，不为清理停止验证或播放任务。存在用户数据或归属不明的路径先保留。

正式工程、模型、已安装环境、当前音源试听缓存、产品发布、固定 N2 数据、最终 N7 验收和全部活动 Kontakt 路径不归临时测试清理器管理。此前被拒绝的 `data/t/manual-nz9w4u0m`、`runtime/build/codex-protocol-controls` 保留，不换方法绕过。

`clean-workspace.ps1`使用本轮审核白名单，默认只检查，`-Execute`才删除。现有授权清单不得扩成任意目录删除器。

本机已完成的精确清理记录集中在 `runtime/maintenance/cleanup-20261023`、`instrument-intake-retirement-20261023` 和 `daw-cleanup-20261023`。旧过程数字不在当前说明反复追加，历史文档由 Git 保存。
