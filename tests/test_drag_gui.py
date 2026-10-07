# -*- coding: utf-8 -*-
"""GUI 回归：拖拽跨卡换位「零 grid 重排」与换位缓动形状。

背景（实测）：一次「拖拽跨卡换位」墙钟 415ms，拆解为换位前同步 grid 重排
101ms + 动画 177ms + 收尾恢复 grid 137ms。成本全在 _layout_cards()
（grid_forget() + grid() 全表）+ update_idletasks()，约 3.4ms/卡，且旧实现
每次换位要跑两遍（setup 一次、finish 一次）——30 卡下约 206ms 纯阻塞，是
连续换位「顿挫」的直接来源。

本套件的核心不变量（回归钉）：**拖拽期间不碰 grid**。槽位矩形表推迟到首次
真正跨卡时才建立（_ensure_slots，只读当时的 grid 几何，不 place、不碰 grid），
之后每次换位纯 place 动画（_animate_slots，零 grid 重排），松手时再交还 grid
一次（_return_to_grid）。进入拖拽（_drag_lift）本身零布局工作——实测 30 卡下
由此把起拖瞬间的 ≈37ms 阻塞降到 ≈0.3ms（详见 app.py 的性能要点注释）。
另校验换位缓动改成 ease_out_quad 后的形状（首帧位移占比从约 33% 降到约 23%、
末帧不再是约 1px 的「静止帧」）。

铁律：
- 合成事件参数名 rootx= / rooty=；首条合成事件会被 Tk 吞掉，需先
  <Enter>+<Motion> 预热；验证动画/布局必须 pump 事件循环。
- 隔离 %APPDATA%（App() 会读配置并 makedirs），绝不污染真实配置。
- 非 Windows / 无显示环境显式打印 SKIP（不是假绿）。

运行：python tests/test_drag_gui.py
"""
import os
import sys
import tempfile
import time

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

# 必须在 import app / config 之前隔离 %APPDATA%（App() 会读配置并 makedirs）
os.environ["APPDATA"] = tempfile.mkdtemp(prefix="bocfx_draggui_")

from ui_utils import ease_out_quad   # noqa: E402

# ---------------------------------------------------------------- 纯函数部分
# 换位缓动形状与平台无关，任何环境都必须真实执行。


def test_ease_out_quad_shape():
    """8 步预算下：首帧位移占比 20%~26%，且没有位移 <4px 的「静止帧」。"""
    travel = 449.0
    steps = 8
    es = [ease_out_quad((i + 1) / steps) for i in range(steps)]
    disp = [e * travel for e in es]
    assert disp[-1] == travel, "末帧必须精确落目标：%r" % disp[-1]
    first_ratio = disp[0] / travel
    assert 0.20 <= first_ratio <= 0.26, \
        "首帧位移占比应在 20%~26%%，实测 %.3f" % first_ratio
    deltas = [disp[0]] + [disp[i] - disp[i - 1] for i in range(1, steps)]
    assert min(deltas) >= 4.0, \
        "存在位移 <4px 的帧（肉眼静止）：deltas=%s" % [round(d, 2) for d in deltas]


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

_APPMOD = None
_APP = None


def _load_app_module():
    """延迟导入 app（GUI 层）。失败时返回 None 并记录原因。"""
    global GUI_SKIP_REASON, _APPMOD
    if _APPMOD is not None:
        return _APPMOD
    try:
        import app as appmod
        _APPMOD = appmod
        return appmod
    except Exception as e:
        GUI_SKIP_REASON = "导入 app 失败：%s" % e
        return None


def _get_app():
    """构造（或复用）一个带卡片的 App；无显示环境返回 None。"""
    global _APP, GUI_SKIP_REASON
    if _APP is not None:
        return _APP
    appmod = _load_app_module()
    if appmod is None:
        return None
    # 阻断网络刷新（refresh 会拉起 fetcher 线程）；必须在 App() 之前打补丁，
    # 否则 __init__ 里 self.after(150, self.refresh) 会先绑定成真 refresh。
    appmod.App.refresh = lambda self: None
    try:
        _APP = appmod.App()
    except Exception as e:
        GUI_SKIP_REASON = "无法创建 App（无显示环境）：%s" % e
        return None
    return _APP


