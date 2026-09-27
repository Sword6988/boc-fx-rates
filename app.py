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

import fetcher
from config import (
    APP_TITLE,
    _clamp_geometry,
    _config_dir,
    _config_path,
    _load_config,
    _save_config,
)
from fetcher import (
    ALL_CURRENCIES,
    CODE_TO_CURRENCY,
    _brief,
    _fmt_time,
    fetch_all,
    fmt,
)

# ----------------------------- 配置 -----------------------------

FONT_FAMILY = "Microsoft YaHei UI"
FONT_MONO = "Consolas"

# ------------------------- 调色板（浅色主题） -------------------------

COLOR_BG = "#f4f5f7"           # 窗口背景
COLOR_CARD = "#ffffff"         # 卡片背景
COLOR_BORDER = "#e4e7ec"       # 卡片描边
COLOR_DIVIDER = "#f0f2f4"      # 卡片内分隔线
COLOR_TEXT = "#1f2329"         # 主文字
COLOR_SUB = "#8a919f"          # 次级文字
COLOR_ACCENT = "#b81b22"       # 中行红（主强调：数值/符号/主按钮）
COLOR_ACCENT_DARK = "#9a151b"  # 中行红（hover/按下加深）
COLOR_TAG_FALLBACK = "#8a6d3b" # 备用源标签
COLOR_BTN = "#f2f3f5"          # 次级按钮底色
COLOR_BTN_ACTIVE = "#e6e9ed"   # 次级按钮 hover
COLOR_BTN_TEXT = "#4a5160"     # 次级按钮文字（比 SUB 深，避免误读为禁用）
COLOR_BTN_TEXT_ACTIVE = "#1f2329"
COLOR_WARN = "#b45309"         # 警告（琥珀色，不刺眼）
COLOR_ERR = "#bb3a3a"          # 错误（柔和红，非纯红）
COLOR_BUSY = "#3a6ea5"         # 进行中（中性蓝，与错误红区分，避免误读为出错）
COLOR_OK = "#1e7f3c"           # 成功反馈（复制成功）
COLOR_OK_SOFT = "#e9f6ee"      # 成功反馈按钮底色（淡绿）
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

F_TITLE = (FONT_FAMILY, 16, "bold")    # 窗口主标题
F_SUBTITLE = (FONT_FAMILY, 9)          # 主标题下副说明
F_CARD_TITLE = (FONT_FAMILY, 12, "bold")  # 卡片币种名
F_CARD_SYMBOL = (FONT_FAMILY, 13, "bold") # 卡片货币符号
F_TAG = (FONT_FAMILY, 9)               # 卡片来源标签
F_VALUE = (FONT_MONO, 20, "bold")      # 主数值（等宽，防刷新跳动）
F_UNIT = (FONT_FAMILY, 11)             # 「1 外币 =」「人民币」单位
F_SRC = (FONT_FAMILY, 9)               # 来源行
F_STATUS = (FONT_FAMILY, 9)            # 状态行
F_BTN_MAIN = (FONT_FAMILY, 9, "bold")  # 主按钮（刷新）
F_BTN_SMALL = (FONT_FAMILY, 9)         # 次级按钮（复制/币种菜单）

