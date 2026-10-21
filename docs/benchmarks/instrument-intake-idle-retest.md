# H盘空闲后的音源兼容复测

2026-10-21。接续[磁盘繁忙处理](instrument-intake-loading-followup.md)。本轮只做独立维护验证；正式工程、服务部署和音色目录发布均未执行。不能把下列局部通过写成三个音源全部修好。

## 实测结果

| 音源 | 已证实 | 仍未通过 |
| --- | --- | --- |
| Ample LP 4.0.0 | 规范索引暂存目录下，默认音色首次及重开发声；工厂 `Bypass FXs` 的名称回读、参数恢复、首次及重开发声通过 | `80s Solo(ARP)` 和 `Autumn Leaves(Blues)` 均实际无声，两款都选择AGLPFg采样组；后者在恢复后、prepare之前等待10秒仍无声。与采样组有关是待验证假设，未进行35款全量验证 |
| Ample SC 4.0.1 | 正确采样路径下，默认音色与 `Autumn Leaves(Blues)` 工厂变体首次及重开发声通过；维护CLI完整执行也通过 | 未进行45款全量验证 |
| Ample M 3.1.0 | 修复创建时默认禁用主输出的问题，标准双声道总线可用；确实访问了对应采样路径 | JUCE及独立纯VST3 SDK两套宿主均实际输出零。增加等待、实时模式与CC控制未解决，不能判定为采样损坏或许可问题 |
| MODO BASS 2.0.4 | 主音量状态可真实修改；Config PAK索引包含22款模型定义；修正应用资源PAK版本的误拒绝 | 其他型号仍回到 `'60s P-Bass`；标准预设三种封装、内部型号ID、控制器连接及完整隐藏视图生命周期均未解决。用户尚未测试播放器自身切换，版本能力限制只是假设 |
| Kontakt 8 Portable | H盘空闲后固定JUCE宿主扫描通过，实际版本8.10.1；可初始化、可读MIDI unit列表 | 独立程序Lua没有执行回执，日志缺 `Native Instruments/NTK/ni-platform.dll`，并有语言资源解析错误；缺DLL是否唯一原因未定。标准VST3预设数据接口不支持，尚无成功的NKI加载及发声证据 |

默认探针（44100Hz、三档力度、5秒含尾音）：LP首次/重开RMS约0.014966/0.007970，SC约0.002806/0.002775，均非零且参数恢复通过。LP `Bypass FXs` 两次RMS约0.001111；SC工厂变体维护CLI两次约0.005283/0.007249。它们是电气发声与状态证据，不是人耳音质评价。

M的纯SDK探针完成标准初始化、控制器双向连接、音频/事件总线、setActive/setProcessing及345个成功process块；176400帧/双声道WAV仍为零。SDK视图只使用自己创建的不可见父窗口，从未显示、激活、连接用户窗口或模拟输入，未使用Computer Use。

## 共享修正

- `native/include/MainOutputBus.h` 在加载/恢复结束时启用默认禁用的主输出，优先恢复插件公布的布局，必要时协商立体声；已有活动布局、辅助总线和无输出插件不变。`native/audio-host/Main.cpp` 共用此规则；只独立构建新探针，没有替换正式/固定宿主。
- `ample_presets.py` 按插件身份和已测版本处理工厂XML：保留原组件的私有尾部，支持原始XML组件与VstW/FBCh封装，更新长度后包装为标准VST3预设。拒绝身份不符、截断、重复参数和实体声明。首次/重开都必须回读原预设名称、实际采样数据库和全部请求参数，而不只是两次状态彼此一致。对已经记录的LP/SC通过项做该规则复核亦通过；LP无声项虽然状态匹配，仍按实际无声失败。
- `probe_staged_ample.py` 的manifest可选 `factory_xml`，共用上述适配与原来的完整音频探针，记录原始失败而不自动改预设；`--native-host` 与 `--scan-timeout` 支持独立候选复现。
- `build_instrument_probe_host.py` 默认不增加人工暂停，实验性 `--settle-ms 0..30000` 必须显式提供，`--settle-phase` 区分恢复结束/prepare之前与prepare之后。旧诊断宿主在prepare后固定暂停5秒时，LP同一状态曾输出零；固定原宿主及新无暂停宿主均实际非零。旧失败保留，不能归罪于音源。其机制仍未确定。
- `modo_bass_resources.py` 按资源角色识别PAK版本：应用资源允许实际出现的v1/v3，其他角色仍需v3。路径/八字节头预检仍不等于成员或可用型号验证。另一次有界只读索引诊断确认Config的25项中含22个模型定义，文件范围均有效，没有整包解压或改PAK。

