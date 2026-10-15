# TextureConverter 上游依赖

项目代码采用 GPL-3.0。第三方依赖保留自己的版权和许可，项目许可不重新许可字体、图标或游戏资产。此包发布可读源码和经核对的原包资源，包含 `tools/texconv.exe` 及其 MIT 通知，不附完整 Python runtime、vendor 依赖目录或产品 EXE。

## 固定 Python 依赖

使用 Python 3.12 的独立 venv，按 `requirements.txt` 安装：

| 包 | 版本 | 说明 |
|---|---|---|
| PySide6-Essentials | 6.9.2 | Qt/PySide6 界面、OpenGL 与所需绑定 |
| shiboken6 | 6.9.2 | Qt for Python 绑定支持 |
| Pillow | 12.3.0 | 本地图像处理 |
| numpy | 2.3.5 | 本地数组与部分 DDS 后备算法 |

这些是声明的固定版本，不代表本次已下载、安装或重新测试。Qt/PySide6、Shiboken、NumPy、Pillow 和 Python 的许可应以各官方包随附的许可与元数据为准；保留现有 `THIRD_PARTY_NOTICES.txt`、`licenses/GPL-3.0.txt`、`licenses/Qt-LGPL-3.0-only.txt`，并为未来实际分发的依赖补齐对应通知。

现有上游入口：[Qt for Python 6.9.2 源码](https://code.qt.io/cgit/pyside/pyside-setup.git/?h=v6.9.2)、[Qt 6.9.2 子模块](https://download.qt.io/archive/qt/6.9/6.9.2/submodules/)。这些链接不表示本包包含了其完整源码；本次没有联网获取。

## texconv 与后备路径

Microsoft DirectXTex 的 `texconv.exe` **随本源码包保留在 `tools/texconv.exe`**，沿用核对后的原工程包字节。上游为 [Microsoft DirectXTex](https://github.com/microsoft/DirectXTex)，MIT 通知保留在 `licenses/DirectXTex-MIT.txt`。这说明所附工具的来源与身份范围，不代表本次新做了原生 DDS 编码测试。若以后替换编码器，应重新核对版本、身份、来源和许可。

程序在固定工具位置查找编码器，不从处理配置接收任意可执行程序路径。缺少编码器时可能采用 NumPy/Pillow 后备路径；它只覆盖部分 DDS 格式，BC7 请求可能降级并告警。后备路径不等于 texconv 的格式覆盖、质量、速度或原生编码验证。查看每次报告中的实际编码器选择、输出格式和警告。

材质单独转换不等于 DDS 编码测试；正反向 BGSM/BGEM 合成检查也不能证明反向纹理重建。当前不提供 DDS/PBR 反向重建。

`templates/default.bgsm`、`templates/default.bgem` 是随包保留的历史生成模板，生成源码为 `tools/gen_templates.py`。它们不是当前字段级转换器猜测布局的依据，也不构成真实游戏材质或现代格式覆盖的验证证据。

## 字体、图标与本地记录

原包字体和图标的公开发布已获用户明确授权，本包按核对后的原始字节保留。项目 GPL 不重新许可第三方资源；各自条款仍独立适用。这不是对所有第三方许可的统一认证，也不表示本次重新验收了 GUI 外观。

配置、手工蒙版、启动日志及运行报告均可能保存本地资产路径、选项和哈希。仓库跟踪干净的 `config.json`；README 的源码 GUI 示例使用 `TEXTURE_CONVERTER_CONFIG`，把可变配置放在用户本地目录的 `source-config.json`，避免覆盖仓库默认值。分享前应检查这些本地记录。仓库不包含私人配置、原始游戏资源或原始机器审计报告。

历史 `tools/legacy/` 构建器依赖原开发环境，只作参考。本包没有新增通用打包器，没有重建或认证发布 EXE。请从 README 的源码 venv 流程开始；源码安装不需要旧发布包的 `app/`、`runtime/`、`vendor/` 布局。

English: Install pinned dependencies into a Python 3.12 venv. Third-party terms remain separate. The verified texconv tool with its MIT notice and the original user-authorized fonts/icon are included; product EXEs and packaged runtimes are not. Partial fallback is not equivalent native-codec coverage, and material-only validation does not establish DDS, gameplay, GUI or security acceptance.
