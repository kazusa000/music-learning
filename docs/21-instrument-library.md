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

后续统一目录增加 `instruments.categories`，目前共 51 项领域操作，任务种类仍为 11。完整乐器优先入口与归类契约见下一节。

先确认用户意图与声部角色，再检查模型 GM 候选、音区、长短音比例、密度和上下文。GM 名称是候选，不是原曲配器真值；不确定时提供替代说明并保留待核对标记。

- 钢琴/电钢采用对应键盘采样；吉他和贝斯分别选拨弦与低音采样，不能只凭低音区把吉他当贝斯。
- 独奏弦乐与弦乐群区分；持续和声优先 sustain，GM 48/49 且多数事件短于 220 ms 时建议 staccato。奏法是建议，需要试听确认。
- 合唱采样只表示元音层，不声称恢复歌词或独唱；木管、铜管、竖琴和有音高打击乐使用相应来源。
- 电子主音、铺底和合成贝斯才使用明确选择的 Surge 预设。未知/无已验证映射的声部明确要求选择，不静默退回 INIT。
- 音域越界、鼓键位缺口、混合声部及不匹配奏法必须报告；不能靠悄悄移调、删音或改写原始分析解决。

新产品流水线要求已安装音色库，在 assemble 中按声部选择实际资源；catalog 摘要进入任务指纹，每个资源都校验摘要，状态和资源随 revision 保存。独立 P0 的通用音色重放仍作为诊断脚本保留，不能作为产品流水线的默认降级。

当前工作台的轨道和音源区显示实际 preset_name/id，旧版本显示未记录明确预设，避免把模型的“吉他候选”误当成正在播放真实吉他预设。

## 统一乐器目录

用户确认：**乐器是入口，音源库是后台来源**。一个多乐器库内的钢琴、电钢、吉他等分别归类；同一乐器可包含多个库提供的多个音色。用户可以只选乐器，由 AI 根据编曲、音域与奏法选预设，也可展开详情指定具体音色。

分类由 `src/music_learning/instrument_taxonomy.py` 统一管理，含 52 个节点（父类及待分类也计入），不是 52 件已安装/已验证的乐器。声学钢琴 `acoustic-piano`（别名木钢琴）、电钢琴 `electric-piano`、电吉他 `electric-guitar`、原声吉他 `acoustic-guitar` 独立；弦乐合奏、小提琴、大提琴及具体木管也分开，sustain/staccato/pizzicato 可在同一乐器下作为不同音色/奏法。合成模拟与明确的声学分类分开，不能把 Surge 的 Strings 文件夹直接当作真实弦乐采样。

每条预设持有唯一 `id` 和一个或多个 `instrument_ids`。多标签仅表示叠层/复合音色的归类，不证明插件可分别控制其声部。`source.library_id/library_name/backend` 保存后台来源，实际加载继续使用既有固定插件、完整状态和依赖闭包。不同库的同名音色并存，以唯一预设 ID 区分；重复 ID 明确拒绝，不能悄悄选第一条。

分类有三个依据：精选明确映射 `curated-registry`、原库分类标签 `source-category`、新 catalog 显式标注 `catalog-assignment`。未知项为 `unclassified`，不按名称、GM 标签或粗略 family 猜测细乐器。当前旧 catalog 不覆盖，读取时叠加版本管理的分类；未来构建 catalog 写出这些字段。后续用户给库后，须逐个可加载预设/乐器程序归类，填写稳定 ID 与来源；分类规则和接入代码进 Git，安装路径、音色与样本留本机数据目录。尚未提供通用格式的“一键导入任意商业库”；新的播放器或私有程序加载仍需实际适配。

当前清单：23 个采样预设是明确精选映射；2,914 个 Surge 文件按原库的明确类别归入合成键盘、贝斯、主音、铺底、序列等，包含以前因第三方作者子目录漏掉的类别；94 个模板、MPE、分区等不能确定具体乐器的预设保留待分类。合计 3,031，已验证仍只有 28，分类数量不是已发声或听辨通过数量。Shiny 的麦克风分支在原声吉他下仍保留“同一空心吉他麦克风采样”的说明，不冒称专门尼龙/钢弦采样。

统一接口：

| 操作 | 结果 |
| --- | --- |
| `instruments.categories {}` | 稳定 ID/名称/别名/parent_id，每类含子类的预设数、已验证数、来源库数；复合预设在父类计数去重。 |
| `instruments.list {instrument_id:"electric-guitar"}` | 该乐器的所有库音色；父类 ID 包含子类；`search` 支持中文别名与用途，`audio_verified` 可独立过滤，分页稳定优先已验证/默认。旧 `family` 过滤保留兼容。 |
| `production.query {instrument_id:...,include_instrument_categories:true}` | 同一分类与分页结果供内置 Agent 使用；不请求时不额外塞入整个分类目录。 |

SDK `Client.instrument_categories()`、`Client.instruments(instrument_id="electric-guitar")`；CLI `music.cmd call instruments.categories`，其他过滤通过既有 `--args <JSON文件>`。后续仍以具体 preset_id 捕获并应用，不能让分类 ID 冒充插件完整状态。

