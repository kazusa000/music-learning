# 原曲与重建双编排

2026-10-21。用户确认恢复SheetSage2，加入BS-RoFormer Leap；原曲呈现音频波形与静音旋律MIDI，重建保留真实人声并对伴奏转录。这次不删除或改写现有正式工程，不动音源库及Pianoteq固定验证批次。

## 产品行为

原曲／重建共用歌曲时钟、循环、缩放和讨论。原曲视图有人声时严格三轨：人声音频、人声旋律MIDI、伴奏音频；纯音乐只有原曲音频。旋律MIDI没有插件，不参与音频独奏计算，不能因为选择旋律而屏蔽录音。原曲两条有声轨支持即时音量、静音、独奏；不叠播完整原曲，不分别归一化。重建为真实人声音频轨加MuScriptor伴奏MIDI，音色、音符、现有实时混音路径继续使用。

原曲上方显示段落及和弦，播放高亮，可点击定位、双击修正文字。旋律音符可以点击定位后修正音高、起止或删除。参考修改通过统一API建立新修订，不自动渲染。原曲参考与改编MIDI分开保存，普通改编不会覆盖原曲标注。

导入类型为自动／有人声／纯音乐。明确纯音乐完全跳过Leap；自动模式先用SheetSage人声旋律的数量及有效覆盖判断，结果记录决策与算法，用户可覆盖。自动判断是启发式，并非人声检测准确率保证。

## 持久数据与接口

Snapshot新增可选source_reference，旧快照仍可读取，不迁移或改写已有记录。保存独立轨道、对齐音频片段、静音旋律、和弦／调性／段落、波形及原始分析资产。严格检查录音身份、轨道角色、音频全长／偏移、旋律归属、时间范围和波形齐备。提交与保留策略均校验、保活参考素材。

新增reference.get和reference.update，共80操作，统一Service/API/SDK/CLI及生成契约；保留expected_revision和幂等。music.query增加view=original/arrangement，时间均为原曲绝对样本。summary同时提供原曲参考，原曲详细查询能分页获取旋律及和声。dialogue.submit携带workspace及独立reference_track_id，原曲声部只作为讨论焦点，不会变成改编权限上限；默认查询跟随正在查看的工作区，可显式读取另一视图。AI工具提示要求比较同一区间两份数据，从实际当前MIDI解读修改后的和声；不把原曲标签当作已更新的改编和弦，不要求每次编辑重跑整曲模型。引用标记view，原曲引用定位到原曲界面。

原曲参考修正沿用既有试听retarget规则：保留位置、循环与模式，刷新不会因旧试听修订退回旧参考。波形按SHA缓存，最多8份；改和弦或旋律不重复请求同一波形，切换界面可复用，读取时有状态提示，失败可重试。整曲密集和弦只抽稀绘制文字，不改变标注区间；当前和弦始终在上方显示，放大后逐段显示，避免标签互相遮挡。

## 模型与流程

normalize → Beat This → SheetSage2 → 可选Leap → MuScriptor large → assemble → render → calibrate → validate。

Beat This和SheetSage读取完整原曲，统一网格仍由Beat This提供。Leap只分离；和弦、旋律、段落由SheetSage提供。有人声时MuScriptor输入为伴奏SHA，纯音乐输入规范化原曲。每阶段有独立产物、进度、内容校验、缓存与可恢复任务；音符时序不量化。

- SheetSage2沿用已核对的b83c3d79a6d98bcffaf8f9ed112c90aa887887bc与固定MERT-v2-FullSong底座，使用新reference目录，不碰以前清理被拒的旧目录。
- Leap使用pcunwa/BS-Roformer-Leap的4e47d6662ae82eaa8b4ac4329fe66099a843b48e，Xe人声权重与配置，所有文件上游SHA或Git blob校验。推理依赖bs-roformer-infer 0.1.5，独立哈希包锁及separation环境。
- reference和separation环境与transcription隔离，CUDA12.8/PyTorch2.8.0，逐阶段退出进程释放显存。所有下载／临时产物在项目E目录，不安装系统包。
- Leap整曲输出在CPU累加，只把一个分块放入GPU。包内ISTFT未指定输出长度，分块先补齐hop边界后裁回有效样本，不更改预训练权重或原始时轴。采样率适配保留原曲帧数。伴奏采用原曲减人声的互补残差，保持共同增益与相位；不会分别用两套提取结果造成合奏不一致。

