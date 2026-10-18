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
