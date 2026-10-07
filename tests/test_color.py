# -*- coding: utf-8 -*-
"""离线测试：颜色归一化与 hover 过渡（颜色名必须能被插值）。

背景（真实 bug）：mix_color 只认 #rrggbb；主按钮 fg="white" 是颜色名，
喂进去会抛 ValueError（int('hi', 16)），导致 _bind_hover 的过渡链在第 1
步就中断——按钮永远停在中间色，既到不了 hover 色也回不到静止色，鼠标反复
划过表现为「一闪一闪 / 颜色漂移」。这里把它钉成回归用例。

分层（重要）：纯函数部分（颜色插值/缓动）直接从 `ui_utils` 导入，**不导入
tkinter**，因此在 Linux / CI（ubuntu-latest）上也能真实运行；GUI 部分才
延迟导入 `app`（它会拉起 tkinter 与 Windows 专有的 ctypes.windll），并在
非 Windows 或无显示环境时显式打印 SKIP 而不是假装通过。

运行：python tests/test_color.py
"""
import os
import sys
import time

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

from ui_utils import ease_out_cubic, lin, mix_color, norm_color  # noqa: E402

# ---------------------------------------------------------------- 纯函数部分
# 这些用例在任何平台都必须真实执行（CI 上就是靠它们守住颜色插值逻辑）。


def test_mix_color_basic():
    """端点与中点插值。"""
    assert mix_color("#000000", "#ffffff", 0.0) == "#000000"
    assert mix_color("#000000", "#ffffff", 1.0) == "#ffffff"
    assert mix_color("#000000", "#ffffff", 0.5) == "#808080"
    # 越界 t 必须被夹紧，不能溢出成非法颜色
    assert mix_color("#000000", "#ffffff", -1.0) == "#000000"
    assert mix_color("#000000", "#ffffff", 2.0) == "#ffffff"


def test_mix_color_channel_clamp():
    """每个通道都必须夹在 0-255，且极值 t 不能产出越界颜色。"""
    # 精确可算的中点：a=(10,20,30) b=(20,30,40) → (15,25,35)
    assert mix_color("#0a141e", "#141e28", 0.5) == "#0f1923"
    # 接近但未到 1：单通道 255*0.999 = 254.745 → 四舍五入回 255
    assert mix_color("#000000", "#ffffff", 0.999) == "#ffffff"
    # 极值 t 必须夹紧（越界会产出 >255 的非法十六进制）
    assert mix_color("#000000", "#ffffff", -5.0) == "#000000"
    assert mix_color("#000000", "#ffffff", 5.0) == "#ffffff"
    # 反向插值同理
    assert mix_color("#ffffff", "#000000", -1.0) == "#ffffff"
    assert mix_color("#ffffff", "#000000", 2.0) == "#000000"


def test_easing_endpoints():
    """缓动函数端点必须精确：末帧落真实终态（0→0、1→1）。"""
    for fn in (ease_out_cubic, lin):
        assert fn(0.0) == 0.0
        assert fn(1.0) == 1.0
    # ease-out：首帧即有可见进度（不做 ease-in-out 的空转首帧）
    assert ease_out_cubic(1.0 / 3) > 0.5
    # 线性：等距采样必须等距
    assert abs((lin(0.4) - lin(0.2)) - (lin(0.6) - lin(0.4))) < 1e-12


def test_norm_color_pure_paths():
    """不依赖 Tk 的分支：空值与已归一化的十六进制。"""
    assert norm_color(None, "") is None
    assert norm_color(None, None) is None
    assert norm_color(None, "#B81B22") == "#b81b22"
    # 颜色名需要 Tk 解析上下文；没有 widget 时必须返回 None 而不是抛异常
    assert norm_color(None, "white") is None


# ---------------------------------------------------------------- GUI 部分
# 需要 Windows + 显示环境；不满足时显式 SKIP（不计为通过）。

GUI_SKIP_REASON = None

if sys.platform != "win32":
    GUI_SKIP_REASON = "非 Windows 平台（Tk/ctypes.windll 依赖不可用）"
else:
    try:
        import tkinter as _tk          # noqa: F401
    except Exception as e:             # pragma: no cover
        GUI_SKIP_REASON = "tkinter 不可用：%s" % e


def _load_gui_module():
    """延迟导入 app（GUI 层）。失败时返回 None 并记录原因。"""
    global GUI_SKIP_REASON
    try:
        import app as appmod
        return appmod
    except Exception as e:
        GUI_SKIP_REASON = "导入 app 失败：%s" % e
        return None


