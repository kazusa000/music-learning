# 目录职责

| 目录 | 用途 |
| --- | --- |
| `apps/web` | React 工作台与界面测试 |
| `native` | 桌面外壳、设备引擎、隔离插件和原生测试 |
| `src/music_learning` | 工程服务、持久化、AI、音源与模型任务 |
| `services` | API、worker 入口与数据库迁移 |
| `packages` | 生成契约和 SDK 接入说明 |
| `scripts`、`tests` | 启动、构建、维护工具与回归 |
| `config`、`resources` | 固定依赖、无凭据配置及产品资源 |
| `docs/guides` | 当前使用说明 |
| `docs/architecture`、`docs/development` | 当前架构、开发与维护规则 |
| `docs/benchmarks` | 仍需保留的验收与当前任务记录 |

## 本机数据

- `data/app-data`：正式数据库、素材、任务、试听缓存和桌面 profile。
- `data/envs`、`data/models`：已安装依赖环境与模型。
- `data/maintenance`：独立验证的数据与证据；当前音源任务在 `instrument-intake/kf22`。
- `data/tmp/tests/<run-id>`：测试夹具，退出默认清除。
- `runtime/build/<target>`：构建中间文件。
- `runtime/test-reports`：小型测试日志与结果。
- `runtime/maintenance/<task>`：精确维护清单和前后核对。
- `runtime/*-releases`、`runtime/engine-bundles`：不可变产品发布。
- `runtime/application-release.local.json`：当前激活发布指针。

音源库保留在用户指定的 H 盘路径。数据、环境、固定发布和活动任务不随文档整理搬迁。根目录不再放 obj/pdb、临时录音或一轮一份的状态文档。旧阶段说明由 Git 历史保存。

测试与清理见[测试收尾](test-cleanup.md)，构建与激活见[发布操作](native-release-operations.md)，音源任务见[入库规则](instrument-intake.md)。
