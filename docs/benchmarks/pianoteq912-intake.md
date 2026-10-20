# Pianoteq 9.1.2 接入检查

2026-10-20，用户提供 `H:\music\tmp\pianoteq_setup_v912.exe`。直接提取到指定库下独立目录 `H:\music\Sound Library\Pianoteq 9.1.2`，没有运行安装器、覆盖 9.1.0、修改系统安装或发布可选音色。Keyscape 正在运行的批次及其固定宿主、验证实现、插件、样本和报告保持不动。

## 实际结果

- VST3 扫描确认 Pianoteq 9、版本 9.1.2、Windows x64 乐器；实际模块路径为新目录内的 `VST3\Pianoteq 9.vst3`、`Pianoteq 9.vst3plugin` 和 `ptq912w.dll`，没有误载旧版。
- 自带程序 `--headless --prefs ... --export-presets-filter builtin --export-vst3-presets ...` 退出 0，完整导出 797 个预设。偏好使用独立文件。
- 钢琴 NY Steinway D Classical、电钢琴 MKI Basic、古典吉他 Classical Guitar 均通过首次及重开发声和参数恢复检查，但三者回读同一个状态 SHA `be6c98e67ccb9ecd731055d846158be38d7d04f5ceac739f8a4f19455428280b`。MKI 和吉他首次 WAV SHA 完全相同；钢琴仅有微小数值变化，不构成换乐器成功。
- 对同一 MIDI，9.1.2 自带程序按名称选择三种乐器，三份 PCM 摘要、长度及电平不同，说明自带程序的切换路径可产生不同音频。尚无人工音质判断。
- 新导出的三个标准预设包含不同的 Comp 数据，含各自的原厂名称和模型。以完整 JUCE IComponent 状态直接加载 MKI 的对照仍返回相同状态。这将问题缩小到当前 VST3 预设/状态加载链路，尚未确定是包装器还是宿主时序等具体原因；不能断言整包损坏，也不继续要求用户寻找第三个版本。

| 选择 | VST3 首次 RMS | 自带程序 RMS | 自带程序 PCM SHA 前缀 |
| --- | ---: | ---: | --- |
| NY Steinway D Classical | 0.01147795 | 0.04722557 | ebfccd3c7300dc75 |
| MKI Basic | 0.01147794 | 0.04655227 | 0477762287cf7f4f |
| Classical Guitar | 0.01147794 | 0.04079361 | ff126f03e2f30062 |

两个路径的增益设置与尾音不同，表中电平不作音质或响度优劣比较。后续须修复共享适配并重新验证不同乐器身份、状态、音频及重开，再入库；自带程序输出不能冒充 VST3 通过。

## 复现与边界

小型摘要、原生请求/响应及逐项指标位于忽略目录 `data/maintenance/pianoteq912-20261020`。使用当前宿主，没有修改原生代码或运行中的 Keyscape 验证逻辑。最初一次探测误用了 `vst3_preset_path`，被契约拒绝；随后改为现有 `preset_path` 后完成上表六次真实探测，不将参数错误当作插件故障。直接 H 盘导出曾超过 90 秒，改到本项目临时输出后完整导出成功；半成品不作成功结果。

正式工程 head 与快照前后相同：`451e81a2fddb4230b1d1f30008234fae`、`fdfc784ea7e684b3c9a1b6484611877f21854413cf26857649030af0d0541f42`。期间总任务数由 19 变 20，新任务为并发成功的 `instrument.capture`；本检查只使用原生独立调用和只读数据库回读，未提交工程任务，不将总数变化伪装成一致。

安装包 SHA：`225110705d0fc7a7a1847b2ec8eabc0329505c164053e63f9a5ed6bbd2b026a2`。
新 VST3 SHA：`c79e057133fd4dc2791dc1c2950e89181d9c81b7b4bc811f0ec88e1895465f67`。
新引擎 SHA：`218ba590b07877d5a80d1e48f2d61f90ceda7971a02e5d34ddbc8ecb0d5678a5`。

旧 innoextract 不识别此 Inno Setup 6.5.2 包。使用维护者的 [innounp 2.67](https://github.com/jrathlev/InnoUnpacker-Windows-GUI) 直接提取文件，没有执行安装脚本。工具及音源不进 Git。本次没有实现代码变更，不用构建或单元测试代替上述真实插件对照。

收尾尝试将 E 盘完整导出归并到新 H 目录、删除仅本次生成的半成品/多余 VST2 副本及 16,423,288 字节探测 WAV；整个命令在执行前被自动审批以 `blocked by policy` 拒绝，所有这些临时文件仍在，没有释放空间或换工具重试。完整 797 导出仍位于本次 E 盘证据目录 `exports`，H 盘同名导出目录只有早期超时的半成品，不能用于入库；新插件文件本身已完整提取。
