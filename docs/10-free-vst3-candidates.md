# 免费 VST3 音源与效果器候选

资料核对日期：2026-10-18。范围为 Windows x64 VST3；优先覆盖合成、采样、管弦乐、均衡、压缩、空间和染色。这里的优先级是本项目的选型判断，不是市场占有率排名。

Surge XT 1.3.4、CHOW Tape 2.11.4、sfizz 1.2.3 已通过 P0a 中的指定无界面用例，证据见 [验证记录](benchmarks/p0a-native.md)；其余候选仅核对官方资料。免费、格式兼容、可控性与声音质量分别记录，不能互相替代。

## 乐器

| 候选 | 用途 | 官方依据 | 本项目安排 |
| --- | --- | --- | --- |
| Surge XT | 通用合成器，贝斯、主音、铺底和合成打击乐 | [官网](https://surge-synthesizer.github.io/)、[Windows VST3 下载说明](https://surge-synthesizer.github.io/downloads/)、[源码](https://github.com/surge-synthesizer/surge)；免费开源 | 首个乐器宿主验证对象；验证音符、参数、状态与预设适配 |
| sfizz | SFZ 采样器，用于钢琴、弦乐、鼓等外部采样库 | [官方功能与 VST3i 说明](https://sfz.tools/sfizz/)；采样库另行获取 | 第二类宿主验证对象，重点验证 SFZ 路径、依赖、CC 和无窗口加载 |
| Dexed | DX7 风格 FM 合成，电钢、钟音、贝斯 | [官网](https://asb2m10.github.io/dexed/)、[VST3 源码与发布入口](https://github.com/asb2m10/dexed)；免费开源 | 补充音色；官方列出可自动化参数，实际参数和音色库操作仍需测 |
| BBC Symphony Orchestra Discover | 管弦乐配器参考与重建 | [官方免费产品和 VST3 说明](https://www.spitfireaudio.com/products/bbc-symphony-orchestra-discover)、[官方安装流程](https://support.spitfireaudio.com/en/articles/11815297-how-do-i-install-bbc-symphony-orchestra-discover) | 扩展候选；涉及账号/App 下载，验证乐器选择与奏法能否自动控制后再纳入默认库 |

sfizz 是采样器，安装插件不会自动拥有所有乐器。音源库须单独记录来源、许可、文件摘要、SFZ opcode 兼容性和映射。

钢琴候选可以考察 Salamander Grand Piano，但不能直接假定任意改编版适配 sfizz。[sfzinstruments 版本](https://github.com/sfzinstruments/SalamanderGrandPiano)明确使用 SFZ2/ARIA 扩展并提示其他引擎可能不兼容。因此先用自制、已知 opcode 的极小 SFZ 夹具验证加载，再选兼容版本做钢琴音色测试；该库尚未定为默认钢琴。

## 效果器

| 候选 | 用途 | 官方依据 | 本项目安排 |
| --- | --- | --- | --- |
| CHOW Tape Model | 磁带饱和、染色和动态质感 | [官方产品](https://chowdsp.com/products.html)、[手册中的 VST3 格式说明](https://chowdsp.com/manuals/ChowTapeManual.pdf) | 首个效果器宿主候选；可研究源码，验证 drive、bypass、状态和渲染 |
| TDR NOVA 免费版 | 参数 EQ、动态 EQ、频段动态控制 | [官方免费版及格式表](https://www.tokyodawn.net/tdr-nova/free/) | 核心均衡候选；验证频率/增益/Q 等参数映射及侧链 |
| TDR Kotelnikov 免费版 | 宽带动态控制、总线压缩 | [官方产品及格式表](https://www.tokyodawn.net/tdr-kotelnikov/) | 压缩候选；验证 threshold/ratio/attack/release、延迟与恢复 |
| Valhalla Supermassive | 延迟、混响和空间铺底 | [官方产品](https://valhalladsp.com/shop/reverb/valhalla-supermassive/)、[免费插件无需授权的官方说明](https://valhalladsp.com/support/) | 空间效果候选；验证参数、同步模式和长尾音 |

使用免费版，不把 GE/商业升级版或演示版能力混入默认清单。插件可以免费使用，不代表允许随本应用重新分发安装包；默认记录官方下载来源，具体分发方案另行核对。

## 验证顺序

1. 自制最小 VST3 测试插件：已知增益、延迟、状态、参数与故障行为，给宿主提供确定性真值。
2. Surge XT + CHOW Tape：一个真实乐器和一个真实效果器，完整自动建轨、写 MIDI、设置参数、渲染、保存和恢复。
3. sfizz + 自制小型 SFZ：验证采样资产依赖和路径恢复，再评估实际免费采样库。
4. NOVA、Kotelnikov、Supermassive：补齐常用效果链，并检测路由、侧链、延迟和尾音。
5. Dexed、BBC Discover：扩展音色，逐个记录可控性，不作为 P0 的全部前置条件。

默认工具箱只包含通过所需自动化用例的插件。P0 至少一个真实 VST3 乐器和一个真实 VST3 效果器通过完整测试；不能用内置合成器代替这一验收。

## 每个插件的能力档案

固定 plugin class ID、版本/摘要、安装与访问条件、参数元数据、总线/侧链、MIDI/CC 支持、程序与预设加载方式、component/controller 状态、外部资源、离线/实时渲染行为、延迟、尾音和确定性。

每项有 `supported / unsupported / untested` 与实测证据。是否能打开原生窗口独立记录，默认自动化测试不操作窗口。参数可回读不代表音频处理生效，必须渲染并测量输出差别。

能力档案是后续 AI 工具发现和插件专用语义映射的依据，例如把“更柔和”转换为可验证的包络/滤波/效果方案，并生成对比试听。
