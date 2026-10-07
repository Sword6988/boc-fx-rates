# -*- coding: utf-8 -*-
"""中行汇率换算（Windows 桌面程序）— 界面层。

抓取/解析逻辑见 fetcher.py，程序入口为 boc_fx_rates.py。

界面设计约定：
- 配色集中在 PALETTE 常量区，全部界面仅引用这些常量；
- 字体/字号集中在 FONTS 常量区，形成「标题 > 卡片题 > 数值 > 说明/状态」层级；
- 内容区（标题/卡片/状态栏）限最大宽度并水平居中，窗口拉大时布局合理延展；
- 所有可点击元素统一 hand2 指针、hover 反馈与 takefocus=0（去除虚线焦点框）。
"""

import ctypes
import ctypes.wintypes
import functools
import logging
import os
import queue
import sys
import tempfile
import threading
import tkinter as tk
import traceback
from datetime import datetime

import currencies
import fetcher
from config import (
    APP_TITLE,
    _clamp_geometry,
    _config_dir,
    _config_path,
    _load_config,
    _save_config,
    _virtual_screen,
)
from currencies import ALL_CURRENCIES, BASE_CNY, CODE_TO_CURRENCY
from fetcher import (
    _brief,
    _fmt_time,
    fetch_all,
    fmt,
)
from tray import WM_APP_TRAY, WM_LBUTTONUP, TrayController
# 纯函数工具（颜色插值/缓动）放在 ui_utils，便于在无显示环境（含 Linux CI）
# 直接导入测试；此处按原名引入，其余代码无需感知这次拆分。
from ui_utils import ease_out_cubic as _ease_out_cubic
from ui_utils import lin as _lin
from ui_utils import mix_color as _mix_color
from ui_utils import norm_color as _norm_color

# ----------------------------- 配置 -----------------------------

FONT_FAMILY = "Microsoft YaHei UI"
FONT_MONO = "Consolas"

# ------------------------- 调色板（浅色 · 现代扁平） -------------------------
# 设计语言：冷灰中性底 + 纯白卡片 + 柔和描边 + 中行红点缀；
# 来源标签用「浅底彩色胶囊（chip）」承载，按钮分主（填充红）/次（幽灵描边）两级。

COLOR_BG = "#f6f7f9"           # 窗口背景（冷灰）
COLOR_CARD = "#ffffff"         # 卡片背景
COLOR_BORDER = "#e6e8ee"       # 卡片描边
COLOR_DIVIDER = "#f1f3f6"      # 卡片内分隔线
COLOR_TEXT = "#1a1d24"         # 主文字
COLOR_SUB = "#878e9c"          # 次级文字
COLOR_ACCENT = "#b81b22"       # 中行红（主强调：数值/符号/主按钮）
COLOR_ACCENT_DARK = "#991419"  # 中行红（hover/按下加深）
COLOR_TAG_BOC_BG = "#fbeeec"   # 「中行牌价」胶囊底（红的浅色调）
COLOR_TAG_FALLBACK = "#9a6b1f" # 「备用源」胶囊文字
COLOR_TAG_FALLBACK_BG = "#f9f2e2"  # 「备用源」胶囊底（琥珀浅调）
COLOR_TAG_STALE_FG = "#6f7684" # 「上次数据」胶囊文字
COLOR_TAG_STALE_BG = "#f1f3f6" # 「上次数据」胶囊底（中性灰）
COLOR_BTN = "#ffffff"          # 次级（幽灵）按钮底色
COLOR_BTN_ACTIVE = "#f0f2f6"   # 次级按钮 hover
COLOR_BTN_TEXT = "#565d6b"     # 次级按钮文字
COLOR_BTN_TEXT_ACTIVE = "#1a1d24"
# 换算方向切换按钮的「激活态」配色（切到反向时亮起，正向为普通幽灵按钮）。
# 底色沿用「中行牌价」胶囊的淡红，与全站红系强调同源；描边同步转淡红，
# 使激活态在白色卡片区里一眼可辨而不刺眼。
COLOR_DIR_ON_BG = COLOR_TAG_BOC_BG        # 激活态底色（淡红）
COLOR_DIR_ON_HOVER = "#f6ddd9"            # 激活态 hover（加深一档）
COLOR_DIR_ON_BORDER = "#eebfb9"           # 激活态描边（淡红）
COLOR_DIR_ON_TEXT = COLOR_ACCENT          # 激活态文字（中行红）
COLOR_WARN = "#b45309"         # 警告（琥珀色，不刺眼）
COLOR_ERR = "#bb3a3a"          # 错误（柔和红，非纯红）
COLOR_BUSY = "#3a6ea5"         # 进行中（中性蓝，与错误红区分，避免误读为出错）
COLOR_OK = "#1e7f3c"           # 成功反馈（复制成功）
COLOR_OK_SOFT = "#e8f7ee"      # 成功反馈按钮底色（淡绿）
COLOR_REFRESH_DISABLED_BG = "#e4e6ea"   # 刷新按钮禁用底色
COLOR_REFRESH_DISABLED_FG = "#a7adb8"   # 刷新按钮禁用文字
# 对话框专用（与主界面同源：主操作沿用中行红，次操作沿用幽灵按钮；
# 危险操作独立配色，与「错误」状态红同色系但更深，用于「直接退出」）
COLOR_DANGER_TEXT = "#8f2b2b"  # 危险按钮文字（hover 态加深，常态为柔和红）
COLOR_DANGER_ACTIVE = "#fbf0ef"  # 危险按钮 hover 底色（极淡红，克制不刺眼）
COLOR_OVERLAY = "#1a1d24"      # 模态遮罩层底色（深色，靠窗口 alpha 呈现半透明）

# 状态圆点/状态文字配色（按 _status_fg_state 键取用，圆点与文字共用同一配色表）
_STATUS_DOT = {
    "sub": COLOR_SUB,
    "warn": COLOR_WARN,
    "err": COLOR_ERR,
    "ok": COLOR_OK,
    "busy": COLOR_BUSY,
}

# --------------------- 字体层级（统一引用） ---------------------

F_TITLE = (FONT_FAMILY, 17, "bold")    # 窗口主标题
F_SUBTITLE = (FONT_FAMILY, 9)          # 主标题下副说明
F_CARD_TITLE = (FONT_FAMILY, 12, "bold")  # 卡片币种名
F_CARD_SYMBOL = (FONT_FAMILY, 13, "bold") # 卡片货币符号
F_TAG = (FONT_FAMILY, 9)               # 卡片来源胶囊
F_VALUE = (FONT_MONO, 22, "bold")      # 主数值（等宽，防刷新跳动）
F_UNIT = (FONT_FAMILY, 11)             # 「1 外币 =」「人民币」单位
F_STATUS = (FONT_FAMILY, 9)            # 状态行
F_BTN_MAIN = (FONT_FAMILY, 10, "bold") # 主按钮（刷新）
F_BTN_SMALL = (FONT_FAMILY, 9)         # 次级按钮（复制/币种菜单）
# 对话框字级：正文比主界面次级文字大一级（独立阅读场景），
# 操作项标题用卡片题同级、选项说明回到次级档，形成「题 > 说明 > 脚注」三层
F_DLG_BODY = (FONT_FAMILY, 10)         # 对话框引导语 / 选项标题
F_DLG_HINT = (FONT_FAMILY, 9)          # 选项行说明 / 底部脚注
F_DLG_BTN = (FONT_FAMILY, 10, "bold")  # 对话框主按钮（与主界面主按钮同级）
F_DLG_TITLE = (FONT_FAMILY, 12, "bold")  # 对话框标题（Modal 标题区）

# 响应式多列卡片布局参数：
# 卡片用 grid 排布，列数由可用宽度与卡片实测自然宽度共同决定（1~4 列），
# 列宽均分。内容区下限保证窄窗口可用；实际上限按最宽卡片的自然宽度
# 联动放大（_content_max_w），并给每列留出 CARD_SLACK 余量——否则上限
# 恰好卡在列数阈值下方几像素时（扣除滚动条/内边距后），超宽窗口下
# 满列数永远无法触发，卡片会被过度拉伸。列数同时钳制到实际卡片数，
# 避免币种较少时出现空列分宽。
CARD_IDEAL_W = 350     # 列数判定的兜底单位宽（无卡片可测时使用）
CARD_GAP = 12          # 卡片间距
MAX_COLS = 4           # 最大列数（超宽窗口下 4 列铺满，减少两侧留白）
CARD_SLACK = 60        # 满列时每列允许吸收的额外宽度（列数判定余量）
DRAG_THRESHOLD = 6     # 进入卡片拖拽所需的最小移动像素（防误触、保双击）
MAX_CONTENT_W = MAX_COLS * CARD_IDEAL_W + (MAX_COLS - 1) * CARD_GAP + 36

# 换算方向：正向 = 1 外币 = X 人民币（中行牌价原始方向）；
# 反向 = 1 人民币 = X 外币（1 / 汇率，仅改展示方向，不重新抓取）。
# 按钮文案 = 「等式左侧货币 ⇆ 等式右侧货币」，与卡片头部展示的基准货币
# 一致（正向卡头是外币、反向是人民币），⇆ 提示「可点击对调」。箭头两侧
# 各留一个空格，避免符号与汉字黏连（⇆ 与 ⇄ 同属 Arrows 区块，经字体链接
# 回退正常渲染；勿改用 ↔，9px 下不可辨）。两种文案只是货币顺序互换，
# 宽度必然相等，切换时按钮尺寸不变，同行的标签不会被推动。
DIR_TEXT_FWD = "外币 ⇆ 人民币"
DIR_TEXT_REV = "人民币 ⇆ 外币"

# --------------------- 动画令牌（时长 + 缓动，统一取用） ---------------------
# 设计约定：
# - 所有分步动画都由 after() 的 tick 驱动（Tk 无 vsync、无真实帧率概念，
#   因此不以「fps/60fps」表述，只保证每个 tick ≤ 单步间隔、界面线程不阻塞）；
# - 四条动画链共用同一套缓动函数族（见 _ease_out_cubic；颜色渐隐类另用
#   线性 _lin），不允许再散落内联缓动公式；
# - 采样点统一为 t = (i+1)/steps（i ∈ [0, steps-1]），末次即 t=1.0 →
#   e 精确等于 1.0，终态落在真实目标，且首帧无「t=0 空转」帧（首帧即有
#   明显进度，跟手）；旧写法 t=i/steps 会让首帧 e=0 空转一整个单步间隔；
# - 节奏分主次：交互反馈（hover / 复制渐隐）最快，窗口淡入居中，
#   列数重排最慢（重排是「结构性」变化，慢一点更从容）；
# - 每条链时长 ≈ 步数 × 单步间隔（下方注释逐一标注）。
#
# 单步间隔下限：Tk after 分辨率约 10~16ms，低于该值 tick 会被合并/饥饿，
# 反而进入「假流畅」且加剧界面线程竞争（见 RELAYOUT_STEPS 的 12ms 下限）。


# 缓动函数 _ease_out_cubic / _lin 已移至 ui_utils.py（纯函数、无 tkinter
# 依赖），文件头部 import 后仍以原名使用。


# 复制反馈节奏：瞬时上色 → 满色停留 → 六步渐隐（停留 850ms + 退场 210ms，
# 停留比旧版 1200ms 短、退场有过渡，反馈更跟手且不生硬）
FLASH_HOLD_MS = 850    # 满色停留时长
FLASH_FADE_STEPS = 6   # 渐隐步数（6 步使 Δ 恒为 1/6 ≈ 0.167，逐帧均匀）
FLASH_FADE_MS = 35     # 渐隐单步间隔；总时长 = 6 × 35 = 210ms（区间 180~220）
FLASH_FADE_EASE = _lin   # 渐隐缓动（线性：Δ 均匀，等距退场最平滑）

# 「减少动画」系统开关查询缓存（避免每帧调 WinAPI）
_REDUCED_MOTION_CACHE = None


@functools.lru_cache(maxsize=1)
def _reduced_motion_sys():
    """读取 Windows 无障碍设置「在 Windows 中显示动画」是否关闭。

    SPI_GETCLIENTAREAANIMATION(0x1042) 返回 pvParam 处 BOOL：非 0 = 动画开启，
    0 = 动画关闭（减少动画）。ctypes 必须显式设置 argtypes/restype，且 pvParam
    需按 c_void_p 传指针（本项目历史上踩过 c_int 截断 x64 指针的坑）。
    任何异常静默回退为「不减少动画」（False = 正常播放动画）。
    """
    try:
        SPI_GETCLIENTAREAANIMATION = 0x1042
        user32 = ctypes.windll.user32
        system_parameters_info = user32.SystemParametersInfoW
        system_parameters_info.argtypes = [
            ctypes.c_uint,           # uiAction
            ctypes.c_uint,           # uiParam
            ctypes.c_void_p,         # pvParam（指针宽度，不可用 c_int）
            ctypes.c_uint,           # fWinIni
        ]
        system_parameters_info.restype = ctypes.wintypes.BOOL
        enabled = ctypes.wintypes.BOOL()
        ok = system_parameters_info(
            SPI_GETCLIENTAREAANIMATION, 0, ctypes.byref(enabled), 0)
        if not ok:
            return False   # 调用失败：按「不减少动画」处理
        return not bool(enabled.value)   # BOOL 为 0 → 动画关闭 → True
    except Exception:
        return False


def _reduced_motion():
    """是否应「减少动画」（无障碍档位）。

    优先级：环境变量 BOCFX_REDUCE_MOTION=1（CI / 无 GUI 环境可强制）
    > 系统无障碍设置。结果缓存，避免每帧查询系统。
    该档位为 True 时，所有分步动画退化为「瞬时终态」——直接落到最终
    几何/颜色/alpha，功能与视觉终态完全不变，只是没有过渡过程。
    """
    env = os.environ.get("BOCFX_REDUCE_MOTION", "").strip().lower()
    if env in ("1", "true", "yes", "on"):
        return True
    if env in ("0", "false", "no", "off"):
        return False
    global _REDUCED_MOTION_CACHE
    if _REDUCED_MOTION_CACHE is None:
        _REDUCED_MOTION_CACHE = _reduced_motion_sys()
    return _REDUCED_MOTION_CACHE


