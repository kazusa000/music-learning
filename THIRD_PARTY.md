# 第三方依赖记录

本仓库的原生部分使用第三方 SDK，并加载独立插件；源码、构建工具、插件与采样库的使用条件分别记录。下载文件保存在被忽略的目录，不随源码提交重新分发。

| 依赖 | 当前方式与来源 |
| --- | --- |
| JUCE 9.0.4 | 模块为 AGPLv3/商业双许可，详见 [JUCE LICENSE](https://github.com/juce-framework/JUCE/blob/9.0.4/LICENSE.md)。本地原型使用其开源许可路径；未来对外发布方式可能需要商业 JUCE 许可，不能将其视为 MIT 依赖。 |
| VST3 SDK | 使用 JUCE 包中附带版本，具体版本与第三方许可见该包的 JUCE.spdx.json 和 VST3_SDK/LICENSE.txt；不修改 SDK。 |
| MSVC / Windows SDK | 从微软固定渠道清单获取，个人本地构建工具；不随本项目重新分发。 |
| portable-msvc | [mmozeiko 原始脚本](https://gist.github.com/mmozeiko/7f3162ec2988e81e56d5c4e22cde9977)，锁定 revision 和摘要，运行前已审查，添加目录边界与清单固定包装；脚本本身不复制进源码仓库。 |
| innoextract | [官方工具](https://constexpr.org/innoextract/)，用于提取安装包到项目目录，不运行插件系统安装。 |
| Surge XT / CHOW Tape / sfizz | 仅下载各自官方二进制并在本地宿主加载；来源和摘要见 config/native-dependencies.json，插件列表见 docs/10-free-vst3-candidates.md。 |

sfizz 1.2.3 文件加载适配依据其 [SfizzVstState.cpp](https://github.com/sfztools/sfizz-ui/blob/1.2.3/plugins/vst/SfizzVstState.cpp) 和 SDK 字符串格式实现，仅用于已识别的版本，不适用于未知私有状态。测试音频和 SFZ 由本项目脚本生成。

P0b 模型依赖独立记录：

| 依赖 | 来源与使用条件 |
| --- | --- |
| MuScriptor 0.3.0 | [代码 MIT](https://github.com/muscriptor/muscriptor)；medium 权重为 CC BY-NC 4.0。用户已接受许可并本机登录，固定权重下载/摘要验证及两首整曲推理完成。 |
| Demucs 4.1.0 / HTDemucs | [作者维护仓库 MIT](https://github.com/adefossez/demucs)、[固定权重来源](https://huggingface.co/adefossez/HTDemucs)；使用 safetensors，未复制第三方代码进本仓库。 |
| SheetSage2 / MERT-v2-FullSong | [SheetSage2](https://huggingface.co/m-a-p/SheetSage2)、[父模型](https://huggingface.co/m-a-p/MERT-v2-FullSong)，模型页及下载 LICENSE 标注 CC BY-NC 4.0。代码/权重/父模型版本与逐文件摘要见 config/model-dependencies.json，执行本地固定远程代码，不修改上游。 |
| PyTorch / torchaudio / Python 包 | 对应 requirements-*.lock 固定版本与分发摘要；本地隔离安装，保留包自身许可。CUDA 包不等于项目源码许可。 |
| Playwright 1.56.1 / Chromium | [官方工具](https://playwright.dev/)，用于独立 headless 播放探测；包锁位于 probes/player，浏览器下载到本机运行目录，不随源码分发。 |
| FFmpeg 9.0 | 使用本机现有 Gyan full build CLI，整曲实际验证；共享库绑定未通过，不将系统安装冒充可移植发行包。 |
| Codex App Server | 使用官方桌面安装中的固定运行组件，四文件 SHA-256 见 config/codex-runtime.json；自行实现 stdio 客户端，凭据交由官方 managed login，不解析/复制。官方入口见 [App Server](https://learn.chatgpt.com/docs/app-server)。当前仅本机复制运行，未来对外发行另核对组件许可与打包方式。 |

原有完整原创夹具和诊断 SFZ 采样由脚本合成，只作为工程诊断；用户素材和模型权重不进入 Git。以上不替代未来应用发布时逐项核对第三方依赖/权重/插件的分发方式。

## 实际音色库补充

六套采样库的官方仓库、固定 commit、下载 SHA-256 与许可标识见 `config/instrument-sources.json`；本机原许可保存在 H: 各库内，来源和用途见 [音源与映射](docs/21-instrument-library.md)。Salamander 为 CC BY 3.0；Shinyguitar/Swagbass 为 CC0；Naked Drums 为 CC BY 4.0；SSO 保存 CC Sampling Plus 1.0 原许可；jRhodes3d 按作者 LICENSE 区分音乐使用、控制文件和样本再分发，不把样本统称 CC0。库文件不随源码重新分发。

宿主 Surge FXP 适配依据固定 [SurgeSynthesizerIO.cpp](https://github.com/surge-synthesizer/surge/blob/release_xt_1.3.4/src/common/SurgeSynthesizerIO.cpp) 的 loadPatchByPath/loadRaw 与 [SurgeSynthProcessor.cpp](https://github.com/surge-synthesizer/surge/blob/release_xt_1.3.4/src/surge-xt/SurgeSynthProcessor.cpp) 的组件状态入口实现。sfizz 仍固定 1.2.3；新库的格式/奏法兼容性仅按已执行用例记录，不宣称所有 SFZ/FXP 都经过实测。
