# Security / 安全说明

Revision: v1.0 · Private0516

对预览进程返回的数组格式、偏移及载荷做一致性校验。材质 JSON 仅接受匹配编辑器格式的单层对象。偏好设置采用原子保存。

检查预览索引和总量限制，固定输入文件和报告目录的句柄，复核复制的外部贴图并使用独占方式创建报告。

共享根目录仅发布游戏资源。关闭报告时命令行不保留启动器转录。

## Scope / 范围

这是本地文件转换工具。本次加固针对异常文件造成的过量读取、资源消耗、输出路径逃逸/意外覆盖、预览崩溃影响主界面、启动器日志失控及残留子进程。它不是安全沙箱或恶意软件检测器，也没有获得安全认证。请从项目公开仓库/发布页取得完整程序；保持普通用户权限，不需要管理员运行。

程序没有自动更新、遥测或云端推理。AI 标注描述开发过程，不表示运行时会上传模型、贴图或材质。Mesh 的 WebView2 桥只服务本机受控页面，外部导航及暴露方法有专门限制。依赖安全核验仅向公开服务提交公开包名/版本；没有上传 EXE 或用户资产。

## Implemented controls / 本轮措施

- 启动器使用固定的随包 Python 和入口、独立工作目录与 `-I`，清理子进程的 Python/Qt 搜索路径注入变量，不修改系统环境。
- 标准输出/错误按有界片段处理，单次启动日志限制为 8 MiB；Job Object 在启动器退出时清理归属子进程。启动器正常调用失败会报告错误，不提升权限。

- 材质、配置和 NIF 预览读取采用同句柄有界读取；拒绝设备名、ADS、特殊路径及 reparse 替换，输入/输出父目录在关键操作时用 Windows 句柄固定。
- 扫描上限为 250,000 个资源文件、250,000 个目录、2,000,000 个条目及 1 TiB 所选资源；支持取消，超限提示分批。
- 校验任务 ID 和源/目标边界；结果采用独占发布。DDS 文件上限 512 MiB、64M 像素、单 mip 256 MiB；具体操作仍受额外内存预算限制。
- PNG/DDS 在打开时限定接受格式，避免先调用无关的图像解析器；保持 8192 尺寸在预算内的正常处理。
- NIF/metal 预览放到固定脚本的子进程；JSON 与有界数值数组传输，不使用 pickle。支持取消、120 秒超时、2 GiB Job 内存限制和退出清理；工作进程先加入 Job，再开始解析。

## Verify your download / 校验下载

每个 ZIP 附 SHA-256；解压目录还有 SHA256SUMS.txt 和 PUBLIC_MANIFEST.json。下载哈希应与同一官方发布页公布值比较。在 PowerShell 中可运行 `Get-FileHash -Algorithm SHA256 -LiteralPath '.\TextureConverter.zip'`。

校验清单能发现相对所发布清单的损坏或变化；如果文件和清单同时被替换，它不能证明发布者身份。自编启动器目前没有受信任的 Authenticode 签名；Windows 的信誉提示仍可能出现。没有添加根证书、关闭杀毒软件或改变系统安全设置。

## Dependencies / 依赖维护

Pillow 从 12.3.0 更新到 13.0.0；PySide6-Essentials/shiboken6 从 6.9.2 成对更新到 6.12.0。官方 PyPI wheel 下载字节经 SHA-256 校验；保留许可、METADATA/RECORD 与实际部署清单。Qt 继续只部署原先的必要模块/插件，不加入 QML、网络或 XML 模块。NumPy 2.3.5 和签名有效的现有 Microsoft texconv 保留。

升级依据包括 [Pillow 13.0.0 安全说明](https://pillow.readthedocs.io/en/stable/releasenotes/13.0.0.html) 和 [Qt SVG CVE-2026-8168](https://www.qt.io/blog/security-advisory-cve-2026-8168)。版本更新仍须结合实际调用路径和测试判断，不能据此声称所有原生依赖都无漏洞。

Python 运行时保留 3.12.14，其版本及来源会记录在 BUILD_PROVENANCE.json；没有冒充升级到 3.12.16。上游 3.12 分支安全发行已是源码发行，不能把其他 ABI 的解释器直接覆盖 CP312 原生扩展。审计确认的 TemporaryDirectory 清理竞态涉及本地并发替换目录；Windows 上缺少相关 dir_fd 安全能力，上游新版也保留相应限制。本次没有拼接不兼容的标准库补丁。

已知数据库结果、原生组件及系统 WebView2/Windows 的覆盖范围不同。审计时“无返回漏洞”不等于不存在漏洞。应继续维护经验证的完整运行时；详见上游 [Python 3.12.16](https://www.python.org/downloads/release/python-31216/) 与 [TemporaryDirectory issue](https://github.com/python/cpython/issues/157579)。

## Remaining boundaries / 限制

- 转换器及其子进程以当前用户身份运行。Job Object 管理进程寿命/资源，不限制文件系统和网络权限；它不是 AppContainer。
- 路径检查、独占创建及 Windows 句柄保护降低意外覆盖和部分并发替换风险，不能防住已控制当前用户账户或可改程序目录的进程。不要让不可信进程同时修改输入、输出、程序或临时目录。
- 大小、计数及时间预算会拒绝超限资产。通过合成样本和回归测试不等于任意 NIF、DDS、BGSM/BGEM 均兼容，也不代替游戏内验证。
- 未进行外部渗透测试、完整原生编解码器源码审计或杀毒认证。仍有旧运行时和签名方面的维护工作。

## Reports / 问题报告

报告时注明工具名称、版本和上述 revision、操作步骤和预期/实际结果；优先提供不含游戏资源或私人路径的最小合成样本。日志、配置和转换报告可能包含本地路径，分享前请检查。不要把包含私人信息的文件直接贴到公开 issue。

English: This revision bounds untrusted input and resource use, strengthens output publication, isolates preview failures, and improves launcher process/log handling. Processes still run as the current user, not in an OS security sandbox. The custom launchers remain unsigned and the bundled CPython remains 3.12.14. Verified download hashes detect changes relative to the published manifest but do not authenticate a replaced manifest. Source, pinned dependencies and third-party notices are provided for review; no safety certification is claimed.
