# -*- coding: utf-8 -*-
"""系统托盘支持（纯 ctypes 实现，无第三方依赖）。

实现要点：
- 不引入 pystray/Pillow 等第三方库：托盘图标通过 Shell_NotifyIconW 注册，
  回调消息（WM_APP+1）挂在主窗口的窗口过程上——Tk 主循环在主线程泵
  Windows 消息，回调因此天然运行在 Tk 主线程，可直接操作界面控件，
  无需跨线程封送。
- 子类化（SetWindowLongPtrW）窗口过程后，除托盘回调与 TaskbarCreated
  外的一切消息都经 CallWindowProcW 原样交还原过程，不影响 Tk 自身的
  消息处理；程序退出前必须 shutdown() 删除图标并还原过程，避免残留
  死图标与悬挂窗口过程。
- 右键托盘图标弹出原生菜单（显示主窗口 / 退出程序）；TrackPopupMenu
  前须 SetForegroundWindow，否则点击菜单外部时菜单不会关闭（Win32 已知
  行为），且返回后须向窗口投递 WM_NULL 空消息。
- x64 正确性：HWND/HICON/HMENU 为指针宽度，一律按 wintypes 句柄类型或
  c_void_p 声明；窗口过程地址按 c_ssize_t（LONG_PTR）传递。
"""

import ctypes
import ctypes.wintypes as wt
import logging

user32 = ctypes.windll.user32
shell32 = ctypes.windll.shell32

# ---- 常量 ----
NIM_ADD, NIM_MODIFY, NIM_DELETE = 0, 1, 2
NIF_MESSAGE, NIF_ICON, NIF_TIP, NIF_INFO = 0x1, 0x2, 0x4, 0x10
NIIF_INFO = 0x1
WM_APP_TRAY = 0x8000 + 1        # WM_APP + 1：托盘回调消息
WM_LBUTTONUP = 0x0201
WM_LBUTTONDBLCLK = 0x0203
WM_RBUTTONUP = 0x0205
WM_CONTEXTMENU = 0x007B
WM_NULL = 0x0000
GWLP_WNDPROC = -4
IMAGE_ICON = 1
LR_LOADFROMFILE = 0x10
TPM_RIGHTBUTTON = 0x2
TPM_BOTTOMALIGN = 0x20
TPM_RETURNCMD = 0x100
MF_STRING = 0x0
MF_SEPARATOR = 0x800
SM_CXSMICON, SM_CYSMICON = 49, 50

WNDPROC = ctypes.WINFUNCTYPE(
    ctypes.c_ssize_t, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)


class NOTIFYICONDATAW(ctypes.Structure):
    class _UNION(ctypes.Union):
        _fields_ = [("uTimeout", wt.UINT), ("uVersion", wt.UINT)]

    _fields_ = [
        ("cbSize", wt.DWORD),
        ("hWnd", wt.HWND),
        ("uID", wt.UINT),
        ("uFlags", wt.UINT),
        ("uCallbackMessage", wt.UINT),
        ("hIcon", wt.HICON),
        ("szTip", wt.WCHAR * 128),
        ("dwState", wt.DWORD),
        ("dwStateMask", wt.DWORD),
        ("szInfo", wt.WCHAR * 256),
        ("Anonymous", _UNION),
        ("szInfoTitle", wt.WCHAR * 64),
        ("dwInfoFlags", wt.DWORD),
        ("guidItem", ctypes.c_ubyte * 16),
        ("hBalloonIcon", wt.HICON),
    ]


class _POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


# ---- 函数签名（x64 指针宽度正确性） ----
shell32.Shell_NotifyIconW.argtypes = [wt.DWORD, ctypes.POINTER(NOTIFYICONDATAW)]
shell32.Shell_NotifyIconW.restype = wt.BOOL
user32.LoadImageW.argtypes = [wt.HINSTANCE, wt.LPCWSTR, wt.UINT,
                              ctypes.c_int, ctypes.c_int, wt.UINT]
