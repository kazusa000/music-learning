# 选型依据与待验证项

日期：2026-10-18。以下官方资料用于确定候选与接口方向；本项目对运行环境、准确率和性能的最终选择以 P0 实测为准。

## 模型与音频工具

| 来源 | 当前依据 | 本项目决策 |
| --- | --- | --- |
| [MuScriptor 官方仓库](https://github.com/muscriptor/muscriptor) | 多乐器音频转 MIDI；small/medium/large；Windows GPU 有专用安装选项 | medium 作为初始转录基线，独立适配器；在本机测量后决定是否调整 |
| [MuScriptor 官方演示](https://muscriptor.kyutai.org/) | 提供录音转 MIDI 与谱面入口 | 参考能力范围，开发自己的工作台和工程系统 |
| [SheetSage2 模型说明](https://huggingface.co/m-a-p/SheetSage2) | 输出旋律、和弦、拍点、调性、结构及事件文件；长曲采用重叠窗口 | 用作结构分析基线，原始候选保留，与转录事件分别存储 |
| [Demucs 作者维护仓库](https://github.com/adefossez/demucs) | htdemucs 提供四类分轨；作者说明功能维护节奏有限 | 固定版本作初始分轨后端，避免将四分轨等同逐乐器干声 |
| [FluidSynth 官方仓库](https://github.com/FluidSynth/fluidsynth) | 基于 SoundFont 的软件合成器 | 更新后仅作为可选参考适配器，默认制作路径使用原生 VST3 |
| [music21 官方文档](https://music21.org/music21docs/) | 音乐事件、和弦、调性、节拍及分析工具 | 用于符号处理和解释候选，不作为原音频事实来源 |

MuScriptor 仓库代码采用 MIT，权重采用 CC BY-NC 4.0，本地获取权重需要完成对应访问流程。其乐谱输出还依赖 MuseScore 4 或更新版本；第一阶段的钢琴卷帘与 MIDI/WAV 不依赖乐谱排版链路。[来源](https://github.com/muscriptor/muscriptor)

SheetSage2 模型页面标注 CC BY-NC 4.0，并列出 Python、FFmpeg 和父模型访问要求。必须固定实际可运行的环境；本机已安装的较新 FFmpeg 不能自动当作兼容证明。[来源](https://huggingface.co/m-a-p/SheetSage2)

许可记录是模型清单的一部分。具体素材、音源和未来 API 分别记录自己的使用条件，不把代码许可套用到权重或音色库。

## 软件平台依据

- [Vite 官方指南](https://vite.dev/guide/)：前端工程与构建入口；实际依赖在实现时锁定。
- [FastAPI 官方文档](https://fastapi.tiangolo.com/)：类型驱动 API、请求校验与 OpenAPI。
- [Web Audio API 文档](https://developer.mozilla.org/en-US/docs/Web/API/Web_Audio_API)：浏览器试听原曲/已渲染产物，不承担 VST3 宿主和制作处理图。
- [TanStack Query 官方文档](https://tanstack.com/query/latest/docs/framework/react/overview)：服务端状态获取、缓存与同步。
- [SQLAlchemy 2 文档](https://docs.sqlalchemy.org/en/20/)：关系数据访问与事务组织。
- [JUCE 插件格式管理](https://docs.juce.com/master/classjuce_1_1AudioPluginFormatManager.html)：原生宿主候选基础；不等于已经实现完整 DAW。
- [Tracktion Engine](https://github.com/Tracktion/tracktion_engine)：提供构建音序器/DAW 的工程能力，采用独立于 JUCE 的许可；P0 比较复用收益后决定。
- [VST3 状态](https://steinbergmedia.github.io/vst3_dev_portal/pages/FAQ/Persistence.html)与[处理约定](https://steinbergmedia.github.io/vst3_dev_portal/pages/FAQ/Processing.html)：插件 component/controller 状态、处理生命周期、延迟和离线处理的宿主依据。
- 免费 VST3 候选及官方资料集中在[插件清单](10-free-vst3-candidates.md)，格式支持与实际无界面可控性分别验证。

## 决策记录

| 编号 | 决策 | 原因与影响 |
| --- | --- | --- |
| D01 | 本地 AI 操作服务、原生宿主与结果前端 | 更新初版网页工作台定位；前端展示/讨论/试听，AI 通过 API/SDK/CLI 操作 |
| D02 | 整曲预处理再进入正式工作台 | 与用户明确流程一致；ready 必须检查全曲产物，保留 partial 诊断状态 |
| D03 | 统一领域数据独立于模型输出 | 未来模型/API 可替换，人工修订与讨论引用长期保留 |
| D04 | 音频分轨与符号声部独立建模 | 多乐器转录与声音分离的能力不同，混合试听必须有可验证映射 |
| D05 | 所有已实现工程操作均有结构化接口 | 轨道、片段、插件、路由、自动化、保存/渲染均可检查、回读与撤销；无 UI 专属核心步骤 |
| D06 | VST3 重建试听必需，混合试听先做贝斯 | 原生图包含真实音源与效果；四分轨中 bass 有明确可替换路径 |
| D07 | SQLite + 本地文件 + 独立 worker | 满足单用户持久化与恢复，减少第一阶段运维负担 |
| D08 | GPU 串行、模型环境隔离 | 避免多个模型抢占显存以及依赖相互冲突 |
| D09 | 原始演奏与量化记谱时间共存 | 分析、编辑、导出和原曲对齐有不同时间需求 |
| D10 | 保存定位笔记，后续构建经验检索 | 研究过程立即产生可复用材料，原创阶段复用同一数据和版本体系 |
| D11 | Windows x64 VST3，免费插件先验证 | 用户已明确 VST3；逐个建立参数、状态、预设/采样和渲染能力档案 |
| D12 | 离线制作优先，无 MIDI 键盘要求 | 用户以讨论和试听参与，AI 为主要操作者；不为现场演奏和完整手工界面增加首期复杂度 |
| D13 | C++ 原生图由权威 revision 派生 | 音频处理不依赖 Python/浏览器回调；插件完整状态和资源进入版本系统 |

## 本机环境快照

2026-10-18 通过只读命令检查：

| 项目 | 检查结果 |
| --- | --- |
| GPU | NVIDIA GeForce RTX 5080，16303 MiB 显存，驱动 617.14 |
| 内存 | 系统可见约 31.1 GiB |
| Git | 2.51.0.windows.2 |
| Node | v24.14.1 |
| pnpm | 11.25.0 |
| uv | 0.12.5 |
| Python | uv 已安装 3.11.15 和 3.12.14；项目优先使用独立 3.11 环境 |
| FFmpeg | 9.0 full build；绑定兼容性待测 |
| FluidSynth / MuseScore | 本次 PATH 查询未找到，不能据此断言整机未安装 |
| Git 提交者 | 按用户提供的 GitHub 账号 kazusa000 设置本仓库身份，使用 ID 格式 noreply 邮箱 |

该快照不代表模型、音源或开发环境已安装完成。尚未下载权重、执行推理或测试音频质量。

## P0 必须解决的事项

| 事项 | 默认方向 | 需要的证据或输入 |
| --- | --- | --- |
| 目标曲目与曲风 | 一首完整曲开发，另加完整对照曲 | 用户目标音乐；早期可先选可用参考样本 |
| CUDA 与依赖组合 | 各模型独立环境、兼容 GPU 的 PyTorch | 实际推理、峰值显存、依赖锁文件 |
| 模型音质与速度 | MuScriptor medium + htdemucs + SheetSage2 | 整曲结果、片段人工校对、处理耗时 |
| 和声/结构失败替代 | 先定位依赖或质量问题，再评估替代引擎 | 相同输入的对比证据和决策修订；不能静默漏掉必需产物 |
| 原生宿主 | C++20/JUCE 探测，对比 Tracktion Engine | 真实 VST3 无点击操作、状态恢复、构建与许可决策 |
| 免费插件/采样库 | Surge XT + CHOW Tape 起步，再 sfizz 与常用效果 | 参数、状态、预设、采样、路由/延迟、无界面完整链路 |
| 对话服务 | 支持工具调用的可配置供应商 | 用户已有服务/凭据或明确选定服务，真实工具往返 |
| 音频理解 | 能力探测后启用 | 是否实际支持音频输入、发送方式与用户配置 |
| 渲染与试听 | 原生图的样本时钟和前端产物播放 | 完整多轨压力测试、脉冲/延迟补偿、尾音和自动化测量 |
| 处理与渲染预算 | 在本机测量后设置 | 处理耗时、峰值显存/内存、磁盘、试听等待时间 |

上述事项分别在需要前解决；它们不阻止当前规划文档落地，也不被写成已经完成的能力。
