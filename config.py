# -*- coding: utf-8 -*-
"""配置持久化与窗口几何校验：独立于 UI（tkinter），可离线测试。

app.py 通过本模块读写 %APPDATA% 下的用户配置（选中币种、窗口尺寸），
并在启动时对记忆的窗口位置做屏外钳制。
"""

import ctypes
import json
import os
import re
import tempfile

from currencies import CURRENCIES, CODE_TO_CURRENCY

APP_TITLE = "外汇现汇买入价查询"


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


# Tk 窗口尺寸的标准格式「WxH[±X±Y]」（config.json 中 geometry 字段只接受此格式，
# 畸形值按无尺寸记忆处理，避免直接传给 self.geometry() 设置异常尺寸/位置）
_GEOMETRY_RE = re.compile(r"^\d{1,5}x\d{1,5}([+-]\d{1,5}[+-]\d{1,5})?$")
# 带位置部分的 geometry（钳制时解析各字段用）
_GEOMETRY_XY_RE = re.compile(
    r"^(\d{1,5})x(\d{1,5})([+-]\d{1,5})([+-]\d{1,5})$")

# 钳制后窗口至少保留这么多像素可见于虚拟屏幕内
_GEOMETRY_VISIBLE_PX = 80

# GetSystemMetrics 的虚拟屏幕 / 主屏索引
_SM_XVIRTUALSCREEN = 76
_SM_YVIRTUALSCREEN = 77
_SM_CXVIRTUALSCREEN = 78
_SM_CYVIRTUALSCREEN = 79
_SM_CXSCREEN = 0
_SM_CYSCREEN = 1


def _virtual_screen():
    """查询多屏虚拟桌面范围 (vx, vy, vw, vh)。

    用 GetSystemMetrics 的 SM_*VIRTUALSCREEN 系列取多屏虚拟桌面整体范围
    （含负坐标，如左侧副屏时 vx<0）；调用失败回退主屏 (0, 0, 宽, 高)。
    """
    user32 = ctypes.windll.user32
    try:
        vx = user32.GetSystemMetrics(_SM_XVIRTUALSCREEN)
        vy = user32.GetSystemMetrics(_SM_YVIRTUALSCREEN)
        vw = user32.GetSystemMetrics(_SM_CXVIRTUALSCREEN)
        vh = user32.GetSystemMetrics(_SM_CYVIRTUALSCREEN)
        if vw <= 0 or vh <= 0:
            raise OSError("虚拟屏幕尺寸无效：%dx%d" % (vw, vh))
        return vx, vy, vw, vh
    except Exception:
        # 回退主屏：SM_CXSCREEN(0) / SM_CYSCREEN(1)，原点 (0, 0)
        return 0, 0, user32.GetSystemMetrics(_SM_CXSCREEN), \
            user32.GetSystemMetrics(_SM_CYSCREEN)


def _clamp_geometry(geo, screen=None):
    """把记忆的窗口位置钳制到虚拟屏幕内，尺寸保持原值。

    背景：双屏用户拔掉副屏后，self.geometry() 会合法记忆出屏外坐标，
    下次启动窗口完全不可见（只能任务栏右键移动恢复）。

    钳制规则：x ∈ [vx - w + 80, vx + vw - 80]，y 同理——保证窗口至少
    保留约 80px 可见于屏内；屏内坐标原样返回（行为不变）。
    screen：(vx, vy, vw, vh)，None 时实时查询虚拟屏幕（测试可注入）。
    geo 无位置部分（仅 WxH）或非标准格式时原样返回。
    """
    m = _GEOMETRY_XY_RE.match(geo.strip())
    if not m:
        return geo
    w, h, x, y = (int(g) for g in m.groups())
    if screen is None:
        screen = _virtual_screen()
    vx, vy, vw, vh = screen
    vis = _GEOMETRY_VISIBLE_PX
    # 理论上 lo ≤ hi 恒成立（w、vw 均非负且屏幕不小于 160px），防御式钳制
    x = min(vx + vw - vis, max(vx - w + vis, x))
    y = min(vy + vh - vis, max(vy - h + vis, y))
    return "%dx%d%+d%+d" % (w, h, x, y)


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
    # isinstance(c, str) 防御畸形配置里的不可哈希元素（如嵌套 dict/list）：
    # 否则 `c in CODE_TO_CURRENCY` 会抛 TypeError 导致启动崩溃；
    # dict.fromkeys 去重（保持首次出现顺序）。
    valid = list(dict.fromkeys(
        c for c in selected if isinstance(c, str) and c in CODE_TO_CURRENCY))
    if not valid:
        valid = [cur.code for cur in CURRENCIES]
    geometry = data.get("geometry")
    if not isinstance(geometry, str) or not _GEOMETRY_RE.match(geometry.strip()):
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