def _set_clipboard_win(text):
    """用 Windows 原生 API 写入剪贴板（UTF-16LE），数据立即生效。

    tkinter 的 clipboard_append 是延迟渲染（数据存于 Tk 进程内，由查看器
    请求时才提供），在 Windows 上与剪贴板查看器/Win+V 兼容性差，常读到空。
    原生 API 直接物化到系统剪贴板，任何程序立即可读。失败返回 False。
    """
    CF_UNICODETEXT = 13
    GMEM_MOVEABLE = 0x0002
    GMEM_ZEROINIT = 0x0040
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    # x64 正确性：HGLOBAL/HANDLE/LPVOID 均为指针宽度（64 位），ctypes 默认把
    # 返回值按 32 位 c_int 处理会截断句柄——尤其是 GlobalLock 返回的内存指针，
    # 截断后 memmove 会写到错误地址，导致复制失败甚至进程崩溃。
    # 语义核对：restype=c_void_p 后 API 失败返回 None，`if not h_mem`、
    # `if not ptr`、`if not SetClipboardData(...)` 的判空逻辑保持不变。
    kernel32.GlobalAlloc.argtypes = [ctypes.c_uint, ctypes.c_size_t]
    kernel32.GlobalAlloc.restype = ctypes.c_void_p
    kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
    kernel32.GlobalFree.argtypes = [ctypes.c_void_p]
    user32.OpenClipboard.argtypes = [ctypes.wintypes.HWND]   # 传 NULL = 关联当前线程
    user32.SetClipboardData.argtypes = [ctypes.c_uint, ctypes.wintypes.HANDLE]
    user32.SetClipboardData.restype = ctypes.wintypes.HANDLE
    data = text.encode("utf-16-le") + b"\x00\x00"   # 含终止 NUL
    try:
        if not user32.OpenClipboard(None):
            return False
        try:
            user32.EmptyClipboard()
            h_mem = kernel32.GlobalAlloc(
                GMEM_MOVEABLE | GMEM_ZEROINIT, len(data))
            if not h_mem:
                return False
            ptr = kernel32.GlobalLock(h_mem)
            if not ptr:
                kernel32.GlobalFree(h_mem)
                return False
            ctypes.memmove(ptr, data, len(data))
            kernel32.GlobalUnlock(h_mem)
            if not user32.SetClipboardData(CF_UNICODETEXT, h_mem):
                # 失败时所有权未移交，需自行释放
                kernel32.GlobalFree(h_mem)
                return False
            return True   # 成功后所有权归系统，不得再 GlobalFree
        finally:
            user32.CloseClipboard()
    except Exception:
        return False


def _resource_path(rel):
    """获取资源绝对路径：兼容源码运行与 PyInstaller 冻结。"""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, rel)


def _log_crash(context, exc):
    """未捕获异常兜底：仅故障发生时追加写 crash.log，不引入常驻日志。

    背景：--windowed 打包的 exe 没有 stderr，未开 --debug 时 logging
    也没有 handler，worker 线程 / Tk 回调里的未预期异常会完全不可见，
    用户报「打开没反应」时无从诊断。此函数保证异常留痕：写入
    %APPDATA%\\<应用名>\\crash.log（目录不可写等失败时静默跳过）。
    超 1MB 时轮转保留一代（crash.log → crash.log.1），避免无限增长。
    """
    try:
        path = os.path.join(_config_dir(), "crash.log")
        try:
            if os.path.getsize(path) > 1 << 20:
                os.replace(path, path + ".1")
        except OSError:
            pass
        with open(path, "a", encoding="utf-8") as f:
            f.write("%s [%s]\n%s\n" % (
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"), context,
                "".join(traceback.format_exception(
                    type(exc), exc, exc.__traceback__)).rstrip()))
    except Exception:
        pass


def _rounded_rect(cv, x1, y1, x2, y2, r, **kw):
    """在 Canvas 上画圆角矩形（polygon smooth 拟合），供纯装饰元素使用。"""
    pts = [x1+r, y1, x2-r, y1, x2, y1, x2, y1+r, x2, y2-r, x2, y2,
           x2-r, y2, x1+r, y2, x1, y2, x1, y2-r, x1, y1+r, x1, y1]
    return cv.create_polygon(pts, smooth=True, **kw)


def _round_window_corners(win):
    """让 Toplevel 窗口启用 Win11 系统圆角 + DWM 投影（旧系统静默跳过）。

    DWMWA_WINDOW_CORNER_PREFERENCE(33) = DWMWCP_ROUND(2)；仅 Win11
    (build 22000+) 支持，Win10/调用失败时保持直角，不影响功能。
    """
    try:
        if sys.getwindowsversion().build < 22000:
            return
        hwnd = ctypes.windll.user32.GetParent(win.winfo_id())
        pref = ctypes.c_int(2)   # DWMWCP_ROUND
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            hwnd, 33, ctypes.byref(pref), ctypes.sizeof(pref))
    except Exception:
        pass


def _screen_rect(widget):
    """当前桌面可用范围 (x, y, w, h)：优先多屏虚拟桌面，失败回退主屏。

    遮罩铺满、弹窗/面板钳制都必须用它——winfo_screenwidth/height 只反映
    主屏，多屏（尤其带负坐标的左侧副屏）下会把窗口错误地钳回主屏边界，
    表现为「对话框从副屏跳回主屏」「面板位置错乱」。
    """
    try:
        vx, vy, vw, vh = _virtual_screen()
        if vw > 0 and vh > 0:
            return vx, vy, vw, vh
    except Exception:
        pass
    return 0, 0, widget.winfo_screenwidth(), widget.winfo_screenheight()


# _mix_color / _norm_color / _ease_out_cubic / _lin 已移至 ui_utils.py
# （纯函数、无 tkinter 依赖），在文件头部 import 后仍以原名使用。


HOVER_MS = 40       # hover 过渡单步间隔（ms）
HOVER_STEPS = 3     # hover 过渡步数；总时长 = 3 × 40 = 120ms（区间 90~140）
HOVER_EASE = _ease_out_cubic   # hover 缓动（跟手但去掉线性生硬感）


def _bind_hover(widget, normal, active, guard=None):
    """统一 hover 变色绑定，带三步颜色微过渡（约 120ms，ease-out）。

    normal/active 为 (bg, fg) 元组（fg 传 None 则只过渡背景，供 Canvas
    等无前景色控件使用）；guard(widget) 返回 False 时该次 Enter/Leave
    不变色（用于禁用态、「已复制 ✓」反馈期等守卫）。
    过渡从控件当前颜色出发：快速来回扫过时从中途色继续，不会跳变；
    回调链挂在控件自身 after 上，控件销毁后自动失效；新过渡启动前
    取消旧链（同一控件只允许一条活动链），杜绝多链叠加互相覆盖。
    采样点 t = (i+1)/steps（i ∈ [0, steps-1]）：首帧 e=0.704（跟手、无空转
    帧），末次 e=1.0 精确落终色，Δ 单调递减（收尾自然）。
    """
    st = {"job": None}

    def cancel():
        if st["job"] is not None:
            try:
                widget.after_cancel(st["job"])
            except Exception:
                pass
            st["job"] = None

    def apply(frm, to, e):
        try:
            # 两端都要归一化：widget 的当前色可能是颜色名（默认系统色），
            # 目标色也可能是颜色名（如 fg="white"）。任一不可解析就跳过该
            # 通道，绝不让它中断整条过渡链。
            c0, c1 = _norm_color(widget, frm[0]), _norm_color(widget, to[0])
            if c0 and c1:
                widget.config(bg=_mix_color(c0, c1, e))
            if to[1] is not None:
                f0, f1 = _norm_color(widget, frm[1]), _norm_color(widget, to[1])
                if f0 and f1:
                    widget.config(fg=_mix_color(f0, f1, e))
            return True
        except (tk.TclError, ValueError):
            return False

    def go(target):
        cancel()
        try:
            frm = (widget.cget("bg"), widget.cget("fg"))
        except tk.TclError:
            return
        # 减少动画：直接落终态（无过程）
        if _reduced_motion():
            apply(frm, target, 1.0)
            return

        def chain(i):
            # 采样 t = (i+1)/steps（i 从 0 到 steps-1），末次即 t=1.0 → e=1.0；
            # 首帧 e=0.704（跟手、无 40ms 空转），Δ 单调递减（收尾自然）
            t = (i + 1) / HOVER_STEPS
            if not apply(frm, target, HOVER_EASE(t)):
                st["job"] = None
                return
            if i + 1 < HOVER_STEPS:
                st["job"] = widget.after(HOVER_MS, lambda: chain(i + 1))
            else:
                st["job"] = None

        chain(0)

    widget.bind("<Enter>", lambda e: None if guard and not guard(e.widget)
                else go(active))
    widget.bind("<Leave>", lambda e: None if guard and not guard(e.widget)
                else go(normal))


FADE_IN_STEPS = 5    # 窗口淡入步数
FADE_IN_MS = 28      # 淡入单步间隔；总时长 = 5 × 28 = 140ms（区间 130~160）
FADE_IN_FROM = 0.4   # 起始不透明度（1.0 = 全不透明）
FADE_IN_EASE = _ease_out_cubic   # 淡入缓动（起步快、收尾缓）


def _fade_in(win, steps=FADE_IN_STEPS, ms=FADE_IN_MS, start=FADE_IN_FROM,
             to=1.0):
    """Toplevel 窗口快速淡入：alpha 从 start 分步过渡到 to。

    统一使用同一套参数（5 步 × 28ms ≈ 140ms，ease-out），保证面板 /
    对话框观感一致；采样点 t = (i+1)/steps，末次精确落 to（无空转首帧）。
    to < 1.0 用于半透明遮罩层（遮罩停在部分不透明，而非全不透明）。
    不支持的平台静默跳过（等效瞬时显示）；减少动画档位直接置终值。
    """
    try:
        win.attributes("-alpha", start)
    except tk.TclError:
        return
    if _reduced_motion():
        try:
            win.attributes("-alpha", to)
        except tk.TclError:
            pass
        return

    def up(i):
        # 采样 t = (i+1)/steps（i 从 0 到 steps-1），末次 t=1.0 → e=1.0
        t = (i + 1) / steps
        try:
            win.attributes("-alpha",
                           min(to, start + (to - start) * FADE_IN_EASE(t)))
        except tk.TclError:
            return
        if i + 1 < steps:
            win.after(ms, lambda: up(i + 1))

    up(0)


# --------------------- 通用模态对话框（Modal 组件） ---------------------
# 结构：遮罩层（全屏半透明窗口）→ 对话框卡片 → 标题区 / 内容区 / 操作按钮区。
#
# 【架构裁定 · 实测依据】Tk 下「遮罩层 + 点击遮罩关闭」与「原生真模态
# grab_set()」不可兼得：grab 会把本应用全部事件重定向到 grab 窗口，实测
# 弹窗 grab 期间遮罩命中 0 次点击，反向（遮罩 grab）时卡片命中 0 次。
# 本组件按「真遮罩 + 软模态」实现：
#   - 遮罩是真实的全屏窗口，物理覆盖背景 ⇒ 背景的鼠标/滚轮到不了（等效
#     于 Web 的「锁定背景滚动」），并直接接收点击 ⇒ 点击遮罩关闭可用；
#   - 模态性由「遮罩拦截 + 焦点回拉 + Tab 内部循环」保证，不使用 grab，
#     因此不存在 grab 未释放导致界面卡死的风险。
#
# 【Web 术语映射】本项目是 tkinter 桌面程序，无 DOM/浏览器：
#   - 「可访问性属性（ARIA）」→ 无对应物；以「键盘可达性」落地：所有按钮
#     可 Tab 循环、Enter 触发默认按钮、Esc 关闭、打开即聚焦默认项。
#   - 「桌面端与移动端」→ 无移动端；「响应式」落地为按屏宽/屏高自适应：
#     卡片宽度不超屏宽、内容超长时内容区滚动、位置钳制在屏幕内。

MODAL_OVERLAY_ALPHA = 0.34   # 遮罩层不透明度（0=全透明，1=全遮挡）
MODAL_PAD = 22               # 卡片内边距
MODAL_MAX_W = 460            # 卡片最大宽度
MODAL_CONTENT_MAX_W = 380    # 内容区最大宽度（超出按此折行）
MODAL_MAX_H_RATIO = 0.72     # 卡片最大高度占屏高比（内容超出则内容区滚动）
MODAL_MIN_CONTENT_W = 240    # 内容区最小宽度（窄屏下限）
# 同一侧相邻按钮的水平间距；8px 时两个按钮几乎贴在一起（关闭弹窗的
# 「直接退出」+「最小化到系统托盘」），视觉上分不出是两个独立按钮。
MODAL_BTN_GAP = 14           # 按钮区同侧相邻按钮水平间距


