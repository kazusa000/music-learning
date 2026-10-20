# Pianoteq 9.1.2 入库与换音色

2026-10-20：Pianoteq 已接入统一乐器目录。797 款原厂预设完成完整状态准备和分类；其中 **34 款、22 类**通过首次及重开发声、参数恢复和完整声源状态身份检查，已可选择。其余 **763 款尚未逐款发声验证，不发布为可选音色**。原 Keyscape 455 款保留，正式 HTTP SDK 回读合计 489 个唯一条目。

插件、引擎、完整 FXP、VST3 预设、库存和验证缓存均在 `H:\music\Sound Library\Pianoteq 9.1.2`。本项目配置仅追加该目录下 `VST3` 的插件允许路径，没有安装到 C 盘或删除 C 盘文件。已重启本项目服务加载配置与后端规则，保留应用窗口。

## 预设加载修复

此前导出的标准 VST3 预设包含 `LEAN-PRESET` 引用。当前加载路径返回成功，但实际仍是默认钢琴；更改等待时序、直接载入 IComponent、改变程序数和普通 FXP 封装的对照都未解决，不把这些实验表述为成功适配。

现在使用原厂程序公开的 JSON-RPC：按原厂名称 `loadPreset`，回读名称及乐器模型，等待参数计算完成，再 `savePreset` 保存完整 FXP。仅为该子进程设置 H 盘 AppData，并使用 H 盘独立偏好文件。实际回读的用户预设路径确认为 H 目录。

`pianoteq_presets.py` 将完整程序装入真实 VST3 实例捕获的单程序 bank，保留其其余路由与 MIDI 映射。适配明确限定已实测的 9.1.2/class ID/容器布局；检查边界与大小，拒绝精简引用、未知布局和损坏文件。没有修改插件二进制、原生宿主或正式常驻链接库。

入库和后续捕获/状态导入均核对实际 IComponent 中的**完整程序 SHA**。首次与重开均须与选定完整 FXP 一致，防止再次把默认钢琴记作电钢琴或吉他。验证结果复用现有持久队列和证据缓存，校验宿主、代码、插件、资源及侧车引擎；已入库状态可走现有快速捕获，不重复生成 WAV。缓存命中也新增侧车引擎检查。

## 乐器映射与验证

沿用原厂组别及模型注册表。新增 Syngular 原厂子组映射为合成键盘、贝斯、主音、铺底、铜管、打击、复音及序列；不从任意第三方文件名猜分类。完整 797 款当前无待分类项。

| 乐器 | 已验证可选数 |
| --- | ---: |
| 声学钢琴、原声吉他、钟琴、钢片琴、击弦古钢琴 | 各 2 |
| 电钢琴 | 3 |
| 竖琴、拨弦古钢琴、钢鼓 | 各 1 |
| 拇指琴、马林巴、玩具钢琴、颤音琴、木琴 | 各 2 |
| 合成键盘、贝斯、主音、铺底、铜管、打击、复音、序列 | 各 1 |

第一次 25 款验证为 24 通过/1 失败，原队列保留。失败的 Glockenspiel Original 在默认 48/60/72 测试音符下 RMS 为 0；上下两个方向的真实音高扫描确认其可发声音域为 MIDI **77–108**。修正该类原厂音域映射，保留 a-la-piano 扩展版本的宽音域；没有降低静音门槛或移调用户音符。新队列加入普通古典吉他及 Syngular 类别，**34/34 通过，0 失败**，耗时约 188 秒，仅为本机本次观测。

原批：`verification-runs/20261020-full-state-intake-v1`。
修正后：`verification-runs/20261020-full-state-intake-v2`。
库存：`full-factory-inventory.json`；完整预设：`Full VST3 Presets`；原厂 FXP：`AppData/Modartt/Pianoteq/Presets/Factory Prepared`。

维护入口：

```powershell
# 默认验证各乐器代表和精选变体；库存存在时复用完整状态准备。
data/envs/app/Scripts/python.exe scripts/intake_pianoteq912.py --exports data/maintenance/pianoteq912-20261020/exports
# 后续可显式逐款验证全部库存；本次没有启动这项长批次。
data/envs/app/Scripts/python.exe scripts/intake_pianoteq912.py --exports data/maintenance/pianoteq912-20261020/exports --all
# 仅在宿主、代码及输入指纹一致时续未完成队列；不重绑旧结果。
data/envs/app/Scripts/python.exe scripts/intake_pianoteq912.py --resume "H:\music\Sound Library\Pianoteq 9.1.2\verification-runs\队列名"
```

