# 桌面窗口、固定工作区与鼠标

## 2026-10-21：空轨道网格填充

用户选择先用空轨道格填满编排下方。DawEditor在实际轨道之后增加仅用于显示的弹性网格，横向轨道分隔、时间线与播放头延伸到视口底部；窗口变高自动补齐，真实轨道超过视口时填充高度归零，保持单一滚动区与固定标尺。空行没有轨道实体、编辑按钮或插件，不改变工程轨道数量，也不产生任务或声音。

TypeScript/Vite构建通过。独立真实组件在Edge headless、DPR2.5下检查0/7/24轨：864px高视口中7轨剩余332px全部补齐，0轨补齐801px，24轨填充0且可滚动，滚动标尺固定；1536×864及1100×740均贴齐底部，空格区右键没有触发编辑，页面错误0。仅用内存合成快照、未连接正式API或音乐工程。截图及小型诊断位于忽略的data/maintenance/empty-lanes-20261021，测试进程已退出。保留其他并发音色/试听修改，仅提交本次编辑器、样式及本说明。

2026-10-20。按用户要求合并顶栏、固定音乐标尺、移除原生滚动条并接入常用 DAW 鼠标操作。本次只改视图和桌面容器；音乐写入仍通过原有 edits.apply / revisions.restore，共享 API/SDK/CLI、幂等和版本检查不变。

## 桌面与 4K

默认入口现在使用本地 WinForms + WebView2 无边框窗口。工作台不再显示品牌栏或浏览器标题栏；最小化、最大化/还原和关闭位于应用播放栏右侧。主页保留自己的窗口按钮，加载失败时仍能看到顶栏。可拖动顶栏空白处移动窗口，边缘调整大小；输入、按钮、菜单和滑杆不启动窗口拖动。显式 `scripts/app.py open --browser` 仍用于浏览器调试。

窗口只加载同一 127.0.0.1 服务来源，阻止外站导航和新窗口。窗口桥只接收本来源的四个窗口动作，音频仍由已有原生宿主负责。启动器继续使用 OwnedProcess 管理所属进程，不写全局注册表、系统兼容性设置或其他盘。

用户报告 4K 模糊后，实际诊断发现：旧壳在本机250%缩放下仍为 DPI 96 / devicePixelRatio 1，整个画面被 Windows 拉伸。仅有 App.config 未充分启用高 DPI。现补齐 Windows 10 兼容清单、.NET Framework 4.8 目标标记和 PerMonitorV2 配置，采用 DPI 自动缩放；边框、最小尺寸和初始窗口随实际屏幕缩放，初始大小限制在工作区内。WebView2 保持 ZoomFactor 1，按屏幕比例栅格化，不使用 CSS zoom 或强制1倍像素。

总览画布按 DPR 创建真实像素缓冲；挂载/宽度/显示比例变化重新绘制，原有一次性 ref 观察改为跟随实际挂载节点。切换显示比例时监听 resolution/resize，原生 DPI 事件补发下一帧 resize 通知。卷帘和编排继续使用 SVG。

| 本机实际窗口诊断 | 修复前 | 修复后 |
| --- | ---: | ---: |
| 线程/窗口 PerMonitorV2 | false / false | true / true |
| 窗口 DPI | 96 | 240 |
| WebView DPR | 1 | 2.5 |
| WebView ZoomFactor | 1 | 1 |
| 诊断截图像素 | 1510×970 | 3510×1965 |
| 修复后 CSS 视口 | — | 1404×786 |

250%下截图与控件物理尺寸最多相差2像素，是 WebView 将分数 CSS 像素取整的结果。实际单屏高 DPI 窗口已验证；100/150/200/250%的画布切换使用浏览器 DPR 仿真及与原生相同的 resize 通知，未表述为真人跨屏拖动验收。

构建：

```powershell
& data/envs/app/Scripts/python.exe scripts/build_desktop.py
& data/envs/app/Scripts/python.exe scripts/verify_desktop_dpi.py
```

构建使用本机 .NET Framework 编译器，下载并校验 WebView2 SDK 1.0.4258.31；SDK、DLL和 EXE 仅在忽略的 runtime/desktop。实际 WebView2 Runtime 为系统已安装组件。DPI 诊断仅启动透明、不激活的独立窗口和临时本机静态页面，不打开音乐工程；输出报告和截图后退出。最终实测 foreground_unchanged=true。诊断的最小化/最大化试验曾改变焦点，已从诊断入口移除，失败记录保留；最终入口不再进行窗口状态切换。

已运行的旧窗口需要关闭后重新双击 start.cmd，刷新网页不能更新 EXE 的 DPI 模式。

## 工作区与鼠标

