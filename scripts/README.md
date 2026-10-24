# 脚本入口

| 位置 | 用途 |
| --- | --- |
| 根目录中的 `app.py`、`desktop.py`、`doctor.py`、`music_cli.py` | 日常应用控制，供仓库根目录 `.cmd` 入口调用 |
| `bootstrap_*.py`、`build_*.py`、`deploy_app_release.py` | 环境准备、构建与不可变发布 |
| `model_runtime.py`、`p0_model_worker.py`、`pipeline_adapter.py`、`daw_adapter.py` 等 | 当前服务调用的执行模块，保留稳定位置 |
| `instruments/`、`library/` | 音源验证、入库与资料处理 |
| `maintenance/` | 测试清理、只读源码审查和维护工具 |
| `probes/engine/`、`probes/instruments/` | 原生引擎与插件的专项诊断 |
| `probes/legacy/` | 早期 P0–P6 人工验收工具 |

根目录中仍有少量供既有任务、测试和外部命令引用的稳定适配器。尤其 `p0_model_worker.py` 虽然保留早期名称，仍是当前分析流程调用的执行模块，不属于废弃诊断，不能因文件名将其移走。

新脚本按职责加入已有子目录，避免继续往根目录添加一轮一份的诊断脚本。报告、PID、队列和执行记录写在 `runtime` 或任务规定的数据目录，不写进本目录。
