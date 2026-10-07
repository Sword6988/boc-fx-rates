# -*- coding: utf-8 -*-
"""界面层的纯函数工具（不依赖 tkinter / ctypes）。

单独抽出的目的：颜色插值与缓动函数是最容易出错、也最该被回归钉住的
逻辑（历史上 _mix_color 遇到颜色名抛 ValueError，导致 hover 过渡链中断、
按钮颜色「一闪一闪」），但它们本身与 GUI 无关。放在这里之后，测试可以
在无显示环境（含 Linux CI）直接导入验证，而不必 import app（那个过程会
连带拉起 tkinter 与 Windows 专有的 ctypes.windll）。
"""

__all__ = ["mix_color", "norm_color", "ease_out_cubic", "lin"]


def mix_color(c1, c2, t):
    """两个 #rrggbb 颜色按 t 线性插值（t=0 → c1，t=1 → c2）。

    t 会被夹紧到 [0, 1]，且每个通道结果夹到 0-255，越界不产生非法颜色。
    只接受 #rrggbb 字面量：颜色名请先用 norm_color 归一化。
    """
    t = min(1.0, max(0.0, t))
    a = (int(c1[1:3], 16), int(c1[3:5], 16), int(c1[5:7], 16))
    b = (int(c2[1:3], 16), int(c2[3:5], 16), int(c2[5:7], 16))
    return "#%02x%02x%02x" % tuple(
        max(0, min(255, round(x + (y - x) * t))) for x, y in zip(a, b))


def norm_color(widget, color):
    """把颜色统一成 #rrggbb（无法解析返回 None）。

    Tk 的颜色既可以是 #rrggbb，也可以是颜色名（"white"、系统色
    "SystemButtonFace" 等）；mix_color 只按 #rrggbb 解析字符串，喂进颜色
    名会抛 ValueError（int('hi', 16)）。实测后果很隐蔽：主按钮 fg="white"，
    hover 过渡在第 1 步混完 bg 后混 fg 时抛错，链就此中断——按钮永久停在
    中间色既到不了 hover 色也回不到静止色，反复划过时就是「一闪一闪 /
    颜色漂移」。这里交给 Tk 自己解析（winfo_rgb 名称与十六进制都认），
    返回 0-65535 分量，右移 8 位得 0-255。

    需要一个已存在的 widget 作为颜色解析上下文（Tk 的解析入口挂在实例上）。
    """
    if not color:
        return None
    try:
        if isinstance(color, str) and color.startswith("#"):
            return color.lower()
        r, g, b = widget.winfo_rgb(color)
        return "#%02x%02x%02x" % (r >> 8, g >> 8, b >> 8)
    except Exception:
        return None


def ease_out_cubic(t):
    """三次缓出：起步快、收尾缓（t=0→0，t=1→1）。"""
    return 1.0 - (1.0 - t) ** 3


def lin(t):
    """线性缓动：Δ 恒定、逐帧等距。

    颜色渐隐这类「等距退场」在步数很少（3~6 步）时，线性最平滑；
    ease-in-out 的中段斜率最大，3 个采样点必然产生一个巨大的中间跳变
    （实测 Δ = 0.148 / 0.704 / 0.148，先卡后窜），故渐隐类改用线性。
    """
    return t