工作台新增“乐器”页：先选乐器，默认不要求选择库；来源可在卡片详情展开。选择当前版本的一个声部后，“使用此乐器 · 交给 AI 选音色”会准备限定该版本/声部、整轨插件范围的改编草稿，发送后才执行。可选“指定此音色”。单纯浏览和准备草稿不会调用模型、捕获或修改工程；声部或版本改变后旧草稿阻止发送，要求重新选择。待分类条目可查看，不能作为已明确乐器一键应用。

验证：86 项后端回归 + 36 项原生/模型格式回归，共 122 项通过；强化既有模拟 provider 测试，实际调用 instrument_id 查询 → 真实 sfizz 捕获 → 原子编辑/渲染排队。该条是模拟 provider 工具边界验证，没有新的真实 Codex 推理。测试覆盖同库拆分、跨库汇集、同名预设、多标签去重、别名、分页、来源隐藏/回读、未知分类/重复 ID 拒绝及旧 catalog 字节不变。Ruff、schema/TS 漂移和 TypeScript/Vite 构建通过。

真实正式实例 API/SDK/CLI 目录结果一致，stop/start 后分类/预设结果一致，音色工程 head 仍为 `473651f138a34e42a5a7c27d8eecd1ed`。证据 `D:\music-learning-runtime\benchmarks\instrument-directory-api-v1\summary.json`。真实浏览器最终记录在 `instrument-directory-browser-v4`：乐器选择、可选预设、来源回读、跨页不重复、待分类禁用、旧声部草稿拦截、390px 无溢出均通过；模型调用和制作写入均为 0。脚本 `apps/web/instrument-directory.mjs` 使用本机已有 Playwright 浏览器；首轮未设置浏览器缓存路径而未启动，第二轮因未等 React 草稿 effect 的测试竞态失败，修正等待后完成，未将失败改标成功。

本次未重跑三模型、重渲染歌曲或覆盖原始资源/工程；NAS 未访问。应用和自有浏览器已停止。P6 人工听辨仍待新版实际反馈。

## 验收与剩余边界

28 个精选预设在 `validation-v6` 完成 56 次原生渲染/恢复，均从打包副本加载，stderr 未发现缺失文件或解析错误。首轮打包暴露的 Salamander 行内 include 漏收问题已经修复并补回归；旧失败任务保留，没有改标成功。

真实目标曲目的独立音色版本已完成：工程 `4012a10f50574b2395d4ec4a9d8f27b5`，revision `473651f138a34e42a5a7c27d8eecd1ed`，完整 render `219822ad67bd44adb3b0822530b5708d`。9,378 音符、轨道和控制器不变，原工程 head 不变；307 秒输出峰值 0.28747、RMS 0.01641、削波样本 0。首个钢琴捕获因依赖不完整而失败，修复后在原分叉继续捕获，旧失败记录保留。

实际 stop/start 后快照、预设/资源引用、音符及捕获任务终态一致；重开后 60–64 秒与 1 秒尾音真实渲染成功（job `cc6a2860676243f6bff89ca8ba401e79`）。SDK/API 的查找、捕获、应用、渲染均实际使用；CLI 回读精选钢琴、50 项操作和 11 类任务。前端真实浏览器显示 6 个实际预设，390px 无溢出，9 个试听控件中首个 15 秒媒体实际解码播放；没有模型调用或人工听辨声明。

另在 `H:\music-learning-instruments\pipeline-adapter-v1` 隔离存储，以既有不可变的真实模型输出回放当前 assemble/render 适配器，完整两阶段及整曲/逐轨真实 VST3 通过，6 个明确预设、9,378 音符，未修改正式数据库。这不是重新运行三模型或新的持久流水线任务。回放辅助脚本首次因读取了不存在的 relative_path 字段停止，修复为实际阶段文件契约 name 后保留 assemble 产物继续完成，不能冒充第一次无故障通过。

新版结尾试听为 `H:\music-learning-delivery\listening-instruments-v1\index.html`，含原曲/旧 INIT/新音色三路、六轨独奏和完整新渲染。比较片段显式调整到相同 RMS 并保留原始电平文件，独奏的额外增益也有记录，不冒充 LUFS 匹配或最终混音。合唱轨在结尾仍为静音，因为该区间模型没有合唱事件；没有补造音符。

音色映射改善不能证明转录已准确，也不能补出模型漏掉的演奏事件；干声电吉他尚未恢复原版失真音箱链。P6 的实际音乐质量仍需听辨。全部运行实例和自有浏览器在收尾停止；NAS 始终未写入。

最终回归为 78 项后端 + 36 项原生/模型格式，共 114 项通过；Ruff、契约漂移及 TypeScript/Vite 构建通过。包含真实采样预设捕获/应用/渲染、内置 Agent 工具往返、初始化 CC 回读、旧幂等收据重放、依赖宏/行内 include/越界和音域越界拦截。

最后的更深临时目录回归暴露 Windows 原生读取/写入路径长度限制：263 字符的冻结源路径存在于扩展路径下，但普通路径加载失败；同文件复制到短路径后加载成功。已将转码临时文件改成有序短名，并缩短 attempt 目录的 token 提示（完整 fencing token 仍在 SQL/input.json，不改变租约语义）。在比失败用例更长的测试根目录中重跑 78 项通过；这不承诺支持无限路径深度。现有任务/音频路径没有迁移或覆盖。
