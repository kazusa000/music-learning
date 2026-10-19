# P1 工程服务与统一接口

2026-10-18 的 P1 基线，证据见 [P1 报告](benchmarks/p1-persistence.md)。2026-10-19 已增加 [P2 整曲流水线](15-preprocessing-pipeline.md)与只读工作台，下文 P1 的 23 个操作/0001 布局为历史基线；当前是 38 个操作与 0003 迁移；[P3](17-daw-implementation.md)已实现制作编辑与同步试听，正式 Codex 对话留在 P4。

## 环境与启动

代码在 `E:\Intrest\music-learning`，本机独立 Python 环境和正式工程数据分别在 `D:\music-learning-runtime\envs\app`、`D:\music-learning-runtime\app-data`。P0 产物仍在原位置，不覆盖。当前从源码 checkout 运行，不提供可搬移的 wheel/桌面安装包。

```powershell
# 首次安装或依赖更新；不安装到系统 Python。
& runtime/tooling/Scripts/python.exe scripts/bootstrap_app.py

# 启动 API + 独立 worker，隐藏运行并记录本项目进程归属。
& runtime/tooling/Scripts/python.exe scripts/app.py start
& runtime/tooling/Scripts/python.exe scripts/app.py status
& runtime/tooling/Scripts/python.exe scripts/app.py open
& runtime/tooling/Scripts/python.exe scripts/app.py stop
```

也可双击根目录 `start.cmd`，启动后自动打开带本机会话的主页；`stop.cmd` 关闭本项目服务。运行地址 `http://127.0.0.1:8765`。不绑定 LAN，不暴露给 NAS 或其他设备。仅直接打开裸地址的新浏览器没有会话时，用 `app.py open` 建立本机会话。

`config/app.example.json` 是可提交样例；bootstrap 首次生成 `runtime/app.local.json`，后续保留已有配置。`MUSIC_APP_CONFIG` 可选择另一份配置，测试时使用独立数据目录和端口。数据库迁移自动执行，未知/更新的 schema 拒绝启动。启动前检查原生宿主和前端构建存在；模型、VST3 的实际可用性仍通过各自操作验证。

Python 依赖用 `requirements-app.lock` 的版本及 SHA-256 同步，独立于两个 GPU 模型环境。前端固定 pnpm 11.25.0，提交 `apps/web/pnpm-lock.yaml`。当前 React 19.3.0 / TypeScript 5.9.3 / Vite 8.0.0；API FastAPI 0.146.0 / Pydantic 2.14.1 / SQLAlchemy 2.0.55 / Alembic 1.20.0。升级须重新验收。

## 唯一写入路径

```text
React / Python SDK / CLI
            ↓ HTTP
    FastAPI transport
            ↓
    Service.execute(command)
            ↓ 同一 SQLite 事务
项目 / revision / 投影 / 任务 / 事件 / 幂等回执
            ↑
     worker 的受控结果发布
```

`src/music_learning/contracts.py` 为数据和操作权威，`Service` 为工程写入权威。原生宿主保持 P0 的进程隔离；worker 经同一 Service 发布 revision。前端不计算或修改权威版本，SDK/CLI 不直接写数据库。

`packages/contracts/schema.json`、`types.ts` 由 `scripts/export_contracts.py` 生成；包含参数、结果及实体契约。后端同时校验输入与回读结果。`--check` 检查生成文件漂移。核心实体包括 Project、Asset、AnalysisRun、TimingMap、Track、Clip、NoteEvent、PluginInstance、RoutingGraph、AutomationLane、Revision、Job、Conversation，以及注释与事件。

P1 的 `revisions.commit` 是完整结构化快照存储事务。它保存音乐数据和插件引用，**不会执行处理图、自动应用插件参数或生成声音**。范围编辑和处理图渲染已由 P3 的 edits.apply/project.render 接入；语义对话编辑仍在 P5。能力发现明确列出未支持的操作。

## API / SDK / CLI

已实现 23 个可发现命令/查询：

| 操作组 | 名称 |
| --- | --- |
| 发现 | `describe_capabilities` |
| 工程 | `projects.list/create/get/rename` |
| 版本 | `revisions.list/get/commit` |
| 音乐读取 | `events.query`、`annotations.query`、`time.convert` |
| 素材 | `assets.list/get/register` |
| 分析基线 | `analysis.register/list` |
| 对话存储 | `conversations.append/list` |
| 任务 | `jobs.submit/get/list/cancel` |
| 持久事件 | `events.list` |

统一请求发送到 `POST /v1/commands`，格式如下；ID 为 32 位 UUID hex，摘要为 64 位 SHA-256。

```json
{
  "schema_version": 1,
  "request_id": "00000000000000000000000000000001",
  "operation": "projects.rename",
  "arguments": {"project_id": "00000000000000000000000000000002", "title": "新的标题"},
  "expected_revision": "00000000000000000000000000000003",
  "idempotency_key": "rename-once"
}
```

