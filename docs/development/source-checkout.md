# 源码准备

当前主要运行平台为 Windows x64，Python 使用 3.11。仓库提供源码、配置模板、依赖锁和产品展示资源；用户工程、模型权重、音源库、原生构建工具、登录状态及预构建发布包不在 Git 中。

## Python 与前端

从仓库根目录准备应用环境：

```powershell
uv venv data/envs/app --python 3.11
uv pip sync requirements/app.lock --python data/envs/app/Scripts/python.exe --require-hashes
pnpm --dir apps/web install --frozen-lockfile
pnpm --dir apps/web typecheck
pnpm --dir apps/web build
```

前端使用 `apps/web/package.json` 中锁定的 pnpm 版本。上述步骤只准备 Python／前端部分，不表示原生播放、模型或付费音源已经可用。依赖的分环境说明见 [requirements](../../requirements/README.md)。

`config/app.example.json` 使用相对于仓库工作目录的示例路径；复制后应从仓库根目录执行，或改为自己的绝对路径。真正的配置放 `runtime/app.local.json`，本机文件不提交。`bootstrap_app.py` 在生成本机配置时会根据所选数据目录填入实际路径。

## 原生组件与可选功能

- 原生源码和依赖版本在 `native`、`CMakeLists.txt` 与 `config/native-dependencies.json`，准备入口是 `scripts/bootstrap_native.py`，构建入口是 `scripts/build_native.py`、`scripts/build_engine.py`、`scripts/build_desktop.py`。各自参数可先用 `--help` 查看。
- AI 使用用户自行安装并登录的 Codex 运行组件；配置和适配器不包含登录凭据。当前有固定版本／摘要要求，源码获取不等于已经准备了对应运行组件。
- 音乐分析模型按 `config/model-dependencies.json` 和各自依赖环境准备。不要把权重加入仓库。
- 音源按自己的安装目录配置；商业插件、预设状态和样本库由使用者单独准备。源码中的乐器展示目录不等于已安装了那些音源。

完整应用仍按[不可变发布流程](native-release-operations.md)准备后启动。当前尚未整理成适合首次安装的一键发行包，`start.cmd` 用于已准备好的本机安装。

开发与测试见[贡献入口](../../CONTRIBUTING.md)；本次只进行了目录和入口检查，没有在新机器重新执行全套安装。
