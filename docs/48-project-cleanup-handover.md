# 旧导入数据清理交接

2026-10-21。用户要求给出剩余删除路径和PowerShell命令，由用户亲自执行。当前范围仅为 `E:\Intrest\music-learning`；没有执行删除，也不恢复受阻目标。

## 项目占用

本轮跳过重解析点，按NTFS文件ID去重统计项目内文件数据约28.55GiB。这不是精确簇分配大小；同一文件在项目外仍有硬链接时，删除项目内路径不能释放该数据。

主要逻辑大小：旧SheetSage环境7.225GiB、当前transcription环境7.051GiB（两者大量共用硬链接）、当前large权重5.090GiB、旧四模型3.791GiB、常驻音轨缓存3.386GiB、构建runtime3.021GiB、旧工程CAS1.909GiB、音色试听缓存1.446GiB、本轮验收目录1.158GiB。不是每项都可删除。

下表白名单加2782个旧CAS文件合计逻辑约15.1GiB（白名单内去重约15.03GiB）。按选中路径数量与NTFS硬链接总数核对，预计实际释放约7.15GiB；最终以执行前后空闲空间为准。

## 明确删除清单

所有下列路径均以 `E:\Intrest\music-learning\` 为前缀：

| 路径 | 逻辑GiB |
| --- | ---: |
| data\models\demucs-cbc8a9b1a870 | 0.078 |
| data\models\mert-d8ba1c745e73 | 2.356 |
| data\models\muscriptor-f32236969308 | 1.144 |
| data\models\sheetsage-b83c3d79a6d9 | 0.213 |
| data\envs\sheetsage | 7.225 |
| data\app-data\backups\projects-1792520318088701500.sqlite3 | 0.405 |
| data\maintenance\large-beats-migration\accept | 1.137 |
| data\t\large-beats-final | 0.213 |
| data\t\large-beats-tests-v1 | 0.212 |
| data\t\large-beats-tests-v2 | 0.212 |
| data\t\stream-weights-tests | <0.001 |
| data\t\stream-weights-final | <0.001 |

旧工程CAS只删除 `data\maintenance\large-beats-migration\project-cas-cleanup-plan.json` 中逐项列出的2782个文件（1.909GiB），不递归清空objects。该清单读取原工程已经删除的project_assets记录生成；执行前要求正式库仍无新工程，并再次逐文件核对大小和SHA。

## 手动命令

已提供固定白名单脚本，默认只预览：

```powershell
& 'E:\Intrest\music-learning\scripts\cleanup-retired-import.ps1'
```

实际删除由用户执行：

```powershell
& 'E:\Intrest\music-learning\scripts\cleanup-retired-import.ps1' -Execute
```

脚本仅停止和恢复本项目应用服务；不停止独立Pianoteq验证器。检测新正式工程或活跃项目任务会停止，服务端口仍在监听会停止。路径固定、父目录/子项联接拒绝、CAS证据变化拒绝；打印净释放空间与未删除条目。部分删除后出错不回滚文件，保留具体异常，应用在finally中尝试恢复。

保留当前large/Beat This权重、transcription/app环境、正式数据库、插件/采样、音色试听缓存、原始模型输出及小型验收报告；不扩大到历史拒绝目录、H音源、NAS或其他项目。

## 本轮验证

PowerShell语法解析和默认预览均通过，预览列出12个固定路径及2782个精确CAS目标；没有运行-Execute。占用证据在忽略的 `data\maintenance\large-beats-migration\storage-audit.json` 和 `cleanup-size-audit.json`。磁盘剩余会变化，不能将预估当作已释放。