所有命令要求幂等键；除新建工程外，写操作还要求当前 head 的 `expected_revision`。同一键、同一规范化请求返回原回执，即使 head 后来改变；同一键更换内容返回 `idempotency_conflict`。过期 head 返回 `revision_conflict`，事务不产生部分修改。失败不登记成功回执；修正后可重试。成功响应为 `{schema_version,request_id,ok:true,result}`；失败为 `{schema_version,ok:false,error:{code,message,details?}}`。

`jobs.submit` 返回 HTTP 202，返回排队任务而非最终成功。其余领域请求返回 200。上传、命令分别使用独立幂等命名空间。单条快照事务是原子的；批处理按顺序执行，首个失败即停止，已成功的前序命令保留。重放完整带稳定 key 的请求可安全继续。

```powershell
$env:PYTHONPATH = 'src'
$python = 'D:\music-learning-runtime\envs\app\Scripts\python.exe'
& $python -m music_learning.cli call describe_capabilities
& $python -m music_learning.cli call projects.list
# JSON 参数从文件或 stdin 读入；不在命令行拼接复杂 JSON。
'{"title":"新的研究工程"}' | & $python -m music_learning.cli call projects.create --args - --key create-study-001
# 从返回值取得 project id / current_revision，再提交导入。
& $python -m music_learning.cli import-audio 'D:\path\song.flac' --project <project_id> --expected-revision <revision_id> --key import-study-001
& $python -m music_learning.cli wait <job_id>
& $python -m music_learning.cli batch 'D:\path\commands.json'
& $python -m music_learning.cli events --project <project_id> --after 0
```

CLI 占位符需要替换，不能原样运行。所有输出为 JSON；错误退出码非零。`wait` 超时只停止等待，不取消持久任务。`asset <sha> --output <local-new-file>` 回读素材，拒绝覆盖现有文件或写网络盘。

```python
from pathlib import Path
from music_learning.sdk import Client

with Client() as music:
    project = music.call("projects.create", {"title": "研究工程"}, idempotency_key="sdk-create-001")
    task = music.import_audio(Path(r"D:\path\song.flac"), project_id=project["id"],
                              expected_revision=project["current_revision"], idempotency_key="sdk-import-001")
    completed = music.wait(task["id"])
    print(completed["status"])
```

其他传输也通过能力发现列出：`PUT /v1/uploads` 接受有大小上限的流式二进制、要求 `Idempotency-Key`，返回 upload ID；`GET /v1/assets/{sha}` 读已登记素材，校验摘要并支持 Range；`GET /v1/events` 是 SSE。浏览器不提交任意源路径或输出路径。SDK/CLI 可以从 NAS **仅以 rb** 读取并流式上传，但服务从不接收 NAS 写路径；本轮验收只使用 P0 本地副本。

API 默认要求本机随机 Bearer Token 或 HttpOnly / SameSite=Strict cookie。启动器把本机会话放在 URL fragment，主页立即移除 fragment 后换 cookie；Token 不进请求 URL、访问日志、前端包或 Git。Host、Origin 校验拒绝其他站点操作。不使用 query token，不允许任意跨域访问。

## 素材与版本

`objects/<sha前两位>/<sha>` 内容寻址去重。上传在本机 staging 写入、fsync、校验后，用不可覆盖的原子硬链接发布。数据库引用只在文件发布后提交；取消/故障产生的无引用对象不算成功，不自动清理。临时文件的写入句柄发布前已关闭，应用没有覆盖已发布对象的操作。再次使用与下载时检测摘要，损坏报错。

原始声音、模型原始输出、完整插件二进制状态作为独立素材。`assets.register` 只登记不透明文件；音频须经 `asset.import` 完整 ffmpeg 解码及 ffprobe 检查。音频解码后的技术元数据可补全先前不透明对象，但文件字节和摘要不变。插件状态另存 asset；不以参数列表代替完整状态。外部资源在 `resource_asset_ids` 中引用，P1 不自动搬移插件私有资源路径。

每个 revision 保存完整快照及摘要、父版本、作者和说明，并在同一事务生成各实体只读投影。原曲一经导入不能移除/替换；分析 run 不可覆盖。新分析只有明确写入新 revision 才进入工程。讨论/笔记绑定明确的 revision，head 改变不会悄悄重定向旧引用。P1 当前原曲工程的 sample rate / duration 固定对应源；当前研究工程仍以原曲正文为编排边界，渲染尾音单独记录；不把延长原曲源时钟当成已实现功能。

## 时间映射

采样点是原始演奏时间的权威；beat 用约分后的 `{n,d}` 有理数表示，单位是四分音符。TimingMap 保存有来源的严格递增 sample/beat 锚点，按测得锚点做分段线性映射，可表达变速/自由速度；不越出已测范围外推。缺少 TimingMap 时仍可 sample↔seconds，beat/小节转换明确失败。

