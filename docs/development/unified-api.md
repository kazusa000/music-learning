# 统一 API、SDK 与 CLI

前端、内置助手和外部脚本调用同一个工程服务。操作语义由服务与契约定义，SDK 和 CLI 负责协议接入，不另建编辑规则。

- 契约：`src/music_learning/contracts.py` 与 `packages/contracts`。
- 领域服务：`src/music_learning/service.py` 及各领域模块。
- Python SDK：`src/music_learning/sdk.py`。
- CLI：`music.cmd`；运行 `music.cmd --help` 查看当前命令。
- API：仅绑定本机，端口和数据根由 `runtime/app.local.json` 决定。

修改工程时使用当前修订前置条件，批量编辑保持事务性；需要重试的操作使用幂等键，收到冲突后回读当前状态。先完成实际操作再反馈结果，不把已提交修订、已加载声音和最终导出混为一项。

SQLite 与内容寻址存储是工程权威，播放引擎是其运行投影。音频线程不能等待 AI、HTTP、Python 或数据库操作。工程的全部能力应可以通过程序操作，无需模拟鼠标点击插件窗口。

`production.query` 的音色目录是适合助手读取的选择信息，不返回内部状态恢复参数或资源校验清单；`instrument.capture` 仍使用完整固定库存。查询页可能因实际响应大小少于请求的 limit，调用者必须使用响应的 next_offset 继续翻页。`music.query` 事件页遵循同样规则，引用 ID 与实际返回项一致。

原生播放对未选择乐器的 MIDI 轨返回 `plugins[track_id].status=unassigned` 与明确原因，其他音轨继续播放。`applied_revision` 只表示完整就绪；存在这类静音输入时，`playable_revision` 表示其余节点已经应用的修订，不能据此声称所有轨道都有声音。绑定插件后仍通过统一编辑事务及既有音源身份验证，随后更新播放图。

以下示例只查询音源，不写工程：

```python
from pathlib import Path
from music_learning.sdk import Client

client = Client(
    url="http://127.0.0.1:18765",  # 使用本机配置中的实际地址
    token_file=Path("data/app-data/session-token.local"),
)
print(client.instruments(search="Kontakt", limit=20))
```

本机会话令牌只在本机读取，不写入示例、日志或 Git。原生协议见[本地引擎架构](../architecture/native-daw.md)，测试入口见[测试清理](test-cleanup.md)。
