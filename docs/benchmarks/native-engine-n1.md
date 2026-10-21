# N1：原生设备与控制协议

2026-10-21。N1完成；**N2–N7尚未完成，新引擎未切为产品默认。** 用户授权持续推进整个本地DAW重构目标。

## 实现

新增独立`native/engine`与`scripts/build_engine.py`，输出`runtime/build/engine-v1/bin/ml_engine.exe`。仅追加编译JUCE audio_devices，链接已验证静态运行库；构建前检查固定SHA，不重建或替换旧离线/常驻宿主。WASAPI shared设备输出由C++设备回调持续提供；N1仅有-42dBFS、最长3秒的确定性探针，文件轨/MIDI/插件图待后续阶段。

Python新增`engine/EngineBroker`，使用当前用户SID专属ACL、拒远程连接的命名管道；每轮检查随机令牌、协议1、单调序号、session/request回执，握手回读原生PID。控制I/O在单独线程有超时，进程归OwnedProcess/Job Object所有；不传PCM，不写工程修订。原生控制在消息线程执行，不在音频回调读管道或操作数据库。

统一契约新增`engine.devices/configure/status/probe/stop/close`六操作，共86操作；Service/API/SDK/CLI共用，关闭API同时清理其引擎。新操作是运行会话控制，不创建音乐任务或数据库迁移。设备未构建明确失败，不退回浏览器或偷偷渲染。

## 实际设备及回环

独立探针`scripts/probe_engine_device.py`，最终报告：`data/maintenance/native-engine/n1/device/20261021-163725-db251779/report.json`。实际设备为LG TV SSCR2 / NVIDIA High Definition Audio，48kHz，驱动实际缓冲480帧（请求512，由设备协商，未伪报512）。149次实际回调，1秒探针推进48,000样本并自然停止；设备xrun=0。

新增原生`ml_engine_loopback.exe`使用WASAPI系统输出回环，未改系统默认设备或主音量。3秒采集144,000帧，discontinuities=0。最终9973Hz测试频率有效窗口RMS中位数0.00555395，非测试窗口中位数0.000143475，约38.7倍；12个窗口高于背景门槛。系统当时同时存在其他声音，因此总峰值0.70166不是探针本身的峰值，也不拿此短录音证明工程音质或长曲连续性。

首次997Hz探针的初始检测不足以排除背景音乐，进一步背景对照未通过；保留早期记录，不把它当成最终验收。最终改用背景能量较低的固定9973Hz，并保留背景比值检查；没有降低判定阈值。后续每次运行使用独立run目录并保存failure.json，避免成功/失败互相覆盖。控制回复本次0.256ms级观测仅为控制回执，不是按播放到声卡首声的延迟；N2之后按计划单独测20次/P95。

## 回归与保护

新增10项原生协议/HTTP SDK/闭合契约/缺引擎/退出清理测试。完整后端`tests/service tests/engine` **282通过**（94.94s）；Ruff、生成契约检查、TS/Vite构建通过。前端构建仍为已部署B3LrRqPN版本，没有接入新引擎或重启正式服务。

`data/maintenance/native-engine/n1/before.json`与`after.json`核对：正式9表/head一致，14项保护SHA一致（既有11项加旧离线EXE、旧常驻EXE、固定运行库）。当次正式1工程/3修订/2任务/7轨/6,684音符，按现场基线而非旧报告数字核对。未访问NAS、未写H库、未操作Pianoteq队列/宿主/插件、未触碰任何历史拒绝清理路径。

独立设备探针、回环与测试引擎均已退出；保留小型报告、日志、独立测试数据与短回环文件。本阶段没有执行文件系统清理命令。