class Modal:
    """通用模态对话框：遮罩层 + 标题 + 内容区（可滚动）+ 操作按钮区。

    用法（非阻塞，结果经回调返回，与本项目现有异步风格一致）：
        Modal(parent, title="标题", content=text_or_builder,
              buttons=[{"text": "确定", "style": "primary", "default": True},
                       {"text": "取消", "style": "ghost"}],
              on_result=lambda v: ..., on_close=lambda: ...).show()

    参数：
        title      标题区文字（None 则不渲染标题区，如标题由系统栏承载）
        content    str（渲染为正文 Label）或 callable(frame)（自定义内容，
                   如关闭确认弹窗的两个选项行）；None 则无内容区
        buttons    按钮规格列表，每项 {text, style, default, value, align}；
                   style ∈ primary（红底主按钮）/ ghost（幽灵次按钮）/
                   danger（危险操作）；default=True 者响应 Enter 并初始聚焦；
                   align ∈ left/right 指定该按钮贴哪一端（省略时 primary
                   靠右、其余靠左），同一端的多个按钮按列表顺序排列
        dismissible 是否允许「点击遮罩 / Esc」关闭（关闭走 on_close）
        overlay     是否启用遮罩层（False 则退化为普通对话框）
        on_result   点击按钮回调，参数为该按钮的 value
        on_close    以「关闭」方式结束（遮罩点击 / Esc / 窗口关闭）时回调
        on_finish   完全销毁后回调（供调用方清理自身引用）
    """

    def __init__(self, parent, title=None, content=None, buttons=None,
                 on_result=None, on_close=None, dismissible=True,
                 overlay=True, on_finish=None, win_title=None):
        self.parent = parent
        self.title = title
        # win_title 独立于 title：前者是系统标题栏文字，后者是卡片内的
        # 标题区。二者必须分开——关闭确认弹窗要求「卡片内不重复标题、
        # 由系统标题栏承载」，若只用 title 传 None 会把标题栏也清空。
        self.win_title = win_title
        self.content = content
        self.buttons = list(buttons or [])
        self.on_result = on_result
        self.on_close = on_close
        self.on_finish = on_finish
        self.dismissible = dismissible
        self.use_overlay = overlay
        self.result = None
        self._closed = False
        self.win = None            # 对话框卡片窗口（Toplevel）
        self.overlay = None        # 遮罩层窗口（Toplevel，可为 None）
        self._btn_widgets = []     # [(spec, Button)]，供焦点循环用
        self._focus_widgets = []   # 可聚焦控件（Tab 循环范围）
        self._body_canvas = None   # 内容区画布（高度自适应收缩用）

    # ---------- 构建 ----------

    def show(self):
        """构建并显示对话框（立即返回，不阻塞调用方）。"""
        p = self.parent
        if self.use_overlay:
            self._build_overlay(p)
        self._build_dialog(p)
        self.win.update_idletasks()
        self._fit()      # 内容过长时先收缩内容区，保证高度不超屏

        # 顺序至关重要：Windows 上设置 -alpha（_fade_in 内部）会让 Tk 重建
        # 窗口的 HWND，重建后此前通过 geometry() 设置的位置会被丢弃
        # （实测：先 geometry 再 fade → 位置从 +1466+455 退化为 +0+0）。
        # 因此所有会改动窗口属性的操作（topmost / alpha）都必须排在
        # _place() 之前，定位放在最后一步。
        try:
            self.win.attributes("-topmost", True)
        except tk.TclError:
            pass
        _fade_in(self.win)
        if self.overlay is not None:
            _fade_in(self.overlay, start=0.0, to=MODAL_OVERLAY_ALPHA)
        self._place()
        # geometry() 只是「请求」，窗口真正被映射（map）时 Windows 窗口管理器
        # 可能重新定位并覆盖它（实测：_place 设成 +1466+455，映射后退回
        # +0+0）。这里立刻update_idletasks 让请求生效，并在 idle 时再校正
        # 一次，覆盖「映射晚于 geometry」的情况。
        self.win.update_idletasks()
        self.win.after_idle(lambda: None if self._closed else self._place())
        if self.overlay is not None:
            self.overlay.lift()
        self.win.lift()
        self.win.focus_force()
        _round_window_corners(self.win)
        self._setup_focus()
        # 背景滚动锁定：遮罩已物理拦截滚轮，这里再置标志位双重保险
        # （例如遮罩被其它窗口盖住、或键盘 PageUp/Down 触发滚动时）
        try:
            self.parent._modal_open = True
        except Exception:
            pass
        return self

    def _build_overlay(self, parent):
        ov = tk.Toplevel(parent)
        ov.overrideredirect(True)      # 无边框：纯遮罩，不参与任务栏
        ov.configure(bg=COLOR_OVERLAY)
        try:
            ov.attributes("-topmost", True)
            ov.attributes("-alpha", 0.0)   # 由 _fade_in 过渡到目标值
        except tk.TclError:
            pass
        # 按【虚拟桌面】整体铺满：winfo_screenwidth/height 只给主屏尺寸，
        # 多屏（尤其带负坐标的左侧副屏）下遮罩盖不住副屏，副屏上的背景
        # 仍可点击——「真遮罩 + 软模态」的前提就失效了。
        vx, vy, vw, vh = _screen_rect(parent)
        ov.geometry("%dx%d+%d+%d" % (vw, vh, vx, vy))
        if self.dismissible:
            # 遮罩点击即关闭：整块遮罩都是「取消区」
            ov.bind("<Button-1>", lambda _e: self.close())
        ov.bind("<Escape>", lambda _e: self.close())
        self.overlay = ov

    def _build_dialog(self, parent):
        dlg = tk.Toplevel(parent, bg=COLOR_CARD, highlightthickness=1,
                          highlightbackground=COLOR_BORDER)
        self.win = dlg
        dlg.title(self.win_title or self.title or "")
        dlg.resizable(False, False)
        dlg.transient(parent)
        try:
            # Win11：系统圆角 + 投影已提供边界感，去掉 1px 直角描边
            # （描边是方角，会被圆角裁出毛边）；旧系统保留描边卡片观感
            if sys.getwindowsversion().build >= 22000:
                dlg.configure(highlightthickness=0)
        except Exception:
            pass

        card = tk.Frame(dlg, bg=COLOR_CARD, padx=MODAL_PAD, pady=MODAL_PAD)
        card.pack(fill="both", expand=True)

        # ---- 标题区 ----
        if self.title:
            tk.Label(card, text=self.title, bg=COLOR_CARD, fg=COLOR_TEXT,
                     font=F_DLG_TITLE, anchor="w").pack(fill="x")
            tk.Frame(card, bg=COLOR_DIVIDER,
                     height=1).pack(fill="x", pady=(12, 0))

        # ---- 内容区（超长可滚动）----
        self._build_body(card)

        # ---- 操作按钮区 ----
        if self.buttons:
            tk.Frame(card, bg=COLOR_DIVIDER,
                     height=1).pack(fill="x", pady=(14, 12))
            self._build_buttons(card)

        if self.dismissible:
            dlg.bind("<Escape>", lambda _e: self.close())
        dlg.protocol("WM_DELETE_WINDOW", self.close)

    def _build_body(self, card):
        """内容区：内容超出屏幕高度上限时自动出现滚动条。"""
        if self.content is None:
            return
        top_pad = (12, 0) if self.title else (0, 0)
        outer = tk.Frame(card, bg=COLOR_CARD)
        outer.pack(fill="both", expand=True, pady=top_pad)

        cv = tk.Canvas(outer, bg=COLOR_CARD, highlightthickness=0)
        self._body_canvas = cv
        sb = tk.Scrollbar(outer, orient="vertical", command=cv.yview,
                          width=10, takefocus=0)
        inner = tk.Frame(cv, bg=COLOR_CARD)
        win_id = cv.create_window((0, 0), window=inner, anchor="nw")

        # 填充内容：字符串 → 正文 Label；callable → 调用方自定义构建
        if callable(self.content):
            self.content(inner)
        else:
            tk.Label(inner, text=str(self.content), bg=COLOR_CARD,
                     fg=COLOR_TEXT, font=F_DLG_BODY, anchor="w",
                     justify="left",
                     wraplength=MODAL_CONTENT_MAX_W).pack(fill="x")

        inner.update_idletasks()
        req_w = max(1, inner.winfo_reqwidth())
        req_h = max(1, inner.winfo_reqheight())
        # 响应式：宽度不超过内容上限；高度上限由 _fit() 按「整窗高度」统一
        # 裁定（此处先按屏高比例给一个宽松初值，避免内容极长时撑爆窗口）
        w = min(max(req_w, MODAL_MIN_CONTENT_W), MODAL_CONTENT_MAX_W)
        max_h = int(_screen_rect(self.parent)[3] * MODAL_MAX_H_RATIO)
        h = min(req_h, max_h)
        cv.configure(width=w, height=h, scrollregion=(0, 0, w, req_h))
        cv.itemconfigure(win_id, width=w)
        cv.configure(yscrollcommand=sb.set)

        if req_h > max_h:
            sb.pack(side="right", fill="y")
        cv.pack(side="left", fill="both", expand=True)
        # 滚轮：canvas 内的子控件其 Tk 父容器即 canvas，事件会传播到它，
        # 因此只在 canvas 上绑定即可覆盖整个内容区
        cv.bind("<MouseWheel>",
                lambda e: cv.yview_scroll(int(-e.delta / 120), "units"))

    def _build_buttons(self, card):
        """操作按钮区：主按钮居右、次按钮居左，两端分列。"""
        row = tk.Frame(card, bg=COLOR_CARD)
        row.pack(fill="x")
        for spec in self.buttons:
            style = spec.get("style", "ghost")
            if style == "primary":
                # 主按钮：与主界面底部「刷新」完全同款（中行红 + F_BTN_MAIN）
                bg, fg = COLOR_ACCENT, "white"
                abg, afg = COLOR_ACCENT_DARK, "white"
                font = F_BTN_MAIN
            elif style == "danger":
                bg, fg = COLOR_CARD, COLOR_DANGER_TEXT
                abg, afg = COLOR_DANGER_ACTIVE, COLOR_ERR
                font = F_DLG_BTN
            else:
                # 次按钮：与主界面「复制 / 币种」同款幽灵按钮（白底 + 灰字 +
                # 描边）；字重取常规，与主按钮的粗体拉开主次层级
                bg, fg = COLOR_CARD, COLOR_BTN_TEXT
                abg, afg = COLOR_BTN_ACTIVE, COLOR_BTN_TEXT_ACTIVE
                font = (FONT_FAMILY, 10)
            btn = tk.Button(
                row, text=spec.get("text", ""), font=font, bg=bg, fg=fg,
                activebackground=abg, activeforeground=afg,
                relief="flat", padx=18, pady=7, cursor="hand2",
                takefocus=0, bd=0,
                command=lambda s=spec: self._pick(s))
            if style != "primary":
                # 次/危险按钮带 1px 描边（与卡片内幽灵按钮同款）
                btn.configure(highlightthickness=1,
                              highlightbackground=COLOR_BORDER,
                              highlightcolor=COLOR_BORDER)
            _bind_hover(btn, (bg, fg), (abg, afg))
            self._btn_widgets.append((spec, btn))
        # 按钮分列两端：主操作贴右缘（与主界面底部「刷新」同侧，符合
        # 「确认在右」的通用习惯），次操作贴左缘。此前全部靠右，「直接
        # 退出」紧挨着「最小化到系统托盘」，既分不出主次、也像连成一块。
        # 对齐规则：spec 的 align（"left"/"right"）优先，未指定时
        # primary → 右，其余（ghost/danger）→ 左。
        def _align(spec):
            a = spec.get("align")
            if a in ("left", "right"):
                return a
            return "right" if spec.get("style") == "primary" else "left"

        lefts = [(s, b) for s, b in self._btn_widgets if _align(s) == "left"]
        rights = [(s, b) for s, b in self._btn_widgets if _align(s) == "right"]
        # side="left" 按打包顺序从左往右排；side="right" 则是「先打包的更
        # 靠右」，故右侧需倒序，视觉顺序才等于 buttons 的书写顺序。
        # 注意：pack_configure(side=...) 不会改变已有堆叠顺序，改它无效。
        for _spec, btn in lefts:
            btn.pack(side="left", padx=(0, MODAL_BTN_GAP))
        for _spec, btn in reversed(rights):
            btn.pack(side="right", padx=(MODAL_BTN_GAP, 0))

    # ---------- 交互 ----------

    def _pick(self, spec):
        """点击按钮：记录结果 → 关闭 → 回调。"""
        self.result = spec.get("value", spec.get("text"))
        cb = self.on_result
        self.close(cancel_cb=True)
        if cb is not None:
            cb(self.result)

    def _setup_focus(self):
        """焦点管理：初始聚焦窗口；Tab 在按钮间循环；焦点逃逸自动回拉。"""
        self._focus_widgets = [b for _, b in self._btn_widgets]
        # 初始焦点放在窗口上，不落到按钮：tk.Button 一旦获得焦点，Windows
        # 会为它画一圈虚线焦点框，与全站「takefocus=0 / 无焦点框」的扁平
        # 风格冲突。键盘可达性不依赖初始焦点——Enter 触发默认按钮、Esc
        # 关闭、Tab 才在按钮间移动焦点（用户主动按 Tab 时出现焦点框是符合
        # 预期的键盘反馈）。
        self.win.focus_set()

        widgets = self._focus_widgets

        def cycle(step):
            if not widgets:
                return
            try:
                cur = self.win.focus_get()
            except Exception:
                cur = None
            try:
                i = widgets.index(cur)
            except ValueError:
                i = -1
            widgets[(i + step) % len(widgets)].focus_set()

        def on_tab(e):
            cycle(-1 if (e.state & 0x0001) else 1)   # Shift → 反向
            return "break"

        for w in widgets + [self.win]:
            w.bind("<Tab>", on_tab)
        # Enter 触发默认按钮（按钮自身已响应 Space，这里补 Enter）
        self.win.bind("<Return>", lambda _e: self._activate_default())
        # 焦点逃逸回拉：焦点离开弹窗（点到背景/其它窗口）时拉回弹窗，
        # 避免键盘输入落到被遮罩挡住的界面上
        self.win.bind("<FocusOut>", lambda _e: self._refocus())

    def _activate_default(self):
        for spec, btn in self._btn_widgets:
            if spec.get("default"):
                btn.invoke()
                return
        if self._btn_widgets:
            self._btn_widgets[0][1].invoke()

    def _refocus(self):
        """焦点回拉（延迟到 idle 判定，避免在弹窗内部移动焦点时误抢）。

        只在焦点落回「本应用的其它窗口」时拉回——若用户切到别的应用，
        focus_get() 取不到本应用的控件（或焦点已不在本进程），此时强行
        focus_force 会与用户反复抢焦点（实测：强制切到主窗口后焦点被弹窗
        夺回）。切走属于用户明确意图，不应拦。
        """
        def check():
            if self._closed:
                return
            try:
                if not self.win.winfo_exists():
                    return
                cur = self.win.focus_get()
            except Exception:
                return
            if cur is None:
                return                      # 焦点已离开本应用 → 不抢
            if self._is_descendant(cur):
                return                      # 仍在弹窗内 → 无需处理
            try:
                # 仅当焦点落在本应用（父窗口）内才拉回
                if str(cur.winfo_toplevel()) == str(self.parent):
                    self.win.focus_force()
            except (tk.TclError, AttributeError):
                pass
        self.win.after_idle(check)

    def _is_descendant(self, widget):
        try:
            return widget.winfo_toplevel() is self.win
        except Exception:
            return False

    def _fit(self):
        """高度自适应：整窗超过屏高上限时，收缩内容区画布（而非裁掉按钮区）。

        上限针对「整个卡片窗口」而非仅内容区——否则标题/按钮等固定装饰会
        额外叠加，导致最终窗口高度突破屏高比例。收缩后内容区内部滚动。
        """
        if self._body_canvas is None:
            return
        dlg = self.win
        try:
            dlg.update_idletasks()
            max_h = int(_screen_rect(self.parent)[3] * MODAL_MAX_H_RATIO)
            over = dlg.winfo_reqheight() - max_h
            if over <= 0:
                return
            cur = self._body_canvas.winfo_height()
            self._body_canvas.configure(height=max(60, cur - over))
            dlg.update_idletasks()
        except tk.TclError:
            pass

    def _place(self):
        """居中于父窗口并钳制到（虚拟）屏幕内（小屏/多屏拔插时始终完整可见）。"""
        dlg = self.win
        try:
            pw = self.parent.winfo_width()
            ph = self.parent.winfo_height()
            dw = dlg.winfo_reqwidth()
            dh = dlg.winfo_reqheight()
            # 用虚拟桌面范围而非主屏尺寸：主窗口停在副屏时，按主屏钳制会把
            # 对话框强行拉回主屏（父窗口在左、对话框飞到右边，观感割裂）。
            vx, vy, vw, vh = _screen_rect(self.parent)
            # 响应式：卡片宽度不超过屏宽（留边距），也不超过组件上限
            dw = min(dw, MODAL_MAX_W, vw - 40)
            x = self.parent.winfo_rootx() + (pw - dw) // 2
            y = self.parent.winfo_rooty() + (ph - dh) // 3
            x = max(vx + 4, min(x, vx + vw - dw - 4))
            y = max(vy + 4, min(y, vy + vh - dh - 4))
            dlg.geometry("%dx%d+%d+%d" % (dw, dh, x, y))
        except tk.TclError:
            pass

    # ---------- 生命周期 ----------

    def close(self, cancel_cb=False):
        """关闭并销毁遮罩与卡片。

        cancel_cb=True 表示由按钮触发（已自行回调 on_result），此时不再
        走 on_close；「点击遮罩 / Esc / 窗口关闭」走 on_close 语义。
        """
        if self._closed:
            return
        self._closed = True
        try:
            self.parent._modal_open = False
        except Exception:
            pass
        for w in (self.overlay, self.win):
            if w is None:
                continue
            try:
                if w.winfo_exists():
                    w.destroy()
            except tk.TclError:
                pass
        self.overlay = None
        if not cancel_cb and self.on_close is not None:
            self.on_close()
        if self.on_finish is not None:
            self.on_finish()


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.configure(bg=COLOR_BG)
        # 最小宽 470：保证最长币种名（马来西亚林吉特/塞尔维亚第纳尔）的
        # 卡片在单列下也完整显示（实测自然宽约 410px + 间距，见 _measure_cards）
        self.minsize(470, 380)
        self.resizable(True, True)
        # 替换默认 Tk 羽毛笔图标
        try:
            self.iconbitmap(default=_resource_path("app_icon.ico"))
        except Exception:
            pass

        self.result_q = queue.Queue()
        self.cards = {}
        self._fetching = False
        self._requested = []        # 本次抓取对应的选择快照（检测抓取期间的选择变更）
        # 会话级数据复用：本次运行已成功取到的币种数据（纯内存，进程退出即
        # 消失，不落盘）。切换币种重建卡片时先用它即时渲染旧值（标注
        # 「上次数据」），避免等待网络期间显示「--」。
        self._session = {}
        self._panel = None          # 币种下拉面板（Toplevel，打开时非 None）
        self._panel_checks = {}     # 面板中各币种的勾选框 Canvas
        self._status_base = "正在准备…"
        self._status_fg_state = "sub"
        self._last_update = ""
        self._sb_visible = True     # 滚动条当前是否显示（按需隐藏）
        self._cols = 1              # 卡片当前列数（响应式，随窗口宽度变化）
        self._relayout_job = None   # 列数变化去抖重排的 after 任务句柄
        self._morph_job = None      # 列数过渡动画的 after 任务句柄
        self._cards_min_w = 0       # 当前卡片的最大自然宽度（不截字所需，实测）
        self._measure_key = None    # 上次测量对应的币种集合（用于方向切换时复用）
        self._drag = None           # 卡片拖拽状态（dict，无拖拽时 None）
        self._drag_scroll_job = None  # 拖拽边缘自动滚动的 after 任务句柄
        self._outer = None          # 内容区的父容器（用于读取窗口宽）
        self._disp_by_code = {cur.code: cur.display for cur in ALL_CURRENCIES}
        # 选中币种与窗口尺寸（持久化到 %APPDATA%）
        # _load_config 已保证 selected 非空且均为合法币种代码
        cfg = _load_config()
        self._selected = list(cfg["selected"])
        self._saved_geometry = cfg.get("geometry")
        # 换算方向（False=1 外币→人民币，True=1 人民币→外币）。
        # 纯展示层偏好：切换只重算卡片文案，不触发任何网络请求。
        self._inverse = bool(cfg.get("inverse"))

        self._build_ui()
        self.bind("<F5>", lambda _e: self.refresh())
        # 滚轮绑定在顶层窗口：Tk 事件按 bindtags 链传播，子组件（卡片等）
        # 上的滚轮事件都会触发，覆盖整个窗口；不要在卡片画布上单独绑定，
        # 否则鼠标在卡片上滚动无效。
        self.bind("<MouseWheel>", self._on_mousewheel)
        # 轮询链句柄必须存下来：_kick_poll() 靠它实现「取消+重排」，否则
        # 首次换挡会留下一条无人管的孤儿链（轮询频率翻倍）
        self._poll_job = self.after(100, self._poll_queue)
        self.after(150, self.refresh)
        # 启动后：有尺寸记忆则恢复；否则窗口居中并让高度自适应卡片数
        self.update_idletasks()
        if self._saved_geometry:
            try:
                # 钳制到虚拟屏幕内：拔掉副屏后记忆的屏外坐标会导致窗口不可见
                self.geometry(_clamp_geometry(self._saved_geometry))
            except Exception:
                self.eval("tk::PlaceWindow . center")
                self.after_idle(self._fit_window)
        else:
            self.eval("tk::PlaceWindow . center")
            self.after_idle(self._fit_window)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        # 系统托盘：启动时仅子类化窗口过程（接收托盘回调与二次启动唤醒
        # 消息），托盘图标在首次「最小化到托盘」时才注册
        self._tray = TrayController(
            self, _resource_path("app_icon.ico"), APP_TITLE,
            self._tray_restore, self._quit_app)
        self._tray.install()
        self._tray_notified = False   # 「已最小化到托盘」气泡只提示一次
        self._close_dlg = None        # 关闭方式选择对话框（打开时非 None）
        self._close_modal = None      # 关闭对话框的 Modal 实例（含遮罩层）
        self._modal_open = False      # 模态打开标志：锁定背景滚动（双重保险）

    # ---------- 界面构建 ----------

    def _build_ui(self):
        # 内容区统一限宽并水平居中：窗口远宽于 MAX_CONTENT_W 时卡片不被拉伸，
        # 标题/状态栏与卡片保持同一边距、同一中轴线，视觉更整齐。
        outer = tk.Frame(self, bg=COLOR_BG)
        outer.pack(fill="both", expand=True)
        self._content = tk.Frame(outer, bg=COLOR_BG)
        self._outer = outer
        self._content.place(relx=0.5, rely=0, anchor="n", relheight=1.0,
                            width=min(440, MAX_CONTENT_W))
        outer.bind("<Configure>", self._on_outer_resize)

        pad = tk.Frame(self._content, bg=COLOR_BG, padx=18, pady=16)
        pad.pack(fill="both", expand=True)

        # 顶部标题区：品牌徽标 + 应用名（左），币种菜单（右）
        header = tk.Frame(pad, bg=COLOR_BG)
        header.pack(fill="x", pady=(0, 14))
        title_box = tk.Frame(header, bg=COLOR_BG)
        title_box.pack(side="left", anchor="n")
        mark = tk.Canvas(title_box, width=36, height=36, bg=COLOR_BG,
                         highlightthickness=0)
        mark.pack(side="left", padx=(0, 10), pady=(2, 0))
        _rounded_rect(mark, 1, 1, 35, 35, 10, fill=COLOR_ACCENT, outline="")
        mark.create_text(18, 19, text="¥", fill="white",
                         font=(FONT_FAMILY, 15, "bold"))
        name_box = tk.Frame(title_box, bg=COLOR_BG)
        name_box.pack(side="left")
        tk.Label(name_box, text="中行汇率换算", bg=COLOR_BG, fg=COLOR_TEXT,
                 font=F_TITLE).pack(anchor="w")
        tk.Label(name_box, text="中国银行外汇牌价 · 现汇买入价 / 市场参考汇率",
                 bg=COLOR_BG, fg=COLOR_SUB,
                 font=F_SUBTITLE).pack(anchor="w", pady=(3, 0))
        self._build_currency_menu(header)

        # 换算方向切换：紧邻卡片区上方，作用对象（卡片数值）一目了然。
        # 不放顶部标题行——实测品牌区（298px）+ 本按钮（115px）+ 币种
        # （63px）共 476px，超出 440 窗口的 404px 可用宽，会把品牌挤出。
        self._build_direction_toggle(pad)

        # 可滚动卡片区：卡片数量多时滚动查看
        body = tk.Frame(pad, bg=COLOR_BG)
        body.pack(fill="both", expand=True)
        self.sb = tk.Scrollbar(body, orient="vertical", command=self._scroll_yview)
        self.sb.pack(side="right", fill="y")
        # 可滚动卡片区：卡片数量多时滚动查看。
        # height=160 给画布一个较小的「请求高度」，真实高度由 pack expand
        # 撑满剩余空间；否则画布默认请求高度过大，窗口缩到最小尺寸时
        # 后打包的状态栏会被挤出窗口外（pack 不回缩已分配空间）。
        self.card_canvas = tk.Canvas(body, bg=COLOR_BG, highlightthickness=0,
                                     height=160,
                                     yscrollcommand=self._on_canvas_scroll)
        self.card_canvas.pack(side="left", fill="both", expand=True)
        self.cards_frame = tk.Frame(self.card_canvas, bg=COLOR_BG)
        self._cards_win = self.card_canvas.create_window(
            (0, 0), window=self.cards_frame, anchor="nw")
        self.cards_frame.bind("<Configure>", self._on_cards_resize)
        self.card_canvas.bind("<Configure>", self._on_canvas_resize)

        self._rebuild_cards()

        # 状态栏：状态圆点 + 状态文字 + 刷新按钮
        status_row = tk.Frame(pad, bg=COLOR_BG)
        status_row.pack(fill="x", pady=(14, 0))
        self.dot_status = tk.Canvas(status_row, width=10, height=10, bg=COLOR_BG,
                                    highlightthickness=0)
        self.dot_status.create_oval(2, 2, 8, 8, fill=COLOR_SUB, outline="",
                                    tags="dot")
        self.dot_status.pack(side="left", padx=(2, 7))
        self.lbl_status = tk.Label(status_row, text="正在准备…", bg=COLOR_BG,
                                   fg=COLOR_SUB, font=F_STATUS,
                                   anchor="w", justify="left", wraplength=400)
        self.lbl_status.pack(side="left", fill="x", expand=True)
        self.lbl_status.bind("<Configure>", self._on_status_resize)
        self.btn_refresh = tk.Button(
            status_row, text="刷 新", command=self.refresh,
            font=F_BTN_MAIN, bg=COLOR_ACCENT, fg="white",
            activebackground=COLOR_ACCENT_DARK, activeforeground="white",
            disabledforeground=COLOR_REFRESH_DISABLED_FG,
            relief="flat", padx=20, pady=6, cursor="hand2", takefocus=0,
            bd=0)
        self.btn_refresh.pack(side="right", padx=(8, 0))
        # 禁用态不变色，避免「刷新中…」被 hover 提亮造成可点错觉
        _bind_hover(self.btn_refresh,
                    (COLOR_ACCENT, "white"), (COLOR_ACCENT_DARK, "white"),
                    guard=lambda w: str(w["state"]) == "normal")

    # ---------- 币种选择 ----------

    def _build_currency_menu(self, parent):
        """右上角「币种」按钮：点击弹出与整体风格一致的自绘下拉面板。

        不用原生 tk.Menu（系统菜单观感与扁平卡片风不搭）：面板为白底
        卡片 + 圆角勾选框 + 行 hover 高亮；点行切换（面板保持打开，
        便于连续增删币种），点面板外部或 Esc 关闭。
        """
        self.menu_btn = tk.Menubutton(
            parent, text="币种 ▾", font=F_BTN_SMALL,
            bg=COLOR_BTN, fg=COLOR_BTN_TEXT,
            activebackground=COLOR_BTN_ACTIVE, activeforeground=COLOR_BTN_TEXT,
            relief="flat", padx=14, pady=5, cursor="hand2", takefocus=0)
        self.menu_btn.pack(side="right", anchor="n")
        _bind_hover(self.menu_btn,
                    (COLOR_BTN, COLOR_BTN_TEXT),
                    (COLOR_BTN_ACTIVE, COLOR_BTN_TEXT))
        self.menu_btn.bind("<Button-1>", lambda _e: self._toggle_currency_panel())
        # 币种勾选状态（面板与配置持久化的单一事实来源）
        self._menu_vars = {cur.code: tk.BooleanVar(value=cur.code in self._selected)
                           for cur in ALL_CURRENCIES}

    def _toggle_currency_panel(self):
        if self._panel is not None:
            self._close_currency_panel()
        else:
            self._open_currency_panel()

    def _open_currency_panel(self):
        panel = tk.Toplevel(self, bg=COLOR_CARD,
                            highlightthickness=1,
                            highlightbackground=COLOR_BORDER)
        panel.overrideredirect(True)     # 无系统边框（自绘卡片）
        panel.attributes("-topmost", True)
        self._panel = panel
        self._panel_checks = {}

        # 头部说明 + 「全部选中/全部取消」切换按钮（右）+ 分隔线。
        # 放头部行右侧而非面板底部：与主界面「标签在左、操作在右」的
        # 布局约定一致，且不增加面板高度。按钮用「复制」同款幽灵样式。
        head = tk.Frame(panel, bg=COLOR_CARD)
        head.pack(fill="x", padx=14, pady=(10, 6))
        tk.Label(head, text="显示币种", bg=COLOR_CARD, fg=COLOR_SUB,
                 font=F_TAG).pack(side="left")
        self._panel_all_btn = tk.Button(
            head, text="全部选中", font=F_BTN_SMALL,
            bg=COLOR_CARD, fg=COLOR_BTN_TEXT,
            activebackground=COLOR_BTN_ACTIVE,
            activeforeground=COLOR_BTN_TEXT_ACTIVE,
            relief="flat", padx=10, pady=2, cursor="hand2",
            takefocus=0, bd=0, highlightthickness=1,
            highlightbackground=COLOR_BORDER, highlightcolor=COLOR_BORDER,
            command=self._panel_toggle_all)
        self._panel_all_btn.pack(side="right")
        _bind_hover(self._panel_all_btn,
                    (COLOR_CARD, COLOR_BTN_TEXT),
                    (COLOR_BTN_ACTIVE, COLOR_BTN_TEXT_ACTIVE))
        tk.Frame(panel, bg=COLOR_DIVIDER, height=1).pack(fill="x")

        # 双列币种行（30 个币种单列会超出屏幕高度）
        body = tk.Frame(panel, bg=COLOR_CARD)
        body.pack(fill="both", expand=True, padx=8, pady=(6, 10))
        n_cols = 2
        per_col = (len(ALL_CURRENCIES) + n_cols - 1) // n_cols
        for idx, cur in enumerate(ALL_CURRENCIES):
            rowf = tk.Frame(body, bg=COLOR_CARD, padx=8, pady=6)
            rowf.grid(row=idx % per_col, column=idx // per_col,
                      sticky="nsew", padx=2, pady=1)
            chk = tk.Canvas(rowf, width=16, height=16, bg=COLOR_CARD,
                            highlightthickness=0)
            chk.pack(side="left", padx=(0, 8))
            lbl = tk.Label(rowf, text="%s（%s）" % (cur.display, cur.code),
                           bg=COLOR_CARD, fg=COLOR_TEXT, font=F_BTN_SMALL)
            lbl.pack(side="left")
            code = cur.code
            widgets = (rowf, chk, lbl)
            for w in widgets:
                w.config(cursor="hand2")
                w.bind("<Button-1>", lambda _e, c=code: self._panel_toggle(c))
                # 行 hover 与全局按钮同一套微过渡（Canvas 无 fg，仅背景色）
                _bind_hover(w, (COLOR_CARD, None), (COLOR_BTN_ACTIVE, None))
            self._panel_checks[code] = chk
        body.grid_columnconfigure(0, weight=1)
        body.grid_columnconfigure(1, weight=1)

        # 定位：按钮右下角下方，钳制到（虚拟）屏幕内
        panel.update_idletasks()
        bx = self.menu_btn.winfo_rootx()
        by = self.menu_btn.winfo_rooty()
        x = bx + self.menu_btn.winfo_width() - panel.winfo_width()
        y = by + self.menu_btn.winfo_height() + 6
        vx, vy, vw, vh = _screen_rect(self)
        x = max(vx + 4, min(x, vx + vw - panel.winfo_width() - 4))
        y = max(vy + 4, min(y, vy + vh - panel.winfo_height() - 4))
        panel.geometry("+%d+%d" % (x, y))
        _fade_in(panel)   # 快速淡入（映射前设置 alpha，不闪全不透明帧）
        self._sync_currency_panel()
        # 全局抓取：点面板外任意位置即关闭（点击落在面板自身上且
        # 坐标在面板范围外 → 判定为外部点击）；Esc 亦可关闭
        panel.bind("<Button-1>", self._on_panel_outside_click)
        panel.bind("<Escape>", lambda _e: self._close_currency_panel())
        try:
            panel.wait_visibility()
            panel.grab_set_global()
        except tk.TclError:
            pass

    def _on_panel_outside_click(self, e):
        p = self._panel
        if p is None:
            return
        if e.x < 0 or e.y < 0 or e.x > p.winfo_width() or e.y > p.winfo_height():
            self._close_currency_panel()

    def _close_currency_panel(self):
        p = self._panel
        self._panel = None
        self._panel_checks = {}
        if p is not None:
            try:
                p.grab_release()
            except Exception:
                pass
            p.destroy()
            self.focus_set()

    def _panel_toggle_all(self):
        """一键切换全部币种：存在未选中项 → 全选；已全部选中 → 全不选。

        不走 _panel_toggle 的「至少保留一个」守卫——那是给单行点击的
        防误触保护；「全部取消」是用户在面板头部的显式操作，必须放行
        （空列表是合法状态，见 config._load_config）。
        """
        select_all = any(not v.get() for v in self._menu_vars.values())
        for cur in ALL_CURRENCIES:
            self._menu_vars[cur.code].set(select_all)
        # 顺序与 _rebuild_cards 的规范化一致（按 ALL_CURRENCIES 表序）
        self._selected = ([cur.code for cur in ALL_CURRENCIES] if select_all
                          else [])
        _save_config(self._selected, self.geometry(), self._inverse)
        self._rebuild_cards()
        self.refresh()
        self._status_base = ("已全选 %d 个币种" % len(ALL_CURRENCIES)
                             if select_all else "已取消全部币种")
        self._status_fg_state = "sub"
        self._render_status()
        self._sync_currency_panel()   # 勾选框 + 按钮文案一并刷新

    def _panel_toggle(self, code):
        var = self._menu_vars[code]
        var.set(not var.get())
        self._on_currency_toggle(code)
        # 「至少保留一个币种」守卫可能回滚 var，统一以 var 重绘勾选框
        self._sync_currency_panel()

    def _sync_currency_panel(self):
        if not (self._panel is not None and self._panel.winfo_exists()):
            return
        for code, chk in self._panel_checks.items():
            chk.delete("all")
            if self._menu_vars[code].get():
                _rounded_rect(chk, 1, 1, 15, 15, 5, fill=COLOR_ACCENT, outline="")
                chk.create_text(8, 8, text="✓", fill="white",
                                font=(FONT_FAMILY, 9, "bold"))
            else:
                _rounded_rect(chk, 1, 1, 15, 15, 5,
                              fill=COLOR_CARD, outline=COLOR_BORDER)
        # 切换按钮文案反映「下一步动作」：存在未选中项 → 「全部选中」；
        # 已全部选中 → 「全部取消」。每次面板内任何状态变化后都经此刷新。
        btn = getattr(self, "_panel_all_btn", None)
        if btn is not None and btn.winfo_exists():
            all_on = all(v.get() for v in self._menu_vars.values())
            btn.config(text="全部取消" if all_on else "全部选中")

    def _on_currency_toggle(self, code):
        # 至少保留一个币种，避免空界面
        if not self._menu_vars[code].get() and len(self._selected) <= 1:
            self._menu_vars[code].set(True)
            return
        if self._menu_vars[code].get():
            if code not in self._selected:
                self._selected.append(code)
        elif code in self._selected:
            self._selected.remove(code)
        _save_config(self._selected, self.geometry(), self._inverse)
        self._rebuild_cards()
        # 窗口尺寸保持固定（默认 3 个币种时的尺寸），币种增减不再变高，
        # 多出的卡片通过滚动条/鼠标滚轮查看。
        self.refresh()

    def _cancel_card_tasks(self):
        """卡片重建前清理挂在窗口/卡片上的瞬时任务（拖拽链、复制反馈链）。

        这些 after 链的回调都持有旧卡片字典：不取消就会在卡片被 destroy 后
        继续跑——拖拽自动滚动链是 40ms 自续，会持续访问已销毁的画布抛
        TclError 落 crash.log，且 _drag 残留指向已不存在的 code；复制反馈链
        的 restore/snap_finish 没有 winfo_exists 守卫，会向废弃字典续链约 1s。
        """
        if self._drag_scroll_job is not None:
            try:
                self.after_cancel(self._drag_scroll_job)
            except Exception:
                pass
            self._drag_scroll_job = None
        self._drag = None
        for info in self.cards.values():
            job = info.get("flash_after")
            if job is not None:
                try:
                    self.after_cancel(job)
                except Exception:
                    pass
                info["flash_after"] = None

    def _rebuild_cards(self):
        """按当前选中币种重建卡片。

        顺序 = _selected 的现有顺序（支持拖拽排序，随 config 的 selected
        字段持久化）；勾选新增的币种由 _on_currency_toggle 追加到末尾，
        因此不再按 ALL_CURRENCIES 表序规范化——规范化会抹掉用户的排序。
        """
        self._cancel_card_tasks()    # 先清理瞬时任务，再销毁卡片
        for w in self.cards_frame.winfo_children():
            w.destroy()
        self.cards = {}
        # 保留 _selected 的用户排序；新选中的币种在 _on_currency_toggle
        # 中追加到末尾（见该函数），这里只做过滤防御
        self._selected = [c for c in self._selected if c in CODE_TO_CURRENCY]
        self._selected_curs = [CODE_TO_CURRENCY[c] for c in self._selected]
        for cur in self._selected_curs:
            self.cards[cur.code] = self._build_card(self.cards_frame, cur)
        if not self._selected_curs:
            # 「全部取消」后的空态：给出指引而不是一片空白
            tip = tk.Label(
                self.cards_frame,
                text="当前未选择任何币种\n点击右上角「币种」添加要显示的货币",
                bg=COLOR_BG, fg=COLOR_SUB, font=(FONT_FAMILY, 10),
                justify="center")
            tip.grid(row=0, column=0, columnspan=MAX_COLS, pady=48)
        # 新增币种时，先用会话内已有数据即时渲染（标注「上次数据」），
        # 避免等待这次网络请求期间显示「--」
        self._render_session()
        self._sync_layout()

    # ---------- 响应式多列布局 ----------

    def _measure_cards(self, force=True):
        """测量当前卡片「不截字所需」的最大自然宽度（_cards_min_w）。

        取所有卡片请求宽度的最大值：任何一张卡被压窄都会截字，列数
        阈值必须以最宽的卡片为准，而不是固定的经验值。测量前把来源
        胶囊临时设为最宽文案「● 备用源参考」——胶囊实际文案随数据
        而定（空/中行牌价/备用源/上次数据），若按当前文案测量，数据
        到达后胶囊变宽会重新截字。测完恢复原文案。

        测量恒以「正向口径」进行：反向时所有卡片头部都是「人民币
        （CNY）」，实测最宽卡明显窄于正向，若按当前方向测量，内容区
        上限与列数都会随之变小——最大化窗口下反向的卡片整体比正向
        窄一截（两个方向尺寸不一致的根因）。临时按正向渲染 → 测量 →
        恢复（纯文本 config，不触发重绘、无闪烁），保证两个方向共用
        同一套布局参数。

        force=False 时若「选中币种集合未变」则直接复用上次结果：测量
        结果只取决于币种集合（测量口径恒为正向、胶囊恒为最宽文案），
        与换算方向无关。切换方向场景下复用可省掉「整卡正向重渲一遍 →
        再反向重渲一遍」的双份开销——30 币种时每次切换省 60 次 config
        调用 + 一次强制 idle 刷新。
        """
        key = tuple(self.cards.keys())
        if not force and key == getattr(self, "_measure_key", None):
            return
        if not self.cards:
            self._cards_min_w = CARD_IDEAL_W
            self._measure_key = None
            return
        saved = {}
        did_fwd = False
        try:
            if self._inverse:
                did_fwd = True
                self._inverse = False
                for code in self.cards:
                    self._render_card_value(code)
            for code, info in self.cards.items():
                saved[code] = info["tag"].cget("text")
                info["tag"].config(text="● 备用源参考")
            self.update_idletasks()
            self._cards_min_w = max(info["card"].winfo_reqwidth()
                                    for info in self.cards.values())
            self._measure_key = key
        finally:
            if did_fwd:
                self._inverse = True
                for code in self.cards:
                    self._render_card_value(code)
            for code, text in saved.items():
                try:
                    self.cards[code]["tag"].config(text=text)
                except tk.TclError:
                    pass

    def _card_unit_w(self):
        """单列占位宽 = 卡片自然宽 + grid 两侧 padx（7×2）。"""
        return (self._cards_min_w or CARD_IDEAL_W) + 14

    def _desired_cols(self, canvas_w):
        """按画布可用宽度与卡片实测自然宽度计算列数，钳制 1..MAX_COLS。

        cols = (可用宽 + 间距) // (单列占位宽 + 间距)。以实测的最宽
        卡片为单位，保证选出的列数下每张卡（含最长币种名）都放得下；
        再钳制到实际卡片数，币种少时不出空列。
        （_cards_min_w 恒为正向口径，见 _measure_cards——因此本判定
        与换算方向无关，两个方向的列数始终一致。）
        """
        unit = self._card_unit_w()
        cols = (canvas_w + CARD_GAP) // (unit + CARD_GAP)
        # n 与 _content_max_w 保持同一口径（空选择按 1 列算），避免两处
        # 对「没有卡片」给出不一致的列数/宽度上限
        n = max(1, len(getattr(self, "_selected_curs", []) or []))
        cols = min(cols, n)
        return max(1, min(MAX_COLS, int(cols)))

    def _content_max_w(self):
        """内容区宽度上限：下限 MAX_CONTENT_W，且随最宽卡片联动放大，
        并给每列留 CARD_SLACK 余量，保证满列数时最宽的卡片放得下、
        列数判定不会卡在阈值下方几像素。列数按实际卡片数收窄（币种
        少时内容区不必为满列数预留宽度）。
        （_cards_min_w 恒为正向口径，见 _measure_cards——上限因此与
        换算方向无关，两个方向的内容区宽度始终一致。）"""
        n = min(MAX_COLS, max(1, len(getattr(self, "_selected_curs", []) or [])))
        need = n * self._card_unit_w() + (n - 1) * CARD_GAP + 36
        return max(MAX_CONTENT_W, need + n * CARD_SLACK)

    def _apply_content_width(self):
        """按当前上限重设内容区宽度（窗口实际宽与上限取小者）。"""
        try:
            win_w = self._outer.winfo_width()
        except (AttributeError, tk.TclError):
            return
        if win_w > 1:
            self._content.place_configure(
                width=min(win_w, self._content_max_w()))

    def _sync_layout(self, force_measure=True):
        """重建卡片后的整体同步：测量 → 定内容宽 → 定列数 → 重排。

        force_measure=False 用于「卡片控件未变、只是文案/方向变了」的场景
        （切换换算方向）：此时测量结果必然与上次一致，直接复用（见
        _measure_cards 的缓存说明）。
        """
        self._measure_cards(force=force_measure)
        self._apply_content_width()
        canvas_w = self.card_canvas.winfo_width()
        if canvas_w > 1:
            self._cols = self._desired_cols(canvas_w)
        self._layout_cards()
        self._update_scrollbar()

    def _schedule_relayout(self, canvas_w):
        """窗口宽度变化时按需重排列数（去抖 80ms）。

        去抖只为防止拖拽中列数在阈值边界来回翻转（重排本身开销很小），
        80ms 足够吸收边界抖动，同时列数切换接近跟手。
        """
        cols = self._desired_cols(canvas_w)
        if cols == self._cols:
            return
        if self._relayout_job is not None:
            self.after_cancel(self._relayout_job)
        self._relayout_job = self.after(80, lambda: self._apply_cols(cols))

    def _apply_cols(self, cols):
        self._relayout_job = None
        if cols == self._cols or not self.winfo_exists():
            return
        if self._drag and self._drag.get("started"):
            return    # 拖拽中不重排列数，避免与离散重排打架；松开时补评估
        self._cols = cols
        self._morph_relayout()
        self._update_scrollbar()

    # ---------- 列数过渡动画（FLIP 式） ----------
    # 原理：先记录每张卡的当前几何（起点），按新列数 grid 重排并实测
    # 目标几何（终点），再临时改用 place 从起点向终点做缓动插值，结束
    # 时还原 grid。起点即真实位置 → 无跳变；插值平滑 → 无闪烁；期间
    # 冻结 cards_frame 高度 → 滚动条不跳动；快速连续变列时从「当前
    # 插值位置」继续动画，不重置、不重叠卡死。

    RELAYOUT_MS = 220      # 过渡总时长（ms，区间 200~230）
    RELAYOUT_STEPS = 11    # 步数；单步 = max(12, 220 // 11) = 20ms（≥12ms 下限）

    def _morph_cleanup(self):
        """动画中断/异常后的兜底：解冻高度、全部卡片归还 grid。

        morph 链被打断后若不清理，cards_frame 会残留显式冻结高度
        （scrollregion 虚高 → 滚动后视口落进卡片下方的空白区，表现为
        「大面积空白」），卡片也可能滞留在 place 中间态不再跟随布局。
        """
        self._morph_job = None
        self.cards_frame.configure(height=0)   # 交还 grid 按内容计算高度
        self._layout_cards()                   # grid 接管（自动解除 place）
        try:
            self.update_idletasks()
        except tk.TclError:
            return
        self._update_scrollbar()

    def _morph_relayout(self):
        if not self.cards:
            return
        # 动画进行中再次变列：先取消旧链。旧链可能正 place 在半途且
        # 高度被冻结——立即归还 grid（起点几何在取消前已无从续接，直接
        # 从 grid 位置重新出发，宁可轻微跳变也不残留中间态）。
        if getattr(self, "_morph_job", None) is not None:
            try:
                self.after_cancel(self._morph_job)
            except Exception:
                pass
            self._morph_job = None
            self.cards_frame.configure(height=0)
            self._layout_cards()

        cards = [self.cards[c.code]["card"] for c in self._selected_curs]
        try:
            start = {w: (w.winfo_x(), w.winfo_y(),
                         w.winfo_width(), w.winfo_height()) for w in cards}
        except tk.TclError:
            self._morph_cleanup()    # 卡片已销毁等异常：不留冻结/中间态
            return

        self._layout_cards()          # 新列数 grid（place 会被 grid 接管）
        self.update_idletasks()       # 让 grid 立即算出目标几何（仅此一次测量）
        anim = []
        for w in cards:
            try:
                t = (w.winfo_x(), w.winfo_y(),
                     w.winfo_width(), w.winfo_height())
            except tk.TclError:
                continue
            if t != start[w]:
                anim.append((w, start[w], t))
        if not anim:
            self._update_scrollbar()
            return    # 起点即终点：grid 已是终态（高度已在上方归还）

        # 减少动画：不做分步插值，网格已是终态（_layout_cards 已执行），
        # 只需同步一次滚动条即可——终态与带动画完全一致。
        if _reduced_motion():
            self._update_scrollbar()
            return

        # 先把所有卡片 place 到起点几何（place 接管会自动解除 grid），
        # 保证动画首帧就在原位——不先落位会让卡片在目标位置闪现一帧
        # 再跳回起点附近，产生肉眼可见的往复跳动
        for w, s, tg in anim:
            try:
                w.place(x=s[0], y=s[1], width=s[2], height=s[3])
            except tk.TclError:
                return

        # 动画期间冻结框架高度：place 管理的子控件不贡献请求高度，
        # 不冻结会让 cards_frame 瞬间塌缩、滚动条跳动
        frozen_h = self.cards_frame.winfo_height()
        self.cards_frame.configure(height=frozen_h)

        step_ms = max(12, self.RELAYOUT_MS // self.RELAYOUT_STEPS)

        def finish():
            """收尾（幂等）：先还原 grid 并强制算好几何，再清 job。

            缺陷 A 修复要点：让「最后一帧就是 grid 帧」。旧实现最后一帧
            先把卡片 place 到实测目标几何、随后才 configure(height=0) +
            _layout_cards()——place 像素几何与 grid 重算几何不保证逐像素
            相等（padx=7、列均分取整、sticky 分配都会引入 1~2px 差异），
            且两条命令之间存在一次可被重绘的窗口，可能产生可见抖动。
            现改为：动画最后一帧就地把 grid 还原（configure height=0 +
            _layout_cards），并 update_idletasks() 保证 grid 几何已算出，
            全程不再 place → 不存在「place 帧 ≠ grid 帧」的错位帧。
            """
            self._morph_job = None
            self.cards_frame.configure(height=0)   # 交还 grid 按内容计算高度
            self._layout_cards()                   # grid 接管（自动解除 place）
            try:
                self.update_idletasks()            # 让 grid 几何立即落定
            except tk.TclError:
                return
            self._update_scrollbar()

        def step(i):
            # 采样 t = (i+1)/steps（i 从 0 到 steps-1）；ease-out，首帧即有
            # 进度（无空转帧），末次 t=1.0 → e 精确 = 1.0
            t = (i + 1) / self.RELAYOUT_STEPS
            e = _ease_out_cubic(t)
            # 逐帧只做 place 更新：测量类调用只在动画开始前做一次
            for w, s, tg in anim:
                try:
                    w.place(x=round(s[0] + (tg[0] - s[0]) * e),
                            y=round(s[1] + (tg[1] - s[1]) * e),
                            width=round(s[2] + (tg[2] - s[2]) * e),
                            height=round(s[3] + (tg[3] - s[3]) * e))
                except tk.TclError:
                    self._morph_cleanup()    # 不留 place 中间态/冻结高度
                    return
            if i + 1 < self.RELAYOUT_STEPS:
                self._morph_job = self.after(step_ms,
                                             lambda: step(i + 1))
            else:
                # 末次（t=1.0, e=1.0）：不再 place，直接以 grid 帧收尾
                finish()

        self._morph_job = self.after(step_ms, lambda: step(0))

    def _layout_cards(self):
        """将当前卡片按 _cols 列用 grid 重排（行优先），列宽均分、同行等高。"""
        for info in self.cards.values():
            info["card"].grid_forget()
        # 只给使用中的列配重；空列必须清零权重，否则 grid 仍会把宽度
        # 均分给带权重的空列，导致缩窗后卡片收不满（实测踩坑）。
        for col in range(MAX_COLS):
            if col < self._cols:
                self.cards_frame.columnconfigure(col, weight=1,
                                                 uniform="cardcol")
            else:
                self.cards_frame.columnconfigure(col, weight=0, uniform="")
        for i, cur in enumerate(self._selected_curs):
            self.cards[cur.code]["card"].grid(
                row=i // self._cols, column=i % self._cols,
                sticky="nsew", padx=7, pady=7)

    # ---------- 卡片拖拽排序 ----------
    # 交互：按住卡片移动 ≥DRAG_THRESHOLD 像素进入拖拽。被拖卡全程可见：
    # 不隐去（用户反馈）、也不跟随鼠标（place 连续移动的窗口在
    # Tk/Windows 下旧位置重绘滞后会留拖影，同为用户反馈）——只加红色
    # 描边高亮表示「抓起」；鼠标越过其它卡片矩形时，全部卡片（含被
    # 拖卡）按新顺序离散重排（grid 改槽位，全程无连续移动的窗口），
    # 被拖卡随即出现在新槽位。松开恢复描边并保存顺序（selected 字段
    # 本身即顺序）。阈值内的按下-松开不进入拖拽，双击复制不受影响。
    # 注意：顺序必须同时写 _selected 与 _selected_curs——_layout_cards/
    # morph 按 _selected_curs 排布，只改 _selected 的话，拖拽后一次
    # 窗口缩放就会把视觉顺序打回旧序（探针实测踩坑）。

    def _drag_press(self, _e, code):
        """按下卡片：仅记录候选拖拽信息，移动超阈值后才真正进入拖拽。"""
        if len(self._selected) < 2 or self._cols < 1:
            return    # 只有一张卡无从排序
        self._drag = {"code": code, "x_root": _e.x_root, "y_root": _e.y_root,
                      "started": False}

    def _drag_motion(self, e, code):
        d = self._drag
        if not d or d["code"] != code:
            return
        if not d["started"]:
            if (abs(e.x_root - d["x_root"]) < DRAG_THRESHOLD
                    and abs(e.y_root - d["y_root"]) < DRAG_THRESHOLD):
                return
            self._drag_lift()
            d["started"] = True
        d["y_root"] = e.y_root    # 供边缘自动滚动读取最新位置
        self._drag_move(e)
        if self._drag_scroll_job is None:
            self._drag_scroll_job = self.after(40, self._drag_autoscroll)

    def _drag_lift(self):
        """进入拖拽：被拖卡红色描边高亮表示「抓起」，位置原地不动。"""
        self.cards[self._drag["code"]]["card"].config(
            highlightthickness=2, highlightbackground=COLOR_ACCENT,
            highlightcolor=COLOR_ACCENT)

    def _drag_move(self, e):
        """鼠标越过其它卡片矩形时更新顺序，全部卡片离散重排到新槽位。"""
        d = self._drag
        frame = self.cards_frame
        mx = e.x_root - frame.winfo_rootx()
        my = e.y_root - frame.winfo_rooty()
        cur_i = self._selected.index(d["code"])
        for i, code in enumerate(self._selected):
            if code == d["code"]:
                continue
            w = self.cards[code]["card"]
            if (w.winfo_x() <= mx < w.winfo_x() + w.winfo_width()
                    and w.winfo_y() <= my < w.winfo_y() + w.winfo_height()):
                if i != cur_i:
                    self._drag_reorder(i)
                break

    def _drag_reorder(self, i):
        """两份顺序列表同步重排，并把全部卡片 grid 到新槽位（离散跳变）。

        _selected_curs 必须与 _selected 同步：_layout_cards/morph 都按
        _selected_curs 排布，漏掉它则拖拽结果在下次窗口缩放时被打回。
        """
        code = self._drag["code"]
        self._selected.remove(code)
        self._selected.insert(i, code)
        cur = next(c for c in self._selected_curs if c.code == code)
        self._selected_curs.remove(cur)
        self._selected_curs.insert(i, cur)
        self._layout_cards()
        self.update_idletasks()    # 立即算好新几何，下一次命中判定才准确

    def _drag_autoscroll(self):
        """拖到卡片区视口上/下边缘时持续滚动（40ms 步进，离开即停）。

        注意用的是 `cv.yview()`——Canvas **没有** `winfo_yview()` 方法
        （曾经误写成 winfo_yview，导致每次开始拖拽都抛 AttributeError：
        自动滚动完全失效，且每次拖拽往 crash.log 写一条记录）。回调整体
        包 try：控件/窗口已销毁时静默停链，不污染 crash.log。
        """
        try:
            d = self._drag
            if not (d and d.get("started")):
                self._drag_scroll_job = None
                return
            cv = self.card_canvas
            my = d["y_root"] - cv.winfo_rooty()
            vh = cv.winfo_height()
            top, bottom = cv.yview()
            if my < 28 and top > 0:
                cv.yview_scroll(-16, "pixels")
            elif my > vh - 28 and bottom < 1:
                cv.yview_scroll(16, "pixels")
            self._drag_scroll_job = self.after(40, self._drag_autoscroll)
        except (tk.TclError, AttributeError):
            self._drag_scroll_job = None

    def _drag_release(self, _e, code):
        """松开：恢复描边、保存新顺序，并补评估拖拽期间错过的列数变化。"""
        d = self._drag
        self._drag = None
        if self._drag_scroll_job is not None:
            self.after_cancel(self._drag_scroll_job)
            self._drag_scroll_job = None
        if not d or d["code"] != code or not d.get("started"):
            return
        self.cards[code]["card"].config(
            highlightthickness=1, highlightbackground=COLOR_BORDER,
            highlightcolor=COLOR_BORDER)
        _save_config(self._selected, self.geometry(), self._inverse)
        # 拖拽中 _apply_cols 会给列数变化让路；松开后补一次评估
        self._schedule_relayout(self.card_canvas.winfo_width())

    # ---------- 滚动 ----------

    def _scroll_yview(self, *args):
        self.card_canvas.yview(*args)

    def _on_canvas_scroll(self, first, _last):
        self.sb.set(first, _last)

    def _on_cards_resize(self, _e):
        self.card_canvas.configure(scrollregion=self.card_canvas.bbox("all"))
        self._update_scrollbar()

    def _update_scrollbar(self):
        """内容不超过可视高度时隐藏滚动条（避免常驻灰条占宽、显冗余）。"""
        try:
            bb = self.card_canvas.bbox("all")
            content_h = (bb[3] - bb[1]) if bb else 0
            view_h = self.card_canvas.winfo_height()
            need = view_h > 1 and content_h > view_h + 1
        except tk.TclError:
            return
        if need == self._sb_visible:
            return
        self._sb_visible = need
        if need:
            # before= 保证滚动条重新插回画布右侧，且画布让出相应宽度
            self.sb.pack(before=self.card_canvas, side="right", fill="y")
        else:
            self.sb.pack_forget()
            self.card_canvas.yview_moveto(0)

    def _on_canvas_resize(self, e):
        self.card_canvas.itemconfigure(self._cards_win, width=e.width)
        self._schedule_relayout(e.width)
        self._update_scrollbar()

    def _on_outer_resize(self, e):
        """窗口尺寸变化：内容区限最大宽度并水平居中。"""
        self._content.place_configure(width=min(e.width, self._content_max_w()))
        self._update_scrollbar()

    def _on_mousewheel(self, e):
        # 模态打开时锁定背景滚动：遮罩已物理拦截滚轮，此处为第二道保险
        # （覆盖遮罩未覆盖到、或事件被其它路径转发进来的情况）
        if getattr(self, "_modal_open", False):
            return
        # 内容不满一屏（滚动条隐藏）时无需滚动，避免多余重绘
        if not self._sb_visible:
            return
        self.card_canvas.yview_scroll(int(-e.delta / 120), "units")

    # ---------- 窗口尺寸 ----------

    def _fit_window(self):
        """首次启动时按默认（3 个币种）内容尺寸设定窗口。

        仅在无尺寸记忆的首次启动调用一次：之后窗口尺寸保持固定，
        币种增减不再调整，多出的卡片通过滚动条/滚轮查看。
        """
        try:
            self.update_idletasks()
            bb = self.card_canvas.bbox("all")
            content_h = (bb[3] - bb[1]) if bb else 0
            # 固定部分：标题区 + 状态栏 + 外边距（不含卡片画布）
            fixed = self.winfo_height() - self.card_canvas.winfo_height()
            total = fixed + content_h
            # 上限按虚拟桌面高度算：主窗口可能停在比主屏更矮的副屏上，
            # 只按主屏比例会让窗口高出副屏可视范围
            total = min(total, int(_screen_rect(self)[3] * 0.85))
            total = max(total, 380)
            self.geometry("%dx%d" % (self.winfo_width(), total))
            self._update_scrollbar()
        except tk.TclError:
            pass

    # ---------- 关闭 / 托盘 ----------

    def _on_close(self):
        """点击窗口关闭按钮：弹窗让用户选择「最小化到托盘」或「直接退出」。"""
        if self._close_dlg is not None and self._close_dlg.winfo_exists():
            self._close_dlg.lift()
            self._close_dlg.focus_force()
            return
        self._open_close_dialog()

    def _open_close_dialog(self):
        """关闭方式选择对话框（模态，与主界面同一套设计语言）。

        恢复最初的「一段说明 + 一排两个按钮」布局：
        - 正文：一句说明讲清两个动作的后果（原文案，未改写）；
        - 按钮区：主按钮（红底）「最小化到系统托盘」居右，与主界面底部
          「刷新」主按钮位置和规范一致；幽灵次按钮「直接退出」在其左侧；
        - 默认动作（Enter）落在安全的「最小化到系统托盘」上；Esc / 点击
          遮罩 / 关闭对话框同样按它处理，不会造成误退出。

        按钮样式不另行发明，全部取主界面既有按钮规范（主按钮中行红 +
        F_BTN_MAIN；次按钮白底 + 1px 描边 + hover 灰底）。
        """
        body = ("「最小化到系统托盘」后程序在后台保持运行，"
                "点击托盘图标可随时打开并自动更新牌价；"
                "「直接退出」将关闭程序。")

        def tray():
            self._minimize_to_tray()

        def quit_now():
            self._quit_app()

        modal = Modal(
            self, win_title="关闭程序", content=body,
            # 书写顺序 = 从左到右：次按钮在左，主按钮在右
            buttons=[
                {"text": "直接退出", "style": "ghost", "value": "quit"},
                {"text": "最小化到系统托盘", "style": "primary",
                 "default": True, "value": "tray"},
            ],
            dismissible=True, overlay=True,
            on_result=lambda v: tray() if v == "tray" else quit_now(),
            on_close=tray,
            on_finish=lambda: self._clear_close_refs())
        self._close_modal = modal

        # 注意顺序：win 由 show() 内部创建，必须在 show() 之后取引用
        modal.show()
        self._close_dlg = modal.win     # 兼容既有 winfo_exists() 判活逻辑
        # 键盘：Enter 触发主按钮（最小化到托盘）、Esc 关闭（同样走托盘）。
        # 初始焦点保持在窗口上而不落到按钮——tk.Button 获得焦点会画出虚线
        # 焦点框，与全站「无焦点框」的扁平风格不符；用户按 Tab 才显式聚焦。
        self._close_dlg.focus_set()


    def _save_geometry_config(self):
        """关闭/最小化前保存窗口尺寸与币种选择，供下次启动恢复。"""
        try:
            _save_config(self._selected, self.geometry(), self._inverse)
        except Exception:
            pass

    def _minimize_to_tray(self):
        """隐藏主窗口并注册托盘图标（后台保持运行）。"""
        self._close_currency_panel()
        self._save_geometry_config()
        self.withdraw()
        if not self._tray.show():
            # 托盘注册失败（极少见）：退回普通最小化，保证窗口不丢
            self.iconify()
            return
        if not self._tray_notified:
            self._tray.notify(
                APP_TITLE,
                "程序已最小化到系统托盘，点击图标可重新打开并自动更新牌价。")
            self._tray_notified = True

    def _tray_restore(self):
        """从托盘恢复窗口：重新显示并自动刷新一次最新牌价。

        触发点：左键单击托盘图标、托盘菜单「显示主窗口」、以及二次启动
        实例的唤醒消息（WM_APP_TRAY，见 _ensure_single_instance）。
        """
        try:
            if not self.winfo_exists():
                return
            self._close_dlg_check()
            self.deiconify()
            self.lift()
            self.focus_force()
            self.refresh()
        except tk.TclError:
            pass

    def _clear_close_refs(self):
        """清理关闭弹窗的引用（Modal 销毁后回调）。"""
        self._close_dlg = None
        self._close_modal = None

    def _close_dlg_check(self):
        """恢复窗口前关闭可能残留的关闭方式选择对话框（含遮罩层）。"""
        modal = getattr(self, "_close_modal", None)
        if modal is not None:
            modal.close(cancel_cb=True)
        self._clear_close_refs()

    def _quit_app(self):
        """退出程序（对话框「直接退出」/ 托盘菜单「退出程序」）。"""
        self._save_geometry_config()
        self.destroy()

    def destroy(self):
        """退出前清理托盘与模态资源：删除图标、还原窗口过程，避免残留。

        模态遮罩是独立窗口，若主窗口先销毁而遮罩残留，会留下一个盖住
        桌面的黑层 —— 退出路径必须显式关闭模态。
        """
        modal = getattr(self, "_close_modal", None)
        if modal is not None:
            modal.close(cancel_cb=True)
        if getattr(self, "_tray", None) is not None:
            self._tray.shutdown()
        super().destroy()

    # ---------- 换算方向 ----------
    # 方向只影响「怎么展示」，不影响「取什么数据」：汇率仅在 refresh() 时抓取
    # 一次并落在 cards[code]["rate1"]，切换方向只用 1/汇率 重算文案，不发起
    # 任何请求、不重建卡片、不清空已有数据（因此也不会闪「--」）。

    def _build_direction_toggle(self, parent):
        """换算方向切换行：右=方向按钮（文案即当前方向），说明标签紧贴
        按钮左侧（用户反馈：分居两端显得松散，挨着更聚拢）。

        pack 顺序：按钮先 pack(side="right") 占最右，标签随后
        pack(side="right") 落在按钮左侧，padx=(0, 8) 留出呼吸间隙。
        按钮文案本身就把两边货币都写出来（「外币 ⇆ 人民币」/
        「人民币 ⇆ 外币」），配合 ⇄ 表明可点击对调；两种文案等长，
        切换时按钮宽度不变，标签不会被推动。
        """
        row = tk.Frame(parent, bg=COLOR_BG)
        row.pack(fill="x", pady=(0, 10))
        self.btn_dir = tk.Button(
            row, command=self._toggle_direction, font=F_BTN_SMALL,
            relief="flat", padx=14, pady=5, cursor="hand2", takefocus=0,
            bd=0, highlightthickness=1)
        self.btn_dir.pack(side="right")
        tk.Label(row, text="换算方向", bg=COLOR_BG, fg=COLOR_SUB,
                 font=F_SUBTITLE).pack(side="right", padx=(0, 8))
        self._render_dir_button()

    def _render_dir_button(self):
        """按当前方向刷新按钮文案与配色（含 hover 端点与描边）。

        hover 端点随状态变化，而 _bind_hover 的端点在绑定时即固化，所以这里
        先用空脚本解绑再重新绑定——否则每切换一次就多挂一条 Enter/Leave
        脚本，多条 go() 互相取消，过渡链只剩最后一帧（表现为颜色瞬跳）。
        """
        btn = self.btn_dir
        if self._inverse:
            text = DIR_TEXT_REV
            bg, fg = COLOR_DIR_ON_BG, COLOR_DIR_ON_TEXT
            abg, afg = COLOR_DIR_ON_HOVER, COLOR_ACCENT_DARK
            border = COLOR_DIR_ON_BORDER
        else:
            text = DIR_TEXT_FWD
            bg, fg = COLOR_BTN, COLOR_BTN_TEXT
            abg, afg = COLOR_BTN_ACTIVE, COLOR_BTN_TEXT_ACTIVE
            border = COLOR_BORDER
        # bg 与 activebackground 必须一起给：tk.Button 悬停时渲染的是
        # activebackground，只改 bg 会在鼠标进出时翻转颜色。
        btn.config(text=text, bg=bg, fg=fg, activebackground=abg,
                   activeforeground=afg, highlightbackground=border,
                   highlightcolor=border)
        btn.bind("<Enter>", "")      # 空脚本 = 解除既有绑定
        btn.bind("<Leave>", "")
        _bind_hover(btn, (bg, fg), (abg, afg))

    def _toggle_direction(self):
        """切换换算方向：重绘卡片文案 + 重评估列数，不抓取、不重建。"""
        self._inverse = not self._inverse
        self._render_dir_button()
        for code in self.cards:
            self._render_card_value(code)
        # 币种名/符号文案变了 → 卡片自然宽度随之变化，必须重排，否则列数
        # 判定沿用旧宽度，可能出现卡片被压窄截字。但测量口径恒为正向、
        # 与方向无关，故可直接复用上次测量结果（force_measure=False）。
        self._sync_layout(force_measure=False)
        self._status_base = ("已切换：1 人民币 = X 外币" if self._inverse
                             else "已切换：1 外币 = X 人民币")
        self._status_fg_state = "sub"
        self._render_status()
        self._save_geometry_config()

    def _display_rate(self, code):
        """当前换算方向下的展示值；无数据或汇率为 0 时返回 None。

        单点定义换算规则，供卡片渲染与复制共用，避免两处算法漂移。
        """
        r = self.cards[code].get("rate1")
        if not r:                     # None / 0 都不做除法
            return None
        return (1.0 / r) if self._inverse else r

    def _render_card_value(self, code):
        """按当前方向渲染单张卡片的符号、名称、单位与数值。

        头部始终展示「基准货币」（等号左侧），与原有语义一致：
        正向 → 符号/名称 = 外币，单位行「1 外币 =」，尾缀「人民币」；
        反向 → 符号/名称 = 人民币，单位行「1 人民币 =」，尾缀 = 外币名。
        """
        c = self.cards[code]
        cur = c["cur"]
        base, quote = (BASE_CNY, cur) if self._inverse else (cur, BASE_CNY)
        c["sym"].config(text=base.symbol)
        c["title"].config(text="%s（%s）" % (base.display, base.code))
        c["unit_from"].config(text="1 %s =" % base.display)
        c["unit_to"].config(text=quote.display)
        c["value"].config(text=fmt(self._display_rate(code)))

    # ---------- 卡片统一排版规范 ----------
    # 所有卡片共用同一组常量与对齐规则，保证任意币种、任意数据状态下
    # 排版一致：
    # - 结构固定：头部行（符号+名称｜来源胶囊+复制）→ 分隔线 → 数值区
    #   两行（小灰字「1 X =」；红色大数值+「Y」基线对齐同行）。X/Y 由换算
    #   方向决定（正向 外币/人民币、反向 人民币/外币），见 _render_card_value；
    #   头部展示的始终是等号左侧的基准货币。
    #   单位独占一行后数值行不再左右争抢空间，窄卡下不拥挤；同排不同
    #   字号的文字用 anchor="s" 底边对齐，共用一条视觉基线；
    # - 左右分区：头部行左侧是主体信息（币种），右侧是元信息/操作
    #   （胶囊、复制），各元素位置不随币种名称长度变化；
    # - 层级分明：币种名深色、数值红色大字（视觉焦点）、单位与次级
    #   文字灰色弱化，主次一眼可辨；
    # - 数值列宽固定 8 字符（等宽字体）：容纳 fmt 对 <0.01 汇率的 8 字符
    #   输出（如 KRW「0.005200」），且数据从「--」变为数值、6~8 字符
    #   长度变化时「人民币」位置保持不动。

    def _build_card(self, parent, cur):
        code, disp = cur.code, cur.display
        card = tk.Frame(parent, bg=COLOR_CARD, highlightbackground=COLOR_BORDER,
                        highlightthickness=1, padx=16, pady=14)
        # 不在此处 pack/grid：位置由 _layout_cards 按响应式列数统一排布

        # 头部行：左=货币符号+名称（主体）；右=来源胶囊+复制（元信息/操作）
        top = tk.Frame(card, bg=COLOR_CARD)
        top.pack(fill="x")
        # 符号标签不设固定宽度：₽、CHF、Mex$ 等宽符号/多字符符号
        # 在 width=2 下会被截断显示不全，改为按内容自适应宽度
        sym = tk.Label(top, text=cur.symbol, bg=COLOR_CARD, fg=COLOR_ACCENT,
                       font=F_CARD_SYMBOL, anchor="s")
        sym.pack(side="left", anchor="s", padx=(0, 6))
        title = tk.Label(top, text="%s（%s）" % (disp, code), bg=COLOR_CARD,
                         fg=COLOR_TEXT, font=F_CARD_TITLE, anchor="s")
        title.pack(side="left", anchor="s", pady=(0, 1))
        btn_copy = tk.Button(top, text="复制", command=lambda c=code: self._copy(c),
                             font=F_BTN_SMALL, bg=COLOR_CARD, fg=COLOR_BTN_TEXT,
                             activebackground=COLOR_BTN_ACTIVE,
                             activeforeground=COLOR_BTN_TEXT_ACTIVE,
                             relief="flat", padx=10, pady=3, cursor="hand2",
                             takefocus=0, bd=0,
                             highlightthickness=1,
                             highlightbackground=COLOR_BORDER,
                             highlightcolor=COLOR_BORDER)
        btn_copy.pack(side="right", anchor="s")
        btn_copy.flashing = False   # 「已复制 ✓」反馈期间 hover 不变色
        _bind_hover(btn_copy,
                    (COLOR_CARD, COLOR_BTN_TEXT),
                    (COLOR_BTN_ACTIVE, COLOR_BTN_TEXT_ACTIVE),
                    guard=lambda w: not getattr(w, "flashing", False))
        tag = tk.Label(top, text="", bg=COLOR_CARD, fg=COLOR_SUB,
                       font=F_TAG, padx=6, pady=2, anchor="s")
        tag.pack(side="right", anchor="s", padx=(0, 6), pady=(0, 1))

        # 分隔线：头部与数值区之间（上下等距）
        tk.Frame(card, bg=COLOR_DIVIDER, height=1).pack(fill="x", pady=11)

        # 数值区（两行结构）：上=灰色小字单位「1 外币 =」；下=红色大数值
        # +「人民币」基线对齐。把单位移出数值行后，数值行左右不再争抢
        # 空间（双列窄卡下此前左侧单位+数值与右侧「人民币」同排，显得
        # 拥挤），大数值独占一行更有呼吸感；「人民币」紧跟数值组成完整
        # 的价格读数。数值列宽固定 8 字符，刷新时「人民币」位置不动。
        # 注：曾试过整体右对齐的「左标签 / 右读数」版式，实际观感不如
        # 左对齐自然（用户反馈），已回退——不要再改回去。
        val_area = tk.Frame(card, bg=COLOR_CARD)
        val_area.pack(fill="x")
        lbl_cur = tk.Label(val_area, text="1 %s =" % disp, bg=COLOR_CARD,
                           fg=COLOR_SUB, font=F_UNIT, anchor="w")
        lbl_cur.pack(anchor="w", pady=(0, 2))
        row = tk.Frame(val_area, bg=COLOR_CARD)
        row.pack(anchor="w")
        value = tk.Label(row, text="--", bg=COLOR_CARD, fg=COLOR_ACCENT,
                         font=F_VALUE, width=8, anchor="w")
        value.pack(side="left")
        lbl_cny = tk.Label(row, text="人民币", bg=COLOR_CARD,
                           fg=COLOR_SUB, font=F_UNIT, anchor="s")
        lbl_cny.pack(side="left", anchor="s", pady=(0, 4), padx=(0, 8))

        # 双击任意位置复制；整卡可点区域统一 hand2 指针示意可交互。
        # btn_copy 不参与双击绑定：其 command 在单击释放时已触发，
        # 双击会导致 command×2 + 双击绑定×1 共 3 次重复复制。
        # 拖拽排序：按住并移动 ≥DRAG_THRESHOLD 像素进入拖拽（松开即落位
        # 并保存顺序）；阈值内的按下-松开不进入拖拽，不影响双击复制。
        # btn_copy 不绑拖拽（它有自己的单击 command，避免误拖）。
        for w in (card, top, sym, title, tag, val_area, row, lbl_cur, value,
                  lbl_cny):
            w.config(cursor="hand2")
            w.bind("<Double-Button-1>", lambda e, c=code: self._copy(c))
            w.bind("<Button-1>", lambda e, c=code: self._drag_press(e, c))
            w.bind("<B1-Motion>", lambda e, c=code: self._drag_motion(e, c))
            w.bind("<ButtonRelease-1>",
                   lambda e, c=code: self._drag_release(e, c))

        # sym/title/unit_from/unit_to 一并登记：切换换算方向时这四个文案
        # 都要跟着换（货币名称、币种符号、等号两侧单位），value 随之重算。
        return {"card": card, "tag": tag, "value": value,
                "btn_copy": btn_copy, "rate1": None, "flash_after": None,
                "cur": cur, "sym": sym, "title": title,
                "unit_from": lbl_cur, "unit_to": lbl_cny}

    # ---------- 数据刷新 ----------

    def _render_session(self):
        """用会话内已有数据即时渲染卡片（重建卡片后调用）。

        只填充当前尚无数据的卡片（rate1 is None，即新建卡片）；
        渲染结果标注「上次数据」，随后的 refresh 成功后会被真实来源覆盖。
        """
        for code, c in self.cards.items():
            d = self._session.get(code)
            if d and c["rate1"] is None:
                c["rate1"] = d["rate1"]
                c["tag"].config(text="◷ 上次数据", fg=COLOR_TAG_STALE_FG,
                                bg=COLOR_TAG_STALE_BG)
            # 无数据的卡片也要按当前方向重绘：新建卡片默认按正向写死了
            # 符号/名称/单位文案，若启动时记忆的是反向，必须在此纠正
            self._render_card_value(code)

    def refresh(self):
        if self._fetching:
            return
        if not self._selected:
            # 空选择（全部取消）：不发无意义的网络请求，状态栏给出指引
            self._status_base = "未选择币种：点击右上角「币种」添加要显示的货币"
            self._status_fg_state = "sub"
            self._render_status()
            return
        self._fetching = True
        # 记录本次抓取对应的选择快照：抓取期间用户切换币种时，
        # _apply_result 据此检测差异并自动补刷（见其末尾）
        self._requested = list(self._selected)
        self.btn_refresh.config(state="disabled", text="刷新中…",
                                bg=COLOR_REFRESH_DISABLED_BG)
        self._status_base = "正在获取最新牌价，请稍候…"
        self._status_fg_state = "busy"
        self._render_status()
        threading.Thread(target=self._worker, daemon=True).start()
        self._kick_poll()    # 空闲档 → 抓取档，结果尽快上屏

    def _worker(self):
        try:
            # 传快照（refresh() 里已固定的 _requested）：避免工作线程读取时
            # UI 线程恰好增删 _selected，出现撕裂视图 / 补刷误判
            rows, err = fetch_all(list(self._requested))
            self.result_q.put({"rows": rows, "error": err})
        except Exception as e:
            logging.exception("抓取工作线程异常")
            _log_crash("worker", e)
            self.result_q.put({"rows": {}, "error": "发生异常：%s" % _brief(e)})
        except BaseException as e:                       # noqa: BLE001
            # KeyboardInterrupt/SystemExit 等非 Exception 基类异常：若不兜底，
            # result_q 收不到任何消息 → _fetching 永久为 True → 刷新按钮永久
            # 禁用、后续 refresh() 全部早退（界面「卡死」）。这里保证一定投递。
            _log_crash("worker-base", e)
            self.result_q.put({"rows": {},
                               "error": "发生异常：%s" % _brief(e)})

    def _poll_queue(self):
        """轮询工作线程结果队列（单链自续，间隔自适应）。

        间隔：抓取中 80ms（结果尽快上屏），空闲 400ms（常驻托盘时把无意义
        的 UI 线程唤醒从 10 次/秒降到 2.5 次/秒）。

        **先排下一 tick、再 drain**：drain 里的 _apply_result 可能触发自动
        补刷（选择变了 → refresh() → _kick_poll()），若此刻 _poll_job 为
        None，_kick_poll 会另排一条链、随后本函数再排一条并覆盖句柄，孤儿链
        从此永久自续 —— 轮询频率翻倍（实测 2.0×）。先排后 drain 保证
        drain 期间 _poll_job 恒非空，_kick_poll 只会「取消+重排」同一条链。
        """
        self._poll_job = None
        try:
            self._poll_job = self.after(80 if self._fetching else 400,
                                        self._poll_queue)
        except tk.TclError:
            return          # 窗口已销毁：链条自然终止
        try:
            while True:
                self._apply_result(self.result_q.get_nowait())
        except queue.Empty:
            pass

    def _kick_poll(self):
        """把空闲档（400ms）立即切到抓取档（80ms），保证结果尽快上屏。

        取消当前 tick 后重排——**不新增第二条链**（见 _poll_queue 注释）。
        """
        if getattr(self, "_poll_job", None) is not None:
            try:
                self.after_cancel(self._poll_job)
            except Exception:
                pass
        try:
            self._poll_job = self.after(80, self._poll_queue)
        except tk.TclError:
            self._poll_job = None

    def _kick_poll(self):
        """把空闲档（400ms）立即切到抓取档（80ms），保证结果尽快上屏。"""
        if getattr(self, "_poll_job", None) is not None:
            try:
                self.after_cancel(self._poll_job)
            except Exception:
                pass
        self._poll_job = self.after(80, self._poll_queue)

    def report_callback_exception(self, exc, val, tb):
        """Tk 主循环回调未捕获异常兜底（覆写 Tk 默认行为）。

        默认实现只写 stderr——--windowed 打包的 exe 下会完全丢失；
        这里落到 crash.log，保证用户侧异常可诊断。
        """
        _log_crash("ui-callback", val if val is not None else exc)

    def _apply_result(self, msg):
        self._fetching = False
        self.btn_refresh.config(state="normal", text="刷 新", bg=COLOR_ACCENT)

        rows = msg.get("rows") or {}
        kept_old = False
        for cur in self._selected_curs:
            c = self.cards[cur.code]
            d = rows.get(cur.code)
            if d:
                self._session[cur.code] = d
                c["rate1"] = d["rate1"]
                if d.get("fallback"):
                    c["tag"].config(text="● 备用源参考", fg=COLOR_TAG_FALLBACK,
                                    bg=COLOR_TAG_FALLBACK_BG)
                else:
                    c["tag"].config(text="● 中行牌价", fg=COLOR_ACCENT,
                                    bg=COLOR_TAG_BOC_BG)
            else:
                if c["rate1"] is None:
                    c["tag"].config(text="", bg=COLOR_CARD)
                else:
                    kept_old = True
                    c["tag"].config(text="◷ 上次数据", fg=COLOR_TAG_STALE_FG,
                                    bg=COLOR_TAG_STALE_BG)
            # 数值与两侧单位文案统一按当前方向重绘（正向/反向只差一次倒数）
            self._render_card_value(cur.code)

        if msg.get("error"):
            self._status_base = msg["error"]
            if kept_old:
                self._status_base += " · 已保留上次成功数据"
            self._status_fg_state = "err"
        else:
            self._status_base = "点击「刷新」或按 F5 可手动更新"
            self._status_fg_state = "sub"

        # 各币种来自同一次抓取，更新时间统一放在元信息行
        upd = ""
        for cur in self._selected_curs:
            d = rows.get(cur.code)
            if d and d.get("time"):
                upd = d["time"]
                break
        self._last_update = _fmt_time(
            upd or datetime.now().strftime("%Y/%m/%d %H:%M:%S"))

        self._render_status()

        # 抓取期间用户切换了币种：在途请求用的是旧快照，新增币种没有数据
        # （卡片停留在「--」）。本次结果落地后自动补一次刷新；
        # refresh() 内部会更新快照，选择不再变化时不会循环。
        if list(self._selected) != self._requested:
            self.refresh()

    # ---------- 其他交互 ----------

    def _on_status_resize(self, event):
        if event.width > 140:
            self.lbl_status.config(wraplength=event.width - 8)

    def _render_status(self):
        state = self._status_fg_state
        fg = _STATUS_DOT.get(state, COLOR_SUB)   # 圆点与文字共用同一配色表
        text = self._status_base
        if self._last_update:
            text = "%s · 更新于 %s" % (text, self._last_update)
        self.lbl_status.config(text=text, fg=fg)
        self.dot_status.itemconfig("dot",
                                   fill=_STATUS_DOT.get(state, COLOR_SUB))

    def _copy(self, code):
        # 复制「屏幕上看到的那个数」：反向时复制 1/汇率，而不是原始牌价
        r = self._display_rate(code)
        if r is None:
            self._status_base = "暂无可复制的数值"
            self._status_fg_state = "warn"
            self._render_status()
            return
        s = fmt(r)
        # 优先用 Windows 原生 API 写入剪贴板（立即物化、任何查看器可读）；
        # 失败（如剪贴板被占用）时回退 tkinter 方案（clear 后先 update 落地）。
        if not _set_clipboard_win(s):
            self.clipboard_clear()
            try:
                self.update()
            except tk.TclError:
                pass
            self.clipboard_append(s)
        disp = self._disp_by_code[code]
        self._status_base = ("已复制：%s（1 人民币 兑%s）" % (s, disp)
                             if self._inverse else
                             "已复制：%s（1 %s 兑人民币）" % (s, disp))
        self._status_fg_state = "ok"
        self._render_status()
        self._flash_copy(code)

    def _flash_copy(self, code):
        c = self.cards[code]
        btn = c["btn_copy"]
        # 复制成功：按钮立即变为淡绿底 + 绿字（瞬时上色保证反馈跟手），
        # 短暂停留后分六步渐隐回常态（6 步 × 35ms = 210ms，线性：Δ 均匀），消除「瞬间
        # 跳回」的生硬感；反馈期间挂起 hover 变色，避免被 hover 规则覆盖。

        # 缺陷 B 修复：收尾统一走幂等的 finish()，负责还原 bg/fg/text 三个
        # 字段并清 flashing。无论「正常走完」还是「中途被打断」都必须经由它，
        # 杜绝两种残留：① 渐隐被 after_cancel 打断时 text 停留在「已复制 ✓」
        # 未还原；② flashing 标记因终态分支未执行而永久卡 True。
        def finish():
            """幂等收尾：还原按钮三字段 + 清 flashing + 释放 job 句柄。"""
            c["flash_after"] = None
            try:
                if btn.winfo_exists():
                    btn.config(text="复制", bg=COLOR_CARD, fg=COLOR_BTN_TEXT)
            except tk.TclError:
                pass
            btn.flashing = False

        # 连续快速复制（上一次渐隐尚未走完）：先把旧链收尾（还原三字段 +
        # 清标记），再从头开始新一轮，保证不出现中间色/错字残留。
        prev = c.get("flash_after")
        if prev is not None:
            try:
                self.after_cancel(prev)
            except Exception:
                pass
            finish()

        btn.flashing = True
        btn.config(text="已复制 ✓", bg=COLOR_OK_SOFT, fg=COLOR_OK)

        # 减少动画档：只去掉「过渡过程」，不移除反馈本身。
        # 若在此处直接 finish()，上色与还原会落在同一个 tick 内——中间没有
        # after 让出事件循环，Tk 来不及重绘，屏幕上一次都不会出现淡绿底，
        # 用户感知为「点了没有任何反应」（本机系统动画关闭时默认走此路径）。
        # 正确做法：瞬时上色 → 停留同样的 FLASH_HOLD_MS → 瞬时还原。
        if _reduced_motion():
            def snap_finish():
                c["flash_after"] = None
                finish()
            c["flash_after"] = self.after(FLASH_HOLD_MS, snap_finish)
            return

        frm = (COLOR_OK_SOFT, COLOR_OK)
        to = (COLOR_CARD, COLOR_BTN_TEXT)

        def fade(i):
            # 采样 t = (i+1)/steps（i 从 0 到 steps-1），末次 t=1.0 → e=1.0，
            # 最后一帧即终态并一并还原 text（不再多跑一帧空转）
            t = (i + 1) / FLASH_FADE_STEPS
            e = FLASH_FADE_EASE(t)         # 线性：Δ 均匀，无中间塌陷/突变
            try:
                if self.winfo_exists() and btn.winfo_exists():
                    btn.config(bg=_mix_color(frm[0], to[0], e),
                               fg=_mix_color(frm[1], to[1], e))
            except tk.TclError:
                finish()
                return
            if i + 1 < FLASH_FADE_STEPS:
                c["flash_after"] = self.after(FLASH_FADE_MS,
                                              lambda: fade(i + 1))
            else:
                finish()                   # 末帧一并还原 bg/fg/text + 清标记

        def restore():
            c["flash_after"] = self.after(FLASH_FADE_MS, lambda: fade(0))

        c["flash_after"] = self.after(FLASH_HOLD_MS, restore)


def _setup_logging():
    """--debug 时写调试日志；程序目录不可写则回退到系统临时目录。"""
    if "--debug" not in sys.argv:
        return
    if getattr(sys, "frozen", False):
        base = os.path.dirname(os.path.abspath(sys.executable))
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        os.path.join(base, "中行汇率换算_debug.log"),
        os.path.join(tempfile.gettempdir(), "中行汇率换算_debug.log"),
    ]
    for name in candidates:
        try:
            logging.basicConfig(filename=name, level=logging.DEBUG,
                                format="%(asctime)s %(levelname)s %(message)s")
            return
        except Exception:
            continue
    logging.basicConfig(level=logging.DEBUG,
                        format="%(asctime)s %(levelname)s %(message)s")


