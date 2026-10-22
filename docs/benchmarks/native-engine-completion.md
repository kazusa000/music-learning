# 本地 DAW 目标收口核对

2026-10-22。**目标仍进行中，正式尚未切默认。** 本页把完整目标对应到证据；不以短测或构建替代实际连续播放。详细原始失败保留于[N7记录](native-engine-n7.md)，开发范围见[迁移计划](../plans/native-daw-migration.md)。

当前完整回归源码d4852c6，候选`runtime/engine-release.desktop-final.local.json`，原生bundle f0563ad22e55a09049379daea6b6a61e74a8d54c22e4fea56d68e92166fbad6d。同图重开修复后完整后端510通过/1权限跳过；实际不可变桌面包四组与真实AI已通过。唯一剩余长测为endurance-v8的1800秒循环，结束后部署并检查正常启动，不扩大测试范围。运行证据根为`data/maintenance/native-engine/`，不写正式工程。

| 目标要求 | 已核对的实现和证据 | 当前结论 |
| --- | --- | --- |
| 原生设备输出、唯一源时钟与控制协议 | `native/engine`；当前固定完整后端`n7/full-desktop-final.xml`，含`tests.engine.test_control/test_files`；私有用户ACL、令牌/顺序/会话校验；真实WASAPI与软件回环 | 核心验收通过；最终长测仍待终态 |
| 本地音频读取、原曲/重建和速度网格 | SessionGraph后台读取/重采样；UI八组中的原曲双音频+静音旋律、SDK定位、空格播放；纯音乐兼容由服务回归覆盖 | 通过；未重新调用音乐模型 |
| 隔离VST3、真实MIDI和按轨替换 | NodeSource/NodeWorker；当前510后端含崩溃/卡死、动态延迟、热替换、暂停声音版本；N6四款真实Pianoteq/Keyscape及连续取消；`n7/reopen-final-v7`三次六worker正常退出 | 功能及独立冷重开通过；本轮完整整曲/循环仍待 |
| 前端/AI/API/SDK/CLI统一 | NativeTransport与EngineBroker；`n7/ui-staged-final/browser-report.json`八组，650ms UI阻塞仍供声，Browser音频/PCM/旧plan/JS全0；`n7/release-sdk-cli-default/report.json`五组 | 通过；默认正式指针尚待激活 |
| 实际桌面与4K | `n7/desktop-package/native-window.json/png`四组真实WinForms/WebView2，240DPI/DPR2.5/Zoom1，foreground保持 | 验证通过；五文件已固定在desktop-releases，待随版本指针激活 |
| 真AI提交与实际声音更新 | `n7/ai-process/report.json`，真实Codex独立sfizz工程音高93→105、新修订与applied一致、进程隔离流1760→3520Hz及旧频消失、0render | 通过；不写正式音乐 |
| 工程/素材/版本/任务生命周期和删除屏障 | 当前完整后端包含`test_engine_lifecycle`：活动租约/CAS保留、死API恢复、关闭取消、删除中装载、释放句柄后删除、共享素材保留、失败不chmod、显式冻结基准 | 通过；部署前后仍需九表/head和14pin一致 |
| 总线、发送、逐插件侧链、自动化、PDC | GraphKernel/GraphSession；当前完整后端含`tests.engine.test_graph/test_nodes`与`test_engine_graph`；N6实际相同DAG后台捕获与离线PCM逐字节一致 | 通过；动态总延迟变化允许重新同步并单独记过渡，非无缝承诺 |
| 导出与监听/响度 | 最终mix走固定ml_graph_runner共用图、冻结revision/mixing_version，失败不发布；N6三档保护算法迁移最高−1.21dBTP，不重复施加校准 | 通过已定范围；不宣称全库音色的人耳质量已验 |
| 快起播和边播边调 | `n7/latency-packaged/report.json`每项20次：冷文件句柄60.25ms、播放58.04ms、定位59.59ms P95；音量57.41ms/静音55.72ms/独奏55.39ms | 前三项满足初始目标；后三项未达初始50ms，原失败报告保留。此处为软件端点回环，非声学延迟 |
| 界面元数据/交互P95≤1秒 | `metadata-v1`：7轨/6685音符/396秒E内MLFixture，20次真实Edge全新context；可见P95456.93ms、选择生效549.41ms | **≤1秒目标通过（独立fixture规模）；native ready单列1815.63ms，不作H音源装载承诺** |
| 连续整曲和30分钟无丢块/漂移 | `n7/endurance-v8`已终态：参考305秒→一次音源装载/20次播放定位/3次A-B→当前整曲→1800秒循环，严格检查时钟≤1源样本、设备/声部丢块0、缓存≤256MiB及PID复用 | **v8两首完整曲均通过：时钟/回环gap/欠载/xrun/插件missed0，PID保持；1800秒循环进行中，不能提前称整批通过** |
| 设备恢复 | `n7/device-recovery-packaged.json`，模拟停止回调/真实不存在设备拒绝/当前设备恢复；`repair-audio.cmd`同SDK保留位置循环监听且暂停 | 已测；不是物理拔插实测 |
| H库、NAS及固定验证保护 | H只读、库不搬盘；启动前连续三次性能计数空闲记录`endurance-v8-storage.json`；正式前审计`endurance-v7/before.json`九表/head14pin相同 | 运行边界保持；最终后审计仍待 |
| 固定发布和文件夹整理 | 新源码/前端/native不可变副本、源码清单、启动锁/发布中断标记；N5探针和前端测试目录迁移保留兼容入口；模型/原数据路径只读核对`n7/release-resource-paths.json`全部存在、声明大小符合 | 候选具备；**正式激活/启动/只读冒烟及精确夹具收尾未完成** |

## 剩余执行顺序

1. v7原失败完整保留，共享图复用已修。v8两首整曲已通过，只等待当前1800秒循环终态；不再扩大测试范围，完整通过后发布。
2. 核对正式工程后审计与全部三段、每次实际操作和回环/原生计数。原始50ms响应目标的差距仍如实保留，不调阈值写成成功。
3. 检查目标端口18765仍可用；原8765当前处于Windows系统保留范围。仅本项目配置换端口，不动系统设置或未知占用进程；SDK/CLI默认已跟随配置。
4. 应用停止并持有既有启动锁时，校验已测桌面五文件的不可变包、校验固定发布描述并激活，正常入口启动固定源码。正式库只使用现有数据，不新建迁移/备份或测试修订；只读健康/前端/SDK/CLI及空闲设备验证后再次核对九表/head14pin。
5. 只对白名单内已结束、本目标新建pytest夹具做保护核对后清理；保留原始失败报告、小型证据、发布文件、旧N2、全部历史拒绝目录及reparse父目录。更新实际释放空间和部署状态，独立Git提交。
6. 按此表再次逐项核对最终状态，只有默认新架构真实运行且所有必须出口满足时才完成整个目标。
