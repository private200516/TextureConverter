# -*- coding: utf-8 -*-
"""
config.py — 配置模型、默认值、JSON 持久化、转换模式预设。
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from dataclasses import asdict, dataclass, field
from options_policy import processing_options, job_options

APP_NAME = "FO76_to_FO4"
APP_TITLE = "FO76 → FO4 Texture Converter"
APP_AUTHOR = "Private0516"
APP_VERSION = "1.0"

# 自定义图标：把 icon.ico / icon.png / icon.jpg 放到 EXE 同目录即可替换，
# 程序运行中替换文件会实时热更新（约 2 秒内生效）。
CUSTOM_ICON_NAMES = ("icon.ico", "icon.png", "icon.jpg", "icon.jpeg")


def resolve_icon_path() -> str | None:
    """优先返回 EXE 同目录的用户自定义图标，否则用内置图标。"""
    for name in CUSTOM_ICON_NAMES:
        p = os.path.join(app_dir(), name)
        if os.path.isfile(p):
            return p
    bundled = resource_path("assets", "icon.ico")
    return bundled if os.path.isfile(bundled) else None


def load_app_icon():
    """
    加载应用 LOGO，返回 (QPixmap | None, source_key)。

    优先级：
    1. EXE 同目录的自定义 icon.ico / icon.png / icon.jpg（用户手动放置，热重载）
    2. EXE 文件自身内嵌的图标（打包后）—— 用 ResourceHacker 等工具改 EXE
       图标后，程序内 LOGO 随之自动更新
    3. 内置 assets/icon.ico（开发模式回退）

    source_key 变化即代表图标来源被修改，用于热重载判断。
    """
    from PySide6.QtGui import QPixmap
    for name in CUSTOM_ICON_NAMES:
        p = os.path.join(app_dir(), name)
        if os.path.isfile(p):
            pm = QPixmap(p)
            if not pm.isNull():
                try:
                    mt = os.path.getmtime(p)
                except OSError:
                    mt = 0.0
                return pm, (p, mt)
    if getattr(sys, "frozen", False):
        from PySide6.QtCore import QFileInfo
        from PySide6.QtWidgets import QFileIconProvider
        exe = sys.executable
        pm = QFileIconProvider().icon(QFileInfo(exe)).pixmap(256, 256)
        if not pm.isNull():
            try:
                mt = os.path.getmtime(exe)
            except OSError:
                mt = 0.0
            return pm, (exe, mt)
    bundled = resource_path("assets", "icon.ico")
    if os.path.isfile(bundled):
        pm = QPixmap(bundled)
        if not pm.isNull():
            try:
                mt = os.path.getmtime(bundled)
            except OSError:
                mt = 0.0
            return pm, (bundled, mt)
    return None, None


def register_fonts() -> list[str]:
    """注册 assets/fonts 下的展示字体，返回成功注册的字族名列表。"""
    from PySide6.QtGui import QFontDatabase
    families: list[str] = []
    fdir = resource_path("assets", "fonts")
    if not os.path.isdir(fdir):
        return families
    for name in sorted(os.listdir(fdir)):
        if not name.lower().endswith((".otf", ".ttf")):
            continue
        fid = QFontDatabase.addApplicationFont(os.path.join(fdir, name))
        if fid >= 0:
            families.extend(QFontDatabase.applicationFontFamilies(fid))
    return families

# ---------------------------------------------------------------------------
# 转换模式预设（见需求第七节）
# ---------------------------------------------------------------------------

MODES = {
    "high": {
        "label": "高质量",
        "diffuse_fmt": "BC7_UNORM",
        "normal_fmt": "BC5_UNORM",
        "specular_fmt": "BC5_UNORM",
        "glow_fmt": "BC7_UNORM",
    },
    "balanced": {
        "label": "平衡",
        "diffuse_fmt": "BC7_UNORM",
        "normal_fmt": "BC5_UNORM",
        "specular_fmt": "BC5_UNORM",
        "glow_fmt": "BC7_UNORM",
    },
    "space": {
        "label": "节省空间",
        # Normal 永远优先 BC5；Diffuse 有 Alpha 时自动改用 BC3（见 converter）
        "diffuse_fmt": "BC1_UNORM",
        "normal_fmt": "BC5_UNORM",
        "specular_fmt": "BC5_UNORM",
        "glow_fmt": "BC1_UNORM",
    },
}


@dataclass
class Config:
    # 基础
    input_dir: str = ""
    output_dir: str = ""
    mode: str = "balanced"

    # 贴图格式（由 mode 预设填充，也可单独修改）
    diffuse_fmt: str = "BC7_UNORM"
    normal_fmt: str = "BC5_UNORM"
    specular_fmt: str = "BC5_UNORM"
    glow_fmt: str = "BC7_UNORM"
    generate_mipmap: bool = True

    # Normal
    invert_green: bool = False

    # 材质
    generate_s: bool = True
    ao_mode: bool = False            # AO → Diffuse，默认 OFF
    ao_strength: float = 0.3         # 0.0 – 1.0
    metal_compensation: bool = False
    metal_strength: float = 0.5      # 0.0 – 1.0
    glow_from_l_alpha: bool = False

    # 输出
    keep_structure: bool = True
    overwrite: bool = True
    generate_log: bool = True
    generate_report: bool = True

    # BGSM/BGEM（第二阶段，实验性）
    convert_bgsm: bool = True

    # Incremental local metal reconstruction. Profiles remain experimental.
    metal_mode: str = "off"
    metal_options: dict = field(default_factory=dict)
    job_overrides: dict = field(default_factory=dict)
    about_seen: bool = False

    # 界面语言（zh / en）
    lang: str = "zh"
    materials_input: str = ""
    output_layout: str = "game"

    def apply_mode(self) -> None:
        preset = MODES.get(self.mode, MODES["balanced"])
        self.diffuse_fmt = preset["diffuse_fmt"]
        self.normal_fmt = preset["normal_fmt"]
        self.specular_fmt = preset["specular_fmt"]
        self.glow_fmt = preset["glow_fmt"]

    # -- 持久化 -----------------------------------------------------------

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, indent=2)

    @classmethod
    def from_json(cls, text: str) -> "Config":
        from security_io import json_bounded
        data = json_bounded(text)
        if not isinstance(data, dict): raise ValueError('Configuration must be an object')
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        values = {k: v for k, v in data.items() if k in known}
        for key in ('input_dir', 'output_dir', 'materials_input'):
            if key in values and (not isinstance(values[key], str) or '\0' in values[key]):
                raise ValueError(f'Invalid directory preference: {key}')
        if values.get('output_layout', 'game') not in ('game', 'isolated'):
            raise ValueError('Unknown output layout')
        if 'generate_report' in values and not isinstance(values['generate_report'], bool):
            raise ValueError('Report preference must be true or false')
        values['metal_options'] = processing_options(values.get('metal_options'))
        values['job_overrides'] = job_options(values.get('job_overrides'))
        return cls(**values)


def app_dir() -> str:
    """EXE 所在目录（开发时为脚本目录）。"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def resource_path(*parts: str) -> str:
    """程序内部资源路径（PyInstaller 打包后位于 sys._MEIPASS）。"""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, *parts)