def _reset(app, appmod, n_cards=3, cols=3, size="980x680"):
    """把 App 复位到确定态：3 张卡、cols 列、无在途动画/拖拽。"""
    for attr in ("_morph_job", "_drag_scroll_job", "_relayout_job", "_poll_job"):
        job = getattr(app, attr, None)
        if job is not None:
            try:
                app.after_cancel(job)
            except Exception:
                pass
            setattr(app, attr, None)
    app._drag = None
    app._drag_slots = None
    try:
        app.cards_frame.configure(height=0)
    except Exception:
        pass
    app.geometry(size)
    app.update()
    codes = list(appmod.CODE_TO_CURRENCY)[:n_cards]
    app._selected = codes
    app._selected_curs = [appmod.CODE_TO_CURRENCY[c] for c in codes]
    app._rebuild_cards()
    app._cols = cols
    app._layout_cards()
    try:
        app.update_idletasks()
    except Exception:
        pass
    app.update()
    # 落到确定态：geometry() 会触发 80ms 列数重排去抖，若此刻到期会起一个在途
    # morph 把卡片 place 掉（非拖拽态），后续「起拖仍应在 grid」的断言会假失败。
    # 显式冲掉去抖任务与在途 morph（_morph_cleanup 会解冻并交还 grid）。
    if getattr(app, "_relayout_job", None) is not None:
        try:
            app.after_cancel(app._relayout_job)
        except Exception:
            pass
        app._relayout_job = None
    if getattr(app, "_morph_job", None) is not None:
        # 必须先 after_cancel：_morph_cleanup 只把句柄置 None 并不取消已排队的
        # step 回调（它就是设计给「step 内异常」用的），漏取消会让该 step 在下一次
        # update 时把卡片 place 掉。
        try:
            app.after_cancel(app._morph_job)
        except Exception:
            pass
        app._morph_job = None
        app._morph_cleanup()


def _pump(app, ms):
    """推进 Tk 事件循环 ms 毫秒（让 after 动画链跑完）。"""
    end = time.time() + ms / 1000.0
    while time.time() < end:
        try:
            app.update()
        except Exception:
            return
        time.sleep(0.005)


class _Ev:
    """最小事件替身：供直接调用 _on_canvas_resize 等回调用。"""

    def __init__(self, **kw):
        self.__dict__.update(kw)


def _prime(app, widget):
    """首条合成事件会被 Tk 吞掉：先 <Enter>+<Motion> 预热。"""
    widget.event_generate("<Enter>", when="now")
    widget.event_generate("<Motion>", when="now", x=1, y=1)
    app.update()


