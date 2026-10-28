# 文档导航

## 使用说明

- [工作台与播放](guides/workspace.md)：新建、导入、原曲与重建、音轨与卷帘、混音。
- [音源与试听](guides/instruments.md)：乐器、型号、变体、中文显示与应用。
- [AI 与笔记](guides/assistant.md)：会话、模型、范围、编辑和工程笔记。
- [工程与版本](guides/projects.md)：保存、回退、导出和永久删除。

## 架构

- [本地音频引擎](architecture/native-daw.md)
- [音频图与插件执行](architecture/native-graph-runtime.md)
- [创作工作区](architecture/composition-workspace.md)
- [工程加载进度](architecture/workspace-loading.md)
- [AI 会话设置](architecture/ai-conversation-controls.md)
- [风格库](architecture/style-library.md)
- [虚拟乐器库](architecture/instrument-library.md)
- [Kontakt 来源与键位查询](architecture/kontakt-source-keymap.md)

## 开发与维护

- [贡献入口](../CONTRIBUTING.md)与[源码准备](development/source-checkout.md)
- [开源准备](development/open-source-preparation.md)
- [目录职责](development/repository-layout.md)
- [依赖环境](../requirements/README.md)与[脚本导航](../scripts/README.md)
- [模型环境的空间共享](development/environment-storage.md)
- [统一 API / SDK / CLI](development/unified-api.md)
- [音源入库规则](development/instrument-intake.md)
- [构建与发布](development/native-release-operations.md)
- [测试与临时文件清理](development/test-cleanup.md)
- [插件允许目录](development/instrument-plugin-allowlist.md)

## 验证记录

- [源码目录与共享检查](benchmarks/source-layout-preparation.md)
- [源码公开发布与许可检查](benchmarks/source-publication.md)
- [已完成验证任务的历史清理](benchmarks/history-cleanup-20261028.md)
- [旧副本清理与模型环境去重](benchmarks/storage-dedup-20261028.md)
- [原生引擎验收摘要](benchmarks/native-engine-completion.md)与[详细记录](benchmarks/native-engine-n7.md)
- [播放与混音操作验证](benchmarks/playback-mixer-workflow.md)
- [AI 编辑工具验证](benchmarks/ai-edit-tools.md)
- [Kontakt 当前任务记录](benchmarks/instrument-intake-full-recovery.md)
- [Kontakt 索引处理](benchmarks/kontakt-derived-index-postload.md)

旧阶段规划、已废弃的排查文档和旧版 README 已从工作目录移除，可从 Git 历史查阅。运行进度和版本以本机定位文件及实际进程为准，文档中的历史快照不作实时统计。