def config_path() -> str:
    return os.environ.get("TEXTURE_CONVERTER_CONFIG", os.path.join(app_dir(), "config.json"))


def load_config() -> Config:
    p = config_path()
    if os.path.isfile(p):
        try:
            from security_io import read_bounded, MAX_JSON_BYTES
            return Config.from_json(read_bounded(p, MAX_JSON_BYTES, 'Configuration').decode('utf-8-sig'))
        except Exception:  # noqa: BLE001 — 配置损坏时回退默认
            pass
    return Config()


def save_config(cfg: Config) -> None:
    from security_io import MAX_JSON_BYTES, assert_no_reparse, pinned_path
    temp = None
    try:
        target = assert_no_reparse(config_path())
        payload = cfg.to_json().encode('utf-8')
        if len(payload) > MAX_JSON_BYTES:
            return
        with pinned_path(target.parent):
            try:
                with tempfile.NamedTemporaryFile(dir=target.parent, prefix='.settings-', suffix='.tmp', delete=False) as stream:
                    temp = Path(stream.name)
                    stream.write(payload)
                # Replacing the name also protects the referents of existing hard links.
                os.replace(temp, target)
                temp = None
            finally:
                if temp is not None:
                    temp.unlink(missing_ok=True)
    except (OSError, ValueError):
        pass  # Keep the last complete preferences if saving fails.
