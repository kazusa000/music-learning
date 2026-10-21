# 新音源接入：独立预检与宿主兼容记录

2026-10-21。用户授权在 DAW 重构期间先做音源接入准备，产物按项目目录保存；正式工程验收等待用户通知。本轮不修改重构中的引擎/宿主实现、不切换服务、不创建正式音乐工程或修订，也不将尚未通过的音色发布到正式目录。

## 目录与可重复入口

- 本机输入定位：忽略的 `runtime/instrument-intake.local.json`。
- 本轮证据集中在 `data/maintenance/instrument-intake/20261021-batch-v2/`：资源清单、逐调用请求/响应/退出码、状态、报告、独立诊断构建和正式数据前后摘要。
- 第一次 Python 3.11 目录探针因缺少 `Path.is_junction` 失败，保留 `20261021-batch-v1` 小型失败记录；改用 reparse 属性检查，后续四项目录/隔离回归通过。
- `scripts/inspect_instrument_intake.py`：只读清点源目录，独立扫描/检查插件，可选默认音色短句测试。输出只能是 `data/maintenance/instrument-intake` 下的新目录；不覆盖旧证据、不跟随源目录联接，不入库。
- `scripts/probe_staged_ample.py`：仅在测试期间改 `*_inst.xml` 的采样定位，结束原样恢复；不读取用户授权/许可证 XML。外部修改冲突时保留外部内容，不覆盖。
- `scripts/probe_modo_bass.py`：解析原厂预设、按真实 `BassModel` 分组，逐型号做首次/重开音频和型号身份检查；支持指定型号和独立诊断宿主，不发布目录。
- `scripts/build_instrument_probe_host.py`：只读链接固定 runtime library，在本轮证据目录构建诊断宿主。五秒加载等待、可选 128 个空 MIDI 预热块只用于检验异步加载假设；不修改/替换产品源码、固定宿主或插件。

## 资源清点

| 来源 | 本机实际文件 | 当前结论 |
| --- | --- | --- |
| Kontakt Factory Library 2 | 894 NKI、80 NKM、23 NKX；约 37.37 GiB 整目录 | 未发现下载残片；含 13 个 Band 鼓组、90 个 Beats 预设，尚未加载发声验收 |
| Cinematic Studio Strings | 7 NKI、24 NKX；约 45.25 GiB 整目录 | `.nicnt` 当前名为 `.nicnt.txt`，本轮未改源文件；加载/采样引用/奏法待验 |
| Cinematic Studio Solo Strings | 11 NKI、21 NKX；约 38.78 GiB 整目录 | 清点通过，加载/采样引用/奏法待验 |
| Ample Guitar M | 133 ASCF | 已有采样定位指向 staging；扫描存在超时 |
| Ample Guitar LP | 358 ASCF、35 个本机原厂状态文件 | 插件扫描/完整状态读取通过；默认采样定位 NOT FOUND |
| Ample Guitar SC | 358 ASCF、45 个本机原厂状态文件 | 插件扫描/完整状态读取通过；默认采样定位 NOT FOUND |
| MODO BASS 2 | 5 PAK、280 个本机原厂 MB2 预设 | 默认 P-Bass 首次/重开非零；其他型号加载身份未通过 |

7 个清点目录均未发现 `.qkdownloading` / `.part` / `.crdownload`。这仅证明当前目录无这些残片，不能据此判定厂商采样包完整或所有型号已可用。Ample 整个下载目录还含安装包，不能把其总大小当成采样库大小。本轮没有复制这些大资源到正式库。

## 实际兼容与发声结果

### Kontakt

原宿主扫描 portable VST3 在 60 秒内未完成，保留超时证据，自有进程已结束。独立 VST3 SDK 探针用同一 wrapper 成功取得 3 个类，音频组件创建、初始化、32 组立体声总线查询、terminate/release 全通过。因此不能把宿主扫描超时等同于播放器损坏。

下一步定位原宿主扫描/控制器生命周期与 portable wrapper 的交互；在新宿主最终候选上复查。尚未完成 NKI 自动加载，不能将 Factory Library 或弦乐列为可选音色。

