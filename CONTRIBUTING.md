# 开发入口

先看[源码准备](docs/development/source-checkout.md)、[目录职责](docs/development/repository-layout.md)和[统一 API](docs/development/unified-api.md)。项目尚在整理公开发布方式，当前没有选定项目许可证。

## 修改放在哪里

- 界面放在 `apps/web/src`；共享契约从 `src/music_learning/contracts.py` 生成到 `packages/contracts`。
- 工程行为在 `src/music_learning` 的同一套服务和事务中实现，API、AI、CLI 和 UI 共用。
- 音频执行放在 `native`；回调不等待 AI、HTTP、Python 或数据库。
- 新维护脚本放在 `scripts/maintenance`，原生诊断放在 `scripts/probes/engine`，音源管理放在 `scripts/instruments` 或 `scripts/library`。
- 早期 P0–P6 人工验收工具放在 `scripts/probes/legacy`，不作为日常启动或默认测试入口。

## 验证与提交

通过 `scripts/maintenance/run-tests.ps1` 运行与变更相关的短检查，夹具写 `data/tmp/tests/<run-id>` 并在退出时清理。构建产物写 `runtime/build/<target>`，小报告写 `runtime/test-reports`。不要在正式工程中调试，也不要为文档整理重跑音乐模型、音源全量验证或整曲长测。

只提交本次修改的源码、配置模板、锁文件和文档。保留现有依赖版本与哈希，迁移路径时更新调用方；修改行为应在共享规则处完成。不要提交自己的工程、音频、数据库、令牌、模型权重或插件二进制。

共享前可以执行只读检查：

```powershell
python scripts/maintenance/audit-source-tree.py --output runtime/maintenance/source-audit/report.json --history-paths
```

报告只记录文件路径和规则名称，不输出匹配到的密钥内容。历史检查只检查历史文件名，不扫描历史 blob，也不改写历史；完整限制见[开源准备记录](docs/development/open-source-preparation.md)。
