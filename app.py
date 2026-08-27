# -*- coding: utf-8 -*-
"""外汇现汇买入价查询（Windows 桌面程序）— 界面层。

抓取/解析逻辑见 fetcher.py，程序入口为 boc_fx_rates.py。
"""

import ctypes
import json
import logging
import os
import queue
import re
import sys
import tempfile
import threading
import tkinter as tk
from datetime import datetime

import fetcher
from fetcher import (
    ALL_CURRENCIES,
    CODE_TO_CURRENCY,
    CURRENCIES,
    _brief,
    fetch_all,
    fmt,
)

# ----------------------------- 配置 -----------------------------

APP_TITLE = "外汇现汇买入价查询"

FONT_FAMILY = "Microsoft YaHei UI"
FONT_MONO = "Consolas"

COLOR_BG = "#f3f4f6"          # 窗口背景
COLOR_CARD = "#ffffff"
COLOR_BORDER = "#e4e7ec"      # 卡片描边
COLOR_DIVIDER = "#eef0f3"     # 卡片内分隔线
COLOR_TEXT = "#1f2329"
COLOR_SUB = "#8a919f"         # 次级文字
COLOR_ACCENT = "#b81b22"      # 中行红
COLOR_ACCENT_DARK = "#9c151c" # 中行红（hover/按下加深）
COLOR_TAG_FALLBACK = "#8a6d3b"
COLOR_BTN = "#f1f3f5"
COLOR_BTN_ACTIVE = "#e4e7ec"
COLOR_WARN = "#b45309"        # 状态圆点/文字：警告

# 状态圆点颜色（按 _status_fg_state）
_STATUS_DOT = {
    "sub": COLOR_SUB,
    "warn": COLOR_WARN,
    "busy": COLOR_ACCENT,
}

# 英文月份缩写 → 数字（用于备用源日期格式 "26 Aug 2026"）
_MONTHS = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
           "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12}


def _resource_path(rel):
    """获取资源绝对路径：兼容源码运行与 PyInstaller 冻结。"""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, rel)


def _config_dir():
    """用户配置目录：%APPDATA%\\外汇现汇买入价查询（不可用时回退临时目录）。"""
    base = os.environ.get("APPDATA") or tempfile.gettempdir()
    d = os.path.join(base, APP_TITLE)
    try:
        os.makedirs(d, exist_ok=True)
    except OSError:
        pass
    return d


def _config_path():
    return os.path.join(_config_dir(), "config.json")


