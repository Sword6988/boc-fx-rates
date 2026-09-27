# -*- coding: utf-8 -*-
"""外汇现汇买入价查询（Windows 桌面程序）— 界面层。

抓取/解析逻辑见 fetcher.py，程序入口为 boc_fx_rates.py。

界面设计约定：
- 配色集中在 PALETTE 常量区，全部界面仅引用这些常量；
- 字体/字号集中在 FONTS 常量区，形成「标题 > 卡片题 > 数值 > 说明/状态」层级；
- 内容区（标题/卡片/状态栏）限最大宽度并水平居中，窗口拉大时布局合理延展；
- 所有可点击元素统一 hand2 指针、hover 反馈与 takefocus=0（去除虚线焦点框）。
"""

import ctypes
import ctypes.wintypes
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
from config import (
    APP_TITLE,
    _clamp_geometry,
    _config_dir,
    _config_path,
    _load_config,
    _save_config,
)
from currencies import ALL_CURRENCIES, CODE_TO_CURRENCY
from fetcher import (
    _brief,
    _fmt_time,
    fetch_all,
    fmt,
)
from tray import WM_APP_TRAY, WM_LBUTTONUP, TrayController

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
COLOR_WARN = "#b45309"         # 警告（琥珀色，不刺眼）
COLOR_ERR = "#bb3a3a"          # 错误（柔和红，非纯红）
COLOR_BUSY = "#3a6ea5"         # 进行中（中性蓝，与错误红区分，避免误读为出错）
COLOR_OK = "#1e7f3c"           # 成功反馈（复制成功）
COLOR_OK_SOFT = "#e8f7ee"      # 成功反馈按钮底色（淡绿）
COLOR_REFRESH_DISABLED_BG = "#e4e6ea"   # 刷新按钮禁用底色
COLOR_REFRESH_DISABLED_FG = "#a7adb8"   # 刷新按钮禁用文字

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

# 响应式多列卡片布局参数：
# 卡片用 grid 排布，列数由可用宽度与卡片实测自然宽度共同决定（1/2/3 列），
# 列宽均分。内容区下限保证窄窗口可用；实际上限按最宽卡片的自然宽度
# 联动放大（_content_max_w），保证满列数时任何币种名称都不截断。
CARD_IDEAL_W = 350     # 列数判定的兜底单位宽（无卡片可测时使用）
CARD_GAP = 12          # 卡片间距
MAX_COLS = 3           # 最大列数
MAX_CONTENT_W = MAX_COLS * CARD_IDEAL_W + (MAX_COLS - 1) * CARD_GAP + 36


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