- 顶部一行集中歌曲、播放/时间、编曲/混音、原曲/重建、循环/起止、当前和弦、时间缩放和窗口按钮。移除原本底部循环/和弦区及单独的模式说明行。
- 制作区填满剩余高度，不再由整页和编辑器双层纵向滚动。编排和卷帘各自保留一个滚动视口，时间/小节标尺固定在该视口顶部，轨名固定在左侧。
- 新增轨道在音轨标题及编排标尺角落的加号；删除/上下移在轨道菜单和轨道右键菜单。原先单独占行的轨道按钮已移除，现有撤销/重做保留。
- 隐藏应用原生滚动条与箭头；普通滚轮、键盘、时间平移条、音高定位和中键拖动仍可导航。左右分隔条与卷帘/力度分隔条保留，音高定位按实际可用高度计算。
- 卷帘右键删除音符，按住右键扫过音符，在松手时提交一次可撤销编辑。编排右键删除片段，同时删除该片段所属音符；不将未编组缩略图伪装为片段。
- 中键拖动上下移动内容、左右平移时间，不改音乐；中键加右键打开编辑工具菜单。Ctrl+右键适配整曲，卷帘同时适配音域。Escape取消未提交的擦除/拖动，版本变化也不会提交旧手势。
- 轨道菜单支持键盘方向键与Escape。文本输入区域仍可使用自己的右键/选择行为；音乐表面不再弹出网页上下文菜单或中键自动滚动标志。

## 验证与数据

TypeScript/Vite构建、8组相关前端单元（编辑、手动音色、预加载、播放启动、实时混音、电平独奏、试听冲突和擦除几何）及3个Python脚本Ruff通过。没有后端、迁移或音频宿主修改，未重复执行模型与插件全库验证。

`apps/web/studio-mouse-e2e.mjs` 仅允许 data/t 下非正式端口的独立夹具。实际 Edge headless、DPR2.5、24轨、真实sfizz两轨，通过26项：固定标尺/无整页滚动/无人工水平溢出、双轴中键、右键删除/扫除/取消/撤销、工具菜单、轨道增删排序、片段及所属音符删除、播放中音量/静音/独奏不改head、画布比例切换、1100×740布局、原生按钮桥动作、无弹窗/页面错误/额外整曲渲染/模型/手动版本保存。按钮桥测试与实际桌面像素诊断分别记录，未把浏览器仿真当成人工原生窗口拖动验收。

证据位于忽略的 `data/maintenance/ui-shell-mouse-20261020`：browser-v8/report.json、截图、dpi-before.json、dpi-final-v3.json、formal-comparison.json。v1缺浏览器、v2组合键、v3片段命中、v4定位器和v6无DPR通知的失败保留；修正后的v7通过，暂存源码独立构建后的v8再次通过。此前 Computer Use 由用户Escape停止，后续没有再次接管鼠标键盘，改用独立headless和原生自诊断。窗口状态诊断的焦点问题也保留原报告，不将其改标为通过。

正式工程只读核对：head `d72d45a522044d7fb7e0036f29448976`、7轨/9378音符/21任务前后不变；SQLite原始snapshot文本SHA `45e604298cf7104caf7ffcda01d7f4a10f153d2679b921c2ce24b44898c61a11` 一致。首次对比误将原始文本SHA与数据库规范化摘要 `4bc70860…` 混用；保留该输出，formal-comparison.json改用基线相同算法核对通过，未重写基线。

未调用正式渲染、音乐模型或对话推理，未修改H音源、NAS、Keyscape报告/固定宿主、正式常驻链接库。独立夹具仅含合成音，三个原setup任务（导入/捕获/渲染）不增加；26项交互验证未排队新渲染。

参考：[微软.NET Framework高DPI配置](https://learn.microsoft.com/en-us/dotnet/desktop/winforms/high-dpi-support-in-windows-forms?view=netframeworkdesktop-4.8)、[WebView2屏幕比例](https://learn.microsoft.com/en-us/dotnet/api/microsoft.web.webview2.core.corewebview2controller.shoulddetectmonitorscalechanges)、[FL Studio卷帘鼠标](https://www.image-line.com/fl-studio-learning/fl-studio-online-manual/html/pianoroll.htm)、[FL Studio快捷操作](https://www.image-line.com/fl-studio-learning/fl-studio-online-manual/html/basics_shortcuts.htm)。

收尾对暂存源码使用checkout-index建立E盘独立构建，避免并发乐器目录/契约未提交修改进入产物；该构建已发布到前端dist，v8复核26项通过。正式服务与用户窗口未重启，独立8777 API/worker已停止。清理本次新建的 `runtime/desktop-ui-verify-20261020`、`data/t/ui-shell-mouse-20261020` 和证据下 `profile` 的命令被自动审批以 blocked by policy 拒绝，命令未执行，实际释放0；构建目录内node_modules联接及三个目录仍在，禁止改工具绕过。此前拒绝路径均未触碰。
