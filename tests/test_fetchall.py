# -*- coding: utf-8 -*-
"""离线测试：fetch_all 编排、解码、错误映射、格式化、列偏移解析（无需联网）。

运行：python tests/test_fetchall.py
"""
import json
import os
import socket
import ssl
import sys
import tempfile
import urllib.error
from email.message import Message
from unittest import mock

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

import fetcher
from fetcher import _fmt_time


# ------------------------- fetch_all 编排 -------------------------

def test_fetch_all_all_ok():
    def fake_boc():
        return {"美元": {"buy": 671.22, "time": "2026/08/27 08:19:14"},
                "卢布": {"buy": 7.58, "time": "2026/08/27 08:19:14"}}

    def fake_pen():
        return 2.0111, "open.er-api.com", "27 Aug 2026"

    with mock.patch.object(fetcher, "fetch_boc_rates", fake_boc), \
         mock.patch.object(fetcher, "fetch_pen_reference", fake_pen):
        rows, err = fetcher.fetch_all()
    assert err is None, "全成功不应有错误"
    assert set(rows) == {"USD", "RUB", "PEN"}
    assert rows["USD"]["rate1"] == 6.7122
    assert rows["USD"]["rate100"] == 671.22
    assert rows["USD"]["fallback"] is False
    assert rows["PEN"]["fallback"] is True
    assert rows["PEN"]["rate1"] == 2.0111
    print("fetch_all 全成功测试通过")


def test_fetch_all_boc_missing_currency():
    def fake_boc():
        return {"美元": {"buy": 671.22, "time": "2026/08/27 08:19:14"}}

    def fake_pen():
        return 2.0111, "open.er-api.com", "27 Aug 2026"

    with mock.patch.object(fetcher, "fetch_boc_rates", fake_boc), \
         mock.patch.object(fetcher, "fetch_pen_reference", fake_pen):
        rows, err = fetcher.fetch_all()
    assert "未找到「卢布」" in err
    assert "USD" in rows and "RUB" not in rows
    assert "PEN" in rows
    print("fetch_all 中行缺币种测试通过")


def test_fetch_all_boc_fails_pen_succeeds():
    def fake_boc():
        raise TimeoutError("timed out")

    def fake_pen():
        return 2.0111, "open.er-api.com", "27 Aug 2026"

    with mock.patch.object(fetcher, "fetch_boc_rates", fake_boc), \
         mock.patch.object(fetcher, "fetch_pen_reference", fake_pen):
        rows, err = fetcher.fetch_all()
    assert "中行牌价页获取失败" in err
    assert set(rows) == {"PEN"}, "中行失败时仅有备用源数据"
    print("fetch_all 中行失败/PEN 成功测试通过")


def test_fetch_all_pen_fails():
    def fake_boc():
        return {"美元": {"buy": 671.22, "time": "2026/08/27 08:19:14"},
                "卢布": {"buy": 7.58, "time": "2026/08/27 08:19:14"}}

    def fake_pen():
        raise RuntimeError("all fallback sources down")

    with mock.patch.object(fetcher, "fetch_boc_rates", fake_boc), \
         mock.patch.object(fetcher, "fetch_pen_reference", fake_pen):
        rows, err = fetcher.fetch_all()
    assert "秘鲁新索尔备用源失败" in err
    assert set(rows) == {"USD", "RUB"}
    assert "PEN" not in rows
    print("fetch_all PEN 失败测试通过")


def test_fetch_all_selected_subset():
    """只选部分币种：仅返回所选，且不请求备用源。"""
    def fake_boc():
        return {"美元": {"buy": 671.22, "time": "2026/08/27 08:19:14"},
                "卢布": {"buy": 7.58, "time": "2026/08/27 08:19:14"}}

    with mock.patch.object(fetcher, "fetch_boc_rates", fake_boc) as fb, \
         mock.patch.object(fetcher, "fetch_pen_reference") as fp:
        rows, err = fetcher.fetch_all(selected=["USD"])
    assert set(rows) == {"USD"}, "只应返回所选币种"
    assert err is None
    fp.assert_not_called(), "未选 PEN 时不应请求备用源"
    print("fetch_all 选中子集测试通过")