来源：[SheetSage2官方模型说明](https://huggingface.co/m-a-p/SheetSage2)、[Leap权重及配置](https://huggingface.co/pcunwa/BS-Roformer-Leap/tree/4e47d6662ae82eaa8b4ac4329fe66099a843b48e)、[推理实现](https://github.com/openmirlab/bs-roformer-infer)。

## 验收记录

独立证据根data/maintenance/reference-workspace，正式工程不用于修改。首次真实SheetSage305秒完成，313人声旋律、303器乐旋律，原始事件及完整窗口保留。真实Leap重试整曲33块完成，输出两条305秒立体声；互补相加最大误差2.9802322387695312e-08，实际RTX5080峰值分配6,836,172,288字节，用时70.578秒。首次不齐hop分块失败证据保留，未伪装成成功。

后续完整导入、浏览器、保护摘要及部署结果在收尾段记录。信号与流程检查不等同于音符准确性或人耳分离质量验收。

## 整曲、交互与AI收尾

独立8792最终整曲任务f5b5f5edf87e44198b3b4e53dde7b231成功，9阶段齐备，305秒／13,450,500源样本，61/61转录块，8,686伴奏音符，7真实VST3乐器轨加1条人声音频轨，混音与8条声部均通过完整性／非零／无数字削波检查。原曲参考3轨、313人声旋律音符、193和弦、18段落及1个调性区间。SheetSage30.50秒、Leap61.046秒、MuScriptor333.984秒，均为本机实际RTX5080观测，不外推其他歌曲。

首次组装暴露纯转换模块误导入SQLAlchemy，已把服务依赖移到接口分支，并加独立进程禁止数据库依赖的回归。首次人声原生渲染暴露扩展名为空的CAS路径不能被JUCE文件格式读器识别，现以共享wave_alias生成校验过的.wav硬链接（必要时复制），不修改CAS内容、插件或固定原生宿主；真实单轨音频渲染回归与最终8轨整曲均通过。首轮24秒无鼓合成夹具无法形成可用小节网格，保留失败事实，不放宽网格检查；改用真实分离伴奏的32秒片段后完整通过，separate只有decision.json，未调用Leap，原曲只有一条波形轨。

272完整后端检查通过，新增18项包含原曲／改编隔离、乐谱修正／试听修订同步、来源／范围／音轨校验、素材保留、显式纯音乐跳过、模型进程无数据库依赖、真实原生音频发声、原曲讨论焦点与权限分离；Ruff、契约导出与TS/Vite构建通过。没有原生源码更改或重建。

真实Edge验证包括1536×864/DPR2.5（3840×2160）及1100×720窄界面：原曲两条有声轨和静音MIDI、真实非零电平、播放中推子／静音／独奏、独奏不重启音频、原曲／重建切换保留时钟、重建人声波形、和弦与旋律修正、刷新后保持且改编MIDI不变、无新音乐任务、0 JS错误。前端上传实测纯音乐参数、9阶段进度与取消；旧工程原曲页提供建立新流程副本入口，原工程不改。两次真实Codex/gpt-6-luna/已有登录回读：第一次查询两视图，读到独立工程中修改后的MIDI音高40且原曲参考不变；第二次不在问题中指定原曲，通过workspace=original正确回答原曲和弦。

正式8765已部署。正式1工程／3修订／2任务／7轨／7,098音符，九张核心表及全部head摘要前后一致；Pianoteq固定11文件SHA一致。正式主页只读浏览器检测三种导入类型与0 JS错误，未提交正式导入／修改／试听。没有访问NAS，没有音源库写入，也没有启动或停止Pianoteq批次。

## 测试素材收尾与限制

验收完成后先通过独立8792的projects.delete删除本次4个独立工程，157个专属CAS登记项／文件已移除（登记逻辑字节1,308,934,274，不作为物理释放空间的测量）；全部返回completed，正式工程没有删除请求。8792随后停止。小型报告、校验摘要、4K截图、模型原始标注等保留在data/maintenance/reference-workspace。

随后只对以下本次新生成目录／文件尝试固定白名单文件系统清理，自动审批在命令执行前以blocked by policy拒绝。该命令删除0项，17个目标仍在，不换工具、回收器或应用接口绕过本次拒绝：

- data/t/reference-v1
- data/t/reference-v2
- data/t/reference-final-unit
- data/t/reference-full-v1
- data/t/reference-full-v2
- data/t/reference-full-v3
- data/t/reference-full-v4
- data/t/reference-full-v5
- data/t/reference-alias-v1
- data/t/reference-source-final
- data/maintenance/reference-workspace/accept/preflight-v2
- data/maintenance/reference-workspace/accept/application（工程已清空，临时数据库目录仍在）
- data/maintenance/reference-workspace/leap-real
- data/maintenance/reference-workspace/leap-real-v2（含独立模型探测音频）
- data/maintenance/reference-workspace/instrumental.flac
- data/maintenance/reference-workspace/instrumental-real.flac
- data/maintenance/reference-workspace/accept/upload-probe.wav

以前所有被拒路径同样未触碰。运行时模型／环境是新功能必需资源，保留在E项目data内。新导入直接使用新流程；旧工程保留，打开“原曲”后可按新流程建立副本。本阶段原曲支持标注与旋律修正，不包含原录音剪切、拉伸或根据参考MIDI重新合成人声。