弱起是 bar 0，完整首小节从 beat 0 / bar 1 开始；例如 6/8 一小节是 3 个四分音符 beat，显示的拍按八分音符计。拍号改变只能落在完整小节边界。三连音精确保存 `1/3`。记谱位置单独引用自己的 timing map，不覆盖演奏时间。区间查询为 `[start,end)` 相交语义，持续跨越选区的音符完整返回，不裁掉延音。

## 任务与恢复

SQLite WAL 队列保存三种 P1 任务：`asset.import`、`plugin.scan`、`plugin.inspect`。插件仅接受配置中本地 allowlist 下的 VST3；扫描/检查返回实际原生宿主数据。失败、崩溃、超时均不当作成功。

数据库串行领取一个执行槽；worker 心跳续租，每次 attempt 有新的 fencing token。租约过期后可重排，最多 1～5 次，达到上限标失败；取消中的过期任务直接取消。过期/旧 attempt 的结果拒绝写回。执行异常标失败，显式重试可重新提交同一 payload、使用新 key。

worker 启动独立 Python 子进程，再调用 ffmpeg/原生宿主。Windows Job Object 在 worker 崩溃时终止整个子树；任务在加入 Job Object 后才通过 gate 启动实际处理，极小的创建/归属间隙内只能等待，10 秒后自行退出。取消和超时先结束子进程，再写入最终状态；已取消的晚到结果不发布。API 与 worker 都重启后，队列/任务/事件仍保留。

事件 cursor 来自 SQLite AUTOINCREMENT，和领域事务一起提交。`events.list(after=cursor)`、SSE `Last-Event-ID` / `after` 支持断线续读；SSE 可 `follow=false` 分页读完当前积压后关闭。主页使用 SSE，并有轮询恢复；显示真实状态。原曲导入只成为 `imported`，整曲分析尚未接入，不能进入 `ready` 工作台。

## 迁移、备份与验证

Alembic `0001` 为初始冻结 schema；后续增加新迁移，不修改冻结布局。迁移到新版本前做 SQLite backup API 备份；不只复制主文件而漏 WAL。管理员可执行 `scripts/app.py backup`，生成有完整性检查的 **SQLite 元数据备份**。这不是完整工程素材备份；P6 的素材/manifest/依赖打包与独立恢复验收仍未实施。不要把数据库备份或源码 Git 当成音频备份。

```powershell
& D:\music-learning-runtime\envs\app\Scripts\python.exe -m pytest tests/service -q --basetemp=D:\music-learning-runtime\tmp\pytest-p1
& D:\music-learning-runtime\envs\app\Scripts\python.exe scripts/export_contracts.py --check
& D:\music-learning-runtime\envs\app\Scripts\python.exe -m ruff check src services tests/service scripts/bootstrap_app.py scripts/app.py scripts/manage_app.py scripts/export_contracts.py scripts/p1_acceptance.py scripts/p1_verify_restart.py
pnpm --dir apps/web typecheck
pnpm --dir apps/web build
```

真实整曲持久化脚本 `scripts/p1_acceptance.py` 要求 `--source`、`--normalized`、`--raw`、新的本机 `--output`。它通过真实 CLI/SDK 保存已有 P0b 模型事件和分析来源，检查 Surge 状态；不运行模型或工程渲染。`scripts/p1_verify_restart.py <summary.json> --output <new-local-report.json>` 实际停止/重启服务并核对所有记录与 cursor。

浏览器验收使用本地已有 Playwright Chromium：

```powershell
$env:PLAYWRIGHT_BROWSERS_PATH = 'D:\music-learning-runtime\playwright'
pnpm --dir apps/web test:e2e D:\music-learning-runtime\app-data\session-token.local D:\music-learning-runtime\references\nas-Mu000001-v1\source.flac D:\music-learning-runtime\benchmarks\my-new-p1-browser-run
```

首次环境没有浏览器时，在上述本地目录运行 `pnpm --dir apps/web exec playwright install chromium`。脚本读取本机 Token，不输出它；真实导入和创建一个明确标记的验收工程，报告与截图留在本机。首次未指定已有浏览器目录的调用曾失败于找不到 Chromium，随后使用固定目录通过，未重复下载。

接口和依赖实现参考官方文档：[FastAPI](https://fastapi.tiangolo.com/tutorial/)、[Pydantic Models](https://pydantic.dev/docs/validation/latest/concepts/models/)、[SQLAlchemy transactions](https://docs.sqlalchemy.org/en/20/orm/session_transaction.html)、[Alembic](https://alembic.sqlalchemy.org/en/latest/tutorial.html)、[React](https://react.dev/learn)、[Vite](https://vite.dev/guide/)。