def test_fetch_all_selected_pen_only():
    """只选 PEN：全部来自备用源。"""
    def fake_boc():
        return {"美元": {"buy": 671.22, "time": "2026/08/27 08:19:14"}}

    def fake_pen():
        return 2.0111, "open.er-api.com", "27 Aug 2026"

    with mock.patch.object(fetcher, "fetch_boc_rates", fake_boc), \
         mock.patch.object(fetcher, "fetch_pen_reference", fake_pen):
        rows, err = fetcher.fetch_all(selected=["PEN"])
    assert set(rows) == {"PEN"}
    assert rows["PEN"]["fallback"] is True
    assert err is None
    print("fetch_all 仅 PEN 测试通过")


# ------------------------- 解码 / 错误映射 / 格式化 -------------------------

def _msg(charset):
    m = Message()
    if charset:
        m["Content-Type"] = "text/html; charset=%s" % charset
    return m


def test_decode_html_charset_priority():
    assert fetcher._decode_html("中文".encode("gb18030"),
                                _msg("gb18030")) == "中文"
    assert fetcher._decode_html("中文".encode("utf-8"), _msg("")) == "中文"
    assert fetcher._decode_html("中文".encode("gb18030"), _msg("")) == "中文"
    assert fetcher._decode_html("中文".encode("utf-8"), None) == "中文"
    print("_decode_html charset 优先级测试通过")


def test_decode_html_garbage_replace():
    out = fetcher._decode_html(b"\xff\xfe\xfa", None)
    assert "\ufffd" in out, "无法解码时应以替换符兜底"
    print("_decode_html 乱码兜底测试通过")


def test_friendly_net_error():
    assert "连接超时" in fetcher._friendly_net_error(
        urllib.error.URLError(socket.timeout()))
    cert = ssl.SSLCertVerificationError("certificate verify failed")
    assert "证书校验失败" in fetcher._friendly_net_error(
        urllib.error.URLError(cert))
    http_err = urllib.error.HTTPError("http://x", 503, "svc", None, None)
    assert "HTTP 503" in fetcher._friendly_net_error(http_err)
    assert "JSON" in fetcher._friendly_net_error(
        json.JSONDecodeError("bad", "doc", 0))
    print("_friendly_net_error 映射测试通过")


def test_fmt():
    assert fetcher.fmt(None) == "--"
    assert fetcher.fmt(671.22) == "671.22"
    assert fetcher.fmt(6.7122) == "6.7122"
    assert fetcher.fmt(0.0758) == "0.0758"
    assert fetcher.fmt(0.000123) == "0.000123"
    print("fmt 小数位测试通过")


def test_fmt_never_zero():
    """反向换算（1 人民币 = X 外币）的极小值不得被舍入成 0。

    固定小数位下 1e-9 会变成「0.000000」，等于没有信息；自适应后要么
    给出非零小数，要么退化为科学计数法，但绝不显示 0。
    同时约束输出长度 ≤ 8：数值列宽固定 8 字符，超长会顶出列宽。
    """
    for v in (1.0 / 7.1235, 1.0 / 0.0855, 5e-5, 1e-6, 9.9e-7, 1e-9, 1e-12):
        s = fetcher.fmt(v)
        assert float(s) != 0.0, "fmt(%r) 舍入成 0：%s" % (v, s)
        assert len(s) <= 8, "fmt(%r) 超出数值列宽：%s" % (v, s)
    assert fetcher.fmt(0.0) == "0"
    assert fetcher.fmt(None) == "--"
    print("fmt 非零保证与列宽上限测试通过")


def test_load_config_inverse():
    """换算方向：只接受严格布尔 true，其它类型一律按默认正向。"""
    assert _load_with('{"inverse": true}')["inverse"] is True
    assert _load_with('{"inverse": false}')["inverse"] is False
    for bad in ('{"inverse": 1}', '{"inverse": "true"}', '{"inverse": "false"}',
                '{"inverse": null}', '{"inverse": {}}', "{}"):
        assert _load_with(bad)["inverse"] is False, bad
    print("config inverse 方向解析测试通过")


def test_fmt_time():
    assert _fmt_time("2026/08/26 20:50:26") == "2026-08-26 20:50"
    assert _fmt_time("2026-08-26 20:50:26") == "2026-08-26 20:50"
    assert _fmt_time("27 Aug 2026") == "2026-08-27"
    assert _fmt_time("") == ""
    assert _fmt_time(" 乱文 ") == "乱文"
    print("_fmt_time 格式测试通过")


