# 用户音源库接入记录

2026-10-20，用户指定 `H:\music\Sound Library` 为今后的音源库，授权检查 `H:\music\tmp` 中的两套音源。此次只涉及这些新文件，不恢复此前删除的旧库，不访问 NAS。

## 当前结果

| 来源 | 已完成 | 待补或未通过 |
| --- | --- | --- |
| Pianoteq 9 | 9.1.0 原包及 797 预设保留；用户补充的 9.1.2 已独立提取到 H 盘，扫描和原厂 797 预设导出成功，自带程序三种乐器输出不同音频。 | 9.1.2 在当前 VST3 加载链路仍接受不同预设却回读相同状态；未入库。已经有可用于排查的完整文件，先排查适配，不再让用户反复更换安装包。见 [9.1.2 对照](benchmarks/pianoteq912-intake.md)。 |
| Keyscape / Spectrasonics | 用户已提供 Windows x64 VST3；150 文件、77,528,440,122 字节复制到指定 H 盘新库并逐文件校验。455 个原厂预设完成乐器分类，已通过的预设进入可选目录。 | 播放器后台加载采样需要宿主兼容等待；完整接入及各轮实际验证见 [Keyscape 接入](35-keyscape-integration.md)。未测试变体仍为库存。 |

Keyscape 播放器与采样现已接入。原先缺播放器的记录保留在 Git 历史中，不再要求重复提供。官方说明：[Keyscape](https://www.spectrasonics.net/products/keyscape/)、[STEAM 文件夹](https://www.spectrasonics.net/support/knowledgebase/article/view/175/21)。没有索取账号、密码或授权码。

被忽略的 `runtime/instrument-library.local.json` 使用新路径。新库的 `library-inventory.json` 保存来源、文件位置和接入状态；首次检查时 `catalog.json` 为 0 个可选预设，现由通过验证的 Keyscape 条目更新。Pianoteq 的 797 个导出文件仍只是库存。插件允许目录仅新增 H 盘 Keyscape 的 `Spectrasonics\VST3` 子目录。

## Pianoteq 验证

本机 `--help` 提供 `--export-vst3-presets` 与 `--export-presets-filter`，据此在副本和独立偏好配置下导出。`builtin` 退出码 0，得到 797 文件；`all` 在附带第三方预设中退出 42，仅有部分输出，不能作为完整成功。功能参考：[Pianoteq 手册](https://www.modartt.com/user_manual?product=pianoteq)。未修改源 FXP 或样本。

早期独立发声与恢复探测均输出非零音频，继续比较不同乐器才发现：吉他、MKI 电钢、钟琴等返回同一个状态 SHA `07a94bf86e675052483c1a0a061e91a62708dae020ba52eb46c11ee9bf97c72d`，RMS 均约 0.01141638。发声与重开不足以证明预设生效，因此停止首轮批量探测，没有发布其“PASS”条目。第二轮加入跨乐器状态检查，在吉他与钢琴之间明确拒绝；导入器没有改写目录。

等待控制器消息、使用标准组件状态加载仍未改变结果，这些尝试未保留为原生代码修改。标准 VST3 预设在独立 MLFixture 中能恢复指定参数并产生预期音频，新增回归覆盖这一行为；它证明通用格式可用，不代表 Pianoteq 通过。当前仅确认这个提供包在本宿主下未通过切换，其他安装版本未测。

分类使用原厂 Factory 分组及明确模型名，不猜第三方 FXP 文件名。同一 Hohner 组拆出 Clavinet 和 Pianet；Celeste 组拆出钢片琴、钟琴、拇指琴、玩具钢琴。库存为：声学钢琴 394、电钢琴 168、古典吉他 19、竖琴 28、羽管键琴 28、Clavinet 13、钢片琴 7、钟琴 8、拇指琴 3、玩具钢琴 6、钢鼓/手碟 33、合成类 56、颤音琴 18、马林巴 8、木琴 8。分类不等于发声验证。

## 接入规则与复测

`instruments.preset_spec` 统一分发 `sfz`、`surge-fxp`、`vst3-preset`，未知后端明确失败。捕获、预处理组装、阶段资源重定位及旧库构建工具共用规则，不再把所有非 SFZ 条目交给 Surge。标准预设由原生宿主加载，完整状态/参数/资源继续走既有 Service/API/SDK/CLI，无新操作或迁移。

可选 `runtime_dependencies` 核对小型加载器之外的引擎文件摘要；复制资源或捕获前检查，不能只靠加载器摘要宣称引擎一致。它不自动安装插件，恢复工程仍需要匹配安装。

`scripts/index_pianoteq.py` 是离线入库工具，必须 `--verify`，使用新的证据目录，先验证再原子发布，保留其他库条目。仅发布实际完成探测的预设；未测导出文件只算库存。不同乐器回读相同状态时整次入库失败，既有目录不变。补齐播放器后，先重新导出匹配的预设，再运行：

```powershell
& data/envs/app/Scripts/python.exe scripts/index_pianoteq.py `
  --library 'H:\music\Sound Library' `
  --bank 'H:\music\Sound Library\Pianoteq 9' `
  --exports 'H:\music\Sound Library\Pianoteq 9\VST3 Builtin Presets' `
  --evidence data/benchmarks/user-instruments-v2 --verify
```

清单固定 Pianoteq 9.1.0；其他版本需要更新清单并实测。工具不改正式工程，也不代替全曲试听。

## 验证及数据边界

- 114 项后端回归通过；22 项既有原生回归通过，新增标准 VST3 预设状态/音频测试另行通过，共 23 项。首次把原生测试放进 app 环境时因缺 numpy 收集失败，随后在 transcription 环境完成，未改变隔离依赖。
- 4 项新增服务测试覆盖格式分发、未知格式、引擎变化拒绝、同库乐器拆分与“发声但未切换”拒绝。Ruff 通过。没有前端改动，没有重新进行浏览器听辨、模型推理或正式歌曲渲染。
- 正式工程 `4012a10f50574b2395d4ec4a9d8f27b5` 的 head `6d12b49aa72840429d79ce93294e1dc6`、快照 SHA `4591d13714931fce665e7f156a66a551487078233d2645c80b0167d19c8ceb5c`、9,378 音符和 15 任务保持。
- 证据位于被忽略的 `data/benchmarks/user-instruments-v1`：复制清单、源文件复核、原生请求/音频、两轮入库诊断、独立夹具对照、正式工程前后回读。音源文件、偏好、数据库和日志不进入 Git。

上述 Pianoteq 测试仍未通过，保持不入库；Keyscape 后续接入的实际结果见 [专项记录](35-keyscape-integration.md)。没有恢复旧删除库。