def _ensure_single_instance():
    """单实例：重复启动时唤醒已有窗口并退出，避免开多个窗口。

    句柄说明：
    - 首个实例创建的互斥锁句柄故意不 CloseHandle——须持有到进程结束才能
      维持单实例；进程退出时由操作系统回收，非句柄泄漏。
    - 互斥锁用「Local\\」命名空间（每登录会话独立）：同一用户多会话
      （如远程桌面）各开各的实例，互不误伤；「Global\\」会跨会话误判。
    """
    if "--selftest" in sys.argv:
        return True   # 自动化测试模式不受单实例限制
    # use_last_error=True：ctypes 在两次外部调用之间可能执行内部 Win32 调用
    # 而覆盖进程级 last error，必须用线程本地副本 ctypes.get_last_error()
    # 读取，直接调 kernel32.GetLastError() 不可靠。
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    user32 = ctypes.windll.user32
    # x64 正确性：HANDLE/HWND 为 64 位，restype 默认 c_int 会截断——
    # FindWindowW 返回值截断后可能变成错误的非零值，ShowWindow 会作用到
    # 无关窗口。c_void_p 失败时返回 None，`if hwnd` 判空语义不变。
    kernel32.CreateMutexW.argtypes = [ctypes.wintypes.HANDLE,
                                      ctypes.wintypes.BOOL,
                                      ctypes.wintypes.LPCWSTR]
    kernel32.CreateMutexW.restype = ctypes.wintypes.HANDLE
    user32.FindWindowW.argtypes = [ctypes.wintypes.LPCWSTR,
                                   ctypes.wintypes.LPCWSTR]
    user32.FindWindowW.restype = ctypes.wintypes.HWND
    user32.ShowWindow.argtypes = [ctypes.wintypes.HWND, ctypes.c_int]
    user32.SetForegroundWindow.argtypes = [ctypes.wintypes.HWND]
    kernel32.CreateMutexW(None, False, "Local\\" + APP_TITLE)
    if ctypes.get_last_error() != 183:   # ERROR_ALREADY_EXISTS
        return True
    hwnd = user32.FindWindowW(None, APP_TITLE)
    if hwnd:   # c_void_p：失败返回 None
        user32.ShowWindow(hwnd, 9)        # SW_RESTORE
        user32.SetForegroundWindow(hwnd)
        # 主窗口可能正最小化到托盘（隐藏且 Tk 状态为 withdraw）：投递托盘
        # 激活消息让程序自行恢复窗口并刷新数据（见 App._tray_restore），
        # 避免 ShowWindow 绕过 Tk 造成状态不同步
        user32.PostMessageW(hwnd, WM_APP_TRAY, 0, WM_LBUTTONUP)
    else:
        # 理论上不应发生（mutex 存在则窗口应已创建），兜底提示而非静默退出
        ctypes.windll.user32.MessageBoxW(
            None, "程序已在运行，但未找到其主窗口。\n"
                  "请在任务栏检查或结束旧进程后重试。",
            APP_TITLE, 0x10)   # MB_ICONERROR
    return False


