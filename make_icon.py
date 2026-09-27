# -*- coding: utf-8 -*-
"""生成程序图标 app_icon.ico：中行红圆角底 + 白色 ¥（与界面头部品牌徽标一致）。

界面头部徽标为 36px 圆角方块（圆角半径 10，约 0.28），¥ 用微软雅黑加粗；
本脚本以同样比例生成多尺寸 ICO，保证标题栏/任务栏/exe 与页面观感统一。
"""
import os

from PIL import Image, ImageDraw, ImageFont

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app_icon.ico")
RED = (184, 27, 34)        # 与程序主色 COLOR_ACCENT (#b81b22) 一致
RED_DARK = (150, 20, 26)   # 底部微暗，增加立体感
WHITE = (255, 255, 255, 255)
SS = 8                      # 超采样倍数，抗锯齿

# 微软雅黑加粗：小尺寸下 ¥ 笔画更清晰
_FONT_CANDIDATES = [
    r"C:\Windows\Fonts\msyhbd.ttc",
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\arialuni.ttf",
]


def _load_font(px):
    for path in _FONT_CANDIDATES:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, px)
            except OSError:
                continue
    return ImageFont.load_default()


def make_icon(size):
    """返回 size×size 的 RGBA 图标（已带透明背景）。"""
    s = size * SS
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))

    # 圆角方形背景：圆角比例与界面徽标一致（10/36 ≈ 0.28）
    margin = int(s * 0.04)
    radius = int(s * 0.28)
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

    # 白色 ¥ 居中：字号约占方块高度 62%（与界面徽标 15/36 ≈ 0.42 相比略大，
    # 因为 ICO 在任务栏只有 16-32px，需要更饱满才可辨认）
    font = _load_font(int(s * 0.62))
    text = "¥"
    bbox = d.textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    # textbbox 原点含字形内部偏移，绘制时反向补偿实现视觉居中
    d.text((s / 2 - tw / 2 - bbox[0], s / 2 - th / 2 - bbox[1]),
           text, font=font, fill=WHITE)

    img = img.resize((size, size), Image.LANCZOS)
    return img


def main():
    sizes = [(s, s) for s in (16, 20, 24, 32, 40, 48, 64, 128, 256)]
    # ICO 仅支持单图 + sizes 参数：以最高清的一张生成各尺寸帧
    big = make_icon(256)
    big.save(OUT, sizes=sizes)
    print("saved:", OUT, "| sizes:", sizes)


if __name__ == "__main__":
    main()
