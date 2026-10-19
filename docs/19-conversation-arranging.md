# 对话改编、自动渲染与贝斯混合

P5 在 P3 编辑/制作和 P4 讨论之上增加有明确授权范围的对话改编。普通讨论默认只读；前端选择“改编”，或 SDK `discuss(..., edit_scope=...)` 才开放制作工具。全部操作继续共用 Service、API/SDK/CLI，当前共 48 项。

## 编辑事务

`dialogue.submit` 保存原 revision、查询区间及 `edit_scope`。范围含 track_ids、start_sample、end_sample、structural；模型不能指定/扩大这些权限，历史版本须先 fork 后改编。轨道/插件/自动化要求整轨时间范围和 structural；该授权不能让模型越过讨论选区改写音乐事件。

Agent 仅获得 `music_query`、`production_query`、`music_edit`。前两者返回权威数据、有限分页和真实插件档案；music_edit 只接受操作批次和说明，服务补入固定工程、原版本和范围。同一轮只能提交一个批次；所有写入仍走 P3 的旧/新事件范围、归属、参数、路由、资源和快照校验。

新增 `arrangements.apply` 在同一 SQLite 事务中完成：校验运行中讨论和租约 → 校验 expected_revision → 应用全部编辑 → 提交不可变新版本 → 排入绑定新版本的完整 project.render → 保存独立收据。任何一步失败全部回滚；幂等请求只产生一个新版本与渲染任务。`arrangements.get` 可回读提交前/后版本、差异、渲染 job 和快照摘要，模型后续断线不会丢掉已提交制作。

音乐实体的 AI 修改由服务写入 `provenance.origin=ai_edit`、source_id=turn_id、review=unreviewed，不把 AI 改写伪装成原始模型分析。新版本使冻结失效；原曲、旧版本和原分析不覆盖。编辑后查询切换到该事务明确返回的新版本；此前引用仍属于旧版本。

编辑成功只表示事务提交、渲染排队。前端单独显示真实渲染进度/失败，完成完整产物后才可试听；Agent 不根据一句“已修改”宣称已经发声。失败后收据与部分讨论可查看，旧音频不改标成新版本。`dialogue.retry` 保留原上下文；如果期间已经编辑或有人改动工程，旧请求会冲突，不能默默再改当前 head。

## 音色、参数和自动化

`production.query` 返回轨道、真实插件身份与同工程已捕获音色。指定 plugin_id、parameter_search 和分页可读公开参数名、normalized 值、步进/自动化能力；不把数千参数全部注入上下文，不暴露私有磁盘路径。

`patch_plugin` 是统一 edits.apply 的新增操作：可修改少量公开参数、旁通、排序，或用 `preset_plugin_id` 复制同工程已捕获完整插件状态/资源/身份，同时保留目标实例 ID、声部归属、插入位置和旁通设置。最终仍验证原生档案和 normalized/discrete/nonautomatable 约束。它不代表任意厂商私有预设浏览器都可操作。

自动化使用既有采样时钟上的 step/linear 曲线；宿主在加载、参数设置和 prepare 后、处理音频前捕获初始状态，因此回读参数对应基础值，不能当成自动化末尾值。完整曲线同时保存在原生工程中；执行时序由确定性原生夹具验证。插件完整状态及原生回读继续保存。

## 贝斯混合与导出

既有 `jobs.submit(kind="project.export")` 支持 midi/wav，P5 新增 format=hybrid。hybrid 必须明确给出 revision、同版本已完成的整曲 render_job_id、replacement_track_id，以及替换区间；要求原曲和 vocals/drums/bass/other 四条唯一、完整、同采样率的 stems。

独立生产子进程在统一绝对样本时钟上求和：替换区间内用原 vocals+drums+other 加新的贝斯逐轨渲染，原 bass stem 不进入求和。区间外原曲 PCM 保持不变；默认 20ms 线性淡化只在区间内部。局部替换在结束处淡回原曲，不将替换尾音泄漏到区间外；整曲替换保留声明的渲染尾音。可显式调整 replacement_gain_db 和 fade_seconds；输出峰值/削波计数和完整 recipe，不偷偷加限幅或改响度。

输出 WAV、recipe/report、素材摘要、来源、区间、淡化与尾音绑定确切 revision，经过既有任务租约、Job Object、代码指纹、CAS 原子发布与校验流程。`audition.set(mode="hybrid", hybrid_job_id=...)` 只接受同工程同版本的成功任务，浏览器采用与其他模式相同的 Web Audio 调度时钟。

前端的改编收据展示事件差异、真实渲染状态、MIDI/WAV 导出和贝斯混合入口；工具查询、原/新版本引用、笔记、版本 A/B 保留。导出失败不影响已提交工程；新版本的可见状态和音频来源须以服务器实际结果为准。

## 持久化与兼容

冻结迁移 0005 新增 arrangements 表，独立于流式讨论文本；旧数据库在备份后升级，不重写旧快照。P5 可选编辑权限和 hybrid audition 字段为空时，幂等哈希兼容 P4/P3 的既有收据。源码/文档进 Git，用户问题、数据库、音乐、渲染和私有证据仍在本地忽略目录。

这些是可检查的制作与听辨入口，不保证自动改编一定悦耳。直接音乐音频理解仍未验证；最终音乐判断须实际试听，不能用事件差异、峰值或模型修辞替代。