user32.LoadImageW.restype = wt.HANDLE
user32.DestroyIcon.argtypes = [wt.HICON]
user32.DestroyIcon.restype = wt.BOOL
user32.GetWindowLongPtrW.argtypes = [wt.HWND, ctypes.c_int]
user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
user32.SetWindowLongPtrW.argtypes = [wt.HWND, ctypes.c_int, ctypes.c_ssize_t]
user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
user32.CallWindowProcW.argtypes = [ctypes.c_ssize_t, wt.HWND, wt.UINT,
                                   wt.WPARAM, wt.LPARAM]
user32.CallWindowProcW.restype = ctypes.c_ssize_t
user32.CreatePopupMenu.argtypes = []
user32.CreatePopupMenu.restype = wt.HMENU
user32.AppendMenuW.argtypes = [wt.HMENU, wt.UINT, ctypes.c_size_t, wt.LPCWSTR]
user32.AppendMenuW.restype = wt.BOOL
user32.TrackPopupMenu.argtypes = [wt.HMENU, wt.UINT, ctypes.c_int,
                                  ctypes.c_int, ctypes.c_int, wt.HWND,
                                  ctypes.c_void_p]
user32.TrackPopupMenu.restype = wt.UINT   # 带 TPM_RETURNCMD，返回命令 ID
user32.DestroyMenu.argtypes = [wt.HMENU]
user32.DestroyMenu.restype = wt.BOOL
user32.GetCursorPos.argtypes = [ctypes.POINTER(_POINT)]
user32.GetCursorPos.restype = wt.BOOL
user32.SetForegroundWindow.argtypes = [wt.HWND]
user32.SetForegroundWindow.restype = wt.BOOL
user32.PostMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
user32.PostMessageW.restype = wt.BOOL
user32.RegisterWindowMessageW.argtypes = [wt.LPCWSTR]
user32.RegisterWindowMessageW.restype = wt.UINT
user32.GetParent.argtypes = [wt.HWND]
user32.GetParent.restype = wt.HWND
user32.GetSystemMetrics.argtypes = [ctypes.c_int]
user32.GetSystemMetrics.restype = ctypes.c_int