def _load_config():
    """读取配置；缺失/损坏时返回默认（选中的 3 种货币）。"""
    default = {"selected": [cur.code for cur in CURRENCIES], "geometry": None}
    try:
        with open(_config_path(), "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return default
    if not isinstance(data, dict):
        return default
    selected = data.get("selected")
    if not isinstance(selected, list):
        selected = [cur.code for cur in CURRENCIES]
    valid = [c for c in selected if c in CODE_TO_CURRENCY] or \
            [cur.code for cur in CURRENCIES]
    geometry = data.get("geometry")
    if not isinstance(geometry, str):
        geometry = None
    return {"selected": valid, "geometry": geometry}


def _save_config(selected, geometry=None):
    """持久化选中币种与窗口尺寸（静默失败：配置写入非关键路径）。"""
    try:
        data = {"selected": selected}
        if geometry:
            data["geometry"] = geometry
        with open(_config_path(), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def _fmt_time(t):
    """统一时间显示：BOC「2026/08/26 20:50:26」→「08-26 20:50」，备用源日期→「2026-08-26」。"""
    t = (t or "").strip()
    if not t:
        return ""
    for pat, out in (("%Y/%m/%d %H:%M:%S", "%Y-%m-%d %H:%M"),
                     ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M")):
        try:
            return datetime.strptime(t, pat).strftime(out)
        except ValueError:
            continue
    m = re.match(r"(\d{1,2})\s+([A-Za-z]{3})\s+(\d{4})$", t)
    if m:
        mon = _MONTHS.get(m.group(2).lower())
        if mon:
            return "%04d-%02d-%02d" % (int(m.group(3)), mon, int(m.group(1)))
    return t


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.configure(bg=COLOR_BG)
        self.minsize(460, 380)
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
        self._disp_by_code = {cur.code: cur.display for cur in ALL_CURRENCIES}
        # 选中币种与窗口尺寸（持久化到 %APPDATA%）
        cfg = _load_config()
        self._selected = [c for c in cfg["selected"] if c in CODE_TO_CURRENCY] or \
                         [c.code for c in CURRENCIES]
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
                self.geometry(self._saved_geometry)
            except Exception:
                self.eval("tk::PlaceWindow . center")
                self.after_idle(self._fit_window)
        else:
            self.eval("tk::PlaceWindow . center")
            self.after_idle(self._fit_window)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ---------- 界面构建 ----------

    def _build_ui(self):
        outer = tk.Frame(self, bg=COLOR_BG, padx=16, pady=12)
        outer.pack(fill="both", expand=True)

        # 顶部标题区：应用名 + 副标题（左），币种菜单（右）
        header = tk.Frame(outer, bg=COLOR_BG)
        header.pack(fill="x", pady=(0, 10))
        title_box = tk.Frame(header, bg=COLOR_BG)
        title_box.pack(side="left", anchor="n")
        tk.Label(title_box, text="外汇现汇买入价", bg=COLOR_BG, fg=COLOR_TEXT,
                 font=(FONT_FAMILY, 15, "bold")).pack(anchor="w")
        tk.Label(title_box, text="中国银行外汇牌价 · 现汇买入价 / 市场参考汇率",
                 bg=COLOR_BG, fg=COLOR_SUB,
                 font=(FONT_FAMILY, 9)).pack(anchor="w", pady=(2, 0))
        self._build_currency_menu(header)

        # 可滚动卡片区：卡片数量多时滚动查看
        body = tk.Frame(outer, bg=COLOR_BG)
        body.pack(fill="both", expand=True)
        sb = tk.Scrollbar(body, orient="vertical", command=self._scroll_yview)
        sb.pack(side="right", fill="y")
        self.card_canvas = tk.Canvas(body, bg=COLOR_BG, highlightthickness=0,
                                     yscrollcommand=sb.set)
        self.card_canvas.pack(side="left", fill="both", expand=True)
        self.cards_frame = tk.Frame(self.card_canvas, bg=COLOR_BG)
        self._cards_win = self.card_canvas.create_window(
            (0, 0), window=self.cards_frame, anchor="nw")
        self.cards_frame.bind("<Configure>", self._on_cards_resize)
        self.card_canvas.bind("<Configure>", self._on_canvas_resize)

        self._rebuild_cards()

        # 状态栏：状态圆点 + 状态文字 + 刷新按钮
        status_row = tk.Frame(outer, bg=COLOR_BG)
        status_row.pack(fill="x", pady=(10, 0))
        self.dot_status = tk.Canvas(status_row, width=10, height=10, bg=COLOR_BG,
                                    highlightthickness=0)
        self.dot_status.create_oval(1, 1, 9, 9, fill=COLOR_SUB, outline="",
                                    tags="dot")
        self.dot_status.pack(side="left", padx=(2, 6))
        self.lbl_status = tk.Label(status_row, text="正在准备…", bg=COLOR_BG,
                                   fg=COLOR_SUB, font=(FONT_FAMILY, 9),
                                   anchor="w", justify="left", wraplength=400)
        self.lbl_status.pack(side="left", fill="x", expand=True)
        self.lbl_status.bind("<Configure>", self._on_status_resize)
        self.btn_refresh = tk.Button(
            status_row, text="刷 新", command=self.refresh,
            font=(FONT_FAMILY, 9, "bold"), bg=COLOR_ACCENT, fg="white",
            activebackground=COLOR_ACCENT_DARK, activeforeground="white",
            relief="flat", padx=14, pady=4, cursor="hand2")
        self.btn_refresh.pack(side="right", padx=(8, 0))
        self.btn_refresh.bind("<Enter>",
                              lambda e: e.widget.config(bg=COLOR_ACCENT_DARK))
        self.btn_refresh.bind("<Leave>",
                              lambda e: e.widget.config(bg=COLOR_ACCENT))

    # ---------- 币种选择 ----------

    def _build_currency_menu(self, parent):
        """右上角「币种」下拉菜单：勾选要显示的币种。"""
        self.menu_btn = tk.Menubutton(
            parent, text="币种 ▾", font=(FONT_FAMILY, 9, "bold"),
            bg=COLOR_BTN, fg=COLOR_TEXT, activebackground=COLOR_BTN_ACTIVE,
            relief="flat", padx=12, pady=3, cursor="hand2")
        self.menu_btn.pack(side="right", anchor="n")
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

    # ---------- 滚动 ----------

    def _scroll_yview(self, *args):
        self.card_canvas.yview(*args)

    def _on_cards_resize(self, _e):
        self.card_canvas.configure(scrollregion=self.card_canvas.bbox("all"))

    def _on_canvas_resize(self, e):
        self.card_canvas.itemconfigure(self._cards_win, width=e.width)

    def _on_mousewheel(self, e):
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
                        highlightthickness=1, padx=14, pady=10)
        card.pack(fill="x", pady=5)

        # 卡片头：货币符号 + 名称 + 标签 + 复制按钮
        top = tk.Frame(card, bg=COLOR_CARD)
        top.pack(fill="x")
        sym = tk.Label(top, text=cur.symbol, bg=COLOR_CARD, fg=COLOR_ACCENT,
                       font=(FONT_FAMILY, 12, "bold"), width=2, anchor="w")
        sym.pack(side="left", padx=(0, 6))
        title = tk.Label(top, text="%s（%s）" % (disp, code), bg=COLOR_CARD,
                         fg=COLOR_TEXT, font=(FONT_FAMILY, 12, "bold"))
        title.pack(side="left")
        tag = tk.Label(top, text="", bg=COLOR_CARD, fg=COLOR_SUB,
                       font=(FONT_FAMILY, 9))
        tag.pack(side="left", padx=8)
        btn_copy = tk.Button(top, text="复制", command=lambda c=code: self._copy(c),
                             font=(FONT_FAMILY, 9), bg=COLOR_BTN, fg=COLOR_SUB,
                             activebackground=COLOR_BTN_ACTIVE, relief="flat",
                             padx=10, pady=2, cursor="hand2",
                             highlightthickness=1,
                             highlightbackground=COLOR_BORDER,
                             highlightcolor=COLOR_BORDER)
        btn_copy.pack(side="right")
        btn_copy.bind("<Enter>", lambda e: e.widget.config(bg=COLOR_BTN_ACTIVE))
        btn_copy.bind("<Leave>", lambda e: e.widget.config(bg=COLOR_BTN))

        # 分隔线：头部与数值区之间
        tk.Frame(card, bg=COLOR_DIVIDER, height=1).pack(fill="x", pady=(8, 7))

        # 主数值：1 外币 = X 人民币（等宽数字，宽度固定避免刷新时跳动）
        mid = tk.Frame(card, bg=COLOR_CARD)
        mid.pack(fill="x")
        lbl_cur = tk.Label(mid, text="1 %s =" % disp, bg=COLOR_CARD, fg=COLOR_TEXT,
                           font=(FONT_FAMILY, 11))
        lbl_cur.pack(side="left")
        value = tk.Label(mid, text="--", bg=COLOR_CARD, fg=COLOR_ACCENT,
                         font=(FONT_MONO, 20, "bold"), width=8, anchor="w")
        value.pack(side="left", padx=3)
        lbl_cny = tk.Label(mid, text="人民币", bg=COLOR_CARD, fg=COLOR_TEXT,
                           font=(FONT_FAMILY, 11))
        lbl_cny.pack(side="left")

        # 来源信息
        src = tk.Label(card, text="来源：--", bg=COLOR_CARD, fg=COLOR_SUB,
                       font=(FONT_FAMILY, 9), anchor="w")
        src.pack(anchor="w", pady=(6, 0))

        # 双击任意位置复制
        for w in (card, top, sym, title, tag, btn_copy, mid, lbl_cur, value,
                  lbl_cny, src):
            w.bind("<Double-Button-1>", lambda e, c=code: self._copy(c))

        return {"card": card, "tag": tag, "value": value, "src": src,
                "btn_copy": btn_copy, "rate1": None}

    # ---------- 数据刷新 ----------

    def refresh(self):
        if self._fetching:
            return
        self._fetching = True
        self.btn_refresh.config(state="disabled", text="刷新中…")
        self._status_base = "正在获取最新牌价，请稍候…"
        self._status_fg_state = "busy"
        self._render_status()
        threading.Thread(target=self._worker, daemon=True).start()

    def _worker(self):
        try:
            rows, err = fetch_all(self._selected)
            self.result_q.put({"rows": rows, "error": err})
        except Exception as e:
            logging.exception("抓取工作线程异常")
            self.result_q.put({"rows": {}, "error": "发生异常：%s" % _brief(e)})

    def _poll_queue(self):
        try:
            while True:
                self._apply_result(self.result_q.get_nowait())
        except queue.Empty:
            pass
        self.after(100, self._poll_queue)

    def _apply_result(self, msg):
        self._fetching = False
        self.btn_refresh.config(state="normal", text="刷 新")

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

        now = datetime.now().strftime("%H:%M:%S")
        if msg.get("error"):
            self._status_base = msg["error"]
            if kept_old:
                self._status_base += " · 已保留上次成功数据"
            self._status_fg_state = "warn"
            self._last_update = now
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
        fg = {"sub": COLOR_SUB, "warn": COLOR_WARN, "busy": COLOR_ACCENT}.get(
            state, COLOR_SUB)
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
        self.clipboard_clear()
        self.clipboard_append(s)
        disp = self._disp_by_code[code]
        self._status_base = "已复制：%s（1 %s 兑人民币）" % (s, disp)
        self._status_fg_state = "sub"
        self._render_status()
        self._flash_copy(code)

    def _flash_copy(self, code):
        btn = self.cards[code]["btn_copy"]
        btn.config(text="已复制 ✓")

        def restore():
            try:
                if self.winfo_exists():
                    btn.config(text="复制")
            except tk.TclError:
                pass

        self.after(1200, restore)


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
    """单实例：重复启动时唤醒已有窗口并退出，避免开多个窗口。"""
    if "--selftest" in sys.argv:
        return True   # 自动化测试模式不受单实例限制
    ctypes.windll.kernel32.SetLastError(0)
    ctypes.windll.kernel32.CreateMutexW(None, False, "Local\\" + APP_TITLE)
    if ctypes.windll.kernel32.GetLastError() != 183:   # ERROR_ALREADY_EXISTS
        return True
    hwnd = ctypes.windll.user32.FindWindowW(None, APP_TITLE)
    if hwnd:
        ctypes.windll.user32.ShowWindow(hwnd, 9)        # SW_RESTORE
        ctypes.windll.user32.SetForegroundWindow(hwnd)
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
    # 高分屏下界面更清晰：Per-Monitor V2（Win10 1803+），旧系统回退到 System DPI aware
    try:
        ctypes.windll.user32.SetProcessDpiAwarenessContext(-4)
    except Exception:
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
    app = App()
    if "--selftest" in sys.argv:
        app.after(6000, app.destroy)   # 自动化冒烟测试：6 秒后自动退出
    app.mainloop()


if __name__ == "__main__":
    main()
