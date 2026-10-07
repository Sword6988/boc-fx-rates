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

APP_TITLE = "中行汇率换算"

# 旧版产品名（v1.6 及更早）：改名后配置目录随之从
# %APPDATA%\外汇现汇买入价查询 变为 %APPDATA%\中行汇率换算，首次启动需把旧
# 目录里的 config.json 迁移过来，否则老用户保存的选中币种 / 窗口尺寸 / 换算
# 方向会全部回落到默认值。因此这个旧字符串在改名后必须继续保留。
LEGACY_CONFIG_DIR_NAME = "外汇现汇买入价查询"


def _write_config_file(path, data):
    """把配置字典原子写入指定路径（静默失败：非关键路径）。

    单独抽出是为了让「正常保存」与「旧目录迁移」共用同一套序列化，避免
    两处写入格式漂移。

    原子性：先写同目录临时文件（同卷，保证 os.replace 是原子 rename），
    flush + fsync 落盘后再 os.replace 覆盖目标，避免写入途中崩溃留下
    半截 config.json。异常时清理临时文件并静默返回。
    """
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass


def _migrate_legacy_config(new_dir):
    """一次性迁移：把旧版配置目录的 config.json 复制到新目录。

    触发条件：新目录下还没有 config.json，且旧目录
    %APPDATA%\\外汇现汇买入价查询\\config.json 存在。

    只复制、绝不删除：旧目录与旧文件原样保留（零丢失风险优先，用户回退
    旧版本时配置仍在）。迁移失败（权限不足 / JSON 损坏 / 目录不可写）一律
    静默跳过——配置读写是非关键路径，不能因此拖垮启动。
    """
    try:
        if os.path.exists(os.path.join(new_dir, "config.json")):
            return                       # 新配置已存在：不覆盖用户现有配置
        old_path = os.path.join(os.path.dirname(new_dir),
                                LEGACY_CONFIG_DIR_NAME, "config.json")
        if not os.path.exists(old_path):
            return                       # 全新用户：无旧配置可迁移
        with open(old_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return                       # 旧配置损坏：交给 _load_config 兜底
        # 原样搬运（不重算字段）：旧配置与本版同 schema，且保留所有字段，
        # 读取时的健壮性校验统一由 _load_config 负责。
        _write_config_file(os.path.join(new_dir, "config.json"), data)
    except Exception:
        pass


def _config_dir():
    """用户配置目录：%APPDATA%\\中行汇率换算（不可用时回退临时目录）。

    目录名跟随 APP_TITLE；改名后首次启动会尝试从旧版目录迁移 config.json，
    见 _migrate_legacy_config。
    """
    base = os.environ.get("APPDATA") or tempfile.gettempdir()
    d = os.path.join(base, APP_TITLE)
    try:
        os.makedirs(d, exist_ok=True)
    except OSError:
        pass
    _migrate_legacy_config(d)
    return d


def _config_path():
    return os.path.join(_config_dir(), "config.json")


# Tk 窗口尺寸的标准格式「WxH[±X±Y]」（config.json 中 geometry 字段只接受此格式，
# 畸形值按无尺寸记忆处理，避免直接传给 self.geometry() 设置异常尺寸/位置）
_GEOMETRY_RE = re.compile(r"^\d{1,5}x\d{1,5}([+-]\d{1,5}[+-]\d{1,5})?$")
# 带位置部分的 geometry（钳制时解析各字段用）
_GEOMETRY_XY_RE = re.compile(
    r"^(\d{1,5})x(\d{1,5})([+-]\d{1,5})([+-]\d{1,5})$")
# 仅含尺寸、无位置部分的 geometry
_GEOMETRY_SIZE_RE = re.compile(r"^(\d{1,5})x(\d{1,5})$")

# 钳制后窗口至少保留这么多像素可见于虚拟屏幕内
_GEOMETRY_VISIBLE_PX = 80

# 记忆尺寸的下限（与 app 的 minsize(470, 380) 保持一致）
_GEOMETRY_MIN_W, _GEOMETRY_MIN_H = 470, 380

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
    """把记忆的窗口尺寸与位置钳制到虚拟屏幕内。

    背景：双屏用户拔掉副屏后，self.geometry() 会合法记忆出屏外坐标，
    下次启动窗口完全不可见（只能任务栏右键移动恢复）。

    尺寸：先钳到虚拟屏范围（拦截 config.json 手改出的 99999x99999 之类
    超大尺寸），再抬到不小于 470x380（与 app 的 minsize 一致）。
    位置：x ∈ [vx - w + 80, vx + vw - 80]，y 同理——保证窗口至少保留约
    80px 可见于屏内；屏内坐标原样返回（行为不变）。
    screen：(vx, vy, vw, vh)，None 时实时查询虚拟屏幕（测试可注入）。
    geo 为「WxH」或「WxH±X±Y」；非标准格式时原样返回。
    """
    g = geo.strip()
    m = _GEOMETRY_XY_RE.match(g)
    if m:
        w, h, x, y = (int(v) for v in m.groups())
        has_pos = True
    else:
        m = _GEOMETRY_SIZE_RE.match(g)
        if not m:
            return geo                    # 非标准格式：不处理
        w, h = int(m.group(1)), int(m.group(2))
        x = y = 0
        has_pos = False
    if screen is None:
        screen = _virtual_screen()
    vx, vy, vw, vh = screen
    # 尺寸：先钳到虚拟屏，再保底不小于 minsize
    w = max(_GEOMETRY_MIN_W, min(w, vw))
    h = max(_GEOMETRY_MIN_H, min(h, vh))
    if not has_pos:
        return "%dx%d" % (w, h)
    vis = _GEOMETRY_VISIBLE_PX
    # 理论上 lo ≤ hi 恒成立（w、vw 均非负且屏幕不小于 160px），防御式钳制
    x = min(vx + vw - vis, max(vx - w + vis, x))
    y = min(vy + vh - vis, max(vy - h + vis, y))
    return "%dx%d%+d%+d" % (w, h, x, y)


def _load_config():
    """读取配置；缺失/损坏时返回默认（选中的 3 种货币）。"""
    default = {"selected": [cur.code for cur in CURRENCIES], "geometry": None,
               "inverse": False}
    try:
        with open(_config_path(), "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return default
    if not isinstance(data, dict):
        return default
    selected = data.get("selected")
    if isinstance(selected, list):
        # isinstance(c, str) 防御畸形配置里的不可哈希元素（如嵌套 dict/list）：
        # 否则 `c in CODE_TO_CURRENCY` 会抛 TypeError 导致启动崩溃；
        # dict.fromkeys 去重（保持首次出现顺序）。
        valid = list(dict.fromkeys(
            c for c in selected if isinstance(c, str) and c in CODE_TO_CURRENCY))
        if not valid and selected:
            # 有条目但全部畸形 → 配置损坏，回退默认。注意「空列表」是
            # 合法状态（用户在币种面板显式点了「全部取消」），必须保留，
            # 否则全不选在重启后被悄悄改回默认币种。
            valid = [cur.code for cur in CURRENCIES]
    else:
        # 字段缺失或类型不对 → 默认
        valid = [cur.code for cur in CURRENCIES]
    geometry = data.get("geometry")
    if not isinstance(geometry, str) or not _GEOMETRY_RE.match(geometry.strip()):
        geometry = None
    # 换算方向：仅接受严格布尔值，其它类型（"true" / 1 / {}）一律按默认正向。
    # 用 `is True` 而非真值判断，避免 1、"false" 之类被误读为开启。
    inverse = data.get("inverse") is True
    return {"selected": valid, "geometry": geometry, "inverse": inverse}


def _save_config(selected, geometry=None, inverse=None):
    """持久化选中币种、窗口尺寸与换算方向（静默失败：非关键路径）。"""
    data = {"selected": selected}
    if geometry:
        data["geometry"] = geometry
    if inverse is not None:
        data["inverse"] = bool(inverse)
    _write_config_file(_config_path(), data)
