# 本地 DAW 重构入口

2026-10-21。用户已选定本地DAW架构，本轮先完成方案与开发顺序；N1引擎编码尚未开始，现有服务未切换。

- [架构决策](architecture/native-daw.md)
- [N0–N7开发计划与验收](plans/native-daw-migration.md)
- [文件夹职责与迁移顺序](development/repository-layout.md)
- [全部当前文档导航](README.md)

前端只负责操作与呈现，工程声音由新的C++本地引擎输出；保留现有SQLite/CAS、统一API/SDK/CLI、模型导入、原曲参考与重建、音源预设及版本规则。目录整理先处理文档入口，源码分组逐步实施，不批量搬正式数据或固定验证文件。
