# 播放与混音连续流程回归

2026-10-20，基于`9139018`继续验证。用户要求将播放、音量、静音、独奏连起来测试，尤其边播放边调整。

## 测试方式

所有制作写入均在`data/benchmarks/playback-mixer-flow-v1`独立实例（8767）完成。使用真实sfizz 1.2.3、2条乐器轨、1条音频轨，12秒原创正弦测试音，乐器采样持续循环、音符从0.1至11.5秒发声，完整原生输出含2秒尾音。持续信号用于判断静音和推子的实际效果，不以没有音符的空窗判断整轨是否坏了。

通过cua_repl操作实际前端，读实际post-fader电平、播放位置、播放源启动计数、活跃通道和提示；SDK回读持久化快照、任务和试听状态。数值框、鼠标点击推子、推子键盘操作均有覆盖。测试驱动对range执行fill没有产生实际输入，后续改用真实鼠标/键盘事件并回读控件值，不能将那次fill计为声像操作通过。

## 发现并修复

1. **曲末自动重播**：自然结束仅停止本地播放器，保存的desired仍是playing=true；轮询把它当成等待音频就绪，又从旧位置播放。工作台现在在自然结束时保存暂停状态；本地结束标记阻止旧播放意图被轮询复活，显式的新播放命令仍有效。曲末再次播放会回到开头。
2. **冷启动混音平衡变化**：完整混音参考电平还没加载时，试听暂时逐轨做响度匹配，破坏声部间比例。Transport现在在启用电平匹配的情况下先取得共同混音参考，再同时起播，首次播放与重播使用同一电平。用户关闭匹配时不增加此参考读取；没有增加渲染任务。

## 最终构建检查

| 流程 | 实际检查 |
| --- | --- |
| 冷启动→暂停→重播 | 共同电平参考一致，音频轨短窗差约-0.384dB；512样本窗口随正弦相位变化，不是设备响度精度承诺 |
| 播放中调高6dB | 音频轨实测变化约+6.000dB，其他轨继续播放 |
| 鼠标/键盘调轨道推子 | 数值、实际电平、自动保存联动 |
| 播放中静音与取消 | 被静音声部到表底，取消后恢复，其他声部持续 |
| 单轨与多轨独奏 | 仅独奏轨有信号；两条独奏可同时发声 |
| 独奏轨叠加静音 | 静音优先；只剩被静音独奏时全静音，清除后完整恢复 |
| 混音台与左侧音轨混合操作 | 两处控件同步，播放源启动计数不增加 |
| 连续改多个值后暂停 | 保存正常完成、没有版本冲突、暂停位置保留 |
| 暂停时修改→恢复 | 恢复后使用保存的混音；新增播放源仅来自显式恢复 |
| SDK在播放中修改混音 | 新版本进入界面，控件保持可编辑，原播放时钟与源保持 |
| SDK只播指定声部→操作混音 | 筛选提示可见；混音控件清掉旧筛选，恢复被排除声部 |
| 自然结束→等待超过一次轮询 | 保持暂停、0个活动通道，源启动计数不增加 |
| 曲末再次播放 | 从0秒起播；观察位置约0.247秒，源启动计数仅增加1 |
| 刷新页面→重新播放 | 自动恢复工程入口；音量、静音/独奏、声像、暂停位置可回读 |
| 保存尚未完成就返回音乐库 | 离开时flush成功，重开保留音量与静音设置 |
| 原曲→重建 | 时钟连续；混音设置只作用重建，回到重建后所有预期声部恢复 |
| 独立服务停止/重启→播放 | revision、快照、desired一致；重连后正常起播与暂停 |

最终构建的25个连续观察点（31–55）通过断言。总共保存55个观察点，包含修复前的曲末自动重播故障，**不能称55个独立用例全部通过**。最终阶段的混音/静音/独奏连续操作保持源启动计数2不变，暂停/恢复、定位和明确从头播放才重新启动。

工程API回读：基线与最终均4个任务，没有因为混音新增渲染；5个音符、片段、插件完整状态、节拍映射和路由均与基线相同。最终3条声音来源可用，试听状态与head一致。

## 可复用检查

先选择新的、尚不存在的本机输出目录；示例全部位于项目数据目录。不得对正式工程运行seed。

```powershell
& data/envs/app/Scripts/python.exe scripts/prepare_mixer_workflow.py prepare data/benchmarks/NEW-RUN
$env:MUSIC_APP_CONFIG = 'E:\Intrest\music-learning\data\benchmarks\NEW-RUN\app.local.json'
$env:PYTHONPATH = 'src'
& data/envs/app/Scripts/python.exe scripts/manage_app.py start
& data/envs/app/Scripts/python.exe scripts/prepare_mixer_workflow.py seed data/benchmarks/NEW-RUN
# 通过实际前端执行上表，再检查没有篡改音乐或排新原生任务。
& data/envs/app/Scripts/python.exe scripts/prepare_mixer_workflow.py check data/benchmarks/NEW-RUN
& data/envs/app/Scripts/python.exe scripts/manage_app.py stop
```

已保存观察数据的断言：

```powershell
node apps/web/check-playback-mixer-evidence.mjs data/benchmarks/playback-mixer-flow-v1/browser-checks.json
node apps/web/live-mix-unit.mjs
node apps/web/audition-sync-unit.mjs
```

证据检查器分析已保存的实际浏览器观察，不自行操作浏览器；它不是能代替真实浏览器运行的端到端驱动。`prepare_mixer_workflow.py check`检查不可变音乐内容、任务数和当前播放计划，不能替代浏览器听音链路检查。

## 其他验证与范围

110项后端完整回归通过；扩展Transport单元覆盖多轨独奏、静音叠加、暂停修改与恢复、冷启动共同电平；试听冲突、既有编辑/电平单元、严格TS、Vite构建、契约漂移与Ruff通过。未运行新的Codex或音乐模型推理，未访问NAS，音色库恢复继续暂停。这里验证真实音频信号和控制行为，不代表P6音乐质量听辨通过。

本机证据包含`browser-checks.json`、`natural-end-failure.json`、`leave-pending-save.json`、`restart-before/after.json`、`workflow-check.json`和`final-playing.jpg`。测试结束后独立服务、浏览器和登录辅助均停止；正式工程仅回读核对，应用加载新版前端后保留供用户使用。