def _tk_root(appmod):
    """返回 (root, ok)；无显示环境时 ok=False。"""
    try:
        root = appmod.tk.Tk()
        # 必须保持映射：未映射的 toplevel 上 <Enter>/<Leave> 不会投递
        # （实测：withdraw() 后 hover 完全无反应，bg 一动不动）。
        # 缩到 1×1 并挪到左上角，避免测试时弹窗干扰。
        root.geometry("200x100+0+0")
        root.update()
        return root, True
    except Exception as e:
        global GUI_SKIP_REASON
        GUI_SKIP_REASON = "无法创建 Tk 窗口（无显示环境）：%s" % e
        return None, False


def test_norm_color_with_widget():
    """颜色名 / 十六进制 / 非法值都要能归一化（需要 Tk 上下文）。"""
    if GUI_SKIP_REASON:
        print("  SKIP _norm_color 的 Tk 部分：%s" % GUI_SKIP_REASON)
        return
    appmod = _load_gui_module()
    if appmod is None:
        print("  SKIP _norm_color 的 Tk 部分：%s" % GUI_SKIP_REASON)
        return
    root, ok = _tk_root(appmod)
    if not ok:
        print("  SKIP _norm_color 的 Tk 部分：%s" % GUI_SKIP_REASON)
        return
    try:
        probe = appmod.tk.Frame(root)
        nc = appmod._norm_color
        assert nc(probe, "#B81B22") == "#b81b22"          # 十六进制转小写
        assert nc(probe, "#b81b22") == "#b81b22"
        assert nc(probe, "white") == "#ffffff"            # 颜色名 → 十六进制
        assert nc(probe, "black") == "#000000"
        assert nc(probe, "") is None                      # 空值不炸
        assert nc(probe, None) is None
        # 非法值：返回 None（由调用方跳过该通道），不得抛异常中断过渡链
        assert nc(probe, "not-a-color") is None
    finally:
        try:
            root.destroy()
        except Exception:
            pass


def test_hover_reaches_target_with_named_fg():
    """回归：fg 为颜色名时，hover 过渡必须走完整链并精确落终色。"""
    if GUI_SKIP_REASON:
        print("  SKIP hover 过渡回归：%s" % GUI_SKIP_REASON)
        return
    appmod = _load_gui_module()
    if appmod is None:
        print("  SKIP hover 过渡回归：%s" % GUI_SKIP_REASON)
        return
    root, ok = _tk_root(appmod)
    if not ok:
        print("  SKIP hover 过渡回归：%s" % GUI_SKIP_REASON)
        return
    try:
        probe = appmod.tk.Frame(root)
        # 必须 pack：容器未映射 → 按钮不可视 → <Enter>/<Leave> 不投递
        probe.pack()
        btn = appmod.tk.Button(probe, text="x", bg="#b81b22", fg="white",
                               activebackground="#991419",
                               activeforeground="white")
        btn.pack()
        root.update()
        appmod._bind_hover(btn, ("#b81b22", "white"), ("#991419", "white"))

        btn.event_generate("<Enter>", x=3, y=3)
        _pump(root, 400)
        # 修复前这里会是中间色 #a2161c（链在第 1 步被 ValueError 打断）
        assert btn.cget("bg") == "#991419", \
            "hover 未到终色: %s" % btn.cget("bg")

        btn.event_generate("<Leave>", x=3, y=3)
        _pump(root, 400)
        assert btn.cget("bg") == "#b81b22", \
            "离开未回静止色: %s" % btn.cget("bg")
    finally:
        try:
            root.destroy()
        except Exception:
            pass


def _pump(root, ms):
    """推进 Tk 事件循环 ms 毫秒（让 after 链跑完）。"""
    end = time.time() + ms / 1000.0
    while time.time() < end:
        root.update()
        time.sleep(0.008)


if __name__ == "__main__":
    os.environ.setdefault("BOCFX_REDUCE_MOTION", "0")   # 强制开动画档
    test_mix_color_basic()
    test_mix_color_channel_clamp()
    test_easing_endpoints()
    test_norm_color_pure_paths()
    test_norm_color_with_widget()
    test_hover_reaches_target_with_named_fg()
    if GUI_SKIP_REASON:
        print("纯函数部分通过；GUI 部分已跳过（%s）" % GUI_SKIP_REASON)
    else:
        print("全部颜色/hover 逻辑离线测试通过")
