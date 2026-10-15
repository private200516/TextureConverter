# TextureConverter · ALPHA 1.0

本地 BGSM/BGEM 材质转换与受支持的 DDS 适配工具。项目代码采用 GPL-3.0；作者为 private0516。原项目使用 GPT-6 Astra 辅助开发，代码与界面包含 AI 生成内容。本项目不代表 Bethesda、OpenAI 或依赖项目背书。

这是完整的源代码开发包，包含原包字体、图标、经核对的 `tools/texconv.exe` 及其 MIT 通知；字体与图标的公开发布已获用户明确授权，资源按原始字节保留。不含预编译产品 EXE、Python runtime、安装后的 vendor 依赖或用户游戏样本。随包编码器的身份不等于本次重新运行或验证了原生 DDS 编码。

## Windows 下载

[TextureConverter.zip](downloads/TextureConverter.zip) · [SHA256 校验文件](downloads/TextureConverter.zip.sha256)

下载后先核对 ZIP 的 SHA256，再完整解压，并保留包内配套目录。Windows 分发包与源码开发包分别审核，文件布局以下载包内说明为准；下文的源码环境说明不描述 ZIP 内的组件。

ZIP 使用上面的独立校验文件。`SOURCE_MANIFEST.json` 仅记录源码文件身份，不是下载 ZIP 的校验清单。源码准备流程不生成 Windows ZIP，也不据这些链接声称已经重新构建产品、验证重新打包结果，或确认其与历史 B6 ZIP 逐字相同。下载包的内容与打包校验由独立分发审核负责。

## 安装源码依赖

在仓库根目录使用 Python 3.12，创建独立环境。以下命令供开发者自行执行；本次整理没有运行安装、产品、构建器或测试。

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

`requirements.txt` 固定 PySide6-Essentials、Shiboken6、Pillow 和 NumPy 版本。依赖及未捆绑资源见 [UPSTREAM_DEPENDENCIES.md](UPSTREAM_DEPENDENCIES.md)。开发时直接使用源码根目录，不需要发布包的 `app/`、`runtime/`、`vendor/` 布局。

## GUI 源码启动

安装固定依赖后，在仓库根目录启动 Qt/PySide6 界面。先把可变配置指向用户本地目录，避免将本地路径写回仓库跟踪的干净 `config.json`：

```powershell
$sourceConfigDirectory = Join-Path $env:LOCALAPPDATA 'private0516\TextureConverter'
New-Item -ItemType Directory -Path $sourceConfigDirectory -Force | Out-Null
$env:TEXTURE_CONVERTER_CONFIG = Join-Path $sourceConfigDirectory 'source-config.json'
.\.venv\Scripts\python.exe -B -X utf8 .\main.py
```

选择自己的输入目录和独立输出目录，检查 BGSM/BGEM 开关与 Metal 模式。普通转换可使用 Metal OFF。预检可先检查任务；保存完整日志和逐任务报告。本次没有重新启动或验收此源码 GUI。

## CLI

输入和输出必须分离。下列目录由使用者自行准备，仓库不提供游戏资产。建议从新输出目录和少量自己的样本开始。

```powershell
.\.venv\Scripts\python.exe -B -X utf8 .\texture_cli.py '..\assets-owned\materials76' '..\results\texture-plan' --direction 76-to-4 --materials-only --dry-run
.\.venv\Scripts\python.exe -B -X utf8 .\texture_cli.py '..\assets-owned\materials76' '..\results\materials-76-to-4' --direction 76-to-4 --materials-only --metal-mode off
.\.venv\Scripts\python.exe -B -X utf8 .\texture_cli.py '..\assets-owned\materials4' '..\results\materials-4-to-76' --direction 4-to-76 --materials-only --metal-mode off
```

`--materials-only` 仅转换材质，不打包或验证其纹理依赖。`--dry-run` 仍会创建运行目录和报告，不生成可部署的材质/DDS 输出。正向材质目标为 v2，反向材质目标为 v22；反向 DDS/PBR 重建未实现，不能把材质双向转换理解为贴图算法可逆。

