# 本地引擎发布与恢复

此流程只更新程序版本指针和本机配置，不迁移、复制或降级工程数据库，不移动音源和模型。正式发布前先完成 N7 对该原生 bundle 的整曲及长循环验收。

## 固定发布资源

- `runtime/releases/<Git commit>`：从已提交 Git 内容提取的独立源码；清单逐文件校验，开发中的未提交修改不会进入正式服务。
- `runtime/web-releases/<内容SHA>`：与源码配套的前端静态资源。
- `runtime/engine-bundles/<内容SHA>`：固定的设备引擎、插件 worker、导出 runner 和回环工具。
- `runtime/desktop-releases/<内容SHA>`：已测WebView2外壳及四个配套文件；发布启动逐文件核对，不再使用可变旧bin。
- `runtime/engine-diagnostics/<内容SHA>`：仅验收使用的进程隔离捕获工具，独立于生产四文件。

发布描述保存在 `runtime/engine-release.*.local.json`。它只是候选；`runtime/application-release.local.json` 才是正式指针。源码目录中的 `runtime`、`plugins` 是明确的资源联接，禁止递归跟随它们扫描或删除发布树。

## 发布顺序

在项目根目录PowerShell执行。以下描述已在2026-10-22通过v8两首整曲及1800秒循环并激活，正式指针固定源码d4852c6、f056原生bundle及aa81f952桌面包。后续新版本仍必须先完整验收，再使用对应描述发布。

```powershell
& data/envs/app/Scripts/python.exe scripts/deploy_app_release.py runtime/engine-release.desktop-final.local.json
.\stop.cmd
& data/envs/app/Scripts/python.exe scripts/deploy_app_release.py runtime/engine-release.desktop-final.local.json --apply
.\start.cmd
```

第一条只检查源码、前端、原生文件与数据位置。激活要求本应用所有者进程已退出、端口空闲，并持有统一启动锁；不会终止占用同一端口的未知程序。成功后仍用原有 `start.cmd`，启动器先验证发布清单，再进入固定源码。

本次首次激活将本项目端口从8765改为18765，因为Windows保留范围包含旧端口。持同一启动锁、先写同描述的publication barrier、原子更新配置，再由`--apply`完成发布并移除barrier，记录在`n7/port-transition.json`。未改系统端口保留、驱动或防火墙；日常启动及默认SDK/CLI自动跟随项目配置。

若发布途中中断，`application-release.pending.local.json` 会阻止普通启动。重新执行**同一份**已验证描述的 `--apply` 完成发布；不要手动删除中断标记或绕过选择器启动开发源码。

## H盘资源约束

音源库保持用户指定H盘，不擅自搬盘。维护验证先用已有loading_budget连续观察空闲窗口，忙时延期；只读性能计数，不扫描全库。重型冷加载和验证串行，尽量复用已装载worker，记录超时诊断，不能把I/O竞争视为音色损坏。这个维护预算不阻塞产品播放回调，也不放宽音频欠载/迟到与预设身份检查。

## 日常音频恢复

音频设备或插件进程异常时，先运行 `repair-audio.cmd`。它通过既有 SDK 关闭并重新连接自有音频进程，保留工程、修订、视图、位置、循环和监听值，恢复后保持暂停。不重新转录，不导出整曲，不修改音乐版本。

`status.cmd` 查询本应用状态，`stop.cmd` 停止自有服务和窗口。不会控制独立音源验证任务。

## 回到先前发布

已有先前验收通过的原生发布时，先停止应用，再用该发布**原有描述文件**执行上述检查和 `--apply`，随后正常启动。`runtime/application-release-state.local.json` 记录上一发布身份；原描述、源码和 bundle 必须保留且通过 SHA 检查。回退程序版本不回退数据库，也不丢弃用户的新修订。

首次原生发布没有上一份正式原生版本。此前 WebAudio 版本属于不同执行路径，不能把旧前端拼到新原生配置上，也不能删正式指针让应用落回未提交的开发树；若需要恢复旧执行路径，必须另做完整配套发布和独立验证。设备重连先使用上述恢复入口。

当前验收结果、设备限制及实际部署状态以 [N7 记录](../benchmarks/native-engine-n7.md)最新段落为准。
