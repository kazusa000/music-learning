# Python SDK / CLI

SDK 实现在 `src/music_learning/sdk.py`，CLI 由根目录 `music.cmd` 进入。两者调用同一个 HTTP 工程服务，不直接写工程数据库。

`Client.call`、有序 `batch`、素材上传、导入、任务等待和事件查询使用服务发布的能力与参数契约。能力以实际 `describe_capabilities` 为准，不在本文固定操作总数。

`batch` 遇到首个错误停止，之前成功的命令仍保留；它不是跨命令事务。需要安全重试时保留完整请求和稳定幂等键；单次编辑事务由服务原子提交并推进修订。

导入完成与预处理完成分别回读。工程修订、运行中的播放图和导出文件也分别确认；不要把提交成功当成声音已加载。客户端收到修订冲突后重新读取状态。

示例和入口见[统一接口](../../docs/development/unified-api.md)，工程音频规则见[本地架构](../../docs/architecture/native-daw.md)。
