# 模型环境的空间共享

转录、原曲参考和分离仍使用 `data/envs/transcription`、`reference`、`separation` 三套独立环境。各自依赖由原 `.lock` 固定；当前 NumPy 版本分别为 2.4.6、1.24.3、2.2.6，不能简单合成一个环境。

## 相同内容只存一份

同一 NTFS 卷上的硬链接让多个普通文件路径使用同一份数据，删除其中一个路径不会使其他路径失效。共享的不是目录联接或指向另一个环境的符号链接；环境路径、包版本和导入位置保持原样。语义见 [Microsoft 硬链接说明](https://learn.microsoft.com/en-us/windows/win32/fileio/hard-links-and-junctions)。

`scripts/maintenance/deduplicate_environments.py` 仅处理安装记录中的普通文件：

- 必须属于相同包、相同版本和相同 RECORD 相对路径，至少 1 MiB；RECORD 的 SHA-256 和文件实际 SHA-256 均须一致。
- editable 安装、字节码、小文件、只读文件、符号链接及所有 reparse 目录不参与共享；不改变 NumPy 等不同版本的依赖。
- 先生成计划并固定计划摘要。执行时再次检查版本、路径、字节数和内容摘要，确认没有模型环境进程运行。
- 在目标目录建立临时硬链接，再原子替换已核对的旧文件。链接或替换失败时保留原文件，不创建环境备份、不改只读属性。
- 共享库不可手工原地修改，原地写入会影响所有链接。依赖维护沿用锁文件安装、替换文件的流程；需要手工改包时先让目标文件恢复独立副本。

维护入口示例，在普通宿主 PowerShell 的项目根目录执行：

```powershell
& data/envs/app/Scripts/python.exe scripts/maintenance/deduplicate_environments.py plan --output runtime/maintenance/<task>/environment-plan.json
Get-FileHash runtime/maintenance/<task>/environment-plan.json -Algorithm SHA256
& data/envs/app/Scripts/python.exe scripts/maintenance/deduplicate_environments.py apply --plan runtime/maintenance/<task>/environment-plan.json --sha256 <计划摘要> --output runtime/maintenance/<task>/environment-result.json
```

每次使用新的报告路径，原报告不覆盖。计划与操作记录只写本机维护目录。此检查不接入应用启动、进工程或换预设流程，不增加日常等待。

## 后续安装

`bootstrap_models.py` 显式使用共享的 `UV_CACHE_DIR` 和 `uv pip sync --link-mode hardlink --require-hashes`。安装仍按三个独立锁文件进行；这能让后续同内容依赖复用缓存的数据，见 [uv link-mode](https://docs.astral.sh/uv/reference/settings/#link-mode)。不为去重重新下载、升级或重装现有依赖。

按路径累加的目录大小会重复计算共享文件。统计节省量时同时记录逻辑长度、宿主文件身份和卷实际可用空间变化，不能将替换了几个路径的总长度都算成释放量。Windows 下应读取实际文件的 `stat` 身份，不能依赖 `DirEntry.stat` 的缓存身份字段。