class TrayController:
    """主窗口的托盘控制器：注册/删除图标、处理回调与托盘菜单。

    on_restore：左键单击托盘图标 / 菜单「显示主窗口」时调用（Tk 主线程）；
    on_exit：菜单「退出程序」时调用（Tk 主线程）。
    """

    ID_SHOW = 1
    ID_EXIT = 2

    def __init__(self, tk_root, icon_path, tip, on_restore, on_exit):
        self._root = tk_root
        self._icon_path = icon_path
        self._tip = tip
        self._on_restore = on_restore
        self._on_exit = on_exit
        self._added = False        # 托盘图标当前是否已注册
        self._nid = None           # 最近一次注册用的 NOTIFYICONDATAW
        self._hicon = None
        self._proc = None          # WNDPROC 回调引用（防 GC，同时标记已子类化）
        self._old_proc = None
        # Tk 主窗口的顶层 HWND：winfo_id() 返回的是 Tk 内层子窗口，
        # 真正带标题栏的顶层窗口是其父窗口
        child = tk_root.winfo_id()
        self._hwnd = user32.GetParent(child) or child
        # explorer 重启会清空托盘区，系统广播 TaskbarCreated，收到后重注册
        self._msg_taskbar = user32.RegisterWindowMessageW("TaskbarCreated")

    # ---------- 对外接口 ----------

    def install(self):
        """子类化主窗口过程以接收托盘回调（幂等；show 前调用）。"""
        if self._proc is not None:
            return
        proto = WNDPROC(self._proc_impl)
        addr = ctypes.cast(proto, ctypes.c_void_p).value or 0
        old = user32.SetWindowLongPtrW(self._hwnd, GWLP_WNDPROC, addr)
        self._old_proc = old
        self._proc = proto

    def show(self):
        """注册托盘图标（已注册时幂等）。失败返回 False。"""
        if self._added:
            return True
        self.install()
        return self._add_icon()

    def hide(self):
        """删除托盘图标（未注册时幂等）。"""
        if not self._added:
            return
        nid = self._nid
        if nid is not None:
            shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(nid))
        self._added = False
        self._nid = None
        if self._hicon:
            user32.DestroyIcon(self._hicon)
            self._hicon = None

    def notify(self, title, text):
        """显示一次气泡提示（须已 show）。"""
        if not self._added or self._nid is None:
            return
        nid = self._nid
        nid.uFlags |= NIF_INFO
        nid.szInfo = text[:255]
        nid.szInfoTitle = title[:63]
        nid.dwInfoFlags = NIIF_INFO
        nid.Anonymous.uTimeout = 3000
        shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(nid))

    @property
    def visible(self):
        return self._added

    def shutdown(self):
        """退出前清理：删除图标 + 还原窗口过程（幂等）。"""
        self.hide()
        if self._proc is not None:
            user32.SetWindowLongPtrW(self._hwnd, GWLP_WNDPROC,
                                     self._old_proc)
            self._proc = None
            self._old_proc = None

    # ---------- 内部实现 ----------

    def _add_icon(self):
        # explorer 重启（TaskbarCreated）后会重新注册图标：旧 HICON 已失效，
        # 先释放避免句柄泄漏
        if self._hicon:
            user32.DestroyIcon(self._hicon)
            self._hicon = None
        hicon = self._load_icon()
        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        nid.hWnd = self._hwnd
        nid.uID = 1
        nid.uCallbackMessage = WM_APP_TRAY
        nid.szTip = self._tip[:127]
        flags = NIF_MESSAGE | NIF_TIP
        if hicon:
            nid.hIcon = hicon
            flags |= NIF_ICON
        nid.uFlags = flags
        if shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid)):
            self._nid = nid
            self._hicon = hicon
            self._added = True
            return True
        if hicon:
            user32.DestroyIcon(hicon)
        return False

    def _load_icon(self):
        """从 ico 文件加载小尺寸图标；失败返回 None（托盘仅无图标可用）。"""
        try:
            return user32.LoadImageW(
                None, self._icon_path, IMAGE_ICON,
                user32.GetSystemMetrics(SM_CXSMICON) or 16,
                user32.GetSystemMetrics(SM_CYSMICON) or 16,
                LR_LOADFROMFILE)
        except Exception:
            return None

    def _proc_impl(self, hwnd, msg, wparam, lparam):
        """子类化后的窗口过程：托盘消息自处理，其余原样转发。"""
        try:
            if msg == WM_APP_TRAY:
                if lparam in (WM_LBUTTONUP, WM_LBUTTONDBLCLK):
                    self._safe(self._on_restore)
                elif lparam in (WM_RBUTTONUP, WM_CONTEXTMENU):
                    self._popup_menu(hwnd)
                return 0
            if msg == self._msg_taskbar and self._added:
                self._add_icon()   # explorer 重启后重注册图标
                return 0
        except Exception:
            logging.exception("托盘窗口过程异常")
        return user32.CallWindowProcW(self._old_proc, hwnd, msg, wparam,
                                      lparam)

    def _popup_menu(self, hwnd):
        """托盘右键菜单：显示主窗口 / 退出程序。"""
        menu = user32.CreatePopupMenu()
        if not menu:
            return
        user32.AppendMenuW(menu, MF_STRING, self.ID_SHOW, "显示主窗口")
        user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
        user32.AppendMenuW(menu, MF_STRING, self.ID_EXIT, "退出程序")
        pt = _POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        # 必须先前置窗口，否则点击菜单外部时菜单不会关闭（Win32 已知行为）
        user32.SetForegroundWindow(hwnd)
        cmd = user32.TrackPopupMenu(
            menu, TPM_RIGHTBUTTON | TPM_BOTTOMALIGN | TPM_RETURNCMD,
            pt.x, pt.y, 0, hwnd, None)
        # TrackPopupMenu 返回后投递空消息，确保菜单 dismiss 逻辑完成
        user32.PostMessageW(hwnd, WM_NULL, 0, 0)
        user32.DestroyMenu(menu)
        if cmd == self.ID_SHOW:
            self._safe(self._on_restore)
        elif cmd == self.ID_EXIT:
            self._safe(self._on_exit)

    @staticmethod
    def _safe(fn):
        """托盘回调兜底：界面已销毁等异常不向外抛（窗口过程内抛异常致命）。"""
        try:
            fn()
        except Exception:
            logging.exception("托盘回调异常")