## 路径、证据与复现

本轮证据统一在 `data/maintenance/instrument-intake/20261021-idle/`：

- `ample-release/report.json`：新无暂停宿主串行完成M、LP、SC，保留M失败。
- `factory-switch/`、`factory-switch-clean-lp/`、`factory-cli/`：工厂预设原始诊断与CLI复测；失败不覆盖。
- `factory-lp-finger/`、`factory-lp-pre-prepare/`：LP指弹变体直接加载与处理前等待10秒对照，均实际为零。
- `factory-semantic-review.json`：不重新发声，用原始首次/重开状态核对工厂的名称、采样组及每项参数，不能替代原音频检查。
- `ample-m-sdk-audio/`：独立SDK代码、运行日志和零输出证据。
- `modo/state-framing/`、`modo/view-probe-*`、`modo/*-index.json`：状态写入、生命周期和索引范围证据。
- `kontakt/host-scan/`、`kontakt/*-api-execution.json`：真实扫描与Lua超时；自有进程文件加载路径记录在 `file-trace/`。
- `kontakt/api-route-assessment.md`：标准SDK接口实测。组件实现IUnitInfo，16个MIDI Channel各有128个通用程序名，但programDataSupported均为false；IProgramListData/IUnitData均kNoInterface。它们不能当作NKI路径加载接口。日志完整后的厂商退出异常0xC000000D仍按异常保留。

LP规范索引暂存仍在用户指定H库的 `.staging/20261021-ample/`。Kontakt独立程序测试准备在 `.staging/20261021-kontakt-runtime`：8465份程序/资源硬链接，19份独立UserData副本约1MB；不是重新复制2GB，也不是最终入库。开发开关和关闭自动更新只改该副本，不写全局设置。源UserData在准备时和后续检查使用同一清单比较；之前运行源独立程序可能产生其自己的设置写入，不能声称整轮源UserData从未变化。

使用已记录的独立清单复现单款工厂变体：

```powershell
& data/envs/app/Scripts/python.exe scripts/probe_staged_ample.py `
  --manifest data/maintenance/instrument-intake/20261021-idle/factory-cli-input.json `
  --output data/maintenance/instrument-intake/<新的独立目录> `
  --native-host data/maintenance/instrument-intake/20261021-idle/bus-host-no-settle/instrument_probe_host.exe
```

不得复用已有输出目录覆盖原报告。`InstDir`只在自己创建的探针期间临时改变，结束精确恢复；外部修改则拒绝覆盖。未读取许可XML、修改插件、模拟许可成功、打开正式工程或使用NAS。

## 验证范围

49项相关后端测试、修改Python文件Ruff通过；主输出原生回归以Release/NDEBUG实际执行五种总线场景通过，独立候选编译及LP/SC真实发声通过。没有在并发引擎N6源码上宣称全项目验收，也没有部署新的主输出规则到正式服务。

最终 `protection-final.json` 回读确认：正式九表/head、14项固定文件SHA、正式catalog SHA均与本轮开始一致；三款Ample的采样路径配置精确恢复；Kontakt源UserData19项自准备基线以来SHA相同。新入库0项。未改并发引擎源码、AD2根部obj/pdb、原始报告或历史拒绝清理路径。

剩余待办是LP失败变体的共同原因、M实际无声、MODO型号切换和Kontakt NKI控制链。仅通过的独立探针不发布库存，后续仍须统一型号/变体/双语与试听流程。

用户没有完整Kontakt平台组件，要求尝试替代或跳过。已有VST3接口的替代路线已经实测；没有可用NKI装载方法。当前暂缓Kontakt入库，保留现有程序与大音色库，不要求重新下载大库，也不创建一个同名空壳DLL伪装成功。以后补齐可用平台运行时或获得真实可控加载接口，再接回既有流程。
