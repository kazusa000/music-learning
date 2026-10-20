# 手动换音色

2026-10-20。音色与效果页现在直接更换乐器完整状态，不再生成给 AI 的草稿。

## 操作

选音轨 → 选乐器 → 点击音色。当前音色显示在音轨选择器下，列表标记“当前”。音轨选择与左侧同步；列表独立滚动，音轨、乐器和搜索保持可见。删除引导段落、数量卡片、已验证筛选及标签、让 AI 选音色按钮。效果与参数折叠在底部。

加载条对应三个真实阶段：读取音色（持久 instrument.capture）、应用到音轨（edits.apply）、加载声音（常驻插件及首块 PCM 就绪）。阶段条不是虚构的下载百分比，显示经过秒数。大音源冷启动仍需等待插件加载，本次没有承诺秒切。

## 规则

- 已有乐器替换完整插件状态与资源，保留音符、音量、声像、效果链和其他轨道。跨插件或已不存在的参数自动化删除，仍兼容的自动化保留。
- 空白乐器轨可直接安装音色；已有后级效果后移，乐器置于首位。空 MIDI 轨不启动无音符音频进程，添加音符或片段后由现有增量播放流程加载。
- 点击时绑定具体轨道和插件。加载中改选其他轨道不会把结果应用到新选择；读取期间其他音符修改会合并到最新 head，目标乐器被另行改动则显示冲突。
- 读取失败不编辑。已应用后加载失败保留真实工程状态，“重试加载”只重试音频加载，不重复捕获或写修订。离开工程后不再提交编辑。
- 与自动预加载共享同一加载请求及 revision sequence，防止重复开插件和互相取消。关闭页面的 live.close 使用 keepalive。
- 沿用统一 API/SDK/CLI 的 jobs.submit/get、edits.apply、playback.get/live.*；没有新增权限、模型调用、迁移或整曲 render。内部 revision 是工作状态；仅手动 versions.save 产生用户版本。
- 首次捕获缺少可复用的完整状态时，既有后端仍会做短音频发声/重开验证；这不是整曲渲染。本次最后一款测试产生了两份短 probe WAV，不能声称完全没有生成 WAV。

## 本次验证

前端构建及六组 Node 回归通过：手动替换/空轨/效果与自动化、并发加载去重/重试/关闭、播放启动、即时音量静音独奏、有效静音显示、试听状态冲突。18 项相关后端测试通过。

真实浏览器在 E 盘独立数据库验证 305 秒、823 个复制钢琴音符及一条持续测试轨：播放中 Wurlitzer → C7 Ballad，更换时拖动音量、开关独奏，持续轨原生 PID 保持不变，播放时钟不重启。随后捕获失败、应用后加载失败、仅加载重试均回读实际状态；空白轨先装 Chorus Wash，再换 Wurlitzer 200A 并完成。共四个 instrument.capture 成功，零 project.render、dialogue.submit、versions.save、mixer.set、mixing.save；音符总数一直为 824，手动版本零个。

最初空白轨仍强制启动插件并超时，已修正为空 MIDI 轨按内容延迟启动；修正后切换完成。没有把这个失败算作成功证据。

证据在忽略目录 data/maintenance/manual-preset-20261020：during.json、success-before-fault.json、capture-error.json、load-error.json、retry-loaded.json、final-state.json 及 manual-panel-final.png。正式工程未用于测试修改；NAS 未访问、H 音源只读、固定常驻宿主未改，Keyscape/Pianoteq 并行工作文件未纳入本提交。

隔离服务8774、Worker与自有浏览器已停止，formal-after.json 的正式元数据与启动前严格相等，正式 HTTP 已返回新版前端。新建夹具 data/t/manual-nz9w4u0m 清理被自动审批以 blocked by policy 拒绝，仍保留，不得换工具绕过；没有触碰其他历史拒绝目录。
