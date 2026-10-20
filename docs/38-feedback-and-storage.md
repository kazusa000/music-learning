# 播放边界、音色任务与存储实查

2026-10-20。本次不清理、不迁移用户数据，不重试正式 AI 请求或 Keyscape 音色，不启动第二批 Keyscape 验证。

## 磁盘增长的原因

用户音源库仍在 `H:\music\Sound Library`。E 盘主要积累了以前多次整曲渲染的任务目录、CAS 音频、模型环境、验证及临时文件。任务工作目录长期保留、缺少清晰的产物保留策略，是本项目的存储管理缺陷。

只读扫描 `E:\Intrest\music-learning`，跳过 484 个重解析目录，4 个历史长路径文件读取失败：文件大小合计 118,951,691,193 字节（110.79 GiB）；按 Windows 文件 ID 去重为 79,809,287,220 字节（74.33 GiB）。这是文件内容长度，不能当作精确分配空间或可释放容量。任务到 CAS 的硬链接、uv 缓存到 Python 环境的硬链接会被普通目录大小重复计算。

| 目录 | 文件大小合计 | 内容 |
| --- | ---: | --- |
| data/app-data/tasks | 43.69 GiB | 历史处理、捕获、渲染的工作产物 |
| data/app-data/objects | 21.28 GiB | CAS 素材，部分与 tasks 为同一硬链接文件 |
| data/envs | 14.34 GiB | 模型与应用环境，部分与 uv-cache 共享 |
| data/uv-cache | 7.36 GiB | Python 包缓存，不能与 envs 直接相加算物理占用 |
| data/benchmarks | 6.03 GiB | 历史验证工程与输出 |
| data/tmp | 3.92 GiB | 历史临时与 pytest 目录 |
| data/models | 3.79 GiB | 音乐分析权重，与 H 盘乐器采样库不同 |
| data/app-data/backups | 2.75 GiB | 数据库备份，不含完整 CAS |

扫描期间仍有后台任务，数字是当时快照。原始审计在忽略目录 `data/benchmarks/storage-unique-20261020.json`；首次扫描 `storage-audit-20261020.json` 的 unique 字段因 Windows DirEntry inode 为零不可用，只可参考其目录明细。后续清理必须先核对活动任务、CAS 引用、原曲、模型原输出和备份依赖；本次没有删除文件，也未实现自动保留/回收策略。

### 用户询问哪些可删后的清单

再次只读核对，正式任务没有 queued/running/cancel_requested；Keyscape 原批次仍运行。以下精确路径记录在忽略的 `data/benchmarks/cleanup-whitelist-20261020.json`，没有执行删除：

| 第一批候选 | 数量 | 目录大小 | 按候选内链接计数，最后链接对应的内容长度 |
| --- | ---: | ---: | ---: |
| data/tmp 下已结束的 pytest 目录 | 88 | 3.908 GiB | 3.677 GiB |
| runtime/downloads 下 zip/exe 安装归档 | 6 | 0.512 GiB | 0.512 GiB |
| data/app-data/backups 的旧数据库备份 | 8 | 1.402 GiB | 1.402 GiB |

备份保留最近两份 `projects-1792514058396072800.sqlite3` 与 `projects-1792510122437148600.sqlite3`，不是删除 backups 整目录。第一批独立内容长度约 5.59 GiB，不是实测释放量。检查未发现候选路径出现在当前进程 cwd/参数中；实际清理前还须重查，并跳过重解析点。`data/tmp` 是 Codex/模型使用的临时根，不能整目录删除。

已完成的 19 个 project.render 工作目录共 26.907 GiB，其中最后链接内容约 16.048 GiB，包括约 7.77 GiB 临时资源副本及多组未发布/重复音频；这是第二批可核验候选，不是整目录删除白名单。失败预处理目录也不能按 failed 标签就删：其已完成阶段可能有需要保留的原始模型产物。必须逐项确认 CAS 发布、内容一致及恢复依赖，保留日志/清单/原始输出后再列入清理。

`data/benchmarks` 当前 6.25 GiB 包含原始模型验证、真值夹具和浏览器证据，不能全部当垃圾删除。`data/uv-cache` 的 7.36 GiB 大多与环境共享，按本次链接计数只有约 0.07 GiB 内容可单独回收，非优先目标。保留 objects、正式数据库/用户工程、模型和环境、H 音源库及正在运行的 Keyscape 验证目录。

## 音色准备误用了整曲预算

正式失败任务 `2098b97767b6482bba83a65fad2d25a3` 的 LA Custom C7 - Natural 资源清单只有 195,753 字节；Keyscape 大采样由 H 盘安装库引用。失败原因是小型捕获任务沿用了整曲流水线默认 3 GiB 剩余空间门槛，不代表要复制整套 Keyscape 到 E 盘。

现在 `instrument.capture` 调度使用独立设置副本：实际依赖资源大小 + 默认 256 MiB 运行余量 + 16 MiB 短探针/状态预算。原整曲 3 GiB 配置不变。真实资源仍逐项计数，空间不足仍明确失败，错误显示资源、可用与保留容量。没有修改后台 Keyscape 批次固定的捕获/验证代码或原生宿主。

## 播放与反馈

- 原生导出可以包含歌曲结束后的释放尾音。之前暂停把尾音时间直接存成原曲位置，触发 source clock 越界。现在 UI 的保存/报告按歌曲时钟夹紧；版本冲突重绑定到较短版本时，重新校准位置及循环，失效循环关闭。SDK 显式越界仍拒绝，返回稳定的 `transport_range` 和可读信息。
- 手填循环起终点不合法时不能提交。原音乐数据、revision fencing、幂等和历史目标不改变。
- 对话“回复结束”与工程实际修改分开；失败捕获标“音色未更换”，真实提交收据标“工程已更新”。无渲染收据只说“修改已保存”，不声称已播放成功。旧对话正文不改写。
- 对话不再提供整曲 WAV/混合导出快捷键；最终音频导出位于独立混音页。正常导出不再放入错误横幅，进行中的 WAV 导出禁用重复按钮。

## 验证边界

- 后端完整运行 159 项：158 通过，1 项仍断言旧英文错误；修改该测试为检查稳定错误码后，该项单独通过。新三项在独立数据目录通过，其中真实 sfizz 捕获、状态恢复及发声验证在模拟 500 MiB 可用下成功，16 MiB 下拒绝；未调用真实 Keyscape 实例。
- TypeScript、前端构建、契约漂移检查、修改文件 Ruff、audition-sync / turn-status / transport-start 单元验证通过。没有新契约/迁移。
- 独立 8774 浏览器工程：8 秒源音频，原生实际生成 2 秒尾音。播放至 8.378 秒后暂停，保存为 8 秒，无错误横幅；9 秒循环终点禁用提交，恢复 8 秒后可提交。工程 head 与 1 个既有任务不变。证据 `data/benchmarks/feedback-tail/{browser.json,after-pause.json,paused.png}`；已关闭测试页与服务。
- 此验证证明时钟、任务预算和反馈逻辑，不代表正式 C7 已更换，也不代表人工音乐听辨通过。
- 代码提交 `82d7edf` 已快进合入原分支 `feat/user-instrument-intake`，重新构建并启动正式应用。部署前后 13 工程 head/快照摘要、64 个任务及 10 项受保护文件摘要一致；无活动正式任务/对话。Keyscape 原监督进程 PID 31420、创建时间保持一致，仍在运行。回读证据 `data/benchmarks/feedback-tail/deployment-{before,after}.json`。
