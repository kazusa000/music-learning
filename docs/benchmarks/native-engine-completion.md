# 本地 DAW 目标收口核对

2026-10-22。**目标仍进行中，正式尚未切默认。** 本页把完整目标对应到证据；不以短测或构建替代实际连续播放。详细原始失败保留于[N7记录](native-engine-n7.md)，开发范围见[迁移计划](../plans/native-daw-migration.md)。

当前验收源码：`2bc6e4a5160e80b7e6f4dbc3753f6a8ddbc4ec4b`；候选描述：`runtime/engine-release.io-aware-v2.local.json`；原生bundle：`ce4876de9e3aade76f43be1c6431208228d8c1e1249ff468df8f4e3a5ed0b806`。其应用源码、web和native与03f325d同一内容，后续变化仅QA和文档。运行证据根为`data/maintenance/native-engine/`，不写正式工程。

| 目标要求 | 已核对的实现和证据 | 当前结论 |
| --- | --- | --- |
| 原生设备输出、唯一源时钟与控制协议 | `native/engine`；当前固定完整后端`n7/full-io-v2.xml`，含`tests.engine.test_control/test_files`；私有用户ACL、令牌/顺序/会话校验；真实WASAPI与软件回环 | 核心验收通过；最终长测仍待终态 |
| 本地音频读取、原曲/重建和速度网格 | SessionGraph后台读取/重采样；UI八组中的原曲双音频+静音旋律、SDK定位、空格播放；纯音乐兼容由服务回归覆盖 | 通过；未重新调用音乐模型 |
| 隔离VST3、真实MIDI和按轨替换 | NodeSource/NodeWorker；当前501后端含崩溃/卡死、动态延迟、热替换、暂停声音版本；N6四款真实Pianoteq/Keyscape及连续取消；`n7/reopen-final-v7`三次六worker正常退出 | 功能及独立冷重开通过；本轮完整整曲/循环仍待 |
| 前端/AI/API/SDK/CLI统一 | NativeTransport与EngineBroker；`n7/ui-staged-final/browser-report.json`八组，650ms UI阻塞仍供声，Browser音频/PCM/旧plan/JS全0；`n7/release-sdk-cli-default/report.json`五组 | 通过；默认正式指针尚待激活 |
| 实际桌面与4K | `n7/desktop-staged-final/native-window.json/png`四组真实WinForms/WebView2，240DPI/DPR2.5/Zoom1，foreground保持 | 验证通过；正式壳需安装该已测五文件并核对摘要 |
| 真AI提交与实际声音更新 | `n7/ai-20261022/report.json`，真实Codex独立sfizz工程音高93→105、新修订与applied一致、回环频率变化、0render | 通过；不写正式音乐 |
| 工程/素材/版本/任务生命周期和删除屏障 | 当前完整后端包含`test_engine_lifecycle`：活动租约/CAS保留、死API恢复、关闭取消、删除中装载、释放句柄后删除、共享素材保留、失败不chmod、显式冻结基准 | 通过；部署前后仍需九表/head和14pin一致 |
| 总线、发送、逐插件侧链、自动化、PDC | GraphKernel/GraphSession；当前完整后端含`tests.engine.test_graph/test_nodes`与`test_engine_graph`；N6实际相同DAG后台捕获与离线PCM逐字节一致 | 通过；动态总延迟变化允许重新同步并单独记过渡，非无缝承诺 |
| 导出与监听/响度 | 最终mix走固定ml_graph_runner共用图、冻结revision/mixing_version，失败不发布；N6三档保护算法迁移最高−1.21dBTP，不重复施加校准 | 通过已定范围；不宣称全库音色的人耳质量已验 |
| 快起播和边播边调 | `n7/latency-20261022/report.json`每项20次：冷文件句柄60.33ms、播放57.15ms、定位60.16ms P95；音量58.16ms/静音58.02ms/独奏57.20ms | 前三项满足初始目标；后三项未达初始50ms，原失败报告保留。此处为软件端点回环，非声学延迟 |
| 界面元数据/交互P95≤1秒 | 7轨/6685音符/396秒独立fixture与`metadata.py`真实Edge20次测量入口已准备，静态检查通过 | **实际计时待运行，不能以八组功能测试替代** |
| 连续整曲和30分钟无丢块/漂移 | `n7/endurance-v7`已终态：参考305秒→一次音源装载/20次播放定位/3次A-B→当前整曲→1800秒循环，严格检查时钟≤1源样本、设备/声部丢块0、缓存≤256MiB及PID复用 | **v7两段音频通过，但同图open重建worker导致PID断言失败，循环未开始；修共享复用规则再新批** |
| 设备恢复 | `n7/device-recovery-20261022.json`，模拟停止回调/真实不存在设备拒绝/当前设备恢复；`repair-audio.cmd`同SDK保留位置循环监听且暂停 | 已测；不是物理拔插实测 |
| H库、NAS及固定验证保护 | H只读、库不搬盘；启动前连续三次性能计数空闲记录`endurance-v7-storage.json`；正式前审计`endurance-v7/before.json`九表/head14pin相同 | 运行边界保持；最终后审计仍待 |
| 固定发布和文件夹整理 | 新源码/前端/native不可变副本、源码清单、启动锁/发布中断标记；N5探针和前端测试目录迁移保留兼容入口；模型/原数据路径只读核对`n7/release-resource-paths.json`全部存在、声明大小符合 | 候选具备；**正式激活/启动/只读冒烟及精确夹具收尾未完成** |

## 剩余执行顺序

1. v7已失败并退出，保留固定脚本/bundle/原报告。先修同一完整manifest重开的图复用规则，增加实际进程/声音/所有权回归，再固定新候选串行重跑完整长测及20次界面交互计时。完整终态通过才进入发布。
2. 核对正式工程后审计与全部三段、每次实际操作和回环/原生计数。原始50ms响应目标的差距仍如实保留，不调阈值写成成功。
3. 检查目标端口18765仍可用；原8765当前处于Windows系统保留范围。仅本项目配置换端口，不动系统设置或未知占用进程；SDK/CLI默认已跟随配置。
4. 应用停止并持有既有启动锁时，安装已测桌面五文件、校验固定发布描述并激活，正常入口启动固定源码。正式库只使用现有数据，不新建迁移/备份或测试修订；只读健康/前端/SDK/CLI及空闲设备验证后再次核对九表/head14pin。
5. 只对白名单内已结束、本目标新建pytest夹具做保护核对后清理；保留原始失败报告、小型证据、发布文件、旧N2、全部历史拒绝目录及reparse父目录。更新实际释放空间和部署状态，独立Git提交。
6. 按此表再次逐项核对最终状态，只有默认新架构真实运行且所有必须出口满足时才完成整个目标。
