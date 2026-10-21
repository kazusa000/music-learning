# N4：产品与统一操作接入

2026-10-21。候选主页、工作台、音色试听均由本地引擎发声。React只提交控制和读状态，音量/静音/独奏不建声音文件、不写音乐修订；MIDI优先运行当前插件，只有显式冻结才使用已验证冻结音频。旧Transport文件暂留兼容测试，产品入口不再导入。

NativeTransport串行控制、按native源样本显示时钟、跳过无变化的循环重建，原曲/重建/历史A/B共用设备。切音色等待对应轨的真实ready/failed，不调用旧playback.live.chunk。新增engine.preview原生独立试听bus，工程音量临时压低但工程推子/时钟不改；迟到取消由client_sequence拒绝。新增engine.claim和带owner/session/generation的engine.close，旧组件卸载不能关闭新组件输出。共92统一操作，无数据库迁移。

API的DialogueWorker共用同一个Service/EngineBroker；已提交音符/状态/参考修改在事务外自动更新当前播放修订，历史查看不被无关head更改强制重定向。SDK/CLI的audition.set直接应用设备意图；UI保存时带本次已应用的engine_ack，避免重复定位或迟到保存重开已关闭窗口。执行失败独立暴露，不能把已提交修改回报为SQL回滚。AI工具回执带native_playback的desired/applied_revision、加载/失败轨道；本阶段模拟模型工具调用＋真实设备验收，不冒称真实Codex新会话已经测试。

发现并修复静音指数斜坡的极小残值：改为3ms有限斜坡，精确归零。299项完整后端通过（132.33s）；包括共享Service的AI编辑工具实际改变设备声部、SDK意图、过期ack不重开、owner移交、原生试听保持时钟和取消顺序。NativeTransport单元、TypeScript、Vite、Ruff和契约生成检查通过。

独立8794端口、Edge headless、1536×864 CSS／DPR2.5即3840×2160截图，最终`data/maintenance/native-engine/n4/ui-v4/browser-report.json`八组流程通过：

- 播放中推子/静音/独奏与恢复，原曲两条波形＋静音旋律。
- SDK定位和原曲/重建切换保留播放位置；修改音符后实际声部改变、另一插件PID保留，前端刷新修订后继续播放。
- 浏览器主线程阻塞650ms，设备继续推进至少24000帧，underrun0。
- 暂停、空格重播、主页试听、旧工作台卸载不误停主页。
- JS错误0、浏览器audio响应0、playback.live请求0、新渲染任务0。

原始`ui-v1`记录静音残值导致的严格归零失败，修复后使用新夹具重跑；未覆盖失败。最终主页/原曲/重建截图在同run。还需N7的最终候选真窗口/冷暖/回环、完整长曲与真实模型会话，不能用此短夹具提前完成整个目标。

一次候选Vite构建误写共享apps/web/dist；已从提交源码独立重建并恢复原`index-B3LrRqPN.js`，正式服务没有重启。此后Settings.web_root指定独立候选输出，构建不再触碰正式静态目录。最终候选为`n4/candidate-v3`，正式仍旧WebAudio，默认切换留到N7。

保护核对`n4/protection.json`：正式九表/head与14项固定文件SHA均与N1基线一致。测试API/浏览器/引擎均已退出，无H库写入、NAS、音乐模型调用或旧宿主替换。N4完成基础产品接线；工程资源释放/删除并发/重开/目录与缓存管理进入N5，完整实时图/导出进入N6，目标保持active。
