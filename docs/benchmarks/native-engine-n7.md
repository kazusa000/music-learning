# N7：默认发布前验收

2026-10-21。**进行中，尚未部署正式默认；整体目标保持 active。** N0–N6阶段已完成，见[计划](../plans/native-daw-migration.md)。此文随最终实测补齐，不将构建或短测当作整曲验收。

## 当前实现与修复

设备意外停止时立即回读 stopped/playing=false，并使声音版本失效；主动拆卸回调用单独的 expectedStop 区分，预览或切换图不会误清正常 transport。重新打开由既有控制层配置设备，旧播放状态不能冒充实际输出。

完整图的首个 DSP 块使用后台准备期限，不用播放中的短期限判其迟到。部分采样器在状态装载结束后还会首次初始化处理器；原先实测出现空首块和空 applied_revision。准备仍限时，设备回调不等待插件；只有实际完成的音频和非空版本才能报告 ready。新增独立动态夹具以75ms首次DSP延迟重现并验证，实时捕获和严格导出PCM一致。

实际整曲第一轮在约190秒记录Keyscape迟到块，设备underrun/xrun为0，原失败保留。新增线程局部Windows MMCSS Pro Audio调度，分别用于后台图生产器和插件处理线程，不改变系统默认输出、主音量、驱动或其他进程。重新开始完整验收，不能把旧失败报告改成成功。

`Loopback.cpp`保留短WAV捕获并增加有界 `--meter` JSONL模式，长测不积累整曲PCM。每窗记录有限性、峰值、RMS、设备帧连续性和QPC时间戳。新的脚本位于 `scripts/probes/engine/{acceptance,latency,ai,desktop}.py`；原N2固定宿主/报告不修改。

## 已完成实测

- `n7/device-recovery-final.json`：真实WASAPI输出、模拟停止回调、实际不存在设备拒绝及重新连接恢复通过；Pro Audio线程注册成功。不是实际拔插硬件试验，也没有切换系统默认设备。
- `n7/graph-regression.log`：8项完整图回归通过，包含新增的75ms首次DSP初始化。
- `n7/ai-v3/report.json`：真实Codex将独立sfizz工程唯一音符从MIDI93提高至105，事务提交、新声音版本、持续播放和设备回环频率变化通过，0整曲渲染任务。先前两轮频率探针未通过，原报告保留；最后使用采样循环周期精确的测试音，并保存暂停时端点背景基线，避免把混合端点上的其他声音当作本轨。
- `n7/native-ui-v2/native-window.json/png`：真实WinForms/WebView2窗口的播放、现场独奏、原曲/重建切换、暂停4组通过，0新增渲染任务；240DPI、DPR2.5、Zoom1、3510×1965捕获。窗口透明且不激活，foreground_unchanged=true。第一轮探针错误地用页面已移除的URL令牌覆盖cookie，已修正探针，产品认证未改。
- TypeScript、原生transport单元和本次Python Ruff通过。最终完整后端正在重跑，结果尚不在此宣称通过。

## 回环响应

`n7/latency-full-v1/report.json`每项20次，实际设备为LG TV SSCR2 / NVIDIA High Definition Audio，48kHz、480帧。输入为小音量不同频率标记，按命令提交QPC与回环标记跨阈值时间比较，5ms分析窗；所有时间戳错误、丢帧和不连续计数均0。测量是Windows软件端点回环，不能当作扬声器/麦克风声学延迟。

| 操作 | 中位数ms | P95 ms | 初始目标 |
| --- | ---: | ---: | --- |
| 新文件句柄/原生图冷开并播放 | 58.04 | 60.95 | ≤1000，通过；OS磁盘缓存未主动清空 |
| 已就绪播放 | 52.99 | 57.95 | ≤150，通过 |
| 定位至不同频率标记 | 54.77 | 60.87 | ≤250，通过 |
| 音量 | 55.98 | 57.84 | ≤50，**未达到** |
| 静音 | 53.61 | 57.56 | ≤50，**未达到** |
| 独奏 | 53.59 | 57.17 | ≤50，**未达到** |

256帧请求仍被当前设备协商为480帧；实际尝试低延迟共享模式也仍为480帧，未改善这一结果，正式默认保持普通WASAPI共享。没有通过放宽断言把50ms写成通过，也没有修改用户系统音频设置。控制回执与实际回环时间分开保存；数毫秒回执不是首声证据。