完整状态已经自包含上述原厂程序，日常捕获和播放不读取 E 盘原始精简导出。维护入口不提交音乐工程任务；同库用 OS 文件锁互斥，发布目录时另取总目录锁并重新读取，保留其他库条目。

## SDK 切换与实际常驻发声

实际流程还发现一项共享规则错误：MKI 的 `Eff 1 DelPol` 原厂回读值是 0.5，但插件报告两个离散步骤。直接套用 0/1 网格会拒绝插件自己的合法保存值，导致 `patch_plugin` 换音色失败。

`daw.plugin_metadata` 现在保留与原生描述一致的原值；用户新写入仍受原参数 ID、类型、范围、可自动化性及离散步骤校验。此规则用于所有插件，没有按 Pianoteq 名称放行或改写原厂值。新增回归验证原值可保留、非法新值和未知 ID 仍被拒绝。

`scripts/probe_pianoteq_intake.py` 在独立数据库中，经 SDK 序列化与进程内 ASGI HTTP API，运行真实 `instrument.capture → edits.apply → playback.live.*`：钢琴 NY Steinway D Classical → 电钢琴 MKI Basic → Classical Guitar，保持同一音轨和 610 个音符。每个音色都读取完整 **305 秒/13,450,500 帧**常驻 PCM，前中后段非零，三者 PCM SHA 不同：

| 音色 | 快速捕获秒数 | 常驻 PCM RMS | 峰值 |
| --- | ---: | ---: | ---: |
| NY Steinway D Classical | 0.766 | 0.05303849 | 0.27689031 |
| MKI Basic | 0.844 | 0.06878564 | 0.23157957 |
| Classical Guitar | 0.750 | 0.05681626 | 0.26315805 |

独立库只有 3 个捕获任务，0 渲染任务、0 WAV，捕获均复用已验证的完整状态。这里证明实际原生供声和程序接口，不是声卡试听、浏览器视觉检查或人工音质评价；测试曲为独立合成的 305 秒音符序列，没有播放或修改正式录音。真实网络 HTTP SDK 另只读核对正式 34+455 个条目。

最终完整后端回归 **197 项通过**，修改文件 Ruff 及 Git diff 空白检查通过。没有修改 API 契约或数据库迁移，也没有以单元测试替代上面的真实插件检查。

首次独立流程记录了上述离散默认值失败，修正规则后使用新独立数据库完成三轮，未覆盖失败夹具或把首次当作通过。相关小型证据汇总在忽略目录 `data/maintenance/pianoteq-admission-20261020`。

## 正式工程及文件边界

本次前后只读核对一致：正式工程 `4012a10f50574b2395d4ec4a9d8f27b5`，head `1c71ff13ccb3415ea5677785dd5ddfd2`，快照 SHA `7c3ce678e52fee15b9c205628fc890170586647a2499e82ed6ba815618c88dc1`，总任务 20。没有向正式工程提交捕获、编辑、渲染或模型任务；没有访问 NAS，也没有恢复旧删除音源库。新安装包 SHA 仍为 `225110705d0fc7a7a1847b2ec8eabc0329505c164053e63f9a5ed6bbd2b026a2`。

Keyscape 已结束的原批/重试、固定宿主、插件、样本及报告没有被替换；正式常驻宿主与固定链接库没有重建。既有自动跟进保持暂停，没有启动新的全量后台任务。

小型报告已复制归档。对本次新建 `pianoteq-state-v2`、两份 `pianoteq-live*` 独立夹具和两份音域扫描 WAV 的清理，先检查绝对路径在 E 项目 data 下、无 reparse point，约 79.3 MiB 逻辑文件；删除命令执行前仍被自动审批以 `blocked by policy` 拒绝。所有文件保留，没有释放空间或换工具重试；此前已经被拒的目录也未动。H 库正式预设、完整状态和缓存必须依赖的验证音频继续保留。
