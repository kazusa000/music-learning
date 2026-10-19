> P4 更新：正式工程讨论已实现，当前方案和登录模式见 [工程讨论](18-music-dialogue.md)，真实验收见 [P4 报告](benchmarks/p4-dialogue.md)。以下 P0 记录保持历史含义。

# Codex 接入方案与当前探测

已用真实 ChatGPT 登录验证 Codex App Server，可作为本应用的默认 Agent 接入方向，供应商适配边界保留。正式前端、工程工具和对话持久化在 P1～P5 接入；当前为可运行的 P0 stdio 客户端和探测工具。

官方依据：[Codex App Server](https://learn.chatgpt.com/docs/app-server)、[Codex 作为应用 Agent 平台](https://developers.openai.com/blog/codex-as-a-platform)。App Server 提供会话/turn、流式事件、自定义工具往返和中断；应用负责工程事实、范围、权限、版本事务与实际声音结果。不会把模型的一句“已修改”当作工程写入成功。

## 已验证的结果

2026-10-18，本机固定 `codex-cli 0.163.0-alpha.10.2`，使用既有 managed ChatGPT 登录，实际模型 `gpt-6.1-sol`：

- 初始化、账户读取、模型目录、临时 thread/turn 成功。模型目录仅用于选择候选，实际完成推理才证明该请求有权限。
- 自定义 `music_probe` 返回随机校验码，真实模型调用工具后复述该码，流式收到 45 个文本 delta，turn completed。
- 工具返回 `success=false / probe_expected_failure`，模型明确回报原始错误码，收到 14 个 delta。
- 工具等待时执行 `turn/interrupt`，turn interrupted；未知 RPC 返回 -32600，按错误处理。
- 记录中的工具执行为 dynamicToolCall，没有 shell、文件修改或 MCP 调用；没有上传歌曲音频，提供的是合成工程数据。
- 自建 server 和 code-mode 子进程完成后退出。

这不是完整产品聊天验收：当前 thread 为 ephemeral，没有测试应用会话保存/重启恢复，也没有实现完整音乐领域工具。现有客户端只发送文本/工具数据，直接音乐音频理解未验证，不能声称 Codex 已听过原曲。

## 本机运行

```powershell
# 从当前匹配版本的官方桌面安装复制完整运行组件到项目独立目录。
& runtime/tooling/Scripts/python.exe scripts/bootstrap_codex.py
# 不消费推理的账户/模型目录检查；默认使用项目独立登录目录。
& runtime/tooling/Scripts/python.exe scripts/p0_codex_probe.py --status-only --output D:\music-learning-runtime\benchmarks\my-codex-status
# 真实推理/工具/流式/中断探测，使用现有 Codex 托管登录，不提取或复制 Token。
& runtime/tooling/Scripts/python.exe scripts/p0_codex_probe.py --use-existing-login --output D:\music-learning-runtime\benchmarks\my-codex-probe
```

每次输出须选择新目录。真实探测会使用所选登录账户的模型额度，不是模拟服务，也不承诺免费或无限使用。

`config/codex-runtime.json` 固定主程序及三个 sidecar 的 SHA-256，bootstrap 先验证整个来源，再复制；版本不匹配明确失败，不混用组件。当前源来自官方桌面本机安装，不自动追踪更新，跨机器恢复时需要匹配安装或保留的受控运行目录。当前是 alpha 运行版，发布前另行选择并回归稳定发行。

早期探测发现两项实际兼容问题并已修正：CLI 的 config 路径不接受我们最初使用的带引号 MCP 名称；当前版本的动态工具路由需要 `codex-code-mode-host.exe`。最终固定复制四个同版本文件，保留 code-mode host，关闭 shell、apps、多 Agent、web search、analytics 与继承 MCP 服务。未支持的宿主请求明确拒绝，不自动批准其他动作。

## 正式应用的登录边界

默认客户端使用 `D:\music-learning-runtime\codex-home` 独立登录目录；已实测该目录当前是 needs_login，未默默继承桌面登录。可在交互 PowerShell 中登录：

```powershell
& runtime/tooling/Scripts/python.exe scripts/codex_login.py
& runtime/tooling/Scripts/python.exe scripts/codex_login.py --status
```

凭据继续交给 Codex 官方 managed login 管理，不解析、复制或在网页中保存 Token。产品阶段可将 `account/login/start` 的授权入口接入界面；当前 CLI 是探测入口。`--use-existing-login` 是本轮显式使用既有登录的开发探测模式；相应 thread 不持久化，工作目录、日志/SQLite 路径仍独立。不会改写用户全局配置文件。

子进程移除继承的 OpenAI/Azure 自定义 API 环境变量与 Hugging Face Token 环境变量，固定 first-party provider。日志只记录协议方法、状态与 item 类型；账户原始记录/凭据不落盘，模型文本与错误详情留在 Git 外本地报告目录。

## 后续实现顺序

1. P1 先完成权威工程 revision、查询/命令服务、持久任务和 SDK/CLI。
2. P3 将原生宿主、插件、MIDI/轨道/混音与渲染映射到统一领域操作。
3. P4 将相同操作以 App Server dynamic tools 或受控 MCP 暴露，工程数据库保存讨论和引用，绑定 project/revision/range；按需查询音符，避免整曲事件一次塞入提示词。
4. P5 将自然语言修改展开为带 expected_revision、范围和幂等键的事务，真实回读后渲染；结果前端展示差异并试听。

原曲、分轨和音频度量由工程工具提供。直接音频输入与音乐理解是独立能力，后续按实际模型/接入路径测试；未验证时，AI 依据音符、和弦、节拍、结构候选与测量讨论，用户通过试听评价声音。
