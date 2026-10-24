# 虚拟乐器库

本阶段仅从主页进入，作为独立试听与个人预设制作空间。按乐器家族筛选，再选择具体型号与预设；不绑定工程音轨。同一型号下的预设共用一张展示图；普通乐器可复用同类图片，具体特色型号保留独立视觉形象，以外观、材质、结构和年代气质体现差异。资源规则与 `media.image_scope / image_identity` 见 `resources/instruments/README.md`。插件来源保留英文产品名。未来三维资源只预留数据字段，本阶段不做三维交互。

卡片、型号页与预设选择共用现有音源目录的中文翻译：型号主标题使用 `translated_names`，原厂变体使用 `display_variant_name`，保留已审订译名；英文原名作辅助信息。配图清单只负责视觉编排，不另外维护运行时译名。个人预设保留用户命名，厂商插件自身的原生界面语言由插件决定。

## 页面与声音

型号页分为「乐器」与「插件」，共用试弹键盘、力度、八度、延音和止音。图片页显示键盘；插件页让原生界面使用整块区域，预设与保存放在顶栏，键盘可随时展开。支持计算机键盘 A–L 一带的半音键位。声音由独立原生 VST3 实例直接进入设备，不生成试听音频供网页下载解码。HTTP 与文件命令只用于控制，音频回调不等待它们。

插件优先嵌入桌面原生子窗口：WebView 报告内容区域，桌面外壳提供专用 HWND，宿主核对父窗口 PID 并挂载插件 IPlugView。使用真实桌面 DPI；窗口尺寸变化重新布局，切换图片页隐藏子窗口。浏览器调试模式提供独立插件窗口入口。原生 GUI 能力以每个插件实际支持为准，不能将无自动化标记的占位参数当成可调旋钮。

插件槽使用物理像素。原生包装窗口选择可容纳的整数平台 DPI 档，先处理 JUCE 的异步通知，再按空间请求插件自己的内容缩放；有效栅格倍率不低于 100%，避免旧界面访问不存在的 0 档素材。桌面与 WebView 保留真实每屏 DPI。顶栏默认「适应窗口」，另可选原始大小及插件支持的倍率，选择按型号保存在本机界面设置。

忽略 VST3 内容缩放的旧界面，使用 Windows mixed-DPI 子窗口兼容缩放，不以截图替代插件，也不模拟网页旋钮。专用 Panel 启用 mixed hosting，只有该插件编辑器在 scoped unaware 上下文创建；其它窗口继续 PMv2。旧界面只显示实际支持的「适应窗口／100%」，空间不足时使用完整的原始大小，不裁掉控制区域。这类 Windows 位图兼容放大的清晰度受原始界面素材限制，不能等同于插件提供高分辨率素材。插件页试弹键盘为紧凑横条，展示页保持大键盘。

依据：[Microsoft mixed-DPI hosting](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-setthreaddpihostingbehavior)、[VST3 IPlugViewContentScaleSupport](https://steinbergmedia.github.io/vst3_dev_portal/pages/Technical%2BDocumentation/Change%2BHistory/3.6.6/IPlugViewContentScaleSupport.html)。

日常试听不重新遍历整套入库清单、样本文件和运行依赖。目录按原子发布文件的元数据缓存，变化后重新读取；每次选择仍核对插件身份和选中预设资源。二进制摘要由 Python 以文件元数据失效的缓存验证，私有启动收据记录准确路径、摘要、长度和修改时间，原生进程先核对未变化，避免同一次打开又读一遍整个插件。入库、离线验证和正式工程流程的完整检查没有改动。

同一客户端在同一插件身份内切换预设，复用现有实例、音频设备和编辑器。先保留旧草稿、释放音符，再在音频状态屏障内应用新完整状态或 VST3 预设与控制器；失败恢复旧状态并如实报告。屏障期间音频回调输出零，不等待文件或数据库。不同插件、客户端或失效宿主仍走独立冷启动。换预设期间允许止音、抬键和隐藏旧编辑器，阻止新的起音。

交互宿主不再使用离线渲染器的固定 10 秒 Keyscape 等待。处理器准备好即可打开 GUI、接受试弹，厂商的样本线程继续流式读取；这不是“全部样本已读取”的承诺。实际发声验证在隔离测试中等待真实音频并记录时间与进程读取量，不向日常试听插入测试音符。`timings_ms` 与 `native_timings_ms` 分别记录目录、选择验证、状态准备、设备、插件创建、界面和原位切换耗时，避免把软件等待误归因于 H 盘。

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
- 新构建分别在 `runtime/build/instrument-studio`、`instrument-desktop-v2` 和 `instrument-library-web`；不覆盖固定发布。桌面外壳使用新构建目录，避免覆盖用户仍打开的旧预览可执行文件。

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
