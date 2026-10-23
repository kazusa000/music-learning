# 对话的思考强度、Codex 快速模式与保存

2026-10-23。两项控制放在既有输入框底部操作行，保持聊天与输入区高度；思考强度为紧凑下拉框，Codex 快速模式为有开关状态的闪电按钮。原曲、重建、从零编曲共用同一组件。

## 独立控制

- `reasoning_effort`：推理强度。前端按当前账号模型目录的 `supportedReasoningEfforts` 显示可用档位，中文显示低、中、高、极高等，保留原始参数值。
- `fast_mode`：Codex 原生快速通道，独立于思考强度。关闭发送 `default`，开启按模型目录的通道 ID 发送 `priority` 或 `fast`。本机固定版本公布 `serviceTiers=[priority]`、兼容字段 `additionalSpeedTiers=[fast]`，实际接受并返回 `priority`。
- 官方说明：[Codex Speed](https://learn.chatgpt.com/docs/agent-configuration/speed)。快速模式会提高额度消耗，按钮悬停提示说明这一点；不会通过降低推理强度冒充快速模式。

`application.settings.set` 保存应用偏好。每次 `dialogue.submit` 在入队时解析默认值并固定到 payload；显式参数优先，之后切换偏好不会改变已排队任务。`dialogue.retry` 保留原参数；界面的“重试此问题”重新按当前选择提交。旧队列缺少新字段时保持原来的 low／普通通道。新字段不改变旧幂等回执的请求指纹。

Codex App Server 打开 `features.fast_mode`；`thread/start` 传 `serviceTier` 和 `config.model_reasoning_effort`，`turn/start` 传独立的 `serviceTierForTurn`、`effort`。目录明确不支持的设置会指出具体原因，不改成另一档继续。模型目录同时保留新旧通道元数据；缺少元数据保持兼容，实际服务端仍负责接受或拒绝请求。执行后的 provider 上下文记录实际模型、思考强度和返回通道。

## 会话保存

正式会话保存在 **`E:\Intrest\music-learning\data\app-data\projects.sqlite3`**：

- `dialogue_sessions`：工程内的会话名称与 ID。
- `dialogue_turns`：每轮问题、回复、状态、工具调用、模型和执行设置。
- `conversations`：消息副本与共享笔记。

关闭或重启应用不会删除会话。当前选中的会话 ID 还会在应用本地存储记住；会话正文以 SQLite 为准。删除工程时按既有永久删除规则清理其会话。

底层每轮 Codex 线程为 ephemeral，应用自己持久保存聊天。当前会给模型附带同一会话最近 5 轮、最多 14000 字符的历史上下文；完整本地记录的保存不受该上下文窗口限制。`data/app-data/dialogue/<turn-id>-<suffix>/` 为单轮进程诊断，不能代替会话数据库。

## 验证与边界

确定性 provider 检查独立控制组合、目录 priority 映射、入队后偏好改变、旧队列／回执兼容、重试、会话与回复在数据库重开后的恢复；最终短回归 22 项通过，另 15 项含本机协议握手与会话检查通过（有重叠，不相加）。本机固定 `codex-cli 0.163.0-alpha.10.2` 实际 `thread/start` 三次返回 default → priority → default，思考强度始终 medium，未发送任何生成请求；这验证配置接入，不宣称网络推理提速测量。

4K／窄窗报告：`runtime/test-reports/studio-ui-1792746543674935400/report.json`。按钮与下拉框的请求参数、偏好重开恢复、输入区高度不变、单行无横向溢出通过；既有原生短播放、音量／静音、笔记与滚动仍通过。界面的消息提交用拦截检查参数，实际后端链路由 provider 检查，未冒充真实 AI 生成。

测试均经 `scripts/maintenance/run-tests.ps1` 自动清理临时工程与浏览器数据。协议生成目录 `runtime/build/codex-protocol-controls` 的清理被自动审批以 `blocked by policy` 拒绝，保留且不换工具绕过；小型字段摘要在 `runtime/maintenance/ai-controls-20261023/protocol-fields.json`。不访问 H 音源库或 NAS，不启动音乐模型、正式渲染或额外音源验证。原生和桌面包保持原样；发布保护记录在同一 maintenance 目录。
