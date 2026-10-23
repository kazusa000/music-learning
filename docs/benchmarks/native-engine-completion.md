# 本地 DAW 目标收口核对

2026-10-22。**N0–N7已验收并切为正式默认。** 本页把完整目标对应到证据；不以短测或构建替代实际连续播放。详细原始失败保留于[N7记录](native-engine-n7.md)，开发范围见迁移计划（历史文档见 Git）。

部署源码d4852c6，原描述`runtime/engine-release.desktop-final.local.json`，正式指针`runtime/application-release.local.json`，原生bundle f0563ad22e55a09049379daea6b6a61e74a8d54c22e4fea56d68e92166fbad6d、桌面aa81f952。完整后端510通过/1权限跳过；实际不可变桌面包四组、真实AI、两首整曲和1800秒循环均已通过。正常入口已启动固定源码，正式主页及默认SDK/CLI只读检查通过。证据根为`data/maintenance/native-engine/`；没有写正式音乐修订或新建正式任务。

| 目标要求 | 已核对的实现和证据 | 当前结论 |
| --- | --- | --- |
| 原生设备输出、唯一源时钟与控制协议 | `native/engine`；当前固定完整后端`n7/full-desktop-final.xml`，含`tests.engine.test_control/test_files`；私有用户ACL、令牌/顺序/会话校验；真实WASAPI与软件回环 | 核心与完整长测通过 |
| 本地音频读取、原曲/重建和速度网格 | SessionGraph后台读取/重采样；UI八组中的原曲双音频+静音旋律、SDK定位、空格播放；纯音乐兼容由服务回归覆盖 | 通过；未重新调用音乐模型 |
| 隔离VST3、真实MIDI和按轨替换 | NodeSource/NodeWorker；当前510后端含崩溃/卡死、动态延迟、热替换、暂停声音版本；N6四款真实Pianoteq/Keyscape及连续取消；`n7/reopen-final-v7`三次六worker正常退出 | 功能、独立冷重开、v8整曲和循环通过 |
| 前端/AI/API/SDK/CLI统一 | NativeTransport与EngineBroker；`n7/ui-reuse/browser-report.json`八组，650ms UI阻塞仍供声，Browser音频/PCM/旧plan/JS全0；`n7/release-sdk-cli-reuse/report.json`五组；应用源码等价证据`final-ui-code-equivalence.json` | 已激活；`default-startup/report.json`默认SDK/CLI和正式主页通过 |
| 实际桌面与4K | `n7/desktop-package/native-window.json/png`四组真实WinForms/WebView2，240DPI/DPR2.5/Zoom1，foreground保持 | 已默认使用不可变五文件包；正式主页启动亦为240DPI/DPR2.5 |
| 真AI提交与实际声音更新 | `n7/ai-process/report.json`，真实Codex独立sfizz工程音高93→105、新修订与applied一致、进程隔离流1760→3520Hz及旧频消失、0render | 通过；不写正式音乐 |
| 工程/素材/版本/任务生命周期和删除屏障 | 当前完整后端包含`test_engine_lifecycle`：活动租约/CAS保留、死API恢复、关闭取消、删除中装载、释放句柄后删除、共享素材保留、失败不chmod、显式冻结基准 | 功能通过；保护核对见下方收尾记录 |
| 总线、发送、逐插件侧链、自动化、PDC | GraphKernel/GraphSession；当前完整后端含`tests.engine.test_graph/test_nodes`与`test_engine_graph`；N6实际相同DAG后台捕获与离线PCM逐字节一致 | 通过；动态总延迟变化允许重新同步并单独记过渡，非无缝承诺 |
| 导出与监听/响度 | 最终mix走固定ml_graph_runner共用图、冻结revision/mixing_version，失败不发布；N6三档保护算法迁移最高−1.21dBTP，不重复施加校准 | 通过已定范围；不宣称全库音色的人耳质量已验 |
| 快起播和边播边调 | `n7/latency-packaged/report.json`每项20次：冷文件句柄60.25ms、播放58.04ms、定位59.59ms P95；音量57.41ms/静音55.72ms/独奏55.39ms | 前三项满足初始目标；后三项未达初始50ms，原失败报告保留。此处为软件端点回环，非声学延迟 |
| 界面元数据/交互P95≤1秒 | `metadata-v1`：7轨/6685音符/396秒E内MLFixture，20次真实Edge全新context；可见P95456.93ms、选择生效549.41ms | **≤1秒目标通过（独立fixture规模）；native ready单列1815.63ms，不作H音源装载承诺** |
| 连续整曲和30分钟无丢块/漂移 | `n7/endurance-v8/report.json`终态succeeded：参考305秒→一次音源装载/20次播放定位/3次A-B→当前整曲→1800秒循环；缓存≤256MiB及PID复用断言保持 | **三段全过，时钟误差/回环gap/断续/时间戳/欠载/xrun/插件missed全部0，六worker PID保持** |
| 设备恢复 | `n7/device-recovery-packaged.json`，模拟停止回调/真实不存在设备拒绝/当前设备恢复；`repair-audio.cmd`同SDK保留位置循环监听且暂停 | 已测；不是物理拔插实测 |
| H库、NAS及固定验证保护 | H只读、库不搬盘；启动前连续三次性能计数空闲记录`endurance-v8-storage.json`；`endurance-v8/before.json`及`after.json`九表/head14pin相同 | 运行边界保持；最终部署与清理核对见收尾记录 |
| 固定发布和文件夹整理 | 源码/前端/native/desktop不可变副本、启动锁/发布中断标记；N5源码目录迁移及兼容入口；资源路径只读核对`n7/release-resource-paths.json` | **已激活并通过正常入口启动，SDK/CLI/实际主页通过；临时夹具清理另存精确记录** |

## 默认运行与保留限制

双击项目根`start.cmd`，服务当前端口18765；只改项目配置，未改Windows保留范围。恢复音频使用`repair-audio.cmd`；版本回退按[发布说明](../development/native-release-operations.md)，不会回退数据库。

初始50ms监听目标尚有约5–7ms差距，真实端点数据和failed状态保留；这不改写为达标。H大型采样器本次冷开28.52秒，三次暖原曲/重建恢复约65–67ms且PID不变；元数据交互与插件冷装载分开记录。不宣称全库音色的人耳音质、物理拔插或声学麦克风延迟已通过。

历次v1–v7失败、小型诊断、原报告、发布包及受保护旧目录保留；清理仅针对本次已结束pytest白名单，不跟随reparse或修改只读属性。

## 最终收尾记录

`cleanup-resumed-final.json`记录27个精确根：删除31041文件/26878目录，保留33份小诊断，跳过9个reparse及36个父目录，0失败。E窗口空闲净增2.144GiB，因同期另有API工程删除，不能全归因于临时夹具。原始失败和发布包不删除。

v8前后九表/head14pin一致；默认启动读回原head和两项任务。随后服务记录旧工程删除→重新导入失败→再删除，最终正式0工程，因此`default-final-protection-v2.json`的九表对旧基线不等、14pin仍全部相同。`default-final-state-assessment.json`保存对应事件；没有覆盖基线、恢复旧工程或将这个不等掩盖成通过。本任务未调用这些音乐写操作，后续失败导入已随工程删除，没有额外运行模型诊断。默认服务仍健康，所有独立验收进程已退出。
