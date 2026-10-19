# 工程讨论与研究笔记

P4 使用固定的 Codex App Server `0.163.0-alpha.10.2`，由应用保存讨论，供应商 thread 为 ephemeral。官方接口依据为 [App Server](https://learn.chatgpt.com/docs/app-server)：initialize → thread/start(dynamicTools) → turn/start → 流式事件/工具回传 → turn/completed；停止走 turn/interrupt。动态工具属于实验接口，升级必须重新验证。

## 运行与登录

默认使用独立 `D:\music-learning-runtime\codex-home`，未登录时返回 `needs_login`。运行 `runtime/tooling/Scripts/python.exe scripts/codex_login.py` 在本机登录；不把 Token 发送给网页或聊天。前端可显式选择“使用既有 Codex 登录”，SDK 使用 `login_mode="existing"`；仅复用官方 managed login，不解析/复制凭据，不修改全局配置。这是对 P0 既有登录探测模式的正式产品接入。

每次真实提问使用相应 Codex 账户额度。应用不会重置额度，不调用重置卡功能。当前连续开发的阶段停止规则见 [额度约定](16-continuous-goal.md)。

`scripts/app.py start` 启动 API 内的独立讨论执行通道和既有音频 worker。讨论队列不占据音频执行槽；渲染/GPU 任务仍全局串行。讨论通道全局最多一轮执行，租约/令牌阻止重复发布；进程丢失后标失败，须显式重试，不自动再次消耗模型额度。

## 统一接口

所有入口共享契约、Service、幂等收据及 API/SDK/CLI，共 45 项已实现操作。新增：

| 操作 | 用途 |
| --- | --- |
| music.query | 固定 revision 的 summary/events/annotations/compare/metrics；查询最多 100 项，可按 annotation_kind 筛选 |
| dialogue.submit | 保存用户问题、revision/快照摘要、绝对样本区间和声部，再排队 |
| dialogue.get / dialogue.list | 回读文本、状态、上下文、工具依据、引用和错误 |
| dialogue.cancel | 停止排队或运行中的讨论，保留已有结果 |
| dialogue.retry | 创建绑定原版本/范围的新轮次，保留原失败，不覆盖历史 |
| notes.save | 保存带版本、范围、来源轮次和 draft/accepted/disputed 状态的笔记 |

写入仍须提供当前工程 `expected_revision` 与幂等键；讨论目标可显式选择历史版本，重试始终使用原问题的固定版本。上下文在提交时冻结，后续工程修改不能改变该轮依据。

SDK `Client.discuss(...)`、`wait_dialogue(project_id, turn_id)` 提供便利方法；CLI 使用 `music-learning call <operation> --args <本地JSON> --expected-revision <head>`，完整参数和返回 schema 由 `describe_capabilities` 发现。

## 事实与引用

模型只获得受限 `music_query` 动态工具，不能指定另一个工程或版本。服务从权威快照计算局部音域、起音密度、音级分布，返回分页音符/注释和 provenance；摘要按注释类型平衡采样，避免满屏拍号遮住和弦/段落候选。compare 返回同版本两段独立统计。metrics 只给已保存渲染测量，引用绑定实际渲染区间，不能把整曲峰值当成局部测量。

单次工具输出限制 45,000 字符、每轮最多 12 次有效请求，180 秒生成时限；历史至多最近 10 条、14,000 字符供方向参考。全曲音符不自动注入提示词。错误明确返回，超大结果要求缩小范围/分页。

引用由服务生成并验证，格式 `[位置](music://citation/<id>)`；完成时必须查询实际工程，且至少有有效引用，伪造引用不能成功。引用保存工程、revision、绝对区间、可选声部/事件 ID 和来源类型；前端点击后切换对应版本并定位、聚焦声部和区间。模型的音乐解释与源候选分析不同，笔记默认 draft，不会自动变成已接受的经验或模型修正。

## 持久化与前端

后续响应优化已接入：空闲时事件触发刷新及 15 秒兜底，生成中每 900 ms 仅回读活跃轮次；工具详情按需展开，显式登录模式跨刷新保留。完整验证及未优化路径见 [加载与响应](23-responsive-loading.md)。以下轮询描述仍指服务端检查点文本的回读，不表示每次重新获取全部历史。

冻结迁移 0004 新增 dialogue_turns，旧迁移/快照不重写，迁移前自动备份 SQLite。问题、生成中的文本、工具结果、引用、错误和终态保存在本地数据库；用户/最终助手消息和笔记仍使用 conversations。流式文本按检查点写入，前端轮询显示，刷新可继续观察；连接中断和取消后，部分文本保留并标明未完成，不能冒充最终结论。取消后迟到结果受租约和终态校验约束。

工作台右侧默认讨论，支持整曲或当前循环区间/声部、登录选择、停止/重试、展开工程查询、引用定位、研究笔记和刷新恢复。暗色工作台沿用 P3 波形/卷帘、轨道颜色、版本和试听，390px 宽度无横向溢出；未增加 MIDI 键盘。

当前 P4 只讨论、保存笔记；自然语言改编与自动渲染属于 P5。没有向模型发送音频，不声称模型听过原曲。根据转录/结构候选给出有根据的理论解释，也不能证明候选音符、和弦、配器或听感准确。
