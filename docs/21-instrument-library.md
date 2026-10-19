# 免费音源库、预设选择与声部映射

2026-10-19。针对用户指出的 290–305 秒重建缺少配器层次，补齐实际采样库与宿主管理预设；不能再把非鼓声部统一映射到 Surge INIT。原始转录、旧工程和旧试听不覆盖。

## 本机工具箱

样本库在本机 H: 的 `H:\music-learning-instruments\libraries`，不是 NAS。固定仓库 revision、下载摘要与许可见 `config/instrument-sources.json`；原始许可文件保存在库内，音乐/库文件不进 Git。

| 来源 | 用途与边界 |
| --- | --- |
| [Salamander Grand Piano](https://github.com/sfzinstruments/SalamanderGrandPiano) | 真钢琴多力度采样；CC BY 3.0。ARIA 扩展以实际 sfizz 测试为准。 |
| [jRhodes3d](https://github.com/sfzinstruments/jlearman.jRhodes3d) | Rhodes 电钢；作者对音乐创作允许自由使用，样本再分发为 CC BY-NC 4.0、控制文件 CC0，按下载 LICENSE 保存，不能把所有权利统称 CC0。 |
| [Shinyguitar](https://github.com/sfzinstruments/karoryfer.shinyguitar) | 空心吉他的拾音器/麦克风信号，CC0；不能冒充专门的尼龙吉他、钢弦吉他或已还原失真音箱链。 |
| [Swagbass](https://github.com/sfzinstruments/karoryfer.swagbass) | 电贝斯采样，CC0；奏法候选仍需核对。 |
| [Sonatina Symphonic Orchestra 4.0](https://github.com/peastman/sso) | 弦乐、合唱、木管、铜管、竖琴、管风琴、羽管键琴与有音高打击乐；保存 CC Sampling Plus 1.0 原许可。 |
| [Naked Drums](https://github.com/sfzinstruments/WilkinsonAudio.NakedDrums) | 真实多采样鼓组，CC BY 4.0；GM 键位须按实际映射检查，不能把所有 128 键都宣称有样本。 |
| 已安装 Surge XT 1.3.4 | 3,008 份原厂/附加 FXP 文件建立索引；适合电子贝斯、主音、铺底和合成拨弦。大多数文件尚未逐个实测，不能把文件数量当成全部可用音色数量。 |

sfizz 1.2.3 VST3 实际播放 SFZ；Surge XT 1.3.4 用固定版本 FXP 适配。共 3,031 个预设条目，其中 23 个采样预设和 5 个合成预设完成精选验证，合计 28 个。未实测预设可以提交 capture 尝试，只有实际成功才可应用；自动映射只选择已验证预设。

## 安装与验证

首次已授权下载使用 `scripts/bootstrap_instruments.py --root <全新本机目录> --record-hashes` 记录固定 commit 归档的首次摘要；后续不加该选项，必须匹配已提交摘要。脚本不执行压缩包中的代码，不写系统插件目录，不使用音乐库/NAS 作为目的地。

`scripts/build_instrument_catalog.py --verify --validation-name validation-<新版本>` 建立 catalog，并从**依赖闭包的独立打包副本**加载预设、实际渲染及重开。保留目录结构，解析 SFZ 宏、default_path、逐行及同一行多个 include；绝对/越界/缺失依赖拒绝，不能只在原安装目录验证。

Shinyguitar wrapper 显式提供原 bank 的 sample_dir 和拾音器/麦克风 CC；管风琴 wrapper 显式开启音栓，避免全部音栓关闭产生静音。sfizz 初始化先处理无音符块并做组件状态回读，同步 SFZ set_cc 的实际默认值，再应用宿主覆盖并保存。控制器默认零值不能覆盖已生效的库设置。

原生 Surge 适配只接受固定 1.3.4、CcnK/FPCh/cjs3/sub3 头和正确 chunk 长度；去掉 60 字节 FXP 外层后经 JUCE VST3 component 恢复，并保存完整新状态。其他插件/格式拒绝，不盲改未知状态。

## API、SDK、CLI 与对话

所有能力由同一 Service/API/SDK/CLI 提供，无需点插件窗口：

| 操作 | 作用 |
| --- | --- |
| `instruments.list` | 按名称、用途、family 查询/分页；区别文件已安装和 audio_verified。 |
| `instruments.recommend` | 指定工程/revision/track 的音色建议、音域、时值、越界事件数、替代说明及候选状态。 |
| `jobs.submit kind=instrument.capture` | 只接收已安装 preset_id；固定依赖清单，后台校验、加载、试奏、状态重开，把资源/完整状态/描述存入工程 CAS。 |
| `patch_plugin.preset_capture_job_id` | 应用同工程成功 capture 的预设；保留目标实例 ID、轨道、链位置与旁通，要求全时段 structural 范围。 |
| `production.query` | 回读实际 preset_id/name；preset_search 查询音色库，capture_job_id 查询捕获状态。 |

SDK 提供 instruments、recommend_instruments、capture_instrument；`music.cmd call` 使用同一操作名和 JSON 契约。新能力共 50 项领域操作、11 类持久任务。

可编辑的内置对话额外提供 `preset_capture` 工具，最多等候 60 秒并持续检查租约/取消；任务独立持久化，不将样本清单塞入模型上下文。待完成时查询已有 job，不能不断重新提交。捕获成功后，一次 music_edit 可换预设并排队完整渲染；模型不得把已排队等同发声成功。原有项目中的捕获状态仍可复用。

## 选择规则

先确认用户意图与声部角色，再检查模型 GM 候选、音区、长短音比例、密度和上下文。GM 名称是候选，不是原曲配器真值；不确定时提供替代说明并保留待核对标记。

- 钢琴/电钢采用对应键盘采样；吉他和贝斯分别选拨弦与低音采样，不能只凭低音区把吉他当贝斯。
- 独奏弦乐与弦乐群区分；持续和声优先 sustain，GM 48/49 且多数事件短于 220 ms 时建议 staccato。奏法是建议，需要试听确认。
- 合唱采样只表示元音层，不声称恢复歌词或独唱；木管、铜管、竖琴和有音高打击乐使用相应来源。
- 电子主音、铺底和合成贝斯才使用明确选择的 Surge 预设。未知/无已验证映射的声部明确要求选择，不静默退回 INIT。
- 音域越界、鼓键位缺口、混合声部及不匹配奏法必须报告；不能靠悄悄移调、删音或改写原始分析解决。

新产品流水线要求已安装音色库，在 assemble 中按声部选择实际资源；catalog 摘要进入任务指纹，每个资源都校验摘要，状态和资源随 revision 保存。独立 P0 的通用音色重放仍作为诊断脚本保留，不能作为产品流水线的默认降级。

当前工作台的轨道和音源区显示实际 preset_name/id，旧版本显示未记录明确预设，避免把模型的“吉他候选”误当成正在播放真实吉他预设。

## 验收与剩余边界

28 个精选预设在 `validation-v6` 完成 56 次原生渲染/恢复，均从打包副本加载，stderr 未发现缺失文件或解析错误。首轮打包暴露的 Salamander 行内 include 漏收问题已经修复并补回归；旧失败任务保留，没有改标成功。

真实目标曲目的独立音色版本、整曲渲染与结尾对照以最终实测记录补充。音色映射改善不能证明转录已准确，也不能补出模型漏掉的演奏事件；P6 的实际音乐质量仍需听辨。
