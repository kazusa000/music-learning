# 测试临时目录与收尾

2026-10-23。开发测试统一使用：

```powershell
& scripts/maintenance/run-tests.ps1
# 指定短套件
& scripts/maintenance/run-tests.ps1 -PytestArgs @('tests/test_release_runtime.py','-q')
```

默认运行app环境中的service/engine/release套件；不自动调用音乐模型、音源全量验证或长时间连续播放。需要模型环境的测试单独安排，不能用默认app环境强行收集全部测试。

- 临时工程、音频、pytest缓存统一在`data/tmp/tests/<run-id>/`；新测试使用`tmp_path`，不再向根目录或`data/t`固定路径写临时文件。
- 日志、JUnit、退出码及清理结果统一在`runtime/test-reports/`。正常或失败退出都会清临时目录；不自动永久保留失败的大录音。只有明确需要调查时用`-KeepFixtures`，随后要写明保留路径和后续清理。
- 仅清本次新建目录；检查是否仍有进程引用，不追踪链接目标、不修改只读属性。检测到活动进程则保留并告警，不能为了清理而终止音源入库任务。
- 新构建的obj/pdb必须输出到`runtime/build/<target>/`。历史未提交的AD2文件由其他入库工作产生，本次保留，不在运行中搬动。
- 产品数据、模型、音源试听缓存、发布包和固定验证记录不归测试清理器管理。历史拒绝目录、固定N2和原始run报告仍保留。

`clean-workspace.ps1`只接收已审核的本轮精确白名单，默认只检查，`-Execute`才清理；它不是全盘或任意目录删除器。2026-10-23本机清理计划与结果位于`runtime/maintenance/cleanup-20261023/`，只涉及E盘本项目，不触碰H库、NAS或整个音源入库工作区。

## 本次实际结果

40个精确目标：28个旧版无owner播放会话、旧浏览器profile、两份已结束N7 pytest夹具、六份历史UI测试profile、两项小缓存、独立动态夹具的CMake中间文件。删除15641文件/2285目录，0错误；清理窗口E盘空闲净增约1.896GiB，不把此前共享文件的路径大小当释放量。

根目录旧`debugger-fixture.log`归到`runtime/logs/development/`；本次盘点和计划集中在`runtime/maintenance/cleanup-20261023/`。模型、env、音色试听缓存、正式素材、整个instrument-intake和data/t、原始run报告及固定发布都保留。数据库空页压缩暂缓，避免运行中增加数据库锁占用。

只做了入口/退出及删除边界核对：`pytest --version`退出0、无效选项退出4均自动删除本次临时目录；项目根和H路径被范围检查拒绝；新建junction只删链接且保留目标，检查自身临时目录也已删除。没有运行功能回归、整曲、音源发声或模型测试。

`after.json`确认正式九表/head相同、发布身份/四个原生文件SHA相同、入库进程47956身份相同、HTTP健康正常；服务未重启，没有向H或NAS读写。根目录其他入库工作的AD2 obj/pdb文件仍留原位，加入构建产物忽略规则，未删除或移动它们。
