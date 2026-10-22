# 仓库目录职责与整理顺序

2026-10-21。目标是按职责找到代码、运行数据与验收入口，不通过大搬家解决播放架构。当前目录包含源码、70个启动/工具/探针脚本、93个前端文件及大量历次运行数据；下表区分**现在保留的位置**和**随后开发的整理目标**，不是已完成的物理迁移。

## 当前保留与新增位置

| 路径 | 职责与约束 |
| --- | --- |
| `apps/web/src/` | React视图和交互；后续按workspace/library/discussion/instruments/shared及engine client分模块，前端不处理工程音频 |
| `native/desktop/` | WinForms/WebView2外壳与DPI；暂不更换UI框架 |
| `native/engine/` | 本地设备、样本时钟、SessionGraph、混音及文件读取 |
| `native/plugin-worker/` | 原生隔离插件执行；不覆盖旧track host |
| `native/audio-host/`、`native/live-track/` | 已验证离线与旧播放实现；迁移完成前保留兼容，固定文件不搬 |
| `native/include/`、`native/tests/`、`native/test-plugin/` | 可复用适配、小型原生测试、确定性VST夹具 |
| `src/music_learning/` | 唯一Python领域包，保留现有导入名；渐进拆分engine、project、instruments、analysis、dialogue等子包 |
| `services/api/`、`services/worker/` | 薄进程入口、冻结数据库迁移；不再复制领域逻辑 |
| `packages/contracts/` | 生成schema和TS类型；不手改生成物 |
| `packages/agent-sdk/` | SDK入口说明；Python实现仍在统一包，不创建第二份操作规则 |
| `scripts/` | 当前兼容入口；逐步归类app、build、models、instruments、maintenance及probes，保留被引用入口 |
| `tests/service/` | 后端事务/规则回归；新增engine协议和内核测试使用`tests/engine/` |
| `apps/web/tests/` | 新引擎unit/e2e已分类；根部旧入口为兼容包装，其他旧测试渐进迁移 |
| `config/` | 可跟踪依赖清单和无凭据样例；本机路径/令牌仍放忽略位置 |
| `docs/architecture/` | 当前架构决策；不是每轮状态日志 |
| `docs/plans/` | 当前开发阶段、出口与待办 |
| `docs/development/` | 开发入口、目录、构建与测试约定 |
| `docs/benchmarks/` | 可复核的小型验收记录；旧报告不改成新成功 |
| `docs/history/` | 历史状态快照；旧编号文档暂保留原路径，避免全仓断链 |

UI目录拆分先抽EngineClient与Transport接口，再拆Workbench，不把一次大规模格式化或TS改名当作架构交付。Python先新增engine子包，旧Service仍是所有操作权威；旧顶层模块用兼容导出渐进替换，Alembic历史文件不移动或重写。

## E盘运行数据

以下是本机已有布局，不在N0重命名：

```text
data/
  app-data/                 正式SQLite、CAS、任务、live、试听缓存与桌面profile
  envs/                     app与模型隔离环境
  models/                   正在使用的固定权重及旧保留目录
  references/               原曲本机副本，保留来源
  maintenance/              独立验收与维护记录、历史保留/拒绝目录
  t/                        历次独立测试根；不能看到t就认定可删
  ...                       其他旧目录按现有引用保留
runtime/
  app.local.json            本机入口及正式data_root配置
  build/                    旧原生构建与固定链接库
    engine-v1/              新引擎独立输出
  desktop/                  已构建外壳及SDK
  toolchain/ tooling/       工具与编译环境
  third-party/              固定依赖源码
  *.local.json              会话定位/构建指纹/后台批次入口等
```

项目数据/模型/构建只在`E:\Intrest\music-learning`内，音源资源保持用户指定`H:\music\Sound Library`。不选新盘，不搬C盘系统依赖，不探查E盘其他项目。旧音源删除/恢复规则和NAS只读边界不变。

后续新引擎只创建 `data/app-data/engine/` 下小型会话清单、控制状态和自有进程记录；数据库和CAS不复制一份到这里。新验收统一使用 `data/maintenance/native-engine/<run-id>/{report,logs,...}`，自动化测试用显式独立`--basetemp`。默认不复制长音频；确需使用现有素材时只读引用，或先列出独立文件计划并记录是否硬链接。原始报告、受保护批次文件及此前清理拒绝路径均不可被普通测试清理器接管。

