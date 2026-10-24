# 虚拟乐器库

本阶段仅从主页进入，作为独立试听与个人预设制作空间。按乐器家族筛选，再选择具体型号与预设；不绑定工程音轨。每个型号共用一张展示插图，插件来源保留英文产品名。未来三维资源只预留数据字段，本阶段不做三维交互。

## 页面与声音

型号页分为「乐器」与「插件」，共用试弹键盘、力度、八度、延音和止音。图片页显示键盘；插件页让原生界面使用整块区域，预设与保存放在顶栏，键盘可随时展开。支持计算机键盘 A–L 一带的半音键位。声音由独立原生 VST3 实例直接进入设备，不生成试听音频供网页下载解码。HTTP 与文件命令只用于控制，音频回调不等待它们。

插件优先嵌入桌面原生子窗口：WebView 报告内容区域，桌面外壳提供专用 HWND，宿主核对父窗口 PID 并挂载插件 IPlugView。使用真实桌面 DPI；窗口尺寸变化重新布局，切换图片页隐藏子窗口。浏览器调试模式提供独立插件窗口入口。原生 GUI 能力以每个插件实际支持为准，不能将无自动化标记的占位参数当成可调旋钮。

先等待 JUCE 的异步 DPI 通知，再遵守插件尺寸约束；必要时选用插件原生 1x/2x 显示档，不请求低于 100% 的任意栅格倍率。Keyscape 的异步采样加载沿用既有宿主的版本限定就绪等待，等待期间保持加载状态，个人预设恢复走同一规则。

## 状态与保存

- 草稿自动保留在 SQLite `instrument_drafts`，包含完整组件／控制器状态与可写参数值。
- 明确点击保存才进入 `instrument_personal_presets`；另存为创建新记录，更新已有个人预设使用版本前置条件及幂等回执。
- 出厂预设和 H 盘音源资源只读。个人预设以原厂基础 ID 关联来源，重新打开先验证来源，再恢复个人完整状态及参数；不以原厂参数覆盖用户设置。
- 这里的状态是独立乐器库状态，不创建工程、修订、素材或导出任务。用户明确要求后才接工程音轨。
- 正常离开、刷新、服务退出均释放音符并保留状态；失去控制端的原生实例有租约保护。切换中的旧加载任务不得迟到后重新启动宿主。

## 接口与目录

`instruments.gallery`、`instruments.gallery.get`、`instruments.personal.list` 和 `instruments.studio.*` 共用 Service、API、SDK、CLI。完整契约在生成的 `packages/contracts`；长耗时原生操作不持有 SQLite 写事务。`current` 可查询当前试听，`controller` 支持调制、音量、声像、表情四个标准 MIDI 控制器；没有通用自动化参数的插件仍能保存完整状态与这些控制器设置。

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

隔离工作树 `data/worktrees/virtual-instrument-library`，分支 `codex/virtual-instrument-library`。实际覆盖见[验证记录](../benchmarks/instrument-library.md)。

## 独立预览

双击 `scripts/instruments/open-library.cmd`。入口启动本工作树的服务与桌面外壳，端口 18767，主页进入乐器库；关闭窗口会停止试听并退出服务。不会切换正式发布指针。

预览草稿与个人预设持久化在本工作树的 `data/development/instrument-library/projects.sqlite3`；该目录是预览用户数据，不属于测试夹具，后续归档工作树前需保留或迁移。每次试听的临时状态和通信文件在实例正常退出后清除，已保存数据留在 SQLite。

SDK 使用 `Client(url="http://127.0.0.1:18767", token_file=Path("data/development/instrument-library/session-token.local"))`，再调用 `client.call("instruments.gallery")` 等同名操作。CLI 使用 `music.cmd --url http://127.0.0.1:18767 --token-file data/development/instrument-library/session-token.local call instruments.gallery`。令牌只从本机文件读取。
