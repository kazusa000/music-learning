# 仓库目录职责与整理顺序

2026-10-21。目标是按职责找到代码、运行数据与验收入口，不通过大搬家解决播放架构。当前目录包含源码、70个启动/工具/探针脚本、93个前端文件及大量历次运行数据；下表区分**现在保留的位置**和**随后开发的整理目标**，不是已完成的物理迁移。

## 当前保留与新增位置

| 路径 | 职责与约束 |
| --- | --- |
| `apps/web/src/` | React视图和交互；后续按workspace/library/discussion/instruments/shared及engine client分模块，前端不处理工程音频 |
| `native/desktop/` | WinForms/WebView2外壳与DPI；暂不更换UI框架 |
| `native/engine/`（待建） | 本地设备、样本时钟、实时图、混音及文件读取 |
| `native/plugin-worker/`（待建） | 新原生隔离插件执行；不覆盖旧track host |
| `native/audio-host/`、`native/live-track/` | 已验证离线与旧播放实现；迁移完成前保留兼容，固定文件不搬 |
| `native/include/`、`native/tests/`、`native/test-plugin/` | 可复用适配、小型原生测试、确定性VST夹具 |
| `src/music_learning/` | 唯一Python领域包，保留现有导入名；渐进拆分engine、project、instruments、analysis、dialogue等子包 |
| `services/api/`、`services/worker/` | 薄进程入口、冻结数据库迁移；不再复制领域逻辑 |
| `packages/contracts/` | 生成schema和TS类型；不手改生成物 |
| `packages/agent-sdk/` | SDK入口说明；Python实现仍在统一包，不创建第二份操作规则 |
| `scripts/` | 当前兼容入口；逐步归类app、build、models、instruments、maintenance及probes，保留被引用入口 |
| `tests/service/` | 后端事务/规则回归；新增engine协议和内核测试使用`tests/engine/` |
| `apps/web/tests/`（迁移目标） | 前端unit/e2e/probes分类；当前根部.mjs保留至路径更新并验证 |
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
    engine-v1/              新引擎独立输出（待建）
  desktop/                  已构建外壳及SDK
  toolchain/ tooling/       工具与编译环境
  third-party/              固定依赖源码
  *.local.json              会话定位/构建指纹/后台批次入口等
```

项目数据/模型/构建只在`E:\Intrest\music-learning`内，音源资源保持用户指定`H:\music\Sound Library`。不选新盘，不搬C盘系统依赖，不探查E盘其他项目。旧音源删除/恢复规则和NAS只读边界不变。

后续新引擎只创建 `data/app-data/engine/` 下小型会话清单、控制状态和自有进程记录；数据库和CAS不复制一份到这里。新验收统一使用 `data/maintenance/native-engine/<run-id>/{report,logs,...}`，自动化测试用显式独立`--basetemp`。默认不复制长音频；确需使用现有素材时只读引用，或先列出独立文件计划并记录是否硬链接。原始报告、受保护批次文件及此前清理拒绝路径均不可被普通测试清理器接管。

波形使用按SHA/分辨率分层的派生文件，首页/编辑器先读取可见层，不一次加载整首所有桶；原始模型JSON保留供查询/追溯，AI只查区间与所需字段。插件预设资源留在H库；CAS仅存合法完整状态与清单，不复制整套采样库。

## 物理迁移规则与顺序

1. **本轮已做：**重写README为当前入口，创建文档导航和架构/计划/目录文档，将整理前README存为历史说明。归档未部署的浏览器分块实验到Git stash。源码、运行资源与受保护路径没有批量搬动。
2. **N1–N2：**新引擎使用新目录与独立构建目标，增加统一build/probe入口；不为了目录美观移动已有固定实现。
3. **N4–N5：**逐组迁移前端测试、纯开发探针和非固定工具；每次先`rg`枚举调用者，再更新导入/文档/命令，并在独立提交中保留必要兼容包装。脚本涉及当前工作目录和ROOT计算的必须做实际调用验证。
4. **N5后：**将Python过大的模块按域拆包，保留对外API、SDK及CLI兼容；requirements清单再评估集中到`requirements/`，连同bootstrap和锁文件引用一并改，不能只搬文件。
5. **N7：**仅在旧路径引用和运行进程均不存在、测试通过后，收口旧入口。历史编号文档不大规模重命名；导航按主题组织即可。运行数据的物理清理是另一项有明确白名单/保护校验的工作，不随代码目录迁移执行。

每次目录迁移应记录原→新路径、调用者、兼容期、验证和回退。不能用通配符移动 `data`、`runtime`、`plugins`，不能重定向已运行批次的固定路径，也不能把用户手工删除结果未经核对写成“已清理”。