时间戳依据：[IAudioCaptureClient::GetBuffer](https://learn.microsoft.com/en-us/windows/win32/api/audioclient/nf-audioclient-iaudiocaptureclient-getbuffer)的QPC单位为100ns；错误标记按[官方定义](https://learn.microsoft.com/en-us/windows/win32/api/audioclient/ne-audioclient-_audclnt_bufferflags)保留。低延迟共享行为参考[IAudioClient3](https://learn.microsoft.com/en-us/windows/win32/api/audioclient/nn-audioclient-iaudioclient3)与仓库固定JUCE实现。

## 后续修复与阶段复核

复杂路由原曲/重建切换现在保留已加载的整个编曲图；缓冲计入同一256MiB预算，保留程序路径供租约回收判断。返回时更新到最新修订并定位，不重建插件；关闭、删工程、换设备或换工程统一释放。`n7/switch-smoke-v1`在实际7轨/6插件工程连续三次切换，6个PID全程不变，重建就绪约46–47ms；`endurance-v3`重跑同一检查约67ms。测试另外覆盖在原曲视图修改MIDI后返回，应用新声音版本且保留worker。

`native_audio_bundle`本机配置可固定已发布不可变bundle；其完整manifest一并保存，设备与显式混音导出使用同一配置。不因开发构建而在下次启动偷偷换worker，也可恢复到已验版本；不复制或回退数据库。设备 stopped 后维护器先退出自有进程树，再释放派生文件/租约，防止仍持文件的worker与清理交错。

- `warm-regression`52项通过，含复杂图切换、原曲中编辑、生命周期和原生内核。
- `release-regression`23项通过，含发布bundle重开、独立混音导出、路由与生命周期。
- `device-cleanup-regression`13项通过；Ruff通过。
- 第一轮完整后端374过/5败：一项冻结任务遇到源码变动，正确返回implementation_changed；另四项属于并发新增的未提交音源入库代码，其解析器也在运行中被修改。原日志保留，未放宽指纹。最终将用固定源码快照重跑当前已提交范围，不混入另一项工作尚在编辑的测试。

第二轮整曲中396秒本曲原生输出和回环均通过；305秒本地FLAC原生欠载/xrun为0，但回环采集器在并发负载中记录6次不连续/11040丢帧，此轮**未通过**。采集器改用线程局部MMCSS及1秒有界采集缓冲（不增加输出延迟），QPC仍来自原始包，不放宽检查。`endurance-v3`重新从头执行，不能把第二轮的部分通过当作最终结果。

第三轮回环采集连续，但约160秒仍记录插件迟到。后续修正生产器满队列的Sleep轮询为消费后的WaitOnAddress/WakeByAddressSingle唤醒；worker空闲消息改为非睡眠的有界待处理消息泵，避免将Windows定时睡眠混进下一块DSP期限。设备回调只发唤醒，不等待或取锁。补充20次空闲唤醒的真实节点PCM/进程及响应回归；当前 `event-wakeup-regression-v2`42项通过。其首轮另外暴露了新资源已装载但成功音频仍属旧版本时误报ready，已要求publishedRevision与desiredRevision严格相同；原失败保留。

固定源码快照 `814c954` 完整回归373过/1败，唯一失败为动态夹具测试临时追加的插件白名单路径没有resolve，目录联接下被正确拒绝；只修测试规范化，不放宽产品白名单。新修复提交后继续完整快照回归与最新长测。

新增根目录 `repair-audio.cmd` / `scripts/app.py repair-audio`，只调用既有SDK engine操作，退出自有音频进程并按原工程/修订/视图/源位置/循环/监听重开，保持暂停。0新增操作/音乐修订/渲染；真实HTTP SDK恢复与空工程重复恢复2项通过。正常启动先校验发布bundle，缺失或SHA异常明确失败；不静默换版本或转回浏览器供声。

## 整曲、发布及保护

`runtime/engine-acceptance-job.local.json`定位当前唯一N7长测；第三轮 `n7/endurance-v3` 使用不可变bundle，当前396秒真实工程、305秒本地FLAC、30分钟循环依次执行，期间现场调音量/静音/独奏，不通过定位或建图清空计数。正式数据库用SQLite mode=ro读取；程序/状态和必要本地音频硬链接仅放本次独立根，H库只读，NAS无访问。原始音频不传浏览器。

Pianoteq既有批次实际status.json已回读completed、verified763、failures0，相关原监督PID已退出；旧locator的starting不作为当前状态。没有修改或重新启动该批次。正式九表/head与14个固定摘要持续用N1基线核对；发布/清理前后还需再次核对。

尚需：完整长测及当前后端失败排查、最终UI回归、正式默认切换、恢复入口与本次临时产物收尾。正式服务仍旧前端，不据此邀请用户验收或结束目标。
