# -*- coding: utf-8 -*-
"""生成程序图标 app_icon.ico：中行红圆角底 + 白色地球(外汇) + 环绕兑换箭头。"""
import math
import os

from PIL import Image, ImageDraw

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app_icon.ico")
RED = (184, 27, 34)        # 与程序主色 COLOR_ACCENT (#b81b22) 一致
RED_DARK = (150, 20, 26)   # 底部微暗，增加立体感
WHITE = (255, 255, 255, 255)
SS = 8                      # 超采样倍数，抗锯齿


def make_icon(size):
    """返回 size×size 的 RGBA 图标（已带透明背景）。"""
    s = size * SS
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # 圆角方形背景
    margin = int(s * 0.04)
    radius = int(s * 0.22)
    bg = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    bd = ImageDraw.Draw(bg)
    # 底部加深的一层（阴影感）
    bd.rounded_rectangle(
        [margin, margin + int(s * 0.03), s - margin, s - margin + int(s * 0.03)],
        radius=radius, fill=RED_DARK + (255,))
    bd.rounded_rectangle(
        [margin, margin, s - margin, s - margin],
        radius=radius, fill=RED + (255,))
    img = Image.alpha_composite(img, bg)
    d = ImageDraw.Draw(img)

    cx = cy = s / 2.0
    r = s * 0.27

    # 地球：外圈 + 经线 + 纬线（白色）
    lw = max(1, int(s * 0.028))
    d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=WHITE, width=lw)
    # 经线（竖直椭圆）
    d.ellipse([cx - r * 0.45, cy - r, cx + r * 0.45, cy + r],
              outline=WHITE, width=max(1, int(lw * 0.8)))
    # 纬线（两条水平弦）
    for k in (-0.5, 0.5):
        y = cy + k * r
        half = math.sqrt(max(0.0, r * r - (k * r) ** 2))
        d.line([cx - half, y, cx + half, y], fill=WHITE, width=max(1, int(lw * 0.8)))

    # 环绕兑换箭头（两个相对的弧箭头，象征外汇兑换/刷新）
    ar = r * 1.5
    aw = max(1, int(s * 0.03))
    # 上半个圆弧（从左下到右上）
    d.arc([cx - ar, cy - ar, cx + ar, cy + ar], start=200, end=340,
          fill=WHITE, width=aw)
    # 下半个圆弧（从右上到左下）
    d.arc([cx - ar, cy - ar, cx + ar, cy + ar], start=20, end=160,
          fill=WHITE, width=aw)
    # 箭头头部（两个小三角）
    _arrow_head(d, cx + ar * math.cos(math.radians(340)),
                cy - ar * math.sin(math.radians(340)), angle=340, w=aw * 2.2)
    _arrow_head(d, cx + ar * math.cos(math.radians(160)),
                cy - ar * math.sin(math.radians(160)), angle=160, w=aw * 2.2)

    # 缩小到目标尺寸
    img = img.resize((size, size), Image.LANCZOS)
    return img


def _arrow_head(d, x, y, angle, w):
    """在 (x,y) 处绘制指向 tangent 方向的小三角箭头。"""
    a = math.radians(angle)
    # 切线方向（圆弧逆时针）
    tx, ty = -math.sin(a), -math.cos(a)
    px, py = math.cos(a), -math.sin(a)  # 法线
    L = w * 1.6
    p1 = (x + tx * L, y + ty * L)
    p2 = (x - tx * L * 0.2 + px * L * 0.9, y - ty * L * 0.2 + py * L * 0.9)
    p3 = (x - tx * L * 0.2 - px * L * 0.9, y - ty * L * 0.2 - py * L * 0.9)
    d.polygon([p1, p2, p3], fill=WHITE)


def main():
    sizes = [(s, s) for s in (16, 20, 24, 32, 40, 48, 64, 128, 256)]
    # ICO 仅支持单图 + sizes 参数：以最高清的一张生成各尺寸帧
    big = make_icon(256)
    big.save(OUT, sizes=sizes)
    print("saved:", OUT, "| sizes:", sizes)


if __name__ == "__main__":
    main()
