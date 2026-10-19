# P3 制作服务与工作台实施记录

状态：P3 已完成。阶段退出证据见 [P3 验收报告](benchmarks/p3-daw.md)，下一步 P4，先核对周额度。

实现顺序：

1. 有范围的原子编辑事务、版本差异/恢复、片段编排与完整状态引用；保持不可变历史及幂等/冲突处理。
2. 从精确 revision 编译原生处理图，执行真实 VST3、效果链、总线/发送/侧链、自动化与补偿；独立持久渲染任务、逐轨/总线产物及冻结/失效回读。
3. 统一能力发现、SDK/CLI、测量与导出；确定性夹具和真实乐器/效果器验证。
4. 重设计主页和工作台；可缩放卷帘、插件链、版本与差异、基于音频时钟的定位/循环/A-B/独奏静音。
5. 用现有本地完整素材验证制作、状态恢复、浏览器与同步边界；记录验收与 Git 提交，核对周额度后才进入 P4。

本轮不访问 NAS，不重跑整曲识别来代替 DAW 编辑；P2 原始分析和已发布音频保留。

## 制作接口

统一入口仍为 `POST /v1/commands`、`Client.call` 和 `music_learning.cli call`。当前 38 项能力由同一契约生成，新增操作：

| 操作 | 实际语义 |
| --- | --- |
| projects.fork | 从指定版本创建独立工程，共享不可变 CAS 文件；复制分析记录并保留来源，原工程 head 不变 |
| edits.apply | 原子、有范围的批量修改；返回真实新 revision |
| revisions.compare / restore | 按稳定 ID 比较实体；恢复创建新版本，不删除历史、不冒用旧音频 |
| controllers.query | 按版本、轨道和半开样本区间查询 CC |
| graph.get | 轨道、完整插件状态引用/参数、路由、自动化和冻结引用 |
| plugins.presets | 指定版本的宿主管理完整状态、参数、描述、资源映射；不是厂商私有预设浏览器 |
| renders.list | 指定版本已完成的制作产物；与 P2 preprocessing.get 分开 |
| audition.get / set / report | 持久试听意图、独立序列号和浏览器实际回报；旧序列的迟到回报被拒绝 |

`jobs.submit` 新增 `project.render`、`audio.measure`、`project.export`，同样支持持久队列、取消、重试、租约和 fencing；子进程不写数据库，全部素材校验后才发布引用。Alembic 0003 新增 renders 与 audition；0001/0002 保持冻结，迁移前的一致 SQLite 元数据备份继续保留。

渲染/导出把默认目标写成显式 revision_id；重试以原 job.revision_id 为准，head 改变也不改换歌曲版本，旧 null 目标记录同样保留原目标。

### 编辑与范围

`scope` 包含 track_ids、start_sample、end_sample、structural。音符/CC/注释修改同时检查旧事件和新事件；不能借更新把选区外事件移进来。轨道、插件和自动化要求全时段 structural scope；路由要明确包含所有受影响轨道。删除轨道不偷偷级联删除依赖，调用者应在同一事务显式处理片段、音符、插件、自动化、路由和顺序。

支持 `put/delete` 的集合为 tracks、clips、notes、controllers、plugins、automation、annotations；实体为对应的封闭类型。另有 timing、routing、track_order、arrange_clip、freeze。所有写入仍须 expected_revision 和 idempotency_key；任一步失败不发布 revision/receipt/projection。

片段编排支持 move、duplicate、split、trim。MIDI 音符用 clip_id 明确归属；未归属却与片段相交的音符必须先分配。分割跨界延音、含糊裁剪和不完整的最后一轮 MIDI 重复会拒绝，不悄悄截断。音频循环和 MIDI 音符循环需要显式 content_samples；CC 保持轨道样本位置，不伪造踏板重复。MIDI 片段音频增益未实现，应修改力度或混音自动化。

采样锚定音符保持原演奏位置。显式 timing_mode=beat 的音符需 notation_beat、notation_duration 和具体 TimingMap；修改拍点时重算这些音符，样本锚定音符不随之量化。弱起通过负 PPQ 和小节起点传给原生插件；VST3 传递小节起点 PPQ，不直接传数字小节号。P2 历史产物不改写，新流水线也使用这套 transport。

### 插件、路由和自动化

