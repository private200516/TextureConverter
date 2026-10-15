# -*- coding: utf-8 -*-
"""gen_icon.py — 生成 assets/icon.ico（纯代码绘制，无外部素材）。"""
import os

from PIL import Image, ImageDraw

BASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "assets")


def main():
    size = 256
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    # 深橄榄绿圆底（辐射风格）+ 两个箭头表示 76 → 4
    d.ellipse([8, 8, size - 8, size - 8], fill=(38, 46, 32, 255),
              outline=(150, 190, 110, 255), width=8)
    # 左 76
    d.text((40, 70), "76", fill=(210, 235, 170, 255))
    # 箭头
    d.line([88, 150, 168, 150], fill=(210, 235, 170, 255), width=14)
    d.polygon([(168, 150), (140, 132), (140, 168)], fill=(210, 235, 170, 255))
    # 右 4
    d.text((188, 70), "4", fill=(210, 235, 170, 255))
    os.makedirs(BASE, exist_ok=True)
    out = os.path.join(BASE, "icon.ico")
    img.save(out, sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print("icon 生成完成:", out)


if __name__ == "__main__":
    main()