def _center_root(app, code):
    w = app.cards[code]["card"]
    return (w.winfo_rootx() + w.winfo_width() // 2,
            w.winfo_rooty() + w.winfo_height() // 2)


def _press(app, code):
    w = app.cards[code]["card"]
    _prime(app, w)
    cx, cy = _center_root(app, code)
    w.event_generate("<Button-1>", when="now", x=5, y=5, rootx=cx, rooty=cy)
    app.update()


def _motion(app, code, rootx, rooty):
    w = app.cards[code]["card"]
    w.event_generate("<B1-Motion>", when="now", x=5, y=5,
                     rootx=rootx, rooty=rooty)
    app.update()


def _release(app, code, rootx, rooty):
    w = app.cards[code]["card"]
    w.event_generate("<ButtonRelease-1>", when="now", x=5, y=5,
                     rootx=rootx, rooty=rooty)
    app.update()


def _lift(app, code):
    """按下并把被拖卡抬起（进入拖拽态），返回 (源头, 目标中心坐标)。"""
    _press(app, code)
    sx, sy = _center_root(app, code)
    # 越过 DRAG_THRESHOLD（6px）触发 _drag_lift → _drag_begin_slots
    _motion(app, code, sx + 30, sy)
    assert app._drag and app._drag.get("started"), "拖拽未能进入 started 态"


def _wrap_layout_counter(app):
    """包装 _layout_cards 计数（探针侧，不改动 app 源码）。"""
    orig = app._layout_cards
    state = {"n": 0}

    def wrapped(*a, **k):
        state["n"] += 1
        return orig(*a, **k)

    app._layout_cards = wrapped
    return state, orig


def _unwrap_layout_counter(app, orig):
    app._layout_cards = orig


# --------------------------------------------------- 用例 1 / 2 / 3（带动画档）

def test_cross_card_reorder_and_chain():
    """跨卡换位：顺序真发生、两份列表一致、当次 0 次全量 grid 重排、松手后全 grid。"""
    if GUI_SKIP_REASON:
        print("  SKIP 跨卡换位：%s" % GUI_SKIP_REASON)
        return
    appmod = _load_app_module()
    app = _get_app()
    if app is None:
        print("  SKIP 跨卡换位：%s" % GUI_SKIP_REASON)
        return
    os.environ["BOCFX_REDUCE_MOTION"] = "0"    # 强制开动画档
    _reset(app, appmod)
    codes = list(app._selected)
    src, dst = codes[0], codes[1]

    state, orig = _wrap_layout_counter(app)
    try:
        _lift(app, src)
        # 推迟方案回归钉：起拖（未跨卡）时槽位表尚未建立，卡片仍在 grid
        assert app._drag_slots is None, \
            "起拖时不应建立槽位表（应推迟到首次跨卡）"
        assert all(app.cards[c]["card"].winfo_manager() == "grid"
                   for c in app._selected), "起拖时卡片不应离开 grid"
        state["n"] = 0                       # 从「已进入拖拽」开始计一次换位
        tx, ty = _center_root(app, dst)      # dst 的槽位（未重排前）
        _motion(app, src, tx, ty)            # 越过 dst → reorder
        # 断言 2：当次换位内不得发生全量 grid 重排
        assert state["n"] == 0, \
            "换位当次发生了 %d 次 _layout_cards（应 0）" % state["n"]
        # 断言 1：换位确实发生且两份列表一致
        assert app._selected == [dst, src] + codes[2:], \
            "顺序未按预期变化：%s" % app._selected
        assert [c.code for c in app._selected_curs] == app._selected, \
            "_selected_curs 与 _selected 不一致"
        _release(app, src, tx, ty)
        _pump(app, 400)
    finally:
        _unwrap_layout_counter(app, orig)

    # 断言 3：松手后全部归还 grid、状态清理干净
    for code in app._selected:
        assert app.cards[code]["card"].winfo_manager() == "grid", \
            "%s 未归还 grid" % code
    assert app._drag_slots is None, "_drag_slots 未清理"
    assert app._morph_job is None, "_morph_job 未清理"
    assert int(app.cards_frame.cget("height")) == 0, \
        "cards_frame 残留冻结高度：%s" % app.cards_frame.cget("height")


def test_resize_during_drag():
    """拖拽中改窗口尺寸：不卡死、槽位表重算、松手后无 place 残留。"""
    if GUI_SKIP_REASON:
        print("  SKIP 拖拽中改尺寸：%s" % GUI_SKIP_REASON)
        return
    appmod = _load_app_module()
    app = _get_app()
    if app is None:
        print("  SKIP 拖拽中改尺寸：%s" % GUI_SKIP_REASON)
        return
    os.environ["BOCFX_REDUCE_MOTION"] = "0"
    _reset(app, appmod)
    n = len(app._selected)
    src = app._selected[0]
    _lift(app, src)
    # 推迟方案：首次跨卡前槽位表尚未建立，卡片仍在 grid 态（零成本）
    assert app._drag_slots is None, "起拖时不应建立槽位表（推迟到首次跨卡）"

    # 改窗口宽度 → canvas <Configure> → _drag_resync 重算并重新落位
    app.geometry("620x680")
    _pump(app, 250)
    assert app._drag_slots is not None and len(app._drag_slots) == n, \
        "改尺寸后槽位表未重算"
    # 未卡死：此刻仍可继续处理事件（update 正常返回即视为未阻塞）

    sx, sy = _center_root(app, src)
    _release(app, src, sx, sy)
    _pump(app, 400)
    for code in app._selected:
        assert app.cards[code]["card"].winfo_manager() == "grid", \
            "%s 未归还 grid（残留 place）" % code
    assert app._drag_slots is None, "松手后 _drag_slots 未清理"


def test_reduce_motion_instant_slots():
    """减少动画档：换位即时落位（位置=目标）、无 morph 链、0 次全量 grid 重排。"""
    if GUI_SKIP_REASON:
        print("  SKIP 减少动画档：%s" % GUI_SKIP_REASON)
        return
    appmod = _load_app_module()
    app = _get_app()
    if app is None:
        print("  SKIP 减少动画档：%s" % GUI_SKIP_REASON)
        return
    os.environ["BOCFX_REDUCE_MOTION"] = "1"
    try:
        _reset(app, appmod)
        codes = list(app._selected)
        src, dst = codes[0], codes[1]
        state, orig = _wrap_layout_counter(app)
        try:
            _lift(app, src)
            state["n"] = 0
            tx, ty = _center_root(app, dst)
            _motion(app, src, tx, ty)
            assert state["n"] == 0, \
                "减少动画档换位仍做了 %d 次 _layout_cards" % state["n"]
            assert app._morph_job is None, "减少动画档不应有 morph 链"
            app.update_idletasks()
            # 即时落位：每张卡的位置/尺寸 == 其新槽位矩形
            for idx, cur in enumerate(app._selected_curs):
                w = app.cards[cur.code]["card"]
                tg = app._drag_slots[idx]
                got = (w.winfo_x(), w.winfo_y(),
                       w.winfo_width(), w.winfo_height())
                assert got == tg, \
                    "%s 未瞬时落位：got=%s tg=%s" % (cur.code, got, tg)
            _release(app, src, tx, ty)
            _pump(app, 200)
            for code in app._selected:
                assert app.cards[code]["card"].winfo_manager() == "grid", \
                    "%s 未归还 grid" % code
            assert app._drag_slots is None
        finally:
            _unwrap_layout_counter(app, orig)
    finally:
        os.environ["BOCFX_REDUCE_MOTION"] = "0"


if __name__ == "__main__":
    os.environ["BOCFX_REDUCE_MOTION"] = "0"      # 默认强制开动画档
    test_ease_out_quad_shape()
    test_cross_card_reorder_and_chain()
    test_resize_during_drag()
    test_reduce_motion_instant_slots()
    if _APP is not None:
        try:
            _APP.destroy()
        except Exception:
            pass
    if GUI_SKIP_REASON:
        print("缓动形状用例通过；GUI 部分已跳过（%s）" % GUI_SKIP_REASON)
    else:
        print("拖拽 GUI 回归全部通过（零 grid 重排 + ease_out_quad 形状）")
