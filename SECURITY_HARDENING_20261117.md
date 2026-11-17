# Texture Converter · ALPHA 1.0 — 安全加固 / Security hardening

作者 / Author: private0516. 2026-11-17.

本轮只调整本地解析、文件边界、外部编码器调用与预览后台执行；保留已有转换格式、中文/英文界面、配置字段及动效。

## 已修复的边界

- NIF 改为同一次受保护读取，最多读取 64 MiB + 1 字节；不再先 `stat` 后无界 `read_bytes`。BGSM/BGEM 继续采用 1 MiB 上限，旧模板读取也复用该入口。
- JSON 配置及 CLI options 最多 8 MiB，最多嵌套 64 层，拒绝重复属性和非有限数字。材质 JSON 继续使用独立的 1 MiB/字段类型校验。
- 所有 DDS 解码入口先检查维度与 mip 数；最多 16,384 边长、64 Mi 像素、512 MiB 文件、256 MiB 单 mip payload。保留 legacy RGB/BGR/L8、row pitch、sRGB 和 SNORM 兼容处理。行步长异常不会触发超大读取。
- PNG mask 先读固定 IHDR 检查 magic、维度及源图网格，再限定 `formats=['PNG']`；DDS 明确限定 `formats=['DDS']`。伪装为 `.png` 的 GIF 不会进入 GIF 探测器。支持当前 Pillow 13 的 16 位灰度 mask。
- Windows 设备名、ADS、UNC/device namespace、空路径分量、尾点/尾空格及 traversal 被拒绝。扫描/读取/发布拒绝 symlink、junction 和其他 reparse point。检查已打开句柄的属性，目录句柄不共享 DELETE，读文件句柄同时不共享 WRITE；普通目录替换、源文件写入与替换因此受阻。输出采用独占创建/发布，Python 新建 staging/scratch 文件不再用 `wb` 截断提前放置的硬链接。
- 扫描通过流式 `scandir` 限制到 2,000,000 个目录项、250,000 个目录、250,000 个 DDS/BGSM/BGEM 资源文件，以及 1 TiB 资源声明总大小；超过时报告对应的 `SCAN_*_LIMIT`，可选择更小的资产目录继续。转换继续使用原有工作集估算和取消检查。
- texconv 固定安装路径仍由程序选择；JSON 不可指定可执行文件。调用使用 argv 列表、`shell=False`、受控 cwd/PATH，执行期间固定程序文件名，保留 600 秒超时、取消、诊断预算及 owned-process 回收。独立 scratch 输出不使用 `-y`；结果经验证后有界、独占复制到 staging，避免 `shutil.move` 跨卷回退到 `wb`。

## 预览执行

NIF 和金属重建预览通过固定 Python worker 脚本在独立进程执行。父进程先创建 Windows Job Object，再通过一个字节的握手允许 worker 开始解析。Job 限制单个进程、2 GiB 进程内存，并在关闭时结束所拥有的 worker；120 秒超时或用户取消会回收该进程及临时 IPC 目录。取消不弹出延迟错误框。

IPC 采用最多 8 MiB JSON 与最多 256 MiB 原始数值数组；不使用 pickle、object dtype 或可执行序列化格式。数组维度、字节范围、总分配量和 NIF 几何/纹理形状在父进程重新校验，共享纹理数组通过引用传递，避免重复分配。对照视图在 worker 中缩至 900px，源尺寸 candidate/mask 保持不变。

8192×8192 DDS 的原生预览已用自生成 BC1 mip chain 验证，worker 直接取 1024×1024 mip。没有降低原有导出/金属重建的 1 GiB 工作集估算；复杂的大图仍可能按既有预算被拒绝，错误会指出预算原因。

## 验证与剩余限制

离线自生成 fixtures 覆盖设备路径、真实 Windows junction、目录替换/源文件写锁、硬链接、超大 header/pitch、增长 NIF、JSON 深度、伪装 GIF、16 位 PNG、IPC object dtype、真实 native BC1 预览、真实 bundled texconv BC7 编码/解码，以及 worker 正常完成、主动取消、超时、异常退出和 Job 内存拒绝。完整回归证据位于项目根目录 `validation/security-hardening-20261117/texture/`；该目录的 `result.json` 与 `full-regression.txt` 记录实际环境和最终结果。

**这是同一用户权限下的进程故障隔离，不是 OS 安全沙箱，也不承诺绝对安全。** Job Object 限制资源和生命周期，不限制文件系统/网络权限。Python/NumPy/Pillow/Qt/DirectXTex/OpenGL 仍包含 native 代码；导出阶段的部分解码与 GPU 上传仍在应用进程内。具有同用户执行权限的进程仍可干预安装文件、临时目录或 reparse 元数据；原生工具内部创建文件的瞬间不由 Python 完全控制。不要把本轮 guard 解释为抵御已控制同用户会话的攻击者。

This release adds bounded reads, explicit decoder selection, Windows path and file-publication checks, and cancellable worker processes. Preview workers run as the same user and are not an OS sandbox. Native parsing/rendering and malicious same-user interference remain material limitations. No third-party sample upload or external malware-scanning service was used.