# ------------------------- 表头列定位 -------------------------

def test_parse_header_column_offset():
    """表头列序变化时仍能按列名定位（货币名称不在第 0 列）。"""
    html = ("<table>"
            "<tr><td>序号</td><td>货币名称</td><td>现汇买入价</td></tr>"
            "<tr><td>1</td><td>美元</td><td>671.22</td></tr>"
            "<tr><td>2</td><td>卢布</td><td>7.58</td></tr>"
            "</table>")
    rows = fetcher.parse_boc_html(html)
    assert rows["美元"]["buy"] == 671.22
    assert rows["卢布"]["buy"] == 7.58
    print("表头列偏移解析测试通过")


def test_parse_default_column_order():
    """无表头时回退默认列序（货币名称第 0 列、现汇买入价第 1 列）。"""
    html = ("<table>"
            "<tr><td>美元</td><td>671.22</td></tr>"
            "</table>")
    rows = fetcher.parse_boc_html(html)
    assert rows["美元"]["buy"] == 671.22
    print("无表头默认列序测试通过")


def test_parse_wanted_filter():
    html = ("<table>"
            "<tr><td>货币名称</td><td>现汇买入价</td></tr>"
            "<tr><td>欧元</td><td>780.12</td></tr>"
            "<tr><td>美元</td><td>671.22</td></tr>"
            "</table>")
    rows = fetcher.parse_boc_html(html, wanted={"美元"})
    assert set(rows) == {"美元"}, "wanted 过滤应只返回指定币种"
    rows_all = fetcher.parse_boc_html(html)
    assert set(rows_all) == {"欧元", "美元"}, "不传 wanted 应全量解析"
    print("parse wanted 过滤/全量解析测试通过")


# ------------------------- 配置读取健壮性 -------------------------

def _write_cfg(content):
    """写入临时 config.json 并返回一个替换 config._config_path 的函数。"""
    import tempfile
    d = tempfile.mkdtemp(prefix="fx_cfg_test_")
    path = os.path.join(d, "config.json")
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)

    def fake_path():
        return path
    return fake_path


def _load_with(content):
    import config as config_mod
    with mock.patch.object(config_mod, "_config_path", _write_cfg(content)):
        return config_mod._load_config()


def test_load_config_unhashable_selected():
    """selected 含不可哈希元素（dict/list）时不应崩溃，仅保留合法项。"""
    cfg = _load_with('{"selected": [{"a": 1}, ["USD"], "USD"]}')
    assert cfg["selected"] == ["USD"], "非法元素应被过滤，仅剩合法项"
    # 全部非法时回退默认币种
    cfg2 = _load_with('{"selected": [{"a": 1}, 42]}')
    assert cfg2["selected"] == [cur.code for cur in fetcher.CURRENCIES]
    print("config 不可哈希 selected 防御测试通过")


def test_load_config_empty_selected():
    """selected 为空列表是合法状态（币种面板「全部取消」），不回退默认。"""
    cfg = _load_with('{"selected": []}')
    assert cfg["selected"] == [], "显式全不选应被保留，不得悄悄恢复默认"
    # 有条目但全部畸形 → 配置损坏，回退默认（与空列表区分开）
    cfg2 = _load_with('{"selected": [{"a": 1}, 42]}')
    assert cfg2["selected"] == [cur.code for cur in fetcher.CURRENCIES]
    # 字段缺失 → 默认
    cfg3 = _load_with('{"inverse": false}')
    assert cfg3["selected"] == [cur.code for cur in fetcher.CURRENCIES]
    print("config 空列表 selected 测试通过")


def test_load_config_dedup_and_filter():
    """selected 去重、过滤非法代码、保留合法项。"""
    cfg = _load_with('{"selected": ["USD", "USD", "XXX", "RUB"]}')
    assert cfg["selected"] == ["USD", "RUB"]
    print("config selected 去重/过滤测试通过")