def main():
    _setup_logging()
    if "--insecure" in sys.argv:
        # 显式允许：证书校验失败时降级为不校验证书（见 fetcher.ALLOW_INSECURE）
        fetcher.ALLOW_INSECURE = True
        logging.warning("已开启 --insecure：证书校验失败时将降级为不校验证书")
    if not _ensure_single_instance():
        return
    # 高分屏下界面更清晰：Per-Monitor V2（Win10 1803+），旧系统回退到 System DPI aware。
    # 注意：DPI_AWARENESS_CONTEXT_* 是指针大小的句柄（-4），必须按 c_void_p 传参，
    # 否则在 64 位进程上默认按 32 位 c_int 传参导致调用静默失败、界面发虚。
    user32 = ctypes.windll.user32
    try:
        user32.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
        user32.SetProcessDpiAwarenessContext.restype = ctypes.c_void_p
        if not user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
            raise OSError("SetProcessDpiAwarenessContext 调用失败")
    except Exception:
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
    app = App()
    if "--selftest" in sys.argv:
        app.after(6000, app.destroy)   # 自动化冒烟测试：6 秒后自动退出
    app.mainloop()
    # 线程池收尾：fetcher 的 ThreadPoolExecutor 工作线程为非 daemon，
    # 解释器退出时会 join 仍在进行的网络请求（15s 超时 × 重试，最长约 30s），
    # 导致窗口关闭后进程滞留、期间单实例互斥锁未释放（期间再次启动会被
    # 误判为重复实例）。此时界面已销毁、配置已在 _on_close 落盘、
    # 日志按条即时落盘，直接结束进程安全。
    os._exit(0)


if __name__ == "__main__":
    main()
