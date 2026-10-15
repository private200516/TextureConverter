# -*- coding: utf-8 -*-
"""
material.py — 通道级材质运算与 BGSM/BGEM 处理。

核心规则（对应需求文档）：
- _l.R = Smoothness → _s.G（FO4 Gloss）
- _l.G = AO → 可选乘入 Diffuse，默认忽略
- _l.B = SSS → 默认忽略（FO4 普通 BGSM 无一一对应参数，不破坏 Diffuse）
- _l.A = Glow → 可选生成 _g，默认关闭
- _r   = Reflectance → 灰度化（Rec.709）后写入 _s.R
- _s = (R=Reflectance, G=Smoothness, B=0, A=255)，BC5_UNORM
- 金属补偿：渐进式混合，绝不用 _r 整体覆盖 _d
"""

from __future__ import annotations

import os
import struct

import numpy as np

try:
    from PIL import Image
except ImportError:  # pragma: no cover
    Image = None

# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------

def to_float(rgba: np.ndarray) -> np.ndarray:
    return rgba.astype(np.float64) / 255.0


def to_uint8(f: np.ndarray) -> np.ndarray:
    return np.clip(np.round(f * 255.0), 0, 255).astype(np.uint8)


def match_size(a: np.ndarray, b: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """把两张图缩放到相同尺寸（取较大者，避免丢细节）。"""
    if a.shape[:2] == b.shape[:2]:
        return a, b
    th = max(a.shape[0], b.shape[0])
    tw = max(a.shape[1], b.shape[1])

    def resize(x):
        if x.shape[0] == th and x.shape[1] == tw:
            return x
        return np.asarray(
            Image.fromarray(x, "RGBA").resize((tw, th), Image.LANCZOS),
            dtype=np.uint8,
        ).copy()

    return resize(a), resize(b)


def luminance709(rgb: np.ndarray) -> np.ndarray:
    """Rec.709 灰度：Y = 0.2126 R + 0.7152 G + 0.0722 B。输入输出均为 0-1 float。"""
    return 0.2126 * rgb[..., 0] + 0.7152 * rgb[..., 1] + 0.0722 * rgb[..., 2]


def has_alpha(rgba: np.ndarray, threshold: int = 250) -> bool:
    return bool((rgba[..., 3] < threshold).any())


# ---------------------------------------------------------------------------
# 通道运算
# ---------------------------------------------------------------------------

def invert_green_channel(rgba: np.ndarray) -> np.ndarray:
    out = rgba.copy()
    out[..., 1] = 255 - out[..., 1]
    return out


def build_specular(l_rgba: np.ndarray, r_rgba: np.ndarray) -> np.ndarray:
    """
    _l + _r → _s
      _s.R = _r 的灰度 Reflectance（Rec.709）
      _s.G = _l.R Smoothness
      _s.B = 0
      _s.A = 255
    """
    l_rgba, r_rgba = match_size(l_rgba, r_rgba)
    # Identical Rec.709 operation order/float64 rounding, without expanding
    # four unused lighting channels or a full float64 RGBA output.
    r = r_rgba[..., :3].astype(np.float64) / 255.0
    luminance = luminance709(r)
    s = np.empty_like(l_rgba)
    s[..., 0] = np.clip(np.round(luminance * 255.0), 0, 255).astype(np.uint8)
    s[..., 1] = l_rgba[..., 0]
    s[..., 2] = 0
    s[..., 3] = 255
    return s


def apply_ao_to_diffuse(diffuse: np.ndarray, l_rgba: np.ndarray,
                        strength: float) -> np.ndarray:
    """AO → Diffuse：Diffuse = Diffuse × lerp(1, AO, strength)。AO 取 _l.G。"""
    diffuse, l_rgba = match_size(diffuse, l_rgba)
    df = to_float(diffuse)
    ao = to_float(l_rgba)[..., 1]
    factor = 1.0 + (ao - 1.0) * float(np.clip(strength, 0.0, 1.0))
    out = df.copy()
    out[..., 0] *= factor
    out[..., 1] *= factor
    out[..., 2] *= factor
    return to_uint8(out)


def apply_metal_compensation(diffuse: np.ndarray, r_rgba: np.ndarray,
                             strength: float) -> np.ndarray:
    """
    自动金属补偿（渐进式混合，绝不用 _r 整体覆盖 _d）。

    疑似金属区域判定：Reflectance 亮度（Rec.709）越高的区域越可能是金属。
    mask = smoothstep(0.25, 0.75, reflectance_luma)
    输出 = lerp(diffuse, max(diffuse, reflectance_color), mask × strength)
    """
    diffuse, r_rgba = match_size(diffuse, r_rgba)
    df = to_float(diffuse)
    rf = to_float(r_rgba)[..., :3]
    luma = luminance709(rf)
    t = np.clip((luma - 0.25) / 0.5, 0.0, 1.0)
    mask = t * t * (3.0 - 2.0 * t) * float(np.clip(strength, 0.0, 1.0))
    target = np.maximum(df[..., :3], rf)
    out = df.copy()
    for c in range(3):
        out[..., c] = df[..., c] * (1.0 - mask) + target[..., c] * mask
    return to_uint8(out)


def build_glow_from_l_alpha(l_rgba: np.ndarray) -> np.ndarray:
    """从 _l.Alpha 生成 Glow（灰度 → RGB，A=255）。"""
    a = l_rgba[..., 3]
    return np.stack([a, a, a, np.full_like(a, 255)], axis=-1)


# ---------------------------------------------------------------------------
# BGSM / BGEM（第二阶段，实验性）
# ---------------------------------------------------------------------------

_TEXTURE_EXTS = (b".dds", b".DDS")


def extract_texture_paths(data: bytes) -> list[str]:
    """
    从 FO76 BGSM/BGEM 二进制中提取贴图路径。
    FO76/FO4 材质文件中贴图路径是以长度前缀（uint16/uint32）或
    NUL 结尾存储的 ASCII 字符串；这里做稳健的保守提取：
    抓取所有形如 'textures/....dds' 的可打印字符串。
    """
    paths: list[str] = []
    i = 0
    n = len(data)
    while i < n:
        # 快速定位 "textures" / "Textures"
        j = data.find(b"extures", i)
        if j < 0:
            break
        start = j - 1
        if start < 0 or data[start:start + 1] not in (b"t", b"T"):
            i = j + 7
            continue
        end = start
        while end < n and 32 <= data[end] < 127:
            end += 1
        s = data[start:end].decode("ascii", errors="ignore")
        if s.lower().endswith(".dds") and len(s) > 12:
            paths.append(s.replace("/", "\\"))
        i = max(end, j + 7)
    return paths


def detect_material_kind(data: bytes) -> str | None:
    if data[:4] == b"BGSM":
        return "bgsm"
    if data[:4] == b"BGEM":
        return "bgem"
    return None


class BgsmTemplate:
    """
    FO4 BGSM/BGEM 模板：程序内置二进制模板（templates/default.bgsm 等），
    贴图槽位以占位路径表示，生成时替换。
    占位路径按固定顺序出现：
      Diffuse, Normal, GlowSpec(未用), Glow, Specular, ...（视模板而定）
    """

    def __init__(self, data: bytes, slots: list[str]):
        self.data = data
        self.slots = slots  # 模板中的占位路径，按出现顺序

    def render(self, replacements: dict[str, str]) -> bytes:
        """
        replacements: 占位路径 -> 新路径（均为 ASCII，反斜杠分隔）。
        模板字符串为 NUL 结尾存储，替换时保持 NUL 结尾。
        """
        out = self.data
        for slot in self.slots:
            new = replacements.get(slot)
            if new is None:
                continue
            old = slot.encode("ascii") + b"\x00"
            new_b = new.encode("ascii", errors="replace") + b"\x00"
            # 模板每条路径前有 uint32 长度（含 NUL）
            old_len = struct.pack("<I", len(old))
            idx = out.find(old_len + old)
            if idx < 0:
                idx = out.find(old)
                if idx < 0:
                    continue
                out = out[:idx] + new_b + out[idx + len(old):]
            else:
                new_len = struct.pack("<I", len(new_b))
                out = out[:idx] + new_len + new_b + out[idx + 4 + len(old):]
        return out


def load_template(path: str) -> BgsmTemplate | None:
    if not os.path.isfile(path):
        return None
    with open(path, "rb") as f:
        data = f.read()
    slots = extract_texture_paths(data)
    return BgsmTemplate(data, slots)