波形使用按SHA/分辨率分层的派生文件，首页/编辑器先读取可见层，不一次加载整首所有桶；原始模型JSON保留供查询/追溯，AI只查区间与所需字段。插件预设资源留在H库；CAS仅存合法完整状态与清单，不复制整套采样库。

## 物理迁移规则与顺序

N7发布资源新增 `runtime/releases/<Git commit>/`（独立源码）、`runtime/web-releases/<内容SHA>/`（前端）、`runtime/engine-bundles/<内容SHA>/`（设备/worker/runner/回环），`runtime/desktop-releases/<内容SHA>/`（桌面已测五文件）和`runtime/engine-diagnostics/<内容SHA>/`（独立诊断）。当前正式数据仍为配置中的原 `data/app-data`。`application-release.local.json`是已激活版本指针，`app.local.json`固定web与native；开发构建不直接覆盖正式输出。源码副本仅runtime/plugins是明确的资源联接，验证按源码清单读取，不能未排除reparse递归扫描/清理发布树。构建/校验入口为 `scripts/build_app_release.py`、`scripts/deploy_app_release.py`；只有应用停止后激活，`start.cmd`入口不变。`repair-audio.cmd`仅恢复自有音频进程，不改工程。

1. **本轮已做：**重写README为当前入口，创建文档导航和架构/计划/目录文档，将整理前README存为历史说明。归档未部署的浏览器分块实验到Git stash。源码、运行资源与受保护路径没有批量搬动。
2. **N1–N2：**新引擎使用新目录与独立构建目标，增加统一build/probe入口；不为了目录美观移动已有固定实现。
3. **N4–N5：**逐组迁移前端测试、纯开发探针和非固定工具；每次先`rg`枚举调用者，再更新导入/文档/命令，并在独立提交中保留必要兼容包装。脚本涉及当前工作目录和ROOT计算的必须做实际调用验证。
4. **N5后：**将Python过大的模块按域拆包，保留对外API、SDK及CLI兼容；requirements清单再评估集中到`requirements/`，连同bootstrap和锁文件引用一并改，不能只搬文件。
5. **N7：**仅在旧路径引用和运行进程均不存在、测试通过后，收口旧入口。历史编号文档不大规模重命名；导航按主题组织即可。运行数据的物理清理是另一项有明确白名单/保护校验的工作，不随代码目录迁移执行。

每次目录迁移应记录原→新路径、调用者、兼容期、验证和回退。不能用通配符移动 `data`、`runtime`、`plugins`，不能重定向已运行批次的固定路径，也不能把用户手工删除结果未经核对写成“已清理”。

## N5已实施的第一组源码归类

独立于音频行为提交，只有本轮新建代码移动；固定宿主、旧验证器、运行数据、H库未搬动。

| 旧入口 | 实现位置 | 兼容 |
| --- | --- | --- |
| scripts/probe_engine_device.py | scripts/probes/engine/device.py | 旧CLI、main/analyse导入保留 |
| scripts/probe_engine_files.py | scripts/probes/engine/files.py | 旧CLI、main/save导入保留；N2旧run不改 |
| scripts/probe_engine_plugins.py | scripts/probes/engine/plugins.py | 旧CLI保留 |
| scripts/probe_engine_protection.py | scripts/probes/engine/protection.py | 旧CLI保留 |
| scripts/probe_engine_ui.py | scripts/probes/engine/ui.py | 旧CLI保留，内部调用新e2e路径 |
| scripts/prepare_engine_ui.py | scripts/probes/engine/prepare_ui.py | 旧CLI保留，显式要求独立web-root |
| apps/web/native-transport-unit.mjs | apps/web/tests/unit/native-transport.mjs | 旧Node命令保留 |
| apps/web/engine-workspace-e2e.mjs | apps/web/tests/e2e/engine-workspace.mjs | 旧Node命令保留 |
| native/engine/FileGraph.h | native/engine/SessionGraph.h | 内部类改名SessionGraph，包含文件同步；非外部SDK |

Python新的实际入口按目录深度解析仓库ROOT；独立执行prepare_ui也能定位原有native_host模块，原生构建仍在原engine-v1。Python领域逻辑已聚到`src/music_learning/engine/{broker,controller,compiler,plugin_compiler,sync,workspaces}.py`，浏览器控制在`apps/web/src/engine/NativeTransport.ts`。日常开发先使用新目录，旧包装只保持已有文档/脚本命令兼容，不复制操作规则。