官方 Lua API 支持从命令行传入脚本，但需要开启开发选项。参见 [官方设置说明](https://docs.native-instruments.com/online-guides/kontakt-api-reference-manual/en/introduction-and-setup)。本机只做一次隐藏的最小 API 回执探针，40 秒无回执后结束自有进程；未假设开发选项已启用、未修改注册表或操作 GUI。`kontakt-api-execution.json` 保留真实结果。

### Ample

默认 LP/SC 状态能读出，默认短句近零。临时接到 staging 采样目录后：LP 短句仍近零（RMS 约 3.07e-11），SC 的扫描超时；两份 InstDir 配置最终 SHA 均恢复原值。不能把路径补齐描述成已经修好发声，也没有将近零输出放宽为通过。

M 的 SDK 探针可完成创建、初始化、总线查询和 terminate，随后 release 阶段未正常结束，在 25 秒上限终止自有进程；原宿主扫描也超时。后续需区分采样准备、原厂状态与宿主生命周期问题，不能仅按文件齐全入库。

### MODO BASS 2

默认 `'60s P-Bass` 首次 RMS 0.01188、重开 RMS 0.01186，非零且参数恢复通过。

原厂 MB2 文件明确包含 `BassModel`；280 份均完成有类型的状态准备，分成 **22 个型号**，不是 280 把独立贝斯。`modo_bass_presets.py` 按现有完整 AppState 的属性类型生成 VST3 状态，保留未修改字段及 JUCE 私有尾部；版本/包装长度/未知字段/XML/树深度均检查。它目前是**诊断用状态准备器**，不在正式入库路径调用。

`modo-models/report.json` 的 22 个型号代表测试结果：**1 通过、21 身份失败**。21 项虽然有输出，读回型号仍是默认 P-Bass，因此全部不得入库，不能通过改显示名称或只检查非零音频冒充成功。没有在本轮重测全部 280 个变体。

为排除单纯加载等待不足，另用独立诊断宿主对 Studio Upright 和 `'70s J-Bass` 各测：五秒等待仍失败；再加 128 个空 MIDI 处理块后仍失败。前后状态都回读默认型号。证据分别在 `settling-check`、`modo-warmup`，未覆盖原失败。当前只确认“这条状态加载路径未切换型号”，未确证是插件许可、包资源或特定宿主适配造成，不能要求用户据此重下素材。

## 当前接入出口与后续顺序

1. 等新引擎候选稳定后，先复查 Kontakt 和 Ample M 的扫描/释放生命周期；不用正式音乐工程造测试轨。
2. 打通 Kontakt NKI 加载及完整状态保存/恢复，再做 Factory Library 鼓组、CSS/CSS Solo 的真实采样引用与奏法验证。
3. 对 Ample 验加载就绪与非零 PCM；对 MODO 验请求型号和原厂完整状态的实际回读，保留当前失败项作为回归。
4. 通过后再按既有分类、型号→变体→确认、中文（英文原名）和共享试听机制发布。产品名 Keyscape/Pianoteq/Kontakt/MODO BASS 等保留原名。
5. 音源持久资源最终仍只放用户指定的 `H:\music\Sound Library`，迁移时明确清单、逐文件校验和配置更新；本轮只读 staging，不自行改名/移动源样本。
6. 正式工作台应用/换音色/边播边混音验收等用户通知，不提前部署或写正式工程。

## 回归、保护与产物

36 项相关后端测试通过，新增/修改文件 Ruff 通过；包括原有 VST3/Pianoteq/Keyscape 状态兼容回归、越界输出、源目录只读、残片识别、目录联接、配置异常恢复/外部冲突、型号身份/包装/未知字段拒绝等。初次默认音频请求少了插件 id，属于探针缺陷，已修复并补回归，用独立 `default-recheck` 重测；原始失败未覆盖、未计为插件发声故障。

正式九表/head 在各批次前后相同；最终 `protection-final.json` 核对与本轮基线一致，14 项固定文件 SHA 与 N1 pin 一致。现有正式 catalog SHA 未变，仍为原 1,252 款；本轮入库 **0**。没有正式 API 修改、模型调用、NAS 访问、产品构建/服务替换或 Computer Use。

各短句 WAV 在结束后记录路径/大小/SHA 到本轮产物清单，再仅清理本轮自建 WAV，保留小型 JSON 诊断和可复测入口；不碰历史清理被拒路径、原报告或现有音源试听缓存。最终实际清理结果见 `temporary-audio-cleanup.json`。根目录既有 AD2 obj/pdb 按当前并行任务约定保留，不混入提交。