def test_load_config_malformed_geometry():
    """geometry 为畸形字符串/非字符串时按无记忆处理，合法值保留。"""
    assert _load_config_geometry_is_none('{"geometry": "abc; drop table x"}')
    assert _load_config_geometry_is_none('{"geometry": "9999999999"}')
    assert _load_config_geometry_is_none('{"geometry": "-100x200"}')
    assert _load_config_geometry_is_none('{"geometry": 123}')
    assert _load_config_geometry_is_none('{"geometry": ["640x480"]}')
    assert _load_config_geometry_is_none('{"geometry": "x480"}')
    import config as config_mod
    for geo in ("640x480", "1024x768+10-5", "440x380-20+30"):
        with mock.patch.object(config_mod, "_config_path",
                               _write_cfg('{"geometry": "%s"}' % geo)):
            assert config_mod._load_config()["geometry"] == geo, geo
    print("config 畸形 geometry 防御测试通过")


def _load_config_geometry_is_none(content):
    import config as config_mod
    with mock.patch.object(config_mod, "_config_path", _write_cfg(content)):
        return config_mod._load_config()["geometry"] is None


def test_load_config_broken_json():
    """JSON 损坏时返回默认配置。"""
    cfg = _load_with("{not valid json")
    assert cfg["selected"] == [cur.code for cur in fetcher.CURRENCIES]
    assert cfg["geometry"] is None
    print("config 损坏 JSON 回退默认测试通过")


# ------------------------- geometry 屏外钳制 -------------------------

# ------------------------- 旧配置目录迁移 -------------------------

def _mig_paths():
    """构造临时 %APPDATA%：写入旧目录 config.json，返回 (appdata, old_path)。"""
    import tempfile
    import config as config_mod
    appdata = tempfile.mkdtemp(prefix="fx_appdata_")
    old_dir = os.path.join(appdata, config_mod.LEGACY_CONFIG_DIR_NAME)
    os.makedirs(old_dir, exist_ok=True)
    old_path = os.path.join(old_dir, "config.json")
    return appdata, old_path


def test_load_config_migrates_legacy_dir():
    """产品改名后：旧目录 config.json 迁移到新目录，且旧文件保留不删除。"""
    import config as config_mod
    appdata, old_path = _mig_paths()
    with open(old_path, "w", encoding="utf-8") as f:
        f.write('{"selected": ["USD", "RUB"], "geometry": "640x480+10+10",'
                ' "inverse": true}')
    with mock.patch.dict(os.environ, {"APPDATA": appdata}):
        cfg = config_mod._load_config()
        new_path = os.path.join(appdata, config_mod.APP_TITLE, "config.json")
    assert cfg["selected"] == ["USD", "RUB"], "应读到旧配置的选中币种"
    assert cfg["geometry"] == "640x480+10+10", "应读到旧配置窗口尺寸"
    assert cfg["inverse"] is True, "应读到旧配置换算方向"
    assert os.path.exists(new_path), "新目录应已写入 config.json"
    assert os.path.exists(old_path), "旧配置文件必须保留（零丢失，不删除）"
    print("旧配置目录迁移测试通过")


def test_load_config_migration_keeps_new_config():
    """新目录已有配置时不覆盖；无旧配置（全新用户）时回退默认且不报错。"""
    import config as config_mod
    # 1) 新旧配置同时存在 → 保留新配置
    appdata, old_path = _mig_paths()
    with open(old_path, "w", encoding="utf-8") as f:
        f.write('{"selected": ["RUB"], "inverse": true}')
    new_dir = os.path.join(appdata, config_mod.APP_TITLE)
    os.makedirs(new_dir, exist_ok=True)
    with open(os.path.join(new_dir, "config.json"), "w", encoding="utf-8") as f:
        f.write('{"selected": ["USD"], "inverse": false}')
    with mock.patch.dict(os.environ, {"APPDATA": appdata}):
        assert config_mod._load_config()["selected"] == ["USD"], \
            "已有新配置时不应被旧配置覆盖"
    # 2) 只有旧目录但旧 JSON 损坏 → 静默回退默认，不崩溃
    appdata2, old_path2 = _mig_paths()
    with open(old_path2, "w", encoding="utf-8") as f:
        f.write("{not valid json")
    with mock.patch.dict(os.environ, {"APPDATA": appdata2}):
        cfg2 = config_mod._load_config()
    assert cfg2["selected"] == [cur.code for cur in fetcher.CURRENCIES], \
        "损坏的旧配置应回退默认币种"
    # 3) 全新用户（无旧目录）→ 默认配置，不报错
    import tempfile
    with mock.patch.dict(os.environ,
                         {"APPDATA": tempfile.mkdtemp(prefix="fx_appdata_")}):
        cfg3 = config_mod._load_config()
    assert cfg3["selected"] == [cur.code for cur in fetcher.CURRENCIES]
    assert cfg3["geometry"] is None and cfg3["inverse"] is False
    print("迁移边界（保留新配置 / 损坏 / 全新用户）测试通过")