MAX_CONTENT_W = 760   # 内容区最大宽度（超出后整体居中，避免卡片被拉得过宽）


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
    """
    try:
        path = os.path.join(_config_dir(), "crash.log")
        with open(path, "a", encoding="utf-8") as f:
            f.write("%s [%s]\n%s\n" % (
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"), context,
                "".join(traceback.format_exception(
                    type(exc), exc, exc.__traceback__)).rstrip()))
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
        self.minsize(440, 380)
        self.resizable(True, True)
        # 替换默认 Tk 羽毛笔图标
        try:
            self.iconbitmap(default=_resource_path("app_icon.ico"))
        except Exception:
            pass

        self.result_q = queue.Queue()
        self.cards = {}
        self._fetching = False
        self._status_base = "正在准备…"
        self._status_fg_state = "sub"
        self._last_update = ""
        self._sb_visible = True     # 滚动条当前是否显示（按需隐藏）
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

    # ---------- 界面构建 ----------

    def _build_ui(self):
        # 内容区统一限宽并水平居中：窗口远宽于 MAX_CONTENT_W 时卡片不被拉伸，
        # 标题/状态栏与卡片保持同一边距、同一中轴线，视觉更整齐。
        outer = tk.Frame(self, bg=COLOR_BG)
        outer.pack(fill="both", expand=True)
        self._content = tk.Frame(outer, bg=COLOR_BG)
        self._content.place(relx=0.5, rely=0, anchor="n", relheight=1.0,
                            width=min(440, MAX_CONTENT_W))
        outer.bind("<Configure>", self._on_outer_resize)

        pad = tk.Frame(self._content, bg=COLOR_BG, padx=18, pady=14)
        pad.pack(fill="both", expand=True)

        # 顶部标题区：应用名 + 副标题（左），币种菜单（右）
        header = tk.Frame(pad, bg=COLOR_BG)
        header.pack(fill="x", pady=(0, 12))
        title_box = tk.Frame(header, bg=COLOR_BG)
        title_box.pack(side="left", anchor="n")
        tk.Label(title_box, text="外汇现汇买入价", bg=COLOR_BG, fg=COLOR_TEXT,
                 font=F_TITLE).pack(anchor="w")
        tk.Label(title_box, text="中国银行外汇牌价 · 现汇买入价 / 市场参考汇率",
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
        status_row.pack(fill="x", pady=(12, 0))
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
            relief="flat", padx=16, pady=4, cursor="hand2", takefocus=0,
            bd=0)
        self.btn_refresh.pack(side="right", padx=(8, 0))
        # 禁用态不变色，避免「刷新中…」被 hover 提亮造成可点错觉
        _bind_hover(self.btn_refresh,
                    (COLOR_ACCENT, "white"), (COLOR_ACCENT_DARK, "white"),
                    guard=lambda w: str(w["state"]) == "normal")

    # ---------- 币种选择 ----------

    def _build_currency_menu(self, parent):
        """右上角「币种」下拉菜单：勾选要显示的币种。"""
        self.menu_btn = tk.Menubutton(
            parent, text="币种 ▾", font=F_BTN_SMALL,
            bg=COLOR_BTN, fg=COLOR_BTN_TEXT,
            activebackground=COLOR_BTN_ACTIVE, activeforeground=COLOR_BTN_TEXT,
            relief="flat", padx=12, pady=4, cursor="hand2", takefocus=0)
        self.menu_btn.pack(side="right", anchor="n")
        _bind_hover(self.menu_btn,
                    (COLOR_BTN, COLOR_BTN_TEXT),
                    (COLOR_BTN_ACTIVE, COLOR_BTN_TEXT))
        menu = tk.Menu(self.menu_btn, tearoff=False)
        self.menu_btn.configure(menu=menu)
        self._menu_vars = {}
        for cur in ALL_CURRENCIES:
            var = tk.BooleanVar(value=cur.code in self._selected)
            self._menu_vars[cur.code] = var
            menu.add_checkbutton(
                label="%s（%s）" % (cur.display, cur.code), variable=var,
                command=lambda c=cur.code: self._on_currency_toggle(c))

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
        self._update_scrollbar()

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
        self._update_scrollbar()

    def _on_outer_resize(self, e):
        """窗口尺寸变化：内容区限最大宽度并水平居中。"""
        self._content.place_configure(width=min(e.width, MAX_CONTENT_W))
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

    def _on_close(self):
        """关闭前保存窗口尺寸与币种选择，供下次启动恢复。"""
        try:
            _save_config(self._selected, self.geometry())
        except Exception:
            pass
        self.destroy()

    def _build_card(self, parent, cur):
        code, disp = cur.code, cur.display
        card = tk.Frame(parent, bg=COLOR_CARD, highlightbackground=COLOR_BORDER,
                        highlightthickness=1, padx=16, pady=12)
        card.pack(fill="x", pady=5)

        # 卡片头：货币符号 + 名称 + 标签 + 复制按钮
        top = tk.Frame(card, bg=COLOR_CARD)
        top.pack(fill="x")
        # 符号标签不设固定宽度：₽、CHF、Mex$ 等宽符号/多字符符号
        # 在 width=2 下会被截断显示不全，改为按内容自适应宽度
        sym = tk.Label(top, text=cur.symbol, bg=COLOR_CARD, fg=COLOR_ACCENT,
                       font=F_CARD_SYMBOL, anchor="w")
        sym.pack(side="left", padx=(0, 8))
        title = tk.Label(top, text="%s（%s）" % (disp, code), bg=COLOR_CARD,
                         fg=COLOR_TEXT, font=F_CARD_TITLE)
        title.pack(side="left")
        tag = tk.Label(top, text="", bg=COLOR_CARD, fg=COLOR_SUB,
                       font=F_TAG)
        tag.pack(side="left", padx=8)
        btn_copy = tk.Button(top, text="复制", command=lambda c=code: self._copy(c),
                             font=F_BTN_SMALL, bg=COLOR_BTN, fg=COLOR_BTN_TEXT,
                             activebackground=COLOR_BTN_ACTIVE,
                             activeforeground=COLOR_BTN_TEXT_ACTIVE,
                             relief="flat", padx=10, pady=2, cursor="hand2",
                             takefocus=0, bd=0,
                             highlightthickness=1,
                             highlightbackground=COLOR_BORDER,
                             highlightcolor=COLOR_BORDER)
        btn_copy.pack(side="right")
        btn_copy.flashing = False   # 「已复制 ✓」反馈期间 hover 不变色
        _bind_hover(btn_copy,
                    (COLOR_BTN, COLOR_BTN_TEXT),
                    (COLOR_BTN_ACTIVE, COLOR_BTN_TEXT_ACTIVE),
                    guard=lambda w: not getattr(w, "flashing", False))

        # 分隔线：头部与数值区之间
        tk.Frame(card, bg=COLOR_DIVIDER, height=1).pack(fill="x", pady=(10, 9))

        # 主数值：1 外币 = X 人民币（等宽数字，宽度固定避免刷新时跳动）
        mid = tk.Frame(card, bg=COLOR_CARD)
        mid.pack(fill="x")
        lbl_cur = tk.Label(mid, text="1 %s =" % disp, bg=COLOR_CARD, fg=COLOR_TEXT,
                           font=F_UNIT)
        lbl_cur.pack(side="left")
        value = tk.Label(mid, text="--", bg=COLOR_CARD, fg=COLOR_ACCENT,
                         font=F_VALUE, width=7, anchor="w")
        value.pack(side="left", padx=3)
        lbl_cny = tk.Label(mid, text="人民币", bg=COLOR_CARD, fg=COLOR_TEXT,
                           font=F_UNIT)
        lbl_cny.pack(side="left")

        # 来源信息
        src = tk.Label(card, text="来源：--", bg=COLOR_CARD, fg=COLOR_SUB,
                       font=F_SRC, anchor="w")
        src.pack(anchor="w", pady=(7, 0))

        # 双击任意位置复制；整卡可点区域统一 hand2 指针示意可交互。
        # btn_copy 不参与双击绑定：其 command 在单击释放时已触发，
        # 双击会导致 command×2 + 双击绑定×1 共 3 次重复复制。
        for w in (card, top, sym, title, tag, mid, lbl_cur, value,
                  lbl_cny, src):
            w.config(cursor="hand2")
            w.bind("<Double-Button-1>", lambda e, c=code: self._copy(c))

        return {"card": card, "tag": tag, "value": value, "src": src,
                "btn_copy": btn_copy, "rate1": None, "flash_after": None}

    # ---------- 数据刷新 ----------

    def refresh(self):
        if self._fetching:
            return
        self._fetching = True
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
                c["rate1"] = d["rate1"]
                c["value"].config(text=fmt(d["rate1"]))
                c["src"].config(text="来源：%s" % d["source"])
                if d.get("fallback"):
                    c["tag"].config(text="● 备用源参考", fg=COLOR_TAG_FALLBACK)
                else:
                    c["tag"].config(text="● 中行牌价", fg=COLOR_ACCENT)
            else:
                if c["rate1"] is None:
                    c["value"].config(text="--")
                    c["src"].config(text="可点击「刷新」重试")
                    c["tag"].config(text="")
                else:
                    kept_old = True
                    c["tag"].config(text="◷ 上次数据", fg=COLOR_SUB)

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
                    btn.config(text="复制", bg=COLOR_BTN, fg=COLOR_BTN_TEXT)
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