插件实例保存原生完整 opaque 状态、描述、参数、版本/二进制摘要和资源映射。插入、删除、排序、旁通、归一化参数及完整状态恢复通过 edits.apply；插件参数须来自真实捕获的 descriptor，离散步数须合法。没有经过适配器验证的不可自动化参数写入会拒绝；物理单位输入/私有菜单不能伪装成支持。

原生 C++ 执行有向无环立体声图：轨道效果链、总线、推子后发送、指定插件的立体声侧链及主输出。逐节点渲染先消除该节点实际插件延迟，再以共同源样本时间汇合，输出每轨/总线和整曲 WAV。侧链跟随相同时间基准，主链前置延迟也计入对齐。动态插件延迟变化导致本次渲染失败，不能沿用错误补偿。当前上限 64 个原生图节点（含主输出）、600 秒正文、60 秒尾音；执行前检查本地空间预算。

链内后级插件收到的 MIDI/CC、自动化和 tempo/meter 也计入其前级延迟，不能只裁掉整条链的输出延迟。当前使用源轨 MIDI，不串接插件生成的 MIDI 输出。

插件自动化支持 sample-aligned step/linear，值为归一化参数。混音自动化使用 track_id，parameter_id 为 gain 或 pan：gain 的 0～1 映射 -120～24 dB，pan 映射 -1～1；mute 是真正零输出。总线 solo 包含其上游，保留下游路径；侧链跟随 solo/mute 的信号遮罩。全图反馈、实时演奏、任意多声道布局尚未实现。

渲染保存精确 revision、快照摘要、宿主与实现摘要、完整准备状态、实际参数、资源、波形和测量。执行期间实现摘要变化会失败并要求重试。插件带随机性时不要求重新合成逐位相同，实际已生成文件永久绑定其版本。未完成、取消、超时、插件缺失或校验失败不发布成功音频。

### 冻结、导出和测量

freeze 必须引用当前 revision 的已完成整曲 render_job_id；冻结 WAV 包含真实推子/声像结果，重放不重复施加。任何后续编辑保守地使全部冻结引用失效，显式再冻结才能复用；无 render_job_id 的 freeze 操作表示解冻。冻结轨渲染跳过插件安装与资源加载，缺失插件仍保留原状态和既有冻结版本；解冻后缺失则返回 plugin_missing，绝不静默换音源。

局部渲染从零点预滚到选区，保留插件/踏板/效果历史，在选区终点停止未来音符和音频输入，再输出显式尾音。已冻结的完整轨若无法隔离选区后的事件，要求先解冻。产物的 start_sample 是源时间偏移，不能把片段文件零点当原曲零点。

project.export 的 MIDI/WAV 明确选择 revision。WAV 必须引用同版本已完成渲染；MIDI 为类型 1 / PPQ 32760，保留采样演奏时刻经 TimingMap 换算后的 tempo/meter/CC，不作量化。弱起用元数据与首个完整小节 marker 说明；外部 DAW 的默认小节编号可能不同。MIDI 时基有限，回读误差单独测量，不将其当作原始样本数据精度。

audio.measure 通过固定 FFmpeg 解码并测 EBU R128 integrated LUFS、true peak、LRA，另记录 peak/RMS、削波、静音样本比例和频段功率。全静音的无限响度返回 null。数字不是主观听辨，也不代表 AI 已经听过。

## 工作台

主页使用统一的深色、低饱和蓝紫配色和音乐图形；工作台分为轨道区、编曲时间线、分析/音源/版本面板和固定试听栏。卷帘以 Canvas 绘制，支持整曲/声部、缩放、平移、点击定位与段落跳转；保持只读，制作由 AI 接口完成。

同一个 Web Audio 时钟调度选定声部，支持独奏/试听静音、循环、原曲/重建和历史版本参考 A/B。位置显示使用 getOutputTimestamp，A/B 调度用处理时钟，避免把输出设备延迟当成新偏移。新播放请求有 generation；本地写入期间不重复消费同一条轮询命令。首次浏览器播放需要激活，实际状态通过 audition.report 回读，不把持久意图冒充已发声。

前端渲染状态来自真实任务；新版本没有渲染时禁用其重建入口，旧音频仍留在旧版本。当前没有占位聊天或假改编，产品对话与自然语言事务分别留到 P4/P5。