def _bind_hover(widget, normal, active, guard=None):
    """统一 hover 变色绑定，消除三处按钮的重复样板。

    normal/active 为 (bg, fg) 元组；guard(widget) 返回 False 时该次
    Enter/Leave 不变色（用于禁用态、「已复制 ✓」反馈期等守卫）。
    """
    widget.bind("<Enter>", lambda e: None if guard and not guard(e.widget)
                else e.widget.config(bg=active[0], fg=active[1]))
    widget.bind("<Leave>", lambda e: None if guard and not guard(e.widget)
                else e.widget.config(bg=normal[0], fg=normal[1]))


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
        self._cards_min_w = 0       # 当前卡片的最大自然宽度（不截字所需，实测）
        self._outer = None          # 内容区的父容器（用于读取窗口宽）
        self._disp_by_code = {cur.code: cur.display for cur in ALL_CURRENCIES}
        # 选中币种与窗口尺寸（持久化到 %APPDATA%）
        # _load_config 已保证 selected 非空且均为合法币种代码
        cfg = _load_config()
        self._selected = list(cfg["selected"])
        self._saved_geometry = cfg.get("geometry")

        self._build_ui()
        self.bind("<F5>", lambda _e: self.refresh())
        # 滚轮绑定在顶层窗口：Tk 事件按 bindtags 链传播，子组件（卡片等）
        # 上的滚轮事件都会触发，覆盖整个窗口；不要在卡片画布上单独绑定，
        # 否则鼠标在卡片上滚动无效。
        self.bind("<MouseWheel>", self._on_mousewheel)
        self.after(100, self._poll_queue)
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
        tk.Label(name_box, text="外汇现汇买入价", bg=COLOR_BG, fg=COLOR_TEXT,
                 font=F_TITLE).pack(anchor="w")
        tk.Label(name_box, text="中国银行外汇牌价 · 现汇买入价 / 市场参考汇率",
                 bg=COLOR_BG, fg=COLOR_SUB,
                 font=F_SUBTITLE).pack(anchor="w", pady=(3, 0))
        self._build_currency_menu(header)

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

        # 头部说明 + 分隔线
        head = tk.Frame(panel, bg=COLOR_CARD)
        head.pack(fill="x", padx=14, pady=(10, 6))
        tk.Label(head, text="显示币种", bg=COLOR_CARD, fg=COLOR_SUB,
                 font=F_TAG).pack(side="left")
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
                w.bind("<Enter>", lambda _e, ws=widgets: [
                    x.config(bg=COLOR_BTN_ACTIVE) for x in ws])
                w.bind("<Leave>", lambda _e, ws=widgets: [
                    x.config(bg=COLOR_CARD) for x in ws])
            self._panel_checks[code] = chk
        body.grid_columnconfigure(0, weight=1)
        body.grid_columnconfigure(1, weight=1)

        # 定位：按钮右下角下方，钳制到屏幕内
        panel.update_idletasks()
        bx = self.menu_btn.winfo_rootx()
        by = self.menu_btn.winfo_rooty()
        x = bx + self.menu_btn.winfo_width() - panel.winfo_width()
        y = by + self.menu_btn.winfo_height() + 6
        x = max(4, min(x, self.winfo_screenwidth() - panel.winfo_width() - 4))
        y = max(4, min(y, self.winfo_screenheight() - panel.winfo_height() - 4))
        panel.geometry("+%d+%d" % (x, y))
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
        _save_config(self._selected, self.geometry())
        self._rebuild_cards()
        # 窗口尺寸保持固定（默认 3 个币种时的尺寸），币种增减不再变高，
        # 多出的卡片通过滚动条/鼠标滚轮查看。
        self.refresh()

    def _rebuild_cards(self):
        """按当前选中币种重建卡片（按 ALL_CURRENCIES 顺序，避免菜单勾选顺序抖动）。"""
        for w in self.cards_frame.winfo_children():
            w.destroy()
        self.cards = {}
        self._selected = [c.code for c in ALL_CURRENCIES if c.code in self._selected]
        self._selected_curs = [CODE_TO_CURRENCY[c] for c in self._selected]
        for cur in self._selected_curs:
            self.cards[cur.code] = self._build_card(self.cards_frame, cur)
        # 新增币种时，先用会话内已有数据即时渲染（标注「上次数据」），
        # 避免等待这次网络请求期间显示「--」
        self._render_session()
        self._sync_layout()

    # ---------- 响应式多列布局 ----------

    def _measure_cards(self):
        """测量当前卡片「不截字所需」的最大自然宽度（_cards_min_w）。

        取所有卡片请求宽度的最大值：任何一张卡被压窄都会截字，列数
        阈值必须以最宽的卡片为准，而不是固定的经验值。测量前把来源
        胶囊临时设为最宽文案「● 备用源参考」——胶囊实际文案随数据
        而定（空/中行牌价/备用源/上次数据），若按当前文案测量，数据
        到达后胶囊变宽会重新截字。测完恢复原文案。
        """
        if not self.cards:
            self._cards_min_w = CARD_IDEAL_W
            return
        saved = {}
        try:
            for code, info in self.cards.items():
                saved[code] = info["tag"].cget("text")
                info["tag"].config(text="● 备用源参考")
            self.update_idletasks()
            self._cards_min_w = max(info["card"].winfo_reqwidth()
                                    for info in self.cards.values())
        finally:
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
        卡片为单位，保证选出的列数下每张卡（含最长币种名）都放得下。
        """
        unit = self._card_unit_w()
        cols = (canvas_w + CARD_GAP) // (unit + CARD_GAP)
        return max(1, min(MAX_COLS, int(cols)))

    def _content_max_w(self):
        """内容区宽度上限：下限 MAX_CONTENT_W，且随最宽卡片联动放大，
        保证满列数（MAX_COLS）时最宽的卡片也放得下。"""
        need = MAX_COLS * self._card_unit_w() + (MAX_COLS - 1) * CARD_GAP + 36
        return max(MAX_CONTENT_W, need)

    def _apply_content_width(self):
        """按当前上限重设内容区宽度（窗口实际宽与上限取小者）。"""
        try:
            win_w = self._outer.winfo_width()
        except (AttributeError, tk.TclError):
            return
        if win_w > 1:
            self._content.place_configure(
                width=min(win_w, self._content_max_w()))

    def _sync_layout(self):
        """重建卡片后的整体同步：测量 → 定内容宽 → 定列数 → 重排。"""
        self._measure_cards()
        self._apply_content_width()
        canvas_w = self.card_canvas.winfo_width()
        if canvas_w > 1:
            self._cols = self._desired_cols(canvas_w)
        self._layout_cards()
        self._update_scrollbar()

    def _schedule_relayout(self, canvas_w):
        """窗口宽度变化时按需重排列数（去抖 150ms，避免拖拽中频繁重排跳动）。"""
        cols = self._desired_cols(canvas_w)
        if cols == self._cols:
            return
        if self._relayout_job is not None:
            self.after_cancel(self._relayout_job)
        self._relayout_job = self.after(150, lambda: self._apply_cols(cols))

    def _apply_cols(self, cols):
        self._relayout_job = None
        if cols == self._cols or not self.winfo_exists():
            return
        self._cols = cols
        self._layout_cards()
        self._update_scrollbar()

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
            total = min(total, int(self.winfo_screenheight() * 0.85))
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

        视觉规范对齐主界面：次级说明文字、卡片内同款分隔线、主（红底）/
        次（幽灵描边）两级按钮；窗口启用 Win11 DWM 圆角 + 系统投影，
        无投影的旧系统回退 1px 描边卡片观感。标题由标题栏承载，正文只
        放说明与操作，避免重复。Esc / 回车 / 关闭对话框均按推荐项
        「最小化到系统托盘」处理，不会造成误退出。
        """
        dlg = tk.Toplevel(self, bg=COLOR_CARD, highlightthickness=1,
                          highlightbackground=COLOR_BORDER)
        self._close_dlg = dlg
        dlg.title("关闭程序")
        dlg.resizable(False, False)
        dlg.transient(self)
        try:
            # Win11：系统圆角 + 投影已提供边界感，去掉 1px 直角描边
            # （描边是方角，会被圆角裁出毛边）；旧系统保留描边卡片观感
            if sys.getwindowsversion().build >= 22000:
                dlg.configure(highlightthickness=0)
        except Exception:
            pass

        pad = tk.Frame(dlg, bg=COLOR_CARD, padx=26, pady=22)
        pad.pack(fill="both", expand=True)

        # 说明文字：字号 11（大于正文次级文字，弹窗独立阅读场景），
        # 窄屏时按屏宽收窄折行，保证小屏完整可读
        wrap = min(348, max(240, self.winfo_screenwidth() - 160))
        tk.Label(pad, text="「最小化到系统托盘」后程序在后台保持运行，"
                           "点击托盘图标可随时打开并自动更新牌价；"
                           "「直接退出」将关闭程序。",
                 bg=COLOR_CARD, fg=COLOR_SUB, font=(FONT_FAMILY, 11),
                 wraplength=wrap, justify="left",
                 anchor="w").pack(fill="x", pady=(0, 18))

        # 分隔线：与卡片内部同款
        tk.Frame(pad, bg=COLOR_DIVIDER, height=1).pack(fill="x")

        def choose(action):
            dlg.grab_release()
            dlg.destroy()
            self._close_dlg = None
            action()

        # 按钮区：主按钮居右（与主界面底部「刷新」主按钮位置一致），
        # 幽灵次按钮在其左侧；字级/内边距对齐，两按钮等高
        btns = tk.Frame(pad, bg=COLOR_CARD)
        btns.pack(fill="x", pady=(16, 0))
        btn_tray = tk.Button(
            btns, text="最小化到系统托盘",
            command=lambda: choose(self._minimize_to_tray),
            font=F_BTN_MAIN, bg=COLOR_ACCENT, fg="white",
            activebackground=COLOR_ACCENT_DARK, activeforeground="white",
            relief="flat", padx=18, pady=7, cursor="hand2", takefocus=0,
            bd=0)
        btn_tray.pack(side="right")
        btn_exit = tk.Button(
            btns, text="直接退出",
            command=lambda: choose(self._quit_app),
            font=(FONT_FAMILY, 10), bg=COLOR_CARD, fg=COLOR_BTN_TEXT,
            activebackground=COLOR_BTN_ACTIVE,
            activeforeground=COLOR_BTN_TEXT_ACTIVE,
            relief="flat", padx=14, pady=7, cursor="hand2", takefocus=0,
            bd=0, highlightthickness=1, highlightbackground=COLOR_BORDER,
            highlightcolor=COLOR_BORDER)
        btn_exit.pack(side="right", padx=(0, 8))
        _bind_hover(btn_tray, (COLOR_ACCENT, "white"),
                    (COLOR_ACCENT_DARK, "white"))
        _bind_hover(btn_exit, (COLOR_CARD, COLOR_BTN_TEXT),
                    (COLOR_BTN_ACTIVE, COLOR_BTN_TEXT_ACTIVE))

        dlg.bind("<Escape>", lambda _e: choose(self._minimize_to_tray))
        dlg.bind("<Return>", lambda _e: choose(self._minimize_to_tray))
        dlg.protocol("WM_DELETE_WINDOW", lambda: choose(self._minimize_to_tray))

        # 居中于主窗口，并钳制到屏幕内（小屏/多屏拔插时弹窗始终完整可见）
        dlg.update_idletasks()
        try:
            x = self.winfo_rootx() + (self.winfo_width()
                                      - dlg.winfo_width()) // 2
            y = self.winfo_rooty() + (self.winfo_height()
                                      - dlg.winfo_height()) // 3
            x = max(4, min(x, self.winfo_screenwidth()
                           - dlg.winfo_width() - 4))
            y = max(4, min(y, self.winfo_screenheight()
                           - dlg.winfo_height() - 4))
            dlg.geometry("+%d+%d" % (x, y))
        except tk.TclError:
            pass
        dlg.deiconify()
        dlg.lift()
        dlg.focus_force()
        _round_window_corners(dlg)
        try:
            dlg.wait_visibility()
            dlg.grab_set()
        except tk.TclError:
            pass

    def _save_geometry_config(self):
        """关闭/最小化前保存窗口尺寸与币种选择，供下次启动恢复。"""
        try:
            _save_config(self._selected, self.geometry())
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

    def _close_dlg_check(self):
        """恢复窗口前关闭可能残留的关闭方式选择对话框。"""
        dlg = self._close_dlg
        if dlg is not None and dlg.winfo_exists():
            dlg.destroy()
        self._close_dlg = None

    def _quit_app(self):
        """退出程序（对话框「直接退出」/ 托盘菜单「退出程序」）。"""
        self._save_geometry_config()
        self.destroy()

    def destroy(self):
        """退出前清理托盘资源：删除图标、还原窗口过程，避免残留。"""
        if getattr(self, "_tray", None) is not None:
            self._tray.shutdown()
        super().destroy()

    # ---------- 卡片统一排版规范 ----------
    # 所有卡片共用同一组常量与对齐规则，保证任意币种、任意数据状态下
    # 排版一致：
    # - 结构固定：头部行（符号+名称｜来源胶囊+复制）→ 分隔线 → 数值行
    #   （单位+数值｜人民币），行内元素全部底边对齐（anchor="s"）——同
    #   排不同字号的文字共用一条视觉底线，避免居中对齐造成的基线漂浮；
    # - 左右分区：左侧是主体信息（币种、数值），右侧是元信息/单位
    #   （胶囊、复制、人民币），各元素位置不随币种名称长度变化，所有
    #   卡片的视觉轴线完全一致；
    # - 层级分明：币种名深色、数值红色大字（视觉焦点）、单位与次级
    #   文字灰色弱化，主次一眼可辨；
    # - 数值列宽固定 8 字符（等宽字体）：容纳 fmt 对 <0.01 汇率的 8 字符
    #   输出（如 KRW「0.005200」），且数据从「--」变为数值、6~8 字符
    #   长度变化时布局保持不动。

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

        # 主数值行：左=「1 外币 =」+ 数值（焦点）；右=「人民币」贴齐卡片
        # 右缘——所有卡片的「人民币」位置完全一致，形成整齐的表格感，
        # 也消除数值中部的大片空洞。单位文字灰色弱化，突出红色数值。
        mid = tk.Frame(card, bg=COLOR_CARD)
        mid.pack(fill="x")
        lbl_cur = tk.Label(mid, text="1 %s =" % disp, bg=COLOR_CARD,
                           fg=COLOR_SUB, font=F_UNIT, anchor="s")
        lbl_cur.pack(side="left", anchor="s", pady=(0, 4))
        value = tk.Label(mid, text="--", bg=COLOR_CARD, fg=COLOR_ACCENT,
                         font=F_VALUE, width=8, anchor="w")
        value.pack(side="left", padx=(4, 6))
        lbl_cny = tk.Label(mid, text="人民币", bg=COLOR_CARD,
                           fg=COLOR_SUB, font=F_UNIT, anchor="s")
        lbl_cny.pack(side="right", anchor="s", pady=(0, 4))

        # 双击任意位置复制；整卡可点区域统一 hand2 指针示意可交互。
        # btn_copy 不参与双击绑定：其 command 在单击释放时已触发，
        # 双击会导致 command×2 + 双击绑定×1 共 3 次重复复制。
        for w in (card, top, sym, title, tag, mid, lbl_cur, value,
                  lbl_cny):
            w.config(cursor="hand2")
            w.bind("<Double-Button-1>", lambda e, c=code: self._copy(c))

        return {"card": card, "tag": tag, "value": value,
                "btn_copy": btn_copy, "rate1": None, "flash_after": None}

    # ---------- 数据刷新 ----------

    def _render_session(self):
        """用会话内已有数据即时渲染卡片（重建卡片后调用）。

        只填充当前尚无数据的卡片（rate1 is None，即新建卡片）；
        渲染结果标注「上次数据」，随后的 refresh 成功后会被真实来源覆盖。
        """
        for code, c in self.cards.items():
            d = self._session.get(code)
            if not d or c["rate1"] is not None:
                continue
            c["rate1"] = d["rate1"]
            c["value"].config(text=fmt(d["rate1"]))
            c["tag"].config(text="◷ 上次数据", fg=COLOR_TAG_STALE_FG,
                            bg=COLOR_TAG_STALE_BG)

    def refresh(self):
        if self._fetching:
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

    def _worker(self):
        try:
            # 传快照：避免工作线程读取时 UI 线程恰好增删 _selected
            rows, err = fetch_all(list(self._selected))
            self.result_q.put({"rows": rows, "error": err})
        except Exception as e:
            logging.exception("抓取工作线程异常")
            _log_crash("worker", e)
            self.result_q.put({"rows": {}, "error": "发生异常：%s" % _brief(e)})

    def _poll_queue(self):
        try:
            while True:
                self._apply_result(self.result_q.get_nowait())
        except queue.Empty:
            pass
        self.after(100, self._poll_queue)

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
                c["value"].config(text=fmt(d["rate1"]))
                if d.get("fallback"):
                    c["tag"].config(text="● 备用源参考", fg=COLOR_TAG_FALLBACK,
                                    bg=COLOR_TAG_FALLBACK_BG)
                else:
                    c["tag"].config(text="● 中行牌价", fg=COLOR_ACCENT,
                                    bg=COLOR_TAG_BOC_BG)
            else:
                if c["rate1"] is None:
                    c["value"].config(text="--")
                    c["tag"].config(text="", bg=COLOR_CARD)
                else:
                    kept_old = True
                    c["tag"].config(text="◷ 上次数据", fg=COLOR_TAG_STALE_FG,
                                    bg=COLOR_TAG_STALE_BG)

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
        r = self.cards[code].get("rate1")
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
        self._status_base = "已复制：%s（1 %s 兑人民币）" % (s, disp)
        self._status_fg_state = "ok"
        self._render_status()
        self._flash_copy(code)

    def _flash_copy(self, code):
        c = self.cards[code]
        btn = c["btn_copy"]
        # 复制成功：按钮短暂变为淡绿底 + 绿字，反馈更明显；
        # 反馈期间挂起 hover 变色，避免绿色被 hover 规则覆盖
        btn.flashing = True
        btn.config(text="已复制 ✓", bg=COLOR_OK_SOFT, fg=COLOR_OK)
        # 连续快速复制时取消上一次的还原回调，避免按钮文字被提前还原
        prev = c.get("flash_after")
        if prev:
            try:
                self.after_cancel(prev)
            except Exception:
                pass

        def restore():
            btn.flashing = False
            try:
                if self.winfo_exists():
                    btn.config(text="复制", bg=COLOR_CARD, fg=COLOR_BTN_TEXT)
            except tk.TclError:
                pass

        c["flash_after"] = self.after(1200, restore)


def _setup_logging():
    """--debug 时写调试日志；程序目录不可写则回退到系统临时目录。"""
    if "--debug" not in sys.argv:
        return
    if getattr(sys, "frozen", False):
        base = os.path.dirname(os.path.abspath(sys.executable))
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        os.path.join(base, "外汇现汇买入价查询_debug.log"),
        os.path.join(tempfile.gettempdir(), "外汇现汇买入价查询_debug.log"),
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
