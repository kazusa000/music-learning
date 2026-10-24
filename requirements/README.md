# Python 环境依赖

每个环境保留一份 `.in` 输入和一份带哈希的 `.lock`。环境仍安装在本机数据目录，本目录只存依赖描述。

| 文件 | 用途 |
| --- | --- |
| `app.in` / `app.lock` | 工程 API、worker、CLI 和短测试 |
| `native.in` / `native.lock` | CMake、Ninja 与原生构建辅助工具 |
| `transcription.in` / `transcription.lock` | MuScriptor 与 Beat This |
| `reference.in` / `reference.lock` | SheetSage 原曲参考分析 |
| `separation.in` / `separation.lock` | 人声／伴奏分离 |

这次整理只迁移文件和更新注释，保留所有包版本、环境标记及分发哈希，没有升级依赖或重装环境。原始生成命令包含本机路径，因此改为在这里说明如何重新生成。

在仓库根目录、对应的 Python 3.11 环境中维护锁文件，例如：

```powershell
uv pip compile requirements/app.in --python 3.11 --generate-hashes --output-file requirements/app.lock
uv pip compile requirements/native.in --python 3.11 --generate-hashes --output-file requirements/native.lock
uv pip compile requirements/transcription.in --python 3.11 --torch-backend cu128 --generate-hashes --output-file requirements/transcription.lock
```

`reference`、`separation` 按相同 GPU 方式使用各自输入与输出文件。锁文件更新是独立依赖变更，需要检查差异；日常安装使用已提交的锁文件与 `--require-hashes`，不重新解析新版本。模型权重、登录及插件资源分别按配置准备，不包含在 Python 锁文件中。
