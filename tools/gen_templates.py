# -*- coding: utf-8 -*-
"""
gen_templates.py — 生成程序内置的 FO4 BGSM/BGEM 模板（templates/ 目录）。

注意：BGSM/BGEM 转换是第二阶段实验性功能。模板采用社区文档记录的
FO4 BGSM v2 / BGEM 布局，贴图槽位使用占位路径，运行时由 material.py
按槽位顺序替换为实际贴图路径。

运行：python tools\\gen_templates.py
"""

import os
import struct

BASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "templates")

# FO4 BGSM 贴图槽位顺序（Diffuse / Normal / Glow / Specular / 其余占位）
BGSM_SLOTS = [
    "textures\\__default_d.dds",   # 0 Diffuse
    "textures\\__default_n.dds",   # 1 Normal
    "textures\\__default_g.dds",   # 2 Glow
    "textures\\__default_s.dds",   # 3 Specular
    "",                            # 4 Greyscale mask
    "",                            # 5 Envmap
    "",                            # 6 预留
    "",                            # 7 预留
    "",                            # 8 预留
]

BGEM_SLOTS = [
    "textures\\__default_d.dds",   # Base
    "",                            # Grayscale
    "",                            # Envmap
    "textures\\__default_n.dds",   # Normal
    "",                            # EnvmapMask
]


def pstr(s: str) -> bytes:
    b = s.encode("ascii") + b"\x00"
    return struct.pack("<I", len(b)) + b


def make_bgsm() -> bytes:
    out = bytearray()
    out += b"BGSM"
    out += struct.pack("<I", 2)          # version
    out += struct.pack("<BB", 1, 1)      # tileU, tileV
    out += struct.pack("<ffff", 0, 0, 1, 1)  # offsetU/V, scaleU/V
    out += struct.pack("<B", 255)        # alpha
    out += struct.pack("<B", 0)          # alphaBlendMode
    out += struct.pack("<B", 128)        # alphaTestRef
    out += struct.pack("<B", 0)          # alphaTest
    out += struct.pack("<B", 1)          # zBufferWrite
    out += struct.pack("<B", 1)          # zBufferTest
    out += struct.pack("<B", 0)          # screenSpaceReflections
    out += struct.pack("<B", 0)          # wetness SSR
    out += struct.pack("<B", 0)          # decal
    out += struct.pack("<B", 0)          # twoSided
    out += struct.pack("<B", 0)          # decalNoFade
    out += struct.pack("<B", 0)          # nonOccluder
    out += struct.pack("<B", 0)          # refraction
    out += struct.pack("<B", 0)          # refractionFalloff
    out += struct.pack("<f", 0.0)        # refractionPower
    out += struct.pack("<B", 0)          # envmap
    out += struct.pack("<f", 1.0)        # envmapMaskScale
    out += struct.pack("<B", 0)          # grayscaleToPaletteColor
    out += struct.pack("<I", len(BGSM_SLOTS))
    for s in BGSM_SLOTS:
        out += pstr(s)
    # 尾部发光等参数（默认值）
    out += struct.pack("<B", 0)          # externalEmittance
    out += struct.pack("<fff", 1.0, 1.0, 1.0)  # emittanceColor
    out += struct.pack("<f", 1.0)        # emittanceMult
    return bytes(out)


def make_bgem() -> bytes:
    out = bytearray()
    out += b"BGEM"
    out += struct.pack("<I", 2)          # version
    out += struct.pack("<BB", 1, 1)      # tileU, tileV
    out += struct.pack("<ffff", 0, 0, 1, 1)
    out += struct.pack("<B", 255)        # alpha
    out += struct.pack("<B", 0)          # alphaBlendMode
    out += struct.pack("<B", 128)        # alphaTestRef
    out += struct.pack("<B", 0)          # alphaTest
    out += struct.pack("<B", 1)          # zBufferWrite
    out += struct.pack("<B", 0)          # decal
    out += struct.pack("<B", 0)          # twoSided
    out += struct.pack("<I", len(BGEM_SLOTS))
    for s in BGEM_SLOTS:
        out += pstr(s)
    # 效果参数默认值
    out += struct.pack("<B", 0)          # envmap
    out += struct.pack("<f", 1.0)        # envmapMaskScale
    out += struct.pack("<fff", 1.0, 1.0, 1.0)  # baseColor
    out += struct.pack("<f", 1.0)        # baseColorScale
    out += struct.pack("<f", 0.0)        # falloffStartAngle
    out += struct.pack("<f", 1.0)        # falloffStopAngle
    out += struct.pack("<f", 0.0)        # falloffStartOpacity
    out += struct.pack("<f", 1.0)        # falloffStopOpacity
    out += struct.pack("<B", 0)          # lightingInfluence
    return bytes(out)


def main():
    os.makedirs(BASE, exist_ok=True)
    with open(os.path.join(BASE, "default.bgsm"), "wb") as f:
        f.write(make_bgsm())
    with open(os.path.join(BASE, "default.bgem"), "wb") as f:
        f.write(make_bgem())
    print("templates 生成完成:", os.path.abspath(BASE))


if __name__ == "__main__":
    main()
