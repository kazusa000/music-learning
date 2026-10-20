# 型号、变体与确认应用

后续中文显示与型号/变体试听见 [中文音色与试听](45-chinese-instrument-previews.md)；本页的分层与确认应用规则继续适用。

2026-10-20。音色目录统一为 **乐器类别 → 型号 → 变体 → 确认应用**。本约定替代文档 42 中“点击音色立即切换”的交互，现有加载、重试和音轨替换规则继续适用。

## 使用

音色与效果页先选目标音轨，再选乐器类别。列表呈现型号及库来源；点型号进入变体列表，点击变体只选择，底部显示目标音轨，点击“确认应用”才调用原来的捕获、应用和常驻加载链路。返回型号列表或换类别会取消待应用选择；切换目标音轨或乐器插槽会重新建立选择器，避免把旧选择应用到新轨。已有型号/变体标记“当前”，当前预设不能重复应用。

型号与变体分别分页、搜索；在型号页搜索变体名称也能找到所属型号，数量仍表示打开后该类别下可见的全部变体。变体列表支持方向键、Home/End，确认按钮始终位于滚动列表之外。目录或列表读取失败原地重试；应用之后的音频加载失败仍沿用原“重试加载”，不重复编辑。

## 数据与后续导入

完整预设仍是实际可加载的最小单元，唯一 `preset_id`、原始名称、完整状态、资源、验证证据和工程引用保持原身份。型号是其上方的目录实体，不成为新的插件状态或音乐修订。

所有导入器共用 `instrument_taxonomy.classify` 与 `instrument_models.with_model`。新增库应在每个预设中明确提供：

```json
{
  "library_id": "maker-library-version",
  "model_spec": {
    "key": "maker-model-stable-key",
    "name": "型号显示名",
    "kind": "instrument"
  },
  "variant_name": "Natural"
}
```

`kind` 为 `instrument`、`layer`、`synth` 或 `unspecified`。同型号各变体复用同一 `key`；这个键不随变体名称和分页改变。服务根据库 ID 与键生成稳定 `model_id`，跨库同名型号保留各自来源。相同键的型号名/类型/来源冲突、非法模型信息及空变体名均拒绝，不静默合并。`variant_name` 未提供时保留原厂完整预设名。

Pianoteq 使用原厂 `expected_pianoteq.instrument`；Keyscape 新导入使用原厂 `Model` 属性，并在索引时写出型号信息。旧 Keyscape 目录只按原厂 Model 注册表读取历史 tags，Duo 与 Hohner Clavinet-Pianet DUO 标为叠层组合。Surge 的预设归在对应库的 Surge XT 合成器型号下。其余缺少型号资料的旧预设暂时各自成组，不根据任意文件名猜同一个乐器。资料补全后导入器仍使用相同统一模型结构，无需前端特例。

本次兼容视图不重写 H 库目录、预设或验证缓存；新导入器写入规范字段。库存与已入库仍分开，未通过入库的 763 个 Pianoteq 库存变体没有因此变成可选项。

## API、SDK 与 CLI

- `instruments.models`：类别、搜索、型号、family、验证状态、分页及可选 `current_preset_id`；返回型号列表、完整 `model_spec`、来源、所属类别、变体数及当前型号 ID。
- `instruments.list`：兼容原查询，新增 `model_id`，每条返回 `model` 和 `variant_name`。型号 ID 不存在时明确失败。
- `production.query`：新增 `model_id` 与 `include_instrument_models`；内置 AI 与前端使用同一目录。
- Python SDK：`client.instrument_models(...)` → `client.instruments(model_id=...)` → 原 `capture_instrument`/编辑流程。
- CLI：`call instruments.models --args models.json` → `call instruments.list --args variants.json`，使用同一契约；不增加独立业务逻辑。

新的只读操作可从 `describe_capabilities` 发现；Python 权威契约已生成 JSON Schema/TS。没有数据库迁移或新增音乐制作任务。

## 验证

当前 489 个可选预设归入 53 个库内型号组：Keyscape 455 个/36 组，Pianoteq 34 个/17 组；其中 50 组为乐器、2 组为叠层、1 组为合成器。这里是已入库部分的分组数，不是 Pianoteq 全部 52 个原厂型号的数量。

后端全套 205 项通过；新增覆盖跨库同名隔离、同型号变体分页、搜索数量一致、原厂型号/叠层、未知旧条目、非法/冲突导入资料、未来库持久化、AI 查询及 HTTP SDK 能力发现。独立真实 HTTP/SDK/CLI 展开全部型号，回读 489 个唯一预设，无丢失或重复。

`scripts/probe_instrument_models.py` 启动独立 8776 HTTP 服务，浏览器通过临时 Vite 服务运行真实 SoundPanel/InstrumentBrowser，读取 H 指定目录；应用回调只记录选择，不连接正式制作工程。22 项浏览器检查覆盖当前标记、选中不应用、确认一次、既有插槽、返回/换轨/换类别取消、分页、失败重试、窄宽度和键盘选择，未调用捕获、编辑、AI 或渲染。测试进程归属独立且已退出。小型报告与截图保存在被忽略的 `data/maintenance/instrument-models-20261020`。

这次验证的是目录和选择交互，未重新做原生音源音质听辨；原有音色替换链路的单元回归另已通过。

## 部署与工程边界

前端构建及手动替换、启动、加载去重、即时混音四组前端单元检查通过，Ruff、契约漂移与 Git 空白检查通过。正式服务已保留桌面窗口重启，HTTP SDK 回读 71 项可发现操作、53 型号组、489 变体。打开中的旧页面刷新后使用新版界面。

本次只读基线中的正式工程 head 为 `d72d45a522044d7fb7e0036f29448976`，1 工程/27 修订/21 任务；全部 projects/revisions/jobs 行摘要在本次前后严格相同。该基线已包含其他授权工作相对此前文档所做的变动，没有把旧 head 当作本次基线。未写正式工程、访问 NAS、改 H 音源/样本/缓存或重建原生宿主，没有启动音色全量验证。