def test_clamp_geometry():
    """屏外坐标钳回虚拟屏幕内（保留 80px 可见），屏内坐标不变。

    尺寸另有下限 470x380（与 app minsize 一致）：440 宽会被抬到 470。
    """
    from config import _clamp_geometry
    pri = (0, 0, 1920, 1080)
    # 屏外大坐标 → 钳到右/下边界内 80px（宽度同时被抬到 470 下限）
    assert _clamp_geometry("440x380+99999+99999", screen=pri) == \
        "470x380+1840+1000"
    # 负方向屏外 → 钳到左/上边界内 80px
    assert _clamp_geometry("440x380-500-500", screen=pri) == \
        "470x380-390-300"
    # 屏内坐标原样返回（正常使用行为不变；尺寸抬到下限）
    assert _clamp_geometry("440x380+100+100", screen=pri) == \
        "470x380+100+100"
    assert _clamp_geometry("640x480+1760+960", screen=pri) == \
        "640x480+1760+960"   # 右下角贴边（恰好在 hi 边界内）
    # 多屏：虚拟屏起点非 0（如左侧副屏）时按虚拟屏整体范围钳制
    assert _clamp_geometry("440x380+99999+99999",
                           screen=(-1920, 0, 3840, 1080)) == \
        "470x380+1840+1000"
    # 无位置部分（仅 WxH）/ 畸形格式 → 原样返回
    assert _clamp_geometry("640x480", screen=pri) == "640x480"
    assert _clamp_geometry("garbage", screen=pri) == "garbage"
    print("geometry 屏外钳制测试通过")


# ------------------------- 汇率合法性校验 -------------------------

def test_valid_rate():
    """_valid_rate：合法值原样返回，非法值一律 None。"""
    assert fetcher._valid_rate("671.22") == 671.22, "数字字符串应被接受"
    assert fetcher._valid_rate(2.5) == 2.5
    assert fetcher._valid_rate("0.0758") == 0.0758
    assert fetcher._valid_rate(float("nan")) is None
    assert fetcher._valid_rate(float("inf")) is None
    assert fetcher._valid_rate(float("-inf")) is None
    assert fetcher._valid_rate(0) is None, "0 非法（会导致无穷/无意义）"
    assert fetcher._valid_rate(-1.5) is None, "负数非法"
    assert fetcher._valid_rate(1e5) is None, "等于上限（不含）应非法"
    assert fetcher._valid_rate(1e6) is None, "超上限应非法"
    assert fetcher._valid_rate("abc") is None, "非数字字符串非法"
    assert fetcher._valid_rate(None) is None
    print("_valid_rate 合法性校验测试通过")


def test_fmt_invalid_values():
    """fmt：nan/inf/负数 → \"--\"；0 仍为 \"0\"；正常值不变。"""
    assert fetcher.fmt(float("nan")) == "--"
    assert fetcher.fmt(float("inf")) == "--"
    assert fetcher.fmt(float("-inf")) == "--"
    assert fetcher.fmt(-1.5) == "--"
    assert fetcher.fmt(0) == "0", "0 保持既有行为"
    assert fetcher.fmt(0.0) == "0"
    assert fetcher.fmt(671.22) == "671.22"
    assert fetcher.fmt(None) == "--"
    print("fmt 非法值 / 零值测试通过")


# ------------------------- fetch_all 请求裁剪 / 0 行报错 -------------------------

def test_fetch_all_pen_only_skips_boc():
    """只选 PEN：不应发起中行牌价页请求。"""
    def fake_pen():
        return 2.0111, "open.er-api.com", "27 Aug 2026"

    with mock.patch.object(fetcher, "fetch_boc_rates") as fb, \
         mock.patch.object(fetcher, "fetch_pen_reference", fake_pen):
        rows, err = fetcher.fetch_all(selected=["PEN"])
    fb.assert_not_called()
    assert set(rows) == {"PEN"}
    assert rows["PEN"]["fallback"] is True
    assert err is None
    print("fetch_all 仅 PEN 不请求中行页测试通过")


