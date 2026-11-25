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
    # Choose an existing grid; independent axis maxima can turn two thin
    # crossed maps into a huge intermediate absent from the DDS memory budget.
    th, tw = max((a.shape[:2], b.shape[:2]), key=lambda hw: hw[0] * hw[1])

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


def _resample_data(pixels, shape):
    """Resample only a scalar plane or RGB channels, independently of other slots."""
    if pixels.shape[:2] == shape:
        return pixels
    height, width = shape
    return np.asarray(Image.fromarray(pixels).resize((width, height), Image.Resampling.LANCZOS))


def build_specular(l_rgba: np.ndarray, r_rgba: np.ndarray, *, cancel_event=None) -> np.ndarray:
    """Pack reflectance luminance and smoothness into the two FO4 channels."""
    if cancel_event is not None:
        from dds import _check_cancel
        _check_cancel(cancel_event)
    shape = max((l_rgba.shape[:2], r_rgba.shape[:2]), key=lambda hw: hw[0] * hw[1])
    smoothness = _resample_data(l_rgba[..., 0], shape)
    reflectance = _resample_data(r_rgba[..., :3], shape)
    result = np.empty((*shape, 4), dtype=np.uint8)
    rows = max(1, 65536 // shape[1])
    for y in range(0, shape[0], rows):
        if cancel_event is not None:
            _check_cancel(cancel_event)
        band = slice(y, y + rows)
        color = reflectance[band].astype(np.float64) / 255.
        result[band, :, 0] = to_uint8(luminance709(color))
    result[..., 1] = smoothness
    result[..., 2] = 0
    result[..., 3] = 255
    return result


def apply_ao_to_diffuse(diffuse: np.ndarray, l_rgba: np.ndarray,
                        strength: float, *, cancel_event=None) -> np.ndarray:
    """Bake _l.G onto the original diffuse grid and retain its coverage alpha."""
    if cancel_event is not None:
        from dds import _check_cancel
        _check_cancel(cancel_event)
    amount = float(strength)
    if not np.isfinite(amount) or not 0 <= amount <= 1:
        raise ValueError('AO strength must be finite and between zero and one')
    out = diffuse.copy()
    if not amount:
        return out
    ao = _resample_data(l_rgba[..., 1], diffuse.shape[:2])
    rows = max(1, 65536 // diffuse.shape[1])
    for y in range(0, diffuse.shape[0], rows):
        if cancel_event is not None:
            _check_cancel(cancel_event)
        band = slice(y, y + rows)
        factor = 1. + (ao[band].astype(np.float64) / 255. - 1.) * amount
        color = diffuse[band, :, :3].astype(np.float64) / 255.
        out[band, :, :3] = to_uint8(color * factor[..., None])
    return out


def _smooth_ramp(value, low, high):
    t = np.clip((value - low) / (high - low), 0., 1.)
    return t * t * (3. - 2. * t)


def apply_metal_compensation(diffuse: np.ndarray, r_rgba: np.ndarray,
                             strength: float, *, reflectance_format='',
                             diffuse_format='', source_diffuse=None, cancel_event=None) -> np.ndarray:
    """Restore dark metal diffuse samples using the paired FO76 reflectance.

    BC4/R8 stores scalar reflectance. An explicitly sRGB diffuse can use a
    neutral, bounded display-space target. RGB maps retain their authored tint.
    All weights taper away at already-bright paint and weak reflections.
    """
    strength = float(strength)
    if not np.isfinite(strength) or not 0 <= strength <= 1:
        raise ValueError('Metal strength must be finite and between zero and one')
    if source_diffuse is not None and source_diffuse.shape != diffuse.shape:
        raise ValueError('Source diffuse and AO-adjusted diffuse grids must match')
    out = diffuse.copy()
    if not strength:
        return out
    height, width = diffuse.shape[:2]
    scalar = reflectance_format in ('BC4_UNORM', 'R8_UNORM')
    paired_scalar = scalar and diffuse_format.endswith('_SRGB')
    if r_rgba.shape[:2] != (height, width):
        # Numeric or RGB data: unused alpha must not premultiply the samples.
        source = r_rgba[..., 0] if scalar else r_rgba[..., :3]
        reflectance = np.asarray(Image.fromarray(source).resize(
            (width, height), Image.Resampling.BILINEAR))
    else:
        reflectance = r_rgba[..., 0] if scalar else r_rgba[..., :3]

    # Keep peak float workspace proportional to a strip, including for 2K+ DDS.
    rows = max(1, 65536 // width)
    for y in range(0, height, rows):
        if cancel_event is not None:
            from dds import _check_cancel
            _check_cancel(cancel_event)
        band = slice(y, y + rows)
        color = diffuse[band, :, :3].astype(np.float32) / 255.
        r = reflectance[band].astype(np.float32) / 255.
        level = r if scalar else luminance709(r)
        # AO darkens paint as well as metal. Determine eligibility from the authored diffuse.
        peak = (color.max(axis=2) if source_diffuse is None else
                source_diffuse[band, :, :3].max(axis=2).astype(np.float32) / 255.)
        dark = 1. - _smooth_ramp(peak, 32. / 255., 74. / 255.)
        if paired_scalar:
            # Matched ordinary BGSMs include blackened metal at 50–75/255
            # scalar reflectance. A broad shoulder limits mirror-bright inputs.
            reflective = _smooth_ramp(level, 32. / 255., 63. / 255.)
            target_linear = np.minimum(r, .32)
            target = np.where(target_linear <= .0031308, 12.92 * target_linear,
                              1.055 * target_linear ** (1. / 2.4) - .055)[..., None]
        else:
            reflective = _smooth_ramp(level, .25, .75)
            target = r[..., None] if scalar else r
        weight = dark * reflective * strength * (diffuse[band, :, 3] > 0)
        color += np.maximum(0., target - color) * weight[..., None]
        out[band, :, :3] = np.rint(color * 255.).astype(np.uint8)
    return out


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
    from material_codec import read_material_bytes
    data = read_material_bytes(path)
    slots = extract_texture_paths(data)
    return BgsmTemplate(data, slots)