每次运行会生成新的 `run_<time>_<id>/`：

- `converted/materials/`、`converted/textures/`：已提交输出。
- `reports/`：逐任务状态、警告、错误、验证、源哈希和日志。
- `COMPLETE` / `PARTIAL`：文件处理状态，不是游戏外观验收；也应检查任务数，空任务不证明发生过转换。

完整保留警告，检查实际输出格式与引用关系。后备 DDS 编码路径只覆盖部分格式；例如 BC7 请求可能降级并告警。不要把后备结果、原生编码器结果或不同质量设置视为等价。

GUI 的源码入口是 `main.py`，使用 Qt/PySide6，原包字体与图标均随源码提供。GUI 保留 76→4 流程，不据材质反向 CLI 宣称提供反向 GUI。

## 已有验证与未解决事项

既有 B6 工程记录中，Mesh 与 Texture 合计 **166 项自动化合成检查：原批次 164 项，加独立夹具修正后的 2 项 Mesh 补充检查**。不是每个仓库各 166 项；原 T8 聚合失败状态保留。对应夹具修正已按批准纳入 Mesh 准备包，不改变产品代码；这不表示当前两个公开测试树已全部通过。本次没有运行当前仓库的测试。

既有合成 CLI 记录覆盖 Mesh 双向和 Texture 材质单独转换双向，不包含由此推导的真实 DDS/原生编码器覆盖。开发者可自行运行源码测试，但此处不预先承诺结果：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

T11 的既有 Texture GUI 记录包含两次转换：每次提交 2 个材质任务、0 个后台错误，共有 4 个有效 v2 材质头。原始协调器仍记录 `0/2`，因为它要求每种扩展名只出现一个文件，而两次运行各留下了一组输出。这个计数规则属于记录工具；原始结果未被改写。

用户另行明确报告了失败或跳过提示，并说明顺序为“转换 → 预检 → 再次转换”，且不是字段未映射或外观警告。两次后台成功不能否定该 UI 事件，也不将两次运行归因为误点。**具体 UI 问题仍未定位，GUI 验收仍待完成。**

上述记录不确认真实 DDS 编码、游戏内效果、GUI 取消延迟、完整动态安全、杀毒软件放行或发布许可。已有工程记录也不能替代对当前源码包的重新检查。

## 构建、隐私与许可

`tools/legacy/` 中旧构建器只作本地工程参考，依赖原开发环境，不是新的通用构建流程。随包的 `launcher.manifest` 是启动器清单源文件，不是产品 EXE。本包未重新构建或认证发布二进制。

仓库跟踪的 `config.json` 采用核对后的干净默认配置，不含用户输入目录。GUI 示例通过 `TEXTURE_CONVERTER_CONFIG` 将后续配置保存为用户本地目录的 `source-config.json`；未设置此变量时，直接从源码启动默认会读写源码目录的 `config.json`。手工蒙版和启动日志也可以保存在用户本地应用数据目录；输出报告会保存输入路径、文件名和哈希。分享前检查这些记录，不要把私人配置、游戏资产、蒙版、日志或结果目录提交到仓库。

项目代码见 [LICENSE](LICENSE)。依赖保留各自许可，见 [THIRD_PARTY_NOTICES.txt](THIRD_PARTY_NOTICES.txt)、`licenses/` 与 [UPSTREAM_DEPENDENCIES.md](UPSTREAM_DEPENDENCIES.md)。项目 GPL 不重新许可第三方字体、图标或游戏资源。

English: Complete source-development package for the local material/texture tool. Install pinned dependencies in a Python 3.12 venv and run `python main.py`. The original authorized fonts/icon and verified texconv tool with its MIT notice are included; product EXEs and packaged runtimes are not. Historical material commits and valid headers do not resolve the separately reported UI failure/skip event, or certify this checkout, native DDS encoding, gameplay, security or release readiness.
