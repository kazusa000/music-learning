# 虚拟乐器库

本阶段仅从主页进入，作为独立试听与个人预设制作空间。按乐器家族筛选，再选择具体型号与预设；不绑定工程音轨。每个型号共用一张展示插图，插件来源保留英文产品名。未来三维资源只预留数据字段，本阶段不做三维交互。

## 页面与声音

型号页分为「乐器」与「插件」，底部共用试弹键盘、力度、八度、延音和止音。支持计算机键盘 A–L 一带的半音键位。声音由独立原生 VST3 实例直接进入设备，不生成试听音频供网页下载解码。HTTP 与文件命令只用于控制，音频回调不等待它们。

插件优先嵌入桌面原生子窗口：WebView 报告内容区域，桌面外壳提供专用 HWND，宿主核对父窗口 PID 并挂载插件 IPlugView。使用真实桌面 DPI；窗口尺寸变化重新布局，切换图片页隐藏子窗口。浏览器调试模式提供独立插件窗口入口。原生 GUI 能力以每个插件实际支持为准，不能将无自动化标记的占位参数当成可调旋钮。

## 状态与保存

- 草稿自动保留在 SQLite `instrument_drafts`，包含完整组件／控制器状态与可写参数值。
- 明确点击保存才进入 `instrument_personal_presets`；另存为创建新记录，更新已有个人预设使用版本前置条件及幂等回执。
- 出厂预设和 H 盘音源资源只读。个人预设以原厂基础 ID 关联来源，重新打开先验证来源，再恢复个人完整状态及参数；不以原厂参数覆盖用户设置。
- 这里的状态是独立乐器库状态，不创建工程、修订、素材或导出任务。用户明确要求后才接工程音轨。
- 正常离开、刷新、服务退出均释放音符并保留状态；失去控制端的原生实例有租约保护。切换中的旧加载任务不得迟到后重新启动宿主。

## 接口与目录

`instruments.gallery/get`、`instruments.personal.list` 和 `instruments.studio.*` 共用 Service、API、SDK、CLI。完整契约在生成的 `packages/contracts`；长耗时原生操作不持有 SQLite 写事务。

- `resources/instruments/gallery.json`：首批型号、独立说明、默认预设与展示资源。
- `resources/instruments/images.json`：型号图及 SHA；同一型号的所有预设共用图片。
- `native/instrument-studio`：专用实时试听宿主与原生编辑器。
- `src/music_learning/instrument_studio.py`：生命周期、草稿、个人预设和统一操作。
- 新构建分别在 `runtime/build/instrument-studio`、`instrument-desktop` 和 `instrument-library-web`；不覆盖固定发布。

## 首批十个型号

| 来源 | 型号 |
| --- | --- |
| Keyscape | LA Custom C7、Rhodes Classic Mark I、Wurlitzer 200A、Hohner Clavinet C |
| Pianoteq 9 | Classical Guitar、Celesta |
| Kontakt Factory Library 2 | Flute、Violins、Band Electric Bass、Analog Machines |

型号及默认预设全部用已入库的稳定身份匹配，不以展示名称猜测。Analog Machines 是合成器音色组，展示插图不声称为某一台实际采样硬件。

隔离工作树 `data/worktrees/virtual-instrument-library`，分支 `codex/virtual-instrument-library`。实际覆盖与未完成项见[验证记录](../benchmarks/instrument-library.md)。