def test_fetch_all_boc_empty_rows_is_error():
    """中行页请求成功但解析到 0 行 → 记为错误（不伪装成成功）。"""
    def fake_pen():
        return 2.0111, "open.er-api.com", "27 Aug 2026"

    with mock.patch.object(fetcher, "fetch_boc_rates", lambda: {}), \
         mock.patch.object(fetcher, "fetch_pen_reference", fake_pen):
        rows, err = fetcher.fetch_all(selected=["USD", "PEN"])
    assert err and "未解析到任何币种" in err, "0 行必须报错，err=%r" % err
    assert set(rows) == {"PEN"}, "非 PEN 币种无数据，仅 PEN 由备用源兜底"
    print("fetch_all 中行 0 行记为错误测试通过")


def test_fetch_all_empty_selection():
    """显式全不选：不请求任何数据源，直接返回空。"""
    with mock.patch.object(fetcher, "fetch_boc_rates") as fb, \
         mock.patch.object(fetcher, "fetch_pen_reference") as fp:
        rows, err = fetcher.fetch_all(selected=[])
    fb.assert_not_called()
    fp.assert_not_called()
    assert rows == {} and err is None
    print("fetch_all 空选择提前返回测试通过")


# ------------------------- 配置原子写 -------------------------

def test_write_config_file_atomic():
    """原子写：写后内容可读回、无 .tmp 残留；写入失败不抛异常。"""
    import config as config_mod
    d = tempfile.mkdtemp(prefix="fx_atomic_")
    path = os.path.join(d, "config.json")
    data = {"selected": ["USD"], "geometry": "470x380+0+0"}
    config_mod._write_config_file(path, data)
    with open(path, "r", encoding="utf-8") as f:
        assert json.load(f) == data, "写后应能被 json.load 读回"
    assert not os.path.exists(path + ".tmp"), "不应残留临时文件"
    assert not [n for n in os.listdir(d) if n.endswith(".tmp")], \
        "目录内不应有 .tmp 残留"
    # 写入失败（path 指向一个已存在目录）→ 静默返回，不抛异常
    dirpath = os.path.join(d, "as_dir")
    os.makedirs(dirpath)
    try:
        config_mod._write_config_file(dirpath, data)
    except Exception as e:
        raise AssertionError("写入失败不应抛异常：%r" % e)
    print("_write_config_file 原子写测试通过")


# ------------------------- 几何尺寸上下界钳制 -------------------------

def test_clamp_geometry_size_bounds():
    """W/H 超虚拟屏被钳到屏内；小于 minsize 被抬到 470x380。"""
    from config import _clamp_geometry
    pri = (0, 0, 1920, 1080)
    assert _clamp_geometry("99999x99999+0+0", screen=pri) == "1920x1080+0+0"
    assert _clamp_geometry("99999x99999", screen=pri) == "1920x1080"
    assert _clamp_geometry("300x200+0+0", screen=pri) == "470x380+0+0"
    assert _clamp_geometry("300x200", screen=pri) == "470x380"
    print("geometry 尺寸上界 / 下界钳制测试通过")


if __name__ == "__main__":
    test_fetch_all_all_ok()
    test_fetch_all_boc_missing_currency()
    test_fetch_all_boc_fails_pen_succeeds()
    test_fetch_all_pen_fails()
    test_fetch_all_selected_subset()
    test_fetch_all_selected_pen_only()
    test_decode_html_charset_priority()
    test_decode_html_garbage_replace()
    test_friendly_net_error()
    test_fmt()
    test_fmt_never_zero()
    test_fmt_time()
    test_parse_header_column_offset()
    test_parse_default_column_order()
    test_parse_wanted_filter()
    test_load_config_unhashable_selected()
    test_load_config_empty_selected()
    test_load_config_dedup_and_filter()
    test_load_config_malformed_geometry()
    test_load_config_broken_json()
    test_load_config_inverse()
    test_load_config_migrates_legacy_dir()
    test_load_config_migration_keeps_new_config()
    test_clamp_geometry()
    test_valid_rate()
    test_fmt_invalid_values()
    test_fetch_all_pen_only_skips_boc()
    test_fetch_all_boc_empty_rows_is_error()
    test_fetch_all_empty_selection()
    test_write_config_file_atomic()
    test_clamp_geometry_size_bounds()
    print("全部 fetch_all/工具函数离线测试通过")
