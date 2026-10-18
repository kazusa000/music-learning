# 原生 VST3 宿主运行说明

状态：P0a 原型已实现。提供独立进程中的能力查询、VST3 扫描、参数/状态查询和离线多轨渲染。接口是 P0 原生处理原语，尚不是 P1 的完整工程数据库、编辑事务或 HTTP 服务。

## 构建与验证

在仓库根目录的 PowerShell 运行：

```powershell
uv --cache-dir runtime/cache venv --python 3.11 runtime/tooling
uv --cache-dir runtime/cache pip sync --require-hashes --python runtime/tooling/Scripts/python.exe requirements-native.lock
& runtime/tooling/Scripts/python.exe scripts/bootstrap_native.py
& runtime/tooling/Scripts/python.exe scripts/build_native.py
& runtime/tooling/Scripts/python.exe -m pytest tests/native -q --basetemp=runtime/pytest-p0a
& runtime/tooling/Scripts/python.exe scripts/make_native_demo.py
```

已有 `runtime/tooling` 时直接使用后续命令，无需重建环境。bootstrap 下载校验摘要，并将 JUCE、编译工具和插件保存在被 Git 忽略的目录中。插件安装包仅提取文件，不运行其系统安装流程。MSVC/Windows SDK 使用审查并锁定的 portable-msvc 辅助脚本，从微软清单获取并解包；只有本地构建路径被配置。

已有 MSVC Developer 环境时可给 bootstrap 加 `--skip-compiler`。构建脚本优先使用本仓库便携 MSVC，否则使用调用进程中的 `cl.exe`。普通 CMake presets 也已提供，但要先准备 MSVC 环境。

产物：`runtime/build/msvc/bin/ml_audio_host.exe` 与 `runtime/build/msvc/MLFixture_artefacts/Release/VST3/MLFixture.vst3`。测试插件不安装到系统插件目录。

## AI 调用

Python 原型客户端：

```python
from pathlib import Path
import sys

sys.path.insert(0, "scripts")
from native_host import NativeHost

host = NativeHost()
capabilities = host.call({"schema_version": 1, "action": "capabilities"})
plugins = host.call({
    "schema_version": 1,
    "action": "scan",
    "plugin_path": str(Path("plugins/surge-xt-1.3.4/Surge Synth Team/Surge XT.vst3").resolve()),
})
```

CLI 支持 JSON 请求/响应文件：

```powershell
& runtime/tooling/Scripts/python.exe scripts/native_host.py request.json response.json --timeout 120
```

每次调用使用唯一目录 `runtime/requests/<id>/`，保存请求、响应、stdout/stderr 和执行终态。插件崩溃、超时、拒绝请求与正常结果有不同状态；超时/取消只终止本次宿主进程树。扫描与渲染各自是独立原生进程，插件失败不会结束调用者。

`--capabilities` 也可直接在原生 exe 上运行，用于无插件依赖的构建检查。

## 请求

所有请求需要 `schema_version: 1` 和 `action`。

| action | 其他字段 | 返回 |
| --- | --- | --- |
| capabilities | 无 | 当前真实支持的操作与限制 |
| scan | plugin_path | 类描述、名称、版本、identifier、输入输出及描述 XML |
| inspect | plugin | 完整状态、参数、总线、版本、二进制摘要与可读取的 VST3 class ID |
| render | project、output_path | WAV、测量、初始插件快照、处理后参数与状态 |

多类插件必须传入扫描返回的 `identifier`，不能默选第一类。sfizz 的立体声类与 multi 类是实际案例。

plugin 支持 `id`、`path`、`identifier`、`parameters`（稳定参数 ID 到归一化数值的映射）、`state_base64`、`preset_path`（标准 VST3 预设），以及已捕获的描述/校验元数据。参数名称用于发现，不能代替稳定 ID。

state_base64 使用 RFC 4648 编码，内容是 JUCE VST3 的完整 component/controller 状态容器；`state_encoding` 为 `rfc4648-juce-vst3-state`。恢复时检查编码、容器、状态摘要、插件二进制和版本。参数快照用于检查，不能代替该状态。

sfizz 1.2.3 另有经源码核对与实际测试的 `sfizz_sfz_path` 适配。该操作只适用于指定版本/类，更新其已知状态格式中的文件引用，其他设置保留。输入 SFZ 必须存在；恢复快照仍需原采样路径可用。完整资源搬迁与工程打包留给 P1 的素材系统。

## 渲染快照

project 字段：`revision_id`、`sample_rate`、`block_size`、`duration_samples`、`tail_samples`、`tempo`、`meter`、`tracks`。

每轨包括 `id`、`gain_db`、`pan`、`plugins`（串联）、`notes`、`controllers`、`audio_clips`、`automation`。具体可执行例子见 [make_native_demo.py](../scripts/make_native_demo.py) 和 [原生测试](../tests/native/test_host.py)。

- notes 使用整数 `sample`、`duration_samples`、`note`、可选 `channel` 与 `velocity`。
- controllers 使用 `sample`、`channel`、`controller`、`value`。
- audio_clips 使用 `path`、`sample`、可选 `source_offset_samples`、`duration_samples`、`gain`。音频当前必须是相同采样率的单/双声道可解码文件。
- automation 使用 `plugin_id`、`parameter_id`、`interpolation: step` 与 `points: [{sample, value}]`。引擎在指定样本边界拆块执行，当前不支持线性曲线。
- tempo 使用 `[{sample, bpm}]`，meter 使用 `[{sample, numerator, denominator}]`，从样本 0 开始且严格递增。

每轨串行效果累计延迟在输出时补偿，轨道求和对齐；处理过程中延迟改变则拒绝本次发布，需要重新生成配置。局部子块的 MIDI 偏移、速度和拍号传递已经通过真实 VST3 夹具测量。

输出是 32 位浮点立体声 WAV。现有输出路径被拒绝，先写临时文件再发布。记录 peak、RMS、削波数量、有效信号样本和耗时。峰值/RMS 不等于响度或审美评价，LUFS/频谱分析将在后续制作工具中补充。

返回的 snapshot 捕获**处理前的初始配置**，可以保存为 JSON 后在新进程重渲染。`tracks[].plugin_readback_after` 则反映自动化等处理后的状态。随机插件不能据相同初始配置承诺逐样本相同；确定性测试插件与当前 SFZ 夹具可做精确/容差对比。

## 当前边界

支持最多 64 轨、600 秒内容和 60 秒显式尾音、8～192 kHz、16～8192 样本处理块；当前混音缓存整段输出，后续再优化为流式。仅支持立体声主总线、轨内串联、轨间求和，尚无发送/侧链/主总线效果图、实时设备播放或线性自动化。

未知配置字段直接报错，不能静默忽略不支持的路由或编辑。当前原型直接接收本地绝对路径，P1 服务将用受控 asset_id 与工程事务包装，不将此原语直接暴露给浏览器。

没有前端、整曲模型流水线、完整工程保存/迁移服务或对话产品。P0a 的快照恢复不等于第一阶段 F01～F22 全部通过。验证记录见 [P0a 报告](benchmarks/p0a-native.md)。
